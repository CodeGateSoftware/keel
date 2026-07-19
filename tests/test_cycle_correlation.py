"""Every event emitted inside one `agent.run_once` cycle shares a `cycle_id`."""

from __future__ import annotations

import json
import logging

from keel_core import telemetry


def test_cycle_id_is_stable_within_a_bound_cycle(caplog) -> None:
    formatter = telemetry.JsonFormatter()
    cycle = telemetry.new_cycle_id()
    telemetry.bind_cycle(cycle)
    try:
        with caplog.at_level(logging.INFO, logger="keel.test"):
            log = logging.getLogger("keel.test")
            telemetry.log_event(log, logging.INFO, "agent.cycle_start")
            telemetry.log_event(log, logging.INFO, "agent.cycle_end")
        ids = {json.loads(formatter.format(r))["cycle_id"] for r in caplog.records}
        assert ids == {cycle}
    finally:
        telemetry.bind_cycle(None)


def test_cycle_ids_differ_across_cycles() -> None:
    first = telemetry.new_cycle_id()
    second = telemetry.new_cycle_id()
    assert first != second
