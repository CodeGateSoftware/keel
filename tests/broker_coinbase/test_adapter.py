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
from keel_broker_api.results import Balance, FeeSummary, OrderStatus, PlaceResult, Preview
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
    ) -> None:
        self._candles = candles
        self._accounts = accounts
        self._preview = preview
        self._placed = placed
        self._summary = summary
        self._order = order
        self.calls: dict[str, dict[str, Any]] = {}
        # Ids this transport has actually issued via `create_order`, so `cancel_orders` can tell
        # a genuine order apart from one the suite's unknown-id test made up -- the same
        # distinction the real venue draws, and the whole point of that assertion.
        self._issued_order_ids: set[str] = set()

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
        return {}

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
    """Idempotency on Coinbase's side depends on this being unique per attempt."""
    transport = FakeTransport(placed=load_fixture("cb_place_order_market.json"))
    adapter = CoinbaseAdapter(transport)
    spec = MarketIOCByBase(product_id="BTC-USD", side=Side.SELL, base_size=Decimal("0.1"))

    adapter.place_order(spec)
    first = transport.calls["create_order"]["client_order_id"]
    adapter.place_order(spec)
    second = transport.calls["create_order"]["client_order_id"]

    assert first != second


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


def test_cancel_order_returns_true_when_the_exchange_confirms() -> None:
    transport = FakeTransport()
    # Bypass the normal create_order plumbing: issue the id directly so the fake transport
    # treats it as one it has actually seen.
    transport._issued_order_ids.add("abc-123")
    adapter = CoinbaseAdapter(transport)

    assert adapter.cancel_order("abc-123") is True
    assert transport.calls["cancel_orders"] == {"order_ids": ["abc-123"]}


def test_cancel_order_returns_false_when_the_exchange_refuses() -> None:
    """Coinbase's batch_cancel reports per-order success -- a 200 response does NOT mean the
    order was cancelled. Reading only the HTTP status would let `_cancel_at_exchange` record a
    cancel that never happened, which is the exact failure it exists to prevent."""
    adapter = CoinbaseAdapter(FakeTransport())

    assert adapter.cancel_order("never-issued") is False


def test_cancel_order_returns_false_on_an_empty_result_set() -> None:
    """No result for the id we asked about means we have no confirmation. Absence of a refusal
    is not a confirmation -- fail closed."""

    class EmptyCancelTransport(FakeTransport):
        def cancel_orders(self, order_ids: list[str], **kwargs: Any) -> Any:
            self.calls["cancel_orders"] = {"order_ids": order_ids}
            return {"results": []}

    adapter = CoinbaseAdapter(EmptyCancelTransport())

    assert adapter.cancel_order("abc") is False
