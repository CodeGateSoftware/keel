"""Coinbase held to the shared contract, driven by canned fixtures.

The transport is the same `FakeTransport` the adapter's own tests use -- never a live
`RESTClient`. The suite calls `place_order`, so a live client here would place real orders.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from keel_broker_api.conformance.suite import BrokerConformanceTests
from keel_broker_api.orders import MarketIOCByQuote
from keel_broker_api.port import TradeScopeDenied
from keel_broker_coinbase import CoinbaseAdapter
from keel_core.types import Side

from tests.broker_coinbase.test_adapter import (
    _MISSING_SCOPES_BODY,
    FakeTransport,
    _HTTPError,
    _RaisingTransport,
    _Response,
    load_fixture,
)


class TestCoinbaseConformance(BrokerConformanceTests):
    def broker(self) -> CoinbaseAdapter:
        return CoinbaseAdapter(
            FakeTransport(
                candles=load_fixture("cb_candles.json"),
                accounts=load_fixture("cb_accounts.json"),
                preview=load_fixture("cb_preview_order.json"),
                placed=load_fixture("cb_place_order_market.json"),
                summary=load_fixture("cb_transaction_summary.json"),
            )
        )

    def test_a_scope_refusal_crosses_the_port_as_TradeScopeDenied(self) -> None:
        """#233's addition to this venue's contract: a `Missing required scopes` 403 must arrive
        at the caller as the port's own word, not as the SDK's `HTTPError`.

        Additive -- nothing in `BrokerConformanceTests` changed, because not every venue has an
        observed permission refusal to map and the shared suite must not force one to be invented
        (see `tests/broker_alpaca/test_adapter.py::TestTradeScopeIsDeliberatelyUnmapped`).
        """
        exc = _HTTPError("403 Client Error", _Response(403, _MISSING_SCOPES_BODY))
        adapter = CoinbaseAdapter(
            _RaisingTransport(exc, products=load_fixture("cb_product.json"))
        )

        with pytest.raises(TradeScopeDenied):
            adapter.place_order(self._scope_spec())

    def test_a_5xx_does_NOT_cross_the_port_as_TradeScopeDenied(self) -> None:
        """The half of the contract that protects a HEALTHY deployment. `TradeScopeDenied`
        latches `REFUTED` and rail 20 then vetoes every live ENTRY until a human re-attests at a
        terminal, so a venue outage classified as a refusal would take a working deployment off
        the market and require physical presence to restore. The body deliberately CONTAINS the
        scopes text, so an implementation that matched on the body without gating on the status
        fails here."""
        exc = _HTTPError("503 Server Error", _Response(503, _MISSING_SCOPES_BODY))
        adapter = CoinbaseAdapter(
            _RaisingTransport(exc, products=load_fixture("cb_product.json"))
        )

        with pytest.raises(_HTTPError):
            adapter.place_order(self._scope_spec())

    @staticmethod
    def _scope_spec() -> MarketIOCByQuote:
        return MarketIOCByQuote(
            product_id="BTC-USD", side=Side.BUY, quote_size=Decimal("100")
        )
