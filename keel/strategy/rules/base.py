"""Shared strategy interfaces and value types.

This is the strategy-layer contract: `Action`, `Setup`, `Signal`, `Trade`, and the
`Rule` ABC defined here are imported by every rule implementation, the CTS scorer,
the evaluation engine, the backtester, and the paper trader (Phase 2 tasks 2-9).

Money/prices are always `Decimal`. Long-only spot, no leverage: `Setup.direction`
is pinned to `"long"` for v1; bearish setups are exit/don't-buy filters, not shorts.

`ParamSpec`/`Rule.param_space` (issue #528) live here too: a rule's declaration of the
dimensions a parameter sweep may legitimately explore, which is what makes the trials
count for the overfitting correction DERIVABLE rather than remembered -- see the PRD
(`docs/superpowers/specs/2026-08-23-strategy-api-expressiveness-prd.md` §4.1).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from enum import Enum
from typing import Any, ClassVar, Literal

from keel.types import Candle, Granularity, Side


class Action(str, Enum):
    """What the evaluation engine decided to do for a rule/product on this bar."""

    ENTER = "ENTER"
    EXIT = "EXIT"
    NONE = "NONE"


@dataclass(frozen=True)
class Setup:
    """A candidate long entry, prior to CTS scoring.

    `context` carries the indicator values that produced this setup, used both for
    explainability and as the CTS scorer's input.
    """

    product_id: str
    direction: Literal["long"]
    entry: Decimal
    stop: Decimal
    target: Decimal
    context: dict[str, Any]
    ts: int

    def __post_init__(self) -> None:
        """Enforce at RUNTIME the one thing `direction: Literal["long"]` only asserts statically.

        `Literal["long"]` is a promise to mypy and nothing else. A rule -- a first-party one
        edited carelessly, or a foreign one, since `RULE_REGISTRY` is a plain dict any caller can
        write to -- could construct `Setup(direction="short", ...)` and nothing raised (#447).

        It has never mattered, and this does not exist because it started to. Four independent
        layers already stand behind it: `strategy.engine.evaluate` builds every `Signal` with
        `side=Side.BUY` unconditionally and never reads `direction` for anything but a diagnostic
        context dict; `guards.check` rails 18 and 19 refuse anything that is not a spot product
        settling in a configured currency, whatever produced the intent; and no sizing path has a
        short branch to take. What was missing is that "a rule cannot go short" was an
        ASSUMPTION about four layers rather than a checked fact about one, and #447 asked for it
        by test rather than assumption. This is the cheapest place to make it a fact: the type
        already says it, so nothing shipped can trip it, and a rule that tries fails inside its
        own `detect()` rather than emitting an intent for four other layers to catch.

        **The price ordering is deliberately NOT checked here, and that is a correction to
        #447's analysis rather than an omission.** `stop < entry < target` is what a rule's
        PROPOSAL guarantees, and `strategy.engine` enforces exactly that where a rule proposes
        (`_long_shaped_ok`). `Setup` is a wider type than a proposal: `agent._handle_exits`
        rebuilds one for a HELD position, whose stop is `open_stop` -- a stop that
        `exit_policy.next_stop` ratchets strictly upward, `max(stop, entry)` on the break-even
        roll and higher still on the trail. A winning trailed long therefore has `stop > entry`
        as its normal, intended state, and its reconstruction sets `target=entry` besides.
        Enforcing the proposal's inequality on the type would raise on the live exit path for
        precisely the trades that are working, which is the worst possible place to be wrong.
        `Dca` is a second, independent reason: it ships `stop=0`/`target=entry` sentinels because
        accumulation is not a risk-defined trade.
        """
        if self.direction != "long":
            raise ValueError(
                f"Setup.direction must be 'long', got {self.direction!r}. keel is long-only "
                f"spot (PRD section 2); there is no short path in sizing, execution or the rails."
            )

    @property
    def rr(self) -> Decimal:
        """Reward:risk ratio, e.g. entry=100, stop=90, target=120 -> rr=2."""
        return (self.target - self.entry) / (self.entry - self.stop)


@dataclass(frozen=True)
class Signal:
    """The evaluation engine's decision for a rule/product on this bar.

    BUY on ENTER; SELL on EXIT. `setup` is the triggering `Setup` for ENTER signals
    and `None` for EXIT/NONE.
    """

    rule_name: str
    product_id: str
    action: Action
    side: Side
    setup: Setup | None
    cts_score: int
    entry_technique: str
    ts: int
    #: The originating `rules.id` DB row, when the emitting `Rule` was reconstructed via
    #: `agent._build_rule` (which threads `Rule.rule_id` through). `None` for a hand-constructed
    #: `Rule`/`Signal` (most tests) -- purely additive metadata, never read by any gate/guard.
    rule_id: int | None = None


#: How a trade ended. Named rather than spelled out at each use so the producer
#: (`backtest._closed_trade`, which picks the branch) and the consumer (`Trade.outcome`) cannot
#: drift: an inline `Literal[...]` repeated in both places lets one side gain a value the other
#: silently rejects, and the mismatch only shows up as an `arg-type` error at the constructor.
#
#: Spelled as a plain assignment, NOT the PEP 695 `type TradeOutcome = ...` statement, and that
#: is load-bearing rather than stylistic: `get_type_hints()` leaves a `type` alias as a
#: `TypeAliasType`, whose `get_origin()` is `None`, while the assignment form resolves through to
#: `Literal`. `commands.rules._declared_choices` validates an operator's `rules add --params`
#: by testing exactly `get_origin(hint) is Literal`, so the modern spelling would silently turn
#: that validation off for any param annotated with it. `StopMethod`/`TargetMethod` in the rule
#: modules use this same form for the same reason -- keep them consistent.
TradeOutcome = Literal["win", "loss", "open", "scratch"]


@dataclass
class Trade:
    """A backtest/paper fill pair (entry + optional exit)."""

    entry_ts: int
    exit_ts: int | None
    entry: Decimal
    exit: Decimal | None
    qty: Decimal
    side: Side
    pnl: Decimal | None
    r_multiple: Decimal | None
    mfe: Decimal
    mae: Decimal
    outcome: TradeOutcome


#: The arithmetic a declared dimension carries: `"int"` means the legitimate values are
#: INTEGRAL (whatever the constructor field's storage type -- `target_rr` is a `Decimal`
#: field whose legitimate values are whole numbers), and only those bounds are suggested
#: as ints; `"float"`/`"decimal"` name the field's own storage (a level like `oversold`
#: vs a money-adjacent multiple like `atr_stop_mult`) and share the fractional dispatch.
ParamType = Literal["int", "float", "decimal"]


@dataclass(frozen=True)
class ParamSpec:
    """One dimension of a rule's declared parameter space (issue #528): what a sweep may
    legitimately explore on this knob, stated BY THE RULE rather than restated by whoever
    runs the sweep.

    A declaration is three facts and a denominator: `name` (the dimension, in the searcher's
    vocabulary), `type` (int vs fractional, which the suggest dispatch and the cells
    arithmetic both read), `lo`/`hi` (the range considered legitimate -- mirroring what the
    #476 study actually pinned, not an aspiration), and `step` (the grid resolution the
    space is COUNTED at, so `cells` below is well-defined for a continuous sampler's box).
    The candor that sentence owes: the sampler itself draws OFF that grid --
    `suggest_int`/`suggest_float` carry no step -- and an off-grid draw is invisible to a
    min/max box, so every cells count is a grid convention, never a set of visited points.

    `param` names the constructor kwarg this dimension feeds, when that is not the
    dimension's own name: `PullbackContinuation`'s EMA fan is searched as three slots
    (`ema_fast`/`ema_mid`/`ema_slow`) but constructed as ONE `ema_periods` tuple, and the
    drift test reads `kwarg` to assert every declared dimension lands on a real, persisted
    constructor parameter -- a declaration naming a kwarg that no longer exists would
    silently shrink the trials count derived from it, which is worse than no declaration.

    This is a DECLARATION, not an invitation: nothing here searches anything, and running
    an optimiser against it is the explicit non-goal of #528 (the issue's own words -- it
    "would manufacture exactly the overfitting the deflated-Sharpe machinery exists to
    detect"). What it buys is that the size of the explorable space is a property of the
    rule, so `n_trials` stops being a number a human remembers to record.
    """

    name: str
    type: ParamType
    lo: int | float
    hi: int | float
    step: Decimal
    #: The constructor kwarg this dimension feeds, when it is not `name` itself.
    param: str | None = None

    def __post_init__(self) -> None:
        """Refuse the malformations loudly, at construction, where the declaration is
        written -- a silently-broken spec would corrupt every count derived from it."""
        if self.type not in ("int", "float", "decimal"):
            raise ValueError(f"{self.name!r}: unknown param space type {self.type!r}")
        if Decimal(str(self.lo)) > Decimal(str(self.hi)):
            raise ValueError(f"{self.name!r}: lo {self.lo} exceeds hi {self.hi}")
        if self.step <= 0:
            raise ValueError(f"{self.name!r}: step {self.step} must be positive")
        if self.type == "int" and not (isinstance(self.lo, int) and isinstance(self.hi, int)):
            # `bool` is an `int` subclass but no one declares a bool dimension; the strict
            # pair check is what keeps the int-vs-float suggest dispatch honest.
            raise ValueError(
                f"{self.name!r}: type {self.type!r} needs int bounds, got {self.lo!r}, {self.hi!r}"
            )

    @property
    def kwarg(self) -> str:
        """The constructor kwarg this dimension feeds: itself, unless it is one slot of a
        decomposed kwarg (`ema_fast` -> `ema_periods`)."""
        return self.name if self.param is None else self.param

    @property
    def bounds(self) -> tuple[int, int] | tuple[float, float]:
        """The `(lo, hi)` pair in the types the sampler dispatch reads: an INT pair exactly
        when the dimension is integral (the #476 harness dispatches on `isinstance(low,
        int)`, so a float `20.0` here would silently turn a discrete lookback into a
        continuous draw), a float pair otherwise."""
        if self.type == "int":
            return (int(self.lo), int(self.hi))
        return (float(self.lo), float(self.hi))

    @property
    def cells(self) -> int:
        """How many grid points the dimension holds at its declared step, both ends
        inclusive. A step that does not evenly divide the span FLOORS -- the honest count
        of grid points inside the range, never one beyond it."""
        span = Decimal(str(self.hi)) - Decimal(str(self.lo))
        return int((span / self.step).to_integral_value(rounding=ROUND_FLOOR)) + 1

    def plain(self) -> dict[str, object]:
        """The spec as JSON-plain data (`step` as a string, the repo's TEXT-money
        convention): the form `describe()` embeds, so a rule's self-description can say
        what a parameter is ALLOWED to be wherever `describe()` already travels."""
        return {
            "name": self.name,
            "type": self.type,
            "lo": self.lo,
            "hi": self.hi,
            "step": str(self.step),
        }


class Rule(ABC):
    """A strategy rule: detects long entries and signals exits from held longs.

    Concrete rules set `name`/`params` and implement `detect`/`exit_signal`/`describe`.

    `promotion_class` selects the rule's promotion floor (`strategy.promotion.floor_for_class`):
    the default `"default"` uses the canonical 100/0.55 floor; trend-followers override it to
    `"trend_follow"` for a low-win/high-R:R floor (KB §25.5).

    `rule_id` is the originating `rules.id` DB row, set by `agent._build_rule` on a rule loaded
    from `repo.get_rules()`. It defaults to `None` -- same pattern as `promotion_class` -- so
    every hand-constructed `Rule` in tests (or anywhere else) is unaffected; it exists purely so
    the id can be threaded onto emitted `Signal`s and, from there, into `orders.rule_id` for
    audit -- it plays no part in `detect`/`exit_signal`/any guard or gate.
    """

    name: str
    params: dict
    #: The product this rule instance trades. Declared here because it was already a de-facto
    #: part of the interface: every concrete rule takes it as its first constructor argument and
    #: stores it (`PullbackContinuation`, `Dca` and `TurtleBreakout` assign it; `RsiMeanReversion`
    #: carries it as a dataclass field), and `sim.portfolio_sim`/`sim.report` read
    #: `rule.product_id` off rules they hold only as this base type. Annotation only -- no
    #: default, exactly like `name`/`params`, so nothing about construction changes.
    product_id: str
    promotion_class: str = "default"
    rule_id: int | None = None
    #: The constructor kwargs whose stored form is a JSON string and whose real type is
    #: `Decimal` -- `agent.build_rule_from_params` reads this to convert them back on the way
    #: in. Declared HERE, on the class, for the same reason `param_space()` and
    #: `promotion_class` are: it is a fact about the rule, and a rule is the only thing that
    #: knows it. It lived until #447 as `agent._DECIMAL_PARAMS`, a module-level dict keyed by
    #: kind, which meant adding a `Decimal` parameter to a rule required editing a table in a
    #: DIFFERENT FILE that nothing forced you to find. Forgetting made no noise: `params`
    #: round-trips through `json.dumps`, so the value arrives at the constructor as the STRING
    #: `"1.5"`, is stored unconverted, and the first symptom is a `Decimal`/`float` `TypeError`
    #: raised deep inside the rule's own arithmetic -- mid-cycle on the live path, or
    #: mid-backtest, far from the declaration that caused it. On the class the declaration sits
    #: beside the field it describes, and `tests/strategy/test_rule_contract.py` checks it
    #: against the constructor's own annotations, so the drift is a failing test rather than a
    #: production incident.
    #:
    #: Read off the CLASS, never an instance: `build_rule_from_params` needs to know how to
    #: coerce the kwargs BEFORE it has an instance to ask. That is why these are class
    #: attributes and not the method `param_space()` is.
    decimal_params: ClassVar[tuple[str, ...]] = ()
    #: The single constructor kwarg holding a `Granularity`, stored as its `.value` string, or
    #: `None` for a rule that has none (`Dca` decides on daily candles unconditionally).
    #: `TurtleBreakout`'s is what lets the hourly evidence profile (#337) store hourly rows:
    #: without the declaration a `granularity` in a stored turtle row would reach the
    #: constructor as the STRING `"ONE_HOUR"`, and the `isinstance(value, Granularity)` lookups
    #: (`_entry_gate_granularity`, `engine._trading_granularity`) would miss it -- silently
    #: re-gating the rule on the coarsest configured granularity while it kept deciding on
    #: daily candles. A rule with two granularity kwargs has no way to say so here; none has
    #: ever wanted one, and inventing the plural now would be a shape with no user.
    granularity_param: ClassVar[str | None] = None
    #: The constructor kwargs whose stored form is a JSON list and whose real type is a tuple.
    #: Until #447 this was not a table at all but a literal `if kind == "pullback_continuation"`
    #: branch inside `build_rule_from_params` -- the same contract as the two above, expressed
    #: as a hardcoded special case for one rule, which is the form drift takes when a table is
    #: not written down. Deliberately NOT part of `agent.coerced_param_keys`: a tuple param
    #: arrives as a LIST, not a quoted string, and that function answers a narrower question
    #: (which values an operator may legitimately quote in `rules add --params`).
    tuple_params: ClassVar[tuple[str, ...]] = ()
    #: Why the last `detect()` call declined, or `None` if it fired (or never recorded one).
    #: `strategy.engine.evaluate` merges it into the `engine.no_signal` event it already emits,
    #: so a cycle reporting `signals=0` can say whether price was 1% or 40% off the trigger.
    #: PURELY DIAGNOSTIC -- no gate, guard or sizing path reads it, and a rule that never sets
    #: it logs exactly what it logged before. Recording it is optional for a rule; the shape is
    #: `{"gate": <stable id>, ...numbers}`.
    last_rejection: dict | None = None

    @abstractmethod
    def detect(self, candles_by_tf: dict[Granularity, list[Candle]]) -> Setup | None:
        """Pure. Returns a long entry `Setup` if the rule's conditions are met, else None."""
        raise NotImplementedError

    @abstractmethod
    def exit_signal(self, held: Setup, candles_by_tf: dict[Granularity, list[Candle]]) -> bool:
        """Whether to close the held long `Setup` given the latest candles."""
        raise NotImplementedError

    @abstractmethod
    def describe(self) -> dict:
        """Name + params, for persistence in the `rules` table."""
        raise NotImplementedError

    def param_space(self) -> tuple[ParamSpec, ...]:
        """The dimensions of this rule's parameter space a sweep may legitimately explore
        (issue #528). Default: EMPTY -- a rule with nothing sweepable declares exactly
        that, which is a truthful statement ("0 declared cells"), not a missing one.

        The declaration is what makes the trials count for the overfitting correction
        (`research.deflate.expected_max_sharpe(n_trials)`) derivable rather than
        remembered: `research.tuning` reads it to build its search spaces (one source of
        truth, not two that can disagree), `declared_cells` counts the grid it implies,
        and a sweep that explores beyond it is refused rather than silently exceeded.
        Declaring the space is NOT licence to search it -- no optimiser ships with this.
        """
        return ()
