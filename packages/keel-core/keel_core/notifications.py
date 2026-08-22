"""Opt-in, outbound-only notifications for the events that are not CRITICAL logs (#444).

`keel_core.alerting`'s CRITICAL webhook delivers escalations nobody can miss. But the events
an operator most needs are silent precisely because they are NOT errors: rail 17's
attestation expiring fails closed and quietly vetoed a real setup for weeks; a rail arming, a
vetoed setup, an allowance nearly spent and a stale feed under an open position are all
INFO/WARNING facts of a healthy-ish system. This module is the delivery layer for those.

**Notify-only.** There is no control surface here and none may be added: notifications are
strictly outbound (#444 scope; #436 keeps every capability-increasing action TTY-gated). The
taxonomy is closed (`EVENTS`), so a caller cannot smuggle in an event class that does
anything but describe a state.

**Per-event opt-in, default off** -- Freqtrade's `notification_settings` shape: the settings
name event keys, every key defaults to disabled, and an event that is not opted in is never
formatted, never serialized, never sent. The transport stays the ONE generic webhook, so no
URL means no delivery attempt at all (the same offline-first contract `install_alerting`
keeps for CRITICAL records). The URL is resolved from `KEEL_ALERT_WEBHOOK` via
`keel_core.alerting.resolve_webhook_url` -- the existing variable -- because it is closer to
a credential than to configuration and `config.yaml` is committed.

**Never breaks the caller.** `send_event` swallows everything, including its own formatting:
the caller is the trading loop, and a notification failure must never cost a cycle (mirrors
`WebhookAlertHandler.emit`'s contract). Failures are logged at WARNING through the ordinary
`keel` logger -- safe here because this module is not a `logging.Handler`, so a WARNING
cannot re-enter it.

**Two payload formats.** `plain` (default) is a generic JSON object any webhook receiver can
log; `slack` is Slack-compatible `{"text": ...}` -- accepted natively by Slack and Mattermost
and by Discord via its `/slack` endpoint, so one chat format covers the widest set of
receivers without a per-platform matrix.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from keel_core.alerting import DEFAULT_TIMEOUT_SEC
from keel_core.telemetry import log_event

#: The two severities the taxonomy distinguishes. Every #444-named event is `warn` -- each
#: one gates money or safety -- but the vocabulary keeps `info` so a future purely-reporting
#: event (a placed entry, say) does not have to choose between over-severe and a second enum.
INFO = "info"
WARN = "warn"

#: The notification payload formats. `plain` is the generic-JSON default; `slack` is the one
#: chat-platform format (#444 asks for at least one).
VALID_FORMATS = ("plain", "slack")


@dataclass(frozen=True)
class EventSpec:
    """One event KEY's declaration -- the part the settings opt into and the payload carries.

    `key` is the stable dotted identifier (the same style as the telemetry event names --
    `reconcile.position_unprotected`), `category` is what an operator routes on
    (attestation/rail/execution/allowance/data), `severity` is how urgent it is.
    """

    key: str
    category: str
    severity: str


#: THE TAXONOMY (#444's five currently-silent events). Where each one's threshold comes from:
#:
#: * `attestation.expiring` -- doctor's `attest.withdrawals` WARN (<=2 of the 7 TTL days
#:   remain) or FAIL (expired or never attested). Rail 17 fails CLOSED: an expired
#:   attestation silently vetoes every entry, which is exactly the loop this event closes.
#: * `rail.armed` -- doctor's `rail.streak_halt` HALTED / `rail.drawdown` FAIL. The kill
#:   switch is deliberately NOT here: it is engaged by an operator at a TTY, who knows.
#: * `setup.unplaced` -- a cycle that detected an entry setup and could not place it.
#: * `allowance.nearing_exhaustion` -- month-to-date BUY spend nearing the in-force rail-14
#:   allowance (see `keel.notifications.ALLOWANCE_NEARING_USED_PCT`).
#: * `feed.stale_open_position` -- staleness on a product with an open position, where the
#:   exits ride on data that has stopped arriving.
EVENTS: tuple[EventSpec, ...] = (
    EventSpec("attestation.expiring", "attestation", WARN),
    EventSpec("rail.armed", "rail", WARN),
    EventSpec("setup.unplaced", "execution", WARN),
    EventSpec("allowance.nearing_exhaustion", "allowance", WARN),
    EventSpec("feed.stale_open_position", "data", WARN),
)

EVENTS_BY_KEY: dict[str, EventSpec] = {spec.key: spec for spec in EVENTS}


@dataclass(frozen=True)
class NotificationEvent:
    """One derived event occurrence: its taxonomy key plus the human message and the
    structured numbers that justify it (`fields`, e.g. `days_remaining=2`, `pct_used=85`)."""

    key: str
    category: str
    severity: str
    message: str
    fields: Mapping[str, Any] = field(default_factory=dict)


def notification_event(key: str, message: str, **fields: Any) -> NotificationEvent:
    """Build a `NotificationEvent` for a taxonomy key, refusing keys outside it.

    The taxonomy is closed on purpose: settings opt in by key, payloads carry the key, and a
    caller minting its own key would opt into an event nobody declared -- the drift the
    `KeyError` exists to prevent.
    """
    spec = EVENTS_BY_KEY.get(key)
    if spec is None:
        raise KeyError(f"notifications: {key!r} is not in the event taxonomy")
    return NotificationEvent(
        key=spec.key,
        category=spec.category,
        severity=spec.severity,
        message=message,
        fields=dict(fields),
    )


@dataclass(frozen=True)
class NotificationSettings:
    """Per-event opt-in (Freqtrade's `notification_settings` shape) plus the payload format.

    `events` holds every key opted in (`true` in the YAML); anything else -- absent, `false`,
    or a key the taxonomy does not know -- is simply not opted in. Unknown keys are ignored
    rather than refused: a settings block naming a retired event opts into nothing, exactly
    like an absent one.
    """

    events: frozenset[str] = frozenset()
    format: str = "plain"

    def opted_in(self, key: str) -> bool:
        return key in self.events


#: The delivery seam: POST `body` to `url`. Injectable so tests (and only tests) can record
#: instead of reaching the network.
Transport = Callable[[str, bytes], None]


def post_json(url: str, body: bytes, timeout: float = DEFAULT_TIMEOUT_SEC) -> None:
    """The one real transport -- the same POST `WebhookAlertHandler` makes, on the same
    deliberately short timeout: this runs at the tail of an agent cycle, so a hung endpoint
    must not stall the next one."""
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout):
        pass


def format_plain(event: NotificationEvent) -> dict[str, Any]:
    """The default payload: a flat JSON object with the event key, severity, category,
    message, and the event's structured fields beside them."""
    payload: dict[str, Any] = {
        "event": event.key,
        "severity": event.severity,
        "category": event.category,
        "message": event.message,
    }
    payload.update(event.fields)
    return payload


def format_slack(event: NotificationEvent) -> dict[str, Any]:
    """The chat payload: Slack-compatible `{"text": ...}` (Mattermost native, Discord via its
    `/slack` endpoint). One key, one human line: the event key, the message, and the numbers
    inline so a phone notification carries the whole fact."""
    text = f"[keel] {event.severity.upper()}: {event.key} -- {event.message}"
    if event.fields:
        detail = "; ".join(f"{name}={value}" for name, value in event.fields.items())
        text += f" ({detail})"
    return {"text": text}


def format_event(event: NotificationEvent, fmt: str) -> dict[str, Any]:
    if fmt == "plain":
        return format_plain(event)
    if fmt == "slack":
        return format_slack(event)
    raise ValueError(f"notifications: unknown format {fmt!r}; must be one of {VALID_FORMATS!r}")


_logger = logging.getLogger(__name__)


def send_event(
    url: str | None,
    event: NotificationEvent,
    settings: NotificationSettings,
    *,
    transport: Transport | None = None,
) -> bool:
    """Deliver `event` if (and only if) it is opted in AND a URL is configured.

    Returns whether a delivery succeeded. Three ways to return `False` without touching the
    network -- not opted in, no URL, or the POST failed -- and NONE of them raises: the
    caller is the trading loop, and a notification failure must never break a cycle. An
    opted-in event with a configured URL still makes exactly one attempt (no retries): these
    are early warnings, and the next cycle re-derives the event if the state persists.
    """
    if not settings.opted_in(event.key) or not url:
        return False
    try:
        # `default=str` is the delivery guarantee, not decoration: an event field of any
        # type the bridge layer can build (a Decimal, say) must serialize, or the plain
        # format would silently swallow a TypeError here and the operator would get
        # nothing -- the failure mode is invisible from the trading loop.
        payload = json.dumps(format_event(event, settings.format), default=str).encode("utf-8")
        (transport or post_json)(url, payload)
        return True
    except Exception:
        # Deliberately broad: see the module docstring. Logged rather than `handleError`
        # because this is not a logging.Handler, so a WARNING cannot re-enter it.
        log_event(_logger, logging.WARNING, "notification.send_failed", event=event.key)
        return False


__all__ = [
    "EVENTS",
    "EVENTS_BY_KEY",
    "EventSpec",
    "INFO",
    "NotificationEvent",
    "NotificationSettings",
    "Transport",
    "VALID_FORMATS",
    "WARN",
    "format_event",
    "format_plain",
    "format_slack",
    "notification_event",
    "post_json",
    "send_event",
]
