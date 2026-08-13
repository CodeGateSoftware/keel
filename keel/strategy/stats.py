"""Shared trade-aggregation stats helper (P3 Task 1).

`summarize()` was previously duplicated verbatim between `backtest.py`'s private
`_summarize` and `paper.py`'s private `_summarize` -- both walked a `list[Trade]` (backtest
fills or paper fills) into the same `BacktestResult` shape so historical and forward-tested
stats stay directly comparable (per `paper.py`'s original module docstring). This module is
the single source of truth for that aggregation; `backtest.py` and `paper.track_record()`
both call it now instead of maintaining their own copies.

`BacktestResult` lives here too (moved out of `backtest.py`) so this module has no
dependency on `backtest.py`, only on the shared `Trade` type from `strategy.rules.base` --
`backtest.py` imports `BacktestResult` back from here (and re-exports it, so existing
`from keel.strategy.backtest import BacktestResult` call sites are unaffected).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from keel.strategy.rules.base import Trade


@dataclass
class BacktestResult:
    """Aggregate stats over a rule's simulated (or paper-traded) trade history.

    All aggregate metrics (everything but `trades`/`n_trades`) are computed over
    *closed* trades only -- a still-open trade at the end of the series is included
    in `trades` for visibility but excluded from win/loss/expectancy/etc.
    """

    trades: list[Trade]
    n_trades: int
    win_rate: float
    avg_win: Decimal
    avg_loss: Decimal
    expectancy: Decimal
    profit_factor: Decimal
    max_drawdown: Decimal
    max_losing_streak: int
    avg_mfe: Decimal
    avg_mae: Decimal


def _closed_pnl(trade: Trade) -> Decimal:
    """`trade`'s realised P&L, with the closed-trade invariant stated instead of assumed.

    `Trade.pnl` is `Decimal | None` because an OPEN trade has no realised P&L yet. Every other
    outcome is produced by a close path that sets it (`backtest._closed_trade`; `paper` does the
    same), and `summarize` filters to `outcome != "open"` before computing any aggregate -- so
    within those aggregates `pnl` is never `None`. Nothing in the type system said so, which left
    every `sum()` below summing `Decimal | None`.

    Asserting it here rather than at each call site buys two things: the aggregates come out
    typed `Decimal` instead of `Decimal | None`, and a violation surfaces as a named error
    naming the offending outcome, rather than as a `TypeError: unsupported operand type(s) for
    +: 'decimal.Decimal' and 'NoneType'` raised from inside a generator with no trade in hand.
    """
    if trade.pnl is None:
        raise ValueError(
            f"trade with outcome={trade.outcome!r} has pnl=None; only an open trade may "
            "omit realised P&L, and open trades are excluded from these aggregates"
        )
    return trade.pnl


def summarize(trades: list[Trade]) -> BacktestResult:
    """Aggregate `trades` into a `BacktestResult`.

    Only *closed* trades (`outcome != "open"`) count toward the aggregate metrics; a
    still-open trade is included in `trades` for visibility only.
    """
    closed = [t for t in trades if t.outcome != "open"]
    n_trades = len(closed)

    if n_trades == 0:
        return BacktestResult(
            trades=trades,
            n_trades=0,
            win_rate=0.0,
            avg_win=Decimal(0),
            avg_loss=Decimal(0),
            expectancy=Decimal(0),
            profit_factor=Decimal(0),
            max_drawdown=Decimal(0),
            max_losing_streak=0,
            avg_mfe=Decimal(0),
            avg_mae=Decimal(0),
        )

    wins = [t for t in closed if t.outcome == "win"]
    losses = [t for t in closed if t.outcome == "loss"]

    win_rate = len(wins) / n_trades
    avg_win = (sum((_closed_pnl(t) for t in wins), Decimal(0)) / len(wins)) if wins else Decimal(0)
    avg_loss = (
        (sum((_closed_pnl(t) for t in losses), Decimal(0)) / len(losses)) if losses else Decimal(0)
    )
    expectancy = sum((_closed_pnl(t) for t in closed), Decimal(0)) / n_trades

    gross_profit = sum((_closed_pnl(t) for t in wins), Decimal(0))
    gross_loss = abs(sum((_closed_pnl(t) for t in losses), Decimal(0)))
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    elif gross_profit > 0:
        profit_factor = Decimal("Infinity")
    else:
        profit_factor = Decimal(0)

    running = Decimal(0)
    peak = Decimal(0)
    max_drawdown = Decimal(0)
    streak = 0
    max_losing_streak = 0
    for t in closed:
        running += _closed_pnl(t)
        peak = max(peak, running)
        max_drawdown = max(max_drawdown, peak - running)
        if t.outcome == "loss":
            streak += 1
            max_losing_streak = max(max_losing_streak, streak)
        else:
            streak = 0

    avg_mfe = sum((t.mfe for t in closed), Decimal(0)) / n_trades
    avg_mae = sum((t.mae for t in closed), Decimal(0)) / n_trades

    return BacktestResult(
        trades=trades,
        n_trades=n_trades,
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        expectancy=expectancy,
        profit_factor=profit_factor,
        max_drawdown=max_drawdown,
        max_losing_streak=max_losing_streak,
        avg_mfe=avg_mfe,
        avg_mae=avg_mae,
    )
