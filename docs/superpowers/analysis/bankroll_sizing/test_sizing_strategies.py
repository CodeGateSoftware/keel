"""Unit tests for sizing_strategies.py (bankroll-sizing math study)."""

from __future__ import annotations

import pytest

from sizing_strategies import (
    cppi_fraction,
    drawdown_adjusted_kelly,
    fixed_fraction,
    fractional_kelly,
    kelly_fraction,
    merton_fraction,
    naive_flat_fraction,
)


# --- kelly_fraction ----------------------------------------------------------------------------


def test_kelly_fraction_even_money_60pct():
    # f* = (b*p - q)/b = (1*0.6 - 0.4)/1 = 0.2
    assert kelly_fraction(0.6, 1.0) == pytest.approx(0.2)


def test_kelly_fraction_floor_win_rate_even_money():
    # p=0.55, b=1.0 -> (0.55 - 0.45)/1 = 0.10
    assert kelly_fraction(0.55, 1.0) == pytest.approx(0.1)


def test_kelly_fraction_floor_rule():
    # p=0.55, b=1.5 (keel's promotion floor: min_win_rate=0.55, min_rr=1.5)
    # f* = (1.5*0.55 - 0.45)/1.5 = (0.825 - 0.45)/1.5 = 0.375/1.5 = 0.25
    assert kelly_fraction(0.55, 1.5) == pytest.approx(0.25)


def test_kelly_fraction_no_edge_returns_zero():
    # p=0.5, b=1.0 -> edge = 0.5 - 0.5 = 0 -> no edge
    assert kelly_fraction(0.5, 1.0) == 0.0


def test_kelly_fraction_losing_edge_returns_zero():
    # p=0.4, b=1.0 -> edge = 0.4 - 0.6 = -0.2 -> negative, clamp to 0
    assert kelly_fraction(0.4, 1.0) == 0.0


def test_kelly_fraction_clamped_to_one():
    # Strong edge with high b can imply f* > 1; must clamp.
    assert kelly_fraction(0.99, 100.0) <= 1.0
    assert kelly_fraction(0.99, 100.0) >= 0.0


def test_kelly_fraction_invalid_p_raises():
    with pytest.raises(ValueError):
        kelly_fraction(1.5, 1.0)
    with pytest.raises(ValueError):
        kelly_fraction(-0.1, 1.0)


def test_kelly_fraction_invalid_b_raises():
    with pytest.raises(ValueError):
        kelly_fraction(0.55, 0.0)
    with pytest.raises(ValueError):
        kelly_fraction(0.55, -1.0)


# --- fractional_kelly ---------------------------------------------------------------------------


def test_fractional_kelly_half():
    full = kelly_fraction(0.55, 1.5)
    assert fractional_kelly(0.55, 1.5, 0.5) == pytest.approx(0.5 * full)
    assert fractional_kelly(0.55, 1.5, 0.5) == pytest.approx(0.125)


def test_fractional_kelly_quarter():
    full = kelly_fraction(0.55, 1.5)
    assert fractional_kelly(0.55, 1.5, 0.25) == pytest.approx(0.25 * full)
    assert fractional_kelly(0.55, 1.5, 0.25) == pytest.approx(0.0625)


def test_fractional_kelly_full_lambda_equals_full_kelly():
    assert fractional_kelly(0.55, 1.5, 1.0) == pytest.approx(kelly_fraction(0.55, 1.5))


def test_fractional_kelly_zero_lambda_is_zero():
    assert fractional_kelly(0.55, 1.5, 0.0) == 0.0


def test_fractional_kelly_negative_lambda_raises():
    with pytest.raises(ValueError):
        fractional_kelly(0.55, 1.5, -0.1)


# --- drawdown_adjusted_kelly ---------------------------------------------------------------------


def test_drawdown_adjusted_kelly_zero_dd_equals_full_kelly():
    assert drawdown_adjusted_kelly(0.55, 1.5, current_dd=0.0, max_dd=0.20) == pytest.approx(
        kelly_fraction(0.55, 1.5)
    )


def test_drawdown_adjusted_kelly_at_max_dd_is_zero():
    assert drawdown_adjusted_kelly(0.55, 1.5, current_dd=0.20, max_dd=0.20) == 0.0


def test_drawdown_adjusted_kelly_beyond_max_dd_is_zero():
    assert drawdown_adjusted_kelly(0.55, 1.5, current_dd=0.30, max_dd=0.20) == 0.0


