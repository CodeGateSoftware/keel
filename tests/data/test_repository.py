"""Tests for keel.data.repository.Repository."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.types import Candle, Granularity


@pytest.fixture
def repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    return Repository(conn)


# -- transactions ------------------------------------------------------------


def _tx(coinbase_id: str = "tx-1", **overrides: Any) -> dict[str, Any]:
    base = dict(
        coinbase_id=coinbase_id,
        source="csv_import",
        type="buy",
        asset="BTC",
        ts=1_700_000_000,
        qty=Decimal("1.5"),
        price=Decimal("50000.123456789"),
        subtotal=Decimal("75000.185185184"),
        total=Decimal("75010.185185184"),
        fees=Decimal("10.00"),
        notes="test",
        rule_id=None,
        order_id=None,
    )
    base.update(overrides)
    return base


def test_upsert_transaction_round_trips_decimal_exactly(repo):
    repo.upsert_transaction(_tx())

    rows = repo.get_transactions()
    assert len(rows) == 1
    row = rows[0]
    assert row["coinbase_id"] == "tx-1"
    assert row["qty"] == Decimal("1.5")
    assert row["price"] == Decimal("50000.123456789")
    assert row["subtotal"] == Decimal("75000.185185184")
    assert row["total"] == Decimal("75010.185185184")
    assert row["fees"] == Decimal("10.00")


def test_upsert_transaction_dedupes_on_coinbase_id(repo):
    repo.upsert_transaction(_tx(notes="first"))
    repo.upsert_transaction(_tx(notes="second"))

    rows = repo.get_transactions()
    assert len(rows) == 1
    assert rows[0]["notes"] == "second"


def test_get_transactions_filters_by_asset(repo):
    repo.upsert_transaction(_tx(coinbase_id="tx-1", asset="BTC"))
    repo.upsert_transaction(_tx(coinbase_id="tx-2", asset="ETH"))

    btc_rows = repo.get_transactions(asset="BTC")
    assert len(btc_rows) == 1
    assert btc_rows[0]["asset"] == "BTC"

    all_rows = repo.get_transactions()
    assert len(all_rows) == 2


# -- candles -------------------------------------------------------------


def _candle(ts: int, price: str = "100") -> Candle:
    p = Decimal(price)
    return Candle(
        ts=ts,
        open=p,
        high=p + Decimal("1"),
        low=p - Decimal("1"),
        close=p,
        volume=Decimal("12.345678901234"),
    )


def test_upsert_candles_round_trips_decimal_exactly(repo):
    candles = [_candle(1_700_000_000, "50000.123456789")]

    written = repo.upsert_candles("BTC-USD", Granularity.ONE_HOUR, candles)

    assert written == 1
    result = repo.get_candles("BTC-USD", Granularity.ONE_HOUR)
    assert result == candles


def test_upsert_candles_dedupes_on_primary_key(repo):
    repo.upsert_candles("BTC-USD", Granularity.ONE_HOUR, [_candle(1_700_000_000, "100")])
    repo.upsert_candles("BTC-USD", Granularity.ONE_HOUR, [_candle(1_700_000_000, "200")])

    result = repo.get_candles("BTC-USD", Granularity.ONE_HOUR)
    assert len(result) == 1
    assert result[0].open == Decimal("200")


def test_get_candles_ordered_by_ts_and_filtered_by_range(repo):
    candles = [_candle(ts) for ts in (3_000, 1_000, 2_000)]
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, candles)

    result = repo.get_candles("BTC-USD", Granularity.ONE_DAY)
    assert [c.ts for c in result] == [1_000, 2_000, 3_000]

    ranged = repo.get_candles("BTC-USD", Granularity.ONE_DAY, start_ts=1_500, end_ts=2_500)
    assert [c.ts for c in ranged] == [2_000]


def test_get_candles_distinguishes_granularity_and_product(repo):
    repo.upsert_candles("BTC-USD", Granularity.ONE_HOUR, [_candle(1_000)])
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, [_candle(1_000)])
    repo.upsert_candles("ETH-USD", Granularity.ONE_HOUR, [_candle(1_000)])

    btc_hourly = repo.get_candles("BTC-USD", Granularity.ONE_HOUR)
    btc_daily = repo.get_candles("BTC-USD", Granularity.ONE_DAY)
    eth_hourly = repo.get_candles("ETH-USD", Granularity.ONE_HOUR)
    assert len(btc_hourly) == 1
    assert len(btc_daily) == 1
    assert len(eth_hourly) == 1


# -- orders ----------------------------------------------------------------


def _order(**overrides: Any) -> dict[str, Any]:
    base = dict(
        mode="paper",
        product_id="BTC-USD",
        side="BUY",
        order_type="limit",
        qty=Decimal("0.5"),
        limit_price=Decimal("49999.99"),
        status="pending",
        fee=Decimal("0.10"),
        expected_fill=Decimal("49999.99"),
        actual_fill=None,
        raw_response=None,
        confirmation="auto",
        rule_id=None,
        created_at=1_700_000_000,
        updated_at=1_700_000_000,
    )
    base.update(overrides)
    return base


def test_insert_order_returns_id_and_round_trips_decimal(repo):
    order_id = repo.insert_order(_order())

    assert isinstance(order_id, int)
    stored = repo.get_order(order_id)
    assert stored["qty"] == Decimal("0.5")
    assert stored["limit_price"] == Decimal("49999.99")
    assert stored["fee"] == Decimal("0.10")
    assert stored["status"] == "pending"
    assert stored["actual_fill"] is None


def test_insert_order_ids_increment(repo):
    id1 = repo.insert_order(_order())
    id2 = repo.insert_order(_order())

    assert id2 > id1


def test_update_order_updates_fields_and_round_trips_decimal(repo):
    order_id = repo.insert_order(_order())

    repo.update_order(order_id, status="filled", actual_fill=Decimal("50000.010101"))

    stored = repo.get_order(order_id)
    assert stored["status"] == "filled"
    assert stored["actual_fill"] == Decimal("50000.010101")
    # untouched fields survive the partial update
    assert stored["qty"] == Decimal("0.5")


# -- agent_state -------------------------------------------------------------


def test_get_state_returns_default_when_missing(repo):
    assert repo.get_state("kill_switch", default=False) is False
    assert repo.get_state("missing_key") is None


def test_set_state_and_get_state_round_trip_various_types(repo):
    repo.set_state("kill_switch", True)
    repo.set_state("auto_trade_mode", "paper")
    repo.set_state("daily_spend_usd", Decimal("123.456789"))
    repo.set_state("weekly_accumulators", {"BTC": "10.5", "ETH": "5.25"})

    assert repo.get_state("kill_switch") is True
    assert repo.get_state("auto_trade_mode") == "paper"
    assert repo.get_state("daily_spend_usd") == Decimal("123.456789")
    assert repo.get_state("weekly_accumulators") == {"BTC": "10.5", "ETH": "5.25"}


def test_set_state_overwrites_existing_key(repo):
    repo.set_state("kill_switch", False)
    repo.set_state("kill_switch", True)

    assert repo.get_state("kill_switch") is True


# -- bypass arm token (Issue #60, in-process bypass hardening) ---------------


def test_is_bypass_armed_false_when_never_armed(repo):
    assert repo.is_bypass_armed(now_ts=1_700_000_000) is False


def test_is_bypass_armed_true_right_after_arm_within_ttl(repo):
    repo.arm_bypass(now_ts=1_700_000_000, ttl_sec=3600)

    assert repo.is_bypass_armed(now_ts=1_700_000_001) is True
    assert repo.is_bypass_armed(now_ts=1_700_003_599) is True


def test_is_bypass_armed_false_once_now_ts_reaches_armed_until(repo):
    repo.arm_bypass(now_ts=1_700_000_000, ttl_sec=3600)

    # armed_until = 1_700_000_000 + 3600 = 1_700_003_600 -- freshness is a strict `<`.
    assert repo.is_bypass_armed(now_ts=1_700_003_600) is False
    assert repo.is_bypass_armed(now_ts=1_700_004_000) is False


def test_is_bypass_armed_false_after_disarm(repo):
    repo.arm_bypass(now_ts=1_700_000_000, ttl_sec=3600)
    assert repo.is_bypass_armed(now_ts=1_700_000_001) is True

    repo.disarm_bypass()

    assert repo.is_bypass_armed(now_ts=1_700_000_001) is False


def test_arm_bypass_overwrites_a_previous_arm(repo):
    repo.arm_bypass(now_ts=1_700_000_000, ttl_sec=10)
    repo.arm_bypass(now_ts=1_700_000_100, ttl_sec=3600)

    # the earlier, shorter-lived arm is gone; the new one governs.
    assert repo.is_bypass_armed(now_ts=1_700_000_015) is True
    assert repo.is_bypass_armed(now_ts=1_700_003_699) is True
    assert repo.is_bypass_armed(now_ts=1_700_003_700) is False


def test_disarm_bypass_is_a_no_op_when_never_armed(repo):
    repo.disarm_bypass()  # must not raise

    assert repo.is_bypass_armed(now_ts=1_700_000_000) is False


# -- rules -------------------------------------------------------------------


def test_insert_rule_returns_id_and_defaults_status_to_candidate(repo):
    rule_id = repo.insert_rule("pullback_continuation", {"lookback": 20})

    assert isinstance(rule_id, int)
    rows = repo.get_rules()
    assert len(rows) == 1
    assert rows[0]["id"] == rule_id
    assert rows[0]["kind"] == "pullback_continuation"
    assert rows[0]["params"] == {"lookback": 20}
    assert rows[0]["status"] == "candidate"
    assert rows[0]["created_at"] is not None


def test_insert_rule_accepts_explicit_status(repo):
    rule_id = repo.insert_rule("dca", {"budget_usd": 50}, status="live")

    row = repo.get_rules()[0]
    assert row["id"] == rule_id
    assert row["status"] == "live"


def test_insert_rule_ids_increment(repo):
    id1 = repo.insert_rule("rsi_meanrev", {})
    id2 = repo.insert_rule("rsi_meanrev", {})

    assert id2 > id1


def test_insert_rule_honors_explicit_now_ts(repo):
    rule_id = repo.insert_rule("dca", {}, now_ts=1_700_000_000)

    row = {r["id"]: r for r in repo.get_rules()}[rule_id]
    assert row["created_at"] == 1_700_000_000


def test_get_rules_filters_by_status(repo):
    repo.insert_rule("a", {}, status="candidate")
    repo.insert_rule("b", {}, status="live")
    repo.insert_rule("c", {}, status="live")

    live = repo.get_rules(status="live")
    assert {r["kind"] for r in live} == {"b", "c"}

    all_rules = repo.get_rules()
    assert len(all_rules) == 3


def test_update_rule_status_updates_status_and_promoted_at(repo):
    rule_id = repo.insert_rule("pullback_continuation", {}, status="candidate")

    repo.update_rule_status(rule_id, "paper")

    row = repo.get_rules()[0]
    assert row["status"] == "paper"
    assert row["promoted_at"] is not None
    assert row["demoted_at"] is None


def test_update_rule_status_to_disabled_sets_demoted_at(repo):
    rule_id = repo.insert_rule("pullback_continuation", {}, status="live")

    repo.update_rule_status(rule_id, "disabled")

    row = repo.get_rules()[0]
    assert row["status"] == "disabled"
    assert row["demoted_at"] is not None


# -- orders (get_orders) ------------------------------------------------------


def test_get_orders_filters_by_mode_product_and_status(repo):
    repo.insert_order(_order(mode="paper", product_id="BTC-USD", status="filled"))
    repo.insert_order(_order(mode="paper", product_id="ETH-USD", status="pending"))
    repo.insert_order(_order(mode="live", product_id="BTC-USD", status="filled"))

    paper_orders = repo.get_orders(mode="paper")
    assert len(paper_orders) == 2
    assert all(o["mode"] == "paper" for o in paper_orders)

    btc_paper = repo.get_orders(mode="paper", product_id="BTC-USD")
    assert len(btc_paper) == 1
    assert btc_paper[0]["product_id"] == "BTC-USD"

    filled = repo.get_orders(status="filled")
    assert len(filled) == 2

    everything = repo.get_orders()
    assert len(everything) == 3


def test_get_orders_ordered_by_id_and_decodes_decimal(repo):
    id1 = repo.insert_order(_order())
    id2 = repo.insert_order(_order())

    rows = repo.get_orders()
    assert [r["id"] for r in rows] == [id1, id2]
    assert rows[0]["qty"] == Decimal("0.5")


# -- signals -------------------------------------------------------------------


def _signal(**overrides: Any) -> dict[str, Any]:
    base = dict(
        rule_id=None,
        product_id="BTC-USD",
        ts=1_700_000_000,
        indicators='{"cts_factors": []}',
        cts_score="5",
        fired=1,
    )
    base.update(overrides)
    return base


def test_insert_signal_returns_id_and_round_trips(repo):
    signal_id = repo.insert_signal(_signal())

    assert isinstance(signal_id, int)
    row = repo._conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
    assert row["product_id"] == "BTC-USD"
    assert row["ts"] == 1_700_000_000
    assert row["cts_score"] == "5"
    assert row["fired"] == 1


def test_insert_signal_ids_increment(repo):
    id1 = repo.insert_signal(_signal())
    id2 = repo.insert_signal(_signal())

    assert id2 > id1
