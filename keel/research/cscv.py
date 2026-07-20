"""Probability of Backtest Overfitting via CSCV (spec §5, KB §78.6).

Model-free, non-parametric and deterministic: no distributional assumption, no forecasting
model, no knowledge of the trading rule -- only the matrix of per-period P&L across the
configurations tried.

⛔ STRATHERN RAIL (spec §6). Nothing here returns the identity of a best-performing
configuration. PBO may gate or report; it may never be a sweep's ranking key, because "when a
measure becomes a target, it ceases to be a good measure" (§78.7). `PBOResult` therefore
carries probabilities and slopes only -- a test in `tests/research/test_cscv.py` fails if a
configuration-bearing field is ever added.

PERFORMANCE. The naive form is ~140M Decimal operations (12,870 combinations x ~12 columns x
904 rows) -- tens of minutes. Sortino is *decomposable*: `count`, `sum(r)` and
`sum(r*r for r < 0)` are additive across blocks, so we precompute those three aggregates once
per (block x column) and each combination becomes O(S/2). ~1.2M operations, seconds, in exact
Decimal. Drawdown-based metrics are NOT decomposable and take the slow path.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from itertools import combinations
from math import comb

#: Stand-ins for a subsample with zero downside deviation, where the Sortino ratio is not
#: finite. They only ever participate in ORDERING, never in reported arithmetic, so a finite
#: sentinel keeps everything in Decimal without introducing a NaN/inf code path.
_POSITIVE_SENTINEL = Decimal("1e18")
_NEGATIVE_SENTINEL = Decimal("-1e18")

Aggregate = tuple[int, Decimal, Decimal]  # (count, sum, sum of squared negatives)
Metric = Callable[[Sequence[Decimal]], Decimal]


@dataclass(frozen=True)
class PBOResult:
    """Probabilities and slopes only -- never a configuration (see the Strathern rail above)."""

    pbo: Decimal
    n_combinations: int
    n_columns: int
    n_blocks: int
    rows_used: int
    rows_dropped: int
    logits: list[Decimal]
    is_performance: list[Decimal]
    oos_performance: list[Decimal]
    degradation_slope: Decimal = Decimal(0)
    degradation_intercept: Decimal = Decimal(0)
    prob_loss: Decimal = Decimal(0)
    dominance_1st: bool = False
    dominance_2nd: bool = False


def combination_count(s: int) -> int:
    return comb(s, s // 2)


def sortino_from_aggregates(count: int, total: Decimal, downside_sq: Decimal) -> Decimal:
    """Sortino from additive aggregates, target 0, rf = 0.

    Downside variance divides by the FULL count (not the negative-only subset), matching
    `keel/sim/metrics._downside_variance`. Deliberately NOT annualised: ranking is invariant
    to a positive scale factor, and the degradation slope scales identically on both axes, so
    annualising would only add noise.
    """
    if count == 0:
        return Decimal(0)
    mean = total / count
    downside_var = downside_sq / count
    if downside_var <= 0:
        # No losing period at all: best possible if we made money, neutral if we broke even.
        if mean > 0:
            return _POSITIVE_SENTINEL
        return Decimal(0) if mean == 0 else _NEGATIVE_SENTINEL
    return mean / downside_var.sqrt()


def sortino_series(returns: Sequence[Decimal]) -> Decimal:
    """Direct (slow-path) Sortino over a series -- the reference the fast path must match."""
    count = len(returns)
    total = sum(returns, Decimal(0))
    downside_sq = sum((r * r for r in returns if r < 0), Decimal(0))
    return sortino_from_aggregates(count, total, downside_sq)


def truncate_to_blocks(columns: Sequence[Sequence[Decimal]], s: int) -> list[list[Decimal]]:
    """Trim to a multiple of `s`, dropping the OLDEST rows so the recent window stays intact."""
    length = min(len(column) for column in columns)
    usable = (length // s) * s
    return [list(column[length - usable :]) for column in columns]


def block_aggregates(column: Sequence[Decimal], s: int) -> list[Aggregate]:
    """`(count, sum, sum of squared negatives)` per block, blocks in original order."""
    size = len(column) // s
    out: list[Aggregate] = []
    for index in range(s):
        chunk = column[index * size : (index + 1) * size]
        out.append(
            (
                len(chunk),
                sum(chunk, Decimal(0)),
                sum((r * r for r in chunk if r < 0), Decimal(0)),
            )
        )
    return out


def _ranks(values: Sequence[Decimal]) -> list[int]:
    """Rank 1 = worst, len = best. Ties break by column index, keeping CSCV deterministic.

    Determinism is a property §78.7 relies on: "running CSCV twice on the same inputs
    generates identical results", which is what makes phi independently replicable.
    """
    order = sorted(range(len(values)), key=lambda i: (values[i], i))
    ranks = [0] * len(values)
    for position, index in enumerate(order, start=1):
        ranks[index] = position
    return ranks


def _ols_slope(xs: Sequence[Decimal], ys: Sequence[Decimal]) -> tuple[Decimal, Decimal]:
    n = len(xs)
    if n < 2:
        return Decimal(0), Decimal(0)
    mean_x = sum(xs, Decimal(0)) / n
    mean_y = sum(ys, Decimal(0)) / n
    denominator = sum(((x - mean_x) ** 2 for x in xs), Decimal(0))
    if denominator == 0:
        return Decimal(0), mean_y
    numerator = sum(((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)), Decimal(0))
    slope = numerator / denominator
    return slope, mean_y - slope * mean_x


def _empirical_cdf(sample: Sequence[Decimal], x: Decimal) -> Decimal:
    return Decimal(sum(1 for value in sample if value <= x)) / len(sample)


def _dominance(selected: Sequence[Decimal], reference: Sequence[Decimal]) -> tuple[bool, bool]:
    """First- and second-order stochastic dominance of `selected` over `reference` (§78.8).

    The direct test of "is our parameter selection better than picking a configuration at
    random?" -- §58.11's random-entry null lifted from entries to the selection process
    itself. §78.8 calls it the most under-rated statistic in the source.
    """
    if not selected or not reference:
        return False, False
    grid = sorted(set(selected) | set(reference))
    first = True
    second = True
    running = Decimal(0)
    for x in grid:
        cdf_selected = _empirical_cdf(selected, x)
        cdf_reference = _empirical_cdf(reference, x)
        if cdf_selected > cdf_reference:
            first = False
        running += cdf_reference - cdf_selected
        if running < 0:
            second = False
    return first, second


def pbo(
    columns: Sequence[Sequence[Decimal]],
    s: int = 16,
    metric: Metric | None = None,
) -> PBOResult:
    """CSCV per Algorithm 2.3 (§78.6). `columns` is N columns of per-bar P&L, length T each.

    `metric=None` uses the fast decomposable Sortino path. Pass a callable (e.g. a
    return/max-drawdown ratio) to take the slow path; submatrices are joined in ORIGINAL ORDER
    either way, because order-dependent metrics require it and getting that wrong is silent.
    """
    if s % 2 != 0:
        raise ValueError(f"s must be even (got {s})")
    if not columns:
        raise ValueError("columns must not be empty")

    original_length = min(len(column) for column in columns)
    trimmed = truncate_to_blocks(columns, s)
    rows_used = len(trimmed[0])
    if rows_used == 0:
        raise ValueError(f"not enough rows ({original_length}) for s={s}")

    n_columns = len(trimmed)
    block_size = rows_used // s
    half = s // 2

    aggregates = [block_aggregates(column, s) for column in trimmed]
    totals: list[Aggregate] = [
        (
            sum(a[0] for a in column_aggregates),
            sum((a[1] for a in column_aggregates), Decimal(0)),
            sum((a[2] for a in column_aggregates), Decimal(0)),
        )
        for column_aggregates in aggregates
    ]

    def _join(block_indices: Sequence[int], column: Sequence[Decimal]) -> list[Decimal]:
        joined: list[Decimal] = []
        for block_index in sorted(block_indices):
            joined.extend(column[block_index * block_size : (block_index + 1) * block_size])
        return joined

    logits: list[Decimal] = []
    is_selected: list[Decimal] = []
    oos_selected: list[Decimal] = []
    oos_means: list[Decimal] = []

    for training_blocks in combinations(range(s), half):
        training = set(training_blocks)
        testing_blocks = [i for i in range(s) if i not in training]

        if metric is None:
            is_performance = []
            oos_performance = []
            for column_index in range(n_columns):
                column_aggregates = aggregates[column_index]
                count = 0
                total = Decimal(0)
                downside = Decimal(0)
                for block_index in training_blocks:
                    block = column_aggregates[block_index]
                    count += block[0]
                    total += block[1]
                    downside += block[2]
                whole = totals[column_index]
                is_performance.append(sortino_from_aggregates(count, total, downside))
                # The complement, for free -- no second pass over the blocks.
                oos_performance.append(
                    sortino_from_aggregates(
                        whole[0] - count, whole[1] - total, whole[2] - downside
                    )
                )
        else:
            # Slow path: rebuild the actual series, joined in ORIGINAL block order.
            is_performance = [metric(_join(training_blocks, c)) for c in trimmed]
            oos_performance = [metric(_join(testing_blocks, c)) for c in trimmed]

        is_ranks = _ranks(is_performance)
        oos_ranks = _ranks(oos_performance)
        best = is_ranks.index(n_columns)  # the IS-best column; never returned to the caller

        omega = Decimal(oos_ranks[best]) / Decimal(n_columns + 1)
        logits.append((omega / (Decimal(1) - omega)).ln())
        is_selected.append(is_performance[best])
        oos_selected.append(oos_performance[best])
        oos_means.append(sum(oos_performance, Decimal(0)) / n_columns)

    n_combinations = len(logits)
    phi = Decimal(sum(1 for value in logits if value <= 0)) / Decimal(n_combinations)
    slope, intercept = _ols_slope(is_selected, oos_selected)
    prob_loss = Decimal(sum(1 for value in oos_selected if value < 0)) / Decimal(n_combinations)
    first, second = _dominance(oos_selected, oos_means)

    return PBOResult(
        pbo=phi,
        n_combinations=n_combinations,
        n_columns=n_columns,
        n_blocks=s,
        rows_used=rows_used,
        rows_dropped=original_length - rows_used,
        logits=logits,
        is_performance=is_selected,
        oos_performance=oos_selected,
        degradation_slope=slope,
        degradation_intercept=intercept,
        prob_loss=prob_loss,
        dominance_1st=first,
        dominance_2nd=second,
    )