def test_drawdown_adjusted_kelly_halfway_scales_by_half():
    full = kelly_fraction(0.55, 1.5)
    result = drawdown_adjusted_kelly(0.55, 1.5, current_dd=0.10, max_dd=0.20)
    assert result == pytest.approx(0.5 * full)


def test_drawdown_adjusted_kelly_invalid_max_dd_raises():
    with pytest.raises(ValueError):
        drawdown_adjusted_kelly(0.55, 1.5, current_dd=0.0, max_dd=0.0)


def test_drawdown_adjusted_kelly_negative_current_dd_raises():
    with pytest.raises(ValueError):
        drawdown_adjusted_kelly(0.55, 1.5, current_dd=-0.01, max_dd=0.20)


# --- fixed_fraction -------------------------------------------------------------------------------


def test_fixed_fraction_default_is_one_percent():
    assert fixed_fraction() == pytest.approx(0.01)


def test_fixed_fraction_passthrough():
    assert fixed_fraction(0.05) == pytest.approx(0.05)


def test_fixed_fraction_clamped_above_one():
    assert fixed_fraction(2.0) == 1.0


def test_fixed_fraction_negative_raises():
    with pytest.raises(ValueError):
        fixed_fraction(-0.01)


# --- cppi_fraction ----------------------------------------------------------------------------


def test_cppi_fraction_basic_math():
    # bankroll=1000, floor=800, m=3 -> 3*(1000-800)/1000 = 3*0.2 = 0.6
    assert cppi_fraction(bankroll=1000.0, floor=800.0, multiplier=3.0) == pytest.approx(0.6)


def test_cppi_fraction_clamped_to_one_when_cushion_large():
    # cushion large relative to bankroll * multiplier -> clamp to 1
    assert cppi_fraction(bankroll=1000.0, floor=100.0, multiplier=3.0) == 1.0


def test_cppi_fraction_zero_when_at_or_below_floor():
    assert cppi_fraction(bankroll=800.0, floor=800.0, multiplier=3.0) == 0.0
    assert cppi_fraction(bankroll=700.0, floor=800.0, multiplier=3.0) == 0.0


def test_cppi_fraction_invalid_bankroll_raises():
    with pytest.raises(ValueError):
        cppi_fraction(bankroll=0.0, floor=0.0, multiplier=3.0)
    with pytest.raises(ValueError):
        cppi_fraction(bankroll=-100.0, floor=0.0, multiplier=3.0)


def test_cppi_fraction_negative_multiplier_raises():
    with pytest.raises(ValueError):
        cppi_fraction(bankroll=1000.0, floor=800.0, multiplier=-1.0)


# --- naive_flat_fraction ------------------------------------------------------------------------


def test_naive_flat_fraction_basic_math():
    assert naive_flat_fraction(fixed_stake=10.0, bankroll=1000.0) == pytest.approx(0.01)


def test_naive_flat_fraction_clamped_when_stake_exceeds_bankroll():
    assert naive_flat_fraction(fixed_stake=2000.0, bankroll=1000.0) == 1.0


def test_naive_flat_fraction_zero_stake_is_zero():
    assert naive_flat_fraction(fixed_stake=0.0, bankroll=1000.0) == 0.0


def test_naive_flat_fraction_invalid_bankroll_raises():
    with pytest.raises(ValueError):
        naive_flat_fraction(fixed_stake=10.0, bankroll=0.0)


def test_naive_flat_fraction_negative_stake_raises():
    with pytest.raises(ValueError):
        naive_flat_fraction(fixed_stake=-10.0, bankroll=1000.0)


# --- merton_fraction ----------------------------------------------------------------------------


def test_merton_fraction_basic_math():
    # exp_return=0.08, variance=0.16, gamma=1 -> 0.5
    assert merton_fraction(exp_return=0.08, variance=0.16, gamma=1.0) == pytest.approx(0.5)


def test_merton_fraction_clamped_to_one():
    assert merton_fraction(exp_return=5.0, variance=0.01, gamma=1.0) == 1.0


def test_merton_fraction_no_edge_is_zero():
    assert merton_fraction(exp_return=0.0, variance=0.16, gamma=1.0) == 0.0
    assert merton_fraction(exp_return=-0.05, variance=0.16, gamma=1.0) == 0.0


def test_merton_fraction_invalid_variance_raises():
    with pytest.raises(ValueError):
        merton_fraction(exp_return=0.08, variance=0.0, gamma=1.0)


def test_merton_fraction_invalid_gamma_raises():
    with pytest.raises(ValueError):
        merton_fraction(exp_return=0.08, variance=0.16, gamma=0.0)
