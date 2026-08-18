"""Tests for keel.strategy.rules.pullback_continuation.PullbackContinuation.

Fixtures below are hand-tuned candle series verified to satisfy (or deliberately fail) the
rule's gates: bullish condition + pullback phase (analysis.regime), EMA fan alignment
(analysis.indicators), price in the configured entry zone, and a qualifying signal candle
(analysis.candles). Small `ema_periods=(3, 5, 8)` are used so a short, hand-verifiable
series is enough to move the EMAs into alignment (the rule itself defaults to (8, 20, 50)
per source-02 §2.1; the periods are just a tunable parameter).

The `#352` block at the bottom pins the running-state optimisation: bar-by-bar equivalence
against the pure functions it mirrors, rebuild-vs-extend identity, a golden backtest
captured from the pre-#352 full-recompute implementation, and a wall-clock smoke bound.
"""

from __future__ import annotations

import inspect
import json
import random
import re
import time
from decimal import Decimal
from pathlib import Path
from typing import get_args

from keel.analysis import indicators, levels, regime
from keel.strategy.backtest import backtest
from keel.strategy.rules.pullback_continuation import (
    _DEFAULT_SIGNAL_PATTERNS,
    PullbackContinuation,
    SignalPattern,
    _RunningState,
    _tail_aligned,
)
from keel.types import Candle, Granularity
from tests.baseline.serialize import serialize_result

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
        from keel.analysis import levels

        rule = _rule(target_method="swing")
        candles = _bullish_pullback_candles()
        expected_target = candles[levels.swing_highs(candles)[-1]].high

        setup = rule.detect({Granularity.ONE_HOUR: candles})

        assert setup is not None
        assert setup.target == expected_target
        assert setup.target > setup.entry
        assert setup.rr == (setup.target - setup.entry) / (setup.entry - setup.stop)

    def test_fib_ext_target_is_1272_extension_of_last_swing_move(self) -> None:
        from keel.analysis import levels

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


class TestSignalPatternDeclaration:
    def test_signal_pattern_declares_exactly_the_names_the_matcher_can_fire_on(self) -> None:
        """`SignalPattern` is the rule's published statement of which pattern names mean
        anything to it -- `keel rules add` reads it (via `typing.get_type_hints`) to refuse a
        typo'd pattern that would otherwise never match and yield 0 trades forever. A name in
        `_match_signal_pattern` that is missing from the Literal would be refused at the CLI
        despite working, and a name in the Literal that the matcher does not handle would be
        accepted and silently ignored. Pinned mechanically so neither can drift.
        """
        handled = set(
            re.findall(
                r'pattern == "([a-z_]+)"',
                inspect.getsource(PullbackContinuation._match_signal_pattern),
            )
        )

        assert handled == set(get_args(SignalPattern))
        assert set(_DEFAULT_SIGNAL_PATTERNS) <= handled


# ---------------------------------------------------------------------------
# #352 -- the running-state optimisation's exactness contract
# ---------------------------------------------------------------------------
#
# `PullbackContinuation` computes every full-series quantity (EMA fan, Wilder ATR, phase
# pivots, target swings) incrementally in `_RunningState` instead of recomputing it per bar
# (#352). That is only a valid optimisation if the running values are BIT-IDENTICAL to the
# pure functions applied to the same prefix -- "close enough" would silently re-parameterize
# the rule at the ulp level and change which setups fire. The tests in this block pin that
# contract from four angles: value-by-value equivalence against the pure recompute on every
# prefix of two deterministic series; rebuild-vs-extend identity (a gappy or cold-started
# walk must land on exactly the state the one-bar walk built); full-backtest goldens captured
# from the PRE-#352 implementation at f7a0cdf (the strongest end-to-end pin: any deviation
# anywhere in detect/exit arithmetic moves a trade price and fails the file compare); and a
# wall-clock smoke bound for the 1-year hourly window that opened the issue.


