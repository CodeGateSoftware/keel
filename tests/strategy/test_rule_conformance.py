"""The four first-party rules, held to `RuleConformanceTests` (#447 stage 3).

Each subclass supplies `rule()` -- a configured instance -- and `firing_candles()`, reusing
the candle builder and rule-construction helper each rule's OWN test module already
hand-verified to make it fire, rather than inventing a fifth, independently-maintained set of
series that could quietly drift from the ones `test_turtle_breakout.py`,
`test_pullback.py`, `test_rsi_meanrev.py` and `test_dca.py` already pin gate-by-gate.

See `tests/strategy/rule_conformance.py`'s module docstring for why this suite is NOT a
plugin-facing API and does not ship from `keel/`: issue #447 decided the rule registry stays
curated, and a suite that shipped would imply a third party is meant to subclass it.
"""

from __future__ import annotations

from decimal import Decimal

from keel.strategy.reduction import Holding, Lot
from keel.strategy.rules.base import Rule
from keel.strategy.rules.dca import Dca
from keel.strategy.rules.reverse_dca import ReverseDca
from keel.types import Candle, Granularity
from tests.strategy.rule_conformance import ReductionConformanceTests, RuleConformanceTests
from tests.strategy.test_dca import _candle as _dca_candle
from tests.strategy.test_pullback import _bullish_pullback_candles
from tests.strategy.test_pullback import _rule as _pullback_rule
from tests.strategy.test_rsi_meanrev import _oversold_at_support_series
from tests.strategy.test_rsi_meanrev import _rule as _rsi_rule
from tests.strategy.test_turtle_breakout import _breakout_candles
from tests.strategy.test_turtle_breakout import _rule as _turtle_rule


class TestPullbackContinuationConformance(RuleConformanceTests):
    def rule(self) -> Rule:
        return _pullback_rule()

    def firing_candles(self) -> dict[Granularity, list[Candle]]:
        return {Granularity.ONE_HOUR: _bullish_pullback_candles()}


class TestRsiMeanReversionConformance(RuleConformanceTests):
    def rule(self) -> Rule:
        return _rsi_rule()

    def firing_candles(self) -> dict[Granularity, list[Candle]]:
        return {Granularity.ONE_HOUR: _oversold_at_support_series()}


class TestDcaConformance(RuleConformanceTests):
    def rule(self) -> Rule:
        return Dca(product_id="BTC-USD", cadence_days=7, budget_usd=Decimal("50"))

    def firing_candles(self) -> dict[Granularity, list[Candle]]:
        # Day 0 is a cadence boundary (0 % 7 == 0): the cadence gate is the first thing
        # detect() checks, so this is the minimal series that fires -- one bar.
        return {Granularity.ONE_DAY: [_dca_candle(day=0, price="100")]}


class TestTurtleBreakoutConformance(RuleConformanceTests):
    def rule(self) -> Rule:
        return _turtle_rule()

    def firing_candles(self) -> dict[Granularity, list[Candle]]:
        return {Granularity.ONE_DAY: _breakout_candles()}


class TestReverseDcaConformance(ReductionConformanceTests):
    def rule(self) -> Rule:
        return ReverseDca("BTC-USD", target_usd=Decimal("100"), min_price_floor=Decimal("1"))

    def holding(self) -> Holding:
        return Holding(
            "BTC-USD", (Lot(1, "dca", 0, Decimal("0.01"), Decimal("50000"), Decimal("0")),)
        )

    def firing_candles(self) -> dict[Granularity, list[Candle]]:
        # Day 60 is a 30-day cadence boundary; one bar at 100000 is its own lookback high, so
        # the drawdown gate passes and the floor of 1 is far below.
        return {Granularity.ONE_DAY: [_dca_candle(day=60, price="100000")]}
