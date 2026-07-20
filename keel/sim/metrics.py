"""Hand-rolled `Decimal` financial metrics for the offline simulator (Sim Task 3).

Pure `Decimal` -- no `float`, no NumPy/Pandas/statsmodels (spec-declined dependencies).
Sample statistics use `Decimal.sqrt()` under the ambient `decimal` context; the EWMA
volatility uses the standard iterative `var = lam*var + (1-lam)*r**2` recurrence rather
than a windowed recomputation. `rf = 0` everywhere (halal policy: riba-free), so Sharpe
and Sortino are simple mean/downside-risk ratios with no risk-free subtraction.

Every function guards its own division-by-zero / insufficient-data edge cases by
returning `Decimal(0)` rather than raising -- these are analytics over historical sims,
not live trading paths, but a raised exception would still abort a report run over one
bad window, so we degrade to a neutral zero instead.
"""

from __future__ import annotations

from decimal import Decimal

DEFAULT_EWMA_LAMBDA = Decimal("0.94")


def _mean(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal(0)
    return sum(values, Decimal(0)) / len(values)


def _sample_variance(values: list[Decimal]) -> Decimal:
    n = len(values)
    if n < 2:
        return Decimal(0)
    mean = _mean(values)
    total = sum(((v - mean) ** 2 for v in values), Decimal(0))
    variance = total / (n - 1)
    return variance if variance > 0 else Decimal(0)


def _downside_variance(returns: list[Decimal]) -> Decimal:
    """Population-style downside variance against a target of 0 (rf=0).

    Squares only the negative returns but divides by the *full* sample size `n`
    (not the count of negative returns) -- the standard Sortino "downside deviation"
    definition. Using the full `n` (rather than the negative-only subset's own sample
    stdev) keeps the denominator well-behaved even when there are very few negative
    observations, and correctly reflects that "no downside at all" is low risk, not
    an undefined one.
    """
    n = len(returns)
    if n == 0:
        return Decimal(0)
    total = sum((r * r for r in returns if r < 0), Decimal(0))
    variance = total / n
    return variance if variance > 0 else Decimal(0)


def daily_returns(equity_curve: list[tuple[int, Decimal]]) -> list[Decimal]:
    """Simple period-over-period returns: `(e[i] - e[i-1]) / e[i-1]`.

    A zero-or-negative prior equity value would make the return undefined; guarded to
    `Decimal(0)` for that step rather than raising `DivisionByZero`.
    """
    if len(equity_curve) < 2:
        return []
    out: list[Decimal] = []
    prev = equity_curve[0][1]
    for _, val in equity_curve[1:]:
        out.append((val - prev) / prev if prev != 0 else Decimal(0))
        prev = val
    return out


def cumulative_returns(equity_curve: list[tuple[int, Decimal]]) -> list[Decimal]:
    """Cumulative return relative to the first point: `(e[i] - e[0]) / e[0]`.

    Computed directly from the equity curve (not by compounding `daily_returns`) so
    there's no accumulated rounding drift across a long series.
    """
    if len(equity_curve) < 2:
        return []
    e0 = equity_curve[0][1]
    if e0 == 0:
        return [Decimal(0) for _ in equity_curve[1:]]
    return [(val - e0) / e0 for _, val in equity_curve[1:]]


def volatility(returns: list[Decimal]) -> Decimal:
    """Sample stdev of `returns` (ddof=1) via `Decimal.sqrt()`. `Decimal(0)` if n<2."""
    variance = _sample_variance(returns)
    return variance.sqrt() if variance > 0 else Decimal(0)


def ewma_volatility(
    returns: list[Decimal], lam: Decimal = DEFAULT_EWMA_LAMBDA
) -> Decimal:
    """Exponentially-weighted volatility via the iterative RiskMetrics recurrence.

    `var_t = lam*var_{t-1} + (1-lam)*r_t**2`, seeded at `var=0`, walked forward over
    `returns` in order; returns `sqrt(var)` of the final state.
    """
    if not returns:
        return Decimal(0)
    var = Decimal(0)
    one_minus_lam = Decimal(1) - lam
    for r in returns:
        var = lam * var + one_minus_lam * (r * r)
    return var.sqrt() if var > 0 else Decimal(0)


def sharpe(returns: list[Decimal], periods_per_year: int = 365) -> Decimal:
    """Annualized Sharpe ratio, rf=0: `mean/stdev * sqrt(periods_per_year)`.

    `Decimal(0)` when `returns` is empty or stdev is 0 (flat or single-point series).
    """
    if not returns:
        return Decimal(0)
    stdev = volatility(returns)
    if stdev == 0:
        return Decimal(0)
    return (_mean(returns) / stdev) * Decimal(periods_per_year).sqrt()


def sortino(returns: list[Decimal], periods_per_year: int = 365) -> Decimal:
    """Annualized Sortino ratio, rf=0: `mean/downside_dev * sqrt(periods_per_year)`.

    `Decimal(0)` when `returns` is empty or there is no downside variance (no negative
    returns at all, or too few observations).
    """
    if not returns:
        return Decimal(0)
    downside_var = _downside_variance(returns)
    if downside_var == 0:
        return Decimal(0)
    downside_dev = downside_var.sqrt()
    return (_mean(returns) / downside_dev) * Decimal(periods_per_year).sqrt()


def max_drawdown_pct(equity_curve: list[tuple[int, Decimal]]) -> Decimal:
    """Largest peak-to-trough decline, as a fraction of the peak (e.g. `0.25` = -25%)."""
    if not equity_curve:
        return Decimal(0)
    peak = equity_curve[0][1]
    worst = Decimal(0)
    for _, val in equity_curve:
        if val > peak:
            peak = val
        if peak > 0:
            dd = (peak - val) / peak
            if dd > worst:
                worst = dd
    return worst


def _npv(
    ranked_cashflows: list[tuple[int, Decimal]],
    ending_value: Decimal,
    ending_period: int,
    rate: Decimal,
) -> Decimal:
    growth = Decimal(1) + rate
    total = Decimal(0)
    for period, amount in ranked_cashflows:
        total += amount / (growth**period)
    total += ending_value / (growth**ending_period)
    return total


def irr(
    cashflows: list[tuple[int, Decimal]],
    ending_value: Decimal,
    max_iter: int = 100,
) -> Decimal:
    """Money-weighted internal rate of return via bisection, solved on `Decimal`.

    Solves `sum(cf_i / (1+r)**t_i) + ending_value / (1+r)**T == 0` for `r`, where each
    cash flow's exponent `t_i` is its 0-indexed rank once sorted by `ts` (not the raw
    `ts` value -- `ts` may be an epoch timestamp, which would blow up the exponent) and
    the terminal `ending_value` is treated as landing one full period after all `N`
    contribution periods have elapsed, i.e. `T = N + 1`.

    Bisects over `r` in `(-0.9999, 10]`, expanding the upper bound a bounded number of
    times if no sign change is found there; returns `Decimal(0)` (rather than raising)
    if the series is empty or no root is bracketed within the iteration budget.
    """
    if not cashflows:
        return Decimal(0)
    ranked = [
        (i, amount) for i, (_, amount) in enumerate(sorted(cashflows, key=lambda cf: cf[0]))
    ]
    ending_period = len(cashflows) + 1

    lo, hi = Decimal("-0.9999"), Decimal("10")
    f_lo = _npv(ranked, ending_value, ending_period, lo)
    f_hi = _npv(ranked, ending_value, ending_period, hi)

    expansions = 0
    while f_lo * f_hi > 0 and expansions < 20:
        hi *= 2
        f_hi = _npv(ranked, ending_value, ending_period, hi)
        expansions += 1
    if f_lo * f_hi > 0:
        return Decimal(0)

    mid = (lo + hi) / 2
    for _ in range(max_iter):
        mid = (lo + hi) / 2
        f_mid = _npv(ranked, ending_value, ending_period, mid)
        if f_mid == 0:
            break
        if (f_lo > 0) == (f_mid > 0):
            lo, f_lo = mid, f_mid
        else:
            hi, f_hi = mid, f_mid
        if hi - lo < Decimal("0.0000001"):
            break
    return mid


def cagr_money_weighted(
    cashflows: list[tuple[int, Decimal]],
    ending_value: Decimal,
    start_ts: int,
    end_ts: int,
) -> Decimal:
    """Annualized money-weighted return over the real calendar span `[start_ts, end_ts]`.

    Unlike `irr` (which annualizes per contribution-period rank), this uses the actual
    elapsed wall-clock time: total contributed (sum of negative cashflows, i.e. money
    put in) compounds to `ending_value` over `years = (end_ts-start_ts)/(365*86400)`.
    `Decimal(0)` if there's no positive contribution, no positive ending value, or a
    non-positive time span.
    """
    total_in = -sum((amount for _, amount in cashflows if amount < 0), Decimal(0))
    if total_in <= 0 or ending_value <= 0 or end_ts <= start_ts:
        return Decimal(0)
    years = Decimal(end_ts - start_ts) / Decimal(365 * 86400)
    if years <= 0:
        return Decimal(0)
    ratio = ending_value / total_in
    return ratio ** (Decimal(1) / years) - Decimal(1)


def return_per_drawdown(total_return_pct: Decimal, max_dd_pct: Decimal) -> Decimal:
    """Total return divided by max drawdown -- a simple return/risk efficiency ratio.

    `Decimal(0)` when `max_dd_pct` is 0 (no drawdown observed) rather than raising.
    """
    if max_dd_pct == 0:
        return Decimal(0)
    return total_return_pct / max_dd_pct


def bar_pnl(
    equity_curve: list[tuple[int, Decimal]],
    contributions: list[tuple[int, Decimal]],
) -> list[Decimal]:
    """Per-bar P&L: equity deltas with external contributions removed.

    A raw `equity[i] - equity[i-1]` would score a monthly deposit as a large positive return,
    which is not P&L at all -- it is new capital. Left uncorrected it would inflate the mean
    and (because deposits are positive) leave the downside deviation untouched, badly
    distorting any Sortino computed downstream. Contributions are matched by timestamp and
    subtracted from the bar they land on.

    Returns one value per step after the first point (an N-point curve yields N-1 P&L values).
    """
    if len(equity_curve) < 2:
        return []
    by_ts: dict[int, Decimal] = {}
    for ts, amount in contributions:
        by_ts[ts] = by_ts.get(ts, Decimal(0)) + amount
    out: list[Decimal] = []
    for (_, previous), (ts, current) in zip(equity_curve, equity_curve[1:]):
        out.append(current - previous - by_ts.get(ts, Decimal(0)))
    return out


def portfolio_volatility(
    returns_by_asset: dict[str, list[Decimal]],
    weights: dict[str, Decimal],
    exposed: list[bool] | None = None,
    periods_per_year: int = 365,
) -> Decimal:
    """Portfolio sigma with NO covariance matrix (KB §83.2).

    Build one synthetic portfolio series -- start each sleeve at its weight, compound it on its
    own return, sum the sleeves each period -- then take the plain stdev of THAT series'
    returns. It equals the `sqrt(transpose(w*sigma) x CorrelationMatrix x (w*sigma))` route
    exactly, because correlation is absorbed by construction in the summed series. No matrix, no
    transpose, no inversion, no conditioning problem, and no NumPy.

    ⚠️ **`exposed` is not optional in spirit.** Pass a per-period mask of "were we actually
    holding anything?" and the flat, mostly-cash days are dropped. A whole-period sigma over the
    Turtle's intermittent series UNDERSTATES risk, because days with no position contribute
    zeros to the denominator -- §54.22's GASP objection, resolved the same way §73.4 resolved it
    for Sharpe. Omitting the mask computes the whole-period number, which is the wrong one for
    an intermittent book; it is allowed only because a fully-invested caller is legitimate.

    ⛔ **A MEASUREMENT, NEVER AN ALLOCATOR.** Feeding this to a weight search is the MPT /
    mean-variance direction, which is declined (§33, §50.1, §54.22). It exists to answer "how
    volatile is the book?", not to choose the book.

    Returns `Decimal(0)` rather than raising on degenerate input -- consistent with the rest of
    this module, which is analytics over historical sims, not a live trading path.
    """
    if not returns_by_asset or not weights:
        return Decimal(0)

    assets = [a for a in returns_by_asset if a in weights]
    if not assets:
        return Decimal(0)
    length = min(len(returns_by_asset[a]) for a in assets)
    if length < 2:
        return Decimal(0)

    sleeves = {a: weights[a] for a in assets}
    total = sum(sleeves.values(), Decimal(0))
    if total <= 0:
        return Decimal(0)

    portfolio_returns: list[Decimal] = []
    previous_value = total
    for index in range(length):
        for asset in assets:
            sleeves[asset] *= Decimal(1) + returns_by_asset[asset][index]
        value = sum(sleeves.values(), Decimal(0))
        if previous_value > 0:
            portfolio_returns.append(value / previous_value - Decimal(1))
        else:
            portfolio_returns.append(Decimal(0))
        previous_value = value

    if exposed is not None:
        portfolio_returns = [
            r for r, is_exposed in zip(portfolio_returns, exposed) if is_exposed
        ]

    if len(portfolio_returns) < 2:
        return Decimal(0)
    return _sample_variance(portfolio_returns).sqrt() * Decimal(periods_per_year).sqrt()
