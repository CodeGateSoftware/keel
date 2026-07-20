"""DSR / E[max] / MinBTL (KB §78.1, §78.3, §73.2).

The load-bearing tests are the ones that reproduce PUBLISHED values: if `expected_max_sharpe`
does not match §73.1's table, every number downstream of it is wrong.
"""

from __future__ import annotations

import math

import pytest

from keel.research.deflate import (
    deflated_sharpe,
    expected_max_sharpe,
    implied_independent_trials,
    inverse_normal_cdf,
    kurtosis,
    min_backtest_length_years,
    min_trades,
    normal_cdf,
    sharpe_rejection_threshold,
    skewness,
)

# -- inverse normal CDF --------------------------------------------------------


def test_inverse_normal_cdf_matches_known_quantiles():
    assert inverse_normal_cdf(0.5) == pytest.approx(0.0, abs=1e-9)
    assert inverse_normal_cdf(0.975) == pytest.approx(1.959963985, abs=1e-6)
    assert inverse_normal_cdf(0.9) == pytest.approx(1.281551566, abs=1e-6)
    assert inverse_normal_cdf(0.025) == pytest.approx(-1.959963985, abs=1e-6)


def test_inverse_normal_cdf_is_the_inverse_of_normal_cdf():
    for x in (-2.5, -0.7, 0.0, 0.7, 2.5):
        assert inverse_normal_cdf(normal_cdf(x)) == pytest.approx(x, abs=1e-6)


def test_inverse_normal_cdf_rejects_out_of_range():
    for bad in (0.0, 1.0, -0.1, 1.1):
        with pytest.raises(ValueError):
            inverse_normal_cdf(bad)


# -- E[max] against the PUBLISHED table ----------------------------------------


def test_expected_max_sharpe_reproduces_the_published_table():
    """§78.1 verifies N=10 -> 1.5746, N=128 -> 2.6163, N=1000 -> 3.2551 against §73.1's table.

    If this drifts, SR_0, DSR and MinBTL are all silently wrong -- they are all built on it.
    """
    assert expected_max_sharpe(10) == pytest.approx(1.5746, abs=5e-4)
    assert expected_max_sharpe(128) == pytest.approx(2.6163, abs=5e-4)
    assert expected_max_sharpe(1000) == pytest.approx(3.2551, abs=5e-4)


def test_expected_max_sharpe_rises_with_trial_count():
    values = [expected_max_sharpe(n) for n in (5, 10, 50, 100, 1000)]
    assert values == sorted(values)


def test_expected_max_sharpe_needs_at_least_two_trials():
    with pytest.raises(ValueError):
        expected_max_sharpe(1)


# -- §78.2 implied independent trials ------------------------------------------


def test_zero_correlation_recovers_the_raw_trial_count():
    assert implied_independent_trials(0.0, 336) == pytest.approx(336)


def test_perfect_correlation_collapses_to_one_trial():
    assert implied_independent_trials(1.0, 336) == pytest.approx(1.0)


def test_the_papers_worked_correction():
    """§78.2: at rho=0.90 with M~336, N̂ ~ 34."""
    assert implied_independent_trials(0.90, 336) == pytest.approx(34.5, abs=0.6)


def test_rho_outside_the_unit_interval_is_rejected():
    for bad in (-0.1, 1.1):
        with pytest.raises(ValueError):
            implied_independent_trials(bad, 10)


# -- SR_0 and DSR --------------------------------------------------------------


def test_zero_dispersion_across_trials_means_no_selection_bias():
    """§78.1's mechanism: SR_0 scales with sqrt(V). A flat plateau has almost no bias."""
    assert sharpe_rejection_threshold(100, 0.0) == 0.0


def test_the_threshold_rises_with_both_dispersion_and_trial_count():
    base = sharpe_rejection_threshold(50, 1.0)
    assert sharpe_rejection_threshold(50, 4.0) > base
    assert sharpe_rejection_threshold(500, 1.0) > base


