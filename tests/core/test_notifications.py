"""Tests for the opt-in notification taxonomy, settings and delivery (#444).

The CRITICAL webhook (`keel_core.alerting`) delivers escalations nobody can miss. But the
events an operator most needs are not CRITICAL logs -- rail 17's attestation nearing expiry
fails closed SILENTLY (cycles keep running and veto every entry; a real setup was lost that
way and only surfaced when someone opened the TUI). These tests pin the layer that fixes
that: a taxonomy of named events, per-event opt-in in the Freqtrade `notification_settings`
shape (default OFF), the generic webhook as the one transport, and formatting for plain JSON
plus Slack-compatible chat payloads.

The notify-only guarantee is structural and pinned here: an event that is not opted in is
never sent, and no URL means no delivery attempt at all -- the same offline-first contract
`install_alerting` already keeps for CRITICAL records.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from keel_core.notifications import (
    EVENTS,
    EVENTS_BY_KEY,
    NotificationSettings,
    notification_event,
    send_event,
)


class _Sink:
    """Stands in for the webhook POST, recording what would have been delivered."""

    def __init__(self, explode: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.explode = explode

    def __call__(self, url: str, body: bytes) -> None:
        self.calls.append({"url": url, "body": body.decode("utf-8")})
        if self.explode:
            raise OSError("endpoint unreachable")


# -- the taxonomy -----------------------------------------------------------------------------


def test_every_taxonomy_event_declares_a_category_and_a_severity():
    """The five issue-named events exist, and every one carries the metadata an operator
    routes on (category) and prioritizes on (severity)."""
    assert {
        "attestation.expiring",
        "rail.armed",
        "setup.unplaced",
        "allowance.nearing_exhaustion",
        "feed.stale_open_position",
    } <= set(EVENTS_BY_KEY)
    for spec in EVENTS:
        assert spec.category, f"{spec.key}: empty category"
        assert spec.severity in ("info", "warn"), f"{spec.key}: severity {spec.severity!r}"


def test_building_an_event_outside_the_taxonomy_is_refused():
    """The taxonomy is closed: a caller cannot mint an event key the settings cannot name,
    or the opt-in contract (settings name keys, events carry them) would drift apart."""
    with pytest.raises(KeyError):
        notification_event("not.a.real.event", "message")


def test_unknown_keys_in_settings_are_ignored_not_errors():
    """Freqtrade's shape: per-event booleans, default off. A settings block naming a key the
    taxonomy does not know is a stale entry, not a crash -- and because the taxonomy is
    closed, no event can ever carry that key, so it opts into nothing."""
    settings = NotificationSettings(events=frozenset({"rail.armed", "not.a.real.event"}))

    assert settings.opted_in("rail.armed") is True
    assert "not.a.real.event" not in EVENTS_BY_KEY  # nothing can ever be sent for it
    assert NotificationSettings().opted_in("rail.armed") is False  # default off


# -- formatting -------------------------------------------------------------------------------


def _expiry_event():
    return notification_event(
        "attestation.expiring",
        "rail 17: withdrawal attestation due soon -- 2 day(s) remain on the 7-day TTL",
        days_remaining=2,
    )


def test_the_plain_payload_carries_the_event_key_message_and_numbers():
    """The default format is a generic JSON object: any webhook receiver can log it, and the
    numbers (days remaining here) ride along as fields, not just prose."""
    from keel_core.notifications import format_plain

    payload = format_plain(_expiry_event())

    assert payload["event"] == "attestation.expiring"
    assert payload["severity"] == "warn"
    assert payload["category"] == "attestation"
    assert "2 day(s) remain" in payload["message"]
    assert payload["days_remaining"] == 2


def test_the_chat_payload_is_slack_compatible_text():
    """One chat-platform format, Slack-compatible `{"text": ...}` -- accepted natively by
    Slack and Mattermost (and by Discord via its `/slack` endpoint), so one format covers the
    widest set of receivers without a per-platform matrix to maintain."""
    from keel_core.notifications import format_slack

    payload = format_slack(_expiry_event())

    assert set(payload) == {"text"}
    text = payload["text"]
    assert "attestation.expiring" in text
    assert "2 day(s) remain" in text
    assert "days_remaining=2" in text


# -- delivery ---------------------------------------------------------------------------------


def test_an_event_that_is_not_opted_in_is_never_sent():
    sink = _Sink()
    settings = NotificationSettings()  # nothing opted in

    assert (
        send_event("https://alerts.example/hook", _expiry_event(), settings, transport=sink)
        is False
    )

    assert sink.calls == []


def test_an_opted_in_event_is_delivered_once_formatted():
    sink = _Sink()
    settings = NotificationSettings(events=frozenset({"attestation.expiring"}))

    sent = send_event("https://alerts.example/hook", _expiry_event(), settings, transport=sink)

    assert sent is True
    assert len(sink.calls) == 1
    assert sink.calls[0]["url"] == "https://alerts.example/hook"
    body = json.loads(sink.calls[0]["body"])
    assert body["event"] == "attestation.expiring"
    assert body["days_remaining"] == 2


def test_the_slack_format_is_used_when_configured():
    sink = _Sink()
    settings = NotificationSettings(events=frozenset({"attestation.expiring"}), format="slack")

    send_event("https://alerts.example/hook", _expiry_event(), settings, transport=sink)

    assert list(json.loads(sink.calls[0]["body"])) == ["text"]


def test_a_transport_failure_never_raises_into_the_caller():
    """A notification must NEVER break a cycle: the endpoint is the component most likely to
    fail, and the caller is the trading loop. Mirrors `WebhookAlertHandler.emit`'s contract."""
    sink = _Sink(explode=True)
    settings = NotificationSettings(events=frozenset({"attestation.expiring"}))

    sent = send_event("https://alerts.example/hook", _expiry_event(), settings, transport=sink)

    assert sent is False  # delivered=False, not an exception


