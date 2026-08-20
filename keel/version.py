"""Report exactly which code is running (version + commit + working-tree state).

For a tool that can move money, *"which build was that?"* has to have an answer. It currently
does not: `version` in `pyproject.toml` has never moved off `0.1.0`, there are no tags, and
`uv run keel` executes whatever happens to be checked out -- including a half-finished edit.

Two sources, in priority order:

1. **`keel/_build_info.py`**, written by the release workflow immediately before `uv build`. An
   installed release therefore reports the exact commit it was built from, with no git and no
   repository present at runtime.
2. **git**, when running from a checkout. Reports the working commit AND whether the tree is
   **dirty** -- the distinction that matters most here, because a dirty tree means the running
   code corresponds to no commit at all and the run is not reproducible.

Falls back to `unknown` rather than raising: failing to identify the build is a reason to warn
loudly, not a reason to prevent the tool from starting.

**One build identity is not enough.** `BuildInfo` describes the `keel-trader` distribution only,
and keel is installed as *several* distributions (`keel-core`, `keel-broker-*`). A deployment
upgraded by installing the `keel_trader` wheel alone leaves the rest at whatever version they
were -- `~/keel` ran `keel-trader 0.5.7` against `keel-core 0.5.5` for two releases, and
`keel --version` reported the new number throughout, because that is all it can see. So this
module also reports the *install*: `check_install()` reads every `keel-*` distribution present in
the running interpreter's environment, which is the only view that can show a partial upgrade.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from importlib import metadata

_GIT_TIMEOUT_SEC = 3


@dataclass(frozen=True)
class BuildInfo:
    version: str
    commit: str
    dirty: bool
    source: str  # "release" | "checkout" | "unknown"

    @property
    def full_version(self) -> str:
        """Version bound to the build hash as semver build metadata: `0.1.0+<commit>`.

        This is the canonical "which build is this" string -- the version alone is ambiguous
        (many commits share a version between bumps), the commit alone omits the human-facing
        number. `+` is the semver / PEP 440 local-version separator, so tooling recognises it.
        """
        if self.commit == "unknown":
            return self.version
        return f"{self.version}+{self.commit}"

    @property
    def is_reproducible(self) -> bool:
        """False when the running code corresponds to no commit -- a dirty tree, or no idea.

        A `release` build is only reproducible if it is also clean: a stale stamp in a modified
        checkout is exactly the case that must not pass.
        """
        return self.source in {"release", "checkout"} and not self.dirty

    def describe(self) -> str:
        parts = [f"keel {self.full_version}"]
        if self.dirty:
            parts.append("(DIRTY)")
        parts.append(f"[{self.source}]")
        return " ".join(parts)


#: The DISTRIBUTION name. Deliberately not "keel": that name is already taken on PyPI by an
#: unrelated project ("Kill proccesses effectively and easily"), so `pip install keel` fetches a
#: stranger's package. For a tool that places live orders, an install path that can resolve to
#: someone else's code is a supply-chain hazard, not a cosmetic clash. The IMPORT package and the
#: CLI command both remain `keel`; only the distribution is renamed.
DISTRIBUTION = "keel-trader"


def _package_version() -> str:
    for name in (DISTRIBUTION, "keel"):
        try:
            return metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
    return "unknown"


def is_packaged() -> bool:
    """True when running inside a frozen bundle rather than from a venv or a checkout.

    Lives here rather than in `keel/install.py` (its first home) because `keel.version` is a leaf
    -- it imports nothing from keel -- and "how was this built, and how is it running" is exactly
    this module's subject. `keel.install` re-exports it so there is one detector, not two that
    can disagree about the same process.

    Both markers are checked: PyInstaller sets `sys.frozen` for every build mode but `sys._MEIPASS`
    only for `--onefile`, and other freezers set one or the other.
    """
    return bool(getattr(sys, "frozen", False)) or hasattr(sys, "_MEIPASS")


def _git(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SEC,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _embedded():
    """The release stamp, or `None`. A seam so tests can exercise the git path deterministically
    regardless of whether a stamp happens to exist on the machine."""
    try:
        from keel import _build_info as embedded  # type: ignore[attr-defined]
    except ImportError:
        return None
    return embedded


def build_info() -> BuildInfo:
    """Resolve the running build. Never raises."""
    embedded = _embedded()

    if is_packaged():
        # A frozen bundle has no checkout, so there is nothing for git to tell us about it -- and
        # asking is actively harmful. `_git` inherits the process CWD, so a packaged app launched
        # from inside ANY git repository reads that repository's HEAD, finds it disagrees with the
        # stamp, and marks a legitimate signed release DIRTY. The user then reads "this build is
        # NOT reproducible -- do not run it against live funds" about a build that is both. A
        # warning that fires on correct builds is a warning people learn to ignore, and this is
        # the one that must never be ignored.
        #
        # The stale-stamp hazard the git cross-check below exists for cannot arise here: there is
        # no working tree to have edited. An UNSTAMPED bundle is `unknown`, never `checkout` --
        # it is not one -- which also keeps `plan_update`'s `source != "release"` refusal correct.
        if embedded is None:
            return BuildInfo(
                version=_package_version(), commit="unknown", dirty=False, source="unknown"
            )
        return BuildInfo(
            version=getattr(embedded, "VERSION", _package_version()),
            commit=getattr(embedded, "COMMIT", "unknown"),
            dirty=bool(getattr(embedded, "DIRTY", False)),
            source="release",
        )

    if embedded is not None:
        stamped_commit = getattr(embedded, "COMMIT", "unknown")
        dirty = bool(getattr(embedded, "DIRTY", False))
        # ⚠️ A STALE stamp in a working checkout would otherwise claim `[release]` and hide a
        # dirty tree -- which is precisely the misreport this module exists to prevent. If git
        # is present and disagrees with the stamp, believe git.
        head = _git("rev-parse", "--short=12", "HEAD")
        if head is not None:
            if head != stamped_commit or _git("status", "--porcelain"):
                dirty = True
        return BuildInfo(
            version=getattr(embedded, "VERSION", _package_version()),
            commit=stamped_commit,
            dirty=dirty,
            source="release",
        )

    commit = _git("rev-parse", "--short=12", "HEAD")
    if commit is None:
        return BuildInfo(
            version=_package_version(), commit="unknown", dirty=False, source="unknown"
        )

    status = _git("status", "--porcelain")
    return BuildInfo(
        version=_package_version(),
        commit=commit,
        # `status` is None only if the second git call failed after the first succeeded --
        # treat that as dirty, because "we could not tell" must not read as "clean".
        dirty=status is None or bool(status),
        source="checkout",
    )


# -- the whole install, not just this distribution ----------------------------------------------

#: Everything the workspace publishes is named `keel-<something>`, so a prefix match enumerates
#: the install without a hand-maintained list that a new package would silently fall out of. The
#: bare name `keel` is excluded on purpose: it belongs to an unrelated PyPI project (see
#: `DISTRIBUTION`), and folding a stranger's version number into this check would be nonsense.
_FAMILY_PREFIX = "keel-"

#: Distributions that must not exist in a deployment. `keel-broker-fake` is a dev-only fake venue
#: (see the `dev` group in `pyproject.toml`) whose reason to exist is proving two-plugin
#: discovery -- it registers a `fake` entry under `keel.brokers`, so an engine that has it
#: installed advertises a venue that trades nothing. It was found installed in `~/keel`. Nothing
#: calls `load_broker()` today, so it is inert; "inert" is a property of this release, not of the
#: package, and is not a reason to leave it on a box that moves money.
DEV_ONLY_DISTRIBUTIONS = frozenset({"keel-broker-fake"})


def _canonical(name: str) -> str:
    """PEP 503 normalisation: `keel_broker_api` and `Keel-Broker-API` are the same distribution."""
    return re.sub(r"[-_.]+", "-", name).strip().lower()


@dataclass(frozen=True)
class InstallReport:
    """Every `keel-*` distribution visible to the running interpreter, and what is wrong with it.

    A pure value: `check_install()` builds it from the environment, everything else here is
    derived, so the rules below are testable without installing anything.
    """

    #: canonical distribution name -> version, e.g. `{"keel-core": "0.6.0"}`.
    distributions: dict[str, str]
    #: `build_info().source` at the time of the check; `"release"` means a deployment.
    source: str = "unknown"

    @property
    def versions(self) -> list[str]:
        return sorted(set(self.distributions.values()))

    @property
    def is_consistent(self) -> bool:
        """False when the distributions disagree -- i.e. a partial upgrade.

        An empty install is vacuously consistent: running from a source checkout with nothing
        installed is a legitimate state, and it is not this check's job to invent a failure.
        """
        return len(self.versions) <= 1

    @property
    def dev_only_installed(self) -> list[str]:
        return sorted(n for n in self.distributions if n in DEV_ONLY_DISTRIBUTIONS)

    @property
    def problems(self) -> list[str]:
        """Every reason this install must not be trusted, worst first. Empty means healthy.

        A dev-only package is a problem in a **release** build only: a checkout is exactly where
        `keel-broker-fake` is supposed to be, and a check that cried wolf on every developer's
        machine would be ignored by the time it mattered. Build state (`DIRTY`, `[checkout]`) is
        deliberately NOT a problem here -- `describe()` already says it, and this report is about
        what is installed, not about which commit it came from.
        """
        found: list[str] = []
        if not self.is_consistent:
            found.append(
                f"PARTIAL INSTALL: {len(self.distributions)} keel distributions at "
                f"{len(self.versions)} different versions ({', '.join(self.versions)}). "
                "`keel --version` reports keel-trader's version alone and cannot see this. "
                "Reinstall every wheel by path (README, 'Deploying a new version')."
            )
        if self.source == "release":
            for name in self.dev_only_installed:
                found.append(
                    f"{name} is installed. It is a dev-only package that registers a venue "
                    "entry point and must not exist in a deployment. Remove it: "
                    f"`uv pip uninstall --python .venv {name}`."
                )
        return found


def installed_distributions() -> dict[str, str]:
    """Canonical name -> version for every installed `keel-*` distribution. Never raises.

    Scoped to the interpreter that is running, so `.venv/bin/keel` reports that venv -- which is
    what makes this answerable in a deployment with no repository and no git.
    """
    found: dict[str, str] = {}
    try:
        dists = list(metadata.distributions())
    except Exception:  # pragma: no cover -- a broken environment must not stop the CLI
        return found
    for dist in dists:
        try:
            name = _canonical(dist.metadata["Name"] or "")
            version = dist.version
        except Exception:  # pragma: no cover -- one unreadable dist must not hide the rest
            continue
        if not name.startswith(_FAMILY_PREFIX):
            continue
        # First occurrence wins: that is the copy the import machinery resolves, so it is the
        # code that would actually run. A second copy further down `sys.path` is unreachable.
        found.setdefault(name, version)
    return found


def check_install(source: str | None = None) -> InstallReport:
    """Read the environment into an `InstallReport`. `source` defaults to this build's."""
    return InstallReport(
        distributions=installed_distributions(),
        source=build_info().source if source is None else source,
    )
