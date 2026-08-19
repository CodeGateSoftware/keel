"""`keel update` -- the self-update service (issue #415): pull the latest release and
deploy it into the launch folder, fail-safe by construction.

ONE service, two front-ends (the C1 rule): the `keel update` CLI command (defined at
the bottom of this module, registered in `keel.cli`) and the console's Account-menu
update view (`keel.commands.account_console`). Everything that DECIDES or DOES lives
here; the front-ends render and ask.

The service is pure ORCHESTRATION over subprocesses and HTTP -- no trading logic, no
sizing, no rails. It automates the release procedure `docs/RELEASING.md` describes and
`docs/operator-runbook.md`'s "Deploying a new version" spells out, in the same order a
human runs it, and it refuses to run anywhere that procedure would be wrong:

* **The four production wheels only** -- `keel_core`, `keel_broker_api`,
  `keel_broker_coinbase`, `keel_trader` (`PRODUCTION_WHEEL_PREFIXES`). RELEASING.md's
  rule: the release also ships `keel_broker_fake` (a dev-only fake venue that
  registers a `fake` entry point) and `keel_broker_robinhood` (an optional venue that
  drags in an Ed25519 stack); a deployment must have neither, so they are never
  selected, by prefix, never by `*.whl`.
* **Backups FIRST, as consistent SQLite snapshots** -- every `keel*.db` in the launch
  folder is copied to `<db>.bak-before-<version>-<ts>` (the runbook convention) through
  SQLite's own online-backup API (a plain file copy of a database with a live rollback
  journal -- launchd writers -- can be torn and restore as corrupt), before anything
  mutates the deployment, and the updater NEVER deletes a backup.
* **Verify before cleanup** -- after install and migrate, the NEW venv's
  `keel versions` must report every keel distribution at the target version (the same
  check a deploy script ends with, the one that can actually fail). Only a verified
  success removes the superseded wheels from `Release/`.
* **A failed verify is loud, never pretended away** -- pip replaces the packages at
  install time, so there is no cheap rollback; the honest contract is: say exactly
  what state the venv is in (phase-true: an install that never finished is NOT claimed
  installed), re-install the PREVIOUS wheels best-effort when the install had
  succeeded and they are still in `Release/` (they are -- cleanup only happens on
  success), and name the manual recovery (the runbook).
* **Gated at the shipped front-ends** -- `run_update` takes a `confirm_gate` and calls
  it before ANY mutation; both front-ends hand it `typed_update_gate` (the CLI's own
  `_require_interactive_confirmation` over this module's shared wording), which fails
  closed off a TTY. The service function underneath is a Python API an operator's own
  code could call with its own gate -- the guarantee is about what keel ships, not
  about what is physically expressible.
* **Refuses everything that is not the deployment layout** -- `plan_update` refuses
  when the build is not a release install, when nothing is installed, and when the
  running `keel` package does not resolve from the launch folder's OWN `.venv`
  site-packages (`<launch>/.venv/lib/python3.x/site-packages/keel/`): a source `keel/`
  directory under the launch folder would be shadowed by the wheels, a package
  resolving from outside the launch folder belongs to some other venv (a repo run),
  and an install whose origin is not one of the wheels is not a deployment to update.

No secrets, no auth: the GitHub releases API is read unauthenticated (60 requests/hour
per IP is plenty for a human-gated check), and the wheels' download URLs are public.
The subprocess/HTTP seams (`download`/`install`/`migrate`/`verify`, `fetch`) are
injectable so the whole procedure is testable with no network, no uv and no venv.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

import click

from keel.version import BuildInfo, build_info, installed_distributions

#: The public, unauthenticated latest-release endpoint. NO auth, NO tokens: this
#: feature must never grow a credential, because a deployment's env must not become a
#: thing a leak can use against the repo.
GITHUB_LATEST_URL = "https://api.github.com/repos/CodeGateSoftware/keel/releases/latest"

#: The four PRODUCTION wheels (RELEASING.md, "Release assets"): `keel_trader` is the
#: CLI and the other three are what it depends on, pinned `==` to the same version.
#: The release also ships `keel_broker_fake` (a dev-only fake venue that registers a
#: `fake` entry point under `keel.brokers`) and `keel_broker_robinhood` (an optional
#: venue dragging in an Ed25519 stack) -- a deployment must have NEITHER, so this set
#: is hard-coded and the selection is by exact name, never `Release/*.whl`.
PRODUCTION_WHEEL_PREFIXES: tuple[str, ...] = (
    "keel_core",
    "keel_broker_api",
    "keel_broker_coinbase",
    "keel_trader",
)

_HTTP_TIMEOUT_SEC = 15
_SUBPROCESS_TIMEOUT_SEC = 600


class UpdateError(Exception):
    """An honest, operator-readable failure of the update procedure (never a guess)."""


# -- the latest release: the public API read -------------------------------------------------------


@dataclass(frozen=True)
class ReleaseAsset:
    """One downloadable asset of a release, by its exact name and public URL."""

    name: str
    url: str


@dataclass(frozen=True)
class ReleaseInfo:
    """The latest release: its tag (`v0.6.0`) and every asset it ships."""

    tag: str
    assets: tuple[ReleaseAsset, ...]

    @property
    def version(self) -> str:
        """The tag minus its leading `v` -- the version the wheels are named with."""
        return self.tag[1:] if self.tag.startswith("v") else self.tag


def parse_release(payload: bytes | str) -> ReleaseInfo:
    """Parse the releases-API payload into a `ReleaseInfo`. PURE. Refuses an
    unexpected shape (`no tag_name`, an asset without a name/URL, non-JSON) with an
    `UpdateError` naming what was wrong -- never a guessed-at release."""
    try:
        doc = json.loads(payload)
    except (TypeError, ValueError) as exc:
        raise UpdateError(f"the releases API returned a non-JSON payload: {exc}") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("tag_name"), str) or not doc.get(
        "tag_name"
    ):
        raise UpdateError(
            "unexpected release payload: no tag_name -- the API answered something "
            f"other than a release ({str(doc)[:120]!r})"
        )
    raw_assets = doc.get("assets")
    if not isinstance(raw_assets, list):
        raise UpdateError("unexpected release payload: assets is not a list")
    assets: list[ReleaseAsset] = []
    for item in raw_assets:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not isinstance(
            item.get("browser_download_url"), str
        ):
            raise UpdateError(
                "unexpected release payload: an asset without a name or a download url"
            )
        assets.append(ReleaseAsset(name=item["name"], url=item["browser_download_url"]))
    return ReleaseInfo(tag=doc["tag_name"], assets=tuple(assets))


def _http_get(url: str) -> bytes:
    """GET `url` with keel's own user agent, honestly. A rate-limit (HTTP 403/429 on
    this unauthenticated endpoint) is named as what it is, with the workaround."""
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "keel-self-update"},
    )
    try:
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_SEC) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 429):
            raise UpdateError(
                f"rate-limited by the GitHub API (HTTP {exc.code}): the unauthenticated "
                "releases endpoint allows 60 requests/hour per IP. Wait and re-check, "
                "or follow the manual procedure (docs/operator-runbook.md, 'Deploying "
                "a new version') -- it needs no API call at all."
            ) from exc
        raise UpdateError(f"the releases API answered HTTP {exc.code}: {exc.reason}") from exc
    except OSError as exc:
        raise UpdateError(f"could not reach the GitHub releases API: {exc}") from exc


def latest_release(fetch: Callable[[str], bytes] | None = None) -> ReleaseInfo:
    """The latest release from the public GitHub API (no auth). `fetch` is the
    injectable HTTP seam -- every test drives a fake; production uses `_http_get`."""
    getter = fetch if fetch is not None else _http_get
    try:
        payload = getter(GITHUB_LATEST_URL)
    except UpdateError:
        raise
    except OSError as exc:
        raise UpdateError(f"could not reach the GitHub releases API: {exc}") from exc
    return parse_release(payload)


# -- version comparison: semver, honest on pre-releases and junk -----------------------------------


_SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")


def version_key(version: str) -> tuple[int, int, int, int] | None:
    """A total-order key for a semver string, or `None` when it is not one.

    A pre-release sorts BELOW its own release (`0.7.0-rc1` < `0.7.0`), per semver --
    so offering `0.7.0` to a machine on `0.7.0-rc1` is an upgrade, never the reverse.
    Anything unparseable is `None`: the caller refuses rather than guessing."""
    match = _SEMVER_RE.match(version.strip())
    if match is None:
        return None
    major, minor, patch, pre = match.groups()
    return (int(major), int(minor), int(patch), 0 if pre else 1)


def is_newer_version(latest: str, current: str) -> bool | None:
    """Whether `latest` is strictly newer than `current`. `None` when either side is
    not semver -- an honest "cannot compare", not a guess in either direction."""
    latest_key = version_key(latest)
    current_key = version_key(current)
    if latest_key is None or current_key is None:
        return None
    return latest_key > current_key


# -- the plan: what an update would do, and whether it may -----------------------------------------


@dataclass(frozen=True)
class UpdatePlan:
    """Everything an update WILL do, computed BEFORE anything runs: what is running,
    what is latest, the four wheel asset names/URLs, the `Release/` dir under the
    launch folder, every `keel*.db` that will be backed up first, the RUNNING venv's
    python -- and whether an update is offered at all, with a refusal reason for
    every reason it is not. A pure value: `plan_update` builds it, both front-ends
    render it, `run_update` executes it."""

    current_version: str
    latest_version: str
    latest_tag: str
    wheel_names: tuple[str, ...]
    wheel_urls: tuple[str, ...]
    release_dir: Path
    db_paths: tuple[Path, ...]
    venv_python: Path
    offered: bool
    refusal_reasons: tuple[str, ...]

    @property
    def launch_dir(self) -> Path:
        return self.release_dir.parent

    @property
    def target_version(self) -> str:
        return self.latest_version


def _launch_dir() -> Path:
    """The launch folder: the working directory the deployment runs from -- the same
    place the CLI resolves its relative `--config`/`--db` paths and the runbook's
    four commands run from. A seam for tests."""
    return Path.cwd()


def _running_python() -> Path:
    """The RUNNING venv's python (`sys.executable`) -- the venv the four wheels are
    installed INTO. A seam for tests."""
    import sys

    return Path(sys.executable)


def _running_package_file() -> Path | None:
    """The running `keel` package's own file (`keel/__init__.py`), resolved -- the
    concrete answer to "where is this code running FROM", which the deployment-layout
    detection below classifies. `None` when it cannot be located. A seam for tests."""
    try:
        import keel as _keel

        file = _keel.__file__
        if not file:
            return None
        return Path(file).resolve()
    except (ImportError, OSError):
        return None


def deployment_layout_refusal(launch_dir: Path, package_file: Path | None) -> str | None:
    """Classify where the running `keel` package resolves from, relative to the launch
    folder. PURE. `None` -- an update may be offered -- ONLY for the deployment
    layout: the package resolving from the launch folder's OWN `.venv` site-packages,
    i.e. a `.venv` path component somewhere between the launch folder and a
    `site-packages` component (`<launch>/.venv/lib/python3.x/site-packages/keel/...`,
    the runbook layout -- those wheels live in exactly the venv the updater installs
    into, so updating that venv IS updating the deployment). Everything else refuses,
    naming the layout it saw:

    * a NON-venv path under the launch folder -- the source checkout itself (the
      `uv run keel` case, `<launch>/keel/__init__.py`): installing wheels would shadow
      the working tree, not update it;
    * a path OUTSIDE the launch folder -- a repo run from another directory or a
      system python: the venv the wheels would land in is not this launch folder's
      own, so there is no deployment here to update;
    * no package file at all -- refusing rather than guessing.
    """
    if package_file is None:
        return (
            "cannot locate the running keel package -- refusing rather than guessing "
            "where the wheels would land"
        )
    try:
        rel = package_file.resolve().relative_to(Path(launch_dir).resolve())
    except ValueError:
        return (
            f"the running keel package resolves from OUTSIDE the launch folder "
            f"({package_file}) -- a repo run or a system python, not this launch "
            "folder's deployment: the wheels would install into a venv that is not "
            "the one this launch folder runs on."
        )
    parts = rel.parts
    if ".venv" in parts and "site-packages" in parts[parts.index(".venv") + 1 :]:
        return None  # the deployment layout: the launch folder's own venv
    return (
        f"the running keel package resolves from a non-venv path inside the launch "
        f"folder ({package_file}) -- this IS the source checkout, not a deployment: "
        "installing wheels into the venv would shadow the working tree, not update it."
    )


def _wheel_origin_refusal(package_file: Path) -> str | None:
    """`None` when the `keel-trader` distribution beside the running package (the
    site-packages dir two levels up from `keel/__init__.py`) was installed as a wheel;
    a refusal reason otherwise. A deployment is the four release WHEELS: an editable
    install or a `pip install <source-dir>` has a `direct_url.json` whose `url` names a
    directory rather than a `.whl` -- updating that in place is not the runbook's
    procedure, so it is refused. No `direct_url.json` at all is an index install
    (shipped as a wheel) and passes. Reads only the dist-info's own metadata."""
    site_packages = package_file.parent.parent
    dist_infos = sorted(site_packages.glob("keel_trader-*.dist-info"))
    if not dist_infos:
        return (
            f"no keel_trader-*.dist-info beside the running package ({site_packages}) "
            "-- cannot confirm the distribution was installed as a wheel; refusing "
            "rather than guessing"
        )
    direct = dist_infos[0] / "direct_url.json"
    if not direct.is_file():
        return None  # an index install: no direct URL, and indexes ship wheels
    try:
        doc = json.loads(direct.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return f"cannot read {direct} -- refusing rather than guessing the install origin"
    url = doc.get("url") if isinstance(doc, dict) else None
    if isinstance(url, str) and url.endswith(".whl"):
        return None
    return (
        f"the running keel-trader distribution was not installed from a wheel "
        f"(its direct_url origin is {url!r}, a source directory or an editable "
        "install) -- a deployment is the four release wheels; re-deploy from the "
        "release (docs/operator-runbook.md, 'Deploying a new version')."
    )


def select_production_wheels(
    release: ReleaseInfo, version: str
) -> tuple[ReleaseAsset, ...]:
    """The FOUR production wheel assets for `version`, in `PRODUCTION_WHEEL_PREFIXES`
    order, matched by exact `<prefix>-<version>-` name -- never `*.whl`, so the
    fake/robinhood wheels can never ride along. PURE; raises `UpdateError` naming any
    of the four the release does not carry."""
    selected: list[ReleaseAsset] = []
    missing: list[str] = []
    for prefix in PRODUCTION_WHEEL_PREFIXES:
        matches = [
            asset
            for asset in release.assets
            if asset.name.startswith(f"{prefix}-{version}-") and asset.name.endswith(".whl")
        ]
        if not matches:
            missing.append(prefix)
        elif len(matches) > 1:
            # A prefix that matches twice (a stray re-upload with an extra platform tag)
            # is a refusal naming BOTH -- a silent first-match could install the wrong
            # artifact into a deployment.
            both = " and ".join(asset.name for asset in matches)
            raise UpdateError(
                f"release {release.tag} carries MORE THAN ONE asset matching "
                f"{prefix}-{version}-: {both}. Which one is the wheel is not the "
                "updater's to guess -- see docs/RELEASING.md, 'Release assets'."
            )
        else:
            selected.append(matches[0])
    if missing:
        names = ", ".join(asset.name for asset in release.assets) or "no assets at all"
        raise UpdateError(
            f"release {release.tag} does not carry the four production wheels "
            f"(missing: {', '.join(missing)}). Its assets: {names}. A release without "
            "all four cannot be deployed -- see docs/RELEASING.md, 'Release assets'."
        )
    return tuple(selected)


def plan_update(
    release: ReleaseInfo,
    *,
    build: BuildInfo | None = None,
    installed: dict[str, str] | None = None,
    launch_dir: Path | None = None,
    venv_python: Path | None = None,
    package_file: Path | None = None,
) -> UpdatePlan:
    """Build the plan for updating to `release`. PURE aside from the glob/stat reads
    of the launch folder; every input is injectable so the offer/refusal semantics
    are testable with no network and no install.

    An update is offered only when EVERY refusal check passes AND the latest version
    is strictly newer than the running one. "Not newer" with no refusals is the calm
    up-to-date state, not a refusal; a version that cannot be compared IS a refusal
    (never a guess)."""
    info = build if build is not None else build_info()
    dists = installed if installed is not None else installed_distributions()
    launch = launch_dir if launch_dir is not None else _launch_dir()
    venv = venv_python if venv_python is not None else _running_python()
    pkg_file = package_file if package_file is not None else _running_package_file()

    reasons: list[str] = []
    wheels: tuple[ReleaseAsset, ...] = ()
    try:
        wheels = select_production_wheels(release, release.version)
    except UpdateError as exc:
        reasons.append(str(exc))

    if info.source != "release":
        reasons.append(
            f"this is a [{info.source}] build, not a release install -- the updater "
            "deploys release wheels into a venv and refuses to run anywhere else (a "
            "checkout would be shadowed, not updated). Install from a release first "
            "(docs/operator-runbook.md, 'Deploying a new version')."
        )
    if not dists:
        reasons.append(
            "no keel distributions are installed in this environment -- a source "
            "checkout run from the repo (e.g. `uv run keel`). There is no install "
            "here to update; a deployment is a venv installed from the four wheels."
        )
    layout_refusal = deployment_layout_refusal(launch, pkg_file)
    if layout_refusal is not None:
        reasons.append(layout_refusal)
    elif pkg_file is not None:
        # only meaningful for the deployment layout (the venv the package resolves
        # from): is the distribution in it one of the wheels?
        origin_refusal = _wheel_origin_refusal(pkg_file)
        if origin_refusal is not None:
            reasons.append(origin_refusal)

    newer = is_newer_version(release.version, info.version)
    if newer is None:
        reasons.append(
            f"cannot compare the running version {info.version!r} with the release tag "
            f"{release.tag!r} -- not semver. Refusing rather than guessing."
        )
    return UpdatePlan(
        current_version=info.version,
        latest_version=release.version,
        latest_tag=release.tag,
        wheel_names=tuple(asset.name for asset in wheels),
        wheel_urls=tuple(asset.url for asset in wheels),
        release_dir=launch / "Release",
        db_paths=tuple(sorted(launch.glob("keel*.db"))),
        venv_python=venv,
        offered=bool(newer) and not reasons,
        refusal_reasons=tuple(reasons),
    )


# -- the run: gate, backups, download, install, migrate, verify, cleanup ---------------------------


@dataclass(frozen=True)
class UpdateResult:
    """What `run_update` did: whether it completed, every step line it streamed (the
    same lines the CLI echoed live), the honest failure text when it did not
    (including the recovery), whether a best-effort reinstall of the previous wheels
    ran, and the backups it wrote (never deleted by the updater)."""

    ok: bool
    steps: tuple[str, ...]
    error: str | None
    rolled_back: bool
    backups: tuple[Path, ...]


def console_entry(venv_python: Path) -> Path:
    """The `keel` console entry beside the venv's python -- what the migrate/verify
    subprocesses run and what a relaunch execv's."""
    return venv_python.parent / ("keel.exe" if os.name == "nt" else "keel")


def backup_path(
    db_path: Path, target_version: str, now_ts: float, occupied: Collection[str] = ()
) -> Path:
    """The runbook's backup name: `<db>.bak-before-<version>-<timestamp>` -- what the
    manual procedure would write, so a self-updated deployment's backups are
    indistinguishable from a hand-upgraded one's. The timestamp has second granularity,
    so `occupied` (the backup names that already exist on disk or were written this
    run) guards the collision: a same-second name gets a `-2`, `-3`, ... counter
    suffix -- a backup is NEVER overwritten. PURE."""
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now_ts))
    base = f"{db_path.name}.bak-before-{target_version}-{stamp}"
    if base not in occupied:
        return db_path.with_name(base)
    counter = 2
    while f"{base}-{counter}" in occupied:
        counter += 1
    return db_path.with_name(f"{base}-{counter}")


