"""The Corwin-Schultz high-low spread estimator (issue #371).

Phase C of the Alpaca PRD needs one number keel has never had: **what a round trip actually
costs on a venue whose commission is zero.** The regulatory pass-throughs are exact and already
modelled (`keel_broker_alpaca.fees`); the spread is not, and keel caches OHLCV bars, never
quotes. Corwin-Schultz recovers a proportional effective spread from daily highs and lows
alone, which makes it the one estimator that can be applied to BOTH asset classes from data
already on disk -- the like-for-like comparison the cost-distortion claim rests on.

The load-bearing test is `test_the_overnight_gap_adjustment_is_applied`: equities gap overnight
and crypto (24/7) essentially does not, so an unadjusted estimator would inflate the equity side
of exactly the comparison this exists to make. The bias would run in the direction that
FLATTERS keel's crypto-heavy prior, which is the direction a measurement must never be trusted
in.
"""

from __future__ import annotations

import math
import random
from decimal import Decimal

import pytest
from keel_core.types import Candle

from keel.research.spread import (
    MONTHLY_BLOCK,
    SpreadEstimate,
    corwin_schultz_pair,
    corwin_schultz_spread,
)


def _c(high: str, low: str, ts: int = 0, close: str | None = None) -> Candle:
    """A candle carrying only what the estimator reads: its high and its low."""
    hi, lo = Decimal(high), Decimal(low)
    mid = close if close is not None else str((hi + lo) / 2)
    return Candle(ts=ts, open=mid, high=hi, low=lo, close=Decimal(mid), volume=Decimal("1000"))


# --- the pair estimator: known answers, computed from the published formula ------------------


def test_a_two_day_pair_reproduces_the_published_formula() -> None:
    """Corwin & Schultz (2012) eq. (14)-(18), evaluated by hand on a wide-range pair."""
    got = corwin_schultz_pair(_c("101", "99"), _c("101.5", "99.5"))
    assert got is not None
    assert math.isclose(float(got), 0.0079088925, rel_tol=1e-6)


def test_a_tighter_range_estimates_a_tighter_spread() -> None:
    wide = corwin_schultz_pair(_c("101", "99"), _c("101.5", "99.5"))
    tight = corwin_schultz_pair(_c("100.2", "99.8"), _c("100.3", "99.9"))
    assert wide is not None and tight is not None
    assert math.isclose(float(tight), 0.0015849918, rel_tol=1e-6)
    assert tight < wide


def test_a_trending_pair_estimates_a_negative_spread() -> None:
    """A strong two-day trend makes the two-day range much wider than the daily ranges, which
    drives alpha negative. The estimator does NOT hide this at the pair level -- the flooring
    is the aggregator's decision, and counting how often it happens is a diagnostic."""
    got = corwin_schultz_pair(_c("105", "100"), _c("110", "104"))
    assert got is not None
    assert got < 0
    assert math.isclose(float(got), -0.0506145009, rel_tol=1e-6)


def test_a_zero_range_bar_is_not_an_estimate() -> None:
    """H == L means no intraday range at all -- a halted or untraded bar, not a zero spread.
    log(1) = 0 collapses beta AND gamma, and the formula would return a spurious 0.0."""
    assert corwin_schultz_pair(_c("100", "100"), _c("101", "99")) is None
    assert corwin_schultz_pair(_c("101", "99"), _c("100", "100")) is None


def test_a_nonpositive_price_is_not_an_estimate() -> None:
    """Guards the logarithm rather than raising: one bad cached bar must not end a sweep."""
    assert corwin_schultz_pair(_c("0", "0"), _c("101", "99")) is None
    assert corwin_schultz_pair(_c("101", "99"), _c("-1", "-2")) is None


def test_a_low_above_its_own_high_is_not_an_estimate() -> None:
    """An inverted bar is corrupt data, never a tradeable range."""
    assert corwin_schultz_pair(_c("99", "101"), _c("101", "99")) is None


# --- the overnight gap adjustment ------------------------------------------------------------


