"""Tests for keel.strategy.rules.turtle_breakout.TurtleBreakout.

TurtleBreakout is a DAILY rule BY DEFAULT: `detect()`/`exit_signal()` read the `ONE_DAY` key
that its `granularity` param declares. That param is persisted (`RsiMeanReversion.timeframe`'s
convention, not `PullbackContinuation`'s non-persisted one) so an hourly evidence profile
(issue #337) can run the SAME rule on `ONE_HOUR` bars -- measured there at n≈250 per 5 years
but NET-NEGATIVE (docs/experiments/2026-08-13, 0 of 90 / 0 of 82 cells); the param exists to
make that measurement collectable, not profitable. Small parameter values (`entry_lookback=5,
exit_lookback=3, adx_period=5, atr_period=5`) are used throughout so short, hand-built daily
series are enough to exercise every gate: the Donchian-high breakout, the ADX
trend-confirmation filter, and the asymmetric Donchian-low channel exit.

The edge backtester passes only the rule's native series (`{ONE_DAY: ...}`, no `ONE_HOUR`
key), so these edge-style fixtures use every daily bar. A separate class exercises the
account-sim shape (`{ONE_HOUR: [...], ONE_DAY: [...]}`), where the last daily bar is the
current *forming* day and must be dropped to avoid intraday lookahead.
"""

from __future__ import annotations

from dataclasses import replace
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


_DAY = 24 * 60 * 60
_HOUR = 60 * 60
#: An epoch second that is exactly a UTC day boundary (86400 * 20660). The forming-bar guard
#: compares real bar timestamps, so the day-shaped fixtures need day-aligned ones.
_DAY_ZERO = 1_785_024_000


def _day_candle(ts: int, o: float, h: float, low: float, c: float) -> Candle:
    return _candle(ts, o, h, low, c)


def _on_days(candles: list[Candle], start_ts: int = _DAY_ZERO) -> list[Candle]:
    """Re-stamp a hand-built series onto consecutive UTC day boundaries.

    The shared fixtures number their bars 0, 1, 2...; the live-agent shape needs timestamps a
    real daily series would have, since that is what the guard reasons about.
    """
    return [replace(c, ts=start_ts + i * _DAY) for i, c in enumerate(candles)]


def _rule(**overrides) -> TurtleBreakout:
    params = {"product_id": "BTC-USD", **_SMALL_PARAMS}
    params.update(overrides)
    return TurtleBreakout(**params)


class TestGranularity:
    def test_rule_trades_on_daily_candles(self) -> None:
        assert _rule().granularity is Granularity.ONE_DAY

    def test_defaults_are_day_counts(self) -> None:
        rule = TurtleBreakout(product_id="BTC-USD")
        # 40/20 = walk-forward OOS-validated default (longer than the classic 20/10 Turtle S1)
        assert rule.params["entry_lookback"] == 40
        assert rule.params["exit_lookback"] == 20
        assert rule.params["adx_period"] == 14
        assert rule.params["atr_period"] == 20


