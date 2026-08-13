"""Shared strategy interfaces and value types.

This is the strategy-layer contract: `Action`, `Setup`, `Signal`, `Trade`, and the
`Rule` ABC defined here are imported by every rule implementation, the CTS scorer,
the evaluation engine, the backtester, and the paper trader (Phase 2 tasks 2-9).

Money/prices are always `Decimal`. Long-only spot, no leverage: `Setup.direction`
is pinned to `"long"` for v1; bearish setups are exit/don't-buy filters, not shorts.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any, Literal

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
type TradeOutcome = Literal["win", "loss", "open", "scratch"]


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
    def exit_signal(
        self, held: Setup, candles_by_tf: dict[Granularity, list[Candle]]
    ) -> bool:
        """Whether to close the held long `Setup` given the latest candles."""
        raise NotImplementedError

    @abstractmethod
    def describe(self) -> dict:
        """Name + params, for persistence in the `rules` table."""
        raise NotImplementedError
