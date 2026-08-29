"""Each test here pins one deliberate divergence from Coinbase.

If one of these starts failing because the fake was "fixed" to match Coinbase, the fake has
stopped doing its job -- it exists to make Coinbase-shaped assumptions in the port fail loudly.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from keel_broker_api.orders import LimitGTC, MarketIOCByBase, MarketIOCByQuote, StopLimitGTC
from keel_broker_api.port import TradeScopeDenied, UnsupportedOrder
from keel_broker_api.results import Balance, CancelOutcome, OrderStatus, PlaceResult, SessionState
from keel_broker_fake import FakeAdapter
from keel_broker_fake.adapter import MAX_CANDLES_PER_CALL
from keel_core.types import Granularity, Side


def test_not_session_bound_and_the_clock_answers_open_as_a_constant() -> None:
    """The fake venue has no clock endpoint at all: `session_bound=False` and the port's
    clock answers OPEN without a network call -- the 24/7 half of FR-9's split, held here so
    the conformance suite's session tests have a no-network venue to run against."""
    adapter = FakeAdapter()
    assert adapter.capabilities().session_bound is False
    assert adapter.market_clock() is SessionState.OPEN


def test_market_schedule_is_the_port_default_open_with_no_times() -> None:
    """Issue #388 C2: the 24/7 venues ship the port's DEFAULT schedule read -- the clock's
    OPEN answer, no next_open/next_close claimed. There is no clock endpoint to consult, so
    claiming timestamps would be inventing a calendar."""
    from keel_broker_api.port import default_market_schedule
    from keel_broker_api.results import MarketSchedule

    adapter = FakeAdapter()
    assert adapter.market_schedule() == MarketSchedule(state=SessionState.OPEN)
    assert adapter.market_schedule() == default_market_schedule(adapter)


def test_market_ioc_quote_is_unsupported() -> None:
    """Not every venue can size a market order in quote currency."""
    adapter = FakeAdapter()
    assert "market_ioc_quote" not in adapter.capabilities().supported_orders
    spec = MarketIOCByQuote(product_id="BTC-USD", side=Side.BUY, quote_size=Decimal("100"))
    with pytest.raises(UnsupportedOrder, match="market_ioc_quote"):
        adapter.place_order(spec)


def test_preview_is_unavailable_and_capabilities_say_so() -> None:
    adapter = FakeAdapter()
    assert not adapter.capabilities().can_preview
    spec = MarketIOCByBase(product_id="BTC-USD", side=Side.SELL, base_size=Decimal("1"))
    with pytest.raises(NotImplementedError):
        adapter.preview_order(spec)


def test_fee_summary_is_unavailable_and_capabilities_say_so() -> None:
    """Lapse detection must degrade to attestation alone for a venue like this."""
    adapter = FakeAdapter()
    assert adapter.capabilities().supports_fee_summary is False
    with pytest.raises(NotImplementedError):
        adapter.get_fee_summary()


def test_unsupported_granularity_raises_rather_than_returning_empty() -> None:
    """Empty would read as "no trades happened", which is a different claim entirely."""
    adapter = FakeAdapter()
    with pytest.raises(ValueError, match="ONE_DAY, ONE_HOUR"):
        adapter.get_candles("BTC-USD", Granularity.FIVE_MINUTE, 0, 86_400)


def test_supported_granularities_work() -> None:
    adapter = FakeAdapter()
    assert adapter.get_candles("BTC-USD", Granularity.ONE_HOUR, 0, 7_200)
    assert adapter.get_candles("BTC-USD", Granularity.ONE_DAY, 0, 172_800)


def test_candles_are_capped_at_this_venues_page_size() -> None:
    """Coinbase's page size is not universal; a hardcoded 300 would break here."""
    adapter = FakeAdapter()
    candles = adapter.get_candles("BTC-USD", Granularity.ONE_HOUR, 0, 3600 * 500)
    assert len(candles) == MAX_CANDLES_PER_CALL
    assert [c.ts for c in candles] == sorted(c.ts for c in candles)


def test_stop_limit_is_two_internal_objects_but_one_place_result() -> None:
    """The port must not leak that this venue models a stop as order + trigger."""
    adapter = FakeAdapter()
    spec = StopLimitGTC(
        product_id="BTC-USD",
        side=Side.SELL,
        base_size=Decimal("1"),
        stop_price=Decimal("60000"),
        limit_price=Decimal("59900"),
    )
    result = adapter.place_order(spec)

    assert isinstance(result, PlaceResult)
    assert result.success is True
    assert len(adapter.resting) == 1
    assert len(adapter.triggers) == 1
    assert adapter.triggers[0].order_id == result.broker_order_id


def test_a_plain_limit_order_creates_no_trigger() -> None:
    adapter = FakeAdapter()
    spec = LimitGTC(
        product_id="BTC-USD", side=Side.SELL, base_size=Decimal("1"), limit_price=Decimal("70000")
    )
    adapter.place_order(spec)
    assert adapter.triggers == []


def test_get_balances_returns_domain_types() -> None:
    assert all(isinstance(b, Balance) for b in FakeAdapter().get_balances())


# --- order status + cancellation -----------------------------------------------------------


def test_get_order_reports_a_resting_order_as_open_with_zero_economics() -> None:
    """This venue fills nothing on its own, so a resting order's money fields are always zero,
    never modelled."""
    adapter = FakeAdapter()
    spec = LimitGTC(
        product_id="BTC-USD", side=Side.SELL, base_size=Decimal("1"), limit_price=Decimal("70000")
    )
    placed = adapter.place_order(spec)
    assert placed.broker_order_id is not None

    order = adapter.get_order(placed.broker_order_id)

    assert isinstance(order, OrderStatus)
    assert order.status == "OPEN"
    assert order.filled_size == Decimal("0")
    assert order.average_filled_price == Decimal("0")
    assert order.total_fees == Decimal("0")


