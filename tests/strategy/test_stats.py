"""Tests for keel.strategy.stats: the shared trade-aggregation helper (P3 Task 1).

Extracted out of the identical `_summarize` implementations previously duplicated in
`backtest.py` and `paper.py`. Builds `Trade` fixtures directly (no backtester/paper-trader
dependency) so this module's aggregation math is verified in isolation; `test_backtest.py`
and `test_paper.py` continue to exercise it indirectly end-to-end via `backtest()` /
`track_record()`.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from keel.strategy.rules.base import Trade
from keel.strategy.stats import BacktestResult, summarize
from keel.types import Side


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


def test_summarize_rejects_a_closed_trade_carrying_no_pnl() -> None:
    """A closed trade with no realised P&L is a broken input, and must SAY so.

    Only an open trade may omit `pnl`; every close path sets it. The value of raising here is
    the diagnostic: unguarded, this surfaced as `TypeError: unsupported operand type(s) for +:
    'Decimal' and 'NoneType'` raised from inside a generator, with no way to tell which trade
    caused it. The outcome is asserted in the message for exactly that reason.
    """
    with pytest.raises(ValueError, match="pnl=None") as excinfo:
        summarize([_trade("win", pnl=None)])

    assert "outcome='win'" in str(excinfo.value)


# ---------------------------------------------------------------------------
# #820: R aggregates -- per-trade R pooled in R, never in price units
# ---------------------------------------------------------------------------


def _r_trade(
    r: str,
    risk: str | None,
    *,
    mfe_r: str = "0",
    mae_r: str = "0",
    exit_ts: int = 1,
    qty: str = "1",
) -> Trade:
    """A closed trade that made exactly `r` R on a per-unit initial risk of `risk`, at
    `qty` units. `pnl = r * risk * qty`, so the trade's price scale is set by `risk` alone;
    MFE/MAE are per unit, as every close path records them. `risk=None` builds a trade
    with no recorded initial risk (pnl then comes from `r` read as price units)."""
    risk_d = Decimal(risk) if risk is not None else None
    qty_d = Decimal(qty)
    unit = risk_d if risk_d is not None else Decimal(1)
    pnl = Decimal(r) * unit * qty_d
    outcome = "win" if pnl > 0 else "loss" if pnl < 0 else "scratch"
    return Trade(
        entry_ts=0,
        exit_ts=exit_ts,
        entry=Decimal("100"),
        exit=Decimal("100"),
        qty=qty_d,
        side=Side.BUY,
        pnl=pnl,
        r_multiple=None,
        mfe=Decimal(mfe_r) * unit,
        mae=Decimal(mae_r) * unit,
        outcome=outcome,  # type: ignore[arg-type]
        initial_risk=risk_d,
    )


def _r_fields(result: BacktestResult) -> tuple:
    return (
        result.expectancy_r,
        result.avg_win_r,
        result.avg_loss_r,
        result.profit_factor_r,
        result.max_drawdown_r,
        result.avg_mfe_r,
        result.avg_mae_r,
        result.n_excluded_no_risk,
        result.n_wins_r,
        result.n_losses_r,
    )


def test_r_aggregates_are_identical_across_a_btc_scale_and_an_xlm_scale_sample() -> None:
    """(a) The same R path at price 100000 and at price 0.1 gives the SAME R aggregates --
    the unit the edge table and the floors claim. The money fields are wildly different,
    which is the whole point: they are price units, not R."""
    btc = summarize([_r_trade("2", "2000"), _r_trade("-1", "2000"), _r_trade("0.5", "2000")])
    xlm = summarize([_r_trade("2", "0.002"), _r_trade("-1", "0.002"), _r_trade("0.5", "0.002")])

    assert _r_fields(btc) == _r_fields(xlm)
    assert btc.expectancy_r == Decimal("1.5") / 3
    assert btc.avg_win_r == Decimal("1.25")
    assert btc.avg_loss_r == Decimal("-1")
    assert btc.profit_factor_r == Decimal("2.5")
    # ... while the price-unit expectancy differs by the price ratio.
    assert btc.expectancy == Decimal("1000")
    assert xlm.expectancy == Decimal("0.001")


def test_r_is_qty_invariant() -> None:
    """R divides by the WHOLE position's risk (`initial_risk * qty`): a paper trade at
    qty 0.5 that made 2R is 2R, not 1R."""
    result = summarize([_r_trade("2", "10", qty="0.5")])

    assert result.expectancy_r == Decimal("2")


def test_r_drawdown_mfe_and_mae_are_in_r() -> None:
    """(b) `max_drawdown_r` is the drawdown of CUMULATIVE R in list order; MFE/MAE are each
    trade's per-unit excursion over its per-unit initial risk, averaged over the R sample."""
    trades = [
        _r_trade("1", "10", mfe_r="2", mae_r="0.5"),
        _r_trade("-1", "0.01", mfe_r="0.5", mae_r="1"),
        _r_trade("-2", "500", mfe_r="0", mae_r="2"),
        _r_trade("3", "4", mfe_r="3.5", mae_r="0.5"),
    ]

    result = summarize(trades)

    # cumulative R: 1, 0, -2, 1 -> peak 1, trough -2 -> drawdown 3R
    assert result.max_drawdown_r == Decimal("3")
    assert result.avg_mfe_r == Decimal("6") / 4
    assert result.avg_mae_r == Decimal("4") / 4
    assert result.n_wins_r == 2
    assert result.n_losses_r == 2


