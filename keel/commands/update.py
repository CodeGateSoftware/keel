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

* **The five production wheels only** -- `keel_core`, `keel_broker_api`,
  `keel_broker_coinbase`, `keel_broker_alpaca`, `keel_trader`
  (`PRODUCTION_WHEEL_PREFIXES`). RELEASING.md's rule: the release also ships
  `keel_broker_fake` (a dev-only fake venue that registers a `fake` entry point),
  `keel_broker_robinhood` (an optional venue that drags in an Ed25519 stack) and
  `keel_broker_kraken` (a stub whose every data method raises); a deployment must have
  none of them, so they are never selected, by prefix, never by `*.whl`.
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
* **A packaged install NEVER self-updates (D6, #439)** -- decided in
  `docs/decisions/0001-desktop-update-path.md`: the desktop product's update path is
  the next signed installer, so for a bundled layout this service only ever REPORTS
  (`packaged_check_lines`: the version comparison and the download URL, in desktop
  vocabulary), the plan's one refusal is the packaged one, and an unreachable check is
  a calm "could not check", never an error. The uv-venv path above is unchanged and
  remains the answer for terminal deployments.

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

import click

from keel.install import RELEASES_URL, is_packaged, packaged_update_refusal
from keel.version import BuildInfo, build_info, installed_distributions

#: The public, unauthenticated latest-release endpoint. NO auth, NO tokens: this
#: feature must never grow a credential, because a deployment's env must not become a
#: thing a leak can use against the repo.
GITHUB_LATEST_URL = "https://api.github.com/repos/CodeGateSoftware/keel/releases/latest"

#: The five PRODUCTION wheels (RELEASING.md, "Release assets"): `keel_trader` is the
#: CLI and the rest is what it runs on, pinned `==` to the same version. `keel_broker_alpaca`
#: joined the set in #425: the equities profile (`config.paper-equities.yaml`,
#: `broker: name: alpaca`, #386) deploys it beside the four, and an update that moved the
#: rest while leaving it behind failed the verify step (`keel versions`: PARTIAL INSTALL)
#: and rolled the WHOLE deployment back -- every self-update on such a box failed. The set
#: is still STATED BY NAME, not derived from the config or the installed set (#425's
#: "awkward part"): naming keeps the plan knowable before anything reads a deployment's
#: config, and the cost -- the alpaca adapter present on a Coinbase-only box -- is one
#: unused module whose single dependency, `requests`, already rides every deployment
#: transitively via the Coinbase SDK. The release also ships `keel_broker_fake` (a
#: dev-only fake venue that registers a `fake` entry point under `keel.brokers`),
#: `keel_broker_robinhood` (an optional venue dragging in an Ed25519 stack for an
#: adapter nothing constructs) and `keel_broker_kraken` (a port-complete STUB, #313:
#: every data/market method raises, so a deployment must never ride it) -- a deployment
#: must have NONE of them, so this set stays hard-coded and the selection is by exact
#: name, never `Release/*.whl`.
PRODUCTION_WHEEL_PREFIXES: tuple[str, ...] = (
    "keel_core",
    "keel_broker_api",
    "keel_broker_coinbase",
    "keel_broker_alpaca",
    "keel_trader",
)

#: Per-socket-operation timeout, NOT a deadline for the whole transfer -- `urlopen` applies it
#: to each read. Raised from 15 with #675's retries: a shared CDN that goes quiet briefly is the
#: common case, and 30-with-retries recovers from more of it than 60-without while giving up on
#: a genuinely dead host sooner.
_HTTP_TIMEOUT_SEC = 30
_SUBPROCESS_TIMEOUT_SEC = 600

#: How many times a TRANSPORT failure is retried before the update is abandoned (#675). Three
#: attempts, because the failure this exists for is a momentary stall: one retry is often the
#: same second, and a fourth is waiting on something that is not coming back.
_DOWNLOAD_ATTEMPTS = 3

#: Seconds to wait before each retry. A tuple rather than a formula so the total added latency
#: of a doomed update is legible at a glance: 4 seconds, once, not an unbounded backoff.
_RETRY_BACKOFF_SEC = (1.0, 3.0)

#: HTTP statuses worth retrying: the server said "not now", not "no". Everything else is an
#: ANSWER -- a 404 is the wrong URL and a 403 is the rate limit `_http_get` names with its own
#: workaround, and retrying either burns time or budget to be told the same thing again.
#:
#: 429 is deliberately ABSENT even though it is transient. On the unauthenticated releases
#: endpoint it means the 60-requests/hour budget is spent, and spending it faster is the one
#: response that cannot help; `_http_get` already turns it into an operator-readable message
#: pointing at the manual procedure, which needs no API call at all.
_RETRYABLE_STATUS = frozenset({500, 502, 503, 504})


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
    if (
        not isinstance(doc, dict)
        or not isinstance(doc.get("tag_name"), str)
        or not doc.get("tag_name")
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
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("name"), str)
            or not isinstance(item.get("browser_download_url"), str)
        ):
            raise UpdateError(
                "unexpected release payload: an asset without a name or a download url"
            )
        assets.append(ReleaseAsset(name=item["name"], url=item["browser_download_url"]))
    return ReleaseInfo(tag=doc["tag_name"], assets=tuple(assets))


