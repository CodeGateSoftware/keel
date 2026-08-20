"""What an installer must decide, in one place both the installer and the app can read (#438/#439).

The desktop product has no self-update. That is D6's decision and it is deliberate: bundle-aware
self-update buys convenience and costs an update channel that must itself be secured, and for a
tool that moves real money a user deliberately downloading a signed installer is the better trust
posture. So **the installer is the update path**, and "what should happen when this build meets
the one already on disk" is a question the installer has to answer correctly every time.

It lives here rather than inside an Inno Setup script or a `.pkg` postinstall because it is real
logic with a real failure mode, and neither of those is a place where logic can be tested. The
installer calls it; `keel update` reads the same module to explain itself on a packaged install.

**The rule that is not obvious.** "Versions differ, so update" is right in one direction only.
`keel/data/db.py` migrates with `if current < target` and ships **no down-migrations**. A database
already at schema N, opened by a build that expects N-2, does not fail loudly: `migrate` finds
nothing to apply and returns, and the old code then runs against tables and columns it was never
written against. So a downgrade is a confirmation with a specific warning, never a silent update.

**And the rule that is absolute.** An installer replaces the PROGRAM. It never touches the
DEPLOYMENT -- `config.yaml`, `keel*.db`, `.env`, `logs/`. An operator's allowlist, caps and
trading mode are hand-edited and irreplaceable, and a database is the only record of what the
engine has done. `keel_core.paths` already separates the two (#434); this module keeps them
separate at the point where a wizard would be most tempted to conflate them.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

#: Where a release is downloaded from, named in every refusal that tells a desktop user to update
#: by downloading rather than by running a command.
RELEASES_URL = "https://github.com/CodeGateSoftware/keel/releases/latest"


def is_packaged() -> bool:
    """True when running inside a frozen bundle rather than from a venv.

    Both markers are checked because PyInstaller sets `sys.frozen` for every build mode but only
    sets `sys._MEIPASS` for `--onefile`, and other freezers set one or the other. A false positive
    here costs a refusal an operator can work around; a false negative sends a desktop user to
    install `uv`, which is the outcome #439 exists to stop.
    """
    return bool(getattr(sys, "frozen", False)) or hasattr(sys, "_MEIPASS")


# -- where things go ---------------------------------------------------------------------------


def default_program_dir(platform: str | None = None, *, home: Path | None = None) -> Path:
    """Where the installer proposes to put the BINARY.

    Per-user on both platforms, deliberately: a machine-wide install needs elevation, and an
    elevation prompt on a first run is exactly the friction this milestone exists to remove. It
    also means an uninstall cannot need admin either.

    `platform`/`home` are parameters rather than reads of `sys.platform` so both answers are
    testable from one machine -- the same reason `keel_core.paths`' tests can check the Windows
    branch on a Mac.
    """
    platform = sys.platform if platform is None else platform
    home = Path.home() if home is None else home
    if platform == "darwin":
        return Path("/Applications/keel.app")
    if platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else home / "AppData" / "Local"
        return root / "Programs" / "keel"
    return home / ".local" / "share" / "keel-app"


def fallback_program_dir(platform: str | None = None, *, home: Path | None = None) -> Path | None:
    """The alternative to offer when the default is not writable, or `None` where there isn't one.

    macOS only: `/Applications` needs admin on a managed machine, and `~/Applications` is the
    documented per-user equivalent that does not. Windows' default is already per-user."""
    platform = sys.platform if platform is None else platform
    home = Path.home() if home is None else home
    if platform == "darwin":
        return home / "Applications" / "keel.app"
    return None


def default_deployment_dir(platform: str | None = None) -> Path:
    """Where the installer proposes to put CONFIG, DATABASE, `.env` and LOGS.

    A separate question from `default_program_dir`, and conflating them is the trap this module
    exists to avoid: the program directory is replaced wholesale on every update, so anything of
    the operator's that lived there would be destroyed by an upgrade.

    Delegates to `keel_core.paths.app_data_dir` rather than restating it, so the installer's
    default and the runtime's discovery cannot disagree -- an installer that proposed a folder
    the app then did not look in would produce a deployment that appears empty on first launch.
    """
    from keel_core import paths

    if platform is None or platform == sys.platform:
        return paths.app_data_dir()
    # Asked about a platform that is not this one: mirror `app_data_dir`'s branches. Only useful
    # for building an installer for the other OS, and for testing both from one machine.
    home = Path.home()
    if platform == "darwin":
        return home / "Library" / "Application Support" / "keel"
    if platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        return Path(base) / "keel" if base else home / "AppData" / "Local" / "keel"
    base = os.environ.get("XDG_DATA_HOME")
    return Path(base) / "keel" if base else home / ".local" / "share" / "keel"


# -- what to do about what is already there ----------------------------------------------------