def test_dsr_is_high_when_the_observed_sharpe_clears_the_bar():
    assert deflated_sharpe(2.0, 0.5, 200) > 0.95


def test_dsr_is_low_when_the_observed_sharpe_sits_at_or_below_the_bar():
    assert deflated_sharpe(0.5, 0.5, 200) == pytest.approx(0.5, abs=1e-9)
    assert deflated_sharpe(0.3, 0.9, 200) < 0.1


def test_more_observations_sharpen_the_same_edge():
    """T is an evidence MULTIPLIER: the same gap over the bar is more convincing with more data."""
    assert deflated_sharpe(1.0, 0.5, 500) > deflated_sharpe(1.0, 0.5, 30)


def test_negative_skew_and_fat_tails_reduce_confidence():
    """The non-Normality terms are the part §78.3 calls genuinely new."""
    normal = deflated_sharpe(1.0, 0.5, 200, skewness=0.0, kurtosis=3.0)
    ugly = deflated_sharpe(1.0, 0.5, 200, skewness=-1.5, kurtosis=9.0)
    assert ugly < normal


def test_dsr_degrades_to_zero_rather_than_raising_on_inconsistent_moments():
    assert deflated_sharpe(5.0, 0.5, 50, skewness=10.0, kurtosis=3.0) == 0.0


def test_dsr_needs_at_least_two_observations():
    assert deflated_sharpe(2.0, 0.5, 1) == 0.0


# -- MinBTL --------------------------------------------------------------------


def test_minbtl_is_selection_bias_over_observed_performance_squared():
    n, observed = 45, 1.0
    assert min_backtest_length_years(n, observed) == pytest.approx(
        (expected_max_sharpe(n) / observed) ** 2
    )


def test_the_papers_worked_example_45_configurations_at_5_years():
    """§73.2: 'if only 5 years of data are available, no more than 45 independent model
    configurations should be tried' at an IS annualised Sharpe of 1."""
    assert min_backtest_length_years(45, 1.0) == pytest.approx(5.0, abs=0.4)


def test_the_alarming_example_7_configurations_at_2_years():
    """§73.2: 'After trying only 7 independent configurations, the expected maximum SR IS is 1
    for a 2-year long backtest.'"""
    assert min_backtest_length_years(7, 1.0) == pytest.approx(2.0, abs=0.4)


def test_a_stronger_edge_needs_less_data():
    assert min_backtest_length_years(30, 2.0) < min_backtest_length_years(30, 1.0)


def test_more_trials_need_more_data():
    assert min_backtest_length_years(300, 1.0) > min_backtest_length_years(30, 1.0)


def test_a_non_positive_edge_can_never_be_established():
    assert min_backtest_length_years(30, 0.0) == math.inf
    assert min_backtest_length_years(30, -0.5) == math.inf


def test_min_trades_converts_years_at_the_observed_frequency():
    years = min_backtest_length_years(30, 0.4)
    assert min_trades(30, 0.4, 6.0) == pytest.approx(years * 6.0)
    assert min_trades(30, 0.4, 0.0) == math.inf


# -- moments -------------------------------------------------------------------


def test_symmetric_data_has_zero_skew():
    assert skewness([-2.0, -1.0, 0.0, 1.0, 2.0]) == pytest.approx(0.0, abs=1e-12)


def test_a_left_tail_produces_negative_skew():
    assert skewness([-10.0, 1.0, 1.0, 1.0, 1.0]) < 0


def test_kurtosis_is_non_excess_and_defaults_to_normal_on_thin_samples():
    assert kurtosis([1.0, 2.0]) == 3.0
    assert kurtosis([0.0, 0.0, 0.0, 0.0]) == 3.0


def test_moments_of_a_constant_series_are_neutral():
    assert skewness([5.0] * 10) == 0.0
    assert kurtosis([5.0] * 10) == 3.0