def _is_retryable(exc: Exception) -> bool:
    """Whether `exc` is a stall worth asking again about, or an answer that will not change.

    **`urllib.error.HTTPError` is a subclass of `OSError`**, so the obvious `except OSError:
    retry` retries a 404 and a 403 as eagerly as a dropped connection. It is the trap this
    function exists to avoid: a 404 means the URL is wrong and three attempts make it wrong
    three times, and a 403 on the unauthenticated releases endpoint is the rate limit, where
    retrying spends the very budget that ran out.

    So an HTTPError is judged by its STATUS and everything else -- a timeout, a reset, a DNS
    failure, anything `urlopen` raises without a response behind it -- is transport, and
    transport is retried.
    """
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in _RETRYABLE_STATUS
    return isinstance(exc, OSError)


def _with_retries[T](attempt: Callable[[], T], *, sleep: Callable[[float], None] = time.sleep) -> T:
    """Run `attempt`, retrying a transport failure up to `_DOWNLOAD_ATTEMPTS` times (#675).

    Re-raises the LAST exception rather than a wrapper, so every caller's own error message --
    `_http_get`'s rate-limit text, `_download_file`'s URL -- survives unchanged. This function
    decides only WHETHER to ask again, never what to say when the answer is final.

    `sleep` is a parameter because the alternative is a test suite that really waits four
    seconds per retry pin.
    """
    last: Exception | None = None
    for index in range(_DOWNLOAD_ATTEMPTS):
        try:
            return attempt()
        except Exception as exc:  # noqa: BLE001 -- re-raised below; the filter is `_is_retryable`
            if not _is_retryable(exc) or index == _DOWNLOAD_ATTEMPTS - 1:
                raise
            last = exc
            sleep(_RETRY_BACKOFF_SEC[min(index, len(_RETRY_BACKOFF_SEC) - 1)])
    raise last if last is not None else AssertionError("unreachable")


