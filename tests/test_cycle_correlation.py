"""Every event emitted inside one `agent.run_once` cycle shares a `cycle_id`."""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any

import pytest
from keel_core import telemetry

from keel.agent import run_once
from keel.config import (
    AutoTradeConfig,
    Caps,
    Config,
    DcaConfig,
    MarketDataConfig,
    MoneyMgmtConfig,
)
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.types import Granularity

PRODUCT = "BTC-USD"


def _config() -> Config:
    return Config(
        allowlist=["BTC"],
        target_weights={},
        risk_pct=Decimal("0.01"),
        caps=Caps(
            max_per_order_usd=Decimal("100000"),
            max_per_day_usd=Decimal("300000"),
            max_exposure_usd=Decimal("1000000"),
            max_per_asset_pct=Decimal("1"),
        ),
        market_data=MarketDataConfig(granularities=[Granularity.ONE_DAY], history_days=365),
        auto_trade=AutoTradeConfig(mode="confirm", interval_sec=50_000),
        money_mgmt=MoneyMgmtConfig(),
        dca=DcaConfig(budget_usd=Decimal("50"), cadence_days=7),
    )


def _repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    r = Repository(conn)
    r.set_state("kill_switch", False)
    return r


class _RaisingBroker:
    """A broker whose `get_candles` always raises -- used to drive `run_once` down its
    exception path (via `market_feed.poll_once`, which does not catch broker errors)."""

    def get_candles(
        self, product_id: str, granularity: Granularity, start: int, end: int
    ) -> list[Any]:
        raise RuntimeError("boom")


def test_run_once_unbinds_cycle_id_after_a_normal_return() -> None:
    """`run_once` binds a fresh `cycle_id` as its first statement (agent.py:303) but nothing
    unbinds it -- so after a normal-return cycle with no live rules (the quiet no-op path),
    `telemetry.current_cycle()` must go back to `None`, not leak the just-finished cycle id."""
    repo = _repo()
    broker = _RaisingBroker()  # never called: no live rules means poll_once has nothing to poll

    result = run_once(broker, repo, _config(), now_ts=90_000)

    assert result.skipped is False
    assert telemetry.current_cycle() is None


def test_run_once_unbinds_cycle_id_even_when_the_cycle_raises() -> None:
    """A broker error inside `market_feed.poll_once` propagates out of `run_once` -- the bound
    `cycle_id` must still be cleared on the way out, exactly like `finally: bind_cycle(None)`
    in `tests/test_cycle_correlation.py`'s own `test_cycle_id_is_stable_within_a_bound_cycle`."""
    repo = _repo()
    repo.insert_rule("dca", {"product_id": PRODUCT}, status="live")
    broker = _RaisingBroker()

    with pytest.raises(RuntimeError, match="boom"):
        run_once(broker, repo, _config(), now_ts=90_000)

    assert telemetry.current_cycle() is None


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


class _JsonCapture(logging.Handler):
    """Formats each record through `JsonFormatter` at `emit()` time -- i.e. synchronously,
    while the record is being logged -- and stores the decoded payload.

    This matters because `JsonFormatter.format` reads `telemetry.current_cycle()` (a
    `ContextVar`) at format time, not from anything stashed on the record. Formatting lazily
    after `run_once` has returned (e.g. via pytest's `caplog.records`, formatted afterward)
    would read the cycle id *after* `run_once`'s `finally: bind_cycle(None)` already cleared
    it, silently defeating this test. A real `logging.Handler.emit()` -- exactly what
    production logging does -- is the only way to observe the cycle id that was actually bound
    at the moment each event was logged.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.setFormatter(telemetry.JsonFormatter())
        self.payloads: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.payloads.append(json.loads(self.format(record)))


def test_run_once_binds_one_cycle_id_to_every_emitted_event() -> None:
    """Every event `run_once` emits during one cycle must carry the *same*, non-`None`
    `cycle_id` -- the cross-module correlation `bind_cycle(new_cycle_id())` at `agent.py:303`
    exists to deliver. `test_run_once_unbinds_cycle_id_after_a_normal_return` above only checks
    that the id is cleared afterward, which stays green even if the bind at line 303 is deleted
    (only the unconditional `finally: bind_cycle(None)` would still run). This test fails in
    that scenario: with nothing ever bound, `JsonFormatter` never writes a `cycle_id` key at
    all, so every captured payload's `cycle_id` is `None` and the `None not in cycle_ids`
    assertion below trips.
    """
    repo = _repo()
    broker = _RaisingBroker()  # never called: no live rules means poll_once has nothing to poll

    agent_logger = logging.getLogger("keel.agent")
    prior_level = agent_logger.level
    handler = _JsonCapture()
    agent_logger.setLevel(logging.INFO)
    agent_logger.addHandler(handler)
    try:
        result = run_once(broker, repo, _config(), now_ts=90_000)
    finally:
        agent_logger.removeHandler(handler)
        agent_logger.setLevel(prior_level)

    assert result.skipped is False
    # The no-live-rules cycle emits exactly agent.cycle_start / agent.feed_polled /
    # agent.mode_resolved -- see agent.py:294-420.
    assert len(handler.payloads) >= 3
    cycle_ids = {payload.get("cycle_id") for payload in handler.payloads}
    assert None not in cycle_ids
    assert len(cycle_ids) == 1
