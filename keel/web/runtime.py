"""Where a running `keel serve` says how to reach it, so `keel open` can answer (#756).

── WHY THIS EXISTS AT ALL, GIVEN WHAT `security.py` SAYS ──────────────────────────────────────

Layer 3 of `keel/web/security.py` rests on the session token being ephemeral: *"the token is
minted per process and never written to disk, so closing the server destroys it and every
outstanding cookie becomes a string that authenticates nothing."* `serve` prints that sentence to
the operator on every run.

This module writes the token to disk, so it is bounded by one rule and the rule is the whole
design:

    THE RECORD EXISTS ONLY WHEN THE URL IS NOT BEING SHOWN TO A HUMAN.

`sys.stdout.isatty()` decides. Attached to a terminal, nothing here runs and the posture is
byte-for-byte what it was; `serve` still prints the sentence, and the sentence is still true.

Detached -- `launchd`, a pipe, a container -- the token is going to a file ANYWAY, because that is
what `StandardOutPath` is. An operator's only route back into their own console then is to `grep`
a log for a URL. So the choice at that point is not "token on disk or not"; it is "token in a log
file at the daemon's umask, or token in a `0600` file in a `0700` directory that is deleted on
shutdown and whose staleness is checked before it is offered". The second is strictly less
exposure than the first, and it is the only one that makes `keel serve` usable unattended.

── WHAT THIS IS NOT ───────────────────────────────────────────────────────────────────────────

**Not an authority on whether a server is running.** A `SIGKILL` or a power cut leaves the file
behind, so `live_record` re-checks the pid before offering anything. The file is a hint.

**Not a way to reissue or extend a token.** Nothing here mints; it records what `serve` already
minted, and `forget` drops it. Stopping keel still revokes every outstanding cookie, which is
the operator's revocation gesture and is unchanged.

**Not a lock.** It does not arbitrate who may bind a port -- the OS already refuses a second
bind, and a lock file that could disagree with the kernel would be a second answer to a question
that already has one.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from keel_core.paths import state_root

#: The directory holding one record per serving port. Under the deployment root, beside the
#: database rather than in a global `/tmp`: two deployments on one machine are the ordinary case
#: (#756 counts four), and their consoles must not read each other's records.
RUN_DIR_NAME = "run"

#: `0700` on the directory and `0600` on the file. Group and world get nothing: this holds a live
#: bearer token, and on a shared machine a readable one would be worse than the log line it
#: replaces. Both are asserted by `tests/web/test_runtime_record.py`.
DIR_MODE = 0o700
FILE_MODE = 0o600


def run_dir(*, create: bool = False) -> Path:
    directory = state_root() / RUN_DIR_NAME
    if create:
        directory.mkdir(parents=True, exist_ok=True)
        # Set explicitly rather than trusting the umask: `mkdir`'s mode is masked, and a
        # deployment running under a permissive umask would otherwise get a group-readable
        # directory holding session tokens.
        directory.chmod(DIR_MODE)
    return directory


def record_path(port: int) -> Path:
    """One file per port. The PORT names it and the token lives inside.

    Directory listings leak names, and a filename is readable by anything that can list the
    directory even when the file itself is not. Pinned by
    `test_the_token_is_never_in_the_filename`.
    """
    return run_dir() / f"serve-{int(port)}.json"


def record_serving(*, host: str, port: int, token: str, interactive: bool) -> Path | None:
    """Record how to reach this server, unless a human is watching stdout.

    `interactive` is the caller's `sys.stdout.isatty()` -- passed in rather than read here so the
    decision is visible at the call site in `serve`, where the operator-facing sentence about it
    is also printed, and so a test can exercise both sides without touching the process's own
    streams.

    Returns the path written, or `None` when the rule above says to write nothing.
    """
    if interactive:
        return None
    directory = run_dir(create=True)
    path = directory / f"serve-{int(port)}.json"
    # Written through a per-port temporary file and then renamed, so a reader can never see a
    # half-written record: `os.replace` is atomic within a directory. The temporary carries the
    # same `0600`, because it holds the same token for the moment it exists.
    staging = directory / f".serve-{int(port)}.json.tmp"
    staging.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "host": host,
                "port": int(port),
                "token": token,
                "started_ts": int(time.time()),
            }
        )
    )
    staging.chmod(FILE_MODE)
    os.replace(staging, path)
    return path


def read_record(port: int) -> dict[str, Any] | None:
    """The record for `port`, or `None` if there is none or it cannot be read.

    Deliberately does NOT check liveness -- `live_record` does, and keeping them apart means a
    caller diagnosing a stale file can still see it. A corrupt or truncated file (a crash
    mid-write) reads as absent rather than raising: `keel open` must not traceback at an operator
    whose server has just died.
    """
    path = record_path(port)
    try:
        raw = path.read_text()
    except OSError:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(parsed, dict) or not parsed.get("token"):
        return None
    return parsed


def _process_alive(pid: int) -> bool:
    """Is `pid` a process this user could signal?

    `signal 0` is the standard existence check: it validates the pid and permissions without
    delivering anything. `pid <= 0` is refused before the call, because `os.kill(0, 0)` signals
    the whole process GROUP -- which on a bad record would be this process, and would report the
    stale record as live.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # It exists and belongs to someone else. Not ours to open, and not ours to claim is gone.
        return True
    except OSError:
        return False
    return True


def live_record(port: int) -> dict[str, Any] | None:
    """The record for `port`, but only if the process that wrote it is still around.

    `forget` covers the clean exit. This covers every other kind: a `SIGKILL`, a panic, a power
    cut. Without it `keel open` would hand an operator a URL that refuses them, which reads as
    keel being broken rather than as keel being stopped.
    """
    record = read_record(port)
    if record is None:
        return None
    try:
        pid = int(record.get("pid", 0))
    except TypeError, ValueError:
        return None
    return record if _process_alive(pid) else None


def forget(port: int) -> None:
    """Drop the record. Called from `serve`'s `finally`, so a clean stop leaves nothing behind."""
    try:
        record_path(port).unlink()
    except OSError:
        # Already gone, or a directory that never existed because the run was interactive. Both
        # are the desired end state, and neither is worth failing a shutdown over.
        pass


def url_for(record: dict[str, Any]) -> str:
    """The address to open, rebuilt from a record.

    The bracketing rule for IPv6 is `ServeConfig.url`'s, restated because this side has no
    `ServeConfig` to ask -- `keel open` reads a file written by a process that has since become
    unreachable. `test_an_ipv6_host_is_bracketed_like_serve_prints_it` holds the two spellings
    to the same output.
    """
    host = str(record.get("host", ""))
    bracketed = f"[{host}]" if ":" in host else host
    return f"http://{bracketed}:{int(record.get('port', 0))}/?token={record.get('token', '')}"


def stdout_is_interactive() -> bool:
    """Whether a human is watching this process's stdout.

    Wrapped rather than called inline so `serve` reads as the rule it implements, and because a
    detached stream can raise on `isatty` rather than answering.
    """
    try:
        return bool(sys.stdout.isatty())
    except AttributeError, ValueError:
        return False
