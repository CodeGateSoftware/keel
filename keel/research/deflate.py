"""Deflated Sharpe Ratio, expected-maximum Sharpe, and Minimum Backtest Length (KB §78, §73).

The arithmetic that turns "we tried N configurations" into "here is the bar this strategy has to
clear, and here is how much data that needs". Reporting only -- ⛔ per §78.7's Strathern rail none
of these may ever be a sweep's ranking key.

Three quantities, in dependency order:

    E[max SR_n]  -- the Sharpe you expect to observe from the LUCKIEST of N zero-skill trials
    SR_0         -- that, scaled by the observed dispersion of trial Sharpes: the rejection bar
    DSR          -- the probability the selected strategy's Sharpe exceeds SR_0, given T
                    observations, skew and kurtosis
    MinBTL       -- (selection bias / observed performance)^2, in years

`float` internally, not `Decimal`: every one of these is a probability or a ratio of estimates, the
inverse-normal CDF is a rational approximation with no exact form, and §78.1 explicitly says float
is not the binding constraint here. `Decimal` is this project's rule for MONEY, and none of this is
money.
"""

from __future__ import annotations

import math

EULER_MASCHERONI = 0.5772156649015329

# Acklam's rational approximation to the inverse standard-normal CDF. ~1.15e-9 relative accuracy,
# which reproduces the paper's published E[max] table to 4 decimals (asserted in the tests).
_A = (-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
      1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00)
_B = (-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
      6.680131188771972e01, -1.328068155288572e01)
_C = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
      -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00)
_D = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00, 3.754408661907416e00)
_P_LOW = 0.02425


def inverse_normal_cdf(p: float) -> float:
    """`Z^-1(p)` for `0 < p < 1`."""
    if not 0.0 < p < 1.0:
        raise ValueError(f"p must be in (0, 1), got {p}")
    if p < _P_LOW:
        q = math.sqrt(-2 * math.log(p))
        return (((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / (
            (((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1
        )
    if p > 1 - _P_LOW:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / (
            (((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1
        )
    q = p - 0.5
    r = q * q
    return (((((_A[0] * r + _A[1]) * r + _A[2]) * r + _A[3]) * r + _A[4]) * r + _A[5]) * q / (
        ((((_B[0] * r + _B[1]) * r + _B[2]) * r + _B[3]) * r + _B[4]) * r + 1
    )


def normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def expected_max_sharpe(n_trials: int) -> float:
    """`E[max{SR_n}]` for `n_trials` INDEPENDENT zero-skill trials, unit variance (§78.1).

    The bracketed term of (A) Eq. (1). §73.1's table is this function, i.e. the special case
    `E[{SR_n}] = 0`, `V[{SR_n}] = 1`.
    """
    if n_trials < 2:
        raise ValueError("n_trials must be >= 2 for the extreme-value approximation")
    gamma = EULER_MASCHERONI
    return (1 - gamma) * inverse_normal_cdf(1 - 1 / n_trials) + gamma * inverse_normal_cdf(
        1 - 1 / (n_trials * math.e)
    )


def implied_independent_trials(rho_hat: float, m_trials: int) -> float:
    """`N̂ = ρ̂ + (1 − ρ̂)·M` (§78.2 Appendix A.3).

    Makes the ledger honest about CORRELATED sweeps: adjacent lookbacks are not independent
    trials, so raw `M` overstates the multiplicity. `ρ̂ = 0` recovers `M`; `ρ̂ = 1` collapses to 1.
    """
    if not 0.0 <= rho_hat <= 1.0:
        raise ValueError(f"rho_hat must be in [0, 1], got {rho_hat}")
    return rho_hat + (1 - rho_hat) * m_trials


def sharpe_rejection_threshold(n_trials: int, variance_of_trial_sharpes: float) -> float:
    """`SR_0 = sqrt(V[{SR_n}]) · E[max_N]` (§78.3).

    ⚠️ Unit discipline: `SR_0` must be in the SAME unit as the `SR` it is compared against. Under
    §73.4's per-trade formulation both are per-trade and there is nothing to de-annualise.
    """
    if variance_of_trial_sharpes < 0:
        raise ValueError("variance must be non-negative")
    return math.sqrt(variance_of_trial_sharpes) * expected_max_sharpe(n_trials)


def deflated_sharpe(
    observed_sharpe: float,
    threshold_sharpe: float,
    n_observations: int,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """DSR = PSR evaluated against `threshold_sharpe` instead of zero (§78.3).

    `kurtosis` is NON-excess (Normal = 3). §78.13 item 6: default `skew=0, kurtosis=3` when
    `T < 30` -- with a handful of trades the higher moments are noise wearing a formula.
    """
    if n_observations < 2:
        return 0.0
    denominator = 1 - skewness * observed_sharpe + ((kurtosis - 1) / 4) * observed_sharpe**2
    if denominator <= 0:
        # Negative variance under the non-Normality adjustment: the moment estimates are not
        # self-consistent at this sample size. Report "no confidence" rather than a complex number.
        return 0.0
    numerator = (observed_sharpe - threshold_sharpe) * math.sqrt(n_observations - 1)
    return normal_cdf(numerator / math.sqrt(denominator))


def min_backtest_length_years(n_trials: int, observed_annual_sharpe: float) -> float:
    """`MinBTL ~ (E[max_N] / observed_SR)^2` years (§73.2 Theorem 3.1).

    Read plainly: **(selection bias / observed performance)^2**. The years of data you need before
    an IS Sharpe of `observed_annual_sharpe`, selected from `n_trials`, is distinguishable from
    the luckiest of `n_trials` coin flips.
    """
    if observed_annual_sharpe <= 0:
        return math.inf
    return (expected_max_sharpe(n_trials) / observed_annual_sharpe) ** 2


def min_trades(n_trials: int, observed_annual_sharpe: float, trades_per_year: float) -> float:
    """MinBTL expressed in TRADES rather than years -- the unit a promotion floor is written in."""
    if trades_per_year <= 0:
        return math.inf
    return min_backtest_length_years(n_trials, observed_annual_sharpe) * trades_per_year


def skewness(values: list[float]) -> float:
    n = len(values)
    if n < 3:
        return 0.0
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / n
    if variance <= 0:
        return 0.0
    return (sum((v - mean) ** 3 for v in values) / n) / variance**1.5


def kurtosis(values: list[float]) -> float:
    """NON-excess kurtosis (Normal = 3), matching §78.3's `γ̂₄`."""
    n = len(values)
    if n < 4:
        return 3.0
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / n
    if variance <= 0:
        return 3.0
    return (sum((v - mean) ** 4 for v in values) / n) / variance**2