def _synthetic_hourly(seed: int, bars: int, start_ts: int = 1_700_000_000) -> list[Candle]:
    """Deterministic seeded hourly OHLC random walk -- the #352 fixture generator.

    Prices are quantized to 2dp and drift flips every ~43 bars, so over a few hundred bars
    every gate in `detect()` spends time both passing and failing: trends form (condition
    BULLISH/BEARISH), pullbacks follow (phase flips between RUN and PULLBACK), the EMA fan
    stacks and unstacks, and both pivot radii confirm many candidates. No database, no
    network -- the same seed always rebuilds the same candles, which is what the golden file
    below depends on.
    """
    rng = random.Random(seed)
    price = 100.0
    out: list[Candle] = []
    for i in range(bars):
        drift = 0.35 if (i // 43) % 2 == 0 else -0.30
        step = drift + rng.gauss(0.0, 1.1)
        o = price
        c = round(o + step, 2)
        hi = round(max(o, c) + abs(rng.gauss(0.35, 0.25)), 2)
        lo = round(min(o, c) - abs(rng.gauss(0.35, 0.25)), 2)
        out.append(
            Candle(
                ts=start_ts + i * 3600,
                open=Decimal(str(o)),
                high=Decimal(str(hi)),
                low=Decimal(str(lo)),
                close=Decimal(str(c)),
                volume=Decimal("1"),
            )
        )
        price = c
    return out


def _tie_heavy_hourly(seed: int, bars: int, start_ts: int = 1_750_000_000) -> list[Candle]:
    """Whole-number prices with frequently-zero wicks: neighbouring highs/lows TIE often, so
    the pivot predicates' strictness (`>` / `<`, never `>=` / `<=`) is exercised on both
    sides -- a running-state pivot that accepted a tie where the pure scan rejects it (or
    vice versa) fails here even though every float in the series still matches.
    """
    rng = random.Random(seed)
    price = 100.0
    out: list[Candle] = []
    for i in range(bars):
        drift = 0.4 if (i // 17) % 2 == 0 else -0.35
        o = price
        c = float(round(o + drift + rng.gauss(0.0, 0.9)))
        wick = float(round(abs(rng.gauss(0.2, 0.15))))
        out.append(
            Candle(
                ts=start_ts + i * 3600,
                open=Decimal(str(o)),
                high=Decimal(str(max(o, c) + wick)),
                low=Decimal(str(min(o, c) - wick)),
                close=Decimal(str(c)),
                volume=Decimal("1"),
            )
        )
        price = c
    return out


def _pure_phase_pivots(prefix: list[Candle]) -> tuple[Decimal | None, Decimal | None]:
    """The exact values `regime.detect_phase` reads: last radius-1 swing high/low, as Decimals."""
    highs = regime._swing_highs(prefix)
    lows = regime._swing_lows(prefix)
    return (
        prefix[highs[-1]].high if highs else None,
        prefix[lows[-1]].low if lows else None,
    )


def _pure_target_pivots(prefix: list[Candle]) -> tuple[Decimal | None, Decimal | None]:
    """The exact values `_compute_target`'s swing/fib branches read: last radius-2 pivots."""
    highs = levels.swing_highs(prefix)
    lows = levels.swing_lows(prefix)
    return (
        prefix[highs[-1]].high if highs else None,
        prefix[lows[-1]].low if lows else None,
    )


def _state_snapshot(state: _RunningState) -> dict[str, object]:
    """Every value the running state carries, as a comparable dict (floats/Decimals compared
    exactly -- `==` on the dict does element-wise `==`, which for floats is ulp-exact)."""
    return {
        "length": state.length,
        "first_ts": state.first_ts,
        "last_ts": state.last_ts,
        "first_close": state.first_close,
        "ema_tail": dict(state.ema_tail),
        "atr_last": state.atr_last,
        "atr_trs": list(state.atr_trs),
        "phase_high": state.phase_high,
        "phase_low": state.phase_low,
        "target_high": state.target_high,
        "target_low": state.target_low,
    }


#: The three deterministic windows the #352 golden was captured on, with the rule params each
#: ran under. Together they exercise every state-read path end-to-end: defaults (fixed stop,
#: measured-1:1 target, ema_touch zone), the ATR stop + swing target (running `atr_last` and
#: `target_high` flow into trade prices), and the ema_band zone + fib_ext target (mid/slow
#: EMA tails and both target pivots).
_352_GOLDEN_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "pullback_352_golden.json"
_352_WINDOWS: tuple[dict[str, object], ...] = (
    {"seed": 352, "bars": 4000, "params": {}},
    {"seed": 352, "bars": 4000, "params": {"stop_method": "atr", "target_method": "swing"}},
    {
        "seed": 5,
        "bars": 6000,
        "params": {"stop_method": "atr", "target_method": "fib_ext", "entry_zone": "ema_band"},
    },
)


class TestRunningStateMatchesPureFunctions:
    """Every prefix of a deterministic series: the running values must equal the pure
    recompute EXACTLY (float `==`, not approx) -- this is the bit-identical contract."""

    def test_every_bar_of_two_series_matches_the_pure_recompute(self) -> None:
        for candles, periods in (
            (_synthetic_hourly(99, 400), (8, 20, 50)),
            (_tie_heavy_hourly(21, 400), (3, 5, 8)),
        ):
            rule = PullbackContinuation(product_id="BTC-USD", ema_periods=periods)
            for n in range(2, len(candles) + 1):
                prefix = candles[:n]
                state = rule._sync(prefix)

                fan = indicators.ema_fan(prefix, periods=periods)
                for period in periods:
                    assert state.ema_tail[period] == fan[period][-1], (period, n)

                assert state.atr_last == indicators.atr(prefix, period=14)[-1], n

                phase_high, phase_low = _pure_phase_pivots(prefix)
                assert state.phase_high == phase_high, n
                assert state.phase_low == phase_low, n

                target_high, target_low = _pure_target_pivots(prefix)
                assert state.target_high == target_high, n
                assert state.target_low == target_low, n

                # The state-evaluated gates equal the pure-function gates at the last index.
                assert rule._phase(state, prefix) == regime.detect_phase(prefix), n
                for direction in ("bullish", "bearish"):
                    assert _tail_aligned(state.ema_tail, direction) == indicators.fan_aligned(
                        fan, n - 1, direction
                    ), (direction, n)


class TestRunningStateRebuildVsExtend:
    """The same final prefix reached by one-bar steps, by a gappy walk, and by a cold start
    must land on byte-for-byte the same state -- and that state must equal a full rebuild."""

    def test_stepwise_gappy_and_cold_start_states_are_identical(self) -> None:
        candles = _synthetic_hourly(7, 300)
        periods = (8, 20, 50)

        stepwise = PullbackContinuation(product_id="BTC-USD", ema_periods=periods)
        for n in range(2, len(candles) + 1):
            stepwise._sync(candles[:n])

        gappy = PullbackContinuation(product_id="BTC-USD", ema_periods=periods)
        for n in (2, 3, 9, 10, 17, 40, 41, 42, 43, 120, 199, 200, 201, 298, 299, 300):
            gappy._sync(candles[:n])

        cold = PullbackContinuation(product_id="BTC-USD", ema_periods=periods)
        cold._sync(candles[:200])
        for n in range(201, len(candles) + 1):
            cold._sync(candles[:n])

        rebuilt = _RunningState.build(candles, periods)
        snapshots = [
            _state_snapshot(stepwise._state),
            _state_snapshot(gappy._state),
            _state_snapshot(cold._state),
            _state_snapshot(rebuilt),
        ]
        assert all(snapshot == snapshots[0] for snapshot in snapshots)

    def test_sync_rebuilds_when_the_window_does_not_extend_the_cache(self) -> None:
        candles = _synthetic_hourly(11, 120)
        rule = _rule()
        periods = rule.params["ema_periods"]

        state = rule._sync(candles[:100])
        assert state.first_ts == candles[0].ts

        # A window that slid forward (new first bar) is a different prefix: rebuild.
        slid = rule._sync(candles[1:101])
        assert slid is not state
        assert _state_snapshot(slid) == _state_snapshot(
            _RunningState.build(candles[1:101], periods)
        )

        # A shorter window cannot extend the cached prefix: rebuild.
        shrunk = rule._sync(candles[:40])
        assert _state_snapshot(shrunk) == _state_snapshot(
            _RunningState.build(candles[:40], periods)
        )

        # An unrelated series sharing no endpoints: rebuild.
        other = _tie_heavy_hourly(12, 60)
        unrelated = rule._sync(other)
        assert _state_snapshot(unrelated) == _state_snapshot(_RunningState.build(other, periods))


class TestBacktestMatchesPre352Golden:
    """The end-to-end pin: full trades + every metric on three deterministic windows must
    reproduce, exactly, the results captured from the PRE-#352 full-recompute implementation
    (commit f7a0cdf, run on these same fixtures via a stashed checkout and recorded to the
    committed golden). `serialize_result` renders Decimals as strings, so a one-ulp drift
    anywhere in detect/exit arithmetic changes a trade price and fails the compare.

    This golden is a CAPTURE, not a regenerable baseline: the implementation it was recorded
    from no longer exists on this branch. If rule semantics are ever deliberately changed,
    re-derive these expectations consciously (re-run the old implementation from git history
    on these windows) rather than overwriting the file with the new output.
    """

    def test_backtest_reproduces_the_pre_352_results_exactly(self) -> None:
        golden = json.loads(_352_GOLDEN_PATH.read_text())
        for window in golden["windows"]:
            rule = PullbackContinuation(
                product_id="BTC-USD", **window["params"]  # type: ignore[arg-type]
            )
            result = backtest(rule, _synthetic_hourly(window["seed"], window["bars"]))
            assert serialize_result(result) == window["result"], (window["seed"], window["bars"])

    def test_the_golden_windows_actually_traded(self) -> None:
        """A zero-trade golden would pass forever while proving nothing."""
        golden = json.loads(_352_GOLDEN_PATH.read_text())
        trades = [t for window in golden["windows"] for t in window["result"]["trades"]]
        outcomes = {t["outcome"] for t in trades}
        assert len(trades) >= 10
        assert "win" in outcomes and "loss" in outcomes


class TestHourlyBacktestSpeed:
    """The #352 smoke bound: the 1-year 8,784-bar hourly window that took 8.2s-8.9s before
    (and 233.5s at 5 years -- quadratic) must finish in seconds. The bound is deliberately
    generous (60s) so CI variance can never flake it; it exists to catch the quadratic
    coming back, not to benchmark."""

    def test_one_year_hourly_backtest_completes_under_60s(self) -> None:
        candles = _synthetic_hourly(352, 8784)  # 366 days x 24h, the issue's 1y window
        start = time.monotonic()
        result = backtest(PullbackContinuation(product_id="BTC-USD"), candles)
        elapsed = time.monotonic() - start
        assert result.n_trades >= 0  # completed the full walk, not an error path
        assert elapsed < 60.0, f"1y hourly backtest took {elapsed:.1f}s (quadratic regressions?)"


# ---------------------------------------------------------------------------
# #363 -- review findings on the running state: anchor validation (finding 1),
# lazy acquisition (finding 2), same-window reuse (finding 3)
# ---------------------------------------------------------------------------
#
# An adversarial review of the #352 optimisation verified the arithmetic equivalence
# (old == new, bit-identical) but found that `extends()` validated TIMESTAMPS only, that
# `_sync()` ran the full O(n) build BEFORE the bounded `regime.detect_condition` gate
# (an 8.5x per-call regression on portfolio_sim's sliding 8,760-bar window), and that the
# same window fed to two calls in one cycle rebuilt twice. These tests pin the fixes.


def _counting_build_spy(monkeypatch) -> dict[str, int]:  # type: ignore[no-untyped-def]
    """Patch `_RunningState.build` with a counting wrapper; returns the counter dict.
    `extend`/`matches` are NOT wrapped -- the point is to count full O(n) rebuilds."""
    counts: dict[str, int] = {"builds": 0}
    real_build = _RunningState.build.__func__

    def counting_build(cls, candles, ema_periods):  # type: ignore[no-untyped-def]
        counts["builds"] += 1
        return real_build(cls, candles, ema_periods)

    monkeypatch.setattr(_RunningState, "build", classmethod(counting_build))
    return counts


class TestAnchorValidation:
    """#363 finding 1: ts-only anchors let a rewritten or colliding series validate.
    The cached boundary bar's CLOSE is a fourth anchor -- a same-ts bar whose close
    changed must fail `extends`/`matches` so the state REBUILDS instead of serving the
    stale running values."""

    def test_boundary_bar_close_rewritten_in_place_rebuilds_not_extends(self) -> None:
        candles = _synthetic_hourly(3, 80)
        periods = (8, 20, 50)
        rule = PullbackContinuation(product_id="BTC-USD", ema_periods=periods)
        state = rule._sync(candles[:60])

        # Rewrite the cached boundary bar (index length-1) in place: same ts, new close --
        # the "repair upserts corrected values" shape. ts-only anchors still validate it.
        rewritten = list(candles[:60])
        boundary = rewritten[-1]
        rewritten[-1] = Candle(
            ts=boundary.ts,
            open=boundary.open,
            high=boundary.high,
            low=boundary.low,
            close=boundary.close + Decimal("7"),
            volume=boundary.volume,
        )
        rewritten.extend(candles[60:70])
        assert rewritten[0].ts == state.first_ts
        assert rewritten[state.length - 1].ts == state.last_ts
        assert rewritten[state.length - 1].close != state.last_close

        assert state.extends(rewritten) is False

        after = rule._sync(rewritten)
        assert _state_snapshot(after) == _state_snapshot(_RunningState.build(rewritten, periods))
        assert after.ema_tail == {
            p: indicators.ema_fan(rewritten, periods=periods)[p][-1] for p in periods
        }

    def test_same_length_in_place_rewrite_rebuilds(self) -> None:
        candles = _synthetic_hourly(3, 80)
        rule = _rule()
        periods = rule.params["ema_periods"]
        rule._sync(candles[:60])

        rewritten = list(candles[:60])
        boundary = rewritten[-1]
        rewritten[-1] = Candle(
            ts=boundary.ts,
            open=boundary.open,
            high=boundary.high,
            low=boundary.low,
            close=boundary.close - Decimal("4"),
            volume=boundary.volume,
        )

        after = rule._sync(rewritten)
        assert _state_snapshot(after) == _state_snapshot(_RunningState.build(rewritten, periods))

    def test_other_series_sharing_the_ts_grid_rebuilds_not_stale_serves(self) -> None:
        # `_synthetic_hourly` always uses the same hourly ts grid, so seeds 3 and 4 share
        # first_ts AND the ts at every index -- the "different product, same hourly grid"
        # collision. ts-only anchors would let seed 4 validate against seed 3's state and
        # serve seed 3's numbers; the close anchor must reject it.
        a = _synthetic_hourly(3, 80)
        b = _synthetic_hourly(4, 80)
        periods = (8, 20, 50)
        rule = PullbackContinuation(product_id="BTC-USD", ema_periods=periods)
        state = rule._sync(a[:60])

        assert b[0].ts == state.first_ts
        assert b[state.length - 1].ts == state.last_ts
        assert b[state.length - 1].close != state.last_close
        assert state.extends(b[:70]) is False

        after = rule._sync(b[:70])
        assert _state_snapshot(after) == _state_snapshot(_RunningState.build(b[:70], periods))


class TestSameWindowReuse:
    """#363 finding 3: the agent and portfolio_sim call `exit_signal` then `detect` on the
    SAME window within one cycle (and tests/cycles repeat either call on one window). A
    same-length, all-anchors-match window must reuse the resolved state -- no second
    O(n) build."""

    def test_exit_signal_then_detect_on_the_same_window_build_once_per_window(
        self, monkeypatch
    ) -> None:
        counts = _counting_build_spy(monkeypatch)
        rule = _rule()
        held = rule.detect({Granularity.ONE_HOUR: _bullish_pullback_candles()})
        assert held is not None  # BULLISH window: detect consumed state -> one build
        assert counts["builds"] == 1

        fires = rule.exit_signal(held, {Granularity.ONE_HOUR: _bearish_mirror_candles()})
        assert fires is True  # BEARISH window: exit_signal consumed state -> second build
        assert counts["builds"] == 2

        # The same-window repeats of one cycle: no further builds, identical results.
        assert rule.exit_signal(held, {Granularity.ONE_HOUR: _bearish_mirror_candles()}) is True
        assert rule.detect({Granularity.ONE_HOUR: _bearish_mirror_candles()}) is None
        assert counts["builds"] == 2

    def test_detect_twice_on_the_same_window_builds_once_and_repeats_the_setup(
        self, monkeypatch
    ) -> None:
        counts = _counting_build_spy(monkeypatch)
        rule = _rule()
        by_tf = {Granularity.ONE_HOUR: _bullish_pullback_candles()}

        first = rule.detect(by_tf)
        assert first is not None
        assert counts["builds"] == 1

        second = rule.detect(by_tf)
        assert counts["builds"] == 1  # same-length anchor match: no rebuild, no extend
        assert second == first


class TestLazyStateAcquisition:
    """#363 finding 2: state must be acquired LAZILY -- a bar the bounded
    `regime.detect_condition` gate declines must not pay the O(n) build. Bars that
    decline early leave the state behind; the next state-consuming call must EXTEND
    across the whole gap and still match the pure recompute exactly."""

    def test_condition_declined_bars_never_build_state(self, monkeypatch) -> None:
        counts = _counting_build_spy(monkeypatch)
        rule = _rule()
        candles = _choppy_candles()

        for n in range(2, len(candles) + 1):
            assert regime.detect_condition(candles[:n]) != regime.Condition.BULLISH
            assert rule.detect({Granularity.ONE_HOUR: candles[:n]}) is None

        assert counts["builds"] == 0
        assert rule._state is None

    def test_exit_signal_declined_by_condition_never_builds_state(self, monkeypatch) -> None:
        counts = _counting_build_spy(monkeypatch)
        minter = _rule()
        held = minter.detect({Granularity.ONE_HOUR: _bullish_pullback_candles()})
        assert held is not None
        assert counts["builds"] == 1

        rule = _rule()  # a fresh instance: its state must stay untouched
        assert rule.exit_signal(held, {Granularity.ONE_HOUR: _bullish_pullback_candles()}) is False
        assert counts["builds"] == 1
        assert rule._state is None

    def test_state_consuming_call_after_declined_bars_extends_across_the_gap(
        self, monkeypatch
    ) -> None:
        candles = _synthetic_hourly(99, 400)
        conditions = [
            regime.detect_condition(candles[:n]) for n in range(2, len(candles) + 1)
        ]
        bullish_ns = [
            n for n, cond in enumerate(conditions, start=2)
            if cond == regime.Condition.BULLISH
        ]
        # A passing bar, then >= 2 declining bars (the gap), then the next passing bar.
        pair = next(
            (n0, n1)
            for i, n0 in enumerate(bullish_ns)
            for n1 in bullish_ns[i + 1 :]
            if n1 >= n0 + 3
            and all(conditions[n - 2] != regime.Condition.BULLISH for n in range(n0 + 1, n1))
        )
        n0, n1 = pair

        counts = _counting_build_spy(monkeypatch)
        rule = _rule()
        periods = rule.params["ema_periods"]

        rule.detect({Granularity.ONE_HOUR: candles[:n0]})  # BULLISH: consumes state -> builds
        assert counts["builds"] == 1
        assert rule._state is not None
        assert rule._state.length == n0

        for n in range(n0 + 1, n1):  # the gap: every bar declined before touching state
            rule.detect({Granularity.ONE_HOUR: candles[:n]})
        assert counts["builds"] == 1
        assert rule._state.length == n0  # left behind by the declined bars

        rule.detect({Granularity.ONE_HOUR: candles[:n1]})  # BULLISH again: extends the gap
        assert counts["builds"] == 1  # extend, NOT a rebuild
        assert rule._state.length == n1

        # The gap-extended state equals the pure recompute / a full rebuild exactly.
        assert _state_snapshot(rule._state) == _state_snapshot(
            _RunningState.build(candles[:n1], periods)
        )
