"""The executable contract every first-party `Rule` is held to.

Subclass `RuleConformanceTests` in a test module and implement `rule()` and
`firing_candles()`::

    class TestMyRuleConformance(RuleConformanceTests):
        def rule(self) -> MyRule:
            return MyRule(product_id="BTC-USD")

        def firing_candles(self) -> dict[Granularity, list[Candle]]:
            return {Granularity.ONE_HOUR: candles_my_rule_actually_enters_on()}

**This suite lives in `tests/`, not in `keel/`, and that is the whole point of it.**
`packages/keel-broker-api/keel_broker_api/conformance/suite.py` ships the broker contract
from an installable package because third-party broker adapters exist and need something to
prove themselves against. Issue #447 decided the rule registry stays CURATED: `keel.agent.
RULE_REGISTRY` is a closed, hand-maintained dict of exactly the four rules this repository
ships, there is no `keel.rules` entry point, and there will not be one -- a rule is not a
pluggable adapter, it is an investment decision this team made and is accountable for. A
suite that shipped from `keel/` would imply the opposite: that some future third party is
meant to subclass it and register a rule of their own. So the class lives under `tests/`
instead, imported by the four subclasses below and nothing else, with no extra and no install
hook -- conformance the four shipped rules are held to, not a contract offered to anyone who
is not already one of them.

**What this suite cannot prove, stated plainly rather than implied by a reassuring name.**
`detect()` is documented as "Pure" on `Rule` itself, but purity is not an assertable property
from inside the same process as the code under test. What follows are SAMPLES, not proofs:

- Determinism is checked across a small, fixed number of repeated calls on the same candles.
  A rule that is non-deterministic only on some unseen input, or only one time in a thousand,
  would pass every test here and still be non-deterministic. The test is named
  `test_detect_is_deterministic_across_repeated_calls`, not `test_detect_is_pure` or
  `test_detect_is_deterministic` -- it checks exactly what it samples, no more.
- No-network and no-I/O are not checked at all. Proving them would need sandboxing (no
  filesystem, no socket, a process boundary) that exists nowhere in this category of test, and
  a suite that claimed to guarantee them without one would be lying about what it ran.

**The anti-tautology guard.** Every assertion below -- determinism, non-mutation, the
direction pin -- is checked against the `Setup` `detect()` returns. If `detect()` returns
`None` on the supplied candles, every one of those assertions passes vacuously: a suite whose
every rule declined on its fixture would report green while checking nothing. So the base
class takes a SECOND hook, `firing_candles()`, on top of the rule factory `broker()`'s mirror
`rule()` -- a candle series the rule actually enters on -- and `_fired_setup()` below asserts
the `Setup` came back non-`None` before anything downstream is allowed to mean something.
That assertion is itself load-bearing, and `test_the_firing_fixture_actually_fires` pins it as
its own named test rather than leaving it as an implicit precondition other tests might skip
past under a different call order.
"""

from __future__ import annotations

import copy
import inspect
import json
from typing import Any

from keel import agent
from keel.strategy import engine, promotion
from keel.strategy.rules.base import ParamSpec, Rule, Setup
from keel.types import Candle, Granularity


