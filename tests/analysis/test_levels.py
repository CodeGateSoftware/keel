"""Tests for keel.analysis.levels: swing pivots, S/R clustering, round numbers,
role reversal, and nearest-level lookup.
"""

from __future__ import annotations

from decimal import Decimal

from keel.analysis.levels import (
    Level,
    find_levels,
    is_round_number,
    nearest_level,
    role_reversed,
    swing_highs,
    swing_lows,
)
from keel.types import Candle


def _c(ts: int, o: str, h: str, l: str, c: str, v: str = "1") -> Candle:  # noqa: E741
    return Candle(
        ts=ts,
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(l),
        close=Decimal(c),
        volume=Decimal(v),
    )


def _three_touch_support_series() -> list[Candle]:
    """Synthetic series with three clean bounces off a low of 100."""
    rows = [
        (0, "108", "110", "105", "107"),
        (1, "105", "108", "102", "106"),
        (2, "103", "105", "100", "104"),  # pivot low @ 100 (touch 1)
        (3, "104", "108", "103", "107"),
        (4, "107", "112", "107", "111"),
        (5, "111", "115", "110", "113"),
        (6, "113", "112", "105", "106"),
        (7, "106", "108", "100", "107"),  # pivot low @ 100 (touch 2)
        (8, "107", "110", "104", "109"),
        (9, "109", "113", "108", "112"),
        (10, "112", "116", "112", "115"),
        (11, "115", "110", "103", "105"),
        (12, "105", "106", "100", "104"),  # pivot low @ 100 (touch 3)
        (13, "104", "109", "104", "107"),
        (14, "107", "112", "108", "111"),
    ]
    return [_c(ts, o, h, low, c) for ts, o, h, low, c in rows]


def _two_touch_series() -> list[Candle]:
    """Synthetic series with only two bounces off a low of 200 (below min_touches)."""
    rows = [
        (0, "208", "210", "205", "207"),
        (1, "205", "208", "202", "206"),
        (2, "203", "205", "200", "204"),  # pivot low @ 200 (touch 1)
        (3, "204", "208", "203", "207"),
        (4, "207", "212", "207", "211"),
        (5, "211", "215", "210", "213"),
        (6, "213", "212", "205", "206"),
        (7, "206", "208", "200", "207"),  # pivot low @ 200 (touch 2)
        (8, "207", "210", "204", "209"),
        (9, "209", "213", "208", "212"),
    ]
    return [_c(ts, o, h, low, c) for ts, o, h, low, c in rows]


def _three_touch_resistance_series() -> list[Candle]:
    """Mirror series: three clean rejections off a high of 300."""
    rows = [
        (0, "292", "295", "290", "293"),
        (1, "295", "298", "293", "294"),
        (2, "297", "300", "295", "296"),  # pivot high @ 300 (touch 1)
        (3, "296", "297", "292", "293"),
        (4, "293", "294", "288", "289"),
        (5, "289", "290", "285", "286"),
        (6, "286", "294", "286", "292"),
        (7, "292", "300", "292", "293"),  # pivot high @ 300 (touch 2)
        (8, "293", "294", "289", "290"),
        (9, "290", "291", "286", "287"),
        (10, "287", "288", "283", "284"),
        (11, "284", "295", "284", "290"),
        (12, "290", "300", "290", "291"),  # pivot high @ 300 (touch 3)
        (13, "291", "292", "287", "288"),
        (14, "288", "290", "285", "286"),
    ]
    return [_c(ts, o, h, low, c) for ts, o, h, low, c in rows]


def test_swing_lows_detects_pivot_indices():
    candles = _three_touch_support_series()

    idxs = swing_lows(candles, lookback=2)

    assert idxs == [2, 7, 12]
    for i in idxs:
        assert candles[i].low == Decimal("100")


def test_swing_highs_detects_pivot_indices():
    candles = _three_touch_resistance_series()

    idxs = swing_highs(candles, lookback=2)

    assert idxs == [2, 7, 12]
    for i in idxs:
        assert candles[i].high == Decimal("300")


