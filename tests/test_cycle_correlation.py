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
