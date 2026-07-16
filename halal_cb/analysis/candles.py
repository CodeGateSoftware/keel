"""Candlestick primitives and pattern detectors — pure functions on `Candle`/`list[Candle]`.

No I/O, no network, no global state: every function takes `Candle` value(s) in and returns a
plain value out. Money/price fields stay `Decimal` throughout (never `float`).

Pattern definitions follow the trading knowledge base (KB source-01 §1.3, source-07 §7.2):
- body = close-open (signed); upper/lower wick and range are always non-negative for a
  well-formed candle (low <= min(open,close) <= max(open,close) <= high).
- High-test / shooting star (bearish): upper wick >= k*body AND close in the lower third.
- Low-test / hammer (bullish): lower wick >= k*body AND close in the upper third.
- Doji (indecision): |close-open| <= m*(high-low).
- Marubozu (strong momentum): both wicks <= a small fraction of the body.
- Pin bar: open AND close both sit in the outer `zone` fraction of the range (top -> bullish,
  bottom -> bearish); a candle whose body sits mid-range is not a pin bar.
- Tweezer top/bottom: two candles with equal highs/lows (within a relative tolerance) and
  bodies located in the outer 50% of each candle's own range.
- Three-bar reversal: the middle candle takes out the first candle's extreme with a rejection
  wick (wick longer than its own body), then the third candle closes beyond the middle
  candle's opposite extreme, confirming the reversal. This module encodes that specific
  three-candle shape; it is not attempting to detect every informal notion of "reversal".
- Reliability ranking for confluence weighting (KB source-07 §7.2): low-test/hammer (and the
  symmetric high-test/shooting-star) rank highest, tweezer next, doji lowest.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from halal_cb.types import Candle

# ---------------------------------------------------------------------------
# Basic geometry
# ---------------------------------------------------------------------------


def body(c: Candle) -> Decimal:
    """Signed candle body: close - open."""
    return c.close - c.open


def upper_wick(c: Candle) -> Decimal:
    """Distance from the higher of open/close up to the high."""
    return c.high - max(c.open, c.close)


def lower_wick(c: Candle) -> Decimal:
    """Distance from the low up to the lower of open/close."""
    return min(c.open, c.close) - c.low


def range_(c: Candle) -> Decimal:
    """Full high-low range of the candle."""
    return c.high - c.low


def _in_top_zone(price: Decimal, c: Candle, zone: Decimal, rng: Decimal) -> bool:
    return (c.high - price) <= zone * rng


def _in_bottom_zone(price: Decimal, c: Candle, zone: Decimal, rng: Decimal) -> bool:
    return (price - c.low) <= zone * rng


# ---------------------------------------------------------------------------
# Single-candle detectors
# ---------------------------------------------------------------------------


def is_pin_bar(c: Candle, zone: Decimal = Decimal("0.30")) -> Literal["bullish", "bearish"] | None:
    """Open AND close both in the outer `zone` fraction of the range.

    Top zone (near the high, long lower wick) -> "bullish"; bottom zone (near the low, long
    upper wick) -> "bearish". A body that straddles the middle of the range is not a pin bar.
    """
    rng = range_(c)
    if rng == 0:
        return None

    bullish = _in_top_zone(c.open, c, zone, rng) and _in_top_zone(c.close, c, zone, rng)
    bearish = _in_bottom_zone(c.open, c, zone, rng) and _in_bottom_zone(c.close, c, zone, rng)

    if bullish and not bearish:
        return "bullish"
    if bearish and not bullish:
        return "bearish"
    return None


def is_doji(c: Candle, m: Decimal = Decimal("0.1")) -> bool:
    """Indecision candle: |close-open| <= m*(high-low)."""
    rng = range_(c)
    if rng == 0:
        # A candle that never moved at all is the degenerate/perfect doji case.
        return True
    return abs(body(c)) <= m * rng


def is_marubozu(c: Candle, frac: Decimal = Decimal("0.05")) -> bool:
    """Strong momentum/continuation candle: both wicks <= frac * |body|.

    Requires a non-zero body — a flat (zero-range) candle is never a marubozu.
    """
    b = abs(body(c))
    if b == 0:
        return False
    return upper_wick(c) <= frac * b and lower_wick(c) <= frac * b


def is_hammer(c: Candle, k: int | Decimal = 2) -> bool:
    """Low-test / hammer (bullish): lower wick >= k*|body| AND close in the upper third."""
    rng = range_(c)
    if rng == 0:
        return False
    if lower_wick(c) < Decimal(k) * abs(body(c)):
        return False
    return 3 * (c.high - c.close) <= rng


def is_shooting_star(c: Candle, k: int | Decimal = 2) -> bool:
    """High-test / shooting star (bearish): upper wick >= k*|body| AND close in the lower third."""
    rng = range_(c)
    if rng == 0:
        return False
    if upper_wick(c) < Decimal(k) * abs(body(c)):
        return False
    return 3 * (c.close - c.low) <= rng


# ---------------------------------------------------------------------------
# Multi-candle detectors
# ---------------------------------------------------------------------------


def is_three_bar_reversal(
    c1: Candle, c2: Candle, c3: Candle
) -> Literal["bullish", "bearish"] | None:
    """Rejection wick takes out the prior extreme, then the next candle closes beyond it.

    Bullish: c2 posts a new low below c1's low with a rejection lower wick longer than its
    own body, then c3 closes beyond c2's high.
    Bearish (mirror): c2 posts a new high above c1's high with a rejection upper wick longer
    than its own body, then c3 closes beyond c2's low.
    """
    bullish = c2.low < c1.low and lower_wick(c2) > abs(body(c2)) and c3.close > c2.high
    bearish = c2.high > c1.high and upper_wick(c2) > abs(body(c2)) and c3.close < c2.low

    if bullish and not bearish:
        return "bullish"
    if bearish and not bullish:
        return "bearish"
    return None


def _approx_equal(a: Decimal, b: Decimal, tol: Decimal) -> bool:
    denom = max(abs(a), abs(b))
    if denom == 0:
        return a == b
    return abs(a - b) <= tol * denom


def _body_in_top_half(c: Candle) -> bool:
    mid = c.low + range_(c) / 2
    return c.open >= mid and c.close >= mid


def _body_in_bottom_half(c: Candle) -> bool:
    mid = c.low + range_(c) / 2
    return c.open <= mid and c.close <= mid


def is_tweezer(
    c1: Candle, c2: Candle, tol: Decimal = Decimal("0.001")
) -> Literal["top", "bottom"] | None:
    """Equal highs/lows (within relative `tol`) with bodies in the outer 50% of each range.

    Equal lows + bodies in the top half of both candles -> "bottom" (support rejection).
    Equal highs + bodies in the bottom half of both candles -> "top" (resistance rejection).
    """
    bottom = _approx_equal(c1.low, c2.low, tol) and _body_in_top_half(c1) and _body_in_top_half(c2)
    top = (
        _approx_equal(c1.high, c2.high, tol)
        and _body_in_bottom_half(c1)
        and _body_in_bottom_half(c2)
    )

    if bottom and not top:
        return "bottom"
    if top and not bottom:
        return "top"
    return None


# ---------------------------------------------------------------------------
# Confluence weighting
# ---------------------------------------------------------------------------

_PATTERN_CONFIDENCE: dict[str, Decimal] = {
    # Low-test/hammer and the symmetric high-test/shooting-star: most aggressive, highest
    # strike rate (KB source-07 §7.2).
    "hammer": Decimal("0.90"),
    "shooting_star": Decimal("0.90"),
    "pin_bar": Decimal("0.85"),
    "marubozu": Decimal("0.80"),
    "three_bar_reversal": Decimal("0.75"),
    # Tweezer: reliable but less aggressive than a low-test/hammer.
    "tweezer": Decimal("0.60"),
    # Doji: pure indecision, lowest strike rate.
    "doji": Decimal("0.30"),
}


def pattern_confidence(name: str) -> Decimal:
    """Per-pattern confidence weight for confluence scoring (low-test/hammer > tweezer > doji)."""
    try:
        return _PATTERN_CONFIDENCE[name]
    except KeyError as exc:
        raise ValueError(f"unknown pattern name: {name!r}") from exc
