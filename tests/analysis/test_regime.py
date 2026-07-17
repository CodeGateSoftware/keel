"""Tests for keel.analysis.regime: condition (trend structure) and phase (run/pullback)."""

from __future__ import annotations

from decimal import Decimal

from keel.analysis.regime import Condition, Phase, detect_condition, detect_phase, is_tradeable
from keel.types import Candle


def make_candle(ts: int, value: float, spread: Decimal = Decimal("0.5")) -> Candle:
    """Build a Candle around a single price `value`; high/low straddle it by `spread`."""
    v = Decimal(str(value))
    return Candle(ts=ts, open=v, high=v + spread, low=v - spread, close=v, volume=Decimal("1"))


def make_series(values: list[float]) -> list[Candle]:
    return [make_candle(i, v) for i, v in enumerate(values)]


class TestDetectCondition:
    def test_higher_highs_and_higher_lows_is_bullish(self):
        # Zigzag where each swing high and each swing low is greater than the prior one.
        candles = make_series([100, 110, 104, 112, 106, 118, 109, 124])

        assert detect_condition(candles) == Condition.BULLISH

    def test_lower_highs_and_lower_lows_is_bearish(self):
        # Mirror of the bullish fixture: each swing high/low is lower than the prior one.
        candles = make_series([124, 109, 118, 106, 112, 104, 110, 100])

        assert detect_condition(candles) == Condition.BEARISH

    def test_flat_swings_is_ranging(self):
        # Peaks hover near 110, troughs hover near 100 -- no directional drift in either.
        candles = make_series(
            [100, 110.02, 100.02, 109.98, 99.98, 110.03, 100.01, 109.99]
        )

        assert detect_condition(candles) == Condition.RANGING

    def test_erratic_series_is_choppy(self):
        # Expanding whipsaw: swing highs trend up while swing lows trend down (mismatched
        # structure) -- a textbook "structure violation".
        candles = make_series([100, 115, 92, 118, 88, 122, 85])

        assert detect_condition(candles) == Condition.CHOPPY

    def test_too_few_pivots_is_choppy(self):
        # Only three candles: not enough swing structure to classify a trend.
        candles = make_series([100, 105, 102])

        assert detect_condition(candles) == Condition.CHOPPY

    def test_lookback_window_limits_candles_considered(self):
        # A long bearish run followed by a short bullish tail: with a small lookback,
        # only the recent bullish structure should be visible.
        bearish_run = [200 - i * 5 for i in range(20)]  # steadily falling
        bullish_tail = [100, 110, 104, 112, 106, 118, 109, 124]
        candles = make_series(bearish_run + bullish_tail)

        assert detect_condition(candles, lookback=8) == Condition.BULLISH


class TestDetectPhase:
    def test_new_high_beyond_last_swing_is_run(self):
        # Uptrend where the final candle pushes to a fresh high past the last swing high.
        candles = make_series([100, 110, 104, 112, 106, 118, 109, 124, 130])

        assert detect_phase(candles) == Phase.RUN

    def test_retracement_in_uptrend_is_pullback(self):
        # Same uptrend, but the tail retraces down off the last swing high without
        # breaking back below the trend's prior swing low.
        candles = make_series([100, 110, 104, 112, 106, 118, 109, 124, 120, 116, 113])

        assert detect_phase(candles) == Phase.PULLBACK

    def test_new_low_beyond_last_swing_is_run_in_downtrend(self):
        # Downtrend where the final candle pushes to a fresh low past the last swing low.
        candles = make_series([124, 109, 118, 106, 112, 104, 110, 100, 94])

        assert detect_phase(candles) == Phase.RUN

    def test_bounce_in_downtrend_is_pullback(self):
        # Downtrend that bounces back up off the last swing low without breaking above
        # the trend's prior swing high.
        candles = make_series([124, 109, 118, 106, 112, 104, 110, 100, 104, 108, 111])

        assert detect_phase(candles) == Phase.PULLBACK

    def test_too_short_series_defaults_to_run(self):
        candles = make_series([100, 101])

        assert detect_phase(candles) == Phase.RUN


class TestIsTradeable:
    def test_choppy_is_not_tradeable(self):
        assert is_tradeable(Condition.CHOPPY) is False

    def test_bullish_is_tradeable(self):
        assert is_tradeable(Condition.BULLISH) is True

    def test_bearish_is_tradeable(self):
        assert is_tradeable(Condition.BEARISH) is True

    def test_ranging_is_tradeable(self):
        assert is_tradeable(Condition.RANGING) is True

    def test_choppy_condition_blocks_trading_end_to_end(self):
        # Erratic series -> CHOPPY -> not tradeable, exercised end-to-end per the
        # source-01 §1.2 rule: "choppy => no trades".
        candles = make_series([100, 115, 92, 118, 88, 122, 85])

        condition = detect_condition(candles)

        assert condition == Condition.CHOPPY
        assert is_tradeable(condition) is False
