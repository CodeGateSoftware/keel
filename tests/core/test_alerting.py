"""Tests for off-machine CRITICAL delivery.

keel escalates "this position has no stop, act now" by logging CRITICAL. Before this handler
those lines reached a rotating file on one machine and `osascript` on that machine's screen, so
the escalation was correctly designed and delivered nowhere -- which, with one cycle per UTC day
and no flatten command, is the difference between a bad trade and a day-long unhedged position.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest
from keel_core.alerting import (
    ALERT_WEBHOOK_ENV,
    WebhookAlertHandler,
    install_alerting,
    resolve_webhook_url,
)


class _Sink:
    """Stands in for `urllib.request.urlopen`, recording what would have been POSTed."""

    def __init__(self, explode: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.explode = explode

    def __call__(self, request: Any, timeout: float | None = None) -> Any:
        self.calls.append(
            {
                "url": request.full_url,
                "body": request.data.decode("utf-8"),
                "headers": dict(request.headers),
                "timeout": timeout,
            }
        )
        if self.explode:
            raise OSError("endpoint unreachable")

        class _Response:
            def __enter__(self_inner) -> Any:
                return self_inner

            def __exit__(self_inner, *exc: object) -> bool:
                return False

        return _Response()


@pytest.fixture
def logger() -> logging.Logger:
    log = logging.getLogger("keel.test.alerting")
    log.handlers.clear()
    log.setLevel(logging.INFO)
    log.propagate = False
    return log


def test_a_critical_escalation_is_posted_off_the_machine(logger, monkeypatch):
    """The whole point: `reconcile.position_unprotected` has to leave the box."""
    sink = _Sink()
    monkeypatch.setattr("urllib.request.urlopen", sink)
    install_alerting(logger, "https://alerts.example/keel")

    from keel_core.telemetry import log_event

    log_event(
        logger,
        logging.CRITICAL,
        "reconcile.position_unprotected",
        product="BTC-USD",
        held_qty="0.01",
    )

    assert len(sink.calls) == 1
    assert sink.calls[0]["url"] == "https://alerts.example/keel"
    assert sink.calls[0]["headers"]["Content-type"] == "application/json"


def test_the_payload_carries_the_event_and_its_structured_fields(logger, monkeypatch):
    """Delivered as the SAME JSON object the log file gets, so an alert and its log line can be
    matched without a second serialisation format to keep in sync."""
    sink = _Sink()
    monkeypatch.setattr("urllib.request.urlopen", sink)
    install_alerting(logger, "https://alerts.example/keel")

    from keel_core.telemetry import log_event

    log_event(
        logger,
        logging.CRITICAL,
        "reconcile.position_unprotected",
        product="BTC-USD",
        held_qty="0.01",
    )

    payload = json.loads(sink.calls[0]["body"])
    assert payload["event"] == "reconcile.position_unprotected"
    assert payload["level"] == "CRITICAL"
    assert payload["product"] == "BTC-USD"
    assert payload["held_qty"] == "0.01"


def test_non_critical_records_are_not_alerted(logger, monkeypatch):
    """`executor.bracket_not_placed` is a WARNING and fires on ordinary refusals. Paging on
    everything trains the alert to be ignored, which is how a real CRITICAL gets missed."""
    sink = _Sink()
    monkeypatch.setattr("urllib.request.urlopen", sink)
    install_alerting(logger, "https://alerts.example/keel")

    logger.warning("executor.bracket_not_placed")
    logger.error("something bad")

    assert sink.calls == []


def test_an_unreachable_endpoint_never_raises_into_the_caller(logger, monkeypatch):
    """This handler is the one in the process most likely to fail -- it reaches the network. A
    logging call must never take down the trade loop, least of all the call reporting that a
    position is unprotected."""
    sink = _Sink(explode=True)
    monkeypatch.setattr("urllib.request.urlopen", sink)
    monkeypatch.setattr(logging, "raiseExceptions", False)
    install_alerting(logger, "https://alerts.example/keel")

    logger.critical("reconcile.position_unprotected")  # must not raise

    assert len(sink.calls) == 1


def test_no_url_attaches_no_handler_at_all(logger):
    """Offline-first: an install that never configured alerting makes no network call, and has
    no handler that could accidentally start making one."""
    assert install_alerting(logger, None) is None
    assert not any(isinstance(h, WebhookAlertHandler) for h in logger.handlers)


def test_installing_twice_does_not_double_send(logger, monkeypatch):
    """`configure_logging` runs once per CLI invocation; accumulating handlers would send one
    copy of every alert per call."""
    sink = _Sink()
    monkeypatch.setattr("urllib.request.urlopen", sink)
    install_alerting(logger, "https://alerts.example/keel")
    install_alerting(logger, "https://alerts.example/keel")

    logger.critical("reconcile.position_unprotected")

    assert len(sink.calls) == 1


def test_the_url_comes_from_the_environment_before_the_dotenv(tmp_path, monkeypatch):
    """A one-off run can redirect alerts without editing a file the deployment shares."""
    env_file = tmp_path / ".env"
    env_file.write_text(f"{ALERT_WEBHOOK_ENV}=https://from-file.example\n")
    monkeypatch.setenv(ALERT_WEBHOOK_ENV, "https://from-environ.example")

    assert resolve_webhook_url(env_file) == "https://from-environ.example"


def test_the_url_falls_back_to_the_dotenv(tmp_path, monkeypatch):
    """It is closer to a credential than to configuration, and `config.yaml` is committed."""
    env_file = tmp_path / ".env"
    env_file.write_text(f"{ALERT_WEBHOOK_ENV}=https://from-file.example\n")
    monkeypatch.delenv(ALERT_WEBHOOK_ENV, raising=False)

    assert resolve_webhook_url(env_file) == "https://from-file.example"


def test_a_missing_dotenv_means_alerting_is_simply_off(monkeypatch):
    monkeypatch.delenv(ALERT_WEBHOOK_ENV, raising=False)

    assert resolve_webhook_url(Path("does-not-exist.env")) is None
