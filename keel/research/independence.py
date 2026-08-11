"""Measure whether two rules (or two horizons of one rule) are actually independent — §80.16.

⚠️ **§73.5 makes this NON-OPTIONAL.** Correlated rules inflate `N` without adding independent
evidence: two rules that fire together are one rule counted twice, consuming trials budget twice
while contributing one observation's worth of information. At `N <= 3` on a 5-year window,
**adding a correlated second rule class is strictly worse than adding nothing** — it spends
budget, inflates apparent trade count, and leaves knowability exactly where it was.

§80.16's finding is that nobody has published this measurement: four papers, 30,000+ tested
rules, two asset classes, 114 years, and **zero between-family correlation measurements**. There
is no paper to cite, so it is a build task.

The five measurements, verbatim from §80.16:

    1. daily POSITION vector  s_t in {0, 1}   (long-only => binary, not {-1, 0, 1})
    2. Overlap    = #{t : sA=1 AND sB=1} / #{t : sA=1 OR sB=1}     (Jaccard)
    3. Signal corr = Pearson correlation of the two position vectors
    4. Trade-timing = for each A-entry, bars to the nearest B-entry; report the distribution
    5. Return corr = correlation of the two rules' daily P&L series

Counting, means and one covariance over equal-length lists. Pure stdlib, exact under `Decimal`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class IndependenceReport:
    """How much two signal streams overlap. Lower is more independent."""

    n_periods: int
    a_active: int
    b_active: int
    both_active: int
    jaccard: Decimal
    position_correlation: Decimal
    pnl_correlation: Decimal
    entry_distances: list[int]
    median_entry_distance: int | None


def pearson(xs: Sequence[Decimal], ys: Sequence[Decimal]) -> Decimal:
    """Pearson correlation. Returns 0 when either series is constant.

    A constant series has no variance to correlate against, so the coefficient is undefined
    rather than zero -- but every caller here wants "no measurable relationship", and raising
    would abort a whole matrix over one degenerate pair. Documented, not silent.

    **Public deliberately (issue #208).** It was `_pearson` while `compare()` was its only
    caller. `research/cts_factors.py` measures correlation between BOOLEAN factor-presence
    vectors, where the fast exact form is the phi coefficient computed from 2x2 contingency
    counts rather than a running covariance -- but phi and Pearson-on-{0,1} are the same
    number, so this function is that module's test ORACLE. An oracle imported through a
    private name is an oracle one refactor away from silently disappearing, and importing
    `_pearson` across modules is exactly the wart `sim/portfolio_sim.py` already carries
    against `engine._assemble_cts_context`. Promoted rather than duplicated.
    """
    n = len(xs)
    if n < 2 or n != len(ys):
        return Decimal(0)
    mean_x = sum(xs, Decimal(0)) / n
    mean_y = sum(ys, Decimal(0)) / n
    var_x = sum(((x - mean_x) ** 2 for x in xs), Decimal(0))
    var_y = sum(((y - mean_y) ** 2 for y in ys), Decimal(0))
    if var_x <= 0 or var_y <= 0:
        return Decimal(0)
    covariance = sum(((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)), Decimal(0))
    return covariance / (var_x.sqrt() * var_y.sqrt())


def jaccard(a: Sequence[int], b: Sequence[int]) -> Decimal:
    """`#{both in market} / #{either in market}` (§80.16 step 2).

    Returns 0 when neither series is ever in the market -- two rules that never trade do not
    overlap, and calling that "undefined" would be pedantry in a report.
    """
    intersection = sum(1 for x, y in zip(a, b) if x and y)
    union = sum(1 for x, y in zip(a, b) if x or y)
    return Decimal(intersection) / Decimal(union) if union else Decimal(0)


def entry_distances(a_entries: Sequence[int], b_entries: Sequence[int]) -> list[int]:
    """For each A entry, the distance in PERIODS to the nearest B entry (§80.16 step 4).

    Small distances mean the two rules are firing on the same events at slightly different
    times, which is the failure mode a raw correlation on sparse vectors can miss.
    """
    if not a_entries or not b_entries:
        return []
    ordered = sorted(b_entries)
    out: list[int] = []
    for entry in a_entries:
        out.append(min(abs(entry - other) for other in ordered))
    return out


def compare(
    a_positions: Sequence[int],
    b_positions: Sequence[int],
    a_pnl: Sequence[Decimal],
    b_pnl: Sequence[Decimal],
    a_entries: Sequence[int] | None = None,
    b_entries: Sequence[int] | None = None,
) -> IndependenceReport:
    """Run all five §80.16 measurements over two aligned, equal-length series.

    `a_positions`/`b_positions` are daily {0,1} in-market flags; `a_pnl`/`b_pnl` are daily P&L;
    `a_entries`/`b_entries` are period INDICES at which each rule opened. Entries default to
    the rising edges of the position vectors, which is what they are in every current caller.
    """
    length = min(len(a_positions), len(b_positions), len(a_pnl), len(b_pnl))
    a_positions, b_positions = list(a_positions[:length]), list(b_positions[:length])
    a_pnl, b_pnl = list(a_pnl[:length]), list(b_pnl[:length])

    if a_entries is None:
        a_entries = _rising_edges(a_positions)
    if b_entries is None:
        b_entries = _rising_edges(b_positions)

    distances = entry_distances(a_entries, b_entries)
    return IndependenceReport(
        n_periods=length,
        a_active=sum(a_positions),
        b_active=sum(b_positions),
        both_active=sum(1 for x, y in zip(a_positions, b_positions) if x and y),
        jaccard=jaccard(a_positions, b_positions),
        position_correlation=pearson(
            [Decimal(v) for v in a_positions], [Decimal(v) for v in b_positions]
        ),
        pnl_correlation=pearson(a_pnl, b_pnl),
        entry_distances=distances,
        median_entry_distance=_median(distances),
    )


def _rising_edges(positions: Sequence[int]) -> list[int]:
    edges: list[int] = []
    previous = 0
    for index, value in enumerate(positions):
        if value and not previous:
            edges.append(index)
        previous = value
    return edges


def _median(values: Sequence[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) // 2
