"""What an installer decides, and the one direction where "versions differ, so update" is wrong.

The desktop product has no self-update (#439's option A), so the installer IS the update path.
That makes "what happens when this build meets the one already on disk" a question something has
to answer correctly every time -- and an Inno Setup script or a `.pkg` postinstall is not a place
where an answer can be tested. It is answered here instead.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from keel.install import (
    DOWNGRADE_WARNING,
    NEVER_TOUCHED,
    RELEASES_URL,
    InstallDecision,
    default_deployment_dir,
    default_program_dir,
    fallback_program_dir,
    is_packaged,
    packaged_update_refusal,
    plan_install,
)

# -- the decision --------------------------------------------------------------------------


def test_nothing_installed_is_a_fresh_install_and_asks_nothing() -> None:
    plan = plan_install("0.11.0", None)
    assert plan.decision is InstallDecision.FRESH
    assert plan.may_proceed_silently


def test_a_newer_build_updates_without_asking() -> None:
    plan = plan_install("0.11.0", "0.10.0")
    assert plan.decision is InstallDecision.UPGRADE
    assert plan.may_proceed_silently
    assert "0.10.0" in plan.summary and "0.11.0" in plan.summary


def test_the_same_version_asks_before_overwriting() -> None:
    """Reinstalling the same build is almost always a repair, so offer it -- but never silently."""
    plan = plan_install("0.10.0", "0.10.0")
    assert plan.decision is InstallDecision.REINSTALL
    assert plan.needs_confirmation
    assert plan.warning is not None


def test_an_older_build_asks_AND_warns_that_migrations_do_not_reverse() -> None:
    """The case that makes "versions differ, so update" wrong in one direction.

    `keel/data/db.py` migrates with `if current < target` and ships no down-migrations. A
    database already at schema N, opened by a build expecting N-2, does NOT fail loudly:
    `migrate` finds nothing to apply and returns, and the old code then runs against tables it
    was never written against. Silence is the whole hazard, so the warning has to be loud."""
    plan = plan_install("0.9.0", "0.10.0")
    assert plan.decision is InstallDecision.DOWNGRADE
    assert plan.needs_confirmation
    assert plan.warning == DOWNGRADE_WARNING
    assert "forward-only" in plan.warning
    assert "OLDER" in plan.summary


def test_the_downgrade_warning_names_the_recovery_and_not_just_the_risk() -> None:
    """A warning that says only "this is dangerous" leaves the reader with no move. The recovery
    is a database backup taken BEFORE the upgrade -- not running the old build anyway."""
    assert "backup" in DOWNGRADE_WARNING
    assert "before the upgrade" in DOWNGRADE_WARNING


@pytest.mark.parametrize(
    ("incoming", "installed"),
    [("nightly", "0.10.0"), ("0.10.0", "nightly"), ("", "0.10.0"), ("0.10.0", "")],
)
def test_a_version_that_cannot_be_compared_asks_rather_than_guessing(
    incoming: str, installed: str
) -> None:
    """ "Cannot tell which is newer" must never resolve to "probably fine". It carries the
    downgrade warning too, because an uncomparable pair might BE a downgrade."""
    plan = plan_install(incoming, installed)
    assert plan.decision is InstallDecision.UNCOMPARABLE
    assert plan.needs_confirmation
    assert plan.warning is not None
    assert "forward-only" in plan.warning


def test_only_a_fresh_install_or_a_genuine_upgrade_proceeds_silently() -> None:
    """The closed statement of the rule: every other outcome stops and asks."""
    silent = {
        plan_install(a, b).decision
        for a, b in [("0.11.0", None), ("0.11.0", "0.10.0")]
        if plan_install(a, b).may_proceed_silently
    }
    assert silent == {InstallDecision.FRESH, InstallDecision.UPGRADE}
    for incoming, installed in [("0.10.0", "0.10.0"), ("0.9.0", "0.10.0"), ("x", "0.10.0")]:
        assert not plan_install(incoming, installed).may_proceed_silently


def test_version_comparison_has_exactly_one_home() -> None:
    """`plan_install` reads `keel.commands.update.version_key` rather than parsing semver again.
    A second reader would be a second place for "is this newer" to disagree -- and the two would
    disagree on exactly the strings nobody tested."""
    import inspect as inspect_mod

    from keel import install

    assert "version_key" in inspect_mod.getsource(install.plan_install)


# -- where things go -----------------------------------------------------------------------


def test_the_program_and_the_deployment_are_different_directories() -> None:
    """The trap this module exists to avoid. The program directory is replaced WHOLESALE on every
    update, so anything of the operator's that lived there would be destroyed by an upgrade."""
    for platform in ("darwin", "win32"):
        assert default_program_dir(platform) != default_deployment_dir(platform)