#: The download guard: 200 MiB, three orders of magnitude above the ~1 MiB wheels -- a
#: mis-pointed URL (a release PAGE, a video) is refused instead of streamed to disk.
_MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024


def _download_file(url: str, dest: Path) -> None:
    """Download a public asset URL to `dest`, with the read BOUNDED at
    `_MAX_DOWNLOAD_BYTES`. The production seam; tests inject."""
    try:
        with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT_SEC) as response:
            payload = response.read(_MAX_DOWNLOAD_BYTES + 1)
    except OSError as exc:
        raise UpdateError(f"could not download {url}: {exc}") from exc
    if len(payload) > _MAX_DOWNLOAD_BYTES:
        raise UpdateError(
            f"refused to download {url}: larger than the 200 MiB guard (the wheels are "
            "~1 MiB) -- the URL does not point at a wheel. Nothing was written."
        )
    dest.write_bytes(payload)


def _backup_file(src: Path, dest: Path) -> None:
    """Back up `src` to `dest`. The GUARANTEE for a database: a CONSISTENT snapshot via
    SQLite's own online-backup API (`src_conn.backup(dest_conn)`), correct even while
    another process is mid-transaction under a rollback journal (launchd writers) --
    where a plain file copy can be torn (half old pages, half new) and restore as
    corrupt. Anything that is not a `.db` (there is none in the plan's `keel*.db` glob,
    but the guard is cheap) falls back to `shutil.copy2`. The production seam."""
    if src.suffix == ".db":
        src_conn = sqlite3.connect(src)
        try:
            dest_conn = sqlite3.connect(dest)
            try:
                src_conn.backup(dest_conn)
            finally:
                dest_conn.close()
        finally:
            src_conn.close()
    else:
        shutil.copy2(src, dest)


