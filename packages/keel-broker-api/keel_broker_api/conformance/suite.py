"""The executable contract every `Broker` adapter must satisfy.

Subclass `BrokerConformanceTests` in your adapter's test module and implement `broker()`::

    class TestMyVenueConformance(BrokerConformanceTests):
        def broker(self) -> MyVenueAdapter:
            return MyVenueAdapter(transport=CannedTransport())

The suite ships from `keel-broker-api` rather than living in this repository's tests so that a
third-party adapter can prove itself against the same contract the first-party ones are held to.
Install the `conformance` extra to get pytest alongside the port.

**The suite calls `place_order`.** The adapter it is handed must therefore be constructed in a
sandbox or in-memory mode -- never against live credentials. The suite has no way to verify that
for you; it is the implementor's responsibility, and getting it wrong means placing real orders.

What the contract enforces is narrow on purpose: it checks that an adapter's `capabilities()`
tells the truth, and that no broker-native type leaks through the port. It does not check that a
venue's business logic is correct -- only that it is honestly *described*, because the engine
gates live spend on those declarations.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from keel_core.types import Granularity, Side

from keel_broker_api.capabilities import ASSET_CLASSES, BrokerCapabilities
from keel_broker_api.orders import (
    ORDER_KINDS,
    BracketGTC,
    LimitGTC,
    MarketIOCByBase,
    MarketIOCByQuote,
    OrderSpec,
    StopLimitGTC,
)
from keel_broker_api.port import UnsupportedOrder
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
    coerce_cancel_outcome,
)

_PRODUCT = "BTC-USD"

#: One representative spec per kind, so the suite can exercise a kind an adapter declares
#: without the adapter's test author having to supply examples.
_SPEC_BY_KIND: dict[str, OrderSpec] = {
    "market_ioc_quote": MarketIOCByQuote(
        product_id=_PRODUCT, side=Side.BUY, quote_size=Decimal("100")
    ),
    "market_ioc_base": MarketIOCByBase(
        product_id=_PRODUCT, side=Side.SELL, base_size=Decimal("0.1")
    ),
    "limit_gtc": LimitGTC(
        product_id=_PRODUCT,
        side=Side.SELL,
        base_size=Decimal("0.1"),
        limit_price=Decimal("70000"),
    ),
    "stop_limit_gtc": StopLimitGTC(
        product_id=_PRODUCT,
        side=Side.SELL,
        base_size=Decimal("0.1"),
        stop_price=Decimal("60000"),
        limit_price=Decimal("59900"),
    ),
    # A SELL exit bracket on a long: stop below, target above. `BracketGTC.__post_init__`
    # refuses any other arrangement, so this entry cannot silently rot into a shape no adapter
    # would be right to accept.
    "bracket_gtc": BracketGTC(
        product_id=_PRODUCT,
        side=Side.SELL,
        base_size=Decimal("0.1"),
        take_profit_price=Decimal("70000"),
        stop_trigger_price=Decimal("60000"),
    ),
}


class BrokerConformanceTests:
    """Mixin of contract tests. Subclass it and supply `broker()`."""

    def broker(self) -> Any:
        raise NotImplementedError("conformance subclasses must supply a broker() factory")

    # --- capabilities are structurally sane -----------------------------------------------

    def test_capabilities_returns_the_declaration_type(self) -> None:
        assert isinstance(self.broker().capabilities(), BrokerCapabilities)

    def test_supported_orders_is_a_subset_of_the_known_kinds(self) -> None:
        """An adapter cannot invent an order kind the engine has no way to construct."""
        caps: BrokerCapabilities = self.broker().capabilities()
        assert caps.supported_orders <= ORDER_KINDS

    def test_venue_is_a_non_empty_string(self) -> None:
        assert self.broker().capabilities().venue

    def test_asset_classes_is_non_empty_and_drawn_from_the_known_vocabulary(self) -> None:
        """An adapter that declares nothing declares nothing checkable.

        `BrokerCapabilities.__post_init__` already refuses an unknown class, so this is the
        subset assertion restated where a future adapter author will read it -- plus the
        non-emptiness that a frozenset default would otherwise let through silently.
        """
        caps: BrokerCapabilities = self.broker().capabilities()
        assert caps.asset_classes
        assert caps.asset_classes <= ASSET_CLASSES

    def test_quote_currencies_is_non_empty(self) -> None:
        """Kept alongside the `asset_classes` check above because rail 18 is the other half of
        the same question, and this is the declaration it is checked against.

        `config.DEFAULT_SETTLEMENT_CURRENCIES` is `{"USD", "USDC"}` because that is what
        `keel_broker_coinbase`'s `_CAPABILITIES.quote_currencies` says the venue settles in --
        neither is derived from the other (see the comment on that constant), and an agreement
        between two independent statements is only meaningful while both actually state
        something. An adapter declaring none would be saying it settles in nothing, which cannot
        be true of a venue that accepts orders, and would make that agreement vacuous rather
        than false -- the failure mode nothing else here would catch.
        """
        assert self.broker().capabilities().quote_currencies

    # --- session awareness (FR-9) -----------------------------------------------------------

    def test_session_bound_is_declared(self) -> None:
        """Every adapter states whether its venue is bound to a trading session.

        The field is `bool`-typed and required, so what this really holds in place is the
        declaration itself: an adapter author cannot leave the session question unanswered and
        let the engine's default (24/7, crypto semantics) answer it for them -- which is exactly
        the false positive FR-9 exists to close, a closed equities venue read as a stale feed.
        """
        assert isinstance(self.broker().capabilities().session_bound, bool)

    def test_market_clock_answers_the_port_type(self) -> None:
        """The clock crosses the port as a `SessionState`, never a venue-native shape.

        A session-bound adapter may answer any of the three members here (its open/closed
        fixtures are its own tests' business); what the PORT guarantees is the type, so the
        engine can branch on identity (`is SessionState.OPEN`) without probing venue fields.
        """
        assert isinstance(self.broker().market_clock(), SessionState)

    def test_market_clock_is_open_without_a_call_for_24x7_venues(self) -> None:
        """A venue that declares `session_bound=False` is 24/7: always open, no clock call.

        The suite's `broker()` factories inject canned transports, so a wrongly-chatty
        `market_clock` would not fail loudly here -- that is why each 24/7 adapter's own tests
        assert the same thing with NO transport injected at all. This test still earns its place:
        it pins the CONTRACT (24/7 means `SessionState.OPEN`, not a venue's own truthiness) at
        the one layer every adapter passes through.
        """
        broker = self.broker()
        if broker.capabilities().session_bound:
            pytest.skip("session-bound venue: open/closed answers are the adapter's own tests")
        assert broker.market_clock() is SessionState.OPEN

    def test_market_schedule_answers_the_port_type(self) -> None:
        """The schedule read (issue #388 C2) crosses the port as a `MarketSchedule`, never a
        venue-native shape -- exactly the guarantee `market_clock`'s own type check makes for
        the state, extended to the value object that carries it plus the next open/close.

        A session-bound adapter may answer any of the three states here (and may or may not
        carry timestamps); what the PORT guarantees is the type, so a renderer can branch on
        `schedule.state is SessionState.OPEN` and on `next_open_ts is None` without probing
        venue fields.
        """
        schedule = self.broker().market_schedule()
        assert isinstance(schedule, MarketSchedule)
        assert isinstance(schedule.state, SessionState)
        assert isinstance(schedule.next_open_ts, int | None)
        assert isinstance(schedule.next_close_ts, int | None)

    def test_market_schedule_agrees_with_the_clock_for_every_venue(self) -> None:
        """`market_schedule()` is the SUPERSET read: its state must be the same answer
        `market_clock()` gives, or the two port reads disagree about whether the venue is
        open -- and every caller (the engine's session gate, the console's banner) would be
        free to pick the one it prefers.
        """
        broker = self.broker()
        assert broker.market_schedule().state is broker.market_clock()

    def test_market_schedule_claims_no_times_for_24x7_venues(self) -> None:
        """A venue that declares `session_bound=False` is 24/7: always open, and there is no
        schedule to carry. `next_open`/`next_close` must be `None` -- a 24/7 adapter that
        synthesized timestamps would be inventing a calendar the venue does not have.
        """
        broker = self.broker()
        if broker.capabilities().session_bound:
            pytest.skip("session-bound venue: timestamps are the adapter's own tests")
        schedule = broker.market_schedule()
        assert schedule.state is SessionState.OPEN
        assert schedule.next_open_ts is None
        assert schedule.next_close_ts is None

    # --- capabilities cannot lie about orders ---------------------------------------------

    def test_every_declared_order_kind_is_actually_accepted(self) -> None:
        """The most important test in the suite.

        An adapter that declares a kind it cannot place will fail here rather than at the
        moment money moves.
        """
        broker = self.broker()
        for kind in sorted(broker.capabilities().supported_orders):
            result = broker.place_order(_SPEC_BY_KIND[kind])
            assert isinstance(result, PlaceResult), f"{kind} did not return a PlaceResult"

    def test_every_declared_order_kind_accepts_an_idempotency_key(self) -> None:
        """#409. `place_order`'s `idempotency_key` is part of the port, so an adapter that does
        not accept it is not a `Broker` -- a caller routing the same intent to two venues would
        get a `TypeError` from one of them, at placement, on the live-money path.

        The suite asserts ACCEPTANCE, not deduplication: whether the venue actually collapses two
        attempts under one key is the venue's behaviour, observable only against the venue, and a
        contract suite that runs on canned transports cannot honestly claim to have seen it. What
        it can hold every adapter to is that the parameter exists and changes no result shape.
        """
        broker = self.broker()
        for kind in sorted(broker.capabilities().supported_orders):
            result = broker.place_order(_SPEC_BY_KIND[kind], idempotency_key=f"conformance-{kind}")
            assert isinstance(result, PlaceResult), (
                f"{kind} did not return a PlaceResult when given an idempotency_key"
            )

    def test_the_same_idempotency_key_is_not_rejected_on_a_second_attempt(self) -> None:
        """A retry is the whole point: the second call under one key must reach the adapter the
        same way the first did. An adapter that remembered keys locally and refused the repeat
        would defeat the mechanism -- deduplication belongs to the VENUE, which is the only party
        that knows whether the first attempt actually landed."""
        broker = self.broker()
        caps = broker.capabilities()
        kind = sorted(caps.supported_orders)[0]
        spec = _SPEC_BY_KIND[kind]

        first = broker.place_order(spec, idempotency_key="conformance-retry")
        second = broker.place_order(spec, idempotency_key="conformance-retry")

        assert isinstance(first, PlaceResult)
        assert isinstance(second, PlaceResult)

    def test_every_undeclared_order_kind_is_refused(self) -> None:
        """Refusal must be explicit. Silently substituting a different order type is the
        failure mode this whole port exists to make impossible."""
        broker = self.broker()
        undeclared = ORDER_KINDS - broker.capabilities().supported_orders
        for kind in sorted(undeclared):
            with pytest.raises(UnsupportedOrder):
                broker.place_order(_SPEC_BY_KIND[kind])

    def test_the_bracket_declaration_cannot_lie_in_either_direction(self) -> None:
        """`bracket_gtc` gets its own case because it is the kind whose two answers diverge most.

        The generic pair above (`..._is_actually_accepted` / `..._is_refused`) already sweeps
        every kind an adapter declares and every kind it does not. This restates the contract for
        the bracket specifically, at the one place an adapter author adding a venue will read it,
        because a bracket is the only kind where a venue's *inability* is the common case rather
        than the exception: exactly one of the venues keel targets today has a native single-order
        bracket, and the other three would have to synthesise one out of two legs to say yes.

        Synthesis is what this test forbids. An adapter that declared `bracket_gtc` and quietly
        placed a stop and a target as two independent orders would be committing the position
        twice and re-opening the client-side pairing race the native bracket exists to close --
        and it would look, from the port, exactly like an adapter that did the right thing. So
        the declaration is the whole promise: say yes and the suite makes you place it; say
        nothing and the suite makes you refuse it out loud, with `UnsupportedOrder` rather than a
        substituted order type.
        """
        broker = self.broker()
        spec = _SPEC_BY_KIND["bracket_gtc"]

        if "bracket_gtc" in broker.capabilities().supported_orders:
            assert isinstance(broker.place_order(spec), PlaceResult)
            return

        with pytest.raises(UnsupportedOrder):
            broker.place_order(spec)

    # --- capabilities cannot lie about preview --------------------------------------------

    def test_preview_matches_its_declaration(self) -> None:
        broker = self.broker()
        caps: BrokerCapabilities = broker.capabilities()
        spec = self._any_supported_spec(caps)

        if not caps.can_preview:
            with pytest.raises((NotImplementedError, UnsupportedOrder)):
                broker.preview_order(spec)
            return

        preview = broker.preview_order(spec)
        assert isinstance(preview, Preview)
        if caps.supports_native_preview:
            assert preview.synthetic is False, "a native preview must not claim to be synthetic"
        else:
            assert preview.synthetic is True, "a synthesised preview must declare itself"

    # --- capabilities cannot lie about fee summaries ---------------------------------------

    def test_fee_summary_matches_its_declaration(self) -> None:
        """Subscription lapse detection branches on this capability, and a venue that lies
        about it would leave a lapse undetected while the engine keeps authorising spend."""
        broker = self.broker()
        caps: BrokerCapabilities = broker.capabilities()

        if not caps.supports_fee_summary:
            with pytest.raises(NotImplementedError):
                broker.get_fee_summary()
            return

        summary = broker.get_fee_summary()
        assert isinstance(summary, FeeSummary)
        assert summary.volume_window in {"trailing_30d", "calendar_month", "unknown"}
        assert isinstance(summary.taker_rate, Decimal)
        assert isinstance(summary.maker_rate, Decimal)
        assert isinstance(summary.volume_usd, Decimal)
        assert isinstance(summary.fees_usd, Decimal)

    def test_get_instrument_answers_the_port_type_or_declares_it_is_unwritten(self) -> None:
        """`Instrument | None`, or an explicit `NotImplementedError` -- never a venue dict.

        There is no capability flag gating this one, unlike `get_fee_summary`, and that is
        deliberate: a product catalogue is not an optional venue FEATURE, it is something every
        venue has and some adapters have not been taught to read yet. `NotImplementedError` says
        which of those it is. `None` must not be used for it -- `None` is this method's word for
        "this venue does not list that product", and an adapter answering it for an unwritten
        lookup would tell `executor._base_increment_for` a symbol is unlisted when the truth is
        that nobody wrote the read, which on the live path means silently skipping quantization.

        The value is checked rather than only its type: a zero or negative increment is what a
        caller divides and quantizes against, so it must never cross the port at all.
        """
        broker = self.broker()
        try:
            instrument = broker.get_instrument("BTC-USD")
        except NotImplementedError:
            return

        if instrument is None:
            return
        assert isinstance(instrument, Instrument), (
            f"get_instrument returned {type(instrument).__name__}, not the port's Instrument"
        )
        assert isinstance(instrument.base_increment, Decimal)
        assert instrument.base_increment > 0, "a non-positive increment must never cross the port"
        assert instrument.product_id == "BTC-USD", (
            "the Instrument must describe the product that was asked for"
        )

    def test_get_instrument_may_answer_none_for_a_product_the_venue_does_not_list(self) -> None:
        """An id that no venue lists. `None` or `NotImplementedError` are both correct; an
        exception of any other kind is not, because a product id reaching this method comes from
        an operator's allowlist and being absent is ordinary."""
        broker = self.broker()
        try:
            instrument = broker.get_instrument("NOT-LISTED")
        except NotImplementedError:
            return
        assert instrument is None or isinstance(instrument, Instrument)

    # --- no broker-native type crosses the port --------------------------------------------

    def test_get_balances_returns_only_domain_types(self) -> None:
        balances = self.broker().get_balances()
        assert isinstance(balances, list)
        for balance in balances:
            assert isinstance(balance, Balance), f"{type(balance).__name__} crossed the port"
            assert isinstance(balance.available, Decimal)
            assert isinstance(balance.total, Decimal)

    def test_place_order_returns_a_domain_type(self) -> None:
        broker = self.broker()
        spec = self._any_supported_spec(broker.capabilities())
        assert isinstance(broker.place_order(spec), PlaceResult)

    # --- order status and cancellation ------------------------------------------------------

    def test_get_order_returns_observed_economics(self) -> None:
        """`execution.reconcile` duck-types this today against `cb_client`, a module Phase B
        deletes. It must exist on the PORT or reconciliation breaks the moment Phase B lands.

        The id is round-tripped through `place_order` rather than hardcoded: an order id is a
        venue's own value, and a suite that invents one tests a fixture instead of a contract.
        """
        broker = self.broker()
        placed = broker.place_order(self._any_supported_spec(broker.capabilities()))
        assert placed.broker_order_id is not None

        order = broker.get_order(placed.broker_order_id)
        assert isinstance(order, OrderStatus), f"{type(order).__name__} crossed the port"
        assert order.status in {"FILLED", "OPEN", "CANCELLED", "EXPIRED", "FAILED", "PENDING"}
        assert isinstance(order.filled_size, Decimal)
        assert isinstance(order.average_filled_price, Decimal)
        assert isinstance(order.total_fees, Decimal)

    def test_cancel_order_reports_what_the_venue_said(self) -> None:
        """Coinbase's `batch_cancel` answers per order, so a 200 is not a confirmation. The port
        must surface the venue's answer about THIS order, not the HTTP result --
        `executor._cancel_at_exchange` acts on the strength of it, and what it does next is place
        another order against the same inventory.

        An unknown id must come back non-`CONFIRMED`, not raise: absence of a refusal is not a
        confirmation, and a raise on the exit path can abort an unwind partway.

        Only `CONFIRMED` is asserted positively. A conformant adapter MAY answer `ACCEPTED` for a
        venue that settles cancels asynchronously (Robinhood does), so the suite pins the
        property that matters -- an unknown id never reports as settled -- rather than a member
        that would be wrong for such a venue.
        """
        broker = self.broker()
        placed = broker.place_order(self._any_supported_spec(broker.capabilities()))
        assert placed.broker_order_id is not None

        outcome = coerce_cancel_outcome(broker.cancel_order(placed.broker_order_id))
        assert outcome is CancelOutcome.CONFIRMED

        unknown = coerce_cancel_outcome(broker.cancel_order("an-id-this-venue-never-issued"))
        assert isinstance(unknown, CancelOutcome)
        assert not unknown.settled

    def test_cancel_order_never_raises_on_the_exit_path(self) -> None:
        """`cancel_order` is called while unwinding a position. An exception escaping it can
        abort the unwind partway and leave both the position and the orders it was clearing live
        -- strictly worse than an answer that claims nothing and keeps the engine watching."""
        broker = self.broker()
        outcome = coerce_cancel_outcome(broker.cancel_order("an-id-this-venue-never-issued"))
        assert not outcome.settled

    # --- candles ---------------------------------------------------------------------------

    def test_get_candles_returns_ascending_candles(self) -> None:
        """Out-of-order candles silently corrupt every windowed indicator downstream."""
        broker = self.broker()
        candles = self._any_candles(broker)
        timestamps = [c.ts for c in candles]
        assert timestamps == sorted(timestamps)

    # --- helpers ---------------------------------------------------------------------------

    def _any_supported_spec(self, caps: BrokerCapabilities) -> OrderSpec:
        if not caps.supported_orders:
            pytest.skip("adapter declares no supported order kinds")
        return _SPEC_BY_KIND[sorted(caps.supported_orders)[0]]

    def _any_candles(self, broker: Any) -> list[Any]:
        """Fetch candles at whichever granularity this venue actually serves.

        A venue is entitled to refuse a granularity -- that is itself contract-conformant -- so
        the suite tries each in turn rather than assuming Coinbase's set is universal.
        """
        for granularity in Granularity:
            try:
                return list(broker.get_candles(_PRODUCT, granularity, 0, 86_400 * 3))
            except ValueError:
                continue
        pytest.skip("adapter serves no granularity the suite could exercise")


__all__ = ["BrokerConformanceTests"]
