"""Monte Carlo and candle-bootstrap resampling (#441): is the equity curve an outlier?

This module measures. It does not score, gate, or change anything. It is report-only
evidence for a question the sibling research modules deliberately do not touch: the live
reconstruction stands at a 14.9% win rate against a ~14.9% break-even, and the honest
question that raises is not "is the family net positive?" (`significance.py` answers that,
and its answer so far is *not distinguishable from zero*) nor "was the selection process
overfit?" (`cscv.py`'s PBO) -- it is "did one lucky PATH produce this curve?". Two nulls:

* **Trade reshuffle** (`reshuffle`): the SAME trades in different orders. Ordering luck is
  what this null can reveal -- and its endpoint is honest by construction: a permutation of
  a multiset sums to the same number, so every reshuffled path ends at the observed final
  equity and the percentile reads exactly 1/2 (ties count half). The report SAYS this
  rather than inventing spread; the ordering luck itself lives in the path between start
  and end (drawdown depth, time underwater), which `equity_curve` exposes to any caller
  who wants it.
* **Moving-block candle bootstrap** (`moving_block_bootstrap`): consecutive blocks of real
  candles resampled with wrap-around to the SAME total length, then re-run through the
  backtest. This is the valuable null, because it preserves the local autocorrelation a
  naive IID resample would destroy -- exactly the structure a trend follower like
  `turtle_breakout` trades. Its honesty costs are stated, not hidden: block STITCHING
  keeps each sampled bar's OHLCV verbatim, so the seam between two blocks is a price
  discontinuity the real series never had, and timestamps are re-anchored monotonically
  from `candles[0].ts` so downstream indicators see a well-formed series rather than the
  sampled bars' original (jumping) clocks.

**Determinism.** No global random anywhere: every entry point takes a `seed` and drives its
own `random.Random` instance, paths drawn sequentially so one seed reproduces the whole
call bit-for-bit. A resample that cannot reproduce itself is not evidence.

`Decimal` for every money quantity; stdlib only.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from keel.types import Candle

__all__ = [
    "MonteCarloReport",
    "equity_curve",
    "final_equities",
    "median",
    "moving_block_bootstrap",
    "percentile_of",
    "reshuffle",
]


def reshuffle(pnls: Sequence[Decimal], n_paths: int, seed: int) -> list[list[Decimal]]:
    """`n_paths` shuffled copies of `pnls` -- the same trades, different orders.

    One `random.Random(seed)` instance is created per call and the paths are drawn
    sequentially from it, so the WHOLE call is deterministic under the seed (same seed ->
    identical paths list, twice). Each path is a permutation of the input multiset, never a
    resample with replacement: the null is ordering luck, and replacing trades would change
    the question to a different one this module does not ask.
    """
    if n_paths < 1:
        raise ValueError(f"n_paths must be >= 1, got {n_paths}")
    rng = random.Random(seed)
    paths: list[list[Decimal]] = []
    for _ in range(n_paths):
        path = list(pnls)  # a copy: the caller's sequence is never touched
        rng.shuffle(path)
        paths.append(path)
    return paths


def equity_curve(pnls: Sequence[Decimal], start: Decimal) -> list[Decimal]:
    """The cumulative equity curve: `[start, start + pnl_0, ...]`, length `len(pnls) + 1`.

    Additive (fixed notional per trade), matching the backtest engine's 1-unit sizing --
    not compounded, so a reshuffled multiset reaches the same endpoint by construction and
    the curve's shape between the endpoints is where ordering luck shows.
    """
    curve = [start]
    equity = start
    for pnl in pnls:
        equity += pnl
        curve.append(equity)
    return curve


def final_equities(paths: Sequence[Sequence[Decimal]], start: Decimal) -> list[Decimal]:
    """The last point of each path's equity curve -- `[equity_curve(p, start)[-1]]`."""
    return [equity_curve(path, start)[-1] for path in paths]


def median(values: Sequence[Decimal]) -> Decimal:
    """Exact-Decimal median: the middle of the sorted values for odd length, the mean of the
    two middles for even length. Raises on empty input rather than inventing a zero."""
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        raise ValueError("median of an empty distribution is undefined")
    middle = n // 2
    if n % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal(2)


def percentile_of(observed: Decimal, distribution: Sequence[Decimal]) -> Decimal:
    """Where `observed` sits inside `distribution`, as an exact Decimal in [0, 1].

    Convention: the fraction of the distribution STRICTLY BELOW `observed`, with TIES
    COUNTING HALF -- `(below + ties/2) / n`. So a value below everything is 0, above
    everything is 1, and the all-ties shape (every reshuffled path ends at the observed
    final equity) is exactly 1/2, never an invented p-value.
    """
    n = len(distribution)
    if n == 0:
        raise ValueError("percentile against an empty distribution is undefined")
    below = sum(1 for value in distribution if value < observed)
    ties = sum(1 for value in distribution if value == observed)
    return (Decimal(below) + Decimal(ties) / Decimal(2)) / Decimal(n)


