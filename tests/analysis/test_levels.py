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

    # These fixtures are stamped at bare indices (ts 0,1,2...), so the shipped 14-day
    # touch separation would collapse every cluster to one touch. They are testing PRICE
    # CLUSTERING, not the separation policy (which has its own tests below), so pin it.
    levels = find_levels(
        candles, tolerance=Decimal("0.002"), min_touches=3, min_separation_sec=0
    )

    support_levels = [lvl for lvl in levels if lvl.kind == "support"]
    assert len(support_levels) == 1
    level = support_levels[0]
    assert isinstance(level, Level)
    assert level.price == Decimal("100")
    assert level.touches >= 3


def test_find_levels_three_bounces_yields_resistance_level_with_min_three_touches():
    candles = _three_touch_resistance_series()

    # These fixtures are stamped at bare indices (ts 0,1,2...), so the shipped 14-day
    # touch separation would collapse every cluster to one touch. They are testing PRICE
    # CLUSTERING, not the separation policy (which has its own tests below), so pin it.
    levels = find_levels(
        candles, tolerance=Decimal("0.002"), min_touches=3, min_separation_sec=0
    )

    resistance_levels = [lvl for lvl in levels if lvl.kind == "resistance"]
    assert len(resistance_levels) == 1
    level = resistance_levels[0]
    assert level.price == Decimal("300")
    assert level.touches >= 3


def test_find_levels_excludes_level_touched_only_twice_at_min_touches_three():
    candles = _two_touch_series()

    # These fixtures are stamped at bare indices (ts 0,1,2...), so the shipped 14-day
    # touch separation would collapse every cluster to one touch. They are testing PRICE
    # CLUSTERING, not the separation policy (which has its own tests below), so pin it.
    levels = find_levels(
        candles, tolerance=Decimal("0.002"), min_touches=3, min_separation_sec=0
    )

    prices = [lvl.price for lvl in levels]
    assert Decimal("200") not in prices


def test_find_levels_includes_level_touched_twice_when_min_touches_two():
    candles = _two_touch_series()

    # These fixtures are stamped at bare indices (ts 0,1,2...), so the shipped 14-day
    # touch separation would collapse every cluster to one touch. They are testing PRICE
    # CLUSTERING, not the separation policy (which has its own tests below), so pin it.
    levels = find_levels(
        candles, tolerance=Decimal("0.002"), min_touches=2, min_separation_sec=0
    )

    support_levels = [lvl for lvl in levels if lvl.kind == "support"]
    assert len(support_levels) == 1
    assert support_levels[0].price == Decimal("200")
    assert support_levels[0].touches == 2


def test_is_round_number_false_on_two_decimal_price_away_from_a_handle():
    """The #225 regression: every 2dp price is an exact multiple of the old absolute
    `step=0.005`, so the check could never fail on BTC/ETH/PAXG and handed those three
    assets a constant +1 CTS point. 64,975.78 is a quarter of the way into a 1,000-wide
    BTC handle interval and must score absent.
    """
    assert is_round_number(Decimal("64975.78")) is False


def test_is_round_number_false_across_the_two_decimal_allowlist_scales():
    """No 2dp-quoted price may score present merely for being quoted to 2dp.

    Every value here is an exact multiple of the old absolute `step=0.005` and so returned
    `True` before #225. Note the asymmetry that falls out of a relative grid and is correct:
    below 1.00 the two-decimal quote grid IS the two-significant-figure handle grid, so `0.01`
    and `0.38` are genuine handles and must keep scoring present. The bug was never "2dp
    prices"; it was that an absolute step cannot see scale at all.
    """
    for price in ("64975.78", "103412.99", "3421.07", "4127.53", "12.34", "1.23"):
        assert is_round_number(Decimal(price)) is False, price
    for handle in ("0.01", "0.38"):
        assert is_round_number(Decimal(handle)) is True, handle


def test_is_round_number_true_on_even_handle_at_every_scale():
    """The same relative position on the handle grid scores the same at 65,000 and at 0.38."""
    for price in ("65000", "65000.00", "3400.00", "0.38", "0.0071", "1.10"):
        assert is_round_number(Decimal(price)) is True, price


def test_is_round_number_is_scale_invariant():
    """Multiplying by a power of ten moves the grid with the price, so the answer is
    unchanged -- the property the absolute-step version could not have.
    """
    for digits in ("64975.78", "65000", "38200", "1", "7.5"):
        base = Decimal(digits)
        expected = is_round_number(base)
        for exponent in (-8, -3, 3, 8):
            assert is_round_number(base.scaleb(exponent)) is expected, f"{digits}e{exponent}"