class TestDeclaredGranularity:
    """The `granularity` param: how the hourly evidence profile (issue #337) runs this rule.

    Everything downstream keys off the rule's DECLARED timeframe -- `engine.
    _trading_granularity`, `agent._entry_gate_granularity`, `backtest._rule_trading_tf` -- so
    an hourly turtle requires the declaration itself to be a param, persisted the way
    `RsiMeanReversion.timeframe` is (its `.value` string inside `params`, coerced back by
    `agent._GRANULARITY_PARAMS`). `PullbackContinuation` is the counter-example this must not
    copy: it accepts `granularity` but does NOT persist it, so `rules add` refuses it outright
    rather than silently rebuild the rule at the default on a different candle series.
    """

    def test_the_default_is_daily_and_is_persisted(self) -> None:
        rule = TurtleBreakout(product_id="BTC-USD")

        assert rule.granularity is Granularity.ONE_DAY
        # `.value` (a plain string), so `describe()`'s params are JSON-plain and the row
        # round-trips without a second serialization step.
        assert rule.params["granularity"] == "ONE_DAY"

    def test_an_hourly_rule_detects_on_the_one_hour_series(self) -> None:
        rule = _rule(granularity=Granularity.ONE_HOUR)

        setup = rule.detect({Granularity.ONE_HOUR: _breakout_candles()})

        assert setup is not None
        assert setup.ts == _breakout_candles()[-1].ts

    def test_an_hourly_rule_does_not_read_the_one_day_series(self) -> None:
        """A daily-keyed breakout alone must not fire an hourly rule: the declared series is
        absent, exactly like `test_missing_granularity_key_returns_none` for the daily default.

        This is the discrimination that makes the param real rather than decorative -- before
        it existed, `detect()` read `ONE_DAY` unconditionally, so no configuration could point
        the rule at another series (docs/experiments/2026-08-11 §7: the hourly corpus had to
        hand hourly bars to a rule that "believed they were days").
        """
        rule = _rule(granularity=Granularity.ONE_HOUR)

        assert rule.detect({Granularity.ONE_DAY: _on_days(_breakout_candles())}) is None

    def test_an_hourly_exit_reads_the_one_hour_series(self) -> None:
        rule = _rule(granularity=Granularity.ONE_HOUR)
        held = rule.detect({Granularity.ONE_HOUR: _breakout_candles()})
        assert held is not None

        fires = rule.exit_signal(held, {Granularity.ONE_HOUR: _falling_candles()})

        assert fires is True

    def test_an_hourly_rule_round_trips_through_agent_build_rule(self) -> None:
        """A stored hourly row must rebuild as an hourly rule -- the whole point of persisting
        the param, since `keel-paperhourly`'s rows are read back by every agent cycle."""
        rule = _rule(granularity=Granularity.ONE_HOUR)
        params = dict(rule.describe()["params"])
        # Simulate the DB round trip: Decimal params get JSON-serialized to strings, and
        # `product_id` is re-applied by the writer (turtle's describe() does not carry it).
        params["atr_stop_mult"] = str(params["atr_stop_mult"])
        params["target_rr"] = str(params["target_rr"])
        params["product_id"] = "BTC-USD"

        rebuilt = agent._build_rule({"kind": "turtle_breakout", "params": params})

        assert rebuilt.granularity is Granularity.ONE_HOUR
        assert rebuilt.detect({Granularity.ONE_HOUR: _breakout_candles()}) is not None
        assert rebuilt.detect({Granularity.ONE_DAY: _on_days(_breakout_candles())}) is None

    def test_a_row_written_before_the_param_existed_defaults_to_daily(self) -> None:
        """Default-compatibility: turtle rows already sit in `keel.db`/`keel-live.db` with no
        `granularity` key, and they must keep meaning exactly what they meant -- daily. This is
        the same asymmetry `_params_delta` (keel/commands/rules.py) already documents for any
        kind that grows a param: an old row simply has no such key.
        """
        params = {
            "product_id": "BTC-USD",
            "entry_lookback": 5,
            "exit_lookback": 3,
            "adx_period": 5,
            "atr_period": 5,
            "adx_threshold": 25.0,
            "atr_stop_mult": "2",
            "target_rr": "6",
        }

        rebuilt = agent._build_rule({"kind": "turtle_breakout", "params": params})

        assert rebuilt.granularity is Granularity.ONE_DAY
        assert rebuilt.detect({Granularity.ONE_DAY: _on_days(_breakout_candles())}) is not None


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


