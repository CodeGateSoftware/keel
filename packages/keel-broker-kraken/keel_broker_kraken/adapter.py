"""The Kraken adapter: a port-complete STUB, honest about implementing nothing (#313).

This package exists so the Kraken venue can grow behind the `Broker` port without a
big-bang landing: the method surface is agreed (it matches `keel_broker_api.port.Broker`
member for member), and every data/market method raises `NotImplementedError` with one
message. That is deliberate, and it is the honest shape for a first commit -- a stub that
returned empty lists or `SessionState.OPEN` would *look* like a working adapter while
silently answering every question with nothing.

⚠️ **Including the never-raise methods.** The port's contract says `market_clock()` must
not raise (a clock outage is `CLOCK_UNAVAILABLE`, not an exception) and `cancel_order()`
must not raise on the exit path. A stub breaks those contracts on purpose: raising here is
loud at the first call, in every caller, and can never be mistaken for a venue's answer.
When the real implementation lands, each method keeps the port's full contract; until then
there is nothing to keep.

**No key handling, no network calls.** Nothing here constructs a client, reads a
credential, or opens a socket -- installing this package cannot reach Kraken. The
constructor takes no arguments at all, so there is no credential-shaped surface to misuse.

The one thing the stub CAN answer is `capabilities()`, and it answers as close to nothing
as the declaration allows: no order kinds, no preview, no fee summary, no quote
currencies -- nothing is declared that nothing verifies. The two claims it does make are
about the VENUE, not the adapter: Kraken is a crypto spot venue (`asset_classes={"spot"}`)
that trades 24/7 (`session_bound=False`).
"""

from __future__ import annotations

from keel_broker_api.capabilities import BrokerCapabilities
from keel_broker_api.orders import OrderSpec
from keel_broker_api.results import (
    Balance,
    CancelOutcome,
    FeeSummary,
    Instrument,
    MarketSchedule,
    OrderStatus,
    PlaceResult,
    Preview,
    SessionState,
)
from keel_core.types import Candle, Granularity

#: One message for every unimplemented method, so the first call names the issue and its
#: tracking number wherever it surfaces.
_STUB_MESSAGE = "kraken adapter is a stub — #313"

_CAPABILITIES = BrokerCapabilities(
    venue="kraken",
    # Empty for the stub reason above, but the venue fact is worth recording before anyone
    # fills this in: Kraken's `AddOrder` has no two-sided bracket. `close[ordertype]` attaches
    # ONE conditional close (a stop-loss or a take-profit, not both) that is triggered by the
    # primary order's execution and is an independent order thereafter -- an OTO, not an OCO.
    # So `bracket_gtc` will still not be declarable here once the rest of the stub is written.
    supported_orders=frozenset(),
    supports_native_preview=False,
    synthesizes_preview=False,
    supports_fee_summary=False,
    quote_currencies=frozenset(),
    asset_classes=frozenset({"spot"}),
    session_bound=False,
    # #372: spot-only surface; Kraken's extend-volume/margin endpoints are not spoken.
    cash_only=True,
)


class KrakenAdapter:
    """Implements the `Broker` port's SURFACE only; every data/market method raises
    `NotImplementedError` (see the module docstring for why raising is the honest stub)."""

    def capabilities(self) -> BrokerCapabilities:
        """The stub's one answerable question, answered conservatively.

        Declares no order kinds, no preview, no fee summary and no quote currencies: an
        empty declaration is the floor the conformance suite can only widen, while a
        generous one would claim behaviour nothing verifies. The venue-level facts stand
        (crypto spot, 24/7), because they are about Kraken rather than about this code.
        """
        return _CAPABILITIES

    def market_clock(self) -> SessionState:
        """Not implemented -- and deliberately raising, against the port's never-raise
        contract for this method: a stub's one job is to be loud at first call."""
        raise NotImplementedError(_STUB_MESSAGE)

    def market_schedule(self) -> MarketSchedule:
        """Not implemented; see `market_clock` for why this raises rather than answering."""
        raise NotImplementedError(_STUB_MESSAGE)

    def get_candles(
        self, product_id: str, granularity: Granularity, start_ts: int, end_ts: int
    ) -> list[Candle]:
        """Not implemented: the stub fetches no market data and opens no connection."""
        raise NotImplementedError(_STUB_MESSAGE)

    def get_balances(self) -> list[Balance]:
        """Not implemented: the stub reads no account and holds no credentials."""
        raise NotImplementedError(_STUB_MESSAGE)

    def get_instrument(self, product_id: str) -> Instrument | None:
        """Not implemented: the stub reads no catalogue."""
        raise NotImplementedError(_STUB_MESSAGE)

    def preview_order(self, spec: OrderSpec) -> Preview:
        """Not implemented: the stub previews nothing (`can_preview` is False in
        `capabilities()`, so callers are told before they try)."""
        raise NotImplementedError(_STUB_MESSAGE)

    def place_order(self, spec: OrderSpec, *, idempotency_key: str | None = None) -> PlaceResult:
        """Not implemented: the stub places no orders, live or otherwise."""
        raise NotImplementedError(_STUB_MESSAGE)

    def get_fee_summary(self) -> FeeSummary:
        """Not implemented: the stub reads no fee tiers."""
        raise NotImplementedError(_STUB_MESSAGE)

    def get_order(self, order_id: str) -> OrderStatus:
        """Not implemented: the stub tracks no orders."""
        raise NotImplementedError(_STUB_MESSAGE)

    def cancel_order(self, order_id: str) -> CancelOutcome:
        """Not implemented -- and deliberately raising, against the port's exit-path
        never-raise contract: there are no orders to strand until something can place
        them, and a silent `UNKNOWN` here would read as a live answer."""
        raise NotImplementedError(_STUB_MESSAGE)


__all__ = ["KrakenAdapter"]
