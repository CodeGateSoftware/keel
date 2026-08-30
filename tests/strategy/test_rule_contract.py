"""The rule contract: what a `Rule` must declare so it can be rebuilt from a database row.

`rules.params` round-trips through `json.dumps`/`json.loads`, which has no `Decimal`, no enum
and no tuple. `agent.build_rule_from_params` is the one place that conversion happens, and until
#447 WHAT to convert lived in `agent._DECIMAL_PARAMS`, `agent._GRANULARITY_PARAMS` and a
hardcoded `if kind == "pullback_continuation"` branch -- three kind-keyed tables in a module no
rule author has any reason to open. It now lives on the rule classes as `Rule.decimal_params`,
`Rule.granularity_param` and `Rule.tuple_params`.

This module pins the property that move exists to buy, in three layers:

1. A rule's OWN declaration is what drives the coercion -- proved on a rule this repository does
   not ship, so the pin cannot pass by accident off one of the four hardcoded table entries.
2. The four shipped rules' declarations agree with their constructors' own type ANNOTATIONS, so
   adding a `Decimal` field and forgetting to declare it is a failing test rather than a
   `Decimal`/`float` `TypeError` raised mid-cycle inside the rule's arithmetic.
3. Every shipped rule's stored row rebuilds to a byte-identical rule, values and types. The live
   deployment rebuilds its rules from the database on every cycle; a coercion regression here is
   a production incident, so it is pinned as a round-trip and not as a table comparison.
"""

from __future__ import annotations

import inspect
import json
import types
import typing
from decimal import Decimal
from typing import Any, get_args, get_origin

import pytest

from keel.agent import RULE_REGISTRY, build_rule_from_params
from keel.strategy.rules.base import Rule, Setup
from keel.types import Candle, Granularity

# -- layer 1: a rule's own declaration drives the coercion ---------------------------------


class _ForeignRule(Rule):
    """A rule this repository does not ship, declaring a `Decimal` and a `Granularity` knob.

    The point of testing on a rule with no entry in any table in `keel/agent.py` is that it is
    the only way to show the DECLARATION is doing the work. A test written against
    `TurtleBreakout` passes identically whether the coercion reads `atr_stop_mult` off the class
    or off a hardcoded dict that happens to name the same string.
    """

    decimal_params = ("threshold",)
    granularity_param = "timeframe"
    tuple_params = ("lookbacks",)

    def __init__(
        self,
        product_id: str,
        threshold: Decimal = Decimal("1.5"),
        timeframe: Granularity = Granularity.ONE_DAY,
        lookbacks: tuple[int, ...] = (5, 20),
        undeclared: Decimal = Decimal("1"),
    ) -> None:
        self.name = "foreign"
        self.product_id = product_id
        self.threshold = threshold
        self.timeframe = timeframe
        self.lookbacks = lookbacks
        self.undeclared = undeclared
        self.params: dict = {}

    def detect(self, candles_by_tf: dict[Granularity, list[Candle]]) -> Setup | None:
        return None

    def exit_signal(self, held: Setup, candles_by_tf: dict[Granularity, list[Candle]]) -> bool:
        return False

    def describe(self) -> dict:
        return {"name": self.name, "params": self.params}


@pytest.fixture
def foreign_kind(monkeypatch: pytest.MonkeyPatch) -> str:
    """Register `_ForeignRule` under a kind for the duration of one test.

    `monkeypatch.setitem` rather than a bare assignment plus cleanup: `RULE_REGISTRY` is
    process-global and a test that failed mid-body would otherwise leave the fake kind behind
    for everything that ran after it.
    """
    monkeypatch.setitem(RULE_REGISTRY, "foreign", _ForeignRule)
    return "foreign"


def test_a_rules_own_declaration_is_what_coerces_its_decimal_param(foreign_kind: str) -> None:
    """The failure #447 names: a `Decimal` param silently arriving as a `str`.

    Before the declarations moved onto `Rule`, this was not merely untested -- it was
    UNREACHABLE. A rule outside `keel/agent.py`'s four-entry `_DECIMAL_PARAMS` had no way to say
    it owned a `Decimal` field, so `build_rule_from_params` handed the constructor the JSON
    string `"2.75"`, the rule stored it unconverted, and the first symptom was a
    `Decimal`/`float` `TypeError` from inside the rule's own arithmetic, cycles later.
    """
    rule = build_rule_from_params(
        foreign_kind, {"product_id": "BTC-USD", "threshold": "2.75"}
    )

    assert isinstance(rule.threshold, Decimal)
    assert rule.threshold == Decimal("2.75")


def test_an_undeclared_decimal_param_still_arrives_as_the_raw_string(foreign_kind: str) -> None:
    """The other half of the same fact, and the reason the test above is not a tautology.

    `_ForeignRule.undeclared` is annotated `Decimal` and is deliberately absent from
    `decimal_params`. If it came back as a `Decimal` anyway, the coercion would be reading
    something other than the declaration -- guessing from annotations, say -- and layer 2's
    drift check below would be pinning a rule nothing enforces. It arrives as the string it was
    stored as, which is exactly the failure the declaration exists to prevent, reproduced here
    on purpose.
    """
    rule = build_rule_from_params(
        foreign_kind, {"product_id": "BTC-USD", "undeclared": "2.75"}
    )

    assert rule.undeclared == "2.75"
    assert not isinstance(rule.undeclared, Decimal)


