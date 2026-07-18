"""Tests for keel.strategy.rules.turtle_breakout.TurtleBreakout.

TurtleBreakout is a DAILY rule: `detect()`/`exit_signal()` read the `ONE_DAY` key. Small
parameter values (`entry_lookback=5, exit_lookback=3, adx_period=5, atr_period=5`) are used
throughout so short, hand-built daily series are enough to exercise every gate: the Donchian-
high breakout, the ADX trend-confirmation filter, and the asymmetric Donchian-low channel exit.

The edge backtester passes only the rule's native series (`{ONE_DAY: ...}`, no `ONE_HOUR`
key), so these edge-style fixtures use every daily bar. A separate class exercises the
account-sim shape (`{ONE_HOUR: [...], ONE_DAY: [...]}`), where the last daily bar is the
current *forming* day and must be dropped to avoid intraday lookahead.
"""

from __future__ import annotations

from decimal import Decimal

from keel import agent
from keel.strategy.backtest import backtest
from keel.strategy.rules.turtle_breakout import TurtleBreakout
from keel.types import Candle, Granularity

_SMALL_PARAMS = {
    "entry_lookback": 5,
    "exit_lookback": 3,
    "adx_period": 5,
    "atr_period": 5,
}

# A minimal non-empty ONE_HOUR window: its mere presence (not its contents) is the account-sim
# signal that the last ONE_DAY bar is the current forming day and must be dropped.
_ANY_HOUR = [Candle(ts=0, open=Decimal("1"), high=Decimal("1"), low=Decimal("1"),
                    close=Decimal("1"), volume=Decimal("1"))]


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


class TestGranularity:
    def test_rule_trades_on_daily_candles(self) -> None:
        assert _rule().granularity is Granularity.ONE_DAY

    def test_defaults_are_day_counts(self) -> None:
        rule = TurtleBreakout(product_id="BTC-USD")
        assert rule.params["entry_lookback"] == 20
        assert rule.params["exit_lookback"] == 10
        assert rule.params["adx_period"] == 14
        assert rule.params["atr_period"] == 20


class TestDetectFires:
    def test_breakout_with_trend_confirmation_returns_long_setup(self) -> None:
        rule = _rule()
        candles = _breakout_candles()

        setup = rule.detect({Granularity.ONE_DAY: candles})

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

        setup = rule.detect({Granularity.ONE_DAY: candles})

        assert setup is not None
        assert setup.rr >= 1
        assert setup.rr == target_rr

    def test_context_carries_gate_explainability(self) -> None:
        rule = _rule()
        candles = _breakout_candles()

        setup = rule.detect({Granularity.ONE_DAY: candles})

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

        assert rule.detect({Granularity.ONE_DAY: _no_breakout_candles()}) is None

    def test_choppy_low_adx_rejects_nominal_breakout(self) -> None:
        rule = _rule()

        assert rule.detect({Granularity.ONE_DAY: _choppy_breakout_candles()}) is None

    def test_adx_threshold_gate_rejects_even_a_clean_breakout(self) -> None:
        # ADX is bounded 0-100 (`_breakout_candles()`'s clean uptrend saturates it at ~100) --
        # a threshold above that ceiling forces the trend gate to fail regardless of how
        # strong the nominal breakout is.
        rule = _rule(adx_threshold=150.0)
        candles = _breakout_candles()

        assert rule.detect({Granularity.ONE_DAY: candles}) is None

    def test_too_few_candles_returns_none(self) -> None:
        rule = _rule()

        assert rule.detect({Granularity.ONE_DAY: _trending_base(3)}) is None

    def test_missing_granularity_key_returns_none(self) -> None:
        rule = _rule()

        assert rule.detect({}) is None