def _uv_install(venv_python: Path, wheels: Sequence[Path]) -> None:
    """Install the wheels BY PATH into the RUNNING venv with uv -- exactly the
    runbook's own command, `uv pip install --python <venv> --find-links <Release> <the
    four paths>`. uv is a deployment dependency (the runbook says so); an absent uv is
    an honest error naming the manual procedure, never a fallback to some other
    installer."""
    # `--find-links` points at the wheels' own directory, exactly as the runbook's
    # command does, so the wheels' ==-pinned siblings resolve from Release/ rather
    # than from PyPI -- where they do not exist.
    argv = [
        "uv",
        "pip",
        "install",
        "--python",
        str(venv_python),
        "--find-links",
        str(wheels[0].parent),
        *(str(wheel) for wheel in wheels),
    ]
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT_SEC, check=False
        )
    except FileNotFoundError as exc:
        raise UpdateError(
            "uv is not on PATH -- the updater installs with `uv pip install` (the "
            "runbook's own tool, a deployment dependency). Install uv, or run the "
            "manual procedure in docs/operator-runbook.md ('Deploying a new version')."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise UpdateError(
            f"uv pip install timed out after {_SUBPROCESS_TIMEOUT_SEC}s -- the venv may "
            "be half-updated; verify with `keel versions` and recover per the runbook."
        ) from exc
    if proc.returncode != 0:
        raise UpdateError(
            f"uv pip install failed (exit {proc.returncode}): {proc.stderr.strip()[:400]}"
        )


def _migrate_db(new_keel: Path, db_path: Path) -> None:
    """`keel migrate --db <path>` via the NEW venv's keel entry -- the new build's own
    migrations, exactly the runbook's step, idempotent and schema-only."""
    argv = [str(new_keel), "migrate", "--db", str(db_path)]
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT_SEC, check=False
        )
    except FileNotFoundError as exc:
        raise UpdateError(
            f"the new build's keel entry is missing ({new_keel}) -- the install did not "
            "produce a console entry; recover per the runbook."
        ) from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()[:400]
        raise UpdateError(
            f"`keel migrate --db {db_path}` failed (exit {proc.returncode}): {detail}"
        )