def test_a_rules_own_declaration_is_what_coerces_its_granularity_param(
    foreign_kind: str,
) -> None:
    """`isinstance(value, Granularity)` is how `engine._trading_granularity` and
    `_entry_gate_granularity` pick a rule's series. `Granularity` subclasses `str`, so an
    uncoerced `"ONE_HOUR"` compares equal to the enum's `.value` and fails every one of those
    identity checks silently -- re-gating the rule on the coarsest configured granularity while
    it keeps deciding on its own candles (#337).
    """
    rule = build_rule_from_params(
        foreign_kind, {"product_id": "BTC-USD", "timeframe": "ONE_HOUR"}
    )

    assert rule.timeframe is Granularity.ONE_HOUR


def test_a_rules_own_declaration_is_what_coerces_its_tuple_param(foreign_kind: str) -> None:
    """Until #447 this was not a table but a literal `if kind == "pullback_continuation"` inside
    `build_rule_from_params` -- so no rule but that one could have a tuple param at all.
    """
    rule = build_rule_from_params(
        foreign_kind, {"product_id": "BTC-USD", "lookbacks": [5, 20, 55]}
    )

    assert rule.lookbacks == (5, 20, 55)
    assert isinstance(rule.lookbacks, tuple)


def test_the_base_class_declares_nothing_so_a_rule_opts_in(foreign_kind: str) -> None:
    """`Rule`'s defaults are empty/`None`, which is the honest declaration for a rule with no
    JSON-lossy params (`Dca` has no timeframe knob). A base class that guessed -- coercing
    every param that parses as a number, say -- would turn a rule's `float` knob into a
    `Decimal` and break its arithmetic in the opposite direction.
    """
    assert Rule.decimal_params == ()
    assert Rule.granularity_param is None
    assert Rule.tuple_params == ()


# -- layer 2: the shipped declarations agree with the constructors --------------------------


def _annotated_kwargs(rule_cls: type[Rule]) -> dict[str, Any]:
    """Every constructor kwarg of `rule_cls` with its RESOLVED type annotation.

    `typing.get_type_hints` rather than the raw `__annotations__` because every rule module is
    `from __future__ import annotations`, so the raw values are strings. Works uniformly for the
    three plain classes and for `RsiMeanReversion`, whose `__init__` the dataclass machinery
    generates with the field annotations attached.
    """
    hints = typing.get_type_hints(rule_cls.__init__)
    return {
        name: hints[name]
        for name in inspect.signature(rule_cls.__init__).parameters
        if name != "self" and name in hints
    }


def _admits(annotation: Any, target: type) -> bool:
    """Whether `annotation` is `target` or an optional/union that includes it.

    `Decimal | None` (`trail_atr_mult`, `be_roll_rr`) must count: it is exactly as lossy through
    JSON as a bare `Decimal`, and `build_rule_from_params` already guards the `None` case.
    """
    if annotation is target:
        return True
    if get_origin(annotation) in (typing.Union, types.UnionType):
        return any(arg is target for arg in get_args(annotation))
    return False


@pytest.mark.parametrize("kind", sorted(RULE_REGISTRY))
def test_every_decimal_constructor_param_is_declared(kind: str) -> None:
    """The drift pin. A rule that gains a `Decimal` field and does not declare it fails HERE,
    at the declaration, rather than in production arithmetic several layers away.

    Derived from the constructor's own annotations, which is the one source that cannot be
    forgotten: you cannot add the parameter without writing the annotation.
    """
    rule_cls = RULE_REGISTRY[kind]
    annotated = {
        name
        for name, hint in _annotated_kwargs(rule_cls).items()
        if _admits(hint, Decimal)
    }

    assert set(rule_cls.decimal_params) == annotated, (
        f"{kind}: `decimal_params` says {sorted(rule_cls.decimal_params)} but the constructor "
        f"annotates {sorted(annotated)} as Decimal"
    )


@pytest.mark.parametrize("kind", sorted(RULE_REGISTRY))
def test_every_granularity_constructor_param_is_declared(kind: str) -> None:
    """As above for the timeframe knob, and it additionally pins that there is AT MOST ONE.

    `granularity_param` is a single name, not a tuple, so a rule that grew a second
    `Granularity` kwarg could declare only one of them and the other would reach the
    constructor as a bare string. No rule has ever wanted two; this is what makes that a
    checked assumption instead of an unstated one.
    """
    rule_cls = RULE_REGISTRY[kind]
    annotated = sorted(
        name
        for name, hint in _annotated_kwargs(rule_cls).items()
        if _admits(hint, Granularity)
    )

    assert len(annotated) <= 1, f"{kind}: `granularity_param` cannot express {annotated}"
    assert rule_cls.granularity_param == (annotated[0] if annotated else None), (
        f"{kind}: `granularity_param` says {rule_cls.granularity_param!r} but the constructor "
        f"annotates {annotated}"
    )


