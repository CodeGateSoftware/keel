"""Tests for keel.strategy.rules.turtle_breakout.TurtleBreakout.

Small parameter values (`entry_lookback=5, exit_lookback=3, adx_period=5, atr_period=5`) are
used throughout so short, hand-built candle series are enough to exercise every gate: the
Donchian-high breakout, the ADX trend-confirmation filter, and the asymmetric Donchian-low
channel exit.
"""

from __future__ import annotations

from decimal import Decimal

from keel import agent
from keel.strategy.rules.turtle_breakout import TurtleBreakout
from keel.types import Candle, Granularity

_SMALL_PARAMS = {
    "entry_lookback": 5,
    "exit_lookback": 3,
    "adx_period": 5,
    "atr_period": 5,
}


def _candle(ts: int, o: float, h: float, low: float, c: float) -> Candle:
    return Candle(
        ts=ts,
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(low)),
        close=Decimal(str(c)),
        volume=Decimal("1"),
    )


def _trending_base(n: int, start: float = 100.0, incr: float = 2.0, spread: float = 0.5):
    """A clean, monotonically rising series -- establishes a rising Donchian high and (since
    there are essentially no down-moves) a strongly trending ADX.
    """
    candles = []
    price = start
    for i in range(n):
        candles.append(_candle(i, price - spread, price + spread, price - spread, price))
        price += incr
    return candles


def _breakout_candles() -> list[Candle]:
    """A trending base, then a final candle that closes well above the prior Donchian high --
    fires every gate (breakout + ADX trend confirmation).
    """
    candles = _trending_base(20)
    breakout_price = float(candles[-1].close) + 10.0
    candles.append(
        _candle(
            len(candles),
            breakout_price - 0.5,
            breakout_price + 0.5,
            breakout_price - 0.5,
            breakout_price,
        )
    )
    return candles


def _no_breakout_candles() -> list[Candle]:
    """Same trending base, but the final candle pulls back well below the recent Donchian
    high -- fails the breakout gate even though the trend (and ADX) is otherwise intact.
    """
    candles = _trending_base(20)
    pullback_price = float(candles[-3].close)
    candles.append(
        _candle(
            len(candles),
            pullback_price - 0.5,
            pullback_price + 0.5,
            pullback_price - 0.5,
            pullback_price,
        )
    )
    return candles


def _choppy_breakout_candles() -> list[Candle]:
    """A whipsaw (non-trending) base -- keeps ADX well below the default 25 threshold -- then
    a final candle that nominally closes above the recent Donchian high. Exercises the ADX
    trend gate rejecting a "breakout" that isn't backed by real trend strength.
    """
    prices = [
        100, 104, 98, 103, 97, 105, 96, 106, 99, 102,
        100, 103, 98, 104, 101, 100, 102, 99, 101, 100,
    ]
    candles = [_candle(i, p - 0.5, p + 1.5, p - 1.5, p) for i, p in enumerate(prices)]
    breakout_price = float(max(c.high for c in candles[-5:])) + 5.0
    candles.append(
        _candle(
            len(candles),
            breakout_price - 0.5,
            breakout_price + 0.5,
            breakout_price - 0.5,
            breakout_price,
        )
    )
    return candles


def _falling_candles(n: int = 10, start: float = 200.0, decr: float = 3.0) -> list[Candle]:
    """A monotonically falling series -- establishes a falling Donchian low that the final
    close breaks below.
    """
    return _trending_base(n, start=start, incr=-decr)


def _rule(**overrides) -> TurtleBreakout:
    params = {"product_id": "BTC-USD", **_SMALL_PARAMS}
    params.update(overrides)
    return TurtleBreakout(**params)


