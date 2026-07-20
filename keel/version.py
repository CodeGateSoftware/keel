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
"""

from __future__ import annotations

import subprocess
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