class TestRejectionDiagnostics:
    """`detect()` records WHY it declined on `last_rejection`, so `engine.evaluate` can put the
    near-miss numbers into its `engine.no_signal` event.

    Purely diagnostic: no gate, guard or sizing path reads it. A cycle logging `signals=0`
    otherwise says nothing about whether price was 1% or 40% away from the channel, which is the
    difference between "nearly fired" and "nowhere near".
    """

    def test_a_close_below_the_channel_records_how_far_below(self) -> None:
        rule = _rule()
        candles = _no_breakout_candles()

        assert rule.detect({Granularity.ONE_DAY: candles}) is None

        rejection = rule.last_rejection
        assert rejection["gate"] == "donchian_high"
        assert rejection["close"] == float(candles[-1].close)
        assert rejection["entry_level"] > rejection["close"]
        assert rejection["gap_pct"] > 0

    def test_a_breakout_rejected_by_adx_records_the_reading_and_the_threshold(self) -> None:
        """The ETH-USD case: price DID clear the channel and only the trend filter declined it."""
        rule = _rule()

        assert rule.detect({Granularity.ONE_DAY: _choppy_breakout_candles()}) is None

        rejection = rule.last_rejection
        assert rejection["gate"] == "adx"
        assert rejection["adx"] < rejection["adx_threshold"]
        # the breakout numbers are carried too -- that it broke out at all is the news
        assert rejection["close"] > rejection["entry_level"]

    def test_too_little_history_records_what_it_needed(self) -> None:
        rule = _rule()

        assert rule.detect({Granularity.ONE_DAY: _trending_base(3)}) is None

        rejection = rule.last_rejection
        assert rejection["gate"] == "insufficient_history"
        assert rejection["bars"] == 3
        assert rejection["bars_needed"] > 3

    def test_a_fired_setup_clears_the_previous_rejection(self) -> None:
        """Stale diagnostics are worse than none: a rule that fired must not still be carrying
        the reason it declined last time."""
        rule = _rule()
        rule.detect({Granularity.ONE_DAY: _no_breakout_candles()})
        assert rule.last_rejection is not None

        assert rule.detect({Granularity.ONE_DAY: _breakout_candles()}) is not None

        assert rule.last_rejection is None


class TestCompletedDailyBarIsUsedInTheLiveAgentPath:
    """The LIVE agent (`agent.run_once`) hands the rule an `ONE_HOUR` key too, but its daily
    series has NO forming bar: `data.market_feed` only ever persists CLOSED candles. Dropping
    the newest daily bar there is not a lookahead guard, it is a full day of lag on every
    breakout -- the rule decides on a bar that closed up to 48h ago.

    What separates the two callers is not the presence of `ONE_HOUR` but where the hourly
    series SITS: the account sim's hourly window is always inside the last daily bar's own
    period (`portfolio_sim` slices daily with `bisect_right(daily_ts, t)`), while the live
    agent's has advanced into a later day.
    """

    def test_breakout_on_the_newest_closed_daily_bar_fires(self) -> None:
        rule = _rule()
        # Penultimate completed day pulls back -- no breakout there -- so only the NEWEST daily
        # bar can produce a setup. If it is dropped, detect() returns None.
        base = _on_days(_trending_base(19))
        pullback_price = float(base[-3].close)
        base.append(
            _day_candle(
                base[-1].ts + _DAY,
                pullback_price - 0.5, pullback_price + 0.5, pullback_price - 0.5, pullback_price,
            )
        )
        breakout_price = float(base[-2].close) + 20.0
        breakout_ts = base[-1].ts + _DAY
        base.append(
            _day_candle(
                breakout_ts,
                breakout_price - 0.5, breakout_price + 0.5, breakout_price - 0.5, breakout_price,
            )
        )
        # The live agent's newest CLOSED hourly bar sits in the day AFTER the newest daily bar,
        # which is only possible once that daily bar has itself closed.
        hourly = [_day_candle(breakout_ts + _DAY, 1, 1, 1, 1)]

        setup = rule.detect({Granularity.ONE_HOUR: hourly, Granularity.ONE_DAY: base})

        assert setup is not None
        assert setup.ts == breakout_ts

    def test_account_sim_forming_bar_is_still_dropped(self) -> None:
        """The guard must keep working for `portfolio_sim`, whose hourly bar sits INSIDE the
        last daily bar's period -- the one shape where that bar really is still forming.
        """
        rule = _rule()
        base = _on_days(_trending_base(19))
        pullback_price = float(base[-3].close)
        base.append(
            _day_candle(
                base[-1].ts + _DAY,
                pullback_price - 0.5, pullback_price + 0.5, pullback_price - 0.5, pullback_price,
            )
        )
        forming_ts = base[-1].ts + _DAY
        breakout_price = float(base[-2].close) + 20.0
        base.append(
            _day_candle(
                forming_ts,
                breakout_price - 0.5, breakout_price + 0.5, breakout_price - 0.5, breakout_price,
            )
        )
        # Mid-day hourly bar, inside the last daily bar's own period = still forming.
        hourly = [_day_candle(forming_ts + 12 * _HOUR, 1, 1, 1, 1)]

        assert rule.detect({Granularity.ONE_HOUR: hourly, Granularity.ONE_DAY: base}) is None

    def test_exit_fires_on_the_newest_closed_daily_bar(self) -> None:
        rule = _rule()
        # A rising base, then a single sharp drop below the prior 3-day channel low ON THE
        # NEWEST bar. No earlier bar breaks its own channel, so dropping the newest = no exit.
        base = _on_days(_trending_base(20))
        break_price = float(min(c.low for c in base[-3:])) - 5.0
        break_ts = base[-1].ts + _DAY
        base.append(
            _day_candle(
                break_ts, break_price + 0.5, break_price + 0.5, break_price - 0.5, break_price
            )
        )
        hourly = [_day_candle(break_ts + _DAY, 1, 1, 1, 1)]

        assert rule.exit_signal(None, {Granularity.ONE_HOUR: hourly, Granularity.ONE_DAY: base})


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