_DISTRIBUTION_ROW = re.compile(r"^(keel-[A-Za-z0-9._-]+)\s+(\S+)$", re.MULTILINE)


def parse_versions_output(text: str) -> dict[str, str]:
    """Every `keel-<name>  <version>` row of `keel versions`' output. PURE -- the
    verify step's read half, testable against the command's exact format."""
    return dict(_DISTRIBUTION_ROW.findall(text))


def _verify_versions(new_keel: Path, target_version: str) -> None:
    """The deploy check that can fail, run against the NEW build: the new venv's own
    `keel versions`, parsed -- every keel distribution must be present-and-equal to
    the target. NOT `keel --version`, which could not fail (see `keel/commands/
    versions.py` for the bug that made this the check)."""
    argv = [str(new_keel), "versions"]
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT_SEC, check=False
        )
    except FileNotFoundError as exc:
        raise UpdateError(
            f"cannot run the new build's keel entry ({new_keel}) to verify the install"
        ) from exc
    output = (proc.stdout or "") + (proc.stderr or "")
    distributions = parse_versions_output(output)
    problems: list[str] = []
    for name, version in sorted(distributions.items()):
        if version != target_version:
            problems.append(f"{name} is at {version}, not {target_version}")
    for prefix in PRODUCTION_WHEEL_PREFIXES:
        distribution = prefix.replace("_", "-")
        if distribution not in distributions:
            problems.append(f"{distribution} is not installed at all")
    if proc.returncode != 0:
        problems.append(f"`keel versions` exited {proc.returncode}: {output.strip()[:400]}")
    if problems:
        raise UpdateError("verify failed: " + "; ".join(problems))


