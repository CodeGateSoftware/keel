"""Unit tests for explore_leads.py (drawdown-taper and Merton-gamma exploration helpers)."""

from __future__ import annotations

import pytest
from explore_leads import (
    compute_mu_sigma2,
    merton_fraction,
    simulate_path,
    solve_implied_gamma,
    strategy_fixed,
    taper_fraction,
)

# --- taper_fraction ------------------------------------------------------------------------


def test_taper_fraction_no_drawdown_returns_f_base():
    # d=0 -> f_eff = (1 - 0/D) * f_base = f_base exactly.
    result = taper_fraction(f_base=0.0625, current_dd=0.0, taper_ceiling=0.25)
    assert result == pytest.approx(0.0625)


def test_taper_fraction_at_ceiling_returns_zero():
    # d >= D -> f_eff = 0 exactly.
    assert taper_fraction(f_base=0.0625, current_dd=0.25, taper_ceiling=0.25) == 0.0
    assert taper_fraction(f_base=0.0625, current_dd=0.30, taper_ceiling=0.25) == 0.0


def test_taper_fraction_linear_decay_midpoint():
    # d = D/2 -> f_eff = 0.5 * f_base.
    assert taper_fraction(f_base=0.10, current_dd=0.10, taper_ceiling=0.20) == pytest.approx(0.05)


def test_taper_fraction_clamped_to_one():
    # A pathological f_base > 1 must still clamp the output to [0, 1].
    assert taper_fraction(f_base=1.5, current_dd=0.0, taper_ceiling=0.25) == 1.0


def test_taper_fraction_invalid_ceiling_raises():
    with pytest.raises(ValueError):
        taper_fraction(f_base=0.05, current_dd=0.0, taper_ceiling=0.0)
    with pytest.raises(ValueError):
        taper_fraction(f_base=0.05, current_dd=0.0, taper_ceiling=-0.1)


def test_taper_fraction_negative_dd_raises():
    with pytest.raises(ValueError):
        taper_fraction(f_base=0.05, current_dd=-0.01, taper_ceiling=0.25)


def test_taper_fraction_negative_f_base_raises():
    with pytest.raises(ValueError):
        taper_fraction(f_base=-0.05, current_dd=0.0, taper_ceiling=0.25)


# --- compute_mu_sigma2 ---------------------------------------------------------------------


def test_compute_mu_sigma2_known_case():
    # p=0.55, b=1.5 -> mu = 0.55*1.5 - 0.45 = 0.825 - 0.45 = 0.375
    # sigma2 = p*b^2 + (1-p)*1 - mu^2 = 0.55*2.25 + 0.45 - 0.140625 = 1.2375 + 0.45 - 0.140625
    mu, sigma2 = compute_mu_sigma2(0.55, 1.5)
    assert mu == pytest.approx(0.375)
    assert sigma2 == pytest.approx(1.546875)


def test_compute_mu_sigma2_invalid_p_raises():
    with pytest.raises(ValueError):
        compute_mu_sigma2(1.5, 1.0)
    with pytest.raises(ValueError):
        compute_mu_sigma2(-0.1, 1.0)


def test_compute_mu_sigma2_invalid_b_raises():
    with pytest.raises(ValueError):
        compute_mu_sigma2(0.55, 0.0)


# --- solve_implied_gamma -------------------------------------------------------------------


def test_solve_implied_gamma_round_trips():
    mu, sigma2 = compute_mu_sigma2(0.55, 1.5)
    target_f = 0.01
    gamma = solve_implied_gamma(mu, sigma2, target_f)
    assert merton_fraction(mu, sigma2, gamma) == pytest.approx(target_f)


def test_solve_implied_gamma_round_trips_second_profile():
    mu, sigma2 = compute_mu_sigma2(0.58, 2.0)
    target_f = 0.01
    gamma = solve_implied_gamma(mu, sigma2, target_f)
    assert merton_fraction(mu, sigma2, gamma) == pytest.approx(target_f)


def test_solve_implied_gamma_no_edge_raises():
    with pytest.raises(ValueError):
        solve_implied_gamma(mu=0.0, sigma2=1.0, target_f=0.01)
    with pytest.raises(ValueError):
        solve_implied_gamma(mu=-0.1, sigma2=1.0, target_f=0.01)


def test_solve_implied_gamma_invalid_sigma2_raises():
    with pytest.raises(ValueError):
        solve_implied_gamma(mu=0.1, sigma2=0.0, target_f=0.01)


def test_solve_implied_gamma_invalid_target_raises():
    with pytest.raises(ValueError):
        solve_implied_gamma(mu=0.1, sigma2=1.0, target_f=0.0)


# --- hard-breaker halt logic (simulate_path) -----------------------------------------------


def test_hard_breaker_halts_trading_for_rest_of_path():
    # A large, constant risk fraction with an unfavorable seed should trip the 20% breaker
    # early; once tripped, terminal bankroll must stop changing for the remaining trades (i.e.
    # simulating fewer bets with the same seed produces the same terminal bankroll once past
    # the trip point).
    fraction_fn = strategy_fixed(0.5)  # aggressive: half of bankroll risked every trade
    full = simulate_path(
        fraction_fn, n_bets=200, p=0.55, b=1.5, seed=1, initial=1000.0, hard_breaker_dd=0.20
    )
    assert full["breaker_tripped"] is True

    # Re-running with far fewer trades (but long enough to have already tripped) must match the
    # full run's terminal bankroll exactly, proving no further trades were placed after the trip.
    short = simulate_path(
        fraction_fn, n_bets=5, p=0.55, b=1.5, seed=1, initial=1000.0, hard_breaker_dd=0.20
    )
    assert short["breaker_tripped"] is True
    assert short["terminal"] == pytest.approx(full["terminal"])


def test_hard_breaker_not_tripped_without_dd_reaching_ceiling():
    # A tiny, safe fraction over a short run should not trip a 20% breaker.
    fraction_fn = strategy_fixed(0.01)
    result = simulate_path(
        fraction_fn, n_bets=20, p=0.55, b=1.5, seed=1, initial=1000.0, hard_breaker_dd=0.20
    )
    assert result["breaker_tripped"] is False


def test_no_hard_breaker_when_none():
    # hard_breaker_dd=None (default) must never trip, even for an aggressive fraction.
    fraction_fn = strategy_fixed(0.9)
    result = simulate_path(
        fraction_fn, n_bets=50, p=0.55, b=1.5, seed=1, initial=1000.0, hard_breaker_dd=None
    )
    assert result["breaker_tripped"] is False
