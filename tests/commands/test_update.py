"""Tests for `keel update` -- the self-update service (issue #415): ONE service,
`keel/commands/update.py`, two front-ends (the `keel update` CLI command and the
console's Account entry). The service is pure orchestration over subprocesses/HTTP --
no network, no real uv, no real venv anywhere in this file: every subprocess/HTTP seam
is injected, exactly as the fake-environment contract demands.

Three surfaces, pinned here:

* **The pure parts** -- `parse_release` (the GitHub payload -> `ReleaseInfo`), the
  semver comparison (honest on pre-releases and junk tags), the production-wheels
  selection (the FIVE named prefixes, #425; never fake/robinhood/kraken), and
  `plan_update`'s offer/refusal semantics, including the dev-checkout refusal.
* **`run_update` against a FAKE environment** -- the fail-safe ordering pins: backups
  exist BEFORE anything installs (asserted from inside the install fake), the
  superseded wheels are deleted ONLY after a verified success, backups are NEVER
  deleted, a failed verify is LOUD with the real state + the manual recovery, and the
  best-effort reinstall of the previous wheels when they are still on disk.
* **The relaunch closure** -- argv reconstruction (`build_relaunch_argv`) and the
  execv closure (`relaunch_tui`, execv itself faked).
"""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from keel.commands import update as up
from keel.version import BuildInfo

NOW_TS = 1_800_000_000.0


# -- shared fixtures/fakes -------------------------------------------------------------------------


def _asset(name: str) -> dict[str, str]:
    return {"name": name, "browser_download_url": f"https://example/rel/{name}"}


def _release_json(version: str = "0.7.0", extra: tuple[str, ...] = ()) -> bytes:
    names = [
        *(f"{prefix}-{version}-py3-none-any.whl" for prefix in up.PRODUCTION_WHEEL_PREFIXES),
        f"keel_broker_fake-{version}-py3-none-any.whl",
        f"keel_broker_kraken-{version}-py3-none-any.whl",
        f"keel_broker_robinhood-{version}-py3-none-any.whl",
        "config.yaml",
        *extra,
    ]
    import json

    return json.dumps(
        {"tag_name": f"v{version}", "assets": [_asset(name) for name in names]}
    ).encode()


_RELEASE_BUILD = BuildInfo(version="0.6.0", commit="deadbeef", dirty=False, source="release")
#: The equities deployment of #425 (`config.paper-equities.yaml`, `broker: name: alpaca`):
#: the four base distributions plus the adapter wheel the profile selects -- the install
#: `keel update` must be able to move as ONE set.
_INSTALLED = {
    "keel-trader": "0.6.0",
    "keel-core": "0.6.0",
    "keel-broker-api": "0.6.0",
    "keel-broker-coinbase": "0.6.0",
    "keel-broker-alpaca": "0.6.0",
}


#: The runbook's deployment layout: the launch folder's OWN `.venv`, its site-packages
#: holding the running `keel` package and the `keel_trader` dist-info.
_VENV_SITE = ".venv/lib/python3.12/site-packages"


def _fake_venv_package(
    launch_dir: Path, *, direct_url: str = "file:///Release/keel_trader-0.6.0-py3-none-any.whl"
) -> Path:
    """Create the fake deployment install under `launch_dir` -- the package resolving
    from the launch folder's OWN `.venv` site-packages, plus a `keel_trader` dist-info
    whose `direct_url.json` names the wheel it was installed from -- and return the
    package file `plan_update` is pointed at."""
    import json

    site = launch_dir / _VENV_SITE
    keel_pkg = site / "keel"
    keel_pkg.mkdir(parents=True, exist_ok=True)
    (keel_pkg / "__init__.py").write_text("")
    dist_info = site / "keel_trader-0.6.0.dist-info"
    dist_info.mkdir(exist_ok=True)
    (dist_info / "direct_url.json").write_text(json.dumps({"url": direct_url}))
    return keel_pkg / "__init__.py"


def _plan(
    launch_dir: Path,
    *,
    version: str = "0.7.0",
    current: str = "0.6.0",
    source: str = "release",
    installed: dict[str, str] | None = None,
    venv_python: Path | None = None,
    extra_assets: tuple[str, ...] = (),
    package_file: Path | None = None,
    direct_url: str = "file:///Release/keel_trader-0.6.0-py3-none-any.whl",
) -> up.UpdatePlan:
    if package_file is None:
        package_file = _fake_venv_package(launch_dir, direct_url=direct_url)
    return up.plan_update(
        up.parse_release(_release_json(version, extra_assets)),
        build=BuildInfo(version=current, commit="deadbeef", dirty=False, source=source),
        installed=dict(_INSTALLED if installed is None else installed),
        launch_dir=launch_dir,
        venv_python=venv_python or launch_dir / ".venv/bin/python",
        package_file=package_file,
    )


def _write_db(path: Path, marker: str) -> None:
    """A REAL SQLite database with one marker row -- the backup pins prove the .bak
    holds the pre-update CONTENT as a consistent snapshot, which only a real database
    (not a byte blob) can show."""
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS marker (value TEXT)")
        conn.execute("INSERT INTO marker VALUES (?)", (marker,))
        conn.commit()
    finally:
        conn.close()


def _read_marker(path: Path) -> str:
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        return str(conn.execute("SELECT value FROM marker").fetchone()[0])
    finally:
        conn.close()


def _deployment(tmp_path: Path) -> Path:
    """A fake launch folder in the runbook's DEPLOYMENT layout: the own `.venv` the
    running package resolves from, two keel databases, an unrelated db, and a Release/
    dir holding the PREVIOUS release's four wheels (the superseded set)."""
    _fake_venv_package(tmp_path)
    _write_db(tmp_path / "keel.db", "original-keel-db")
    _write_db(tmp_path / "keel-live.db", "original-live-db")
    (tmp_path / "unrelated.db").write_bytes(b"not-a-keel-db")
    release = tmp_path / "Release"
    release.mkdir()
    for prefix in up.PRODUCTION_WHEEL_PREFIXES:
        (release / f"{prefix}-0.6.0-py3-none-any.whl").write_bytes(b"old-wheel")
    return tmp_path