_MANUAL_RECOVERY = (
    "MANUAL RECOVERY: docs/operator-runbook.md, 'Deploying a new version' -- download "
    "the four wheels, `uv pip install --python <venv>` them by path, then "
    "`keel versions` and `keel status`. The .bak-before-* backups are untouched."
)


def run_update(
    plan: UpdatePlan,
    *,
    echo: Callable[[str], None],
    confirm_gate: Callable[[], bool],
    download: Callable[[str, Path], None] | None = None,
    install: Callable[[Path, Sequence[Path]], None] | None = None,
    migrate: Callable[[Path, Path], None] | None = None,
    verify: Callable[[Path, str], None] | None = None,
    backup: Callable[[Path, Path], None] | None = None,
    now_ts: float | None = None,
) -> UpdateResult:
    """Run the update, in the runbook's order, streaming every step through `echo`.

    **Fail-safe by construction:** the `confirm_gate` runs INSIDE this function, before
    any mutation -- both shipped front-ends gate the run with the same typed gate (the
    service function itself could be called by an operator's own code with their own
    gate; nothing keel ships calls it ungated). The order is: gate -> consistent
    SQLite-snapshot backups of every `keel*.db` (never overwriting an existing backup)
    -> download the four wheels (verified non-empty, read bounded at 200 MiB) ->
    install into the RUNNING venv -> migrate each database with the new build -> verify
    with the new build's `keel versions` -> only then delete the superseded wheels.
    Backups are never deleted. A failure is PHASE-TRUE about the venv's state: a
    failure OF the install says the venv was NOT updated (or is half-updated) and
    removes the downloaded wheel files from `Release/` (a torn file must not poison a
    later rollback's superseded set); a failure AFTER a finished install says the new
    wheels ARE installed (pip replaces the packages at install time, so the old build
    does not come back on its own), re-installs the previous wheels best-effort when
    they are still in `Release/` (they are, until a verified success cleans them), and
    points at the `.bak-before-*` backups as the data recovery.

    Every subprocess/HTTP seam is injectable; the defaults are the real thing. Never
    raises `UpdateError` -- a failure is the returned result, with the steps that
    already streamed."""
    steps: list[str] = []

    def say(line: str) -> None:
        steps.append(line)
        echo(line)

    if not plan.offered:
        return UpdateResult(
            ok=False,
            steps=tuple(steps),
            error="no update is offered -- nothing to do",
            rolled_back=False,
            backups=(),
        )
    if not confirm_gate():
        return UpdateResult(
            ok=False,
            steps=tuple(steps),
            error="confirmation not given -- nothing was changed",
            rolled_back=False,
            backups=(),
        )

    fetch_file = download if download is not None else _download_file
    install_wheels = install if install is not None else _uv_install
    migrate_db = migrate if migrate is not None else _migrate_db
    verify_install = verify if verify is not None else _verify_versions
    backup_file = backup if backup is not None else _backup_file
    ts = time.time() if now_ts is None else now_ts

    say(f"updating {plan.launch_dir}: {plan.current_version} -> {plan.target_version}")

    # The superseded set, read BEFORE anything lands: the keel wheels already in
    # Release/ that are not this release's four (the previous version's). They are
    # the verify-failure recovery path, so they are deleted only on verified success.
    target_names = set(plan.wheel_names)
    superseded: list[Path] = (
        sorted(p for p in plan.release_dir.glob("*.whl") if p.name not in target_names)
        if plan.release_dir.is_dir()
        else []
    )
    if superseded:
        say(
            "superseded wheels kept until verify succeeds: "
            + ", ".join(path.name for path in superseded)
        )

    # BACKUPS FIRST -- before any download, before any install: if anything after
    # this point half-happens, the databases' pre-update state exists on disk. Each
    # is a consistent SQLite snapshot, and a same-second name never overwrites one.
    occupied = {p.name for p in plan.launch_dir.glob("*.bak-before-*")}
    backups: list[Path] = []
    for db_path in plan.db_paths:
        dest = backup_path(db_path, plan.target_version, ts, occupied=occupied)
        backup_file(db_path, dest)
        occupied.add(dest.name)
        backups.append(dest)
        say(f"backed up {db_path.name} -> {dest.name}")
    if not plan.db_paths:
        say("no keel*.db databases in the launch folder -- nothing to back up")

    plan.release_dir.mkdir(parents=True, exist_ok=True)
    wheel_paths: list[Path] = []
    try:
        for name, url in zip(plan.wheel_names, plan.wheel_urls):
            dest = plan.release_dir / name
            wheel_paths.append(dest)
            say(f"downloading {name}")
            fetch_file(url, dest)
            if not dest.is_file() or dest.stat().st_size == 0:
                raise UpdateError(
                    f"the downloaded wheel {name} is missing or empty -- nothing was "
                    "installed; the running binary is unchanged. The partial file was "
                    "removed from Release/ (a torn file must not stay behind to "
                    "poison a later rollback); retry, or follow the manual procedure "
                    "(the runbook, 'Deploying a new version')."
                )
    except UpdateError as exc:
        for partial in wheel_paths:
            partial.unlink(missing_ok=True)
        say(
            "removed the partial wheel file(s) from Release/ -- a torn download must "
            "not poison a later rollback"
        )
        return UpdateResult(
            ok=False, steps=tuple(steps), error=str(exc), rolled_back=False,
            backups=tuple(backups),
        )

    new_keel = console_entry(plan.venv_python)
    installed = False  # whether the four wheels FINISHED installing (uv returned success)
    try:
        say(f"installing the four wheels into the RUNNING venv ({plan.venv_python})")
        install_wheels(plan.venv_python, wheel_paths)
        installed = True
        for db_path in plan.db_paths:
            say(f"migrating {db_path.name} with the new build")
            migrate_db(new_keel, db_path)
        say(
            f"verifying with the new build's `keel versions` (every distribution at "
            f"{plan.target_version})"
        )
        verify_install(new_keel, plan.target_version)
    except UpdateError as exc:
        say(f"FAILED: {exc}")
        if not installed:
            # The failure IS the install (uv absent, a timeout, a corrupt wheel that
            # passed the non-empty check): the venv was NOT updated, or is
            # half-updated -- and a reinstall of the previous wheels is NOT attempted
            # (uv just refused this venv; the previous wheels are untouched in
            # Release/ for the manual recovery). The downloaded files are removed so
            # a torn one cannot ride into a later rollback's superseded set.
            for path in wheel_paths:
                path.unlink(missing_ok=True)
            say(
                "removed the downloaded wheel files from Release/ -- a torn file must "
                "not poison a later rollback's superseded set"
            )
            error = (
                f"update FAILED while installing the wheels: {exc}\n"
                f"STATE: the venv was NOT updated (or is half-updated -- run `keel "
                f"versions` to see exactly what is installed); the previous wheels "
                f"are still in {plan.release_dir}, and the downloaded files were "
                f"removed from it.\n"
                f"{_MANUAL_RECOVERY}"
            )
            return UpdateResult(
                ok=False, steps=tuple(steps), error=error, rolled_back=False,
                backups=tuple(backups),
            )
        rolled_back = False
        if superseded:
            say("best-effort recovery: re-installing the PREVIOUS wheels from Release/")
            try:
                install_wheels(plan.venv_python, superseded)
                rolled_back = True
                say("re-installed the previous wheels -- the venv is back on the old build.")
                say(
                    "The databases were already migrated; the old build is ASSUMED to "
                    "still open them (this release's migrations are written to be "
                    "additive -- an assumption, not a guarantee). The real data "
                    "recovery is the backups: <db>.bak-before-<version>-<ts> beside "
                    "each database, never deleted by the updater."
                )
            except UpdateError as reinstall_exc:
                say(f"best-effort re-install FAILED: {reinstall_exc}")
        recovery = (
            "rolled back: the previous wheels were re-installed from Release/ "
            "(best-effort); confirm with `keel versions` -- and if the old build "
            "cannot open the migrated databases, restore from the .bak-before-* "
            "backups beside them."
            if rolled_back
            else "no previous wheels remained in Release/ to re-install -- the "
            ".bak-before-* backups beside the databases are the data recovery."
        )
        error = (
            f"update FAILED after the wheels were installed: {exc}\n"
            f"STATE: the {plan.target_version} wheels ARE installed into the running "
            f"venv ({plan.venv_python}) -- pip replaces the packages at install time, "
            f"so the old build does not come back on its own. {recovery}\n"
            f"{_MANUAL_RECOVERY}"
        )
        return UpdateResult(
            ok=False, steps=tuple(steps), error=error, rolled_back=rolled_back,
            backups=tuple(backups),
        )

    # Verified success -- only now is the superseded set removed. The backups stay.
    for path in superseded:
        path.unlink()
        say(f"removed superseded wheel {path.name}")
    say(f"update complete: every keel distribution verified at {plan.target_version}")
    return UpdateResult(
        ok=True, steps=tuple(steps), error=None, rolled_back=False, backups=tuple(backups)
    )