def test_the_overnight_gap_adjustment_is_applied() -> None:
    """Corwin & Schultz section I.B: when day 2's LOW sits above day 1's HIGH, the two-day
    range contains an overnight jump that no spread produced. The gap is subtracted from day
    2 before the estimate, or the jump is priced as if it were a spread.

    Day 1 [99, 101], day 2 [110, 112]: a 9-point gap up on a 2-point daily range. Unadjusted,
    the two-day range spans 13 points and the estimate is deeply negative; adjusted, day 2
    becomes [101, 103] and the overnight jump is gone from the two-day range."""
    d1, d2 = _c("101", "99"), _c("112", "110")
    adjusted = corwin_schultz_pair(d1, d2)
    unadjusted = corwin_schultz_pair(d1, d2, adjust_overnight_gap=False)
    assert adjusted is not None and unadjusted is not None
    # The gap contaminated the estimate downward by an order of magnitude; removing it is
    # most of the correction.
    assert unadjusted < adjusted
    assert float(unadjusted) < 8 * float(adjusted)
    # Shifting day 2 down by the gap must give EXACTLY the same answer as adjusting it -- this
    # is the assertion that pins the arithmetic, not the sign.
    shifted = corwin_schultz_pair(d1, _c("103", "101"), adjust_overnight_gap=False)
    assert shifted is not None
    assert math.isclose(float(adjusted), float(shifted), rel_tol=1e-12)


def test_a_pure_gap_still_floors_to_zero_after_adjustment() -> None:
    """An honest limitation, pinned so nobody later reads a gap-heavy series as a cheap one.

    The adjustment shifts day 2 to sit EXACTLY on top of day 1, which is the most
    trend-like geometry two ranges can have, so a pure gap-and-continue day estimates negative
    and floors to zero. Corwin & Schultz's estimate on a gapping series is therefore carried by
    its OVERLAPPING pairs; the gap days contribute nothing rather than contributing noise. That
    is why `gap_adjusted_pairs` is reported -- a series that gaps constantly is a series whose
    estimate rests on a smaller sample than `pairs` suggests."""
    adjusted = corwin_schultz_pair(_c("101", "99"), _c("112", "110"))
    assert adjusted is not None and adjusted < 0


def test_the_gap_adjustment_works_downward_too() -> None:
    """A gap DOWN (day 2's high below day 1's low) is the same distortion, mirrored."""
    d1, d2 = _c("101", "99"), _c("90", "88")
    adjusted = corwin_schultz_pair(d1, d2)
    # The 9-point gap (99 - 90) is added back to day 2, lifting [88, 90] to [97, 99].
    shifted = corwin_schultz_pair(d1, _c("99", "97"), adjust_overnight_gap=False)
    assert adjusted is not None and shifted is not None
    assert math.isclose(float(adjusted), float(shifted), rel_tol=1e-12)


def test_overlapping_days_are_not_gap_adjusted() -> None:
    """Ranges that overlap have no overnight jump to remove; touching the estimate there would
    be the estimator inventing an adjustment the paper does not make."""
    d1, d2 = _c("101", "99"), _c("101.5", "99.5")
    assert corwin_schultz_pair(d1, d2) == corwin_schultz_pair(d1, d2, adjust_overnight_gap=False)


# --- the aggregate: WHICH average, and why it is not the obvious one ---------------------


def test_the_default_averages_within_blocks_before_flooring() -> None:
    """Corwin & Schultz's own procedure: average the two-day estimates within a month, THEN
    set a negative MONTHLY average to zero. Not: floor every pair and average the survivors.

    The difference is not cosmetic -- see `test_flooring_each_pair_biases_a_quiet_series_up`.
    Here, two pairs in one block whose mean is negative give zero, where per-pair flooring
    would have kept the positive one and reported half of it."""
    candles = [_c("101", "99", ts=0), _c("101.5", "99.5", ts=1), _c("110", "104", ts=2)]
    got = corwin_schultz_spread(candles)
    assert got is not None
    assert got.pairs == 2
    assert got.blocks == 1
    assert got.spread_pct == Decimal("0")