class RuleConformanceTests:
    """Mixin of contract tests. Subclass it and supply `rule()` and `firing_candles()`."""

    def rule(self) -> Rule:
        raise NotImplementedError("conformance subclasses must supply a rule() factory")

    def firing_candles(self) -> dict[Granularity, list[Candle]]:
        """A `candles_by_tf` on which `self.rule().detect(...)` returns a `Setup`, not `None`.

        Reuse a firing fixture from the rule's own test module (`tests/strategy/test_*.py`)
        rather than inventing a new one -- those series are already hand-verified against the
        rule's real gates, and a second, independently-invented fixture is a second place for
        the two to quietly drift apart.
        """
        raise NotImplementedError(
            "conformance subclasses must supply a firing_candles() fixture the rule enters on"
        )

    # --- the anti-tautology guard -----------------------------------------------------------

    def _fired_setup(self) -> Setup:
        """Call `detect()` on a fresh rule and the firing fixture, and assert it fired.

        Every test below that reads the returned `Setup` calls this rather than `detect()`
        directly, so a fixture that stopped firing (a rule's gates tightened under it, a typo
        in a candle builder import) turns every dependent test into a failure here -- loudly,
        at the one shared assertion -- instead of several tests quietly passing against `None`.
        """
        setup = self.rule().detect(copy.deepcopy(self.firing_candles()))
        assert setup is not None, (
            "firing_candles() did not fire rule().detect() -- every determinism/mutation/"
            "direction assertion in this suite would otherwise pass vacuously against None. "
            "Supply a candle series the rule actually enters on."
        )
        return setup

    def test_the_firing_fixture_actually_fires(self) -> None:
        """Pins the anti-tautology guard itself as a named, independent test.

        `_fired_setup()`'s assertion backs every other test in this class, but a helper's
        assertion is easy to lose track of under a different run order or a future refactor
        that stops calling it. Stating it here, once, as its own test is what keeps a
        future-`None` fixture a visible failure rather than a set of tests that quietly started
        checking nothing.
        """
        self._fired_setup()

    # --- determinism (sampled, not proven -- see module docstring) -------------------------

    def test_detect_is_deterministic_across_repeated_calls(self) -> None:
        """Three calls, same rule instance, same candles in (a fresh deep copy each time so
        this test cannot pass by accident off the separate non-mutation guarantee below) ->
        the same `Setup` out every time.

        The SAME rule instance matters here, not a fresh one per call:
        `PullbackContinuation` caches a `_RunningState` across `detect()` calls as a
        performance optimisation (#352), and that cache is exactly the kind of internal state
        that could make two calls on identical candles disagree if the incremental path and
        the full-recompute path it mirrors ever drifted. A fresh instance per call would never
        exercise that cache at all.
        """
        rule = self.rule()
        candles = self.firing_candles()

        first = rule.detect(copy.deepcopy(candles))
        assert first is not None, "firing_candles() did not fire on the first call"

        for _ in range(2):
            again = rule.detect(copy.deepcopy(candles))
            assert again is not None, "firing_candles() fired once and then stopped firing"
            assert again == first, "detect() returned a different Setup from identical candles"

    # --- non-mutation ------------------------------------------------------------------------

    def test_detect_does_not_mutate_its_input_candles(self) -> None:
        """`detect()` is handed the same `candles_by_tf` the evaluation engine built for every
        rule on this cycle (`strategy.engine.evaluate` loops rules over one shared read). A
        rule that reordered, trimmed or appended to its input list in place would corrupt what
        the NEXT rule in that loop sees, silently and far from the line that did it.

        The snapshot is taken before the call and compared after, rather than asserting
        anything about identity: a rule is free to build new lists internally (every shipped
        rule slices its input into a lookback window), and only mutation of the CALLER's list
        is the failure this guards against.
        """
        candles_by_tf = self.firing_candles()
        snapshot = copy.deepcopy(candles_by_tf)

        setup = self.rule().detect(candles_by_tf)

        assert setup is not None, "firing_candles() did not fire"
        assert candles_by_tf == snapshot, (
            "detect() mutated the candle series it was given -- a series shared across every "
            "rule in one evaluation cycle cannot survive a rule that edits it in place."
        )

    # --- the runtime direction pin (#447) ----------------------------------------------------

    def test_every_fired_setup_is_long_shaped(self) -> None:
        """Every `Setup` a rule returns claims `direction == "long"` (`Setup.__post_init__`,
        #447) and, separately, has the price geometry that claim implies
        (`engine._long_shaped_ok`): `stop < entry` strictly and `target >= entry`. The first is
        a runtime-checked fact about the TYPE; the second is a fact about what the four shipped
        rules actually guarantee, checked at the boundary where a `Setup` is known to be a
        fresh proposal rather than a reconstruction of something already held. A rule that
        regressed on either would emit an intent the kill-zone R:R gate and every downstream
        sizing path assume can never happen.
        """
        setup = self._fired_setup()
        assert setup.direction == "long"
        assert engine._long_shaped_ok(setup), (
            f"{setup!r} does not have the price geometry direction='long' implies"
        )

    # --- the describe() <-> build_rule_from_params round trip (PR1) ------------------------

    def test_describe_round_trips_through_build_rule_from_params(self) -> None:
        """The live deployment rebuilds every `live` rule from `rules.params` on every cycle
        through exactly this hop: `describe()` -> `json.dumps` -> `json.loads` ->
        `build_rule_from_params`. `tests/strategy/test_rule_contract.py` already pins this for
        each rule's DEFAULT construction; this repeats it for whatever THIS suite's `rule()`
        configured -- `PullbackContinuation`'s non-default `ema_periods`, `TurtleBreakout`'s
        small lookbacks, `RsiMeanReversion`'s zeroed separation -- so a coercion that happens to
        be correct only at default values is still caught here.

        Types are asserted alongside values, not only equality: `Decimal("2") == 2.0` and
        `Granularity.ONE_HOUR == "ONE_HOUR"` are both true, so an equality-only assertion would
        pass against exactly the regression PR1 (#447) fixed -- a `decimal_params`/
        `granularity_param`/`tuple_params` declaration that silently stopped covering a field.
        """
        rule = self.rule()
        kind = rule.name
        assert kind in agent.RULE_REGISTRY, f"{kind!r} is not a registered rule kind"

        described = rule.describe()
        shipped_params = described["params"]
        stored = json.loads(json.dumps(shipped_params, default=str))

        rebuilt = agent.build_rule_from_params(kind, {**stored, "product_id": rule.product_id})
        rebuilt_params = rebuilt.describe()["params"]

        assert rebuilt_params == shipped_params
        for name, value in shipped_params.items():
            assert type(rebuilt_params[name]) is type(value), (
                f"{kind}.{name}: rebuilt as {type(rebuilt_params[name]).__name__}, "
                f"configured is {type(value).__name__}"
            )

    # --- param_space() validity (#528) -------------------------------------------------------

    def test_param_space_specs_are_valid_and_name_real_constructor_kwargs(self) -> None:
        """`ParamSpec.__post_init__` already refuses a malformed spec at construction, so the
        shape check below is mostly restating a guarantee that held before this test ran. What
        it adds is the check that cannot be made at construction time: `spec.kwarg` must still
        name a parameter the rule's OWN constructor accepts. A rename of a constructor
        parameter that forgot to update the matching `ParamSpec.param` would under-count the
        rule's declared search space (a sweep silently exploring fewer cells than it reports)
        without raising anywhere -- `research.tuning` reads `param_space()` to build its search
        spaces, and a dangling `kwarg` is invisible to it.
        """
        rule = self.rule()
        accepted = set(inspect.signature(type(rule).__init__).parameters) - {"self", "name"}

        for spec in rule.param_space():
            assert isinstance(spec, ParamSpec)
            assert spec.kwarg in accepted, (
                f"{rule.name}.{spec.name} -> kwarg {spec.kwarg!r} is not a parameter of "
                f"{type(rule).__name__}.__init__"
            )

    # --- promotion_class is a value floor_for_class actually recognises (#247/#338) --------

    def test_promotion_class_is_a_recognised_value(self) -> None:
        """`promotion.floor_for_class()` falls back to the DEFAULT floor for any
        `class_name` it does not recognise -- silently, with no error of any kind. A rule whose
        `promotion_class` was typo'd or renamed out from under `promotion._CLASS_FLOORS` would
        therefore not fail to promote; it would promote against the WRONG floor, forever,
        with nothing in the path ever saying so. This test is what makes that typo a failure
        here instead of a live floor quietly misapplied.
        """
        rule = self.rule()
        known: set[Any] = {promotion.DEFAULT_CLASS} | set(promotion._CLASS_FLOORS)
        assert rule.promotion_class in known, (
            f"{rule.name}: promotion_class {rule.promotion_class!r} is not one of {sorted(known)} "
            f"-- floor_for_class() would silently apply the default floor instead"
        )


__all__ = ["RuleConformanceTests"]