# -- the relaunch: execv the new build's console ---------------------------------------------------


def build_relaunch_argv(venv_python: Path, original_argv: Sequence[str]) -> list[str]:
    """The argv a relaunch execv's: the NEW venv's `keel` console entry carrying the
    original invocation's arguments. PURE.

    `original_argv` is the running process's `sys.argv`. argv[0] -- the OLD binary's
    path, possibly a wrapper -- is REPLACED by the new entry, and EVERY argument after
    it is kept VERBATIM: no reordering, no prepending, ever. Deployment wrappers exec
    `keel --config X --db Y tui`, so argv[0] is the wrapper and the subcommand sits
    AFTER the flags -- click parses that identically on the relaunched process, and
    any reconstruction of our own (the old fallback prepended `tui`, producing a
    duplicate subcommand and a usage error) breaks exactly those wrappers. Only when
    the original argv is genuinely unavailable -- the bare-wrapper case, an argv with
    NO arguments at all -- is `[keel, 'tui']` constructed, so a relaunch always opens
    the operator console, never a bare interpreter."""
    new_keel = console_entry(venv_python)
    args = [str(arg) for arg in original_argv[1:]]
    if not args:
        return [str(new_keel), "tui"]
    return [str(new_keel), *args]


def relaunch_tui(
    venv_python: Path,
    original_argv: Sequence[str],
    *,
    execv: Callable[[str, list[str]], NoReturn] | None = None,
) -> Callable[[], NoReturn]:
    """Build (do not run) the relaunch closure: execv the new build's `keel` entry
    with the reconstructed TUI argv. PURE -- it returns the closure; the CLI/TUI
    decide to call it (only the TUI does; the CLI prints the command instead).

    The CALLER must have restored the terminal first -- the TUI runs the closure from
    inside its curses suspend dance, after `endwin`. `execv` replaces the process, so
    the closure does not return; an `OSError` from it (permissions, a missing
    interpreter) is wrapped into the same honest `UpdateError` as a returning execv --
    naming the manual `keel tui` start -- so no front-end ever crashes on a bare
    OSError, and a failed relaunch can be rendered instead of losing the run's state."""
    new_keel = console_entry(venv_python)
    argv = build_relaunch_argv(venv_python, original_argv)

    def _relaunch() -> NoReturn:
        try:
            if execv is not None:
                execv(str(new_keel), argv)
            else:
                os.execv(str(new_keel), argv)
        except OSError as exc:
            raise UpdateError(
                f"relaunch failed ({exc}): execv could not start the new build -- "
                f"start the console by hand: `{new_keel} tui`"
            ) from exc
        raise UpdateError(
            f"relaunch failed: execv returned -- start the console by hand: "
            f"`{new_keel} tui`"
        )

    return _relaunch


