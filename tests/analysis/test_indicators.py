"""Tests for halal_cb.analysis.indicators.

Numeric functions (ema/rsi/macd/atr) are checked against small hand-computed
reference values (tolerance 1e-6). Fib retracement/extension levels are checked
exactly as Decimal. Divergence and deceleration are checked on synthetic fixtures.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from halal_cb.analysis.indicators import (
    atr,
    deceleration,
    ema,
    ema_fan,
    fan_aligned,
    fib_extensions,
    fib_retracements,
    is_overbought,
    is_oversold,
    macd,
    rsi,
    rsi_divergence,
)
from halal_cb.types import Candle


def _candle(ts, o, h, low, c, v="1"):
    return Candle(
        ts=ts,
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low),
        close=Decimal(c),
        volume=Decimal(v),
    )


# ---------------------------------------------------------------------------
# ema
# ---------------------------------------------------------------------------


def test_ema_matches_hand_computed_reference():
    # alpha = 2/(3+1) = 0.5; ema[0]=v0, ema[i]=0.5*v[i]+0.5*ema[i-1]
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    result = ema(values, 3)
    expected = [1, 1.5, 2.25, 3.125, 4.0625]
    assert result == pytest.approx(expected, abs=1e-6)


def test_ema_length_matches_input():
    values = [1.0, 2.0, 3.0]
    assert len(ema(values, 2)) == len(values)


# ---------------------------------------------------------------------------
# ema_fan / fan_aligned
# ---------------------------------------------------------------------------


def test_ema_fan_returns_ema_per_period_keyed_by_period():
    closes = [1.0, 2.0, 3.0, 4.0, 5.0]
    candles = [_candle(i, c, c, c, c) for i, c in enumerate(closes)]
    fan = ema_fan(candles, periods=(2, 3))
    assert set(fan.keys()) == {2, 3}
    assert fan[2] == pytest.approx(ema(closes, 2), abs=1e-6)
    assert fan[3] == pytest.approx(ema(closes, 3), abs=1e-6)


def test_fan_aligned_bullish_when_fast_above_mid_above_slow():
    fan = {8: [10.0, 12.0], 20: [10.0, 11.0], 50: [10.0, 10.5]}
    assert fan_aligned(fan, 1, "bullish") is True
    assert fan_aligned(fan, 1, "bearish") is False


def test_fan_aligned_bearish_when_fast_below_mid_below_slow():
    fan = {8: [10.0, 9.0], 20: [10.0, 10.0], 50: [10.0, 11.0]}
    assert fan_aligned(fan, 1, "bearish") is True
    assert fan_aligned(fan, 1, "bullish") is False


def test_fan_aligned_false_when_not_ordered():
    fan = {8: [10.0], 20: [12.0], 50: [11.0]}
    assert fan_aligned(fan, 0, "bullish") is False
    assert fan_aligned(fan, 0, "bearish") is False


# ---------------------------------------------------------------------------
# rsi
# ---------------------------------------------------------------------------


def test_rsi_matches_hand_computed_reference():
    values = [10, 11, 12, 11, 10, 11, 12, 13]
    result = rsi(values, period=3)
    expected = [
        50.0,
        50.0,
        50.0,
        66.66666666666666,
        44.44444444444444,
        62.96296296296296,
        75.30864197530865,
        83.53909465020577,
    ]
    assert result == pytest.approx(expected, abs=1e-6)


def test_rsi_all_gains_is_100():
    values = [1, 2, 3, 4, 5, 6]
    result = rsi(values, period=3)
    assert result[3] == pytest.approx(100.0, abs=1e-6)


def test_is_overbought_and_is_oversold_thresholds():
    assert is_overbought(85.0) is True
    assert is_overbought(80.0) is False
    assert is_overbought(75.0) is False
    assert is_oversold(15.0) is True
    assert is_oversold(20.0) is False
    assert is_oversold(25.0) is False
    assert is_overbought(90.0, thr=95.0) is False
    assert is_oversold(5.0, thr=10.0) is True


# ---------------------------------------------------------------------------
# rsi_divergence
# ---------------------------------------------------------------------------


def test_rsi_divergence_bearish_on_higher_high_lower_rsi_high():
    highs = [10, 11, 12, 11, 10, 11, 13, 12, 11]
    lows = [h - 1 for h in highs]
    closes = [h - Decimal("0.5") for h in [Decimal(x) for x in highs]]
    candles = [
        _candle(i, closes[i], highs[i], lows[i], closes[i]) for i in range(len(highs))
    ]
    rsi_vals = [50.0] * len(highs)
    rsi_vals[2] = 70.0
    rsi_vals[6] = 60.0  # price higher high (13>12) but RSI lower high (60<70)

    assert rsi_divergence(candles, rsi_vals, lookback=20) == "bearish"


def test_rsi_divergence_bullish_on_lower_low_higher_rsi_low():
    lows = [20, 19, 18, 19, 20, 19, 17, 18, 19]
    highs = [low + 1 for low in lows]
    closes = [low + Decimal("0.5") for low in [Decimal(x) for x in lows]]
    candles = [
        _candle(i, closes[i], highs[i], lows[i], closes[i]) for i in range(len(lows))
    ]
    rsi_vals = [50.0] * len(lows)
    rsi_vals[2] = 30.0
    rsi_vals[6] = 35.0  # price lower low (17<18) but RSI higher low (35>30)

    assert rsi_divergence(candles, rsi_vals, lookback=20) == "bullish"


def test_rsi_divergence_none_when_price_and_rsi_converge():
    highs = [10, 11, 12, 11, 10, 11, 13, 12, 11]
    lows = [h - 1 for h in highs]
    closes = [h - Decimal("0.5") for h in [Decimal(x) for x in highs]]
    candles = [
        _candle(i, closes[i], highs[i], lows[i], closes[i]) for i in range(len(highs))
    ]
    rsi_vals = [50.0] * len(highs)
    rsi_vals[2] = 60.0
    rsi_vals[6] = 70.0  # both price and RSI make a higher high -> convergence

    assert rsi_divergence(candles, rsi_vals, lookback=20) is None


def test_rsi_divergence_none_on_too_short_series():
    candles = [_candle(0, 1, 1, 1, 1)]
    assert rsi_divergence(candles, [50.0], lookback=20) is None


# ---------------------------------------------------------------------------
# macd
# ---------------------------------------------------------------------------


def test_macd_matches_hand_computed_reference():
    values = [10, 11, 12, 11, 10, 11, 12, 13]
    macd_line, signal_line, hist = macd(values, fast=2, slow=4, signal=2)

    expected_macd = [
        0,
        0.2666666666666675,
        0.5155555555555562,
        0.161185185185186,
        -0.21933827160493813,
        0.02971390946501984,
        0.33826730315500697,
        0.5764400343850014,
    ]
    expected_signal = [
        0,
        0.1777777777777783,
        0.4029629629629636,
        0.24177777777777854,
        -0.06563292181069921,
        -0.002068367626886513,
        0.22482207956104247,
        0.45923404944368174,
    ]

    assert macd_line == pytest.approx(expected_macd, abs=1e-6)
    assert signal_line == pytest.approx(expected_signal, abs=1e-6)
    assert hist == pytest.approx(
        [m - s for m, s in zip(expected_macd, expected_signal, strict=True)], abs=1e-6
    )


def test_macd_returns_three_equal_length_lists():
    values = [float(i) for i in range(1, 30)]
    macd_line, signal_line, hist = macd(values)
    assert len(macd_line) == len(signal_line) == len(hist) == len(values)


# ---------------------------------------------------------------------------
# atr
# ---------------------------------------------------------------------------


def test_atr_matches_hand_computed_reference():
    highs = [10, 11, 12, 11, 12]
    lows = [9, 10, 11, 10, 11]
    closes = [Decimal("9.5"), Decimal("10.5"), Decimal("11.5"), Decimal("10.5"), Decimal("11.5")]
    candles = [
        _candle(i, closes[i], highs[i], lows[i], closes[i]) for i in range(len(highs))
    ]

    result = atr(candles, period=3)
    expected = [
        1.3333333333333333,
        1.3333333333333333,
        1.3333333333333333,
        1.3888888888888886,
        1.4259259259259256,
    ]
    assert result == pytest.approx(expected, abs=1e-6)


def test_atr_length_matches_candles():
    candles = [_candle(i, 1, 2, 0, 1) for i in range(5)]
    assert len(atr(candles, period=3)) == 5


# ---------------------------------------------------------------------------
# fib_retracements / fib_extensions
# ---------------------------------------------------------------------------


def test_fib_retracements_exact_decimal():
    levels = fib_retracements(Decimal("100"), Decimal("50"))
    assert levels == {
        "0.382": Decimal("80.900"),
        "0.5": Decimal("75.0"),
        "0.618": Decimal("69.100"),
        "0.786": Decimal("60.700"),
        "0.886": Decimal("55.700"),
    }
    assert all(isinstance(v, Decimal) for v in levels.values())


def test_fib_extensions_exact_decimal():
    levels = fib_extensions(Decimal("100"), Decimal("50"))
    assert levels == {
        "1.272": Decimal("113.600"),
        "1.618": Decimal("130.900"),
    }
    assert all(isinstance(v, Decimal) for v in levels.values())


# ---------------------------------------------------------------------------
# deceleration
# ---------------------------------------------------------------------------


def test_deceleration_true_on_three_shrinking_bodies():
    candles = [
        _candle(0, 100, 106, 99, 105),  # body 5
        _candle(1, 105, 109, 104, 108),  # body 3
        _candle(2, 108, 110, 107, 109),  # body 1
    ]
    assert deceleration(candles, n=3) is True


def test_deceleration_false_when_bodies_grow():
    candles = [
        _candle(0, 100, 102, 99, 101),  # body 1
        _candle(1, 101, 105, 100, 104),  # body 3
        _candle(2, 104, 111, 103, 110),  # body 6
    ]
    assert deceleration(candles, n=3) is False


def test_deceleration_false_when_too_few_candles():
    candles = [_candle(0, 100, 106, 99, 105), _candle(1, 105, 109, 104, 108)]
    assert deceleration(candles, n=3) is False
