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
        avg = sum(price for _, price in current) / len(current)
        if abs(pivot[1] - avg) <= avg * tolerance:
            current.append(pivot)
        else:
            clusters.append([pivot])

    levels = []
    for cluster in clusters:
        avg_price = sum(price for _, price in cluster) / len(cluster)
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


def is_round_number(price: Decimal, step: Decimal = Decimal("0.005")) -> bool:
    """True if `price` is close to a multiple of `step` (an "even handle"), within
    10% of the step size.
    """
    remainder = price % step
    distance = min(remainder, step - remainder)
    return distance <= step * Decimal("0.1")


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
