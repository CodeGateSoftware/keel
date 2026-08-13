"""Support/resistance level primitives.

Pure functions over `Candle` sequences: swing-pivot detection, clustering of pivots
into support/resistance `Level`s with a minimum touch-count threshold (KB §7.3),
round-number ("even handle") proximity, role-reversal detection (KB §1.3, prior
resistance now acting as support and vice versa), and nearest-level lookup used for
magnet-level bias/targeting (KB §9.2).

No network, no global state — data in, values out.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from keel.types import Candle


@dataclass
class Level:
    """A clustered support/resistance level."""

    price: Decimal
    kind: Literal["support", "resistance"]
    touches: int
    angular: bool


def swing_highs(candles: list[Candle], lookback: int = 2) -> list[int]:
    """Indices of pivot highs: strictly higher than every candle within `lookback`
    bars on both sides.
    """
    n = len(candles)
    idxs = []
    for i in range(lookback, n - lookback):
        pivot_high = candles[i].high
        window = candles[i - lookback : i + lookback + 1]
        if all(pivot_high > c.high for j, c in enumerate(window) if j != lookback):
            idxs.append(i)
    return idxs


def swing_lows(candles: list[Candle], lookback: int = 2) -> list[int]:
    """Indices of pivot lows: strictly lower than every candle within `lookback`
    bars on both sides.
    """
    n = len(candles)
    idxs = []
    for i in range(lookback, n - lookback):
        pivot_low = candles[i].low
        window = candles[i - lookback : i + lookback + 1]
        if all(pivot_low < c.low for j, c in enumerate(window) if j != lookback):
            idxs.append(i)
    return idxs


def _distinct_touches(timestamps: list[int], min_separation_sec: int) -> int:
    """Count touches that are separated in TIME, not merely in the list (KB §81.5).

    Three pivots inside one week are one visit to a level, not three independent tests of it.
    Counting them as three inflates `touches` and manufactures "strong" levels out of a single
    consolidation. Greedy left-to-right: take the earliest touch, then the next one at least
    `min_separation_sec` later, and so on.
    """
    if min_separation_sec <= 0:
        return len(timestamps)
    kept = 0
    last: int | None = None
    for ts in sorted(timestamps):
        if last is None or ts - last >= min_separation_sec:
            kept += 1
            last = ts
    return kept


def _cluster_pivots(
    pivots: list[tuple[int, Decimal]],
    kind: Literal["support", "resistance"],
    tolerance: Decimal,
    min_separation_sec: int,
) -> list[Level]:
    """Greedily cluster `(ts, price)` pivots within relative `tolerance` of the running cluster
    average into a single `Level`, counting TIME-SEPARATED touches.
    """
    if not pivots:
        return []

    ordered = sorted(pivots, key=lambda item: item[1])
    clusters: list[list[tuple[int, Decimal]]] = [[ordered[0]]]
    for pivot in ordered[1:]:
        current = clusters[-1]
        # `Decimal(0)` start: a bare `sum()` starts from an `int` 0, which types the running
        # average as `Decimal | float` and carries that widening into `Level.price` below. Every
        # cluster holds at least the pivot that opened it, so the start value is never actually
        # summed -- this pins the type without changing a single computed number.
        avg = sum((price for _, price in current), Decimal(0)) / len(current)
        if abs(pivot[1] - avg) <= avg * tolerance:
            current.append(pivot)
        else:
            clusters.append([pivot])

    levels = []
    for cluster in clusters:
        avg_price = sum((price for _, price in cluster), Decimal(0)) / len(cluster)
        touches = _distinct_touches([ts for ts, _ in cluster], min_separation_sec)
        levels.append(Level(price=avg_price, kind=kind, touches=touches, angular=False))
    return levels


#: Minimum time between two pivots for them to count as SEPARATE touches (KB §81.5, which
#: specifies two weeks). Adopted `a_priori` from the source, not fitted here, so it costs no
#: trials budget (§73.12). Set to 0 to restore the old behaviour of counting every pivot.
MIN_TOUCH_SEPARATION_SEC = 14 * 24 * 3600


def find_levels(
    candles: list[Candle],
    tolerance: Decimal = Decimal("0.002"),
    min_touches: int = 3,
    min_separation_sec: int = MIN_TOUCH_SEPARATION_SEC,
) -> list[Level]:
    """Cluster swing-pivot prices into support/resistance levels, count TIME-SEPARATED touches
    per cluster, and keep only levels with `touches >= min_touches` (KB §7.3, §81.5).

    ⚠️ **`min_separation_sec` is why this is not just a touch count.** Without it, three pivots
    inside a single week's consolidation counted as three independent tests of a level, and any
    tight chop manufactured a "strong" level. KB §81.5 requires at least two weeks between
    touches; that is the default here.
    """
    lows = [(candles[i].ts, candles[i].low) for i in swing_lows(candles)]
    highs = [(candles[i].ts, candles[i].high) for i in swing_highs(candles)]

    levels = _cluster_pivots(lows, "support", tolerance, min_separation_sec)
    levels += _cluster_pivots(highs, "resistance", tolerance, min_separation_sec)

    return [level for level in levels if level.touches >= min_touches]


#: Significant figures that define a round handle. Two is not a tuning knob, it is what the
#: words mean: 65,000 and 0.38 are handles, 65,100 and 0.381 are not, and both pairs stand in
#: exactly the same relation to their own price. Three would push the grid down onto ADA's and
#: XLM's quote increment -- measured, it lifts ADA's presence rate to 0.30 against BTC's 0.19
#: purely from tick quantization, which is the same class of artifact this function is being
#: fixed to remove.
_HANDLE_SIGNIFICANT_FIGURES = 2

#: Half-width of the "at the handle" band, as a fraction of the SPACING BETWEEN HANDLES (not of
#: price). See `is_round_number` for why that denominator is the load-bearing choice.
DEFAULT_HANDLE_TOLERANCE = Decimal("0.02")


def _handle_spacing(price: Decimal) -> Decimal:
    """Distance between adjacent round handles at `price`'s own order of magnitude.

    `10 ** (floor(log10(price)) - 1)`, computed from `Decimal.adjusted()` so it is exact
    integer arithmetic on the exponent -- no float `log10`, which would misplace the grid for
    prices sitting a few ulps under a power of ten. Returns a spacing such that
    `price / spacing` always lands in `[10, 100)`, i.e. the handles are the two-significant-
    figure prices: 1,000 at BTC's 65,000; 100 at ETH's 3,400; 0.01 at ADA's 0.38.
    """
    return Decimal((0, (1,), price.adjusted() - (_HANDLE_SIGNIFICANT_FIGURES - 1)))


def is_round_number(price: Decimal, tolerance: Decimal = DEFAULT_HANDLE_TOLERANCE) -> bool:
    """True if `price` sits at a psychological round handle for its own order of magnitude.

    A magnet level is a number enough people are watching that orders pile up on it, and what
    makes a number watchable is having few significant figures — 65,000, 3,400, 0.38. That is a
    property of the price *relative to its own scale*, so the handle grid has to be derived
    from the price and cannot be a constant.

    ⚠️ **This function used to take an absolute `step=Decimal("0.005")` and it could not fail
    (issue #225).** Coinbase quotes BTC, ETH and PAXG to two decimals, and every 2dp value is an
    exact multiple of half a cent because `0.01 = 2 * 0.005`. So `price % step` was always
    exactly zero and the answer was always `True`: measured over the daily history in the candle
    cache, P(present) was **1.0000 on BTC-USD, ETH-USD and PAXG-USD** against 0.217 on ADA-USD
    and 0.190 on XLM-USD. Because `round_number_proximity` is weight 1 of `DEFAULT_WEIGHTS`'
    14, three of the five live allowlist assets were carrying an unconditional +1 on every CTS
    score — a constant, which is worse than a redundant factor, because a redundant factor at
    least varies. It also broke cross-asset comparability of the total: any threshold read
    against CTS meant something different on BTC than on XLM by exactly one point.

    **`tolerance` is a fraction of the handle SPACING, not of price, and the denominator is the
    whole argument.** Both denominators scale with the instrument, so both fix the bug; they
    differ in what they hold constant. A fraction-of-price band makes the presence rate depend
    on where in the decade the price happens to sit — the spacing is 10% of price just above a
    power of ten and 1% of it just below, so the same rule would fire ten times as often on BTC
    at 99,000 as at 10,500, and the factor would silently change meaning as an asset trended
    through a decade. A fraction-of-spacing band makes P(present) identically `2 * tolerance`
    for any price series that is smooth on the scale of the grid, which is precisely the
    property #225 asks for: the factor must mean the same thing at 65,000 as at 0.38. Measured
    on the same daily history, it does — 0.037 / 0.036 / 0.041 / 0.044 / 0.043 across the five
    allowlist assets, a 1.22x spread where the old code's was 5.3x.

    The default `0.02` puts the band at +/- 2% of the gap to the next handle (+/- 20 dollars on
    BTC's 1,000-wide grid), giving a ~4% presence rate — rarer than `candlestick_pattern`
    (0.203) and commoner than `rsi_extreme` (0.023), so it sits inside the existing spread of
    CTS factor base rates rather than dominating or vanishing. It is deliberately tighter than
    the 10% the original docstring claimed: at 10% the band on BTC is +/- 100 and 64,975.78
    scores present, which #225 names as a case that must score absent. That is the one genuinely
    free parameter here and the write-up carries the sensitivity ladder for it.

    Degenerate inputs score absent rather than raising. Zero has no order of magnitude (and
    `_handle_spacing` would hand back a meaningless grid), negatives are not prices, and
    NaN/Infinity have no `adjusted()` worth trusting — none of them is a round handle, and a
    pure predicate on the live scoring path should not be able to throw. Extreme magnitudes are
    safe without a guard: `price / spacing` is always in `[10, 100)`, so the `%` below never
    needs more than two digits of integer quotient and cannot trip the Decimal context.
    """
    if not price.is_finite() or price <= 0:
        return False

    spacing = _handle_spacing(price)
    remainder = price % spacing
    distance = min(remainder, spacing - remainder)
    return distance <= spacing * tolerance


def role_reversed(
    level: Level, candles: list[Candle], tolerance: Decimal = Decimal("0.002")
) -> bool:
    """True if price decisively broke through `level` and later returned to touch it
    from the other side and held — the old resistance now acts as support (or vice
    versa), per KB §1.3.
    """
    price = level.price
    broke = False

    if level.kind == "resistance":
        for candle in candles:
            if not broke:
                if candle.close > price * (1 + tolerance):
                    broke = True
                continue
            near = abs(candle.low - price) <= price * tolerance
            if near and candle.close > price:
                return True
        return False

    # support -> now acting as resistance
    for candle in candles:
        if not broke:
            if candle.close < price * (1 - tolerance):
                broke = True
            continue
        near = abs(candle.high - price) <= price * tolerance
        if near and candle.close < price:
            return True
    return False


def nearest_level(
    price: Decimal,
    levels: list[Level],
    kind: Literal["support", "resistance"] | None = None,
) -> Level | None:
    """The `Level` whose price is closest to `price`, optionally filtered by `kind`."""
    candidates = [level for level in levels if kind is None or level.kind == kind]
    if not candidates:
        return None
    return min(candidates, key=lambda level: abs(level.price - price))
