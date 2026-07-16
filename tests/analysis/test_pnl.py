"""Tests for halal_cb.analysis.pnl: FIFO realized/unrealized P&L, position sizing,
daily pnl_daily snapshots, and drawdown/recovery metrics.

Transaction dicts mirror the shape returned by `Repository.get_transactions()`
(see halal_cb/data/repository.py): coinbase_id, source, type, asset, ts, qty,
price, subtotal, total, fees, notes, rule_id, order_id. Only the fields pnl.py
actually consumes (type, asset, ts, qty, price, fees) are populated here.
"""

from __future__ import annotations

from decimal import Decimal

from halal_cb.analysis.pnl import (
    Position,
    daily_snapshot,
    max_drawdown,
    position,
    realized_pnl,
    recovery_pct,
    unrealized_pnl,
)


def _tx(
    type_: str,
    asset: str,
    ts: int,
    qty: str,
    price: str,
    fees: str | None = None,
) -> dict:
    return {
        "coinbase_id": f"{type_}-{asset}-{ts}",
        "source": "csv_import",
        "type": type_,
        "asset": asset,
        "ts": ts,
        "qty": Decimal(qty),
        "price": Decimal(price),
        "subtotal": None,
        "total": None,
        "fees": Decimal(fees) if fees is not None else None,
        "notes": None,
        "rule_id": None,
        "order_id": None,
    }


def _base_history() -> list[dict]:
    """buy 1@100, buy 1@200, sell 1@300 (FIFO closes the first lot)."""
    return [
        _tx("buy", "BTC", 1, "1", "100"),
        _tx("buy", "BTC", 2, "1", "200"),
        _tx("sell", "BTC", 3, "1", "300"),
    ]


class TestRealizedPnl:
    def test_fifo_matches_first_lot(self):
        assert realized_pnl(_base_history()) == Decimal("200")

    def test_filters_by_asset(self):
        txs = _base_history() + [
            _tx("buy", "ETH", 1, "1", "50"),
            _tx("sell", "ETH", 2, "1", "40"),
        ]
        assert realized_pnl(txs, asset="BTC") == Decimal("200")
        assert realized_pnl(txs, asset="ETH") == Decimal("-10")
        assert realized_pnl(txs) == Decimal("190")

    def test_convert_closes_lots_like_a_sell(self):
        txs = [
            _tx("buy", "BTC", 1, "1", "100"),
            _tx("convert", "BTC", 2, "1", "150"),
        ]
        assert realized_pnl(txs) == Decimal("50")

    def test_out_of_order_transactions_are_sorted_by_ts(self):
        txs = [
            _tx("sell", "BTC", 3, "1", "300"),
            _tx("buy", "BTC", 1, "1", "100"),
            _tx("buy", "BTC", 2, "1", "200"),
        ]
        assert realized_pnl(txs) == Decimal("200")

    def test_fees_adjust_cost_basis_and_proceeds(self):
        txs = [
            _tx("buy", "BTC", 1, "1", "100", fees="10"),
            _tx("sell", "BTC", 2, "1", "300", fees="5"),
        ]
        # cost basis = 100 + 10 = 110; proceeds = 300 - 5 = 295; realized = 185
        assert realized_pnl(txs) == Decimal("185")

    def test_non_trade_types_are_ignored(self):
        txs = _base_history() + [_tx("rewards income", "BTC", 4, "1", "0")]
        assert realized_pnl(txs) == Decimal("200")


class TestPosition:
    def test_remaining_lot_after_fifo_sell(self):
        pos = position(_base_history(), "BTC")
        assert pos == Position(qty=Decimal("1"), avg_cost=Decimal("200"))

    def test_no_transactions_gives_flat_position(self):
        pos = position([], "BTC")
        assert pos == Position(qty=Decimal("0"), avg_cost=Decimal("0"))

    def test_weighted_average_across_multiple_open_lots(self):
        txs = [
            _tx("buy", "BTC", 1, "1", "100"),
            _tx("buy", "BTC", 2, "3", "200"),
        ]
        pos = position(txs, "BTC")
        # (1*100 + 3*200) / 4 = 175
        assert pos == Position(qty=Decimal("4"), avg_cost=Decimal("175"))


class TestUnrealizedPnl:
    def test_marks_open_position(self):
        result = unrealized_pnl(_base_history(), {"BTC": Decimal("250")})
        assert result == {"BTC": Decimal("50")}

    def test_omits_assets_without_a_mark(self):
        result = unrealized_pnl(_base_history(), {})
        assert result == {}

    def test_omits_fully_closed_assets(self):
        txs = [
            _tx("buy", "BTC", 1, "1", "100"),
            _tx("sell", "BTC", 2, "1", "300"),
        ]
        result = unrealized_pnl(txs, {"BTC": Decimal("250")})
        assert result == {}


class TestDailySnapshot:
    def test_row_shape_matches_pnl_daily_columns(self):
        rows = daily_snapshot(_base_history(), {"BTC": Decimal("250")}, "2026-07-15")
        assert rows == [
            {
                "date": "2026-07-15",
                "asset": "BTC",
                "qty": Decimal("1"),
                "avg_cost": Decimal("200"),
                "price": Decimal("250"),
                "realized": Decimal("200"),
                "unrealized": Decimal("50"),
            }
        ]

    def test_flat_asset_has_zero_qty_and_unrealized(self):
        txs = [
            _tx("buy", "BTC", 1, "1", "100"),
            _tx("sell", "BTC", 2, "1", "300"),
        ]
        rows = daily_snapshot(txs, {"BTC": Decimal("250")}, "2026-07-15")
        assert rows == [
            {
                "date": "2026-07-15",
                "asset": "BTC",
                "qty": Decimal("0"),
                "avg_cost": Decimal("0"),
                "price": Decimal("250"),
                "realized": Decimal("200"),
                "unrealized": Decimal("0"),
            }
        ]


class TestMaxDrawdown:
    def test_depth_and_duration_on_synthetic_curve(self):
        curve = [Decimal(v) for v in [100, 120, 60, 60, 90, 120, 150]]
        depth, duration = max_drawdown(curve)
        assert depth == Decimal("0.5")
        assert duration == 4

    def test_no_drawdown_when_monotonically_rising(self):
        curve = [Decimal(v) for v in [100, 110, 120, 130]]
        depth, duration = max_drawdown(curve)
        assert depth == Decimal("0")
        assert duration == 0

    def test_empty_curve(self):
        assert max_drawdown([]) == (Decimal("0"), 0)

    def test_never_recovers_duration_runs_to_end(self):
        curve = [Decimal(v) for v in [100, 50, 40]]
        depth, duration = max_drawdown(curve)
        assert depth == Decimal("0.6")
        assert duration == 2


class TestRecoveryPct:
    def test_fifty_percent_drawdown_needs_a_double(self):
        assert recovery_pct(Decimal("0.5")) == Decimal("1.0")

    def test_ten_percent_drawdown(self):
        result = recovery_pct(Decimal("0.1"))
        assert round(result, 4) == Decimal("0.1111")

    def test_zero_drawdown_needs_no_recovery(self):
        assert recovery_pct(Decimal("0")) == Decimal("0")
