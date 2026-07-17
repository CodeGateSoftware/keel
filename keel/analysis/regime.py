"""Market regime classification: condition (trend structure) and phase (impulse vs retracement).

Implements the confluence checklist components #1-#2 from the knowledge base (source-01 §1.2):
market condition (bullish/bearish/ranging/choppy from swing structure — choppy forbids trading)
and market phase (run vs pullback — entries only fire on pullbacks within a trend).
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum

from keel.types import Candle

_DEFAULT_FLAT_TOLERANCE = Decimal("0.001")


class Condition(str, Enum):
    """Market structure condition derived from recent swing highs/lows."""

    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    RANGING = "RANGING"
    CHOPPY = "CHOPPY"


class Phase(str, Enum):
    """Market phase within a condition: impulse (run) vs retracement (pullback)."""

    RUN = "RUN"
    PULLBACK = "PULLBACK"


# NOTE: local pivot helper to keep this module independent of analysis.levels; dedupe once both
# land.
def _swing_highs(candles: list[Candle], arm: int = 1) -> list[int]:
    """Indices of local maxima: `high` strictly greater than `arm` candles on each side."""
    idx = []
    n = len(candles)
    for i in range(arm, n - arm):
        neighbors = [candles[j].high for j in range(i - arm, i + arm + 1) if j != i]
        if all(candles[i].high > h for h in neighbors):
            idx.append(i)
    return idx


# NOTE: local pivot helper to keep this module independent of analysis.levels; dedupe once both
# land.
def _swing_lows(candles: list[Candle], arm: int = 1) -> list[int]:
    """Indices of local minima: `low` strictly less than `arm` candles on each side."""
    idx = []
    n = len(candles)
    for i in range(arm, n - arm):
        neighbors = [candles[j].low for j in range(i - arm, i + arm + 1) if j != i]
        if all(candles[i].low < low for low in neighbors):
            idx.append(i)
    return idx


def _pivot_trend(prev: Decimal, curr: Decimal, tol: Decimal) -> str:
    """Classify the move from one pivot value to the next as "up", "down", or "flat"."""
    if prev == 0:
        return "flat"
    relative_diff = (curr - prev) / prev
    if abs(relative_diff) <= tol:
        return "flat"
    return "up" if relative_diff > 0 else "down"


def detect_condition(
    candles: list[Candle],
    lookback: int = 20,
    flat_tolerance: Decimal = _DEFAULT_FLAT_TOLERANCE,
) -> Condition:
    """Classify market condition from the last `lookback` candles' swing structure.

    Higher-highs + higher-lows -> BULLISH. Lower-highs + lower-lows -> BEARISH.
    Flat swing highs and flat swing lows -> RANGING. Anything else (mismatched
    swing directions, or too few pivots to establish structure) -> CHOPPY.
    """
    window = candles[-lookback:] if len(candles) > lookback else candles
    high_idx = _swing_highs(window)
    low_idx = _swing_lows(window)

    if len(high_idx) < 2 or len(low_idx) < 2:
        return Condition.CHOPPY

    high_trend = _pivot_trend(window[high_idx[-2]].high, window[high_idx[-1]].high, flat_tolerance)
    low_trend = _pivot_trend(window[low_idx[-2]].low, window[low_idx[-1]].low, flat_tolerance)

    if high_trend == "up" and low_trend == "up":
        return Condition.BULLISH
    if high_trend == "down" and low_trend == "down":
        return Condition.BEARISH
    if high_trend == "flat" and low_trend == "flat":
        return Condition.RANGING
    return Condition.CHOPPY


def detect_phase(candles: list[Candle]) -> Phase:
    """Classify whether price is currently in an impulse (RUN) or a retracement (PULLBACK).

    Direction is taken from net close-to-close drift across the given candles. In an
    uptrend, price beyond the most recent swing high is a RUN (still making new highs);
    price below it is a PULLBACK (retracing). Mirrored for a downtrend. With no detected
    pivots yet (e.g. a short, still-monotonic move) defaults to RUN.
    """
    if len(candles) < 2:
        return Phase.RUN

    high_idx = _swing_highs(candles)
    low_idx = _swing_lows(candles)
    if not high_idx or not low_idx:
        return Phase.RUN

    trend_up = candles[-1].close >= candles[0].close
    current_close = candles[-1].close

    if trend_up:
        last_swing_high = candles[high_idx[-1]].high
        return Phase.RUN if current_close > last_swing_high else Phase.PULLBACK

    last_swing_low = candles[low_idx[-1]].low
    return Phase.RUN if current_close < last_swing_low else Phase.PULLBACK


def is_tradeable(condition: Condition) -> bool:
    """CHOPPY forbids trading (source-01 §1.2: "choppy => no trades"); all others allow it."""
    return condition != Condition.CHOPPY
