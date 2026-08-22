"""Tests for `keel.strategy.exit_policy` -- the per-family, ratchet-only stop-management
policy the backtest and portfolio-sim engines apply to a held position each bar (#442).

The policy exists to give the SIM/backtest engines the same stop management the executor's
live primitives (`trail_stop_atr`, `roll_to_break_even`) express against an exchange
bracket -- ratchet-only, never widening a stop -- without pretending the live path has a
management cycle (it does not; see `executor.py`'s module docstring and issue #502).

Pure-function tests: every number is hand-computed, no engine, no repo, no broker.
"""

from __future__ import annotations

from decimal import Decimal

from keel.strategy.exit_policy import (
    EXIT_POLICY_OFF,
    ExitPolicy,
    next_stop,
    policy_for,
    trailing_atr,
)
from keel.strategy.rules.base import Rule, Setup
from keel.strategy.rules.turtle_breakout import TurtleBreakout
from keel.types import Candle, Granularity


def _candle(ts: int, o: str, h: str, l: str, c: str) -> Candle:  # noqa: E741 - OHLC convention
    return Candle(
        ts=ts, open=Decimal(o), high=Decimal(h), low=Decimal(l), close=Decimal(c), volume=Decimal(0)
    )


class _ParamRule(Rule):
    """A minimal rule that carries whatever `params` it is handed -- the shape `policy_for`
    reads. `detect`/`exit_signal` are never called by these tests."""

    name = "param_rule"

    def __init__(self, params: dict, product_id: str = "BTC-USD") -> None:
        self.params = params
        self.product_id = product_id

    def detect(self, candles_by_tf: dict[Granularity, list[Candle]]) -> Setup | None:
        return None

    def exit_signal(self, held: Setup, candles_by_tf: dict[Granularity, list[Candle]]) -> bool:
        return False

    def describe(self) -> dict:
        return {"name": self.name, "params": self.params}


# -- policy_for ---------------------------------------------------------------------------------


def test_policy_for_is_off_for_a_rule_without_exit_params() -> None:
    assert policy_for(_ParamRule({})) == EXIT_POLICY_OFF


def test_policy_for_is_off_for_turtle_by_family_design() -> None:
    """`turtle_breakout` deliberately carries NO trailing/BE-roll params: its exit IS the
    Donchian channel, and a trail would cut the long winners the system exists to let run
    (#442 hypothesis 3). `policy_for` must therefore read it as OFF -- the engine wiring
    cannot silently turn turtle trailing on."""
    assert policy_for(TurtleBreakout("BTC-USD")) == EXIT_POLICY_OFF


def test_policy_for_reads_the_per_family_params() -> None:
    policy = policy_for(_ParamRule({"trail_atr_mult": Decimal("1.5"), "be_roll_rr": Decimal("1")}))
    assert policy == ExitPolicy(
        trail_atr_mult=Decimal("1.5"), be_roll_rr=Decimal("1"), atr_period=14
    )


def test_policy_for_trailing_alone_is_a_policy() -> None:
    policy = policy_for(_ParamRule({"trail_atr_mult": Decimal("2")}))
    assert policy.trail_atr_mult == Decimal("2")
    assert policy.be_roll_rr is None


def test_policy_for_uses_the_rules_own_atr_period_when_it_has_one() -> None:
    policy = policy_for(_ParamRule({"trail_atr_mult": Decimal("2"), "atr_period": 20}))
    assert policy.atr_period == 20


# -- next_stop: the ratchet ----------------------------------------------------------------------


def test_trailing_arm_ratchets_up_as_price_rises() -> None:
    policy = ExitPolicy(trail_atr_mult=Decimal("2"), be_roll_rr=None, atr_period=14)
    stop = Decimal("94")
    stop = next_stop(policy, Decimal("100"), Decimal("94"), stop, _candle(0, "100", "101", "99", "100"), Decimal("2"))
    assert stop == Decimal("96")  # 100 - 2*2
    stop = next_stop(policy, Decimal("100"), Decimal("94"), stop, _candle(1, "100", "102", "100", "102"), Decimal("2"))
    assert stop == Decimal("98")  # 102 - 4


def test_trailing_arm_never_widens_on_a_dip() -> None:
    policy = ExitPolicy(trail_atr_mult=Decimal("2"), be_roll_rr=None, atr_period=14)
    stop = Decimal("98")
    # A close far below the prior trail computes 90 - 2*2 = 86 < 98 -- the stop MUST stay.
    stop = next_stop(policy, Decimal("100"), Decimal("94"), stop, _candle(0, "99", "100", "85", "90"), Decimal("2"))
    assert stop == Decimal("98")