# ---------------------------------------------------------------------------
# S1 profitable-trade filter
# ---------------------------------------------------------------------------


def _impulse_pullback_cycle(price: float, ts: int, *, impulse: int = 8, pullback: int = 5,
                            up: float = 3.0, down: float = 4.0, crash: bool = False):
    """One impulse-up (breakout + ADX) then pullback-down (channel exit) cycle, à la
    `_daily_uptrend_with_pullbacks`. `crash=True` makes the first pullback bar gap its LOW far
    below (≈ -40) so the 2N stop is hit -> the cycle's trade is a LOSS rather than a channel-exit
    win. Returns (candles, next_price, next_ts).
    """
    candles: list[Candle] = []
    for _ in range(impulse):
        price += up
        candles.append(_candle(ts, price - up, price + 1, price - 4, price))
        ts += 1
    for k in range(pullback):
        price -= down
        low = price - (40.0 if crash and k == 0 else 1.0)
        candles.append(_candle(ts, price + down, price + down, low, price))
        ts += 1
    return candles, price, ts


# Empirically-tuned cycle shapes (verified via backtest): a long impulse + shallow pullback
# exits well ABOVE entry (a WIN); a short impulse whose first pullback bar gaps its low through
# the 2N stop exits BELOW entry (a LOSS).
_WIN_CYCLE = dict(impulse=18, up=4.0, pullback=5, down=3.0)
_LOSS_CYCLE = dict(impulse=8, up=4.0, pullback=5, down=4.0, crash=True)
_DUMMY_CURRENT = _candle(9999, 300, 301, 299, 300)  # a trailing "current" bar (excluded by [:-1])


def _chain(cycle_kwargs: list[dict]) -> list[Candle]:
    candles: list[Candle] = []
    price, ts = 100.0, 0
    for kw in cycle_kwargs:
        chunk, price, ts = _impulse_pullback_cycle(price, ts, **kw)
        candles.extend(chunk)
    return candles