class InstallDecision(str, Enum):
    """What the installer is about to do. Every member except `FRESH` and `UPGRADE` needs the
    user to agree first."""

    FRESH = "fresh"
    UPGRADE = "upgrade"
    REINSTALL = "reinstall"
    DOWNGRADE = "downgrade"
    UNCOMPARABLE = "uncomparable"


@dataclass(frozen=True)
class InstallPlan:
    decision: InstallDecision
    installed_version: str | None
    incoming_version: str
    #: Whether the installer must stop and ask before proceeding.
    needs_confirmation: bool
    #: One sentence naming what is about to happen, for the confirmation dialog.
    summary: str
    #: The specific hazard, where there is one. Shown in addition to `summary`, never instead.
    warning: str | None = None

    @property
    def may_proceed_silently(self) -> bool:
        return not self.needs_confirmation


#: Said in full wherever a downgrade is confirmed, because the failure it describes is silent.
DOWNGRADE_WARNING = (
    "This is OLDER than the version already installed. keel's database migrations are "
    "forward-only -- there are no down-migrations -- so a database already at a newer schema "
    "will not be converted back. The older build will simply find nothing to migrate and then "
    "run against tables it was never written against. If you need to go back, restore a database "
    "backup taken before the upgrade rather than running the older build against the newer "
    "database."
)


def plan_install(incoming_version: str, installed_version: str | None) -> InstallPlan:
    """Decide what installing `incoming_version` over `installed_version` should do.

    `installed_version` is `None` when the target directory holds no keel. It should be read from
    the on-disk artifact's METADATA and never by executing the installed binary: running an old
    build to decide whether to replace it is fragile, and it is the exact case `keel versions`
    exists to catch -- a partially-upgraded tree reports the new number from `--version` while
    running old libraries.
    """
    if installed_version is None:
        return InstallPlan(
            decision=InstallDecision.FRESH,
            installed_version=None,
            incoming_version=incoming_version,
            needs_confirmation=False,
            summary=f"Install keel {incoming_version}.",
        )

    # Lazy: `keel.commands.update` imports THIS module for its packaged refusal, so a
    # module-level import here would close the cycle. `version_key` is the ONE semver reader --
    # a second one in this module would be a second place for "is this newer" to be wrong.
    from keel.commands.update import version_key

    incoming_key = version_key(incoming_version)
    installed_key = version_key(installed_version)
    if incoming_key is None or installed_key is None:
        return InstallPlan(
            decision=InstallDecision.UNCOMPARABLE,
            installed_version=installed_version,
            incoming_version=incoming_version,
            needs_confirmation=True,
            summary=(
                f"keel {installed_version} is already installed here and cannot be compared with "
                f"{incoming_version}."
            ),
            warning=(
                "One of these versions is not semver, so keel cannot tell which is newer. "
                "Proceeding replaces the installed program with this one. " + DOWNGRADE_WARNING
            ),
        )

    if incoming_key > installed_key:
        return InstallPlan(
            decision=InstallDecision.UPGRADE,
            installed_version=installed_version,
            incoming_version=incoming_version,
            needs_confirmation=False,
            summary=f"Update keel {installed_version} to {incoming_version}.",
        )

    if incoming_key == installed_key:
        return InstallPlan(
            decision=InstallDecision.REINSTALL,
            installed_version=installed_version,
            incoming_version=incoming_version,
            needs_confirmation=True,
            summary=f"keel {installed_version} is already installed here.",
            warning=(
                "Reinstalling the same version replaces the program files. Your config, database "
                "and credentials are not touched."
            ),
        )

    return InstallPlan(
        decision=InstallDecision.DOWNGRADE,
        installed_version=installed_version,
        incoming_version=incoming_version,
        needs_confirmation=True,
        summary=f"Replace keel {installed_version} with the OLDER {incoming_version}.",
        warning=DOWNGRADE_WARNING,
    )


#: Everything an installer must leave exactly as it found it. Not a suggestion: an operator's
#: allowlist, caps and trading mode are hand-edited and irreplaceable, and a database is the only
#: record of what the engine has done. `keel.commands.setup.create_config` already refuses to
#: overwrite a config and offers no `force` for a caller to pass; an installer must match it.
NEVER_TOUCHED: tuple[str, ...] = ("config*.yaml", "keel*.db", ".env", "logs/")


def packaged_update_refusal() -> str:
    """Why `keel update` cannot run here, phrased for someone who has never opened a terminal.

    The refusals `keel update` already produces are all correct and all useless to a desktop user:
    they talk about `site-packages` layouts and tell the reader to put `uv` on PATH. A packaged
    user has no venv and no `uv`, and never will -- so the honest message names the actual update
    path, which is downloading the next signed installer (#439's option A)."""
    return (
        "this is a packaged install, which updates by downloading the next release rather than "
        f"from the command line. Get it from {RELEASES_URL} and run it -- it will keep your "
        "config, database and credentials exactly as they are."
    )
