"""What a venue can do, declared by its adapter and checked before the engine sizes an order."""

from __future__ import annotations

from dataclasses import dataclass

from keel_broker_api.orders import ORDER_KINDS


@dataclass(frozen=True)
class BrokerCapabilities:
    """An adapter's self-declaration. The conformance suite verifies it does not lie."""

    venue: str
    supported_orders: frozenset[str]
    supports_native_preview: bool
    synthesizes_preview: bool
    supports_fee_summary: bool
    quote_currencies: frozenset[str]
    asset_classes: frozenset[str]

    def __post_init__(self) -> None:
        unknown = self.supported_orders - ORDER_KINDS
        if unknown:
            raise ValueError(f"unknown order kinds: {sorted(unknown)}")

    @property
    def can_preview(self) -> bool:
        """Whether `confirm` mode is usable against this venue at all."""
        return self.supports_native_preview or self.synthesizes_preview


__all__ = ["BrokerCapabilities"]