def moving_block_bootstrap(
    candles: Sequence[Candle],
    *,
    block_len: int,
    n_paths: int,
    seed: int,
    step_sec: int,
) -> list[list[Candle]]:
    """Resample consecutive blocks of `candles` into `n_paths` paths of the SAME total length.

    Each block starts at a uniformly drawn index and runs `block_len` bars with wrap-around
    (the tail may continue into the head of the series WITHIN a block), so local
    autocorrelation survives resampling -- the null a trend follower's candles deserve; an
    IID bar resample would destroy exactly the structure the rule trades. Timestamps are
    re-anchored monotonically from `candles[0].ts` stepping `step_sec`, because downstream
    indicators read clocks, and the sampled bars' original timestamps would jump backwards
    at every block seam. OHLCV are kept VERBATIM per sampled bar: the block-stitching
    artifact (a seam discontinuity between the close of one block and the open of the next)
    is real, documented here, and visible to the backtest rather than smoothed away.

    Every output bar is a FRESH `Candle`; the input sequence and its bars are never aliased
    or mutated. Deterministic under `seed` (one `random.Random(seed)`, paths drawn
    sequentially).
    """
    if not candles:
        raise ValueError("cannot bootstrap an empty candle series")
    if block_len < 1:
        raise ValueError(f"block_len must be >= 1, got {block_len}")
    if n_paths < 1:
        raise ValueError(f"n_paths must be >= 1, got {n_paths}")
    if step_sec < 1:
        raise ValueError(f"step_sec must be >= 1, got {step_sec}")

    n = len(candles)
    base_ts = candles[0].ts
    rng = random.Random(seed)
    paths: list[list[Candle]] = []
    for _ in range(n_paths):
        bars: list[Candle] = []
        while len(bars) < n:
            start = rng.randrange(n)
            for offset in range(block_len):
                if len(bars) >= n:
                    break  # the last block is truncated to the original length
                src = candles[(start + offset) % n]
                bars.append(
                    Candle(
                        ts=base_ts + len(bars) * step_sec,
                        open=src.open,
                        high=src.high,
                        low=src.low,
                        close=src.close,
                        volume=src.volume,
                    )
                )
        paths.append(bars)
    return paths


@dataclass(frozen=True)
class MonteCarloReport:
    """One equity curve read against one resampled distribution.

    Every money quantity is exact `Decimal`. `percentile` follows `percentile_of`'s
    convention (strictly-below fraction, ties half), and in trades mode it is exactly 1/2
    BY CONSTRUCTION -- the same multiset cannot sum differently -- which `render_lines`
    states rather than lets read as a verdict.
    """

    mode: str  # "trades" | "candles"
    n_paths: int
    seed: int
    start: Decimal
    n_trades: int
    observed_final: Decimal
    distribution_min: Decimal
    distribution_median: Decimal
    distribution_max: Decimal
    percentile: Decimal
    #: Candles mode only; `None` in trades mode, which has no block structure.
    block_len: int | None = None

    def render_lines(self) -> list[str]:
        """The report, always naming mode, seed, n_paths, the observed final, the resampled
        `[min..max]`, the percentile -- and the refusal: what this does NOT answer."""
        head = f"monte-carlo ({self.mode} mode"
        if self.block_len is not None:
            head += f", block_len={self.block_len}"
        head += f"): {self.n_paths} paths, seed {self.seed}"
        lines = [
            head,
            f"  observed final equity {self.observed_final} "
            f"(start {self.start}, {self.n_trades} closed trades)",
            "  resampled final equity: "
            f"min {self.distribution_min} / median {self.distribution_median} "
            f"/ max {self.distribution_max}",
            "  observed percentile: "
            f"{self.percentile} (fraction of paths strictly below; ties count half)",
        ]
        if self.mode == "trades":
            lines.append(
                "  trades mode: every path ends at the SAME final equity by construction -- "
                "reordering a multiset cannot change its sum, so the percentile is exactly "
                "1/2; the ordering luck this null exposes lives in the path between (see "
                "equity_curve), not the endpoint"
            )
        else:
            lines.append(
                "  candles mode: blocks are stitched from real bars, so each seam is a price "
                "discontinuity the real series never had -- the null keeps local structure, "
                "not global narrative"
            )
        lines.append(
            "  a percentile here is path luck, not evidence of edge -- and it does not "
            "answer significance at all: whether the edge is distinguishable from zero is "
            "keel/research/significance.py's question"
        )
        return lines