@pytest.mark.parametrize(
    ("platform", "expected"),
    [("darwin", "/Applications/keel.app"), ("win32", "Programs")],
)
def test_the_proposed_program_directory_is_per_user_and_os_appropriate(
    platform: str, expected: str
) -> None:
    """Per-user on both, deliberately: a machine-wide install needs elevation, and an elevation
    prompt on a first run is the friction this milestone exists to remove."""
    assert expected in str(default_program_dir(platform, home=Path("/home/tester")))


def test_macos_offers_a_per_user_fallback_and_windows_needs_none() -> None:
    """`/Applications` needs admin on a managed machine; `~/Applications` is the documented
    per-user equivalent. Windows' default is already per-user, so there is nothing to fall back
    to -- and inventing one would be a second path for no reason."""
    mac = fallback_program_dir("darwin", home=Path("/home/tester"))
    assert mac is not None and "Applications" in str(mac)
    assert fallback_program_dir("win32", home=Path("/home/tester")) is None


def test_the_proposed_deployment_directory_is_the_one_the_app_will_look_in() -> None:
    """An installer that proposed a folder the runtime does not discover would produce a
    deployment that appears EMPTY on first launch -- config written, database written, and a
    dashboard reporting a healthy install with no history."""
    from keel_core import paths

    assert default_deployment_dir() == paths.app_data_dir()
    assert default_deployment_dir(sys.platform) == paths.app_data_dir()


def test_the_untouchable_list_covers_every_piece_of_operator_state() -> None:
    """Config, databases, credentials and logs. An operator's allowlist, caps and trading mode
    are hand-edited and irreplaceable; a database is the only record of what the engine did."""
    assert "config*.yaml" in NEVER_TOUCHED
    assert "keel*.db" in NEVER_TOUCHED
    assert ".env" in NEVER_TOUCHED


def test_the_untouchable_patterns_match_a_real_deployments_files(tmp_path: Path) -> None:
    """The patterns are matched against real filenames, not eyeballed: `keel*.db` must actually
    catch `keel-live.db`, and a typo in one of them would silently protect nothing."""
    for name in ("config.yaml", "config.live-sandbox.yaml", "keel.db", "keel-live.db", ".env"):
        (tmp_path / name).touch()
    matched = {
        path.name for pattern in NEVER_TOUCHED for path in tmp_path.glob(pattern.rstrip("/"))
    }
    assert matched == {
        "config.yaml",
        "config.live-sandbox.yaml",
        "keel.db",
        "keel-live.db",
        ".env",
    }


# -- what a packaged install says about updating -------------------------------------------


def test_a_venv_install_is_not_packaged() -> None:
    """The test suite runs from a venv, so this also proves the detector is not simply True."""
    assert is_packaged() is False


