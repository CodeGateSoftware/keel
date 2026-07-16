"""Tests for halal_cb.strategy.stats: the shared trade-aggregation helper (P3 Task 1).

Extracted out of the identical `_summarize` implementations previously duplicated in
`backtest.py` and `paper.py`. Builds `Trade` fixtures directly (no backtester/paper-trader
dependency) so this module's aggregation math is verified in isolation; `test_backtest.py`
and `test_paper.py` continue to exercise it indirectly end-to-end via `backtest()` /
`track_record()`.
"""

from __future__ import annotations

from decimal import Decimal

from halal_cb.strategy.rules.base import Trade
from halal_cb.strategy.stats import BacktestResult, summarize
from halal_cb.types import Side


def _trade(
    outcome: str,
    pnl: Decimal | None = None,
    mfe: Decimal = Decimal("0"),
    mae: Decimal = Decimal("0"),
    entry_ts: int = 0,
    exit_ts: int | None = 1,
) -> Trade:
    return Trade(
        entry_ts=entry_ts,
        exit_ts=exit_ts,
        entry=Decimal("100"),
        exit=Decimal("100") + (pnl or Decimal("0")),
        qty=Decimal("1"),
        side=Side.BUY,
        pnl=pnl,
        r_multiple=(pnl / Decimal("10")) if pnl is not None else None,
        mfe=mfe,
        mae=mae,
        outcome=outcome,
    )


def test_summarize_empty_trades_returns_zeroed_result() -> None:
    result = summarize([])

    assert isinstance(result, BacktestResult)
    assert result.trades == []
    assert result.n_trades == 0
    assert result.win_rate == 0.0
    assert result.avg_win == Decimal(0)
    assert result.avg_loss == Decimal(0)
    assert result.expectancy == Decimal(0)
    assert result.profit_factor == Decimal(0)
    assert result.max_drawdown == Decimal(0)
    assert result.max_losing_streak == 0


def test_summarize_only_open_trades_returns_zeroed_aggregate_but_keeps_trades() -> None:
    open_trade = _trade("open", pnl=None, exit_ts=None)

    result = summarize([open_trade])

    assert result.trades == [open_trade]
    assert result.n_trades == 0
    assert result.win_rate == 0.0


def test_summarize_computes_win_rate_and_averages() -> None:
    trades = [
        _trade("win", pnl=Decimal("30")),
        _trade("win", pnl=Decimal("10")),
        _trade("loss", pnl=Decimal("-10")),
        _trade("scratch", pnl=Decimal("0")),
    ]

    result = summarize(trades)

    assert result.n_trades == 4
    assert result.win_rate == 0.5
    assert result.avg_win == Decimal("20")
    assert result.avg_loss == Decimal("-10")
    assert result.expectancy == Decimal("30") / 4


def test_summarize_profit_factor_gross_profit_over_gross_loss() -> None:
    trades = [_trade("win", pnl=Decimal("40")), _trade("loss", pnl=Decimal("-20"))]

    result = summarize(trades)

    assert result.profit_factor == Decimal("2")


def test_summarize_profit_factor_infinite_when_no_losses() -> None:
    trades = [_trade("win", pnl=Decimal("10"))]

    result = summarize(trades)

    assert result.profit_factor == Decimal("Infinity")


def test_summarize_max_drawdown_and_losing_streak() -> None:
    trades = [
        _trade("win", pnl=Decimal("10")),
        _trade("loss", pnl=Decimal("-5")),
        _trade("loss", pnl=Decimal("-5")),
        _trade("loss", pnl=Decimal("-5")),
        _trade("win", pnl=Decimal("2")),
    ]

    result = summarize(trades)

    # running: 10, 5, 0, -5, -3; peak: 10 the whole way; max_drawdown = 10 - (-5) = 15
    assert result.max_drawdown == Decimal("15")
    assert result.max_losing_streak == 3


def test_summarize_avg_mfe_and_mae_over_closed_trades_only() -> None:
    trades = [
        _trade("win", pnl=Decimal("10"), mfe=Decimal("12"), mae=Decimal("2")),
        _trade("loss", pnl=Decimal("-5"), mfe=Decimal("4"), mae=Decimal("6")),
        _trade("open", pnl=None, exit_ts=None, mfe=Decimal("100"), mae=Decimal("100")),
    ]

    result = summarize(trades)

    assert result.avg_mfe == Decimal("8")
    assert result.avg_mae == Decimal("4")


def test_summarize_open_trade_included_in_trades_but_excluded_from_aggregates() -> None:
    closed = _trade("win", pnl=Decimal("10"))
    still_open = _trade("open", pnl=None, exit_ts=None)

    result = summarize([closed, still_open])

    assert result.trades == [closed, still_open]
    assert result.n_trades == 1