def test_get_order_on_an_unknown_id_reports_failed_not_a_raise() -> None:
    """Callers do arithmetic on `OrderStatus`'s money fields without special-casing `None` --
    special-casing "does this id exist" one layer up would just move the same problem."""
    order = FakeAdapter().get_order("never-placed")

    assert isinstance(order, OrderStatus)
    assert order.status == "FAILED"
    assert order.filled_size == Decimal("0")
    assert order.average_filled_price == Decimal("0")
    assert order.total_fees == Decimal("0")


def test_cancel_order_removes_a_resting_order_and_confirms() -> None:
    adapter = FakeAdapter()
    spec = LimitGTC(
        product_id="BTC-USD", side=Side.SELL, base_size=Decimal("1"), limit_price=Decimal("70000")
    )
    placed = adapter.place_order(spec)
    assert placed.broker_order_id is not None

    assert adapter.cancel_order(placed.broker_order_id) is CancelOutcome.CONFIRMED
    assert adapter.resting == []
    assert adapter.get_order(placed.broker_order_id).status == "FAILED"


def test_cancel_order_on_an_unknown_id_is_refused() -> None:
    assert FakeAdapter().cancel_order("never-placed") is CancelOutcome.REFUSED


def test_the_fake_venue_never_reports_accepted() -> None:
    """This venue is synchronous and in-process: removing the order IS the settlement, so there
    is no window in which a cancel is accepted-but-unsettled. An `ACCEPTED` here would mean the
    fake had stopped modelling a venue faithfully (#412)."""
    adapter = FakeAdapter()
    spec = LimitGTC(
        product_id="BTC-USD", side=Side.SELL, base_size=Decimal("1"), limit_price=Decimal("70000")
    )
    placed = adapter.place_order(spec)
    assert placed.broker_order_id is not None
    assert adapter.cancel_order(placed.broker_order_id) is not CancelOutcome.ACCEPTED
    assert adapter.cancel_order("never-placed") is not CancelOutcome.ACCEPTED


def test_cancel_order_drops_the_stops_trigger_too() -> None:
    """A stop is two objects at this venue. Cancelling the order without dropping its trigger
    would leave the trigger free to fire later and place a brand new order for a cancellation
    the caller believes already happened -- this venue's version of the naked-position bug."""
    adapter = FakeAdapter()
    spec = StopLimitGTC(
        product_id="BTC-USD",
        side=Side.SELL,
        base_size=Decimal("1"),
        stop_price=Decimal("60000"),
        limit_price=Decimal("59900"),
    )
    placed = adapter.place_order(spec)
    assert placed.broker_order_id is not None
    assert len(adapter.triggers) == 1

    assert adapter.cancel_order(placed.broker_order_id) is CancelOutcome.CONFIRMED

    assert adapter.triggers == []


# --- #233: the trade-scope refusal knob -----------------------------------------------------


def test_by_default_the_fake_venue_places_and_touches_no_scope() -> None:
    """The knob is OFF unless asked for. Every existing caller -- the conformance run, the
    paper-hourly profile, the dev venue -- constructs `FakeAdapter()` with no arguments, and a
    default that refused would turn this fake from a stand-in into a fault injector."""
    adapter = FakeAdapter()

    assert adapter.place_order(
        MarketIOCByBase(product_id="BTC-USD", side=Side.BUY, base_size=Decimal("0.1"))
    ).success


def test_the_knob_raises_the_venues_words_verbatim() -> None:
    """The one venue in this repo that can be MADE to refuse. Every real adapter needs a live
    credential without trade scope to exercise its refusal path, and the executor's confirm/refute
    write needs something it can drive end to end without one."""
    adapter = FakeAdapter(
        trade_scope_denied="You do not have permission to perform this action."
    )

    with pytest.raises(TradeScopeDenied) as caught:
        adapter.place_order(
            MarketIOCByBase(product_id="BTC-USD", side=Side.BUY, base_size=Decimal("0.1"))
        )

    assert str(caught.value) == "You do not have permission to perform this action."


def test_the_refusal_leaves_no_resting_order_behind() -> None:
    """A refused placement is not a placement. If the fake appended to `resting` and then raised,
    it would model a venue that accepted an order it says it refused -- and the executor's
    refute path would be tested against a state no venue produces."""
    adapter = FakeAdapter(trade_scope_denied="nope")

    with pytest.raises(TradeScopeDenied):
        adapter.place_order(
            MarketIOCByBase(product_id="BTC-USD", side=Side.BUY, base_size=Decimal("0.1"))
        )

    assert adapter.resting == []
    assert adapter.triggers == []


def test_an_unsupported_kind_is_still_refused_as_unsupported_first() -> None:
    """Order of refusals. A spec this venue cannot express is `UnsupportedOrder` whatever the
    knob says -- the adapter never reached the point of asking the venue for permission, so
    reporting a scope refusal would write `REFUTED` over a bug in the caller's spec."""
    adapter = FakeAdapter(trade_scope_denied="nope")

    with pytest.raises(UnsupportedOrder):
        adapter.place_order(
            MarketIOCByQuote(product_id="BTC-USD", side=Side.BUY, quote_size=Decimal("100"))
        )
