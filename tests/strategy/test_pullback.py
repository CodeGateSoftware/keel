"""Tests for halal_cb.strategy.rules.pullback_continuation.PullbackContinuation.

Fixtures below are hand-tuned candle series verified to satisfy (or deliberately fail) the
rule's gates: bullish condition + pullback phase (analysis.regime), EMA fan alignment
(analysis.indicators), price in the configured entry zone, and a qualifying signal candle
(analysis.candles). Small `ema_periods=(3, 5, 8)` are used so a short, hand-verifiable
series is enough to move the EMAs into alignment (the rule itself defaults to (8, 20, 50)
per source-02 §2.1; the periods are just a tunable parameter).
"""

from __future__ import annotations

from decimal import Decimal

from halal_cb.analysis import indicators
from halal_cb.strategy.rules.pullback_continuation import PullbackContinuation
from halal_cb.types import Candle, Granularity

_EMA_PERIODS = (3, 5, 8)
_BUFFER_TICKS = Decimal("0.02")


def _candle(ts: int, o: float, h: float, low: float, c: float) -> Candle:
    return Candle(
        ts=ts,
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(low)),
        close=Decimal(str(c)),
        volume=Decimal("1"),
    )


def _flat_series(values: list[float], spread: float = 0.5) -> list[Candle]:
    """Simple symmetric candles (open=close=value, high/low straddle by `spread`)."""
    return [_candle(i, v, v + spread, v - spread, v) for i, v in enumerate(values)]


def _bullish_pullback_candles() -> list[Candle]:
    """An impulse leg (clear swing low, then higher highs) followed by a shallow
    single-bar pullback that closes as a bullish pin bar (open+close in the upper 30%
    of its range, below the fan's fast EMA) -- satisfies every `detect()` gate.
    """
    leg = [130, 124, 118, 110, 116, 122, 117, 126, 120, 132, 128]
    candles = _flat_series(leg)
    # Crafted signal candle: pin bar (bullish), entirely below the fast EMA (entry zone).
    candles.append(_candle(len(leg), 127.6, 128.0, 125.0, 127.9))
    return candles


def _bearish_mirror_candles() -> list[Candle]:
    """Mirror image of `_bullish_pullback_candles()` around 240: inverts the trend
    structure and turns the closing bullish pin bar into a bearish one, exercising
    `exit_signal()`'s bearish-mirror gate.
    """
    mirror = Decimal("240")
    mirrored = []
    for c in _bullish_pullback_candles():
        mirrored.append(
            Candle(
                ts=c.ts,
                open=mirror - c.open,
                high=mirror - c.low,
                low=mirror - c.high,
                close=mirror - c.close,
                volume=c.volume,
            )
        )
    return mirrored


def _no_signal_pattern_candles() -> list[Candle]:
    """Same impulse leg, but the final candle's body straddles the mid-range instead of
    sitting in the outer 30% -- fails the pin-bar gate while every other gate still holds.
    """
    leg = [130, 124, 118, 110, 116, 122, 117, 126, 120, 132, 128]
    candles = _flat_series(leg)
    candles.append(_candle(len(leg), 126.0, 128.0, 125.0, 126.6))
    return candles


def _choppy_candles() -> list[Candle]:
    """Erratic whipsaw series (mismatched swing-high/low trends) -> Condition.CHOPPY."""
    return _flat_series([100, 115, 92, 118, 88, 122, 85])


def _run_phase_candles() -> list[Candle]:
    """Bullish uptrend still making new highs -> Phase.RUN, not a pullback."""
    return _flat_series([100, 110, 104, 112, 106, 118, 109, 124, 130])


def _rule(**overrides) -> PullbackContinuation:
    params = {
        "product_id": "BTC-USD",
        "granularity": Granularity.ONE_HOUR,
        "ema_periods": _EMA_PERIODS,
        "buffer_ticks": _BUFFER_TICKS,
    }
    params.update(overrides)
    return PullbackContinuation(**params)


class TestDetectBullishSetup:
    def test_detects_textbook_pullback_long(self) -> None:
        rule = _rule()
        candles = _bullish_pullback_candles()

        setup = rule.detect({Granularity.ONE_HOUR: candles})

        assert setup is not None
        assert setup.product_id == "BTC-USD"
        assert setup.direction == "long"
        assert setup.ts == candles[-1].ts

    def test_entry_is_buy_stop_above_signal_high_plus_buffer(self) -> None:
        rule = _rule()
        candles = _bullish_pullback_candles()

        setup = rule.detect({Granularity.ONE_HOUR: candles})

        assert setup is not None
        assert setup.entry == candles[-1].high + _BUFFER_TICKS

    def test_fixed_stop_is_signal_low_minus_buffer(self) -> None:
        rule = _rule(stop_method="fixed")
        candles = _bullish_pullback_candles()

        setup = rule.detect({Granularity.ONE_HOUR: candles})

        assert setup is not None
        assert setup.stop == candles[-1].low - _BUFFER_TICKS

    def test_measured_1to1_target_and_rr(self) -> None:
        rule = _rule(target_method="measured_1to1")
        candles = _bullish_pullback_candles()

        setup = rule.detect({Granularity.ONE_HOUR: candles})

        assert setup is not None
        risk = setup.entry - setup.stop
        assert setup.target == setup.entry + risk
        assert setup.rr == Decimal(1)

    def test_context_carries_gate_explainability(self) -> None:
        rule = _rule()
        candles = _bullish_pullback_candles()

        setup = rule.detect({Granularity.ONE_HOUR: candles})

        assert setup is not None
        assert setup.context["condition"] == "BULLISH"
        assert setup.context["phase"] == "PULLBACK"
        assert setup.context["fan_aligned"] is True
        assert setup.context["pattern"] == "pin_bar"
        assert setup.context["stop_method"] == "fixed"
        assert setup.context["target_method"] == "measured_1to1"