def _http_get(url: str) -> bytes:
    """GET `url` with keel's own user agent, honestly. A rate-limit (HTTP 403/429 on
    this unauthenticated endpoint) is named as what it is, with the workaround."""
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "keel-self-update"},
    )
    def attempt() -> bytes:
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_SEC) as response:
            return bytes(response.read())

    try:
        return _with_retries(attempt)
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
    what is latest, the wheel asset names/URLs (`PRODUCTION_WHEEL_PREFIXES` -- five
    since #425), the `Release/` dir under the
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
    """The RUNNING venv's python (`sys.executable`) -- the venv the production wheels
    are installed INTO. A seam for tests."""
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
      own, so there is no deployment here to update. A packaged install also lands
      here (a frozen bundle is outside any launch folder), so the message names the
      desktop path -- the installer, never a command (#439);
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
            "the one this launch folder runs on. (A packaged install also resolves "
            "from outside any launch folder -- it updates by downloading the new "
            "installer, not by running a command; see docs/desktop-install.md.)"
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
    a refusal reason otherwise. A deployment is the release WHEELS: an editable
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
        "install) -- a deployment is the release wheels; re-deploy from the "
        "release (docs/operator-runbook.md, 'Deploying a new version')."
    )


def select_production_wheels(release: ReleaseInfo, version: str) -> tuple[ReleaseAsset, ...]:
    """The production wheel assets for `version`, in `PRODUCTION_WHEEL_PREFIXES`
    order, matched by exact `<prefix>-<version>-` name -- never `*.whl`, so the
    fake/robinhood/kraken wheels can never ride along. PURE; raises `UpdateError`
    naming any of them the release does not carry."""
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
            f"release {release.tag} does not carry every production wheel "
            f"(missing: {', '.join(missing)}). Its assets: {names}. A release without "
            "all of them cannot be deployed -- see docs/RELEASING.md, 'Release assets'."
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
    (never a guess).

    A PACKAGED install short-circuits to its one refusal and offers nothing, ever:
    D6 (#439, docs/decisions/0001-desktop-update-path.md) decided the desktop product
    updates per-release installer, so there is no plan to build. Every other refusal
    below is venv vocabulary -- a frozen bundle would pile "outside the launch folder"
    and "no distributions installed" onto the packaged reason, each true and each
    useless to someone whose update path is a download."""
    info = build if build is not None else build_info()
    dists = installed if installed is not None else installed_distributions()
    launch = launch_dir if launch_dir is not None else _launch_dir()
    venv = venv_python if venv_python is not None else _running_python()
    pkg_file = package_file if package_file is not None else _running_package_file()

    if is_packaged():
        return UpdatePlan(
            current_version=info.version,
            latest_version=release.version,
            latest_tag=release.tag,
            wheel_names=(),
            wheel_urls=(),
            release_dir=launch / "Release",
            db_paths=tuple(sorted(launch.glob("keel*.db"))),
            venv_python=venv,
            offered=False,
            refusal_reasons=(packaged_update_refusal(),),
        )

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
            "here to update; a deployment is a venv installed from the production "
            "wheels."
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
    def attempt() -> bytes:
        with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT_SEC) as response:
            return bytes(response.read(_MAX_DOWNLOAD_BYTES + 1))

    try:
        payload = _with_retries(attempt)
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
    wheel paths>`. uv is a deployment dependency (the runbook says so); an absent uv is
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
            "manual procedure in docs/operator-runbook.md ('Deploying a new version'). "
            "(A packaged install never needs uv and never reaches this step: it "
            "updates by downloading the new installer -- docs/desktop-install.md.)"
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
    "the production wheels, `uv pip install --python <venv>` them by path, then "
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
    -> download the production wheels (verified non-empty, read bounded at 200 MiB) ->
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
    # Release/ that are not this release's (the previous version's). They are
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

    # DOWNLOAD FIRST, THEN BACK UP -- and this inverts what this function used to do, so the
    # argument belongs here rather than in the issue that moved it (#676).
    #
    # The old order backed up every database before fetching anything, on the reasoning that
    # "if anything after this point half-happens, the databases' pre-update state exists on
    # disk". That guarantee is real and it is UNCHANGED by this order, because a download
    # cannot make anything half-happen TO A DATABASE: `_download_file` writes only into
    # `Release/`. The only steps that touch a database are the install (which replaces the
    # running binary) and the migrate, and both still run after every backup exists.
    #
    # What the old order did do is spend the expensive, irreversible work before the cheap,
    # failure-prone one. On 2026-09-01 a transient stall on the FIRST of five wheels threw away
    # ~466 MB of `sqlite3.backup()` across three databases -- and because backups are
    # timestamped and deliberately never deleted, the retry left a second full set behind
    # rather than reusing the first. A failed download now costs nothing but the partial wheel
    # the handler below already removes.
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
        # EMPTY, and that is the change: no database was touched, so there is nothing to
        # report and nothing on disk to clean up.
        return UpdateResult(
            ok=False,
            steps=tuple(steps),
            error=str(exc),
            rolled_back=False,
            backups=(),
        )

    # Every wheel is on disk. NOW back up -- before the install, which is the first step that
    # can change a database. Each is a consistent SQLite snapshot, and a same-second name never
    # overwrites one.
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

    new_keel = console_entry(plan.venv_python)
    installed = False  # whether the wheels FINISHED installing (uv returned success)
    try:
        say(f"installing the production wheels into the RUNNING venv ({plan.venv_python})")
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
                ok=False,
                steps=tuple(steps),
                error=error,
                rolled_back=False,
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
            "(best-effort); the re-install covers only the superseded set, so a "
            "distribution outside it that this update newly installed (a wheel "
            "the previous release did not ship) may remain at "
            f"{plan.target_version}, leaving the venv a MIXED set -- confirm with "
            "`keel versions`; and if the old build cannot open the migrated "
            "databases, restore from the .bak-before-* backups beside them."
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
            ok=False,
            steps=tuple(steps),
            error=error,
            rolled_back=rolled_back,
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


# -- the relaunch, and why there is no longer one --------------------------------------------------
#
# `build_relaunch_argv` and `relaunch_tui` stood here until #541. They existed for ONE caller --
# the console's Account-menu update view, which had to replace its own process because a
# long-running curses front-end keeps the build it started with. The console is deleted, the
# dashboard with it, and nothing else ever called them: an `execv` reachable from no code path is
# a loaded gun in a drawer.
#
# Their fallback was also, by this commit, wrong: with no argv naming a subcommand they built
# `[keel, "tui"]`, which is now a command that does not exist. A relaunch that execs into
# `Error: No such command 'tui'` is worse than no relaunch, because it happens after the new build
# is already installed.
#
# `keel serve` needs no equivalent. It is a server an operator stops and starts, and the browser
# tab reconnects to whatever is listening; the CLI path always printed the command rather than
# relaunching anything, and that is now the only path.


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
        f"launch folder {plan.launch_dir}: download the production wheels to "
        f"{plan.release_dir}, back up every keel database once they are all on disk "
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


def packaged_check_lines(build: BuildInfo, release: ReleaseInfo) -> list[str]:
    """The check report for a PACKAGED install (D6, #439): the same first line as
    `render_plan_lines` (current vs latest, so the two front-ends cannot disagree about
    the facts), then exactly one verdict line in DESKTOP vocabulary -- the download URL
    and docs/desktop-install.md, never a venv word (`uv`, `site-packages`, `not
    offered`): a packaged install is not being refused an update, it has a different
    one. The version comparison is `is_newer_version`'s, so a junk tag stays an honest
    "cannot compare" with the releases URL as the user's path. PURE."""
    lines = [f"current: {build.version}   latest: {release.version} (tag {release.tag})"]
    newer = is_newer_version(release.version, build.version)
    if newer is None:
        lines.append(
            f"cannot compare the running version {build.version!r} with the release tag "
            f"{release.tag!r} -- not semver. The latest installer is always at "
            f"{RELEASES_URL}."
        )
    elif newer:
        lines.append(
            f"a newer release exists: {release.tag} -- a packaged install updates by "
            f"re-downloading the installer; get it from {RELEASES_URL} (see "
            "docs/desktop-install.md, 'How updates arrive')."
        )
    else:
        lines.append(f"you are at the latest release ({build.version}) -- nothing to do.")
    return lines


def render_plan_lines(plan: UpdatePlan) -> list[str]:
    """The check report: current vs latest, then the whole plan (or every refusal).
    The exact lines the CLI prints and the console's update view renders -- ONE
    renderer, so the two front-ends cannot drift. PURE."""
    lines = [
        f"current: {plan.current_version}   latest: {plan.latest_version} (tag {plan.latest_tag})"
    ]
    if not plan.offered:
        if plan.refusal_reasons:
            lines.append("update NOT offered:")
            lines.extend(f"  - {reason}" for reason in plan.refusal_reasons)
        else:
            lines.append(f"already at the latest release ({plan.latest_version}) -- nothing to do.")
        return lines
    lines.append(f"update available: {plan.current_version} -> {plan.latest_version}")
    lines.append(f"the plan (launch folder {plan.launch_dir}):")
    for name in plan.wheel_names:
        lines.append(f"  wheel: {name}")
    lines.append(f"  download to: {plan.release_dir}")
    if plan.db_paths:
        names = ", ".join(path.name for path in plan.db_paths)
        lines.append(f"  then back up (never deleted): {names}")
        lines.append(f"  backups named: <db>.bak-before-{plan.latest_version}-<timestamp>")
    else:
        lines.append("  then back up: no keel*.db databases in the launch folder")
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
    human-gated: download the five production wheels to Release/, back up every
    keel*.db first, install into the RUNNING venv with uv, migrate, verify with
    `keel versions`, and only then clean the superseded wheels. NEVER automatic: the
    full run demands a typed confirmation at a terminal. `--check` mutates nothing.

    On a PACKAGED install (D6, #439) there is nothing to run -- the desktop product
    updates per-release installer -- so both `--check` and the full command are the
    same read-only report: the version comparison and where to download, with no gate
    (nothing mutates, so there is nothing to confirm) and a network failure rendered
    calm with exit 0 (an unavailable check is not an error state).

    On success this command does NOT relaunch anything (a CLI process is not the
    TUI) -- it prints the command to start the new build. Only the TUI relaunches
    itself.
    """
    try:
        release = latest_release()
    except UpdateError as exc:
        if is_packaged():
            click.echo(
                f"could not check for a newer release ({exc}) -- you are running "
                f"{build_info().version}; the latest installer is always at "
                f"{RELEASES_URL}."
            )
            return
        raise click.ClickException(str(exc)) from exc

    if is_packaged():
        for line in packaged_check_lines(build_info(), release):
            click.echo(line)
        return

    try:
        plan = plan_update(release)
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
            "a long-running process keeps the build it started with: stop and restart "
            "`keel serve` (or your deployment wrapper) to pick up the new build -- this "
            "command does not restart anything for you."
        )
        return
    ctx.exit(1)
