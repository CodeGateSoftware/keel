"""The order model: one frozen dataclass per order shape the engine can express.

A sum type rather than one flat dataclass with an `order_type` enum, because a flat shape makes
nonsense representable -- a market order carrying a limit price, a stop-limit with no stop. On
the live-money path a malformed order is not a crash you notice; it can be a *filled* order you
did not intend.

`kind` is a stable string, not a type object: capabilities are declared against it, and strings
are serialisable, loggable, and debuggable in a way `frozenset[type]` is not.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar

from keel_core.types import Side


def _require_positive(name: str, value: Decimal) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value}")


@dataclass(frozen=True)
class MarketIOCByQuote:
    """Market order sized in quote currency (spend N USDC). Used for entries."""

    kind: ClassVar[str] = "market_ioc_quote"
    initial_status: ClassVar[str] = "filled_or_rejected"

    product_id: str
    side: Side
    quote_size: Decimal

    def __post_init__(self) -> None:
        _require_positive("quote_size", self.quote_size)


@dataclass(frozen=True)
class MarketIOCByBase:
    """Market order sized in base currency (sell N BTC). Used for exits."""

    kind: ClassVar[str] = "market_ioc_base"
    initial_status: ClassVar[str] = "filled_or_rejected"

    product_id: str
    side: Side
    base_size: Decimal

    def __post_init__(self) -> None:
        _require_positive("base_size", self.base_size)


@dataclass(frozen=True)
class LimitGTC:
    """Resting limit order, good until cancelled. Used for take-profit legs."""

    kind: ClassVar[str] = "limit_gtc"
    initial_status: ClassVar[str] = "open"

    product_id: str
    side: Side
    base_size: Decimal
    limit_price: Decimal

    def __post_init__(self) -> None:
        _require_positive("base_size", self.base_size)
        _require_positive("limit_price", self.limit_price)


@dataclass(frozen=True)
class StopLimitGTC:
    """Stop-limit, good until cancelled. Used for protective stop legs."""

    kind: ClassVar[str] = "stop_limit_gtc"
    initial_status: ClassVar[str] = "open"

    product_id: str
    side: Side
    base_size: Decimal
    stop_price: Decimal
    limit_price: Decimal

    def __post_init__(self) -> None:
        _require_positive("base_size", self.base_size)
        _require_positive("stop_price", self.stop_price)
        _require_positive("limit_price", self.limit_price)


@dataclass(frozen=True)
class BracketGTC:
    """Native exit bracket: ONE order carrying both protective prices, good until cancelled.

    The VENUE owns the race between the stop and the target, and that is the whole reason the
    kind exists. The alternative keel shipped first was two independent SELL legs paired
    client-side: a fill we failed to observe left the sibling live and able to sell an
    already-closed position, and because both legs were sized at the full quantity a 1x position
    was committed 2x. Neither failure mode exists when there is only one order and no sibling to
    cancel.

    ⚠️ This is an **exit** that closes an EXISTING position -- not an entry-plus-exits parent
    order, which several venues also call a bracket. keel enters with a market IOC and protects
    the position afterwards, so a kind that could carry an entry price would describe a shape no
    keel path produces. Making it expressible would only give a future caller a way to ask for
    something the engine has no code to mean.

    There is deliberately **no `stop_direction` field**. The direction is a function of `side`
    and is derived at translation time, exactly as `StopLimitGTC`'s is: a SELL bracket protects a
    long, so its stop triggers on the way down. A field would make a SELL bracket that triggers
    UPWARD representable -- nonsense the venue would refuse, or worse, honour -- and refusing to
    represent nonsense is what this sum type is for.

    The price names are **keel's, not Coinbase's**. Coinbase spells the take-profit `limit_price`;
    adopting that here would make a second venue's translation start from Coinbase's vocabulary
    rather than the port's, and would put a field named `limit_price` on two different order kinds
    where it means two different things (`LimitGTC.limit_price` is the price of the whole order;
    this one is the profitable half of a pair). `take_profit_price` says which exit it is.
    """

    kind: ClassVar[str] = "bracket_gtc"
    initial_status: ClassVar[str] = "open"

    product_id: str
    side: Side
    base_size: Decimal
    take_profit_price: Decimal
    stop_trigger_price: Decimal

    def __post_init__(self) -> None:
        _require_positive("base_size", self.base_size)
        _require_positive("take_profit_price", self.take_profit_price)
        _require_positive("stop_trigger_price", self.stop_trigger_price)
        # An inverted OR EQUAL pair is not a bracket. Equal is the subtler half and the reason
        # this is `>=` rather than `>`: two equal prices read as a perfectly ordinary pair of
        # numbers, and what they describe is a stop and a target racing at the same price, where
        # whichever side the venue happens to evaluate first decides whether the position took a
        # profit or a loss. That is a coin flip wearing a protective order's name. Both halves
        # are refused here, at construction, where the caller's own numbers are still in scope --
        # not at the venue, where the position is already open and unprotected.
        if self.side is Side.SELL and self.stop_trigger_price >= self.take_profit_price:
            raise ValueError(
                f"a SELL bracket exits a long: stop_trigger_price ({self.stop_trigger_price}) "
                f"must be below take_profit_price ({self.take_profit_price})"
            )
        if self.side is Side.BUY and self.stop_trigger_price <= self.take_profit_price:
            raise ValueError(
                f"a BUY bracket exits a short: stop_trigger_price ({self.stop_trigger_price}) "
                f"must be above take_profit_price ({self.take_profit_price})"
            )


OrderSpec = MarketIOCByQuote | MarketIOCByBase | LimitGTC | StopLimitGTC | BracketGTC

ORDER_KINDS: frozenset[str] = frozenset(
    {
        MarketIOCByQuote.kind,
        MarketIOCByBase.kind,
        LimitGTC.kind,
        StopLimitGTC.kind,
        BracketGTC.kind,
    }
)

__all__ = [
    "ORDER_KINDS",
    "BracketGTC",
    "LimitGTC",
    "MarketIOCByBase",
    "MarketIOCByQuote",
    "OrderSpec",
    "StopLimitGTC",
]