class _FakeOps:
    """The injected subprocess/HTTP seams: every call records an event, the downloads
    write real bytes, and the migrate fake MUTATES the database -- so the backup pin
    can prove the .bak captured the PRE-update content."""

    def __init__(self, launch_dir: Path) -> None:
        self.launch_dir = launch_dir
        self.events: list[tuple[str, Any]] = []
        self.fail_verify = False
        self.fail_verify_with = "verify exploded"
        self.fail_install_with: str | None = None
        self.fail_migrate_with: str | None = None
        self.installs = 0

    def download(self, url: str, dest: Path) -> None:
        baks = sorted(p.name for p in self.launch_dir.glob("*.bak-before-0.7.0-*"))
        self.events.append(("download", dest.name, tuple(baks)))
        dest.write_bytes(b"new-wheel-bytes")

    def install(self, venv_python: Path, wheels: Any) -> None:
        # counted FIRST so a spying test can tell the backups' own connections from
        # this pin's reads
        self.installs += 1
        # THE ORDERING PIN, asserted from inside the mutation: by the time anything is
        # installed, every database backup must already exist and OPEN CLEANLY as a
        # consistent SQLite snapshot. If run_update is ever reordered -- or the backup
        # ever stops being a real snapshot -- THIS raises and the test fails.
        baks = sorted(self.launch_dir.glob("*.bak-before-0.7.0-*"))
        assert len(baks) >= 2, f"backups before install: {[b.name for b in baks]}"
        for bak in baks:
            _read_marker(bak)
        if self.fail_install_with is not None and self.installs == 1:
            raise up.UpdateError(self.fail_install_with)
        self.events.append(("install", [Path(w).name for w in wheels]))

    def migrate(self, new_keel: Path, db_path: Path) -> None:
        self.events.append(("migrate", str(new_keel), db_path.name))
        if self.fail_migrate_with is not None:
            raise up.UpdateError(self.fail_migrate_with)
        _write_db(db_path, "migrated-by-the-new-build")

    def verify(self, new_keel: Path, target: str) -> None:
        self.events.append(("verify", str(new_keel), target))
        if self.fail_verify:
            raise up.UpdateError(self.fail_verify_with)

    def empty_download(self, url: str, dest: Path) -> None:
        self.events.append(("download", dest.name))
        dest.write_bytes(b"")


def _run(plan: up.UpdatePlan, ops: _FakeOps, *, gate: bool = True) -> up.UpdateResult:
    steps: list[str] = []
    return up.run_update(
        plan,
        echo=steps.append,
        confirm_gate=lambda: gate,
        download=ops.download,
        install=ops.install,
        migrate=ops.migrate,
        verify=ops.verify,
        now_ts=NOW_TS,
    ), steps


# -- the pure parts: parse, semver, wheel selection ------------------------------------------------


def test_parse_release_reads_the_tag_and_the_asset_list() -> None:
    release = up.parse_release(_release_json("0.7.0"))
    assert release.tag == "v0.7.0"
    assert release.version == "0.7.0"
    names = [asset.name for asset in release.assets]
    assert "keel_trader-0.7.0-py3-none-any.whl" in names
    assert "config.yaml" in names
    assert all(asset.url.startswith("https://") for asset in release.assets)


def test_parse_release_refuses_an_unexpected_shape_honestly() -> None:
    with pytest.raises(up.UpdateError, match="unexpected"):
        up.parse_release(b'{"message": "not found"}')
    with pytest.raises(up.UpdateError, match="unexpected"):
        up.parse_release(b'{"tag_name": "v1", "assets": [{"name": 7}]}')
    with pytest.raises(up.UpdateError, match="payload"):
        up.parse_release(b"not json at all")


def test_latest_release_maps_a_network_failure_to_an_honest_error() -> None:
    def _offline(url: str) -> bytes:
        raise OSError("connection refused")

    with pytest.raises(up.UpdateError, match="GitHub"):
        up.latest_release(fetch=_offline)


def test_the_default_fetch_maps_a_rate_limit_to_an_honest_unauthenticated_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import urllib.error

    def _rate_limited(req: Any, timeout: Any = None) -> Any:
        raise urllib.error.HTTPError(
            str(req.full_url), 403, "Forbidden", None, None  # type: ignore[arg-type]
        )

    monkeypatch.setattr(up.urllib.request, "urlopen", _rate_limited)
    with pytest.raises(up.UpdateError, match="rate-limit"):
        up.latest_release()


def test_version_key_orders_releases_above_pre_releases_and_refuses_junk() -> None:
    assert up.version_key("v0.7.0") == up.version_key("0.7.0")
    assert up.version_key("0.7.0") > up.version_key("0.7.0-rc1")
    assert up.version_key("0.7.1") > up.version_key("0.7.0")
    assert up.version_key("1.0.0") > up.version_key("0.9.9")
    assert up.version_key("not-a-version") is None
    assert up.version_key("0.7") is None


def test_is_newer_version_is_honest_about_pre_releases_and_junk() -> None:
    assert up.is_newer_version("0.7.0", "0.6.0") is True
    assert up.is_newer_version("0.6.0", "0.6.0") is False
    assert up.is_newer_version("0.5.0", "0.6.0") is False
    # a release SUPERSEDES its own pre-release, never the other way round
    assert up.is_newer_version("0.7.0", "0.7.0-rc1") is True
    assert up.is_newer_version("0.7.0-rc2", "0.7.0-rc1") is False
    # junk is a refusal, not a guess
    assert up.is_newer_version("banana", "0.6.0") is None


