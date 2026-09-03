"""Proportional effective spread, estimated from daily highs and lows -- issue #371.

⚠️ **This module measures. It does not gate, size, or price anything on the live path.**
Nothing here is imported by `execution/` or `strategy/`; it exists so a cost claim can be
checked rather than assumed.

WHY AN ESTIMATOR AT ALL. Phase C of the Alpaca PRD (§6.3, O4) needs one number keel has never
had: what a round trip actually costs on a venue whose commission is zero. Two of the three
components were already exact -- Alpaca's commission is $0 and the sell-side regulatory
pass-throughs are published formulas (`keel_broker_alpaca.fees`). The third, the SPREAD, is
the one that dominates, and keel caches OHLCV bars, never quotes. Buying a quote history to
answer it would make the number unreproducible by anyone reading the write-up.

Corwin & Schultz (2012), "A Simple Way to Estimate Bid-Ask Spreads from Daily High and Low
Prices", Journal of Finance 67(2), 719-759, recovers the spread from highs and lows alone.
The insight: a day's high-low RANGE contains both the true variance of the security and the
spread, but variance scales with the length of the interval while the spread does not. So two
single-day ranges (two units of variance, two spreads) compared against one two-day range (two
units of variance, ONE spread) isolate the spread.

THE POINT OF USING IT HERE IS THAT IT APPLIES TO BOTH ASSET CLASSES, from data already on
disk, with ONE estimator and no per-venue tuning. A cost comparison between crypto and
equities measured two different ways would be an artifact of the methods.

THE OVERNIGHT-GAP ADJUSTMENT IS LOAD-BEARING, NOT A REFINEMENT. Section I.B of the paper
subtracts any overnight jump before estimating, because a gap widens the two-day range without
any spread having been paid. Equities gap every night; crypto trades continuously and barely
gaps at all. An unadjusted estimator would therefore inflate the EQUITIES side of exactly the
comparison this module exists to make -- and inflating equity costs is the direction that
flatters keel's existing crypto-heavy conclusion. A measurement must never be trusted in the
direction of its author's prior, so the adjustment is on by default and the count of adjusted
pairs is reported beside every estimate.

WHAT IT IS NOT. This is an estimator with real dispersion, not a quote feed. Corwin & Schultz
report it tracking TAQ spreads closely in the cross-section while being noisy for any single
security-month; negative two-day estimates are common, since no venue charges a negative spread.

**HOW THE NEGATIVES ARE HANDLED DECIDES THE ANSWER, BY A FACTOR OF TWENTY.** This is the single
most consequential choice in the module, and the obvious treatment is the wrong one:

* *Floor every pair, then average the survivors.* Tempting, and wrong. On a series whose true
  spread is small relative to its volatility, the two-day estimate is symmetric noise about
  zero; keeping the positive half and discarding the negative half makes the mean converge on
  E[max(X,0)] > 0. It reports a spread that is not there, and reports a LARGER one the more
  volatile the series is. Measured on keel's own equity candles this reads 41.8bp for MSFT --
  roughly twenty times the penny spread that name actually quotes.
* *Average within a month, then floor the monthly mean* -- Corwin & Schultz's own procedure,
  and the default here. The negatives cancel against the positives inside the block, so noise
  averages out instead of accumulating. The same MSFT series reads 1.9bp, which is the right
  order of magnitude for a mega-cap.

Both are reachable (`block_size=None` selects the biased one) because the comparison between
them is itself a finding, and `raw_spread_pct` reports the mean with no flooring anywhere --
the only figure that can come out negative, and therefore the only one able to say "this series
carries no measurable spread" out loud. Read `negative_pairs` as the noise diagnostic it is: a
series that floors half its pairs has not been measured, it has been sampled.

ONE LIMITATION WORTH KNOWING BEFORE READING ANY NUMBER THIS PRODUCES. The gap adjustment
shifts day 2 to sit exactly ON TOP of day 1, which is the most trend-like geometry two ranges
can have, so a pure gap-and-continue day estimates negative and floors to zero. The estimate
for a gapping series is therefore carried by its OVERLAPPING pairs, and `gap_adjusted_pairs`
is the honest sample-size caveat: a series that gaps constantly rests on fewer effective
observations than `pairs` suggests. This does not bias the estimate up -- gap days contribute
zero rather than noise -- but it does widen its uncertainty on the equities side.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from keel_core.types import Candle

#: 3 - 2*sqrt(2), the constant in Corwin & Schultz eq. (14)/(18). Named because it appears
#: three times and a bare `0.1716` in the arithmetic is unreadable.
_K: float = 3.0 - 2.0 * math.sqrt(2.0)

#: The estimate's resolution. Ten places keeps sub-basis-point detail (1bp = 1e-4) without
#: pretending to precision the estimator does not have.
_QUANTUM: Decimal = Decimal("1e-10")

#: Basis-point figures are for the write-up, where two decimals is already generous.
_BP_QUANTUM: Decimal = Decimal("0.01")

#: Pairs per averaging block. 21 trading days is the month Corwin & Schultz aggregate over.
#: It is a bias/variance dial, not a free parameter: smaller blocks floor more often and drift
#: back toward the per-pair bias; larger ones average across regimes that differ.
MONTHLY_BLOCK: int = 21


@dataclass(frozen=True)
class SpreadEstimate:
    """A series' estimated proportional spread, with the diagnostics needed to judge it.

    `spread_pct` is the FULL quoted spread as a fraction of price (0.0012 = 12bp). One leg of
    a trade crosses half of it, which is what `half_spread_pct` reports and what maps onto
    keel's slippage assumption -- `strategy/backtest.slippage_for_quote_volume` is likewise a
    one-way number.
    """

    spread_pct: Decimal
    raw_spread_pct: Decimal
    pairs: int
    negative_pairs: int
    gap_adjusted_pairs: int
    blocks: int

    @property
    def half_spread_pct(self) -> Decimal:
        """The one-way cost: crossing from the mid to the touch."""
        return self.spread_pct / 2

    @property
    def raw_spread_bp(self) -> Decimal:
        """The unfloored mean, in basis points. Negative means "no measurable spread"."""
        return (self.raw_spread_pct * 10000).quantize(_BP_QUANTUM)

    @property
    def spread_bp(self) -> Decimal:
        return (self.spread_pct * 10000).quantize(_BP_QUANTUM)

    @property
    def half_spread_bp(self) -> Decimal:
        return (self.half_spread_pct * 10000).quantize(_BP_QUANTUM)

    @property
    def negative_pair_share(self) -> Decimal:
        """How much of the series estimated a negative spread -- the noise diagnostic.

        NOT "how much floored to zero": under the default block aggregation a negative PAIR
        does not floor, it averages against the positives inside its block, and only a
        negative BLOCK mean floors. (Under `block_size=None` the two coincide, which is
        exactly the conflation that makes that variant biased.) Read it as signal-to-noise:
        approaching half means the estimator cannot resolve a spread in this series at this
        frequency, whichever aggregation is then applied."""
        if self.pairs == 0:
            return Decimal("0")
        return (Decimal(self.negative_pairs) / Decimal(self.pairs)).quantize(Decimal("0.0001"))


def _usable(candle: Candle) -> bool:
    """A bar the logarithm can take. A zero-range bar (H == L) is excluded deliberately: it
    means halted or untraded, not a zero spread, and it collapses both beta and gamma to zero
    which would return a spurious 0.0."""
    return candle.low > 0 and candle.high > candle.low


def corwin_schultz_pair(
    first: Candle, second: Candle, *, adjust_overnight_gap: bool = True
) -> float | None:
    """The two-day estimate, Corwin & Schultz eq. (14)-(18). `None` when the pair is unusable.

    Returned as a raw float and NOT floored at zero: the flooring is the aggregator's
    decision, and how often a pair goes negative is a diagnostic the caller needs. Float
    rather than Decimal because the formula is logarithms and exponentials throughout; the
    conversion to Decimal happens once, at the aggregate.
    """
    if not _usable(first) or not _usable(second):
        return None

    high_1, low_1 = float(first.high), float(first.low)
    high_2, low_2 = float(second.high), float(second.low)

    if adjust_overnight_gap:
        # Section I.B: a gap moves day 2's whole range without any spread being paid. Shift
        # day 2 back onto day 1's range -- the shift cancels inside every log ratio of day 2's
        # own bar, so it changes ONLY the two-day range, which is precisely the term the gap
        # contaminated.
        if low_2 > high_1:
            gap = low_2 - high_1
            high_2, low_2 = high_2 - gap, low_2 - gap
        elif high_2 < low_1:
            gap = low_1 - high_2
            high_2, low_2 = high_2 + gap, low_2 + gap

    beta = math.log(high_1 / low_1) ** 2 + math.log(high_2 / low_2) ** 2
    gamma = math.log(max(high_1, high_2) / min(low_1, low_2)) ** 2
    alpha = (math.sqrt(2.0 * beta) - math.sqrt(beta)) / _K - math.sqrt(gamma / _K)
    return 2.0 * (math.exp(alpha) - 1.0) / (1.0 + math.exp(alpha))


def corwin_schultz_spread(
    candles: Sequence[Candle],
    *,
    adjust_overnight_gap: bool = True,
    block_size: int | None = MONTHLY_BLOCK,
) -> SpreadEstimate | None:
    """Estimate a series' proportional spread from consecutive high-low pairs.

    `block_size` chooses the aggregation, and the choice is worth twenty-fold on a quiet
    series -- see the module docstring. The default averages within blocks of that many pairs
    and floors each BLOCK mean; `None` floors each PAIR instead, which is the biased variant,
    retained so the bias stays demonstrable rather than folklore.

    `None` (the return, not the argument) means NOTHING WAS MEASURABLE -- fewer than two bars,
    or no usable pair. That is a different statement from an estimate of zero, and a caller
    must not read the two the same way: one says the venue is cheap, the other says the
    question was not answered.

    **PRECONDITION, UNCHECKED: `candles` must be CONSECUTIVE bars of one series.** `ts` is
    never read. Every adjacent pair is treated as a two-day pair, so a series with a hole in
    it -- a fetch gap, two series concatenated, a filtered subset -- prices that hole as an
    overnight move and returns a number with no warning attached. Blocks are likewise formed
    over usable PAIRS, not over calendar time, so a gappy series' "month" can span far more
    than a month. Checking adjacency here would need a granularity this function is not given;
    the caller has it, and owns this.
    """
    if block_size is not None and block_size < 1:
        # Caught by name rather than left to the slice arithmetic: 0 reached `range(step=0)`
        # and negatives produced an empty block list to divide by. `None` is the one
        # non-positive-integer value with a meaning, and it keeps it.
        raise ValueError(f"block_size must be a positive integer or None; got {block_size!r}")

    estimates: list[float] = []
    negative = 0
    gapped = 0
    for first, second in zip(candles, candles[1:], strict=False):
        estimate = corwin_schultz_pair(first, second, adjust_overnight_gap=adjust_overnight_gap)
        if estimate is None:
            continue
        estimates.append(estimate)
        if adjust_overnight_gap and (second.low > first.high or second.high < first.low):
            gapped += 1
        if estimate < 0:
            negative += 1
    if not estimates:
        return None

    raw = sum(estimates) / len(estimates)
    if block_size is None:
        floored = sum(e for e in estimates if e > 0) / len(estimates)
        blocks = 0
    else:
        chunks = [estimates[i : i + block_size] for i in range(0, len(estimates), block_size)]
        means = [sum(chunk) / len(chunk) for chunk in chunks]
        floored = sum(max(m, 0.0) for m in means) / len(means)
        blocks = len(means)

    return SpreadEstimate(
        spread_pct=Decimal(repr(floored)).quantize(_QUANTUM),
        raw_spread_pct=Decimal(repr(raw)).quantize(_QUANTUM),
        pairs=len(estimates),
        negative_pairs=negative,
        gap_adjusted_pairs=gapped,
        blocks=blocks,
    )


__all__ = ["MONTHLY_BLOCK", "SpreadEstimate", "corwin_schultz_pair", "corwin_schultz_spread"]
