"""Tests for keel.data.repository.Repository."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.types import Candle, Granularity, Side


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


def test_insert_order_round_trips_a_non_none_rule_id(repo):
    """`orders.rule_id` is a real FK into `rules` -- once the row exists, an order naming it
    must round-trip that id exactly, not just `None` (the `_order()` default)."""
    rule_id = repo.insert_rule("pullback_continuation", {"lookback": 20})

    order_id = repo.insert_order(_order(rule_id=rule_id))

    stored = repo.get_order(order_id)
    assert stored["rule_id"] == rule_id


def test_get_orders_also_round_trips_rule_id(repo):
    rule_id = repo.insert_rule("dca", {})
    repo.insert_order(_order(rule_id=rule_id))
    repo.insert_order(_order(rule_id=None))

    rows = repo.get_orders()
    assert {r["rule_id"] for r in rows} == {rule_id, None}


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


def test_insert_signal_round_trips_a_non_none_rule_id(repo):
    rule_id = repo.insert_rule("rsi_meanrev", {})

    signal_id = repo.insert_signal(_signal(rule_id=rule_id))

    row = repo._conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
    assert row["rule_id"] == rule_id


def test_insert_signal_ids_increment(repo):
    id1 = repo.insert_signal(_signal())
    id2 = repo.insert_signal(_signal())

    assert id2 > id1


def test_held_products_lists_products_with_filled_live_orders(repo: Repository) -> None:
    """Feeds `agent._mark_to_market_equity`'s product union: a holding whose rule is no longer
    in the live set must still be valued, or retiring a rule manufactures a phantom drawdown."""
    for product_id in ("BTC-USD", "ETH-USD"):
        repo.insert_order(
            dict(
                mode="live", product_id=product_id, side=Side.BUY.value, order_type="market",
                qty=Decimal("1"), limit_price=Decimal("100"), status="filled",
                fee=Decimal("0"), expected_fill=Decimal("100"), actual_fill=Decimal("100"),
            )
        )
    # a paper-mode order must NOT leak into the live equity calculation
    repo.insert_order(
        dict(
            mode="paper", product_id="SOL-USD", side=Side.BUY.value, order_type="market",
            qty=Decimal("1"), limit_price=Decimal("100"), status="filled",
            fee=Decimal("0"), expected_fill=Decimal("100"), actual_fill=Decimal("100"),
        )
    )
    # an unfilled order is not a holding
    repo.insert_order(
        dict(
            mode="live", product_id="DOGE-USD", side=Side.BUY.value, order_type="market",
            qty=Decimal("1"), limit_price=Decimal("100"), status="pending",
            fee=Decimal("0"), expected_fill=Decimal("100"), actual_fill=None,
        )
    )

    assert repo.held_products() == ["BTC-USD", "ETH-USD"]


# -- profile (autonomy is a durable USER CHOICE, read live) ---------------------


def test_an_absent_profile_row_reads_as_NOT_autonomous(repo):
    """Fails closed: a fresh or damaged database must never imply unattended trading."""
    assert repo.get_profile().autonomous is False


def test_set_autonomous_round_trips(repo):
    repo.set_autonomous(True, now_ts=1000)
    p = repo.get_profile()
    assert p.autonomous is True
    assert p.updated_ts == 1000

    repo.set_autonomous(False, now_ts=2000)
    p = repo.get_profile()
    assert p.autonomous is False
    assert p.updated_ts == 2000


def test_set_autonomous_upserts_and_never_creates_a_second_row(repo):
    """The table is deliberately single-row; two rows would make 'the' profile ambiguous."""
    for ts in range(1, 6):
        repo.set_autonomous(ts % 2 == 0, now_ts=ts)
    rows = repo._conn.execute("SELECT COUNT(*) AS n FROM profile").fetchone()["n"]
    assert rows == 1


def test_autonomy_can_carry_an_expiry_and_lapses_on_its_own(repo):
    """The removed bypass-arm token was TIME-LIMITED so a forgotten arm could not grant
    unattended trading forever. An unbounded profile flag loses that; an optional expiry
    restores it without forcing it on a user who wants a durable choice."""
    repo.set_autonomous(True, now_ts=1000, expires_ts=2000)
    p = repo.get_profile()
    assert p.autonomous is True
    assert p.autonomous_until == 2000

    assert p.is_autonomous(now_ts=1999) is True
    # strict now < expiry, like every other freshness check in this codebase
    assert p.is_autonomous(now_ts=2000) is False
    assert p.is_autonomous(now_ts=5000) is False


def test_autonomy_without_an_expiry_never_lapses(repo):
    repo.set_autonomous(True, now_ts=1000)
    p = repo.get_profile()
    assert p.autonomous_until is None
    assert p.is_autonomous(now_ts=10**12) is True


def test_autonomy_off_is_never_autonomous_whatever_the_expiry(repo):
    repo.set_autonomous(False, now_ts=1000, expires_ts=10**12)
    assert repo.get_profile().is_autonomous(now_ts=1001) is False


def test_profile_readable_reports_damage_that_get_profile_hides(repo):
    """`get_profile` fails closed on a damaged table, which is right for the trading path but
    indistinguishable from "never opted in". `profile_readable` is how a caller tells a human
    the stored setting is UNKNOWN rather than confidently reporting it off."""
    assert repo.profile_readable() is True

    repo._conn.execute("DROP TABLE profile")
    repo._conn.commit()

    assert repo.profile_readable() is False
    assert repo.get_profile().autonomous is False  # still fails closed, still no exception


# -- screen exceptions --------------------------------------------------------


def test_upsert_screen_exception_round_trips_through_get(repo):
    repo.upsert_screen_exception(
        asset="PAXG",
        criterion="history",
        rationale="only 441 daily bars; sector/backing/liquidity all clear",
        granted_by="tester",
        granted_at=1_800_000_000,
    )
    assert repo.get_screen_exceptions("PAXG") == {
        "history": "only 441 daily bars; sector/backing/liquidity all clear"
    }


def test_get_screen_exceptions_is_empty_for_an_asset_with_none(repo):
    assert repo.get_screen_exceptions("PAXG") == {}


def test_upsert_screen_exception_on_conflict_updates_rationale_and_grant_fields(repo):
    repo.upsert_screen_exception(
        asset="PAXG", criterion="history", rationale="first", granted_by="alice",
        granted_at=1_000,
    )
    repo.upsert_screen_exception(
        asset="PAXG", criterion="history", rationale="second", granted_by="bob",
        granted_at=2_000,
    )

    assert repo.get_screen_exceptions("PAXG") == {"history": "second"}
    (row,) = repo.list_screen_exceptions()
    assert row["granted_by"] == "bob"
    assert row["granted_at"] == 2_000


def test_list_screen_exceptions_returns_all_rows_ordered(repo):
    repo.upsert_screen_exception(
        asset="SOL", criterion="history", rationale="r2", granted_by="b", granted_at=2
    )
    repo.upsert_screen_exception(
        asset="PAXG", criterion="history", rationale="r1", granted_by="a", granted_at=1
    )

    rows = repo.list_screen_exceptions()
    assert [(r["asset"], r["criterion"]) for r in rows] == [
        ("PAXG", "history"),
        ("SOL", "history"),
    ]


def test_delete_screen_exception_removes_the_row_and_returns_rowcount_1(repo):
    repo.upsert_screen_exception(
        asset="PAXG", criterion="history", rationale="r", granted_by="a", granted_at=1
    )
    removed = repo.delete_screen_exception("PAXG", "history")
    assert removed == 1
    assert repo.get_screen_exceptions("PAXG") == {}
    assert repo.list_screen_exceptions() == []


def test_delete_screen_exception_on_a_nonexistent_row_returns_0(repo):
    """The caller must be able to tell a real revoke from a no-op -- a nonexistent row is not an
    error, but it must not be reported as a successful revoke either."""
    removed = repo.delete_screen_exception("PAXG", "history")
    assert removed == 0


def test_get_screen_exceptions_is_scoped_to_the_asset(repo):
    repo.upsert_screen_exception(
        asset="PAXG", criterion="history", rationale="paxg reason", granted_by="a", granted_at=1
    )
    repo.upsert_screen_exception(
        asset="SOL", criterion="history", rationale="sol reason", granted_by="a", granted_at=1
    )

    assert repo.get_screen_exceptions("PAXG") == {"history": "paxg reason"}
    assert "SOL" not in repo.get_screen_exceptions("PAXG")