def test_the_production_wheel_set_is_pinned_by_name_and_alpaca_is_in_it() -> None:
    """#425: the equities profile (`config.paper-equities.yaml`, `broker: name: alpaca`,
    #386) deploys `keel_broker_alpaca` beside the four base wheels, so self-update must
    move it with them -- an updater that left it behind installed four new wheels next to
    one old adapter, failed the verify step's `keel versions` with PARTIAL INSTALL, and
    rolled the WHOLE deployment back to the previous version: every self-update on such a
    box failed, by construction. The set stays STATED BY NAME (never `Release/*.whl`,
    never derived from the config): the release also ships venues a deployment must never
    ride -- fake (registers a `fake` venue entry point), robinhood (an Ed25519 stack for
    an adapter nothing constructs) and kraken (a stub whose every data method raises,
    #313) -- and an exact-tuple pin keeps any future venue a deliberate addition to this
    list rather than an accident of a glob."""
    assert up.PRODUCTION_WHEEL_PREFIXES == (
        "keel_core",
        "keel_broker_api",
        "keel_broker_coinbase",
        "keel_broker_alpaca",
        "keel_trader",
    )
    assert "keel_broker_alpaca" in up.PRODUCTION_WHEEL_PREFIXES
    for excluded in ("keel_broker_fake", "keel_broker_robinhood", "keel_broker_kraken"):
        assert excluded not in up.PRODUCTION_WHEEL_PREFIXES, excluded


def test_the_production_wheels_are_exactly_the_prefixes_and_never_fake_robinhood_kraken() -> None:
    """The selection returns exactly the pinned prefixes' wheels, in order, even though
    the release CARRIES the excluded venue wheels too -- matching by exact
    `<prefix>-<version>-` name is what keeps fake, robinhood and the kraken stub from
    ever riding an update into a deployment."""
    release = up.parse_release(_release_json("0.7.0"))
    wheels = up.select_production_wheels(release, "0.7.0")
    assert [wheel.name for wheel in wheels] == [
        f"{prefix}-0.7.0-py3-none-any.whl" for prefix in up.PRODUCTION_WHEEL_PREFIXES
    ]
    joined = " ".join(wheel.name for wheel in wheels)
    for excluded in ("fake", "robinhood", "kraken"):
        assert excluded not in joined, excluded


def test_an_ambiguous_wheel_match_is_refused_naming_both() -> None:
    """Two assets matching one prefix (a stray re-upload with an extra platform tag,
    say) is a REFUSAL that names both -- never a silent first-match, which could pick
    the wrong artifact and install it into a deployment."""
    import json

    payload = json.dumps(
        {
            "tag_name": "v0.7.0",
            "assets": [
                _asset(name)
                for name in (
                    "keel_core-0.7.0-py3-none-any.whl",
                    "keel_core-0.7.0-cp312-cp312-macosx_11_0.whl",
                    "keel_broker_api-0.7.0-py3-none-any.whl",
                    "keel_broker_coinbase-0.7.0-py3-none-any.whl",
                    "keel_broker_alpaca-0.7.0-py3-none-any.whl",
                    "keel_trader-0.7.0-py3-none-any.whl",
                )
            ],
        }
    ).encode()
    with pytest.raises(up.UpdateError) as excinfo:
        up.select_production_wheels(up.parse_release(payload), "0.7.0")
    message = str(excinfo.value)
    assert "keel_core-0.7.0-py3-none-any.whl" in message
    assert "keel_core-0.7.0-cp312-cp312-macosx_11_0.whl" in message


def test_a_missing_production_wheel_is_named_honestly() -> None:
    import json

    payload = json.dumps(
        {
            "tag_name": "v0.7.0",
            "assets": [
                _asset(name)
                for name in (
                    "keel_core-0.7.0-py3-none-any.whl",
                    "keel_broker_api-0.7.0-py3-none-any.whl",
                    "keel_broker_coinbase-0.7.0-py3-none-any.whl",
                    "config.yaml",
                )
            ],
        }
    ).encode()
    with pytest.raises(up.UpdateError, match="keel_trader"):
        up.select_production_wheels(up.parse_release(payload), "0.7.0")


# -- plan_update: offer semantics, refusals, the deployment facts ----------------------------------