class TestS1Filter:
    def test_default_off_still_detects_breakout(self) -> None:
        # filter defaults off -> a clean breakout still yields a Setup (behavior unchanged)
        assert _rule().detect({Granularity.ONE_DAY: _breakout_candles()}) is not None
        assert _rule(s1_filter=False).detect(
            {Granularity.ONE_DAY: _breakout_candles()}
        ) is not None

    def test_param_round_trips_through_agent(self) -> None:
        described = _rule(s1_filter=True).describe()
        assert described["params"]["s1_filter"] is True
        # mirror `rules seed`: it stores product_id alongside describe()'s params
        params = {**described["params"], "product_id": "BTC-USD"}
        rebuilt = agent._build_rule({"kind": "turtle_breakout", "params": params})
        assert rebuilt.params["s1_filter"] is True

    def test_no_completed_prior_trade_does_not_skip(self) -> None:
        # a single clean breakout series: the rising base enters and never exits (no COMPLETED
        # prior trade) -> filter must not skip (returns False).
        assert _rule(s1_filter=True)._prior_breakout_won(_breakout_candles()) is False

    def test_prior_breakout_won_true_after_winning_cycle(self) -> None:
        series = _chain([_WIN_CYCLE]) + [_DUMMY_CURRENT]
        assert _rule(s1_filter=True)._prior_breakout_won(series) is True

    def test_prior_breakout_won_false_after_losing_cycle(self) -> None:
        series = _chain([_LOSS_CYCLE]) + [_DUMMY_CURRENT]
        assert _rule(s1_filter=True)._prior_breakout_won(series) is False

    def test_filter_removes_entries_after_wins(self) -> None:
        # a run of WINNING cycles: the filter should skip some post-win breakouts -> strictly
        # fewer trades than the unfiltered rule (and it confirms the unfiltered ones won).
        series = _chain([_WIN_CYCLE] * 5)
        off = backtest(_rule(s1_filter=False), series)
        on = backtest(_rule(s1_filter=True), series)
        assert off.n_trades >= 2
        assert on.n_trades < off.n_trades


def _with_breakout_volume(candles: list[Candle], breakout_vol: float,
                          base_vol: float = 100.0) -> list[Candle]:
    """Rebuild `candles` with a flat base volume and a chosen volume on the final (breakout) bar
    (Candle is frozen, so construct new ones)."""
    out: list[Candle] = []
    for i, c in enumerate(candles):
        v = breakout_vol if i == len(candles) - 1 else base_vol
        out.append(Candle(ts=c.ts, open=c.open, high=c.high, low=c.low, close=c.close,
                          volume=Decimal(str(v))))
    return out


class TestVolumeFilter:
    def test_default_off_ignores_volume(self) -> None:
        # filter off: even a thin-volume breakout is still taken (behavior unchanged)
        thin = _with_breakout_volume(_breakout_candles(), breakout_vol=1.0)
        assert _rule().detect({Granularity.ONE_DAY: thin}) is not None

    def test_param_round_trips_through_agent(self) -> None:
        described = _rule(min_volume_filter=True, volume_ma_period=30, volume_mult=1.5).describe()
        assert described["params"]["min_volume_filter"] is True
        assert described["params"]["volume_ma_period"] == 30
        assert described["params"]["volume_mult"] == 1.5
        params = {**described["params"], "product_id": "BTC-USD"}
        rebuilt = agent._build_rule({"kind": "turtle_breakout", "params": params})
        assert rebuilt.params["min_volume_filter"] is True

    def test_high_volume_breakout_passes(self) -> None:
        # breakout volume 150 > 1.2 x 100 avg -> filter passes, Setup emitted
        series = _with_breakout_volume(_breakout_candles(), breakout_vol=150.0)
        assert _rule(min_volume_filter=True).detect({Granularity.ONE_DAY: series}) is not None

    def test_low_volume_breakout_skipped(self) -> None:
        # breakout volume 110 < 1.2 x 100 = 120 -> filter skips it (None), but only with filter on
        series = _with_breakout_volume(_breakout_candles(), breakout_vol=110.0)
        assert _rule(min_volume_filter=True).detect({Granularity.ONE_DAY: series}) is None
        assert _rule(min_volume_filter=False).detect({Granularity.ONE_DAY: series}) is not None
