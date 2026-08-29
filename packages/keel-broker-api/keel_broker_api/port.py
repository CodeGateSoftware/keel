"""The `Broker` port. Every venue adapter implements exactly this."""

from __future__ import annotations

import uuid
from typing import Protocol

from keel_core.types import Candle, Granularity

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


class UnsupportedOrder(Exception):
    """Raised when an adapter is handed an `OrderSpec` kind it does not support.

    This is the backstop at the last gate before money moves: capability gating happens earlier,
    at rule evaluation, but an adapter must still refuse rather than substitute a different order
    type. Never catch this and retry with a different spec.
    """


class TradeScopeDenied(Exception):
    """Raised when the VENUE refuses a placement because THIS CREDENTIAL may not trade.

    The one channel the venue has for writing its half of #233's trade-scope record. The record
    has two writers -- the operator attests (`keel scope attest`), and the venue falsifies -- and
    this is the second one. `executor._run_order` catches exactly this type, writes `REFUTED` plus
    the message below into `venue_trade_scopes`, and re-raises; rail 20 then vetoes live ENTRIES
    on that venue until a human re-attests. Nothing else in the codebase writes `REFUTED`.

    **Not a subclass of `UnsupportedOrder`, and not a superclass of it.** The two answer different
    questions. `UnsupportedOrder` is a fact about the ADAPTER -- it cannot express this spec --
    and the fix is a different spec. This is a fact about the CREDENTIAL, and no spec fixes it.

    **Raise it ONLY for a permission refusal, and construct it with the venue's own words.** The
    message is written verbatim into `refuted_reason` and read back by `doctor` and
    `keel scope show`; the 2026-08-19 incident's entire cost was that the venue's answer
    (`"You do not have permission to perform this action."`) was thrown away, so a paraphrase here
    would rebuild the same blindness one layer up.

    ⚠️ **A 5xx, a timeout, a DNS failure or an unparseable body must NEVER raise this.** They are
    not evidence about the credential -- they are evidence about the network -- and this exception
    is a latch that halts live entries until a human clears it. Classifying a transient outage as
    a refusal would take a healthy deployment off the market and require a terminal to put it
    back. When in doubt, raise the plain error: the cost of failing to record a real refusal is
    one more refused order, and the cost of recording a fake one is an outage.
    """


#: The namespace every derived `client_order_id` is minted under.
#:
#: A fixed, arbitrary UUID -- it exists only so that `uuid5` has a stable seed, and so that two
#: DIFFERENT deployments deriving from the same caller key land on the same venue id. It must
#: never change: changing it would silently make every previously-derived id unreachable, which
#: is precisely the deduplication this whole mechanism exists to provide.
CLIENT_ORDER_ID_NAMESPACE = uuid.UUID("6f1b6d4e-5a2c-4f8b-9c3d-1e0a7b5c9d42")


