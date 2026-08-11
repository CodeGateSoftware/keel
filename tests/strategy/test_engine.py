"""Tests for keel.strategy.engine: the per-bar evaluation engine (Task 7).

Drives `evaluate()` with a mix of real, merged rules (`PullbackContinuation`, `Dca`) for
realistic CTS-context assembly, and a small test-only scripted `Rule` (mirroring the
pattern already used in `tests/strategy/test_backtest.py`) for deterministic kill-zone /
choppy-regime gate scenarios that don't depend on any one rule's internal detect() gates.

Fixtures are verified against the *actual* merged `analysis.*` outputs (not guessed): see
the module docstring notes inline for what each fixture is known to produce.
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from decimal import Decimal

from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.strategy import indicators_cts
from keel.strategy.engine import DEFAULT_RR_FLOOR, assemble_cts_context, evaluate
from keel.strategy.rules.base import Action, Rule, Setup
from keel.strategy.rules.dca import Dca
from keel.strategy.rules.pullback_continuation import PullbackContinuation
from keel.strategy.rules.turtle_breakout import TurtleBreakout
from keel.types import Candle, Granularity, Side

_DAY = 86_400


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
    return [_candle(i, v, v + spread, v - spread, v) for i, v in enumerate(values)]


def _bullish_pullback_candles() -> list[Candle]:
    """Verified (via `PullbackContinuation`'s own fixture, `tests/strategy/test_pullback.py`)
    to satisfy `regime.detect_condition == BULLISH` and `regime.detect_phase == PULLBACK`,
    with a closing bullish pin bar. Independently confirmed by direct script run against the
    merged `analysis.*` modules: `condition_aligned` and `candlestick_pattern` ("pin_bar")
    both score present; `ema_fan_aligned`/`rsi_extreme`/`rsi_divergence`/`sr_touches`/
    `fib_confluence`/`deceleration` all score absent at this series' length -- exactly why the
    "high CTS" test below uses a narrowed `weights` dict (real engine-computed presence, just
    concentrating the point budget) rather than trying to engineer all eleven flags at once.
    """
    leg = [130, 124, 118, 110, 116, 122, 117, 126, 120, 132, 128]
    candles = _flat_series(leg)
    candles.append(_candle(len(leg), 127.6, 128.0, 125.0, 127.9))
    return candles


def _choppy_candles() -> list[Candle]:
    """Verified: `regime.detect_condition` -> CHOPPY, `regime.is_tradeable` -> False."""
    return _flat_series([100, 115, 92, 118, 88, 122, 85])


def _uptrend_candles() -> list[Candle]:
    """Verified: `regime.detect_condition` -> BULLISH, `regime.is_tradeable` -> True. Used to
    isolate the kill-zone/rr gate from the choppy-regime gate (this series passes the choppy
    gate cleanly so a rejection can only be attributed to rr).
    """
    return _flat_series([100, 105, 102, 108, 104, 112, 109, 118, 114, 124])


def _dca_daily_candle() -> list[Candle]:
    """A single day-0 candle: `cadence_days=7` boundary (`0 % 7 == 0`), matching
    `tests/strategy/test_dca.py`'s minimal cadence fixture.
    """
    return [
        Candle(
            ts=0,
            open=Decimal("100"),
            high=Decimal("100"),
            low=Decimal("100"),
            close=Decimal("100"),
            volume=Decimal("1"),
        )
    ]


class _FixedSetupRule(Rule):
    """Test-only rule (mirrors `tests/strategy/test_backtest.py::_ScriptedRule`): always
    returns the same pre-built `Setup`, regardless of the candles it's given. Lets the
    kill-zone/choppy-regime gate tests drive `evaluate()`'s gating logic deterministically,
    independent of any concrete rule's own detect() gates.
    """

    def __init__(self, setup: Setup, name: str = "fixed") -> None:
        self.name = name
        self.params: dict = {}
        self._setup = setup

    def detect(self, candles_by_tf: dict[Granularity, list[Candle]]) -> Setup | None:
        return self._setup

    def exit_signal(self, held: Setup, candles_by_tf: dict[Granularity, list[Candle]]) -> bool:
        return False

    def describe(self) -> dict:
        return {"name": self.name, "params": self.params}


class _NoSetupRule(Rule):
    """Test-only rule that never fires -- `evaluate()` must simply skip it."""

    name = "no_setup"
    params: dict = {}

    def detect(self, candles_by_tf: dict[Granularity, list[Candle]]) -> Setup | None:
        return None

    def exit_signal(self, held: Setup, candles_by_tf: dict[Granularity, list[Candle]]) -> bool:
        return False

    def describe(self) -> dict:
        return {"name": self.name, "params": self.params}


def _pullback_rule() -> PullbackContinuation:
    return PullbackContinuation(
        product_id="BTC-USD",
        granularity=Granularity.ONE_HOUR,
        ema_periods=(3, 5, 8),
        buffer_ticks=Decimal("0.02"),
    )


class TestHighCtsAggressiveEntry:
    def test_high_cts_yields_aggressive_enter_signal(self) -> None:
        rule = _pullback_rule()
        candles_by_tf = {Granularity.ONE_HOUR: _bullish_pullback_candles()}
        # condition_aligned is confirmed present for this fixture; concentrating the entire
        # point budget on it isolates the CTS->technique mapping from needing to also
        # engineer every other confluence flag into a single short candle series.
        weights = {"condition_aligned": 10}

        signals = evaluate(rules=[rule], candles_by_tf=candles_by_tf, weights=weights)

        assert len(signals) == 1
        signal = signals[0]
        assert signal.rule_name == "pullback_continuation"
        assert signal.action == Action.ENTER
        assert signal.side == Side.BUY
        assert signal.cts_score == 10
        assert signal.entry_technique == "aggressive"
        assert signal.setup is not None
        assert signal.setup.direction == "long"

    def test_default_weights_on_same_fixture_yields_confirm_3bar_tier(self) -> None:
        # Sanity check against the *default* CTS weights (spec §9): this fixture earns
        # condition_aligned(2) + in_pullback(1) + candlestick_pattern(1) = 4, which lands in
        # the low ("confirm_3bar") tier -- confirming the engine doesn't silently inflate the
        # real score.
        #
        # ⚠️ This expectation was 5 / "signal_candle" before #225, and the missing point is
        # `round_number_proximity`. The fixture enters at 128.02, which is 2.02 away from the
        # nearest round handle (130, on a 10-wide grid at that magnitude) -- it is not near a
        # magnet level and never was. It scored present only because the old
        # `levels.is_round_number` compared against an ABSOLUTE `step=Decimal("0.005")` and
        # 128.02 is an exact multiple of half a cent, as every 2dp price is. So this test is
        # also the smallest end-to-end demonstration of the bug's consequence: removing one
        # spurious point moved this setup across `entry_technique`'s `low=5` edge and down a
        # posture rung. The companion test below shows the point is still earned when the
        # entry genuinely sits on a handle.
        rule = _pullback_rule()
        candles_by_tf = {Granularity.ONE_HOUR: _bullish_pullback_candles()}

        signals = evaluate(rules=[rule], candles_by_tf=candles_by_tf)

        assert len(signals) == 1
        assert signals[0].cts_score == 4
        assert signals[0].entry_technique == "confirm_3bar"

    def test_round_number_point_is_earned_when_the_entry_sits_on_a_handle(self) -> None:
        # The other half of #225: the factor must still fire when it should. Same fixture,
        # same everything, with the entry nudged onto the 130 handle -- the point comes back
        # and the tier returns to "signal_candle". Asserted through `assemble_cts_context`
        # rather than `evaluate` because the entry is a function of the fixture's candles and
        # cannot be set independently through the rule.
        rule = _pullback_rule()
        candles = _bullish_pullback_candles()
        setup = rule.detect({Granularity.ONE_HOUR: candles})
        assert setup is not None

        off_handle = assemble_cts_context(setup, candles)
        on_handle = assemble_cts_context(replace(setup, entry=Decimal("130.00")), candles)

        assert off_handle["round_number_proximity"] is False
        assert on_handle["round_number_proximity"] is True
        assert indicators_cts.score(on_handle).total == indicators_cts.score(off_handle).total + 1


class TestLowCtsConfirm3Bar:
    def test_low_but_present_score_yields_confirm_3bar(self) -> None:
        rule = _pullback_rule()
        candles_by_tf = {Granularity.ONE_HOUR: _bullish_pullback_candles()}
        # 3 points (present) is below the default low=5 threshold.
        weights = {"condition_aligned": 3}

        signals = evaluate(rules=[rule], candles_by_tf=candles_by_tf, weights=weights)

        assert len(signals) == 1
        assert signals[0].cts_score == 3
        assert signals[0].entry_technique == "confirm_3bar"

    def test_no_present_factors_yields_confirm_3bar(self) -> None:
        rule = _pullback_rule()
        candles_by_tf = {Granularity.ONE_HOUR: _bullish_pullback_candles()}
        # deceleration is confirmed absent for this fixture -> total stays 0.
        weights = {"deceleration": 5}

        signals = evaluate(rules=[rule], candles_by_tf=candles_by_tf, weights=weights)

        assert len(signals) == 1
        assert signals[0].cts_score == 0
        assert signals[0].entry_technique == "confirm_3bar"


class TestKillZoneGate:
    def test_sub_floor_rr_yields_no_signal(self) -> None:
        setup = Setup(
            product_id="BTC-USD",
            direction="long",
            entry=Decimal("100"),
            stop=Decimal("90"),
            target=Decimal("105"),  # rr = 0.5, below the >=1:1 kill-zone floor
            context={},
            ts=_uptrend_candles()[-1].ts,
        )
        assert setup.rr == Decimal("0.5")
        rule = _FixedSetupRule(setup)
        candles_by_tf = {Granularity.ONE_HOUR: _uptrend_candles()}  # non-choppy: isolates rr

        signals = evaluate(rules=[rule], candles_by_tf=candles_by_tf)

        assert signals == []

    def test_at_floor_rr_is_accepted(self) -> None:
        setup = Setup(
            product_id="BTC-USD",
            direction="long",
            entry=Decimal("100"),
            stop=Decimal("90"),
            target=Decimal("110"),  # rr = 1.0, exactly at the default floor
            context={},
            ts=_uptrend_candles()[-1].ts,
        )
        assert setup.rr == DEFAULT_RR_FLOOR
        rule = _FixedSetupRule(setup)
        candles_by_tf = {Granularity.ONE_HOUR: _uptrend_candles()}

        signals = evaluate(rules=[rule], candles_by_tf=candles_by_tf)

        assert len(signals) == 1
        assert signals[0].action == Action.ENTER


class TestChoppyRegimeGate:
    def test_choppy_regime_yields_no_signal(self) -> None:
        setup = Setup(
            product_id="BTC-USD",
            direction="long",
            entry=Decimal("100"),
            stop=Decimal("90"),
            target=Decimal("120"),  # rr = 2.0 -- would clear the kill-zone gate cleanly
            context={},
            ts=_choppy_candles()[-1].ts,
        )
        rule = _FixedSetupRule(setup)
        candles_by_tf = {Granularity.ONE_HOUR: _choppy_candles()}

        signals = evaluate(rules=[rule], candles_by_tf=candles_by_tf)

        assert signals == []


class TestDcaExemptFromKillZone:
    def test_dca_setup_is_emitted_despite_zero_rr(self) -> None:
        rule = Dca(product_id="BTC-USD", cadence_days=7, budget_usd=Decimal("50"))
        candles_by_tf = {Granularity.ONE_DAY: _dca_daily_candle()}
        raw_setup = rule.detect(candles_by_tf)
        assert raw_setup is not None
        assert raw_setup.rr == Decimal("0")

        signals = evaluate(rules=[rule], candles_by_tf=candles_by_tf)

        assert len(signals) == 1
        signal = signals[0]
        assert signal.rule_name == "dca"
        assert signal.action == Action.ENTER
        assert signal.side == Side.BUY
        assert signal.setup is not None
        assert signal.setup.rr == Decimal("0")

    def test_dca_setup_survives_a_choppy_daily_regime(self) -> None:
        # DCA buys through drawdowns/chop by design (spec §12.1) -- it is exempt from the
        # regime-based choppy gate for the same reason it's exempt from the rr gate. Days are
        # spaced 7 apart (verified via `regime.detect_condition`: CHOPPY, not tradeable) so the
        # *last* candle still lands on a cadence_days=7 boundary (42 % 7 == 0).
        days = (0, 7, 14, 21, 28, 35, 42)
        prices = [Decimal(v) for v in ("100", "115", "92", "118", "88", "122", "85")]
        choppy_daily = [
            Candle(ts=day * _DAY, open=p, high=p, low=p, close=p, volume=Decimal("1"))
            for day, p in zip(days, prices, strict=True)
        ]
        rule = Dca(product_id="BTC-USD", cadence_days=7, budget_usd=Decimal("50"))

        signals = evaluate(rules=[rule], candles_by_tf={Granularity.ONE_DAY: choppy_daily})

        assert len(signals) == 1
        assert signals[0].action == Action.ENTER


class TestNoSetupSkipsRule:
    def test_rule_that_never_fires_yields_no_signal(self) -> None:
        signals = evaluate(
            rules=[_NoSetupRule()], candles_by_tf={Granularity.ONE_HOUR: _uptrend_candles()}
        )
        assert signals == []


class TestMultipleRules:
    def test_evaluates_every_rule_independently(self) -> None:
        good_setup = Setup(
            product_id="ETH-USD",
            direction="long",
            entry=Decimal("100"),
            stop=Decimal("90"),
            target=Decimal("115"),
            context={},
            ts=_uptrend_candles()[-1].ts,
        )
        bad_setup = Setup(
            product_id="ETH-USD",
            direction="long",
            entry=Decimal("100"),
            stop=Decimal("90"),
            target=Decimal("95"),
            context={},
            ts=_uptrend_candles()[-1].ts,
        )
        rules: list[Rule] = [
            _NoSetupRule(),
            _FixedSetupRule(good_setup, name="good"),
            _FixedSetupRule(bad_setup, name="bad"),
        ]
        candles_by_tf = {Granularity.ONE_HOUR: _uptrend_candles()}

        signals = evaluate(rules=rules, candles_by_tf=candles_by_tf)

        assert [s.rule_name for s in signals] == ["good"]


class TestSignalPersistence:
    def test_evaluate_writes_emitted_signals_to_the_signals_table(self) -> None:
        conn = connect(":memory:")
        migrate(conn)
        repo = Repository(conn)
        rule = _pullback_rule()
        candles_by_tf = {Granularity.ONE_HOUR: _bullish_pullback_candles()}

        signals = evaluate(rules=[rule], candles_by_tf=candles_by_tf, repo=repo)

        assert len(signals) == 1
        rows = conn.execute("SELECT * FROM signals").fetchall()
        assert len(rows) == 1
        row = rows[0]
        assert row["product_id"] == "BTC-USD"
        assert row["ts"] == signals[0].ts
        assert row["cts_score"] == str(signals[0].cts_score)
        assert row["fired"] == 1
        payload = json.loads(row["indicators"])
        assert payload["rule_name"] == "pullback_continuation"
        assert payload["entry_technique"] == signals[0].entry_technique

    def test_evaluate_does_not_persist_rejected_candidates(self) -> None:
        conn = connect(":memory:")
        migrate(conn)
        repo = Repository(conn)
        setup = Setup(
            product_id="BTC-USD",
            direction="long",
            entry=Decimal("100"),
            stop=Decimal("90"),
            target=Decimal("95"),
            context={},
            ts=_choppy_candles()[-1].ts,
        )
        rule = _FixedSetupRule(setup)
        candles_by_tf = {Granularity.ONE_HOUR: _choppy_candles()}

        signals = evaluate(rules=[rule], candles_by_tf=candles_by_tf, repo=repo)

        assert signals == []
        rows = conn.execute("SELECT * FROM signals").fetchall()
        assert rows == []


class TestHigherTfBiasGate:
    def test_bearish_higher_tf_rejects_a_long_setup(self) -> None:
        setup = Setup(
            product_id="BTC-USD",
            direction="long",
            entry=Decimal("100"),
            stop=Decimal("90"),
            target=Decimal("115"),
            context={},
            ts=0,
        )
        rule = _FixedSetupRule(setup)
        # Reversed uptrend values -> a downtrend; verified via `regime.detect_condition`:
        # BEARISH. Re-stamped so ts is still monotonically increasing.
        bearish_daily = [
            Candle(ts=i * _DAY, open=c.open, high=c.high, low=c.low, close=c.close, volume=c.volume)
            for i, c in enumerate(reversed(_uptrend_candles()))
        ]
        candles_by_tf = {
            Granularity.ONE_HOUR: _uptrend_candles(),
            Granularity.ONE_DAY: bearish_daily,
        }

        signals = evaluate(rules=[rule], candles_by_tf=candles_by_tf)

        assert signals == []

    def test_no_higher_tf_data_does_not_gate(self) -> None:
        setup = Setup(
            product_id="BTC-USD",
            direction="long",
            entry=Decimal("100"),
            stop=Decimal("90"),
            target=Decimal("115"),
            context={},
            ts=_uptrend_candles()[-1].ts,
        )
        rule = _FixedSetupRule(setup)
        candles_by_tf = {Granularity.ONE_HOUR: _uptrend_candles()}

        signals = evaluate(rules=[rule], candles_by_tf=candles_by_tf)

        assert len(signals) == 1


class TestNoSignalDiagnostics:
    """`engine.no_signal` carries whatever the rule recorded on `last_rejection`.

    The event already fires once per declining rule; enriching it costs no extra call site and
    no extra event, which matters because `backtest`/`portfolio_sim` drive `evaluate()` once per
    bar. A rule that records nothing logs exactly what it logged before.
    """

    def test_the_event_carries_the_rules_rejection_numbers(self, caplog) -> None:
        rule = TurtleBreakout(
            product_id="BTC-USD", entry_lookback=5, exit_lookback=3, adx_period=5, atr_period=5
        )
        # A rising base whose final bar pulls back below the channel: declines on donchian_high.
        candles = _flat_series([100 + 2 * i for i in range(20)] + [104])

        with caplog.at_level(logging.INFO):
            assert evaluate(rules=[rule], candles_by_tf={Granularity.ONE_DAY: candles}) == []

        record = next(
            r for r in caplog.records if r.getMessage() == "engine.no_signal"
        )
        fields = record.keel_fields
        assert fields["rule"] == "turtle_breakout"
        assert fields["gate"] == "donchian_high"
        assert fields["close"] == 104.0
        assert fields["entry_level"] > fields["close"]

    def test_the_event_names_the_product(self, caplog) -> None:
        """Every turtle rule is named `turtle_breakout`, so without the product the five lines a
        five-product cycle emits are indistinguishable -- and near-miss numbers you cannot
        attribute to an asset are not diagnostics."""
        rule = TurtleBreakout(
            product_id="ETH-USD", entry_lookback=5, exit_lookback=3, adx_period=5, atr_period=5
        )
        candles = _flat_series([100 + 2 * i for i in range(20)] + [104])

        with caplog.at_level(logging.INFO):
            evaluate(rules=[rule], candles_by_tf={Granularity.ONE_DAY: candles})

        record = next(r for r in caplog.records if r.getMessage() == "engine.no_signal")
        assert record.keel_fields["product"] == "ETH-USD"

    def test_a_rule_recording_nothing_logs_the_bare_event(self, caplog) -> None:
        rule = _NoSetupRule()

        with caplog.at_level(logging.INFO):
            candles_by_tf = {Granularity.ONE_DAY: _flat_series([1, 2])}
            assert evaluate(rules=[rule], candles_by_tf=candles_by_tf) == []

        record = next(r for r in caplog.records if r.getMessage() == "engine.no_signal")
        assert record.keel_fields == {"rule": rule.name}