# -- the typed gate: ONE wording, both front-ends --------------------------------------------------


def gate_action(plan: UpdatePlan) -> str:
    """The gate's action phrase: names both versions. PURE."""
    return f"update this deployment from {plan.current_version} to {plan.latest_version}"


def gate_detail(plan: UpdatePlan) -> str:
    """The gate's detail: names the launch folder, the Release/ dir, the backups, the
    RUNNING venv -- and says plainly that the binary this process is running is
    REPLACED. The same words `keel update` asks and the console's update view asks.
    PURE."""
    return (
        f"launch folder {plan.launch_dir}: download the four production wheels to "
        f"{plan.release_dir}, back up every keel database first "
        f"(.bak-before-{plan.latest_version}-<ts>, never deleted by the updater), "
        f"install them into the RUNNING venv ({plan.venv_python}) -- the binary this "
        "process is running RIGHT NOW is replaced -- then migrate and verify. This "
        "never runs unattended: it happens only behind this typed confirmation."
    )


def typed_update_gate(plan: UpdatePlan) -> bool:
    """The CLI's own `_require_interactive_confirmation` over the shared wording --
    the one gate both front-ends hand to `run_update`'s `confirm_gate`. Demands the
    typed word `yes` at a terminal, fails closed off a TTY (a scheduled job has no
    terminal to type into; the CLI driven with scripted input on a real TTY is an
    operator's own choice, not a hole in the gate), and returns False on any refusal
    instead of raising, so it can be passed straight through the service's confirm
    seam."""
    from keel.commands._common import _require_interactive_confirmation

    try:
        _require_interactive_confirmation(gate_action(plan), gate_detail(plan))
    except click.ClickException:
        return False
    return True