class TestDetectFires:
    def test_breakout_with_trend_confirmation_returns_long_setup(self) -> None:
        rule = _rule()
        candles = _breakout_candles()

        setup = rule.detect({Granularity.ONE_HOUR: candles})

        assert setup is not None
        assert setup.product_id == "BTC-USD"
        assert setup.direction == "long"
        assert setup.stop < setup.entry < setup.target
        assert setup.entry == candles[-1].close
        assert setup.ts == candles[-1].ts

    def test_rr_equals_configured_target_rr(self) -> None:
        target_rr = Decimal("6")
        rule = _rule(target_rr=target_rr)
        candles = _breakout_candles()

        setup = rule.detect({Granularity.ONE_HOUR: candles})

        assert setup is not None
        assert setup.rr >= 1
        assert setup.rr == target_rr

    def test_context_carries_gate_explainability(self) -> None:
        rule = _rule()
        candles = _breakout_candles()

        setup = rule.detect({Granularity.ONE_HOUR: candles})

        assert setup is not None
        assert setup.context["rule_class"] == "trend_follow"
        assert setup.context["adx"] > 25.0
        assert setup.context["donchian_entry"] == 5
        assert setup.context["donchian_exit"] == 3
        assert "no_stop" not in setup.context
        assert "order_class" not in setup.context


class TestDetectRejectsNonSetups:
    def test_no_breakout_returns_none(self) -> None:
        rule = _rule()

        assert rule.detect({Granularity.ONE_HOUR: _no_breakout_candles()}) is None

    def test_choppy_low_adx_rejects_nominal_breakout(self) -> None:
        rule = _rule()

        assert rule.detect({Granularity.ONE_HOUR: _choppy_breakout_candles()}) is None

    def test_adx_threshold_gate_rejects_even_a_clean_breakout(self) -> None:
        # ADX is bounded 0-100 (`_breakout_candles()`'s clean uptrend saturates it at ~100) --
        # a threshold above that ceiling forces the trend gate to fail regardless of how
        # strong the nominal breakout is.
        rule = _rule(adx_threshold=150.0)
        candles = _breakout_candles()

        assert rule.detect({Granularity.ONE_HOUR: candles}) is None

    def test_too_few_candles_returns_none(self) -> None:
        rule = _rule()

        assert rule.detect({Granularity.ONE_HOUR: _trending_base(3)}) is None

    def test_missing_granularity_key_returns_none(self) -> None:
        rule = _rule()

        assert rule.detect({}) is None


class TestExitSignal:
    def test_fires_when_close_breaks_below_prior_channel_low(self) -> None:
        rule = _rule()
        held = rule.detect({Granularity.ONE_HOUR: _breakout_candles()})
        assert held is not None

        fires = rule.exit_signal(held, {Granularity.ONE_HOUR: _falling_candles()})

        assert fires is True

    def test_does_not_fire_while_still_trending_up(self) -> None:
        rule = _rule()
        candles = _breakout_candles()
        held = rule.detect({Granularity.ONE_HOUR: candles})
        assert held is not None

        fires = rule.exit_signal(held, {Granularity.ONE_HOUR: candles})

        assert fires is False

    def test_too_few_candles_returns_false(self) -> None:
        rule = _rule()

        assert rule.exit_signal(None, {Granularity.ONE_HOUR: _trending_base(2)}) is False


class TestDescribe:
    def test_returns_name_and_params(self) -> None:
        rule = _rule(atr_stop_mult=Decimal("3"), target_rr=Decimal("8"))

        described = rule.describe()

        assert described["name"] == "turtle_breakout"
        assert described["params"]["entry_lookback"] == 5
        assert described["params"]["exit_lookback"] == 3
        assert described["params"]["atr_stop_mult"] == Decimal("3")
        assert described["params"]["target_rr"] == Decimal("8")

    def test_round_trips_through_agent_build_rule(self) -> None:
        rule = _rule(atr_stop_mult=Decimal("3"), target_rr=Decimal("8"))
        described = rule.describe()

        # Simulate the DB round trip: Decimal params get JSON-serialized to strings.
        json_params = dict(described["params"])
        json_params["atr_stop_mult"] = str(json_params["atr_stop_mult"])
        json_params["target_rr"] = str(json_params["target_rr"])
        json_params["product_id"] = "BTC-USD"

        rebuilt = agent._build_rule({"kind": "turtle_breakout", "params": json_params})

        assert isinstance(rebuilt, TurtleBreakout)
        assert rebuilt.params == described["params"]
        assert isinstance(rebuilt.params["atr_stop_mult"], Decimal)
        assert isinstance(rebuilt.params["target_rr"], Decimal)

        # And the reconstructed rule actually works.
        setup = rebuilt.detect({Granularity.ONE_HOUR: _breakout_candles()})
        assert setup is not None
