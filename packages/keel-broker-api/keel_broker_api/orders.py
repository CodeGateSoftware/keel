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


OrderSpec = MarketIOCByQuote | MarketIOCByBase | LimitGTC | StopLimitGTC

ORDER_KINDS: frozenset[str] = frozenset(
    {MarketIOCByQuote.kind, MarketIOCByBase.kind, LimitGTC.kind, StopLimitGTC.kind}
)

__all__ = [
    "ORDER_KINDS",
    "LimitGTC",
    "MarketIOCByBase",
    "MarketIOCByQuote",
    "OrderSpec",
    "StopLimitGTC",
]
