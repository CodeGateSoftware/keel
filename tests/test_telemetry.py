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


# -- exception path (`log_exception` / `exc_info`) -------------------------------------------


def _capture_exception(caplog, fn) -> dict:
    """Like `_capture`, but formats the record while the exception is still live and captures
    at ERROR level -- `log_exception` always logs at ERROR."""
    formatter = telemetry.JsonFormatter()
    with caplog.at_level(logging.ERROR, logger="keel.test"):
        fn(logging.getLogger("keel.test"))
    assert len(caplog.records) == 1
    return json.loads(formatter.format(caplog.records[0]))


def test_log_exception_populates_exc_with_traceback_text(caplog) -> None:
    def fn(log: logging.Logger) -> None:
        try:
            raise ValueError("boom")
        except ValueError:
            telemetry.log_exception(log, "executor.place_failed", product="BTC-USD")

    payload = _capture_exception(caplog, fn)
    assert payload["event"] == "executor.place_failed"
    assert payload["level"] == "ERROR"
    assert payload["product"] == "BTC-USD"
    assert "exc" in payload
    assert "ValueError: boom" in payload["exc"]
    assert "Traceback (most recent call last)" in payload["exc"]


def test_log_exception_payload_is_one_line_despite_traceback_newlines(caplog) -> None:
    def fn(log: logging.Logger) -> None:
        try:
            raise ValueError("boom")
        except ValueError:
            telemetry.log_exception(log, "executor.place_failed")

    payload = _capture_exception(caplog, fn)
    # `formatException` output is guaranteed to contain newlines (frame + traceback lines);
    # confirm that's actually true of this payload, not a vacuously-passing assertion.
    assert "\n" in payload["exc"]
    # ... but once serialised into the single-line JSON payload, those newlines must be
    # JSON-escaped (`\n`), not raw -- `JsonFormatter`'s whole contract is one object per line.
    serialised = json.dumps(payload)
    assert "\n" not in serialised


def test_log_exception_field_named_exc_is_renamed_and_does_not_overwrite_traceback(
    caplog,
) -> None:
    def fn(log: logging.Logger) -> None:
        try:
            raise ValueError("boom")
        except ValueError:
            telemetry.log_exception(log, "executor.place_failed", exc="not-a-traceback")

    payload = _capture_exception(caplog, fn)
    # The real traceback still lands under the reserved `exc` key.
    assert "ValueError: boom" in payload["exc"]
    # The caller's colliding `exc` field is preserved, renamed, not dropped.
    assert payload["field_exc"] == "not-a-traceback"


def test_unbind_cycle_restores_the_outer_id_rather_than_clearing_it() -> None:
    """A nested cycle must not clobber the trace it was nested inside.

    `bind_cycle` used to `set()` without keeping the token, and `run_once` unwound with
    `bind_cycle(None)`. That is indistinguishable from this test's arrangement while nothing
    nests -- but the moment an ingest or LLM span wraps a cycle, clearing to `None` on the way
    out silently drops the outer correlation id and every subsequent event goes uncorrelated.
    Under the old behaviour the `== "outer"` assertion below fails with `None`.
    """
    outer = telemetry.bind_cycle("outer")
    try:
        inner = telemetry.bind_cycle("inner")
        assert telemetry.current_cycle() == "inner"

        telemetry.unbind_cycle(inner)
        assert telemetry.current_cycle() == "outer"
    finally:
        telemetry.unbind_cycle(outer)
    assert telemetry.current_cycle() is None


def test_run_once_style_unwind_preserves_an_enclosing_trace(caplog) -> None:
    """The same property observed through the emitted payload, not just the ContextVar."""
    outer = telemetry.bind_cycle("outer-trace")
    try:
        inner = telemetry.bind_cycle(telemetry.new_cycle_id())
        telemetry.unbind_cycle(inner)

        payload = _capture(
            caplog, lambda log: telemetry.log_event(log, logging.INFO, "ingest.batch_done")
        )
        assert payload["cycle_id"] == "outer-trace"
    finally:
        telemetry.unbind_cycle(outer)


def test_bound_venue_is_attached_to_every_event(caplog) -> None:
    """Spec 10.2 names `venue` a stable field. It is bound once, not passed per call site."""
    token = telemetry.bind_venue("coinbase")
    try:
        payload = _capture(
            caplog, lambda log: telemetry.log_event(log, logging.INFO, "agent.cycle_start")
        )
        assert payload["venue"] == "coinbase"
    finally:
        telemetry.unbind_venue(token)


def test_venue_absent_when_unbound(caplog) -> None:
    payload = _capture(
        caplog, lambda log: telemetry.log_event(log, logging.INFO, "agent.cycle_start")
    )
    assert "venue" not in payload


def test_an_explicit_venue_overrides_the_bound_one(caplog) -> None:
    """`venue` is an ambient DEFAULT, not a computed field.

    `subscription.attestation_overdue` reports on a specific venue's record, which need not be
    the venue the process is driving -- so a call site that names one must win. Contrast
    `cycle_id` below, which is computed and must not be forgeable.
    """
    token = telemetry.bind_venue("coinbase")
    try:
        payload = _capture(
            caplog,
            lambda log: telemetry.log_event(
                log, logging.WARNING, "subscription.attestation_overdue", venue="kraken"
            ),
        )
        assert payload["venue"] == "kraken"
    finally:
        telemetry.unbind_venue(token)


def test_an_explicit_cycle_id_is_still_renamed_not_honoured(caplog) -> None:
    """The contrast that makes the venue rule deliberate rather than an oversight."""
    token = telemetry.bind_cycle("real-cycle")
    try:
        payload = _capture(
            caplog,
            lambda log: telemetry.log_event(
                log, logging.INFO, "agent.cycle_start", cycle_id="forged"
            ),
        )
        assert payload["cycle_id"] == "real-cycle"
        assert payload["field_cycle_id"] == "forged"
    finally:
        telemetry.unbind_cycle(token)


def test_unbind_venue_restores_the_outer_venue() -> None:
    outer = telemetry.bind_venue("coinbase")
    try:
        inner = telemetry.bind_venue("kraken")
        assert telemetry.current_venue() == "kraken"
        telemetry.unbind_venue(inner)
        assert telemetry.current_venue() == "coinbase"
    finally:
        telemetry.unbind_venue(outer)
    assert telemetry.current_venue() is None
