"""Tests for keel.sim.metrics: hand-rolled Decimal financial metrics (Sim Task 3).

Every fixture here is hand-computed (no numpy/pandas/statsmodels reference values) so the
expected numbers are exact `Decimal` arithmetic that anyone can re-derive on paper. rf=0
throughout (halal policy: riba-free), matching the Global Constraints in the sim plan.
"""

from __future__ import annotations

from decimal import Decimal

from keel.sim.metrics import (
    bar_pnl,
    cagr_money_weighted,
    cumulative_returns,
    daily_returns,
    ewma_volatility,
    irr,
    max_drawdown_pct,
    return_per_drawdown,
    sharpe,
    sortino,
    volatility,
)


def test_daily_returns_simple():
    ec = [(0, Decimal("100")), (1, Decimal("110")), (2, Decimal("99"))]
    assert daily_returns(ec) == [Decimal("0.1"), Decimal("-0.1")]


def test_daily_returns_empty_and_single_point():
    assert daily_returns([]) == []
    assert daily_returns([(0, Decimal("100"))]) == []


def test_daily_returns_guards_zero_prior_equity():
    ec = [(0, Decimal("0")), (1, Decimal("50"))]
    assert daily_returns(ec) == [Decimal("0")]


def test_cumulative_returns_simple():
    ec = [(0, Decimal("100")), (1, Decimal("110")), (2, Decimal("99"))]
    assert cumulative_returns(ec) == [Decimal("0.1"), Decimal("-0.01")]


def test_cumulative_returns_empty():
    assert cumulative_returns([]) == []
    assert cumulative_returns([(0, Decimal("100"))]) == []


def test_volatility_known_sample_stdev():
    # mean=0.2, deviations -0.1/0/0.1, sample var = 0.02/2 = 0.01 => stdev = 0.1
    returns = [Decimal("0.1"), Decimal("0.2"), Decimal("0.3")]
    assert volatility(returns) == Decimal("0.1")


def test_volatility_needs_at_least_two_points():
    assert volatility([]) == Decimal("0")
    assert volatility([Decimal("0.05")]) == Decimal("0")


def test_volatility_zero_when_flat():
    assert volatility([Decimal("0.02"), Decimal("0.02"), Decimal("0.02")]) == Decimal("0")


def test_ewma_volatility_matches_hand_computed_recurrence():
    # lam=0.5: var0=0.5*0+0.5*0.2^2=0.02; var1=0.5*0.02+0.5*0^2=0.01 => sqrt=0.1
    r = [Decimal("0.2"), Decimal("0")]
    assert ewma_volatility(r, lam=Decimal("0.5")) == Decimal("0.1")


def test_ewma_volatility_empty_returns_zero():
    assert ewma_volatility([]) == Decimal("0")


def test_sharpe_zero_when_flat():
    assert sharpe([Decimal("0"), Decimal("0"), Decimal("0")]) == Decimal("0")


def test_sharpe_zero_on_empty():
    assert sharpe([]) == Decimal("0")


def test_sortino_ignores_upside_vol():
    # a series with big positive spikes but tiny downside has Sortino > Sharpe
    r = [Decimal("0.2"), Decimal("-0.01"), Decimal("0.2"), Decimal("-0.01")]
    assert sortino(r) > sharpe(r)


def test_sortino_zero_when_no_downside():
    # no negative returns => downside deviation is 0 => guarded to 0, not a ZeroDivisionError
    assert sortino([Decimal("0.1"), Decimal("0.2")]) == Decimal("0")


def test_max_drawdown_pct():
    ec = [(0, Decimal("100")), (1, Decimal("120")), (2, Decimal("90")), (3, Decimal("110"))]
    assert max_drawdown_pct(ec) == Decimal("0.25")  # 120 -> 90


def test_max_drawdown_pct_zero_when_monotonic_up():
    ec = [(0, Decimal("100")), (1, Decimal("110")), (2, Decimal("120"))]
    assert max_drawdown_pct(ec) == Decimal("0")


def test_max_drawdown_pct_empty_returns_zero():
    assert max_drawdown_pct([]) == Decimal("0")


def test_irr_recovers_known_rate():
    # $100 in at t0, ends $121 after 2 periods => ~10%/period
    rate = irr([(0, Decimal("-100"))], ending_value=Decimal("121"))
    assert abs(rate - Decimal("0.1")) < Decimal("0.01")


def test_irr_empty_cashflows_returns_zero():
    assert irr([], ending_value=Decimal("100")) == Decimal("0")


def test_cagr_money_weighted_known_case():
    # $100 contributed at t0, grows to $121 over exactly 2 years => CAGR ~10%/yr
    year = 365 * 86400
    rate = cagr_money_weighted(
        [(0, Decimal("-100"))], ending_value=Decimal("121"), start_ts=0, end_ts=2 * year
    )
    assert abs(rate - Decimal("0.1")) < Decimal("0.01")


def test_cagr_money_weighted_guards_zero_contribution():
    assert cagr_money_weighted([], ending_value=Decimal("100"), start_ts=0, end_ts=100) == Decimal(
        "0"
    )


def test_return_per_drawdown_basic():
    assert return_per_drawdown(Decimal("0.5"), Decimal("0.25")) == Decimal("2")


def test_return_per_drawdown_guards_zero_drawdown():
    assert return_per_drawdown(Decimal("0.5"), Decimal("0")) == Decimal("0")


# -- bar_pnl: contribution-adjusted per-bar P&L (spec §4.5) --------------------


def test_bar_pnl_subtracts_contributions_from_the_bar_they_land_on():
    """A deposit is new capital, not profit.

    Uncorrected it would inflate the mean while leaving downside deviation untouched --
    silently distorting every Sortino computed from this series.
    """
    equity = [(0, Decimal("1000")), (1, Decimal("1600")), (2, Decimal("1550"))]
    contributions = [(1, Decimal("500"))]
    assert bar_pnl(equity, contributions) == [Decimal("100"), Decimal("-50")]


def test_bar_pnl_without_contributions_is_plain_differencing():
    equity = [(0, Decimal("100")), (1, Decimal("110")), (2, Decimal("105"))]
    assert bar_pnl(equity, []) == [Decimal("10"), Decimal("-5")]


def test_bar_pnl_needs_two_points():
    assert bar_pnl([], []) == []
    assert bar_pnl([(0, Decimal("100"))], []) == []


def test_bar_pnl_sums_multiple_contributions_on_the_same_bar():
    equity = [(0, Decimal("0")), (1, Decimal("300"))]
    assert bar_pnl(equity, [(1, Decimal("100")), (1, Decimal("150"))]) == [Decimal("50")]
