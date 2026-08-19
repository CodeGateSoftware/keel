"""The `Broker` port. Every venue adapter implements exactly this."""

from __future__ import annotations

from typing import Protocol

from keel_core.types import Candle, Granularity

from keel_broker_api.capabilities import BrokerCapabilities
from keel_broker_api.orders import OrderSpec
from keel_broker_api.results import (
    Balance,
    FeeSummary,
    MarketSchedule,
    OrderStatus,
    PlaceResult,
    Preview,
    SessionState,
)


class UnsupportedOrder(Exception):
    """Raised when an adapter is handed an `OrderSpec` kind it does not support.

    This is the backstop at the last gate before money moves: capability gating happens earlier,
    at rule evaluation, but an adapter must still refuse rather than substitute a different order
    type. Never catch this and retry with a different spec.
    """


def default_market_schedule(broker: Broker) -> MarketSchedule:
    """The port's DEFAULT `market_schedule()` implementation, derived from `market_clock()`
    (issue #388 C2): the venue's session state crosses unchanged and NO next open/close is
    claimed.

    That derivation is the whole implementation for a 24/7 adapter -- its clock answers the
    constant `OPEN`, so the default is OPEN with null timestamps and never touches the
    network. For a session-bound adapter that has not overridden the schedule read it is the
    honest fallback: the state it CAN answer, with no schedule it cannot vouch for. A
    protocol cannot carry a method body to structural implementors, so the default ships
    here and the 24/7 adapters call it verbatim -- one derivation, not four copies of it.
    """
    return MarketSchedule(state=broker.market_clock())


class Broker(Protocol):
    def capabilities(self) -> BrokerCapabilities: ...

    def market_clock(self) -> SessionState:
        """The venue's session state, read from the VENUE's own clock -- never a locally
        maintained calendar that drifts (FR-9).

        The two postures, exactly as `BrokerCapabilities.session_bound` declares them:

        * A session-bound adapter reads the venue's clock/calendar endpoint each call.
          A clock it cannot read answers `SessionState.CLOCK_UNAVAILABLE` -- it must not
          raise (a clock outage must not crash the caller's cycle) and must not guess `OPEN`
          (trading on an unknown session state is the failure fail-closed exists to prevent).
          The caller decides what CLOSED/CLOCK_UNAVAILABLE mean for it; this method's one
          promise is that the answer is the venue's, or an honest "could not read".
        * A venue that is not session-bound (24/7, crypto) answers `SessionState.OPEN`
          as a constant, with NO network call -- there is no clock to consult, and inventing
          one would spend a request to learn nothing.
        """
        ...

    def market_schedule(self) -> MarketSchedule:
        """The venue's clock WITH its schedule: `market_clock()`'s state plus the next
        open/close timestamps where the venue provides them (issue #388 C2).

        The DEFAULT implementation is `default_market_schedule(broker)` -- the broker's own
        `market_clock()` answer with NO timestamps claimed. Every 24/7 adapter ships exactly
        that (its clock is the constant `OPEN`, so the derivation is OPEN with nulls and
        costs no request); a session-bound adapter OVERRIDES it to carry the venue's own
        next open/close when its clock endpoint states them (Alpaca's `/v2/clock` does).

        The state half keeps `market_clock()`'s contract unchanged: never raises, never
        guesses open. The schedule half claims nothing it was not told -- absent or
        unusable timestamps are `None`, never synthesized.
        """
        ...

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


__all__ = ["Broker", "UnsupportedOrder", "default_market_schedule"]