def test_trailing_arm_is_inert_when_atr_is_unavailable() -> None:
    policy = ExitPolicy(trail_atr_mult=Decimal("2"), be_roll_rr=None, atr_period=14)
    stop = next_stop(policy, Decimal("100"), Decimal("94"), Decimal("94"), _candle(0, "100", "101", "99", "100"), None)
    assert stop == Decimal("94")


def test_be_roll_arm_moves_the_stop_to_entry_once_the_threshold_clears() -> None:
    policy = ExitPolicy(trail_atr_mult=None, be_roll_rr=Decimal("1"), atr_period=14)
    # entry 100, initial stop 90 -> risk 10; a high of 110 clears +1R.
    stop = next_stop(policy, Decimal("100"), Decimal("90"), Decimal("90"), _candle(0, "100", "110", "99", "108"), Decimal("2"))
    assert stop == Decimal("100")


def test_be_roll_arm_does_not_fire_below_the_threshold() -> None:
    policy = ExitPolicy(trail_atr_mult=None, be_roll_rr=Decimal("1"), atr_period=14)
    stop = next_stop(policy, Decimal("100"), Decimal("90"), Decimal("90"), _candle(0, "100", "109", "99", "108"), Decimal("2"))
    assert stop == Decimal("90")


def test_be_roll_arm_never_widens_after_a_ratchet() -> None:
    policy = ExitPolicy(trail_atr_mult=None, be_roll_rr=Decimal("1"), atr_period=14)
    # Already rolled to entry; a subsequent pullback bar computes nothing above entry.
    stop = next_stop(policy, Decimal("100"), Decimal("90"), Decimal("100"), _candle(0, "99", "101", "95", "96"), Decimal("2"))
    assert stop == Decimal("100")


def test_both_arms_combine_by_max_and_can_ride_above_entry() -> None:
    policy = ExitPolicy(trail_atr_mult=Decimal("2"), be_roll_rr=Decimal("1"), atr_period=14)
    # BE proposes entry=100; the trail proposes 104-4=100 as well; a later bar proposes more.
    stop = next_stop(policy, Decimal("100"), Decimal("90"), Decimal("90"), _candle(0, "100", "110", "99", "104"), Decimal("2"))
    assert stop == Decimal("100")
    stop = next_stop(policy, Decimal("100"), Decimal("90"), stop, _candle(1, "104", "106", "103", "106"), Decimal("2"))
    assert stop == Decimal("102")


def test_off_policy_returns_the_stop_unchanged() -> None:
    stop = next_stop(EXIT_POLICY_OFF, Decimal("100"), Decimal("94"), Decimal("94"), _candle(0, "100", "110", "99", "108"), Decimal("2"))
    assert stop == Decimal("94")


def test_next_stop_is_monotone_over_an_adversarial_walk() -> None:
    """The property the whole design rests on, stated as one walk: for ANY sequence of
    bars, the stop `next_stop` emits is non-decreasing. This is the sim-side twin of
    `tests/execution/test_executor.py::test_a_ratchet_only_trail_can_never_trip_rail_9`."""
    policy = ExitPolicy(trail_atr_mult=Decimal("1.5"), be_roll_rr=Decimal("1"), atr_period=14)
    entry, initial = Decimal("100"), Decimal("94")
    stop = initial
    bars = [
        _candle(0, "100", "103", "99", "102"),
        _candle(1, "101", "104", "97", "99"),  # dip: computed trail falls
        _candle(2, "99", "112", "98", "111"),  # spike: BE + trail both propose
        _candle(3, "110", "111", "96", "97"),  # crash: nothing may lower the stop
        _candle(4, "97", "100", "95", "99"),
        _candle(5, "99", "120", "98", "119"),
    ]
    atrs = [Decimal("2"), Decimal("3"), Decimal("2"), Decimal("8"), Decimal("4"), Decimal("2")]
    seen = [stop]
    for bar, atr in zip(bars, atrs, strict=True):
        stop = next_stop(policy, entry, initial, stop, bar, atr)
        assert stop >= seen[-1]
        seen.append(stop)


# -- trailing_atr --------------------------------------------------------------------------------


def test_trailing_atr_is_constant_when_true_range_is_constant() -> None:
    bars = [_candle(i, "100", "101", "99", "100") for i in range(20)]
    assert trailing_atr(bars, 14) == Decimal("2")


def test_trailing_atr_is_none_without_enough_bars() -> None:
    bars = [_candle(i, "100", "101", "99", "100") for i in range(3)]
    assert trailing_atr(bars, 14) is None