# -- the renderers: one implementation, both front-ends --------------------------------------------


def render_plan_lines(plan: UpdatePlan) -> list[str]:
    """The check report: current vs latest, then the whole plan (or every refusal).
    The exact lines the CLI prints and the console's update view renders -- ONE
    renderer, so the two front-ends cannot drift. PURE."""
    lines = [
        f"current: {plan.current_version}   latest: {plan.latest_version} "
        f"(tag {plan.latest_tag})"
    ]
    if not plan.offered:
        if plan.refusal_reasons:
            lines.append("update NOT offered:")
            lines.extend(f"  - {reason}" for reason in plan.refusal_reasons)
        else:
            lines.append(
                f"already at the latest release ({plan.latest_version}) -- nothing to do."
            )
        return lines
    lines.append(f"update available: {plan.current_version} -> {plan.latest_version}")
    lines.append(f"the plan (launch folder {plan.launch_dir}):")
    for name in plan.wheel_names:
        lines.append(f"  wheel: {name}")
    lines.append(f"  download to: {plan.release_dir}")
    if plan.db_paths:
        names = ", ".join(path.name for path in plan.db_paths)
        lines.append(f"  back up first (never deleted): {names}")
        lines.append(f"  backups named: <db>.bak-before-{plan.latest_version}-<timestamp>")
    else:
        lines.append("  back up first: no keel*.db databases in the launch folder")
    lines.append(f"  install into the RUNNING venv: {plan.venv_python}")
    lines.append("  then: migrate every database with the new build, verify with")
    lines.append(f"  `keel versions` (every distribution at {plan.latest_version}),")
    lines.append("  and only then remove the superseded wheels from Release/.")
    return lines


def render_result_lines(result: UpdateResult) -> list[str]:
    """The run's summary (the steps themselves streamed live through `echo`): the
    completion line, or the honest failure text with its recovery, plus the backups
    written. PURE."""
    if result.ok:
        lines = [
            "update complete -- installed and verified; the backups stay beside the "
            "databases (the updater never deletes them)."
        ]
    elif result.error:
        lines = [f"update did not complete: {result.error}"]
    else:
        lines = ["update did not complete."]
    if result.rolled_back:
        lines.append(
            "rolled back: the previous wheels were re-installed from Release/ "
            "(best-effort recovery)."
        )
    if result.backups:
        lines.append("backups written: " + ", ".join(path.name for path in result.backups))
    return lines


# -- the CLI front-end -----------------------------------------------------------------------------


@click.command("update")
@click.option(
    "--check",
    is_flag=True,
    default=False,
    help="Report the current/latest versions and the plan only -- no downloads, no changes.",
)
@click.pass_context
def update_cmd(ctx: click.Context, check: bool) -> None:
    """Check for a newer release and deploy it into this launch folder.

    The procedure docs/operator-runbook.md spells out by hand, automated and still
    human-gated: download the four production wheels to Release/, back up every
    keel*.db first, install into the RUNNING venv with uv, migrate, verify with
    `keel versions`, and only then clean the superseded wheels. NEVER automatic: the
    full run demands a typed confirmation at a terminal. `--check` mutates nothing.

    On success this command does NOT relaunch anything (a CLI process is not the
    TUI) -- it prints the command to start the new build. Only the TUI relaunches
    itself.
    """
    try:
        plan = plan_update(latest_release())
    except UpdateError as exc:
        raise click.ClickException(str(exc)) from exc

    for line in render_plan_lines(plan):
        click.echo(line)
    if check:
        if plan.offered:
            click.echo(
                "no changes made (--check). Run `keel update` to apply -- a typed "
                "confirmation is required."
            )
        return
    if not plan.offered:
        return

    result = run_update(plan, echo=click.echo, confirm_gate=lambda: typed_update_gate(plan))
    for line in render_result_lines(result):
        click.echo(line)
    if result.ok:
        click.echo(
            "the TUI keeps the build it started with until you relaunch it: run "
            "`keel tui` (or your deployment wrapper) to start the new build -- this "
            "command does not relaunch it for you."
        )
        return
    ctx.exit(1)
