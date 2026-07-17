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


def _cluster_pivots(
    prices: list[Decimal], kind: Literal["support", "resistance"], tolerance: Decimal
) -> list[Level]:
    """Greedily cluster sorted prices within relative `tolerance` of the running
    cluster average into a single `Level`, counting touches.
    """
    if not prices:
        return []

    ordered = sorted(prices)
    clusters: list[list[Decimal]] = [[ordered[0]]]
    for price in ordered[1:]:
        current = clusters[-1]
        avg = sum(current) / len(current)
        if abs(price - avg) <= avg * tolerance:
            current.append(price)
        else:
            clusters.append([price])

    levels = []
    for cluster in clusters:
        avg_price = sum(cluster) / len(cluster)
        levels.append(Level(price=avg_price, kind=kind, touches=len(cluster), angular=False))
    return levels


def find_levels(
    candles: list[Candle],
    tolerance: Decimal = Decimal("0.002"),
    min_touches: int = 3,
) -> list[Level]:
    """Cluster swing-pivot prices into support/resistance levels, count touches per
    cluster, and keep only levels with `touches >= min_touches` (KB §7.3).
    """
    low_prices = [candles[i].low for i in swing_lows(candles)]
    high_prices = [candles[i].high for i in swing_highs(candles)]

    levels = _cluster_pivots(low_prices, "support", tolerance)
    levels += _cluster_pivots(high_prices, "resistance", tolerance)

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