def resolve_client_order_id(idempotency_key: str | None) -> str:
    """The venue-facing `client_order_id` for one `place_order` attempt.

    **`None` mints a fresh uuid4 -- one id per ATTEMPT.** That is the historical behaviour of
    every adapter here and stays the default, because it is the right answer to the opposite
    hazard: an id derived from the ORDER would silently collapse two orders a strategy genuinely
    meant to place twice into one, and a position half the intended size is as wrong as a
    position twice it.

    **A key derives a uuid5 -- one id per INTENT.** Two attempts carrying the same key produce
    the same id, so a venue that deduplicates on `client_order_id` sees one order however many
    times the caller retried. This is what makes a placement retry safe, and until a caller
    passes a key, retrying placement is not.

    **uuid5, not the key verbatim, and that is not cosmetic.** Robinhood, Coinbase and Alpaca do
    not agree on what a client order id may look like -- Robinhood's is a UUID, Alpaca's is a
    string of up to 128 characters. Hashing every key into a UUID lands on the intersection of
    all three, so a caller can use whatever natural key it has (a cycle id, a position id and a
    leg name) without knowing which venue it will be routed to, and the same key always yields
    the same id on every venue. There is no branch here and no venue-specific rule, which is the
    point: the caller must not have to know.

    Deterministic by construction, so it is safe across processes and restarts. A retry issued
    by a NEW process -- the case a crash produces, and the one an in-memory record of "ids I have
    already sent" cannot cover -- derives the same id as the attempt that crashed.
    """
    if idempotency_key is None:
        return str(uuid.uuid4())
    return str(uuid.uuid5(CLIENT_ORDER_ID_NAMESPACE, idempotency_key))


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

    def get_instrument(self, product_id: str) -> Instrument | None:
        """One product's venue-imposed granularity, or `None` if this venue does not list it.

        **`None` is an answer, not a failure, and that is why this differs from `get_order`.** An
        order id is one the caller was handed by this venue, so its absence is a genuine
        inconsistency worth raising on. A product id arrives from an operator's config allowlist
        and may simply not be listed here -- a perfectly ordinary fact about a venue, and the one
        a multi-venue deployment most needs to be able to ask without catching an exception.

        Raise for anything that is a real failure: an unreachable venue, a refused credential, a
        response that cannot be parsed. `executor._base_increment_for` treats every one of those
        the same way it treats `None` -- unknown, send the quantity unquantized, never refuse the
        exit -- but that is the CALLER's policy about its own path, not a licence for an adapter
        to swallow errors on its behalf.
        """
        ...

    def preview_order(self, spec: OrderSpec) -> Preview: ...

    def place_order(self, spec: OrderSpec, *, idempotency_key: str | None = None) -> PlaceResult:
        """Place one order.

        `idempotency_key` identifies the INTENT, not the order. Omit it and the adapter mints a
        fresh venue id per attempt, which is what every adapter did before this parameter existed
        and remains the default: two identical orders a strategy genuinely meant to place are two
        orders. Pass one and every attempt carrying that key resolves to the same venue-facing
        `client_order_id`, so a retry after a timeout -- exactly when the first request may
        already have reached the venue -- is deduplicated by the venue instead of becoming a
        second live order.

        **Without a key, placement must not be retried.** That is the standing hazard this
        parameter exists to remove, and it is a property of the CALLER, not of any adapter: a
        timeout or a 5xx is not evidence the order was refused, and a blind retry under a fresh
        id has no recovery. Adapters resolve the key through
        `keel_broker_api.port.resolve_client_order_id`, which is where the derivation and its
        reasoning live; an adapter must not invent its own rule, or the same key would mean
        different orders at different venues.
        """
        ...

    def get_fee_summary(self) -> FeeSummary: ...

    def get_order(self, order_id: str) -> OrderStatus: ...

    def cancel_order(self, order_id: str) -> CancelOutcome:
        """Cancel one resting order and report WHAT THE VENUE SAID about THIS order id.

        Return `CancelOutcome.CONFIRMED` only when the venue states this order is
        terminal-cancelled -- absence of a refusal is still not a confirmation. A caller
        (`executor._cancel_at_exchange`) acts on the strength of this, and the action it takes
        next is placing another order against the same inventory, so a `CONFIRMED` the venue
        never gave would commit that inventory twice.

        The other three members exist because venues say more than two things, and collapsing
        them lost a real distinction. Robinhood answers `200` with the order still `open`: the
        request is accepted and settles about a second later. That is `ACCEPTED`, and reporting
        it as a failure (which a `bool` had no choice but to do) told operators a successful
        cancel had left a position at risk, and made an exit wait a whole cycle for a cancel that
        had already landed. `REFUSED` is the venue declining -- already filled, unknown id --
        and `UNKNOWN` is a 5xx, a timeout, or an answer with no row for this id. See
        `CancelOutcome` for the full mapping.

        **Do not raise on the exit path.** This is called while unwinding a position; an
        exception escaping here can abort the unwind partway and leave both the position and the
        orders it was clearing live. A transport failure is `UNKNOWN`, which claims nothing.

        A `bool` is still accepted for compatibility with adapters written against the older
        contract -- `coerce_cancel_outcome` maps `True` to `CONFIRMED` and `False` to `REFUSED`,
        which fails closed exactly as before.
        """
        ...


__all__ = [
    "CLIENT_ORDER_ID_NAMESPACE",
    "Broker",
    "CancelOutcome",
    "TradeScopeDenied",
    "UnsupportedOrder",
    "default_market_schedule",
    "resolve_client_order_id",
]