def test_trades_with_no_or_zero_risk_are_excluded_from_r_and_counted() -> None:
    """(c) A trade with no initial risk, or a zero one, has no R. It is EXCLUDED from every R
    aggregate (not counted as 0R) and the exclusion is counted -- while it still counts in
    the money aggregates and in `n_trades`."""
    trades = [
        _r_trade("2", "10"),
        _r_trade("-1", "10"),
        _r_trade("-50", None),
        _r_trade("-50", "0"),
    ]

    result = summarize(trades)

    assert result.n_trades == 4
    assert result.n_excluded_no_risk == 2
    assert result.expectancy_r == Decimal("0.5")
    assert result.avg_loss_r == Decimal("-1")
    assert result.max_drawdown_r == Decimal("1")
    assert result.n_losses_r == 1


def test_an_all_no_risk_sample_gives_none_r_fields_never_zero() -> None:
    """(c) With no R at all, every R aggregate is None -- 0R would read as a flat, measured
    edge, which is a claim nobody measured."""
    result = summarize([_r_trade("1", None), _r_trade("-1", "0")])

    assert result.n_trades == 2
    assert result.n_excluded_no_risk == 2
    assert _r_fields(result) == (None, None, None, None, None, None, None, 2, 0, 0)


def test_an_empty_sample_gives_none_r_fields() -> None:
    result = summarize([])

    assert _r_fields(result) == (None, None, None, None, None, None, None, 0, 0, 0)


def test_r_profit_factor_keeps_the_money_conventions() -> None:
    """No losses -> Infinity; an R sample of wins only reports avg_loss_r 0, like avg_loss."""
    result = summarize([_r_trade("2", "10")])

    assert result.profit_factor_r == Decimal("Infinity")
    assert result.avg_loss_r == Decimal(0)


def test_money_fields_still_read_pnl_not_r() -> None:
    """(i) The money consumers (`insights`, the web payload, tuning, walk-forward) read
    `expectancy`/`avg_win`/`avg_loss`/`max_drawdown`/`avg_mfe` as price units. Pinned here
    on a sample where R and pnl disagree in size, so a regression that pointed the money
    fields at R cannot pass."""
    result = summarize(
        [_r_trade("2", "1000", mfe_r="3", mae_r="1"), _r_trade("-1", "1000", mfe_r="1")]
    )

    assert result.expectancy == Decimal("500")
    assert result.avg_win == Decimal("2000")
    assert result.avg_loss == Decimal("-1000")
    assert result.max_drawdown == Decimal("1000")
    assert result.avg_mfe == Decimal("2000")
    assert result.avg_mae == Decimal("500")
    assert result.expectancy_r == Decimal("0.5")


def test_the_shared_r_helper() -> None:
    """One formula for every close path: `pnl / (initial_risk * qty)`, None without a risk."""
    from keel.strategy.rules.base import initial_risk_of, r_multiple_of

    assert initial_risk_of(Decimal("90"), Decimal("94")) == Decimal("4")
    assert initial_risk_of(Decimal("100"), Decimal("94")) == Decimal("6")
    assert r_multiple_of(Decimal("-2"), Decimal("4"), Decimal("1")) == Decimal("-0.5")
    assert r_multiple_of(Decimal("6"), Decimal("4"), Decimal("0.5")) == Decimal("3")
    assert r_multiple_of(Decimal("6"), None, Decimal("1")) is None
    assert r_multiple_of(Decimal("6"), Decimal("0"), Decimal("1")) is None
