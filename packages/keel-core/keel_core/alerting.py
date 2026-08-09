"""Off-machine delivery for the events that need a human before the next cycle.

keel escalates a handful of conditions by logging CRITICAL and saying, in effect, *act now*:
`reconcile.position_unprotected` ("re-place one or close it before trading on") is the sharpest
example. Until this module those escalations went to a rotating file on the machine the agent
happens to run on, and the live deployment's only other channel is `osascript display
notification` in `keel-live-run.sh` -- visible on that Mac's screen and nowhere else. An
escalation nobody receives is not an escalation; with one cycle per UTC day and no flatten
command, the gap between "logged" and "delivered" is the difference between a bad trade and a
day-long unhedged position.

**Opt-in, and off by default.** No URL configured means no handler is attached at all, so an
offline-first install makes no network call it did not ask for. The URL is read from
`KEEL_ALERT_WEBHOOK` -- process environment first, then the git-ignored `.env` -- because it is
closer to a credential than to configuration, and `config.yaml` is committed.

**Never breaks the caller.** `emit` swallows everything. A logging call must not take down the
trade loop, and this one reaches the network, so it is the most likely handler in the process to
fail. It also must not log its own failure through the `keel` logger: that would re-enter this
handler on a CRITICAL and recurse, so failures go to `logging.Handler.handleError` (stderr)
instead.

**CRITICAL only.** The level is set on the handler, not the logger, so raising log verbosity
never turns the alert channel into a firehose -- and the file handler keeps recording everything
regardless.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from keel_core.telemetry import JsonFormatter

#: Environment (or `.env`) key holding the POST target. Unset disables alerting entirely.
ALERT_WEBHOOK_ENV = "KEEL_ALERT_WEBHOOK"

#: Deliberately short. This runs inline on the logging call, so a hung endpoint would otherwise
#: stall the cycle that is trying to tell someone a position is unprotected.
DEFAULT_TIMEOUT_SEC = 5.0


class WebhookAlertHandler(logging.Handler):
    """POST each CRITICAL record to `url` as the same JSON object the log file receives."""

    def __init__(self, url: str, *, timeout: float = DEFAULT_TIMEOUT_SEC) -> None:
        super().__init__(level=logging.CRITICAL)
        self.url = url
        self.timeout = timeout
        self.setFormatter(JsonFormatter())

    def emit(self, record: logging.LogRecord) -> None:
        try:
            body = self.format(record).encode("utf-8")
            request = urllib.request.Request(
                self.url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=self.timeout):
                pass
        except Exception:
            # Deliberately broad, and deliberately NOT `log_exception`: see module docstring.
            self.handleError(record)


def resolve_webhook_url(env_path: str | Path = ".env") -> str | None:
    """The configured alert URL, or `None` when alerting is off.

    Process environment wins over `.env` so a one-off run can redirect alerts without editing a
    file the rest of the deployment shares.
    """
    from_env = os.environ.get(ALERT_WEBHOOK_ENV)
    if from_env:
        return from_env

    path = Path(env_path)
    if not path.exists():
        return None
    value = dotenv_values(path).get(ALERT_WEBHOOK_ENV)
    return value or None


def install_alerting(
    logger: logging.Logger,
    url: str | None,
    *,
    timeout: float = DEFAULT_TIMEOUT_SEC,
) -> WebhookAlertHandler | None:
    """Attach (or remove) the CRITICAL webhook handler on `logger`. Returns it, or `None`.

    Idempotent for the same reason `configure_logging` is: it runs once per CLI invocation, and
    accumulating handlers would send one copy of every alert per call.
    """
    for handler in list(logger.handlers):
        if isinstance(handler, WebhookAlertHandler):
            logger.removeHandler(handler)
            handler.close()

    if not url:
        return None

    handler = WebhookAlertHandler(url, timeout=timeout)
    logger.addHandler(handler)
    return handler


def alert_payload(record: logging.LogRecord) -> dict[str, Any]:
    """The JSON body `WebhookAlertHandler` would send for `record`. Exposed for tests."""
    return json.loads(JsonFormatter().format(record))


__all__ = [
    "ALERT_WEBHOOK_ENV",
    "DEFAULT_TIMEOUT_SEC",
    "WebhookAlertHandler",
    "alert_payload",
    "install_alerting",
    "resolve_webhook_url",
]
