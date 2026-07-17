"""Tests for keel.strategy.rules.base: the shared strategy interfaces and value
types (Action, Setup, Signal, Trade, Rule) that all downstream rules/engine/backtest
modules import.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from keel.strategy.rules.base import (
    Action,
    Rule,
    Setup,
    Signal,
    Trade,
)
from keel.types import Candle, Granularity, Side


def _setup(
    entry: str = "100",
    stop: str = "90",
    target: str = "120",
    ts: int = 0,
    context: dict | None = None,
) -> Setup:
    return Setup(
        product_id="BTC-USD",
        direction="long",
        entry=Decimal(entry),
        stop=Decimal(stop),
        target=Decimal(target),
        context=context or {},
        ts=ts,
    )


class TestAction:
    def test_members(self) -> None:
        assert Action.ENTER.value == "ENTER"
        assert Action.EXIT.value == "EXIT"
        assert Action.NONE.value == "NONE"
        assert isinstance(Action.ENTER, str)


class TestSetup:
    def test_rr_computes_reward_to_risk(self) -> None:
        setup = _setup(entry="100", stop="90", target="120")
        assert setup.rr == Decimal(2)

    def test_rr_with_fractional_prices(self) -> None:
        setup = _setup(entry="50", stop="45", target="65")
        assert setup.rr == Decimal(3)

    def test_is_frozen(self) -> None:
        setup = _setup()
        with pytest.raises(Exception):  # noqa: B017 - dataclasses.FrozenInstanceError
            setup.entry = Decimal(1)  # type: ignore[misc]

    def test_context_and_fields_roundtrip(self) -> None:
        setup = _setup(ts=1_700_000_000, context={"rsi": 28.5})
        assert setup.product_id == "BTC-USD"
        assert setup.direction == "long"
        assert setup.context == {"rsi": 28.5}
        assert setup.ts == 1_700_000_000


class TestSignal:
    def test_constructs_with_expected_fields(self) -> None:
        setup = _setup()
        signal = Signal(
            rule_name="pullback_continuation",
            product_id="BTC-USD",
            action=Action.ENTER,
            side=Side.BUY,
            setup=setup,
            cts_score=7,
            entry_technique="signal_candle",
            ts=1_700_000_100,
        )
        assert signal.rule_name == "pullback_continuation"
        assert signal.action is Action.ENTER
        assert signal.side is Side.BUY
        assert signal.setup is setup
        assert signal.cts_score == 7
        assert signal.entry_technique == "signal_candle"
        assert signal.ts == 1_700_000_100

    def test_setup_may_be_none_for_none_action(self) -> None:
        signal = Signal(
            rule_name="rsi_meanrev",
            product_id="BTC-USD",
            action=Action.NONE,
            side=Side.BUY,
            setup=None,
            cts_score=0,
            entry_technique="confirm_3bar",
            ts=0,
        )
        assert signal.setup is None

    def test_is_frozen(self) -> None:
        signal = Signal(
            rule_name="r",
            product_id="BTC-USD",
            action=Action.NONE,
            side=Side.BUY,
            setup=None,
            cts_score=0,
            entry_technique="confirm_3bar",
            ts=0,
        )
        with pytest.raises(Exception):  # noqa: B017 - dataclasses.FrozenInstanceError
            signal.cts_score = 5  # type: ignore[misc]


class TestTrade:
    def test_constructs_with_expected_fields_open_trade(self) -> None:
        trade = Trade(
            entry_ts=0,
            exit_ts=None,
            entry=Decimal(100),
            exit=None,
            qty=Decimal(1),
            side=Side.BUY,
            pnl=None,
            r_multiple=None,
            mfe=Decimal(0),
            mae=Decimal(0),
            outcome="open",
        )
        assert trade.exit_ts is None
        assert trade.exit is None
        assert trade.pnl is None
        assert trade.outcome == "open"

    def test_constructs_with_expected_fields_closed_win(self) -> None:
        trade = Trade(
            entry_ts=0,
            exit_ts=100,
            entry=Decimal(100),
            exit=Decimal(120),
            qty=Decimal(1),
            side=Side.BUY,
            pnl=Decimal(20),
            r_multiple=Decimal(2),
            mfe=Decimal(22),
            mae=Decimal(2),
            outcome="win",
        )
        assert trade.outcome == "win"
        assert trade.pnl == Decimal(20)
        assert trade.r_multiple == Decimal(2)


class _TrivialRule(Rule):
    """A minimal concrete Rule used only to prove the ABC can be satisfied."""

    name = "trivial"
    params: dict = {}

    def detect(
        self, candles_by_tf: dict[Granularity, list[Candle]]
    ) -> Setup | None:
        return None

    def exit_signal(
        self, held: Setup, candles_by_tf: dict[Granularity, list[Candle]]
    ) -> bool:
        return False

    def describe(self) -> dict:
        return {"name": self.name, "params": self.params}


class TestRule:
    def test_cannot_instantiate_abstract_rule_directly(self) -> None:
        with pytest.raises(TypeError):
            Rule()  # type: ignore[abstract]

    def test_concrete_subclass_satisfies_the_abc(self) -> None:
        rule = _TrivialRule()
        assert rule.detect({}) is None
        assert rule.exit_signal(_setup(), {}) is False
        assert rule.describe() == {"name": "trivial", "params": {}}
        assert rule.name == "trivial"
        assert rule.params == {}