def test_an_unserializable_field_still_delivers_serialized_as_text():
    """The emitter's delivery guarantee: an event field of ANY type must serialize
    (`json.dumps(..., default=str)`), or the plain format would swallow a TypeError in the
    broad except and the operator would get NOTHING. This is the emitter half of the fix for
    the allowance event that carried a raw Decimal; the bridge half (real payloads of all
    five events, both formats) is pinned in `tests/test_notifications.py`."""
    from decimal import Decimal

    sink = _Sink()
    settings = NotificationSettings(events=frozenset({"allowance.nearing_exhaustion"}))
    event = notification_event(
        "allowance.nearing_exhaustion", "spend 850 of 1000", pct_used=Decimal("85.00")
    )

    sent = send_event("https://alerts.example/hook", event, settings, transport=sink)

    assert sent is True
    assert json.loads(sink.calls[0]["body"])["pct_used"] == "85.00"


def test_a_base_exception_from_the_transport_propagates():
    """`send_event` swallows `Exception`, not `BaseException`: a KeyboardInterrupt (Ctrl-C at
    the cycle tail -- an operator stopping keel) must reach the operator's terminal, not be
    quietly converted into 'delivered False'. The swallow is for delivery failures; an
    interrupt is not one."""

    class _Interrupting:
        def __call__(self, url: str, body: bytes) -> None:
            raise KeyboardInterrupt

    settings = NotificationSettings(events=frozenset({"attestation.expiring"}))

    with pytest.raises(KeyboardInterrupt):
        send_event(
            "https://alerts.example/hook", _expiry_event(), settings, transport=_Interrupting()
        )


def test_no_url_means_no_delivery_attempt_at_all():
    """Offline-first, same as `install_alerting(None)`: a deployment that never configured a
    webhook makes no network call, and has no code path that could start making one."""
    sink = _Sink()
    settings = NotificationSettings(events=frozenset({"attestation.expiring"}))

    assert send_event(None, _expiry_event(), settings, transport=sink) is False
    assert send_event("", _expiry_event(), settings, transport=sink) is False

    assert sink.calls == []
