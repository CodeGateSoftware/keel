"""A macOS notification posted by keel itself (#1010).

Why this exists beside `keel_core.alerting` and `keel.notifications`: a vetoed live DCA buy is a
normal zero-exit cycle. The wrapper's `osascript` alerts fire only on non-zero exits, and the
webhook path is opt-in and unconfigured on the live deployment, so the operator found out days
late, more than once. This is the channel that reaches the screen on the cycle that skipped.

**Inert where it cannot help.** Off macOS, with `KEEL_DISABLE_MACOS_NOTIFY` set, and under pytest
without an injected `runner`, `post` does nothing and returns `False` -- a test run must never
pop a banner on the developer's Mac, and a Linux CI box has no `osascript`.

**Never breaks the caller.** Every error is swallowed and reported as `False`; a notification
must not cost a trading cycle. The 10 s timeout bounds a hung Notification Centre.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from typing import Any

#: Set (to anything) to silence every post, e.g. on a deployment that does not want banners.
DISABLE_ENV = "KEEL_DISABLE_MACOS_NOTIFY"

OSASCRIPT = "/usr/bin/osascript"
TIMEOUT_SEC = 10


def _escape(text: str) -> str:
    """Make `text` safe inside an AppleScript string literal: `\\` and `"` escaped, and line
    breaks flattened to spaces (a raw newline would end the literal and break the script)."""
    flat = text.replace("\r", " ").replace("\n", " ")
    return flat.replace("\\", "\\\\").replace('"', '\\"')


def _run(argv: list[str]) -> Any:
    return subprocess.run(argv, timeout=TIMEOUT_SEC, check=True, capture_output=True)


def post(
    title: str,
    message: str,
    *,
    runner: Callable[[list[str]], Any] | None = None,
) -> bool:
    """Post a macOS notification; `True` only when `osascript` ran cleanly. Never raises."""
    try:
        if sys.platform != "darwin" or os.environ.get(DISABLE_ENV):
            return False
        if runner is None and os.environ.get("PYTEST_CURRENT_TEST"):
            return False
        script = f'display notification "{_escape(message)}" with title "{_escape(title)}"'
        (runner or _run)([OSASCRIPT, "-e", script])
        return True
    except Exception:  # noqa: BLE001 -- a notification must never cost the caller
        return False
