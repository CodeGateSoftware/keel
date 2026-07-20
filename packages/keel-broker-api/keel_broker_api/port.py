"""The `Broker` port. Every venue adapter implements exactly this."""

from __future__ import annotations

from typing import Protocol

from keel_core.types import Candle, Granularity

from keel_broker_api.capabilities import BrokerCapabilities
from keel_broker_api.orders import OrderSpec
from keel_broker_api.results import Balance, FeeSummary, OrderStatus, PlaceResult, Preview


class UnsupportedOrder(Exception):
    """Raised when an adapter is handed an `OrderSpec` kind it does not support.

    This is the backstop at the last gate before money moves: capability gating happens earlier,
    at rule evaluation, but an adapter must still refuse rather than substitute a different order
    type. Never catch this and retry with a different spec.
    """


class Broker(Protocol):
    def capabilities(self) -> BrokerCapabilities: ...

    def get_candles(
        self, product_id: str, granularity: Granularity, start_ts: int, end_ts: int
    ) -> list[Candle]: ...

    def get_balances(self) -> list[Balance]: ...

    def preview_order(self, spec: OrderSpec) -> Preview: ...

    def place_order(self, spec: OrderSpec) -> PlaceResult: ...

    def get_fee_summary(self) -> FeeSummary: ...

    def get_order(self, order_id: str) -> OrderStatus: ...

    def cancel_order(self, order_id: str) -> bool:
        """Cancel one resting order. Return `True` ONLY when the venue CONFIRMS the
        cancellation for THIS order id -- absence of a refusal is not a confirmation. A caller
        (`executor._cancel_at_exchange`) records local state on the strength of this boolean, so
        a `True` that the venue never actually confirmed would record a cancel that never
        happened.
        """
        ...


__all__ = ["Broker", "UnsupportedOrder"]
