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

**Two units, side by side (#820).** The original aggregates (`expectancy`, `avg_win`, ...,
`avg_mae`) are built from `Trade.pnl`/`mfe`/`mae`: PRICE UNITS -- for the backtester, dollars
per one coin of the base asset. They are money, and `insights`, the web payload, tuning and
walk-forward present or optimise them as money, so they stay exactly as they were. They are
NOT comparable across assets: a BTC trade and an XLM trade differ by five orders of magnitude
in price, so any pool of them is decided by the highest-priced asset.

The `*_r` twins are the same aggregates in R -- each trade's net P&L over the risk it carried
(`rules.base.r_multiple_of`) -- which is what the edge table, G2 and the promotion floors
judge (spec §5.1: "expectancy (R) ... max_drawdown (R)"). A trade with no recorded initial
risk has no R: it is EXCLUDED from every R aggregate (never counted as 0R) and counted in
`n_excluded_no_risk`, and a sample with no R at all reports every R aggregate as `None`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from keel.strategy.rules.base import Trade, r_multiple_of


@dataclass
class BacktestResult:
    """Aggregate stats over a rule's simulated (or paper-traded) trade history.

    All aggregate metrics (everything but `trades`/`n_trades`) are computed over
    *closed* trades only -- a still-open trade at the end of the series is included
    in `trades` for visibility but excluded from win/loss/expectancy/etc.

    The fields through `avg_mae` are in PRICE UNITS; the `*_r` fields are the same
    aggregates in R over the closed trades that carry an initial risk (see the module
    docstring). The R fields are defaulted so a hand-built result (tests, `pool_stats`)
    that predates them still constructs -- and reads as "no R", which the floors refuse.
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
    #: Mean R over the closed trades with R; `None` when none has R.
    expectancy_r: Decimal | None = None
    #: Mean R of the winning / losing trades with R (0 when the R sample has none of that
    #: side, the money fields' convention); `None` when no trade has R.
    avg_win_r: Decimal | None = None
    avg_loss_r: Decimal | None = None
    #: Gross winning R over gross losing R, with the money field's Infinity / 0 conventions.
    profit_factor_r: Decimal | None = None
    #: The largest peak-to-trough fall of CUMULATIVE R, in the order the trades are given.
    max_drawdown_r: Decimal | None = None
    #: Mean per-unit MFE / MAE over per-unit initial risk.
    avg_mfe_r: Decimal | None = None
    avg_mae_r: Decimal | None = None
    #: Closed trades left out of every R aggregate for carrying no (or a zero) initial risk.
    n_excluded_no_risk: int = 0
    #: The R sample's winning / losing trade counts -- what `promotion.pool_stats` weights
    #: the per-product R means by, so a pooled R mean is the union's exact mean.
    n_wins_r: int = 0
    n_losses_r: int = 0


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


def trade_r(trade: Trade) -> Decimal | None:
    """`trade`'s R, from the ONE shared formula (`rules.base.r_multiple_of`) over its recorded
    initial risk -- `None` when the trade carries none. Aggregates read R through here rather
    than `Trade.r_multiple`, so whether a trade is in the R sample is decided by one fact (its
    initial risk) for R, MFE and MAE alike."""
    if trade.initial_risk is None or trade.initial_risk == 0:
        return None
    return r_multiple_of(_closed_pnl(trade), trade.initial_risk, trade.qty)


def _r_aggregates(closed: list[Trade]) -> dict[str, Any]:
    """The `*_r` fields of `BacktestResult` over `closed` (see its field comments), as
    keyword arguments. A sample with no R returns only the exclusion count, leaving every R
    aggregate at its `None` default."""
    # (trade, its R, its per-unit risk) for every closed trade that has R.
    sample: list[tuple[Trade, Decimal, Decimal]] = []
    for t in closed:
        r = trade_r(t)
        if r is not None and t.initial_risk is not None:
            sample.append((t, r, t.initial_risk))
    excluded = len(closed) - len(sample)
    if not sample:
        return {"n_excluded_no_risk": excluded}

    win_rs = [r for t, r, _risk in sample if t.outcome == "win"]
    loss_rs = [r for t, r, _risk in sample if t.outcome == "loss"]
    gross_win = sum(win_rs, Decimal(0))
    gross_loss = abs(sum(loss_rs, Decimal(0)))
    if gross_loss > 0:
        profit_factor_r = gross_win / gross_loss
    elif gross_win > 0:
        profit_factor_r = Decimal("Infinity")
    else:
        profit_factor_r = Decimal(0)

    running = Decimal(0)
    peak = Decimal(0)
    max_drawdown_r = Decimal(0)
    for _t, r, _risk in sample:
        running += r
        peak = max(peak, running)
        max_drawdown_r = max(max_drawdown_r, peak - running)

    n = len(sample)
    return {
        "expectancy_r": sum((r for _t, r, _risk in sample), Decimal(0)) / n,
        "avg_win_r": (gross_win / len(win_rs)) if win_rs else Decimal(0),
        "avg_loss_r": (sum(loss_rs, Decimal(0)) / len(loss_rs)) if loss_rs else Decimal(0),
        "profit_factor_r": profit_factor_r,
        "max_drawdown_r": max_drawdown_r,
        "avg_mfe_r": sum((t.mfe / risk for t, _r, risk in sample), Decimal(0)) / n,
        "avg_mae_r": sum((t.mae / risk for t, _r, risk in sample), Decimal(0)) / n,
        "n_excluded_no_risk": excluded,
        "n_wins_r": len(win_rs),
        "n_losses_r": len(loss_rs),
    }


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
        )  # every R field defaults to None: an empty sample has no R

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
        **_r_aggregates(closed),
    )