def test_the_plan_offers_the_update_when_the_release_is_newer(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    assert plan.offered is True
    assert plan.refusal_reasons == ()
    assert plan.current_version == "0.6.0"
    assert plan.latest_version == "0.7.0"


def test_the_plan_does_not_offer_the_same_or_an_older_release(tmp_path: Path) -> None:
    same = _plan(tmp_path, version="0.6.0")
    assert same.offered is False
    assert same.refusal_reasons == ()
    older = _plan(tmp_path, version="0.5.9")
    assert older.offered is False
    assert older.refusal_reasons == ()


def test_the_plan_refuses_a_dev_checkout_by_build_source(tmp_path: Path) -> None:
    plan = _plan(tmp_path, source="checkout")
    assert plan.offered is False
    assert any("checkout" in reason.lower() for reason in plan.refusal_reasons)


def test_the_plan_refuses_when_nothing_is_installed(tmp_path: Path) -> None:
    plan = _plan(tmp_path, installed={})
    assert plan.offered is False
    assert any("installed" in reason.lower() for reason in plan.refusal_reasons)


def test_the_plan_offers_the_runbooks_real_deployment_layout(tmp_path: Path) -> None:
    """The runbook's own layout: the running `keel` package resolves from the launch
    folder's OWN `.venv` site-packages (`<launch>/.venv/lib/python3.x/site-packages/
    keel/`), installed from a wheel -- that IS the deployment, and the update is
    OFFERED. The previous detection refused ANY package under the launch folder, which
    refused every real deployment on the box."""
    plan = _plan(tmp_path)
    assert plan.offered is True
    assert plan.refusal_reasons == ()


def test_the_plan_refuses_a_source_keel_dir_under_the_launch_folder(tmp_path: Path) -> None:
    """A checkout run via `uv run keel` resolves `keel` to `<launch>/keel/__init__.py`
    -- a NON-venv path under the launch folder. Deploying wheels would shadow the
    working tree, so it is refused -- detected by LAYOUT, not by the build stamp."""
    (tmp_path / "keel").mkdir()
    (tmp_path / "keel" / "__init__.py").write_text("")
    plan = _plan(tmp_path, package_file=tmp_path / "keel" / "__init__.py")
    assert plan.offered is False
    assert any("source checkout" in reason for reason in plan.refusal_reasons)


def test_the_plan_refuses_a_repo_run_from_outside_the_launch_folder(tmp_path: Path) -> None:
    """`keel` running from a repo checkout (or a system python) while the launch folder
    is elsewhere: the venv the wheels would land in is NOT this launch folder's own --
    refused, naming the outside resolution."""
    elsewhere = tmp_path.parent / "elsewhere-repo"
    plan = _plan(tmp_path, package_file=elsewhere / "keel" / "__init__.py")
    assert plan.offered is False
    assert any("outside the launch folder" in reason.lower() for reason in plan.refusal_reasons)


def test_the_plan_refuses_an_install_that_is_not_a_wheel(tmp_path: Path) -> None:
    """The deployment layout but a non-wheel origin: the `keel_trader` distribution's
    own `direct_url.json` names a source directory (an editable or `pip install .`
    install), not a `.whl` -- a deployment is the release wheels, so this is
    refused rather than updated in place."""
    plan = _plan(tmp_path, direct_url="file:///Users/op/keel-repo")
    assert plan.offered is False
    assert any("wheel" in reason.lower() for reason in plan.refusal_reasons)


def test_the_layout_classifier_refuses_an_unknown_package_file(tmp_path: Path) -> None:
    assert up.deployment_layout_refusal(tmp_path, None) is not None


def test_the_plan_refuses_a_release_missing_a_production_wheel(tmp_path: Path) -> None:
    import json

    payload = json.dumps(
        {
            "tag_name": "v0.7.0",
            "assets": [_asset("keel_core-0.7.0-py3-none-any.whl"), _asset("config.yaml")],
        }
    ).encode()
    plan = up.plan_update(
        up.parse_release(payload),
        build=_RELEASE_BUILD,
        installed=dict(_INSTALLED),
        launch_dir=tmp_path,
        venv_python=tmp_path / ".venv/bin/python",
    )
    assert plan.offered is False
    assert any("keel_trader" in reason for reason in plan.refusal_reasons)


def test_the_plan_backs_up_every_keel_db_in_the_launch_folder(tmp_path: Path) -> None:
    _deployment(tmp_path)
    plan = _plan(tmp_path)
    assert sorted(p.name for p in plan.db_paths) == ["keel-live.db", "keel.db"]


def test_the_plan_names_the_release_dir_and_the_running_venv(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    assert plan.release_dir == tmp_path / "Release"
    assert plan.launch_dir == tmp_path
    venv = tmp_path / ".venv/bin/python"
    assert plan.venv_python == venv


# -- backups: the runbook's name, never an overwrite -----------------------------------------------


def test_backup_path_appends_a_counter_when_a_same_second_name_exists() -> None:
    db = Path("/launch/keel.db")
    base = up.backup_path(db, "0.7.0", NOW_TS)
    second = up.backup_path(db, "0.7.0", NOW_TS, occupied={base.name})
    assert second != base
    assert second.name == f"{base.name}-2"
    third = up.backup_path(db, "0.7.0", NOW_TS, occupied={base.name, second.name})
    assert third.name == f"{base.name}-3"


def test_run_update_twice_in_one_second_writes_two_backups_not_one(tmp_path: Path) -> None:
    """Second granularity is not operator granularity: two runs inside one second (a
    failed run retried immediately) must not overwrite the first backup -- a counter
    suffix is appended, and BOTH exist afterwards."""
    launch = _deployment(tmp_path)
    ops = _FakeOps(launch)
    first, _steps = _run(_plan(launch), ops)
    assert first.ok is True
    second, _steps = _run(_plan(launch), ops)
    assert second.ok is True
    for name in ("keel.db.bak-before-0.7.0-*", "keel-live.db.bak-before-0.7.0-*"):
        assert len(list(launch.glob(name))) == 2, name


# -- run_update against the fake environment -------------------------------------------------------


def test_run_update_refuses_without_the_gate_and_writes_nothing(tmp_path: Path) -> None:
    launch = _deployment(tmp_path)
    plan = _plan(launch)
    ops = _FakeOps(launch)
    steps: list[str] = []
    result = up.run_update(
        plan,
        echo=steps.append,
        confirm_gate=lambda: False,
        download=ops.download,
        install=ops.install,
        migrate=ops.migrate,
        verify=ops.verify,
        now_ts=NOW_TS,
    )
    assert result.ok is False
    assert "nothing was changed" in (result.error or "").lower()
    assert ops.events == []
    assert not list(launch.glob("*.bak-before-*"))
    assert sorted(p.name for p in (launch / "Release").glob("*.whl")) == sorted(
        f"{prefix}-0.6.0-py3-none-any.whl" for prefix in up.PRODUCTION_WHEEL_PREFIXES
    )


def test_run_update_refuses_a_plan_that_is_not_offered(tmp_path: Path) -> None:
    launch = _deployment(tmp_path)
    plan = _plan(launch, source="checkout")
    ops = _FakeOps(launch)
    result, _steps = _run(plan, ops)
    assert result.ok is False
    assert ops.events == []
    assert not list(launch.glob("*.bak-before-*"))


def test_run_update_backs_up_before_installing_and_finishes_the_whole_procedure(
    tmp_path: Path,
) -> None:
    launch = _deployment(tmp_path)
    plan = _plan(launch)
    ops = _FakeOps(launch)
    result, steps = _run(plan, ops)

    assert result.ok is True, result.error
    # the order: download (backups already on disk) -> install -> migrate -> verify,
    # and the gate ran inside the service before ANY of it. One download per production
    # wheel -- FIVE since #425 (alpaca rides with the four), never a count-free glob.
    n_wheels = len(up.PRODUCTION_WHEEL_PREFIXES)
    kinds = [event[0] for event in ops.events]
    assert kinds == ["download"] * n_wheels + ["install", "migrate", "migrate", "verify"]
    # the install carried every wheel PATH and the RUNNING venv's python
    venv = launch / ".venv/bin/python"
    assert ops.events[n_wheels] == (
        "install",
        [f"{prefix}-0.7.0-py3-none-any.whl" for prefix in up.PRODUCTION_WHEEL_PREFIXES],
    )
    # the migrate ran with the NEW venv's keel entry, once per database
    new_keel = venv.parent / "keel"
    assert {event[1] for event in ops.events if event[0] == "migrate"} == {str(new_keel)}
    assert sorted(event[2] for event in ops.events if event[0] == "migrate") == [
        "keel-live.db",
        "keel.db",
    ]
    # the verify demanded the TARGET version
    assert ops.events[-1] == ("verify", str(new_keel), "0.7.0")
    # backups were taken with the runbook's naming convention and never deleted
    baks = sorted(p.name for p in launch.glob("keel*.db.bak-before-0.7.0-*"))
    assert len(baks) == 2
    assert all(".bak-before-0.7.0-" in name for name in baks)
    # the backups hold the PRE-update content (as clean, openable snapshots) even though
    # migrate rewrote the dbs
    assert baks[0].startswith("keel-live.db.bak-before-")
    assert _read_marker(launch / baks[0]) == "original-live-db"
    # every step was streamed
    assert any("download" in step.lower() for step in steps)
    assert any("back" in step.lower() for step in steps)


def test_run_update_deletes_only_the_superseded_wheels_and_only_on_success(
    tmp_path: Path,
) -> None:
    launch = _deployment(tmp_path)
    plan = _plan(launch)
    ops = _FakeOps(launch)
    result, _steps = _run(plan, ops)
    assert result.ok is True
    wheels = sorted(p.name for p in (launch / "Release").glob("*.whl"))
    assert wheels == sorted(
        f"{prefix}-0.7.0-py3-none-any.whl" for prefix in up.PRODUCTION_WHEEL_PREFIXES
    )


def test_run_update_refuses_a_zero_byte_download(tmp_path: Path) -> None:
    launch = _deployment(tmp_path)
    plan = _plan(launch)
    ops = _FakeOps(launch)
    steps: list[str] = []
    result = up.run_update(
        plan,
        echo=steps.append,
        confirm_gate=lambda: True,
        download=ops.empty_download,
        install=ops.install,
        migrate=ops.migrate,
        verify=ops.verify,
        now_ts=NOW_TS,
    )
    assert result.ok is False
    assert "empty" in (result.error or "").lower()
    # nothing was installed: the running binary is unchanged
    assert not any(event[0] == "install" for event in ops.events)
    assert "nothing was installed" in (result.error or "").lower()
    # the torn file was REMOVED, and the failure says so -- a partial wheel left in
    # Release/ must not poison a later rollback's superseded set
    assert "removed" in (result.error or "").lower() or any(
        "removed" in step.lower() for step in steps
    )
    assert not list((launch / "Release").glob("*0.7.0*.whl"))
    # the previous wheels (the recovery path) stay -- every production wheel's
    # superseded copy, which since #425 includes the alpaca adapter's
    assert len(list((launch / "Release").glob("*.whl"))) == len(up.PRODUCTION_WHEEL_PREFIXES)


def test_a_download_larger_than_the_guard_is_refused_not_streamed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The read is BOUNDED (200 MiB, generous vs the ~1 MiB wheels): a mis-pointed URL
    (a release page, someone's screen recording) must be refused, never streamed to
    disk -- and nothing lands in Release/ when it is."""

    class _HugeResponse:
        def __enter__(self) -> _HugeResponse:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        def read(self, n: int = -1) -> bytes:
            return b"x" * (n + 1)  # always one byte MORE than the bound allows

    monkeypatch.setattr(
        up.urllib.request, "urlopen", lambda url, timeout=None: _HugeResponse()
    )
    with pytest.raises(up.UpdateError, match="200 MiB"):
        up._download_file("https://example/huge", tmp_path / "w.whl")
    assert not (tmp_path / "w.whl").exists()


def test_run_update_verify_failure_is_loud_reinstalls_the_previous_wheels_best_effort(
    tmp_path: Path,
) -> None:
    launch = _deployment(tmp_path)
    plan = _plan(launch)
    ops = _FakeOps(launch)
    ops.fail_verify = True
    result, steps = _run(plan, ops)

    assert result.ok is False
    assert result.rolled_back is True
    # the previous wheels were re-installed from Release/ (they were still there)
    kinds = [event[0] for event in ops.events]
    assert kinds.count("install") == 2
    reinstall = [event for event in ops.events if event[0] == "install"][1]
    assert sorted(reinstall[1]) == sorted(
        f"{prefix}-0.6.0-py3-none-any.whl" for prefix in up.PRODUCTION_WHEEL_PREFIXES
    )
    # nothing was cleaned up: the superseded wheels stay (they are the recovery path)
    wheels = sorted(p.name for p in (launch / "Release").glob("*.whl"))
    assert any("0.6.0" in name for name in wheels)
    assert any("0.7.0" in name for name in wheels)
    # the failure says the REAL state and names the manual recovery
    error = (result.error or "").lower()
    assert "verify" in error
    assert "installed" in error  # the new wheels ARE in the venv -- pip already replaced
    assert "runbook" in error or "manual" in error
    assert any("best-effort" in step.lower() or "reinstall" in step.lower() for step in steps)
    # the rollback text points at the BACKUPS as the real data recovery, and states the
    # additive-migration belief as an ASSUMPTION, never a guarantee
    assert any("assumption" in step.lower() for step in steps)
    assert any(".bak-before" in step for step in steps)


def test_run_update_install_failure_says_the_venv_was_not_updated_and_never_rolls_back(
    tmp_path: Path,
) -> None:
    """Phase-true failure text: when the failure IS the install (uv absent, a timeout,
    a torn wheel that passed the non-empty check), the wheels are NOT claimed
    installed -- the venv was not updated, or is half-updated, and `keel versions` is
    the honest next step. No reinstall of the previous wheels is attempted (uv already
    refused this venv once; the previous wheels are still in Release/ untouched)."""
    launch = _deployment(tmp_path)
    plan = _plan(launch)
    ops = _FakeOps(launch)
    ops.fail_install_with = "uv pip install failed (exit 2): wheel is corrupted"
    result, steps = _run(plan, ops)

    assert result.ok is False
    assert result.rolled_back is False
    assert [event[0] for event in ops.events].count("install") == 0  # no reinstall attempted
    error = (result.error or "").lower()
    assert "not updated" in error
    assert "half-updated" in error
    assert "keel versions" in error
    # the downloaded (possibly torn) wheels were removed from Release/ so they cannot
    # poison a later rollback's superseded set; the previous four stay
    wheels = sorted(p.name for p in (launch / "Release").glob("*.whl"))
    assert wheels == sorted(
        f"{prefix}-0.6.0-py3-none-any.whl" for prefix in up.PRODUCTION_WHEEL_PREFIXES
    )
    assert any("removed" in step.lower() for step in steps)
    assert "runbook" in error or "manual" in error


def test_run_update_migrate_failure_states_the_wheels_are_installed(tmp_path: Path) -> None:
    """Phase-true failure text for a POST-install failure (the migrate step): the
    wheels ARE installed -- pip replaced the packages -- and the best-effort reinstall
    of the previous wheels runs exactly as for a verify failure."""
    launch = _deployment(tmp_path)
    plan = _plan(launch)
    ops = _FakeOps(launch)
    ops.fail_migrate_with = "`keel migrate --db keel.db` failed (exit 1): locked"
    result, _steps = _run(plan, ops)

    assert result.ok is False
    assert result.rolled_back is True
    error = (result.error or "").lower()
    assert "after the wheels were installed" in error
    assert "are installed" in error
    assert [event[0] for event in ops.events].count("install") == 2  # the rollback ran


def test_the_default_backup_uses_the_sqlite_backup_api_not_a_file_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A live database is snapshotted through SQLite's own online-backup API (a
    connection pair per database): a plain `copy2` of a database with a live rollback
    journal (launchd writers) can be torn -- half old pages, half new -- and restore as
    corrupt. The spy proves the sqlite route, the functional test below the guarantee."""
    launch = _deployment(tmp_path)
    plan = _plan(launch)
    ops = _FakeOps(launch)
    real_connect = sqlite3.connect
    connected: list[tuple[object, bool]] = []

    def _spy_connect(*args: object, **kwargs: object) -> object:
        # tag each connection with whether it happened BEFORE anything installed -- the
        # backups' own connections, not the ordering pin's reads
        before_install = ops.installs == 0
        connected.append((args[0] if args else kwargs.get("database"), before_install))
        return real_connect(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(up.sqlite3, "connect", _spy_connect)
    result, _steps = _run(plan, ops)
    assert result.ok is True
    # the sqlite route: a source+destination connection pair per database, all made
    # before anything installed -- a plain copy2 would connect zero times
    backup_connects = [c for c, before_install in connected if before_install]
    assert len(backup_connects) == 4
    assert len([c for c in backup_connects if ".bak-before-" in str(c)]) == 2


def test_a_backup_of_a_midtransaction_database_opens_cleanly(tmp_path: Path) -> None:
    """The functional guarantee: with a writer holding an OPEN transaction (the
    launchd-writer pattern), the backup opens cleanly, passes integrity_check, and
    carries the last COMMITTED state -- never a torn page mix."""
    db = tmp_path / "keel.db"
    _write_db(db, "committed")
    dest = tmp_path / "keel.db.bak-before-0.7.0-x"
    writer = sqlite3.connect(db)
    try:
        writer.execute("BEGIN")
        writer.execute("INSERT INTO marker VALUES ('not-committed')")
        up._backup_file(db, dest)
    finally:
        writer.rollback()
        writer.close()
    assert _read_marker(dest) == "committed"  # integrity_ok + the committed row only


def test_run_update_verify_failure_without_previous_wheels_says_manual_recovery(
    tmp_path: Path,
) -> None:
    launch = _deployment(tmp_path)
    for wheel in (launch / "Release").glob("*.whl"):
        wheel.unlink()
    plan = _plan(launch)
    ops = _FakeOps(launch)
    ops.fail_verify = True
    result, steps = _run(plan, ops)

    assert result.ok is False
    assert result.rolled_back is False
    assert [event[0] for event in ops.events].count("install") == 1
    error = (result.error or "").lower()
    assert "manual" in error
    assert "runbook" in error or "deploying a new version" in error
    # the backups still exist: a failed update never touches them
    assert len(list(launch.glob("*.bak-before-0.7.0-*"))) == 2


def test_the_default_install_runs_uv_with_the_venv_and_every_production_wheel_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    launch = _deployment(tmp_path)
    venv = launch / ".venv/bin/python"
    recorded: list[list[str]] = []

    def _run_uv(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        recorded.append(argv)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(up.subprocess, "run", _run_uv)
    up._uv_install(venv, [launch / "Release" / f"{p}-0.7.0-py3-none-any.whl"
                          for p in up.PRODUCTION_WHEEL_PREFIXES])
    assert recorded == [
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(venv),
            # the runbook's own command carries --find-links Release so the wheels'
            # ==-pinned siblings resolve from Release/ (they are not on PyPI)
            "--find-links",
            str(launch / "Release"),
            *[str(launch / "Release" / f"{p}-0.7.0-py3-none-any.whl")
              for p in up.PRODUCTION_WHEEL_PREFIXES],
        ]
    ]


def test_an_absent_uv_is_an_honest_error_naming_the_manual_procedure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    def _no_uv(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        raise FileNotFoundError("uv")

    monkeypatch.setattr(up.subprocess, "run", _no_uv)
    with pytest.raises(up.UpdateError) as excinfo:
        up._uv_install(tmp_path / ".venv/bin/python", [tmp_path / "w.whl"])
    message = str(excinfo.value).lower()
    assert "uv" in message
    assert "runbook" in message or "deploying a new version" in message


def test_parse_versions_output_reads_the_distribution_rows() -> None:
    healthy = (
        "keel 0.7.0+abc [release]\n"
        "\n"
        "keel-broker-api       0.7.0\n"
        "keel-broker-coinbase  0.7.0\n"
        "keel-core             0.7.0\n"
        "keel-trader           0.7.0\n"
        "\n"
        "ok: 4 keel distributions, all at 0.7.0.\n"
    )
    assert up.parse_versions_output(healthy) == {
        "keel-broker-api": "0.7.0",
        "keel-broker-coinbase": "0.7.0",
        "keel-core": "0.7.0",
        "keel-trader": "0.7.0",
    }


def test_the_default_verify_demands_every_production_distribution_at_the_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Since #425 the demanded set is FIVE: `keel-broker-alpaca` must be present and at
    the target too, or an equities deployment whose adapter was never moved would
    verify as healthy while its venue import stayed a release behind."""

    def _keel_versions(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        text = (
            "keel 0.7.0+abc [release]\n\nkeel-core  0.7.0\nkeel-trader  0.7.0\n"
            "keel-broker-api  0.7.0\nkeel-broker-coinbase  0.7.0\n"
            "keel-broker-alpaca  0.7.0\n\n"
            "ok: 5 keel distributions, all at 0.7.0.\n"
        )
        return subprocess.CompletedProcess(argv, 0, text, "")

    monkeypatch.setattr(up.subprocess, "run", _keel_versions)
    up._verify_versions(Path("/venv/bin/keel"), "0.7.0")  # agrees -> silent

    def _missing_alpaca(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        # the pre-#425 healthy shape: no alpaca row at all -- now a verify failure,
        # because an equities deployment without the adapter is not a healthy install
        text = (
            "keel 0.7.0+abc [release]\n\nkeel-core  0.7.0\nkeel-trader  0.7.0\n"
            "keel-broker-api  0.7.0\nkeel-broker-coinbase  0.7.0\n\n"
            "ok: 4 keel distributions, all at 0.7.0.\n"
        )
        return subprocess.CompletedProcess(argv, 0, text, "")

    monkeypatch.setattr(up.subprocess, "run", _missing_alpaca)
    with pytest.raises(up.UpdateError, match="keel-broker-alpaca is not installed"):
        up._verify_versions(Path("/venv/bin/keel"), "0.7.0")

    def _partial(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        text = (
            "keel 0.7.0+abc [release]\n\nkeel-core  0.5.5\nkeel-trader  0.7.0\n\n"
            "error: PARTIAL INSTALL: 2 keel distributions at 2 different versions.\n"
        )
        return subprocess.CompletedProcess(argv, 1, text, "")

    monkeypatch.setattr(up.subprocess, "run", _partial)
    with pytest.raises(up.UpdateError, match="PARTIAL INSTALL"):
        up._verify_versions(Path("/venv/bin/keel"), "0.7.0")


# -- the relaunch: argv reconstruction and the execv closure ---------------------------------------


def test_build_relaunch_argv_replaces_the_entry_and_keeps_the_tui_args() -> None:
    venv = Path("/deployment/.venv/bin/python")
    argv = up.build_relaunch_argv(
        venv, ["/deployment/.venv/bin/keel", "tui", "--config", "config.live-sandbox.yaml"]
    )
    assert argv == [
        "/deployment/.venv/bin/keel",
        "tui",
        "--config",
        "config.live-sandbox.yaml",
    ]


def test_build_relaunch_argv_preserves_flags_before_the_subcommand_verbatim() -> None:
    """A deployment wrapper execs `keel --config X --db Y tui`: argv[0] is the WRAPPER,
    not the subcommand -- the arguments after argv[0] must be carried VERBATIM, byte
    for byte, no reordering and no prepending. (The old fallback prepended `tui` here,
    producing `keel tui --config X --db Y tui` and a click usage error on every
    relaunch of a wrapped deployment.)"""
    venv = Path("/deployment/.venv/bin/python")
    original = [
        "/deployment/keel-live",
        "--config",
        "config.live-sandbox.yaml",
        "--db",
        "keel-live.db",
        "tui",
    ]
    assert up.build_relaunch_argv(venv, original) == [
        "/deployment/.venv/bin/keel",
        "--config",
        "config.live-sandbox.yaml",
        "--db",
        "keel-live.db",
        "tui",
    ]


def test_build_relaunch_argv_preserves_a_leading_subcommand_verbatim() -> None:
    venv = Path("/deployment/.venv/bin/python")
    original = ["/deployment/.venv/bin/keel", "tui", "--interval", "5"]
    assert up.build_relaunch_argv(venv, original) == [
        "/deployment/.venv/bin/keel",
        "tui",
        "--interval",
        "5",
    ]


def test_build_relaunch_argv_falls_back_to_the_tui_command_when_the_argv_does_not_name_it(
) -> None:
    venv = Path("/deployment/.venv/bin/python")
    argv = up.build_relaunch_argv(venv, ["/deployment/keel-live"])
    assert argv[0] == "/deployment/.venv/bin/keel"
    assert argv[1] == "tui"


def test_relaunch_tui_execvs_the_new_console_entry(tmp_path: Path) -> None:
    venv = tmp_path / ".venv/bin/python"
    recorded: list[tuple[str, list[str]]] = []

    def _fake_execv(path: str, argv: list[str]) -> None:
        recorded.append((path, argv))

    relaunch = up.relaunch_tui(venv, [str(venv.parent / "keel"), "tui"], execv=_fake_execv)
    with pytest.raises(up.UpdateError, match="relaunch"):
        relaunch()  # a real execv never returns; the fake does, and the closure says so
    assert recorded == [(str(venv.parent / "keel"), [str(venv.parent / "keel"), "tui"])]


def test_relaunch_tui_maps_an_execv_oserror_to_the_manual_instruction(tmp_path: Path) -> None:
    """An execv that RAISES (permissions, a missing interpreter) surfaces as the
    service's honest UpdateError naming the manual `keel tui` -- never a bare OSError
    for a front-end to crash on."""

    def _raising_execv(path: str, argv: list[str]) -> None:
        raise OSError(13, "permission denied")

    relaunch = up.relaunch_tui(
        tmp_path / ".venv/bin/python",
        ["/old/keel", "tui"],
        execv=_raising_execv,
    )
    with pytest.raises(up.UpdateError, match="keel tui") as excinfo:
        relaunch()
    assert "permission denied" in str(excinfo.value)


# -- the gate: ONE wording, both front-ends --------------------------------------------------------


def test_the_gate_wording_names_version_launch_folder_and_the_replaced_binary(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path)
    action = up.gate_action(plan)
    detail = up.gate_detail(plan)
    assert "0.6.0" in action and "0.7.0" in action
    lowered = detail.lower()
    assert str(tmp_path) in detail  # the launch folder
    assert "release" in lowered  # the Release/ dir
    assert "back" in lowered  # the backups
    assert "replac" in lowered  # the RUNNING binary is replaced
    assert str(plan.venv_python) in detail


def test_typed_update_gate_is_the_clis_own_and_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import click as click_mod

    import keel.commands._common as common

    plan = _plan(tmp_path)
    asked: list[tuple[str, str]] = []

    def _refusing(action: str, detail: str) -> None:
        asked.append((action, detail))
        raise click_mod.ClickException("aborted (confirmation not given).")

    monkeypatch.setattr(common, "_require_interactive_confirmation", _refusing)
    assert up.typed_update_gate(plan) is False

    def _accepting(action: str, detail: str) -> None:
        asked.append((action, detail))

    monkeypatch.setattr(common, "_require_interactive_confirmation", _accepting)
    assert up.typed_update_gate(plan) is True

    # the wording is the SHARED one, verbatim -- what `keel update` asks is what the
    # console's update view asks
    assert asked[0] == (up.gate_action(plan), up.gate_detail(plan))
    assert asked[1] == asked[0]


# -- the renderers: one implementation, both front-ends --------------------------------------------


def test_render_plan_lines_offer_and_refusal_and_up_to_date(tmp_path: Path) -> None:
    _deployment(tmp_path)  # the databases exist, so the plan names the backups
    offered = "\n".join(up.render_plan_lines(_plan(tmp_path)))
    assert "0.6.0" in offered and "0.7.0" in offered
    assert "->" in offered
    for prefix in up.PRODUCTION_WHEEL_PREFIXES:
        assert f"{prefix}-0.7.0-py3-none-any.whl" in offered
    assert str(tmp_path / "Release") in offered
    assert ".bak-before-0.7.0-" in offered
    assert str(tmp_path / ".venv/bin/python") in offered

    checkout = "\n".join(up.render_plan_lines(_plan(tmp_path, source="checkout")))
    assert "not offered" in checkout.lower()
    assert "checkout" in checkout.lower()

    current = "\n".join(up.render_plan_lines(_plan(tmp_path, version="0.6.0")))
    assert "latest" in current.lower()


def test_render_result_lines_names_the_state_and_the_recovery(tmp_path: Path) -> None:
    ok = up.UpdateResult(ok=True, steps=(), error=None, rolled_back=False, backups=())
    assert "complete" in "\n".join(up.render_result_lines(ok)).lower()
    failed = up.UpdateResult(
        ok=False,
        steps=(),
        error="verify failed: PARTIAL INSTALL -- the manual procedure is the runbook's",
        rolled_back=True,
        backups=(),
    )
    text = "\n".join(up.render_result_lines(failed)).lower()
    assert "verify failed" in text
    assert "rolled back" in text or "reinstall" in text


# -- the CLI front-end -----------------------------------------------------------------------------


def _cli_plan_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point the CLI's plan at a fake release/deployment: no network, no install."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(up, "latest_release", lambda fetch=None: up.parse_release(
        _release_json("0.7.0")))
    monkeypatch.setattr(up, "build_info", lambda: _RELEASE_BUILD)
    monkeypatch.setattr(up, "installed_distributions", lambda: dict(_INSTALLED))
    monkeypatch.setattr(
        up, "_running_python", lambda: tmp_path / ".venv/bin/python"
    )
    monkeypatch.setattr(
        up, "_running_package_file", lambda: _fake_venv_package(tmp_path)
    )


def test_cli_check_prints_the_offer_and_mutates_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from keel.cli import cli

    _cli_plan_env(monkeypatch, tmp_path)

    def _must_not_run(*_args: Any, **_kwargs: Any) -> up.UpdateResult:
        raise AssertionError("run_update must not run under --check")

    monkeypatch.setattr(up, "run_update", _must_not_run)
    result = CliRunner().invoke(cli, ["update", "--check"])
    assert result.exit_code == 0, result.output
    assert "0.6.0" in result.output and "0.7.0" in result.output
    assert not (tmp_path / "Release").exists()  # not even the directory


def test_cli_check_prints_the_dev_checkout_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from keel.cli import cli

    _cli_plan_env(monkeypatch, tmp_path)
    monkeypatch.setattr(up, "build_info", lambda: BuildInfo(
        version="0.6.0", commit="deadbeef", dirty=False, source="checkout"))
    result = CliRunner().invoke(cli, ["update", "--check"])
    assert result.exit_code == 0
    assert "checkout" in result.output.lower()
    assert "not offered" in result.output.lower()


def test_cli_full_update_gate_refusal_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Off a TTY the typed gate fails closed: the command says so, exits non-zero,
    and not one file moves (no download, no backup, no install)."""
    from keel.cli import cli

    _cli_plan_env(monkeypatch, tmp_path)
    (tmp_path / "keel.db").write_bytes(b"db")
    monkeypatch.setattr("keel.commands._common._is_interactive", lambda: False)

    def _no_network(url: str, dest: Path) -> None:
        raise AssertionError("a refused gate must not download")

    monkeypatch.setattr(up, "_download_file", _no_network)
    result = CliRunner().invoke(cli, ["update"])
    assert result.exit_code != 0
    assert "confirmation" in result.output.lower()
    assert not list(tmp_path.glob("*.bak-before-*"))


def test_cli_full_update_runs_the_service_and_prints_the_relaunch_instruction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from keel.cli import cli

    _cli_plan_env(monkeypatch, tmp_path)
    launch = _deployment(tmp_path)
    monkeypatch.chdir(launch)
    monkeypatch.setattr("keel.commands._common._is_interactive", lambda: True)
    monkeypatch.setattr(
        "keel.commands._common._require_interactive_confirmation",
        lambda action, detail: None,
    )
    ops = _FakeOps(launch)
    monkeypatch.setattr(up, "_download_file", ops.download)
    monkeypatch.setattr(up, "_uv_install", ops.install)
    monkeypatch.setattr(up, "_migrate_db", ops.migrate)
    monkeypatch.setattr(up, "_verify_versions", ops.verify)
    result = CliRunner().invoke(cli, ["update"], input="yes\n")
    assert result.exit_code == 0, result.output
    # the whole procedure ran against the fake seams
    assert ops.events
    # the CLI does NOT auto-relaunch -- it prints the instruction
    assert "keel tui" in result.output