class TestDetectRejectsNonSetups:
    def test_choppy_market_returns_none(self) -> None:
        rule = _rule()

        assert rule.detect({Granularity.ONE_HOUR: _choppy_candles()}) is None

    def test_run_phase_without_pullback_returns_none(self) -> None:
        rule = _rule()

        assert rule.detect({Granularity.ONE_HOUR: _run_phase_candles()}) is None

    def test_missing_signal_pattern_returns_none(self) -> None:
        rule = _rule()

        assert rule.detect({Granularity.ONE_HOUR: _no_signal_pattern_candles()}) is None

    def test_missing_granularity_key_returns_none(self) -> None:
        rule = _rule()

        assert rule.detect({}) is None

    def test_too_few_candles_returns_none(self) -> None:
        rule = _rule()
        candles = _bullish_pullback_candles()[:1]

        assert rule.detect({Granularity.ONE_HOUR: candles}) is None


class TestTargetMethods:
    def test_swing_target_is_previous_swing_high(self) -> None:
        from halal_cb.analysis import levels

        rule = _rule(target_method="swing")
        candles = _bullish_pullback_candles()
        expected_target = candles[levels.swing_highs(candles)[-1]].high

        setup = rule.detect({Granularity.ONE_HOUR: candles})

        assert setup is not None
        assert setup.target == expected_target
        assert setup.target > setup.entry
        assert setup.rr == (setup.target - setup.entry) / (setup.entry - setup.stop)

    def test_fib_ext_target_is_1272_extension_of_last_swing_move(self) -> None:
        from halal_cb.analysis import levels

        rule = _rule(target_method="fib_ext")
        candles = _bullish_pullback_candles()
        swing_high = candles[levels.swing_highs(candles)[-1]].high
        swing_low = candles[levels.swing_lows(candles)[-1]].low
        expected_target = indicators.fib_extensions(swing_high, swing_low)["1.272"]

        setup = rule.detect({Granularity.ONE_HOUR: candles})

        assert setup is not None
        assert setup.target == expected_target
        assert setup.target > setup.entry

    def test_three_target_methods_all_beat_the_measured_1to1_floor(self) -> None:
        # 1:1 measured move is the conservative floor (source-02 §2.1); swing/fib_ext
        # extend further given this fixture's structure (source-07 §7.1/§7.4).
        candles = _bullish_pullback_candles()
        rr_by_method = {}
        for method in ("measured_1to1", "swing", "fib_ext"):
            rule = _rule(target_method=method)
            setup = rule.detect({Granularity.ONE_HOUR: candles})
            assert setup is not None
            rr_by_method[method] = setup.rr

        assert rr_by_method["measured_1to1"] == Decimal(1)
        assert rr_by_method["swing"] > rr_by_method["measured_1to1"]
        assert rr_by_method["fib_ext"] > rr_by_method["swing"]


class TestStopMethods:
    def test_atr_stop_differs_from_fixed_stop(self) -> None:
        candles = _bullish_pullback_candles()

        fixed_setup = _rule(stop_method="fixed").detect({Granularity.ONE_HOUR: candles})
        atr_setup = _rule(stop_method="atr").detect({Granularity.ONE_HOUR: candles})

        assert fixed_setup is not None
        assert atr_setup is not None
        assert atr_setup.stop != fixed_setup.stop

    def test_atr_stop_is_signal_low_minus_last_atr_value(self) -> None:
        rule = _rule(stop_method="atr")
        candles = _bullish_pullback_candles()
        atr_vals = indicators.atr(candles, period=14)
        expected_stop = candles[-1].low - Decimal(str(atr_vals[-1]))

        setup = rule.detect({Granularity.ONE_HOUR: candles})

        assert setup is not None
        assert setup.stop == expected_stop
        assert setup.entry > setup.stop


class TestExitSignal:
    def test_fires_on_bearish_mirror(self) -> None:
        rule = _rule()
        held = rule.detect({Granularity.ONE_HOUR: _bullish_pullback_candles()})
        assert held is not None

        fires = rule.exit_signal(held, {Granularity.ONE_HOUR: _bearish_mirror_candles()})

        assert fires is True

    def test_does_not_fire_while_still_bullish(self) -> None:
        rule = _rule()
        held = rule.detect({Granularity.ONE_HOUR: _bullish_pullback_candles()})
        assert held is not None

        fires = rule.exit_signal(held, {Granularity.ONE_HOUR: _bullish_pullback_candles()})

        assert fires is False

    def test_does_not_fire_on_choppy_data(self) -> None:
        rule = _rule()
        held = rule.detect({Granularity.ONE_HOUR: _bullish_pullback_candles()})
        assert held is not None

        fires = rule.exit_signal(held, {Granularity.ONE_HOUR: _choppy_candles()})

        assert fires is False


class TestDescribe:
    def test_returns_name_and_params(self) -> None:
        rule = _rule(target_method="swing", stop_method="atr")

        described = rule.describe()

        assert described["name"] == "pullback_continuation"
        assert described["params"]["target_method"] == "swing"
        assert described["params"]["stop_method"] == "atr"
        assert described["params"]["ema_periods"] == _EMA_PERIODS
