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


def test_reserved_key_collision_is_renamed_not_dropped(caplog) -> None:
    payload = _capture(
        caplog,
        lambda log: telemetry.log_event(
            log, logging.INFO, "order.rejected", ts="not-a-timestamp", cycle_id="spoofed"
        ),
    )
    # The stable `ts` key still holds the record's real numeric timestamp.
    assert isinstance(payload["ts"], float)
    # The caller's colliding value is preserved under a `field_`-prefixed key.
    assert payload["field_ts"] == "not-a-timestamp"
    assert payload["field_cycle_id"] == "spoofed"


def test_event_level_logger_fields_do_not_raise(caplog) -> None:
    payload = _capture(
        caplog,
        lambda log: telemetry.log_event(
            log, logging.INFO, "order.rejected", event="x", level="y", logger="z"
        ),
    )
    assert payload["event"] == "order.rejected"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "keel.test"
    assert payload["field_event"] == "x"
    assert payload["field_level"] == "y"
    assert payload["field_logger"] == "z"


def test_non_serialisable_field_falls_back_to_str(caplog) -> None:
    class Weird:
        def __str__(self) -> str:
            return "weird-repr"

    payload = _capture(
        caplog,
        lambda log: telemetry.log_event(
            log, logging.INFO, "agent.cycle_start", obj=Weird()
        ),
    )
    assert payload["obj"] == "weird-repr"
