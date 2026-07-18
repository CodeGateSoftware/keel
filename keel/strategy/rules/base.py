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
    outcome: Literal["win", "loss", "open", "scratch"]


class Rule(ABC):
    """A strategy rule: detects long entries and signals exits from held longs.

    Concrete rules set `name`/`params` and implement `detect`/`exit_signal`/`describe`.

    `promotion_class` selects the rule's promotion floor (`strategy.promotion.floor_for_class`):
    the default `"default"` uses the canonical 100/0.55 floor; trend-followers override it to
    `"trend_follow"` for a low-win/high-R:R floor (KB §25.5).
    """

    name: str
    params: dict
    promotion_class: str = "default"

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