def test_both_freezer_markers_are_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    """PyInstaller sets `sys.frozen` for every build mode but `sys._MEIPASS` only for
    `--onefile`, and other freezers set one or the other. A false negative is the outcome #439
    exists to stop: a desktop user told to install `uv`."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert is_packaged() is True
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", "/tmp/whatever", raising=False)
    assert is_packaged() is True


def test_the_packaged_refusal_names_the_download_and_not_a_command() -> None:
    """Every other refusal `keel update` produces is correct and useless to a desktop user: they
    talk about `site-packages` layouts and tell the reader to put `uv` on PATH."""
    message = packaged_update_refusal()
    assert RELEASES_URL in message
    assert "uv" not in message.split()
    assert "site-packages" not in message
    assert "config" in message and "database" in message
    # D6 (#439): the refusal also names the page that explains HOW updates arrive, so the
    # sentence a desktop user reads is not a dead end.
    assert "docs/desktop-install.md" in message


def test_keel_update_refuses_a_packaged_install_and_says_why(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Wired into the plan and driven through it, not merely available -- a refusal nobody calls
    is documentation. Built against an OTHERWISE-VALID deployment layout, so the packaged
    refusal is the thing being observed rather than one of the four the layout would produce
    anyway."""
    import keel.commands.update as update_mod
    from tests.commands.test_update import _plan

    plan = _plan(tmp_path)
    assert plan.offered, "the fixture must be updatable, or this test proves nothing"

    monkeypatch.setattr(update_mod, "is_packaged", lambda: True)
    packaged = _plan(tmp_path)
    assert not packaged.offered
    assert any(RELEASES_URL in reason for reason in packaged.refusal_reasons)
    # And it is the FIRST thing said: every other refusal talks about venv layouts and `uv`,
    # which is exactly the advice a desktop user cannot act on.
    assert RELEASES_URL in packaged.refusal_reasons[0]


# -- the marker, and the command an installer script calls ------------------------------------


def test_a_marker_round_trips(tmp_path: Path) -> None:
    from keel.install import read_installed_version, write_install_marker

    write_install_marker(tmp_path, "0.10.0", commit="abc123")
    assert read_installed_version(tmp_path) == "0.10.0"


def test_the_marker_goes_in_the_program_directory_and_is_ini(tmp_path: Path) -> None:
    """INI rather than JSON because Inno Setup reads INI natively and would otherwise need a JSON
    parser written in Pascal -- and a hand-rolled parser deciding whether to overwrite someone's
    install is not a trade worth making."""
    from keel.install import INSTALL_MARKER, write_install_marker

    path = write_install_marker(tmp_path, "0.10.0")
    assert path.name == INSTALL_MARKER
    assert path.suffix == ".ini"
    assert "[keel]" in path.read_text()


@pytest.mark.parametrize(
    "content", ["", "not ini at all", "[keel]\n", "[keel]\nversion=\n", "[other]\nversion=1\n"]
)
def test_an_unusable_marker_reads_as_unknown_and_never_raises(tmp_path: Path, content: str) -> None:
    """Missing, unreadable, malformed and empty all mean "cannot establish what is installed",
    which `plan_install` turns into a confirmation rather than a silent overwrite. An installer
    that crashed while deciding whether to overwrite would be worse than one that asks."""
    from keel.install import INSTALL_MARKER, plan_install_into, read_installed_version

    (tmp_path / INSTALL_MARKER).write_text(content)
    assert read_installed_version(tmp_path) is None
    assert plan_install_into(tmp_path, "0.11.0").decision is InstallDecision.FRESH


def test_a_directory_with_no_marker_reads_as_nothing_installed(tmp_path: Path) -> None:
    from keel.install import read_installed_version

    assert read_installed_version(tmp_path) is None
    assert read_installed_version(tmp_path / "does-not-exist") is None


@pytest.mark.parametrize(
    ("installed", "incoming", "code"),
    [
        (None, "0.11.0", 0),
        ("0.10.0", "0.11.0", 0),
        ("0.10.0", "0.10.0", 2),
        ("0.11.0", "0.10.0", 2),
    ],
)
def test_install_plan_exits_zero_to_proceed_and_two_to_confirm(
    tmp_path: Path, installed: str | None, incoming: str, code: int
) -> None:
    """The exit code carries the decision, so a script that reads nothing but the status still
    fails safe: anything non-zero means stop and ask."""
    from click.testing import CliRunner

    from keel.cli import cli
    from keel.install import write_install_marker

    if installed is not None:
        write_install_marker(tmp_path, installed)
    result = CliRunner().invoke(
        cli, ["install-plan", "--target", str(tmp_path), "--incoming", incoming]
    )
    assert result.exit_code == code, result.output


