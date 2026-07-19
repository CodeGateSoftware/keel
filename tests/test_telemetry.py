from __future__ import annotations

import json
import logging

from keel_core import telemetry


def _capture(caplog, fn) -> dict:
    """Run `fn`, formatting the single emitted record through JsonFormatter."""
    formatter = telemetry.JsonFormatter()
    with caplog.at_level(logging.INFO, logger="keel.test"):
        fn(logging.getLogger("keel.test"))
    assert len(caplog.records) == 1
    return json.loads(formatter.format(caplog.records[0]))


def test_log_event_emits_stable_fields(caplog) -> None:
    payload = _capture(
        caplog,
        lambda log: telemetry.log_event(
            log, logging.INFO, "agent.cycle_start", product="BTC-USD", venue="coinbase"
        ),
    )
    assert payload["event"] == "agent.cycle_start"
    assert payload["product"] == "BTC-USD"
    assert payload["venue"] == "coinbase"
    assert payload["level"] == "INFO"
    assert "ts" in payload


def test_cycle_id_is_attached_when_bound(caplog) -> None:
    telemetry.bind_cycle("cycle-abc")
    try:
        payload = _capture(
            caplog, lambda log: telemetry.log_event(log, logging.INFO, "agent.cycle_start")
        )
        assert payload["cycle_id"] == "cycle-abc"
    finally:
        telemetry.bind_cycle(None)


def test_cycle_id_absent_when_unbound(caplog) -> None:
    telemetry.bind_cycle(None)
    payload = _capture(
        caplog, lambda log: telemetry.log_event(log, logging.INFO, "agent.cycle_start")
    )
    assert payload.get("cycle_id") is None


def test_output_is_one_json_object_per_line(caplog) -> None:
    payload = _capture(
        caplog, lambda log: telemetry.log_event(log, logging.ERROR, "executor.order_rejected")
    )
    assert "\n" not in json.dumps(payload)


def test_new_cycle_id_is_unique() -> None:
    assert telemetry.new_cycle_id() != telemetry.new_cycle_id()