def test_find_levels_three_bounces_yields_support_level_with_min_three_touches():
    candles = _three_touch_support_series()

    levels = find_levels(candles, tolerance=Decimal("0.002"), min_touches=3)

    support_levels = [lvl for lvl in levels if lvl.kind == "support"]
    assert len(support_levels) == 1
    level = support_levels[0]
    assert isinstance(level, Level)
    assert level.price == Decimal("100")
    assert level.touches >= 3


def test_find_levels_three_bounces_yields_resistance_level_with_min_three_touches():
    candles = _three_touch_resistance_series()

    levels = find_levels(candles, tolerance=Decimal("0.002"), min_touches=3)

    resistance_levels = [lvl for lvl in levels if lvl.kind == "resistance"]
    assert len(resistance_levels) == 1
    level = resistance_levels[0]
    assert level.price == Decimal("300")
    assert level.touches >= 3


def test_find_levels_excludes_level_touched_only_twice_at_min_touches_three():
    candles = _two_touch_series()

    levels = find_levels(candles, tolerance=Decimal("0.002"), min_touches=3)

    prices = [lvl.price for lvl in levels]
    assert Decimal("200") not in prices


def test_find_levels_includes_level_touched_twice_when_min_touches_two():
    candles = _two_touch_series()

    levels = find_levels(candles, tolerance=Decimal("0.002"), min_touches=2)

    support_levels = [lvl for lvl in levels if lvl.kind == "support"]
    assert len(support_levels) == 1
    assert support_levels[0].price == Decimal("200")
    assert support_levels[0].touches == 2


def test_is_round_number_true_on_even_handle():
    assert is_round_number(Decimal("1.10000"), step=Decimal("0.005")) is True


def test_is_round_number_false_off_handle():
    assert is_round_number(Decimal("1.10237"), step=Decimal("0.005")) is False


def test_is_round_number_default_step():
    assert is_round_number(Decimal("100.000")) is True
    assert is_round_number(Decimal("100.00317")) is False


def test_role_reversed_true_when_prior_resistance_now_holds_as_support():
    level = Level(price=Decimal("100"), kind="resistance", touches=3, angular=False)
    candles = [
        _c(0, "95", "98", "93", "96"),
        _c(1, "96", "99", "94", "97"),
        _c(2, "97", "105", "96", "104"),  # breaks decisively above 100
        _c(3, "104", "108", "102", "106"),
        _c(4, "106", "109", "100", "107"),  # returns, touches 100 from above, holds
    ]

    assert role_reversed(level, candles) is True


def test_role_reversed_false_when_level_never_broken():
    level = Level(price=Decimal("100"), kind="resistance", touches=3, angular=False)
    candles = [
        _c(0, "90", "95", "88", "92"),
        _c(1, "92", "96", "90", "94"),
        _c(2, "94", "97", "91", "93"),
    ]

    assert role_reversed(level, candles) is False


def test_nearest_level_returns_closest_by_absolute_price_distance():
    levels = [
        Level(price=Decimal("90"), kind="support", touches=3, angular=False),
        Level(price=Decimal("110"), kind="resistance", touches=3, angular=False),
        Level(price=Decimal("101"), kind="resistance", touches=4, angular=False),
    ]

    nearest = nearest_level(Decimal("100"), levels)

    assert nearest is not None
    assert nearest.price == Decimal("101")


def test_nearest_level_filters_by_kind():
    levels = [
        Level(price=Decimal("99"), kind="resistance", touches=3, angular=False),
        Level(price=Decimal("80"), kind="support", touches=3, angular=False),
    ]

    nearest = nearest_level(Decimal("100"), levels, kind="support")

    assert nearest is not None
    assert nearest.price == Decimal("80")


def test_nearest_level_returns_none_when_no_levels_of_kind():
    levels = [Level(price=Decimal("99"), kind="resistance", touches=3, angular=False)]

    assert nearest_level(Decimal("100"), levels, kind="support") is None