def test_flooring_each_pair_biases_a_quiet_series_up() -> None:
    """THE LOAD-BEARING TEST. On a series with NO spread at all -- a pure random walk whose
    highs and lows come only from volatility -- the two-day estimate is symmetric noise around
    zero. Flooring each pair keeps the positive half and discards the negative half, so its
    mean converges on E[max(X,0)] > 0: it reports a spread that does not exist, and reports a
    BIGGER one the more volatile the series is.

    That is not a hypothetical. On real mega-cap equities the two aggregations differ by ~20x,
    and only the block figure lands anywhere near the penny spreads those names actually quote.
    Getting this wrong would have priced a commission-free venue at 20x its true cost."""
    rng = random.Random(20260902)
    price, candles = 100.0, []
    for i in range(2000):
        price *= math.exp(rng.gauss(0.0, 0.02))
        # High/low from intraday volatility ONLY -- no bid-ask bounce, no spread.
        hi = price * math.exp(abs(rng.gauss(0.0, 0.01)))
        lo = price * math.exp(-abs(rng.gauss(0.0, 0.01)))
        candles.append(_c(f"{hi:.6f}", f"{lo:.6f}", ts=i))

    naive = corwin_schultz_spread(candles, block_size=None)
    block = corwin_schultz_spread(candles)
    assert naive is not None and block is not None
    assert naive.spread_bp > 5 * block.spread_bp
    # And the block estimate is near zero, which is the truth for this series.
    assert block.spread_bp < Decimal("10")


def test_the_naive_per_pair_flooring_is_still_reachable() -> None:
    """Kept, not deleted: the biased variant is what makes the bias demonstrable, and the
    write-up quotes both figures. `block_size=None` selects it explicitly."""
    candles = [_c("101", "99", ts=0), _c("101.5", "99.5", ts=1), _c("110", "104", ts=2)]
    got = corwin_schultz_spread(candles, block_size=None)
    assert got is not None
    assert got.pairs == 2
    assert got.negative_pairs == 1
    assert got.blocks == 0  # no blocking was done
    assert math.isclose(float(got.spread_pct), 0.0079088925 / 2, rel_tol=1e-6)


def _quiet_then_trending() -> list[Candle]:
    """21 pairs of tight overlapping ranges (a positive block), then 21 pairs of a 5%-a-bar
    ramp whose ranges never overlap (a negative block, floored)."""
    candles = [_c("101", "99", ts=i) for i in range(22)]
    centre = 100.0
    for i in range(22, 43):
        centre *= 1.05
        candles.append(_c(f"{centre * 1.001:.6f}", f"{centre / 1.001:.6f}", ts=i))
    return candles


def test_a_floored_block_still_counts_toward_the_average() -> None:
    """A negative block contributes ZERO to the mean, not nothing at all -- the denominator is
    every block, not just the surviving ones.

    Dropping floored blocks from the denominator would reintroduce the selection bias the
    blocking exists to remove, one level up: a series whose spread is unmeasurable in half its
    months would report the other half's figure as if it held throughout. Here that is worth
    exactly 2x, because one of the two blocks floors."""
    mixed = corwin_schultz_spread(_quiet_then_trending())
    quiet_only = corwin_schultz_spread([_c("101", "99", ts=i) for i in range(22)])
    assert mixed is not None and quiet_only is not None
    assert mixed.blocks == 2 and quiet_only.blocks == 1
    # The trending block floored, so the mixed series reports HALF the quiet block's figure.
    assert math.isclose(
        float(mixed.spread_pct), float(quiet_only.spread_pct) / 2, rel_tol=1e-6
    )


def test_the_block_size_defaults_to_a_trading_month() -> None:
    assert MONTHLY_BLOCK == 21