def test_install_plan_json_carries_the_warning(tmp_path: Path) -> None:
    import json as json_mod

    from click.testing import CliRunner

    from keel.cli import cli
    from keel.install import write_install_marker

    write_install_marker(tmp_path, "0.11.0")
    result = CliRunner().invoke(
        cli, ["install-plan", "--target", str(tmp_path), "--incoming", "0.10.0", "--json"]
    )
    payload = json_mod.loads(result.output)
    assert payload["decision"] == "downgrade"
    assert payload["needs_confirmation"] is True
    assert "forward-only" in payload["warning"]


# -- the stamp, and why a frozen build must not ask git ----------------------------------------


def _stamped(monkeypatch: pytest.MonkeyPatch, commit: str) -> None:
    import types

    import keel.version as version_mod

    stamp = types.SimpleNamespace(VERSION="0.10.0", COMMIT=commit, DIRTY=False)
    monkeypatch.setattr(version_mod, "_embedded", lambda: stamp)


def test_a_frozen_release_is_not_marked_dirty_by_an_unrelated_git_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_git` inherits the process CWD, so a packaged app launched from inside ANY git repository
    reads that repository's HEAD, finds it disagrees with the stamp, and marks a legitimate
    signed release DIRTY. The user then reads "this build is NOT reproducible -- do not run it
    against live funds" about a build that is both.

    A warning that fires on correct builds is one people learn to ignore, and this is the warning
    that must never be ignored."""
    import keel.version as version_mod

    _stamped(monkeypatch, "aaaaaaaaaaaa")
    monkeypatch.setattr(version_mod, "_git", lambda *args: "bbbbbbbbbbbb")
    monkeypatch.setattr(version_mod, "is_packaged", lambda: True)

    info = version_mod.build_info()
    assert info.source == "release"
    assert info.dirty is False
    assert info.is_reproducible


def test_a_VENV_release_is_still_marked_dirty_when_git_disagrees(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fix must not weaken the case it was written for. A stale stamp in a working checkout
    would otherwise claim `[release]` and hide a dirty tree -- the exact misreport
    `keel/version.py` exists to prevent -- and that hazard is real for a venv install and
    impossible for a bundle, which has no working tree to have edited."""
    import keel.version as version_mod

    _stamped(monkeypatch, "aaaaaaaaaaaa")
    monkeypatch.setattr(version_mod, "_git", lambda *args: "bbbbbbbbbbbb")
    monkeypatch.setattr(version_mod, "is_packaged", lambda: False)

    info = version_mod.build_info()
    assert info.source == "release"
    assert info.dirty is True
    assert not info.is_reproducible


def test_an_unstamped_frozen_bundle_is_unknown_and_never_checkout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It is not a checkout -- there is no working tree. `unknown` also keeps `plan_update`'s
    `source != "release"` refusal correct for a bundle that was built without a stamp."""
    import keel.version as version_mod

    monkeypatch.setattr(version_mod, "_embedded", lambda: None)
    monkeypatch.setattr(version_mod, "is_packaged", lambda: True)

    info = version_mod.build_info()
    assert info.source == "unknown"
    assert not info.is_reproducible


def test_a_frozen_build_never_shells_out_to_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not merely that the answer is right, but that git is not consulted at all: a subprocess
    per `--version` on a desktop app is also a visible pause, and on a machine with no git it is
    an exception handler doing nothing useful."""
    import keel.version as version_mod

    _stamped(monkeypatch, "aaaaaaaaaaaa")
    monkeypatch.setattr(version_mod, "is_packaged", lambda: True)

    calls: list[tuple[str, ...]] = []

    def _record(*args: str) -> str | None:
        calls.append(args)
        return None

    monkeypatch.setattr(version_mod, "_git", _record)
    version_mod.build_info()
    assert calls == []


def test_there_is_one_packaged_detector_not_two() -> None:
    """`keel.install.is_packaged` is `keel.version`'s, re-exported. Two detectors would
    eventually disagree about the same process."""
    from keel import install, version

    assert install.is_packaged is version.is_packaged
