"""Where keel's state lives: config, database, credentials and logs.

Every one of those has always resolved relative to the process's **current working directory** --
`keel.db`, `config.yaml`, `.env`, `logs/keel.log`. That is not an accident, and for the deployment
model it is exactly right: `~/keel` is one folder holding one deployment's config, database,
credentials and logs, and the four profiles (paper, live, paper-hourly, paper-equities) are sibling
folders that share nothing. `cd` selects which deployment you are operating. The operator runbook is
written on that premise -- "every path below is relative to it".

**It stops working the moment keel is launched by anything other than a shell.** A macOS
application bundle opened from Finder runs with `cwd = /`, so those defaults resolve to
`/keel.db`, `/config.yaml`, `/.env` -- not writable by an unprivileged user, and nowhere near
where the operator believes their data is. A signed bundle is itself read-only, so it cannot hold
them either. That is issue #434, and this module is its answer.

## The rule, in one line

**An existing deployment folder always wins; an app-data directory is the fallback for a launch
that has no deployment folder to be in.**

Concretely, in order:

1. `KEEL_HOME`, if set. The explicit escape hatch -- one env var, no ambiguity, and the thing to
   reach for when a wrapper script needs to pin a deployment regardless of cwd.
2. The current working directory, **if it looks like a deployment** (`is_deployment_root`). This is
   what keeps every existing install byte-identical: `~/keel` holds `config.yaml` and `keel*.db`,
   so it is detected, and nothing about its behaviour changes. A source checkout is detected too,
   for the same reason -- developers keep the working tree they have.
3. `app_data_dir()` -- the OS-standard per-user location, created on demand.

## Why detection rather than migration

The alternative -- move state into the app-data directory on upgrade -- was rejected. The failure
mode of a wrong guess is not an error message: it is keel opening a **fresh empty database** beside
a populated one and reporting a healthy deployment with no positions and no history. Detection
fails safe in the direction that matters, because the deployment folder it is looking for either
exists (use it, unchanged) or does not (nothing to lose).

Explicit `--db` / `--config` flags override everything here, exactly as before. This module only
decides what a bare invocation means.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

#: Env var pinning the state root, ahead of every other rule. A wrapper that must not depend on
#: cwd sets this; nothing else reads it.
HOME_ENV_VAR = "KEEL_HOME"

#: Files whose presence marks a directory as somebody's deployment. Any ONE is enough.
#:
#: * `config.yaml` -- what `keel init-config` writes.
#: * `.env` -- credentials. A folder holding these is a working folder even before `keel init` has
#:   run, and `load_secrets()` has always read the one in cwd. Dropping it from this list would
#:   silently stop finding credentials that used to be found, which is the exact class of
#:   regression this module must not introduce.
#: * `keel*.db` (below) -- covers a folder whose config lives under a profile-specific name
#:   (`config.live-sandbox.yaml` and friends) but whose ledger is still right there.
#:
#: The list is deliberately generous. A false positive means keel uses the folder it was run in,
#: which is precisely the historical behaviour and therefore costs nothing; a false negative would
#: send a bare invocation to an app-data directory while a real ledger sat unread in cwd.
_DEPLOYMENT_MARKERS = ("config.yaml", ".env")
_DEPLOYMENT_DB_GLOB = "keel*.db"


def app_data_dir() -> Path:
    """The OS-standard per-user directory for keel's state. Not created here -- see `state_root`.

    **One directory for config, database, logs and credentials together**, rather than splitting
    config into a roaming location and data into a local one as some platform conventions suggest.
    That split would be more idiomatic and would break the mental model the whole operator runbook
    is written in: a deployment is a *folder you can look inside*. Keeping the shapes identical
    means the runbook's instructions still describe the app-data install, and an operator can move
    between the two by copying a directory.

    Windows uses `LOCALAPPDATA` in preference to `APPDATA` because the roaming profile is the wrong
    place for a SQLite database -- it would be copied between machines on login, which is both slow
    and a corruption risk for a file being written by a running process.
    """
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "keel"
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        return Path(base) / "keel" if base else Path.home() / "AppData" / "Local" / "keel"
    # XDG, and its documented default for everything else.
    base = os.environ.get("XDG_DATA_HOME")
    return Path(base) / "keel" if base else Path.home() / ".local" / "share" / "keel"


def is_deployment_root(path: Path) -> bool:
    """Whether `path` holds somebody's deployment.

    Deliberately cheap and deliberately generous: a false positive costs nothing (keel uses the
    folder it was run in, which is the historical behaviour), while a false negative is the
    dangerous direction -- it would send a bare invocation to an app-data directory while a real
    ledger sat in the current folder unread.

    Never raises. An unreadable directory is simply not a deployment root, which degrades to the
    app-data fallback rather than to a traceback out of a path lookup.
    """
    try:
        if any((path / marker).is_file() for marker in _DEPLOYMENT_MARKERS):
            return True
        return any(path.glob(_DEPLOYMENT_DB_GLOB))
    except OSError:
        return False


def state_root(*, create: bool = False) -> Path:
    """The directory a bare invocation resolves its state against. See the module docstring.

    `create=True` makes the app-data directory if that is what was selected; it never creates a
    deployment folder, because a deployment folder that does not exist is not one this function
    chose.
    """
    pinned = os.environ.get(HOME_ENV_VAR)
    if pinned:
        root = Path(pinned).expanduser()
        if create:
            root.mkdir(parents=True, exist_ok=True)
        return root

    try:
        cwd = Path.cwd()
    except OSError:
        # A deleted cwd is survivable here and should not take down a path lookup.
        cwd = None
    if cwd is not None and is_deployment_root(cwd):
        return cwd

    root = app_data_dir()
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root


def default_config_path() -> Path:
    return state_root() / "config.yaml"


def default_db_path() -> Path:
    return state_root() / "keel.db"


def default_env_path() -> Path:
    return state_root() / ".env"


def resolve_under_state_root(path: str | Path) -> Path:
    """Resolve a possibly-relative configured path against the state root.

    `logging.file` in config.yaml is the caller that matters: it defaults to `logs/keel.log` and is
    documented as relative-by-design, so that a deployment's log lands beside its database. Against
    a bare `Path(...)` that relativity means "relative to cwd", which is the same defect this module
    exists to fix -- an app-bundle launch would write to `/logs/keel.log`.

    An absolute path is returned unchanged, so an operator who pinned one keeps it.
    """
    candidate = Path(path).expanduser()
    return candidate if candidate.is_absolute() else state_root() / candidate
