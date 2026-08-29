"""Tests for `CoinbaseAdapter`, the Coinbase implementation of the `Broker` port.

Follows the fake-transport pattern established in `tests/data/test_cb_client.py`: every test
injects a `FakeTransport` returning canned, real-shaped JSON from `tests/fixtures/cb_*.json`.
No live network calls are made, and no live order is ever placed.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from keel_broker_api.orders import LimitGTC, MarketIOCByBase, MarketIOCByQuote
from keel_broker_api.port import TradeScopeDenied
from keel_broker_api.results import (
    Balance,
    CancelOutcome,
    FeeSummary,
    OrderStatus,
    PlaceResult,
    Preview,
    SessionState,
)
from keel_broker_coinbase import CoinbaseAdapter
from keel_core.types import Candle, Granularity, Side

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


def load_fixture(name: str) -> dict[str, Any]:
    with (FIXTURES_DIR / name).open() as f:
        data: dict[str, Any] = json.load(f)
    return data


class FakeTransport:
    """Duck-types `coinbase.rest.RESTClient`, returning fixtures and recording call kwargs."""

    def __init__(
        self,
        candles: dict[str, Any] | None = None,
        accounts: dict[str, Any] | None = None,
        preview: dict[str, Any] | None = None,
        placed: dict[str, Any] | None = None,
        summary: dict[str, Any] | None = None,
        order: dict[str, Any] | None = None,
        product: dict[str, Any] | None = None,
        products: dict[str, Any] | None = None,
    ) -> None:
        self._candles = candles
        self._accounts = accounts
        self._preview = preview
        self._placed = placed
        self._summary = summary
        self._order = order
        self._product = product
        self._products = products
        self.calls: dict[str, dict[str, Any]] = {}
        # Ids this transport has actually issued via `create_order`, so `cancel_orders` can tell
        # a genuine order apart from one the suite's unknown-id test made up -- the same
        # distinction the real venue draws, and the whole point of that assertion.
        self._issued_order_ids: set[str] = set()

    def get_products(self, product_type: str = "SPOT", **kwargs: Any) -> Any:
        self.calls["get_products"] = {"product_type": product_type}
        return self._products

    def get_candles(
        self, product_id: str, start: str, end: str, granularity: str, **kwargs: Any
    ) -> Any:
        self.calls["get_candles"] = {
            "product_id": product_id,
            "start": start,
            "end": end,
            "granularity": granularity,
        }
        return self._candles

    def get_product(self, product_id: str, **kwargs: Any) -> Any:
        self.calls["get_product"] = {"product_id": product_id}
        return {} if self._product is None else self._product

    def get_accounts(self, **kwargs: Any) -> Any:
        self.calls["get_accounts"] = {}
        return self._accounts

    def preview_order(
        self, product_id: str, side: str, order_configuration: dict[str, Any], **kwargs: Any
    ) -> Any:
        self.calls["preview_order"] = {
            "product_id": product_id,
            "side": side,
            "order_configuration": order_configuration,
        }
        return self._preview

    def create_order(
        self,
        client_order_id: str,
        product_id: str,
        side: str,
        order_configuration: dict[str, Any],
        **kwargs: Any,
    ) -> Any:
        self.calls["create_order"] = {
            "client_order_id": client_order_id,
            "product_id": product_id,
            "side": side,
            "order_configuration": order_configuration,
        }
        if self._placed is not None:
            issued_id = (self._placed.get("success_response") or {}).get("order_id")
            if issued_id is not None:
                self._issued_order_ids.add(issued_id)
        return self._placed

    def get_transaction_summary(self, **kwargs: Any) -> Any:
        self.calls["get_transaction_summary"] = {}
        return self._summary

    def get_order(self, order_id: str, **kwargs: Any) -> Any:
        self.calls["get_order"] = {"order_id": order_id}
        if self._order is not None:
            return self._order
        return {
            "order": {
                "order_id": order_id,
                "product_id": "BTC-USD",
                "side": "BUY",
                "status": "OPEN",
                "filled_size": "0",
            }
        }

    def cancel_orders(self, order_ids: list[str], **kwargs: Any) -> Any:
        self.calls["cancel_orders"] = {"order_ids": order_ids}
        return {
            "results": [
                {"order_id": order_id, "success": order_id in self._issued_order_ids}
                for order_id in order_ids
            ]
        }


def test_capabilities_declare_coinbase() -> None:
    caps = CoinbaseAdapter().capabilities()
    assert caps.venue == "coinbase"
    assert caps.supports_native_preview and not caps.synthesizes_preview
    assert caps.supports_fee_summary
    assert caps.can_preview


def test_coinbase_is_not_session_bound_and_answers_open_without_a_transport() -> None:
    """Crypto trades 24/7, so the venue declares `session_bound=False` and the clock answers
    OPEN as a constant. Constructed with NO transport: the proof that answering the clock for
    a 24/7 venue touches no network -- a clock call here would raise RuntimeError (the
    adapter's own "constructed without a transport" guard), so this passing IS the no-call
    guarantee (FR-9: crypto venues are always open)."""
    assert CoinbaseAdapter().capabilities().session_bound is False
    assert CoinbaseAdapter().market_clock() is SessionState.OPEN


def test_market_schedule_is_the_port_default_open_with_no_times() -> None:
    """Issue #388 C2: the 24/7 venues ship the port's DEFAULT schedule read -- the clock's
    OPEN answer with NO next_open/next_close claimed. Constructed without a transport for
    the same reason the clock test above is: a schedule call that touched the network would
    raise here, so this passing IS the no-call guarantee. A 24/7 adapter that synthesized
    timestamps would be inventing a calendar the venue does not have."""
    from keel_broker_api.port import default_market_schedule
    from keel_broker_api.results import MarketSchedule

    adapter = CoinbaseAdapter()
    assert adapter.market_schedule() == MarketSchedule(state=SessionState.OPEN)
    assert adapter.market_schedule() == default_market_schedule(adapter)


def test_get_candles_returns_ascending_domain_candles() -> None:
    """The fixture is deliberately out of order; the adapter must sort."""
    adapter = CoinbaseAdapter(FakeTransport(candles=load_fixture("cb_candles.json")))
    candles = adapter.get_candles("BTC-USD", Granularity.ONE_DAY, 1_720_915_200, 1_721_088_000)

    assert all(isinstance(c, Candle) for c in candles)
    assert [c.ts for c in candles] == sorted(c.ts for c in candles)
    assert candles[0].open == Decimal("63980.20")


def test_get_balances_returns_balance_objects_not_dicts() -> None:
    adapter = CoinbaseAdapter(FakeTransport(accounts=load_fixture("cb_accounts.json")))
    balances = adapter.get_balances()

    assert balances
    assert all(isinstance(b, Balance) for b in balances)
    btc = next(b for b in balances if b.currency == "BTC")
    assert btc.available == Decimal("0.53219871")
    assert btc.total >= btc.available


def test_preview_order_is_not_synthetic() -> None:
    """Coinbase returns its own quote, so approving it is not approving an estimate."""
    adapter = CoinbaseAdapter(FakeTransport(preview=load_fixture("cb_preview_order.json")))
    spec = MarketIOCByQuote(product_id="BTC-USD", side=Side.BUY, quote_size=Decimal("100"))
    preview = adapter.preview_order(spec)

    assert isinstance(preview, Preview)
    assert preview.synthetic is False
    assert preview.est_quote_size == Decimal("100.00")
    assert preview.est_base_size == Decimal("0.00152834")
    assert preview.est_fee == Decimal("0.60")
    assert preview.errors == ()


def test_place_order_maps_success() -> None:
    transport = FakeTransport(placed=load_fixture("cb_place_order_market.json"))
    adapter = CoinbaseAdapter(transport)
    spec = MarketIOCByQuote(product_id="BTC-USD", side=Side.BUY, quote_size=Decimal("100"))
    result = adapter.place_order(spec)

    assert isinstance(result, PlaceResult)
    assert result.success is True
    assert result.broker_order_id == "b1cd9a3b-4e5f-4a3c-9c8a-1f2e3d4c5b6a"
    assert result.reason is None
    assert transport.calls["create_order"]["order_configuration"] == {
        "market_market_ioc": {"quote_size": "100"}
    }


def test_place_order_maps_failure_with_a_reason() -> None:
    adapter = CoinbaseAdapter(FakeTransport(placed=load_fixture("cb_place_order_error.json")))
    spec = MarketIOCByQuote(product_id="BTC-USD", side=Side.BUY, quote_size=Decimal("100"))
    result = adapter.place_order(spec)

    assert result.success is False
    assert result.broker_order_id is None
    assert result.reason == "Insufficient balance in source account"


def test_place_order_generates_a_fresh_client_order_id_per_call() -> None:
    """With NO idempotency key, one id per attempt -- the default, unchanged by #409.

    The docstring here used to say "idempotency on Coinbase's side depends on this being unique
    per attempt", which had it backwards: a unique-per-attempt id is what WITHHOLDS idempotency,
    because it leaves the venue nothing to deduplicate on. It is still the right default -- two
    orders a strategy genuinely meant to place must not collapse into one -- but it is the
    opposite of idempotent, and the test below is the one that buys that property.
    """
    transport = FakeTransport(placed=load_fixture("cb_place_order_market.json"))
    adapter = CoinbaseAdapter(transport)
    spec = MarketIOCByBase(product_id="BTC-USD", side=Side.SELL, base_size=Decimal("0.1"))

    adapter.place_order(spec)
    first = transport.calls["create_order"]["client_order_id"]
    adapter.place_order(spec)
    second = transport.calls["create_order"]["client_order_id"]

    assert first != second


def test_an_idempotency_key_pins_the_client_order_id_across_attempts() -> None:
    """#409. The retry case: two attempts under one key must reach Coinbase as one
    `client_order_id`, so a retry after a timeout -- when the first request may already have
    landed -- is deduplicated by the venue rather than becoming a second live order."""
    transport = FakeTransport(placed=load_fixture("cb_place_order_market.json"))
    adapter = CoinbaseAdapter(transport)
    spec = MarketIOCByBase(product_id="BTC-USD", side=Side.SELL, base_size=Decimal("0.1"))

    adapter.place_order(spec, idempotency_key="cycle-7:pos-3:exit")
    first = transport.calls["create_order"]["client_order_id"]
    adapter.place_order(spec, idempotency_key="cycle-7:pos-3:exit")
    second = transport.calls["create_order"]["client_order_id"]

    assert first == second
    # ...and a DIFFERENT intent on the same position is still a different order.
    adapter.place_order(spec, idempotency_key="cycle-7:pos-3:stop")
    assert transport.calls["create_order"]["client_order_id"] != first


def test_limit_order_translates_through_to_the_transport() -> None:
    transport = FakeTransport(placed=load_fixture("cb_place_order_limit.json"))
    adapter = CoinbaseAdapter(transport)
    spec = LimitGTC(
        product_id="BTC-USD", side=Side.SELL, base_size=Decimal("1"), limit_price=Decimal("70000")
    )
    adapter.place_order(spec)

    assert transport.calls["create_order"]["order_configuration"] == {
        "limit_limit_gtc": {"base_size": "1", "limit_price": "70000"}
    }


def test_get_fee_summary_maps_the_transaction_summary() -> None:
    adapter = CoinbaseAdapter(FakeTransport(summary=load_fixture("cb_transaction_summary.json")))
    summary = adapter.get_fee_summary()

    assert isinstance(summary, FeeSummary)
    assert summary.venue == "coinbase"
    assert summary.taker_rate == Decimal("0.0075")
    assert summary.maker_rate == Decimal("0.0035")
    assert summary.fees_usd == Decimal("74.88")
    assert summary.volume_usd == Decimal("12480.55")
    assert summary.fetched_at > 0


def test_get_fee_summary_declares_an_unknown_window() -> None:
    """Coinbase's docs do not state the window, so the adapter must not guess one.

    Declaring `calendar_month` here would let reconciliation compare a possibly-trailing-30-day
    volume against a calendar-month allowance and mis-detect a lapse.
    """
    adapter = CoinbaseAdapter(FakeTransport(summary=load_fixture("cb_transaction_summary.json")))
    assert adapter.get_fee_summary().volume_window == "unknown"


def test_a_transportless_adapter_refuses_network_calls_clearly() -> None:
    """`capabilities()` works offline; anything needing the network says why it cannot."""
    adapter = CoinbaseAdapter()
    assert adapter.capabilities().venue == "coinbase"
    with pytest.raises(RuntimeError, match="without a transport"):
        adapter.get_balances()


# --- order status + cancellation -----------------------------------------------------------


def test_get_order_normalizes_status_fill_price_and_fees() -> None:
    """The reconciliation pass needs three things a placement response cannot give: whether the
    order actually filled, at what price, and for how much in fees. `average_filled_price` and
    `total_fees` are OBSERVED, replacing the expected-price and previewed-commission estimates
    the executor records at placement time."""
    transport = FakeTransport(
        order={
            "order": {
                "order_id": "abc-123",
                "product_id": "BTC-USD",
                "side": "SELL",
                "status": "FILLED",
                "filled_size": "0.01",
                "average_filled_price": "49875.42",
                "total_fees": "2.9925",
                "completion_percentage": "100",
            }
        }
    )
    adapter = CoinbaseAdapter(transport)

    order = adapter.get_order("abc-123")

    assert transport.calls["get_order"] == {"order_id": "abc-123"}
    assert isinstance(order, OrderStatus)
    assert order.order_id == "abc-123"
    assert order.status == "FILLED"
    assert order.filled_size == Decimal("0.01")
    assert order.average_filled_price == Decimal("49875.42")
    assert order.total_fees == Decimal("2.9925")


def test_get_order_on_an_unfilled_order_reports_zero_fill_not_none() -> None:
    """An OPEN bracket has no fills yet. Money fields must still come back as Decimals so
    callers never have to special-case None in arithmetic."""
    transport = FakeTransport(
        order={"order": {"order_id": "abc-123", "status": "OPEN", "filled_size": "0"}}
    )
    adapter = CoinbaseAdapter(transport)

    order = adapter.get_order("abc-123")

    assert order.status == "OPEN"
    assert order.filled_size == Decimal("0")
    assert order.average_filled_price == Decimal("0")
    assert order.total_fees == Decimal("0")


def test_cancel_order_is_confirmed_when_the_exchange_confirms() -> None:
    transport = FakeTransport()
    # Bypass the normal create_order plumbing: issue the id directly so the fake transport
    # treats it as one it has actually seen.
    transport._issued_order_ids.add("abc-123")
    adapter = CoinbaseAdapter(transport)

    outcome = adapter.cancel_order("abc-123")
    assert outcome is CancelOutcome.CONFIRMED
    assert outcome.settled
    assert transport.calls["cancel_orders"] == {"order_ids": ["abc-123"]}


def test_cancel_order_is_refused_when_the_exchange_refuses() -> None:
    """Coinbase's batch_cancel reports per-order success -- a 200 response does NOT mean the
    order was cancelled. Reading only the HTTP status would let `_cancel_at_exchange` record a
    cancel that never happened, which is the exact failure it exists to prevent."""
    adapter = CoinbaseAdapter(FakeTransport())

    assert adapter.cancel_order("never-issued") is CancelOutcome.REFUSED


def test_cancel_order_is_unknown_on_an_empty_result_set() -> None:
    """No result for the id we asked about means we have no confirmation. Absence of a refusal
    is not a confirmation -- fail closed.

    `UNKNOWN` rather than `REFUSED` (#412): the exchange did not answer about this order at all,
    which is a different operational fact from the exchange declining. Neither is settled, so the
    fail-closed behaviour is identical -- only the log line changes."""

    class EmptyCancelTransport(FakeTransport):
        def cancel_orders(self, order_ids: list[str], **kwargs: Any) -> Any:
            self.calls["cancel_orders"] = {"order_ids": order_ids}
            return {"results": []}

    adapter = CoinbaseAdapter(EmptyCancelTransport())

    outcome = adapter.cancel_order("abc")
    assert outcome is CancelOutcome.UNKNOWN
    assert not outcome.settled


# -- the product catalogue (#524) ------------------------------------------------------------


def test_get_instrument_reads_the_base_increment_from_the_per_product_endpoint() -> None:
    """`get_product`, not `get_products`.

    The caller (`executor._base_increment_for`) needs ONE product and caches ONE; fetching the
    whole ~900-row catalogue inside the order-placement path to use a single field of it is the
    wrong shape, and Coinbase exposes the per-product read this uses instead.
    """
    transport = FakeTransport(product={"product_id": "BTC-USD", "base_increment": "0.00000001"})
    adapter = CoinbaseAdapter(transport)

    instrument = adapter.get_instrument("BTC-USD")

    assert instrument is not None
    assert instrument.product_id == "BTC-USD"
    assert instrument.base_increment == Decimal("0.00000001")
    assert transport.calls["get_product"] == {"product_id": "BTC-USD"}


def test_get_instrument_unwraps_a_product_envelope() -> None:
    """Coinbase returns the product both bare and wrapped in `{"product": {...}}` depending on
    the endpoint and the client version; both must read the same."""
    adapter = CoinbaseAdapter(
        FakeTransport(product={"product": {"product_id": "ETH-USD", "base_increment": "0.0001"}})
    )
    instrument = adapter.get_instrument("ETH-USD")
    assert instrument is not None and instrument.base_increment == Decimal("0.0001")


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"product_id": "BTC-USD"},
        {"product_id": "BTC-USD", "base_increment": None},
        {"product_id": "BTC-USD", "base_increment": "not-a-number"},
        {"product_id": "BTC-USD", "base_increment": "0"},
        {"product_id": "BTC-USD", "base_increment": "-1"},
    ],
)
def test_get_instrument_answers_none_for_anything_unusable(payload: dict[str, Any]) -> None:
    """Missing, unparseable, zero and negative are one fact to a caller: no usable granularity.

    Zero and negative matter most. `executor._order_configuration` quantizes a SELL against this
    value, so a zero crossing the port is a division error or a silent zero size on the exit
    path -- which is why `Instrument.__post_init__` refuses it too, and why this returns `None`
    rather than constructing one.
    """
    adapter = CoinbaseAdapter(FakeTransport(product=payload))
    assert adapter.get_instrument("BTC-USD") is None


def test_list_products_serves_the_discovery_sweep() -> None:
    """The whole-catalogue read `keel assets discover` needs (#524's move of the last
    Coinbase-only client method onto the registry-resolved adapter).

    NOT a port method, on purpose: the port's catalogue surface is the per-product
    `get_instrument` the executor reads on the order path, and a ~900-row sweep is a DISCOVERY
    concern, not an order-path one. The projection is pinned field-for-field because the
    discovery sweep's filters read these keys -- a silently renamed key would quietly narrow
    every candidate list.
    """
    transport = FakeTransport(
        products={
            "products": [
                {
                    "product_id": "BTC-USD",
                    "base_name": "Bitcoin",
                    "quote_currency_id": "USD",
                    "status": "online",
                    "trading_disabled": False,
                    "is_disabled": False,
                    "view_only": False,
                    "approximate_quote_24h_volume": "12345.67",
                    "base_increment": "0.00000001",
                    "quote_increment": "0.01",
                }
            ]
        }
    )
    adapter = CoinbaseAdapter(transport)

    products = adapter.list_products()

    assert products == [
        {
            "product_id": "BTC-USD",
            "base_name": "Bitcoin",
            "quote_currency_id": "USD",
            "status": "online",
            "trading_disabled": False,
            "is_disabled": False,
            "view_only": False,
            "quote_24h_volume": "12345.67",
            "base_increment": "0.00000001",
            "quote_increment": "0.01",
        }
    ]
    assert transport.calls["get_products"] == {"product_type": "SPOT"}


# --- #233: the venue's half of the trade-scope record ---------------------------------------


class _Response:
    """The two attributes `requests.HTTPError.response` exposes that the classifier reads.

    Not a `requests.Response`: the adapter must classify on shape, not on a type it would have
    to import. `keel-broker-coinbase` depends on `coinbase-advanced-py`, which depends on
    `requests` -- but the adapter reaching past its SDK to `isinstance`-check the HTTP library
    underneath it would couple this package to a transitive dependency it never declared.
    """

    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text


class _HTTPError(Exception):
    """Shaped like `requests.HTTPError`: a message, and the response that produced it."""

    def __init__(self, message: str, response: _Response) -> None:
        super().__init__(message)
        self.response = response


#: The body Coinbase returns when the presented CDP key lacks the Trade scope. Not invented:
#: `coinbase.rest.rest_base.handle_exception` special-cases this exact substring on a 403,
#: ahead of every other 4xx, and rewrites the error message to "Missing Required Scopes.
#: Please verify your API keys include the necessary permissions." The SDK would not carry a
#: hard-coded branch for a body the venue does not send.
_MISSING_SCOPES_BODY = (
    '{"error":"PERMISSION_DENIED","error_details":"Missing required scopes",'
    '"message":"Missing required scopes"}'
)

#: A 403 from the SAME venue that is NOT about the credential's scope: observed live on
#: 2026-08-05 (`docs/experiments/2026-08-05-coinbase-asset-class-feasibility.md`, the
#: `preview_order` row) against a futures product on a portfolio that had not been onboarded
#: for FCM. The account was not entitled to the PRODUCT; the key's scopes were never in
#: question, and refuting the trade scope over it would take a working spot deployment off the
#: market for a fact about futures.
_FCM_NOT_ONBOARDED_BODY = (
    '{"error":"PERMISSION_DENIED","error_details":"",'
    '"message":"FCM preview orders are only enabled for onboarded users"}'
)

_MARKET_BUY = MarketIOCByQuote(product_id="BTC-USD", side=Side.BUY, quote_size=Decimal("100"))


class _RaisingTransport(FakeTransport):
    """A transport whose order-path calls raise `exc` the way the Coinbase SDK does."""

    def __init__(self, exc: Exception, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._exc = exc

    def create_order(self, **kwargs: Any) -> Any:
        raise self._exc

    def preview_order(self, **kwargs: Any) -> Any:
        raise self._exc


def _adapter(exc: Exception) -> CoinbaseAdapter:
    return CoinbaseAdapter(_RaisingTransport(exc, products=load_fixture("cb_product.json")))


def test_a_403_missing_required_scopes_on_placement_is_a_trade_scope_refusal() -> None:
    """The credential half of #233, in the one shape Coinbase actually signals it in.

    This is the whole point of the record's second writer: a CDP key minted with View but not
    Trade reads every endpoint happily and refuses exactly here. Without this mapping the
    executor sees an anonymous `HTTPError`, re-raises it, and the next cycle knows nothing --
    which is the 2026-08-19 incident's actual cost, restated on a different venue.
    """
    exc = _HTTPError("403 Client Error", _Response(403, _MISSING_SCOPES_BODY))

    with pytest.raises(TradeScopeDenied) as caught:
        _adapter(exc).place_order(_MARKET_BUY)

    assert "Missing required scopes" in str(caught.value)
    assert caught.value.__cause__ is exc


def test_a_403_missing_required_scopes_on_PREVIEW_is_also_a_trade_scope_refusal() -> None:
    """Coinbase's preview is a real venue call under the same scope, and the executor previews
    BEFORE it places.

    So on this venue a scope-less key is refused at preview and `place_order` is never reached.
    Classifying only placement would ship a gate that can never fire on the one venue this
    deployment actually trades -- `keel/capabilities.py` records that a gate nothing reaches is
    worse than no gate, because it reads as a defence.
    """
    exc = _HTTPError("403 Client Error", _Response(403, _MISSING_SCOPES_BODY))

    with pytest.raises(TradeScopeDenied):
        _adapter(exc).preview_order(_MARKET_BUY)


def test_a_500_does_NOT_refute_the_scope() -> None:
    """THE constraint. A `TradeScopeDenied` latches: it writes `REFUTED`, and rail 20 then vetoes
    every live ENTRY until a human types `yes` at a terminal. A venue outage that took a healthy
    deployment off the market and required physical presence to restore would be a far worse
    failure than the one this whole design exists to fix."""
    exc = _HTTPError("500 Server Error", _Response(500, "upstream unavailable"))

    with pytest.raises(_HTTPError):
        _adapter(exc).place_order(_MARKET_BUY)


def test_a_503_does_NOT_refute_the_scope() -> None:
    exc = _HTTPError("503 Server Error", _Response(503, _MISSING_SCOPES_BODY))

    with pytest.raises(_HTTPError):
        _adapter(exc).place_order(_MARKET_BUY)


def test_a_timeout_does_NOT_refute_the_scope() -> None:
    """No response object at all -- the classifier must answer "not a refusal" rather than
    raise a second error while handling the first."""
    with pytest.raises(TimeoutError):
        _adapter(TimeoutError("connection timed out")).place_order(_MARKET_BUY)


def test_a_403_that_is_not_about_scopes_does_NOT_refute_the_scope() -> None:
    """The live-observed near miss. Coinbase answers 403 `PERMISSION_DENIED` for product
    entitlement too, and that says nothing about the key's scopes -- which is exactly why the
    classifier keys on the SDK's own `Missing required scopes` predicate and not on the status
    code, and not on `PERMISSION_DENIED`."""
    exc = _HTTPError("403 Client Error", _Response(403, _FCM_NOT_ONBOARDED_BODY))

    with pytest.raises(_HTTPError):
        _adapter(exc).place_order(_MARKET_BUY)


def test_a_401_does_NOT_refute_the_scope() -> None:
    """A rejected credential is not a scoped one. The fix for a 401 is a new key, and the
    readiness display (#233 PR4) is where an absent or malformed credential belongs -- refuting
    the trade scope would send the operator to `keel scope attest` to fix a key that cannot
    read either."""
    exc = _HTTPError("401 Client Error", _Response(401, "Unauthorized"))

    with pytest.raises(_HTTPError):
        _adapter(exc).place_order(_MARKET_BUY)


def test_a_venue_rejection_returned_as_a_result_does_NOT_refute_the_scope() -> None:
    """`success: false` with `INSUFFICIENT_FUND` is the venue refusing THIS ORDER, on a
    credential that plainly reached the trading endpoint. It stays a `PlaceResult`, and the
    record is not touched."""
    transport = FakeTransport(placed=load_fixture("cb_place_order_error.json"))

    result = CoinbaseAdapter(transport).place_order(_MARKET_BUY)

    assert result.success is False
    assert result.reason == "Insufficient balance in source account"
# -- credential declaration (#233 PR4) ------------------------------------------------------------


def test_declares_its_credential_env_names() -> None:
    """The capability-display readiness surfaces (`keel brokers list`, `/api/venues`) read this
    with `getattr`, never by importing this package -- pinned against
    `keel_core.config.load_secrets`'s own names so the two cannot silently drift apart."""
    assert CoinbaseAdapter.DECLARED_CREDENTIAL_ENV == ("CDP_API_KEY", "CDP_API_SECRET")


def test_declares_no_credential_defect_hook() -> None:
    """Only Robinhood implements `credential_defect` (#233 PR4) -- Coinbase's credential is an
    opaque pair with no locally-provable shape to check, so the `getattr(..., None)` default is
    what the readiness derivation actually reads here, not a stub that always says "fine"."""
    assert getattr(CoinbaseAdapter, "credential_defect", None) is None