def test_is_round_number_near_a_handle_at_small_scale():
    """ADA/XLM scale: 0.3801 is inside 0.38's band, 0.3835 is not."""
    assert is_round_number(Decimal("0.3801")) is True
    assert is_round_number(Decimal("0.3835")) is False
    assert is_round_number(Decimal("0.070008")) is True
    assert is_round_number(Decimal("0.073451")) is False


def test_is_round_number_boundary_is_inclusive():
    """`tolerance` is a fraction of the handle spacing; at exactly the band edge the
    factor is present, one ulp outside it is not.
    """
    # 65,000 sits on a 1,000-wide grid, so the default 0.02 band is +/- 20.
    assert is_round_number(Decimal("65020")) is True
    assert is_round_number(Decimal("65020.01")) is False
    assert is_round_number(Decimal("64980")) is True
    assert is_round_number(Decimal("64979.99")) is False


def test_is_round_number_tolerance_is_relative_not_absolute():
    """A wider `tolerance` widens the band in proportion to the grid, at every scale."""
    assert is_round_number(Decimal("64975.78"), tolerance=Decimal("0.1")) is True
    assert is_round_number(Decimal("0.3897"), tolerance=Decimal("0.1")) is True
    assert is_round_number(Decimal("64975.78"), tolerance=Decimal("0")) is False
    assert is_round_number(Decimal("65000"), tolerance=Decimal("0")) is True


def test_is_round_number_degenerate_prices_do_not_divide_by_zero():
    """Zero has no order of magnitude and negatives are not prices: both score absent
    rather than raising or taking a modulo by zero.
    """
    assert is_round_number(Decimal("0")) is False
    assert is_round_number(Decimal("-0")) is False
    assert is_round_number(Decimal("-65000")) is False
    assert is_round_number(Decimal("NaN")) is False
    assert is_round_number(Decimal("Infinity")) is False


def test_is_round_number_survives_extreme_magnitudes():
    """No overflow, no context error, no modulo-by-zero at either end of the range.

    `3.7e28` and `3.7e-28` ARE handles (two significant figures); `3.75e28` is not. The point
    of the pair is that the grid tracks the exponent for 60 orders of magnitude.
    """
    assert is_round_number(Decimal("1E-30")) is True
    assert is_round_number(Decimal("3.7E-28")) is True
    assert is_round_number(Decimal("3.75E-28")) is False
    assert is_round_number(Decimal("1E+30")) is True
    assert is_round_number(Decimal("3.7E+28")) is True
    assert is_round_number(Decimal("3.75E+28")) is False


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


# -- minimum touch separation (KB §81.5) ---------------------------------------


def _pivot_series(pivot_indices: list[int], step_sec: int) -> list[Candle]:
    """Bars at `i * step_sec`, dipping to 100 at each pivot index and 104 elsewhere.

    `swing_lows(lookback=2)` needs a pivot strictly lower than every bar within two on each
    side, so pivot indices must be at least 3 apart and at least 2 from either end.
    """
    length = max(pivot_indices) + 3
    marks = set(pivot_indices)
    return [
        _c(i * step_sec, "105", "106", "100" if i in marks else "104", "105")
        for i in range(length)
    ]


def test_touches_within_the_window_collapse_to_one():
    """Three pivots inside half a day are ONE visit to the level, not three."""
    candles = _pivot_series([3, 7, 11], step_sec=3600)

    counted = find_levels(candles, min_touches=1, min_separation_sec=0)
    separated = find_levels(candles, min_touches=1)

    assert max(level.touches for level in counted) == 3
    assert max(level.touches for level in separated) == 1


def test_touches_two_weeks_apart_count_separately():
    # 5-day bars, pivots 4 bars apart -> 20 days between touches, clear of the 14-day floor.
    candles = _pivot_series([3, 7, 11], step_sec=5 * 24 * 3600)
    separated = find_levels(candles, min_touches=1)
    assert max(level.touches for level in separated) == 3


def test_a_compressed_cluster_no_longer_reaches_min_touches():
    """The point of the change: tight chop must stop manufacturing 'strong' levels."""
    candles = _pivot_series([3, 7, 11], step_sec=3600)
    assert find_levels(candles, min_touches=3, min_separation_sec=0)
    assert find_levels(candles, min_touches=3) == []


def test_separation_is_greedy_from_the_earliest_touch():
    # 4-day bars at indices 3, 5(+8d: too close), 7(+16d from the first: counts), 11(counts).
    candles = _pivot_series([3, 7, 11, 15], step_sec=4 * 24 * 3600)
    levels = find_levels(candles, min_touches=1)
    assert max(level.touches for level in levels) == 4