class TestFormingBarLookaheadGuard:
    """The account sim (`portfolio_sim`) hands the rule BOTH an `ONE_HOUR` window and the
    `ONE_DAY` series whose LAST bar is the current, still-forming day. The presence of the
    `ONE_HOUR` key is the signal to drop that last daily bar and decide on completed days only.
    """

    def test_forming_bar_is_dropped_so_a_lookahead_breakout_does_not_fire(self) -> None:
        rule = _rule()
        # Second-to-last daily bar (the one detect should decide on in the account sim) pulls
        # back below the Donchian high -> no breakout on the completed day.
        base = _trending_base(19)
        pullback_price = float(base[-3].close)
        base.append(
            _candle(
                19, pullback_price - 0.5, pullback_price + 0.5, pullback_price - 0.5, pullback_price
            )
        )
        # Last bar = still-forming day = a big breakout that MUST be ignored intraday.
        breakout_price = float(base[-2].close) + 20.0
        base.append(
            _candle(
                20, breakout_price - 0.5, breakout_price + 0.5, breakout_price - 0.5, breakout_price
            )
        )

        with_forming = rule.detect({Granularity.ONE_HOUR: _ANY_HOUR, Granularity.ONE_DAY: base})
        assert with_forming is None

        # Sanity: without the ONE_HOUR key (edge pass) the forming breakout bar IS used -> fires.
        edge = rule.detect({Granularity.ONE_DAY: base})
        assert edge is not None

    def test_completed_breakout_fires_despite_non_triggering_forming_bar(self) -> None:
        rule = _rule()
        # The last COMPLETED bar is a clean breakout; the forming last bar does not itself break
        # out. Dropping the forming bar leaves the breakout as the current decided-on bar.
        base = _trending_base(20)
        breakout_price = float(base[-1].close) + 10.0
        base.append(
            _candle(
                20, breakout_price - 0.5, breakout_price + 0.5, breakout_price - 0.5, breakout_price
            )
        )
        # forming bar: a small pullback, not a new high
        forming_price = breakout_price - 3.0
        base.append(
            _candle(
                21, forming_price - 0.5, forming_price + 0.5, forming_price - 0.5, forming_price
            )
        )

        setup = rule.detect({Granularity.ONE_HOUR: _ANY_HOUR, Granularity.ONE_DAY: base})
        assert setup is not None
        # The decided-on bar is the completed breakout, not the forming pullback.
        assert setup.entry == Decimal(str(breakout_price))
        assert setup.ts == 20


class TestExitSignal:
    def test_fires_when_close_breaks_below_prior_channel_low(self) -> None:
        rule = _rule()
        held = rule.detect({Granularity.ONE_DAY: _breakout_candles()})
        assert held is not None

        fires = rule.exit_signal(held, {Granularity.ONE_DAY: _falling_candles()})

        assert fires is True

    def test_does_not_fire_while_still_trending_up(self) -> None:
        rule = _rule()
        candles = _breakout_candles()
        held = rule.detect({Granularity.ONE_DAY: candles})
        assert held is not None

        fires = rule.exit_signal(held, {Granularity.ONE_DAY: candles})

        assert fires is False

    def test_too_few_candles_returns_false(self) -> None:
        rule = _rule()

        assert rule.exit_signal(None, {Granularity.ONE_DAY: _trending_base(2)}) is False

    def test_forming_bar_dropped_in_account_sim_shape(self) -> None:
        rule = _rule()
        falling = _falling_candles()
        # Append a forming bar that bounces sharply up (would NOT trigger the exit on its own);
        # with the ONE_HOUR key present it's dropped, so the completed channel-break still fires.
        bounce = float(falling[-1].close) + 30.0
        falling_with_forming = [
            *falling,
            _candle(len(falling), bounce - 0.5, bounce + 0.5, bounce - 0.5, bounce),
        ]
        fires = rule.exit_signal(
            None,
            {Granularity.ONE_HOUR: _ANY_HOUR, Granularity.ONE_DAY: falling_with_forming},
        )
        assert fires is True


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
        assert rebuilt.granularity is Granularity.ONE_DAY

        # And the reconstructed rule actually works.
        setup = rebuilt.detect({Granularity.ONE_DAY: _breakout_candles()})
        assert setup is not None


def _daily_uptrend_with_pullbacks(cycles: int = 6) -> list[Candle]:
    """A long daily series that ratchets up in impulse-then-pullback cycles: enough down-moves
    to keep the Donchian channels meaningful and a strong enough net trend to warm ADX above
    the threshold, producing repeated 5-day-high breakout -> 3-day-low exit round trips.

    The rising bars deliberately OVERLAP (each bar's low sits at/below the prior bar's close)
    so a breakout-close entry -- which `strategy.backtest` treats as a pending limit that a
    LATER bar must trade through -- actually gets filled on the following bar rather than being
    gapped over.
    """
    candles: list[Candle] = []
    price = 100.0
    ts = 0
    for _ in range(cycles):
        # impulse leg: rising, overlapping bars (new highs -> breakout, ADX up, fills next bar)
        for _ in range(8):
            price += 3.0
            candles.append(_candle(ts, price - 3, price + 1, price - 4, price))
            ts += 1
        # pullback leg: falling bars that break the 3-day low -> channel exit
        for _ in range(5):
            price -= 4.0
            candles.append(_candle(ts, price + 3, price + 3, price - 1, price))
            ts += 1
    return candles


class TestDailyEdgeBacktestProducesTrades:
    def test_backtest_on_daily_series_yields_at_least_one_trade(self) -> None:
        rule = _rule()
        series = _daily_uptrend_with_pullbacks()

        result = backtest(rule, series)

        # The whole point of the daily move: the rule now actually fires and round-trips.
        assert result.n_trades >= 1