@pytest.mark.parametrize("kind", sorted(RULE_REGISTRY))
def test_every_tuple_constructor_param_is_declared(kind: str) -> None:
    """A `tuple` kwarg left undeclared arrives as a LIST. That is not a `TypeError` anywhere --
    it is worse: the rule works, and its `describe()` emits a list where the row it was built
    from held a list too, so the corruption is invisible until something asks for a hash or
    compares a rebuilt rule against a freshly constructed one.
    """
    rule_cls = RULE_REGISTRY[kind]
    annotated = {
        name
        for name, hint in _annotated_kwargs(rule_cls).items()
        if get_origin(hint) is tuple
    }

    assert set(rule_cls.tuple_params) == annotated, (
        f"{kind}: `tuple_params` says {sorted(rule_cls.tuple_params)} but the constructor "
        f"annotates {sorted(annotated)} as tuple"
    )


@pytest.mark.parametrize("kind", sorted(RULE_REGISTRY))
def test_every_declared_name_is_a_real_constructor_kwarg(kind: str) -> None:
    """The reverse direction: a declaration naming a kwarg that no longer exists.

    `build_rule_from_params` only coerces keys already present in `params`, so a stale name
    raises nothing and coerces nothing -- it just quietly stops covering the field it was
    renamed from. The same failure `ParamSpec.kwarg`'s drift test exists to catch, one contract
    over.
    """
    rule_cls = RULE_REGISTRY[kind]
    kwargs = set(_annotated_kwargs(rule_cls))

    declared = set(rule_cls.decimal_params) | set(rule_cls.tuple_params)
    if rule_cls.granularity_param is not None:
        declared.add(rule_cls.granularity_param)

    assert declared <= kwargs, (
        f"{kind}: declares {sorted(declared - kwargs)}, which no longer exist"
    )


# -- layer 3: the live path -- a stored row rebuilds identically ----------------------------


@pytest.mark.parametrize("kind", sorted(RULE_REGISTRY))
def test_a_stored_rule_row_rebuilds_identically(kind: str) -> None:
    """The live deployment rebuilds every `live` rule from `rules.params` on every cycle, so a
    coercion regression is a production incident rather than a test failure. Pinned end to end
    through the real lossy hop -- `describe()` -> `json.dumps` -> `json.loads` ->
    `build_rule_from_params` -> `describe()` -- rather than by comparing declarations, because
    the declarations are what is being changed and a table-to-table comparison would agree with
    itself no matter how wrong both sides were.

    TYPES as well as values: `Decimal("2") == 2.0` and `Granularity.ONE_HOUR == "ONE_HOUR"` are
    both true, so an equality-only assertion passes against exactly the regression this guards.
    """
    shipped = RULE_REGISTRY[kind](product_id="BTC-USD")
    stored = json.loads(json.dumps(shipped.describe()["params"], default=str))

    rebuilt = build_rule_from_params(kind, {**stored, "product_id": "BTC-USD"})
    rebuilt_params = rebuilt.describe()["params"]
    shipped_params = shipped.describe()["params"]

    assert rebuilt_params == shipped_params
    for name, value in shipped_params.items():
        assert type(rebuilt_params[name]) is type(value), (
            f"{kind}.{name}: rebuilt as {type(rebuilt_params[name]).__name__}, "
            f"shipped is {type(value).__name__}"
        )


@pytest.mark.parametrize("kind", sorted(RULE_REGISTRY))
def test_a_stored_rule_row_rebuilds_with_real_decimals_on_its_attributes(kind: str) -> None:
    """`describe()` is the rule's own account of itself, so the round-trip above could in
    principle be satisfied by a rule that stored strings and stringified them back on the way
    out. This reads the ATTRIBUTES the rule's arithmetic actually uses.

    `params` is checked alongside because three of the four rules build that dict in their
    constructor from the coerced kwargs, and it is what `exit_policy` reads for `trail_atr_mult`
    (`keel/strategy/exit_policy.py`'s own docstring relies on those arriving coerced).
    """
    rule_cls = RULE_REGISTRY[kind]
    shipped = rule_cls(product_id="BTC-USD")
    stored = json.loads(json.dumps(shipped.describe()["params"], default=str))
    rebuilt = build_rule_from_params(kind, {**stored, "product_id": "BTC-USD"})

    for name in rule_cls.decimal_params:
        value = getattr(rebuilt, name, None)
        if value is not None:
            assert isinstance(value, Decimal), f"{kind}.{name} rebuilt as {type(value).__name__}"
        if name in rebuilt.params and rebuilt.params[name] is not None:
            assert isinstance(rebuilt.params[name], Decimal), f"{kind}.params[{name!r}]"

    if rule_cls.granularity_param is not None:
        assert isinstance(getattr(rebuilt, rule_cls.granularity_param), Granularity)

    for name in rule_cls.tuple_params:
        assert isinstance(getattr(rebuilt, name, ()), tuple), f"{kind}.{name}"
