"""Tests for halal_cb.analysis.candles: pure candlestick primitives and pattern detectors.

Fixtures are hand-built Candle instances chosen so each detector's boundary conditions are
exercised with both a positive (pattern present) and a negative (pattern absent) case, per
KB source-01 §1.3 and source-07 §7.2.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from halal_cb.analysis.candles import (
    body,
    is_doji,
    is_hammer,
    is_marubozu,
    is_pin_bar,
    is_shooting_star,
    is_three_bar_reversal,
    is_tweezer,
    lower_wick,
    pattern_confidence,
    range_,
    upper_wick,
)
from halal_cb.types import Candle


def mk(ts: int, o: str, h: str, l: str, c: str, v: str = "1") -> Candle:  # noqa: E741
    """Build a Candle from decimal-literal strings for exact fixtures."""
    return Candle(
        ts=ts,
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(l),
        close=Decimal(c),
        volume=Decimal(v),
    )


# ---------------------------------------------------------------------------
# body / upper_wick / lower_wick / range_
# ---------------------------------------------------------------------------


def test_body_upper_wick_lower_wick_range_basic_candle():
    c = mk(1, "100", "110", "95", "105")

    assert body(c) == Decimal("5")
    assert upper_wick(c) == Decimal("5")
    assert lower_wick(c) == Decimal("5")
    assert range_(c) == Decimal("15")


def test_body_is_negative_for_bearish_candle():
    c = mk(1, "110", "112", "98", "100")

    assert body(c) == Decimal("-10")
    assert upper_wick(c) == Decimal("2")
    assert lower_wick(c) == Decimal("2")
    assert range_(c) == Decimal("14")


# ---------------------------------------------------------------------------
# is_pin_bar
# ---------------------------------------------------------------------------


def test_is_pin_bar_bullish_textbook():
    # range=10, zone=0.30 -> top zone is price >= 97; long lower wick (8), small body near top.
    c = mk(1, "98", "100", "90", "99")

    assert is_pin_bar(c) == "bullish"


def test_is_pin_bar_bearish_textbook():
    # range=10, zone=0.30 -> bottom zone is price <= 103; long upper wick (8), small body near low.
    c = mk(1, "101", "110", "100", "102")

    assert is_pin_bar(c) == "bearish"


def test_is_pin_bar_mid_range_close_returns_none():
    c = mk(1, "95", "100", "90", "96")

    assert is_pin_bar(c) is None


def test_is_pin_bar_doji_is_not_a_pin_bar():
    c = mk(1, "100", "101", "99", "100.05")

    assert is_doji(c) is True
    assert is_pin_bar(c) is None


def test_is_pin_bar_custom_zone():
    # range=10; open/close sit in the top 50% [95,100] but not the top 30% [97,100].
    c = mk(1, "96", "100", "90", "95.5")

    assert is_pin_bar(c) is None
    assert is_pin_bar(c, zone=Decimal("0.5")) == "bullish"


# ---------------------------------------------------------------------------
# is_doji
# ---------------------------------------------------------------------------


def test_is_doji_small_body_true():
    c = mk(1, "100.00", "101", "99", "100.05")

    assert is_doji(c) is True


def test_is_doji_large_body_false():
    c = mk(1, "100", "106", "99", "105")

    assert is_doji(c) is False


# ---------------------------------------------------------------------------
# is_marubozu
# ---------------------------------------------------------------------------


def test_is_marubozu_tiny_wicks_true():
    c = mk(1, "100", "110.2", "99.8", "110")

    assert is_marubozu(c) is True


def test_is_marubozu_large_wick_false():
    c = mk(1, "100", "112", "99.8", "110")

    assert is_marubozu(c) is False


def test_is_marubozu_zero_body_false():
    c = mk(1, "100", "100", "100", "100")

    assert is_marubozu(c) is False


# ---------------------------------------------------------------------------
# is_hammer
# ---------------------------------------------------------------------------


def test_is_hammer_long_lower_wick_close_upper_third_true():
    c = mk(1, "99", "100.2", "90", "100")

    assert is_hammer(c) is True


def test_is_hammer_close_not_in_upper_third_false():
    c = mk(1, "95", "100", "90", "96")

    assert is_hammer(c) is False


def test_is_hammer_insufficient_lower_wick_false():
    c = mk(1, "99", "100.2", "98", "100")

    assert is_hammer(c) is False


# ---------------------------------------------------------------------------
# is_shooting_star
# ---------------------------------------------------------------------------


def test_is_shooting_star_long_upper_wick_close_lower_third_true():
    c = mk(1, "100", "110", "98.8", "99")

    assert is_shooting_star(c) is True


def test_is_shooting_star_close_not_in_lower_third_false():
    # Sufficient upper wick (5 >= 2*1), but close(99) is not in the lower third of [90,105].
    c = mk(1, "100", "105", "90", "99")

    assert is_shooting_star(c) is False


def test_is_shooting_star_insufficient_upper_wick_false():
    c = mk(1, "100", "101.2", "98.8", "99")

    assert is_shooting_star(c) is False


# ---------------------------------------------------------------------------
# is_three_bar_reversal
# ---------------------------------------------------------------------------


def test_is_three_bar_reversal_bullish():
    c1 = mk(1, "105", "106", "100", "102")
    c2 = mk(2, "97", "99", "90", "98")  # new low below c1, long rejection lower wick
    c3 = mk(3, "99", "101", "98", "101")  # closes beyond c2's high (99)

    assert is_three_bar_reversal(c1, c2, c3) == "bullish"


def test_is_three_bar_reversal_bearish():
    c1 = mk(1, "95", "100", "94", "98")
    c2 = mk(2, "101", "110", "99", "102")  # new high above c1, long rejection upper wick
    c3 = mk(3, "101", "102", "98", "98")  # closes beyond c2's low (99)

    assert is_three_bar_reversal(c1, c2, c3) == "bearish"


def test_is_three_bar_reversal_no_confirmation_returns_none():
    c1 = mk(1, "105", "106", "100", "102")
    c2 = mk(2, "97", "99", "90", "98")  # new low, rejection wick
    c3 = mk(3, "98.5", "99", "97", "98.5")  # fails to close beyond c2's high

    assert is_three_bar_reversal(c1, c2, c3) is None


# ---------------------------------------------------------------------------
# is_tweezer
# ---------------------------------------------------------------------------


def test_is_tweezer_bottom_equal_lows_top_half_bodies():
    c1 = mk(1, "103", "105", "100", "104")
    c2 = mk(2, "103", "105", "100.05", "104")

    assert is_tweezer(c1, c2) == "bottom"


def test_is_tweezer_top_equal_highs_bottom_half_bodies():
    c1 = mk(1, "104", "110", "100", "103")
    c2 = mk(2, "104", "110.05", "100", "103")

    assert is_tweezer(c1, c2) == "top"


def test_is_tweezer_unequal_lows_returns_none():
    c1 = mk(1, "103", "105", "100", "104")
    c2 = mk(2, "103", "105", "110", "104")

    assert is_tweezer(c1, c2) is None


def test_is_tweezer_bodies_not_in_outer_half_returns_none():
    # Near-equal lows, but bodies sit in the bottom half instead of the top half; highs
    # differ so this doesn't accidentally qualify as a tweezer-top either.
    c1 = mk(1, "100.5", "105", "100", "100.6")
    c2 = mk(2, "100.5", "115", "100.02", "100.6")

    assert is_tweezer(c1, c2) is None


# ---------------------------------------------------------------------------
# pattern_confidence
# ---------------------------------------------------------------------------


def test_pattern_confidence_ordering_low_test_gt_tweezer_gt_doji():
    assert pattern_confidence("hammer") > pattern_confidence("tweezer")
    assert pattern_confidence("tweezer") > pattern_confidence("doji")


def test_pattern_confidence_shooting_star_matches_hammer_tier():
    assert pattern_confidence("shooting_star") == pattern_confidence("hammer")


def test_pattern_confidence_returns_decimal():
    assert isinstance(pattern_confidence("doji"), Decimal)


def test_pattern_confidence_unknown_pattern_raises():
    with pytest.raises(ValueError, match="unknown"):
        pattern_confidence("not_a_pattern")