def test_blocks_are_counted_and_a_short_tail_is_still_a_block() -> None:
    """43 pairs at 21 to a block is two full blocks and a 1-pair remainder. Dropping the
    remainder would silently discard the most recent data, which is the part a cost estimate
    most needs."""
    candles = [_c("101", "99", ts=i) for i in range(45)]
    got = corwin_schultz_spread(candles)
    assert got is not None
    assert got.pairs == 44
    assert got.blocks == 3


def test_the_raw_unfloored_mean_is_reported_as_a_diagnostic() -> None:
    """The only figure with no flooring anywhere. It can be negative, and when it is, that is
    the honest statement that the series carries no measurable spread -- a fact both floored
    variants are structurally incapable of expressing."""
    candles = [_c("105", "100", ts=0), _c("110", "104", ts=1)]
    got = corwin_schultz_spread(candles)
    assert got is not None
    assert got.raw_spread_pct < 0
    assert got.spread_pct == Decimal("0")


def test_the_half_spread_is_the_one_way_cost() -> None:
    """keel prices ONE leg at a time, so the number that maps onto slippage is half the
    quoted spread -- crossing from the mid to the touch."""
    got = corwin_schultz_spread([_c("101", "99", ts=i) for i in range(30)])
    assert got is not None
    assert got.half_spread_pct == got.spread_pct / 2
    assert got.spread_pct > 0


def test_a_series_shorter_than_two_bars_has_no_estimate() -> None:
    assert corwin_schultz_spread([]) is None
    assert corwin_schultz_spread([_c("101", "99")]) is None


def test_a_series_whose_every_pair_is_unusable_has_no_estimate() -> None:
    """Distinct from "the spread is zero": nothing was measurable, and the caller must be able
    to tell those apart rather than reading 0bp as a free venue."""
    assert corwin_schultz_spread([_c("100", "100", ts=0), _c("100", "100", ts=1)]) is None


def test_the_gap_adjusted_pair_count_is_reported() -> None:
    """The equities-vs-crypto comparison turns on this number: if the equity series adjusts
    many pairs and the crypto series adjusts almost none, that asymmetry is the finding."""
    candles = [_c("101", "99", ts=0), _c("112", "110", ts=1), _c("112.5", "110.5", ts=2)]
    got = corwin_schultz_spread(candles)
    assert got is not None
    assert got.gap_adjusted_pairs == 1


def test_the_estimate_is_a_decimal_so_it_composes_with_keels_money_types() -> None:
    got = corwin_schultz_spread([_c("101", "99", ts=i) for i in range(30)])
    assert got is not None
    assert isinstance(got.spread_pct, Decimal)
    assert isinstance(got.half_spread_pct, Decimal)
    assert isinstance(got.raw_spread_pct, Decimal)
    assert got.spread_pct * Decimal("1000") > 0


def test_the_estimate_is_never_negative_in_aggregate() -> None:
    """Every pair negative means the block mean is negative and floors to zero -- reported as
    zero WITH its pair count, not as None, because the pairs were measurable."""
    got = corwin_schultz_spread([_c("105", "100", ts=0), _c("110", "104", ts=1)])
    assert got is not None
    assert got.spread_pct == Decimal("0")
    assert got.negative_pairs == 1


@pytest.mark.parametrize("bars", [2, 3, 10, 50])
def test_pair_count_is_one_less_than_the_usable_bar_count(bars: int) -> None:
    candles = [_c("101", "99", ts=i) for i in range(bars)]
    got = corwin_schultz_spread(candles)
    assert got is not None
    assert got.pairs == bars - 1


def test_the_estimate_is_reported_as_basis_points_for_the_write_up() -> None:
    est = SpreadEstimate(
        spread_pct=Decimal("0.0012"),
        raw_spread_pct=Decimal("0.0010"),
        pairs=100,
        negative_pairs=10,
        gap_adjusted_pairs=0,
        blocks=5,
    )
    assert est.spread_bp == Decimal("12.00")
    assert est.half_spread_bp == Decimal("6.00")
    assert est.raw_spread_bp == Decimal("10.00")
