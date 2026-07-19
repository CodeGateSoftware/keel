"""Coinbase held to the shared contract, driven by canned fixtures.

The transport is the same `FakeTransport` the adapter's own tests use -- never a live
`RESTClient`. The suite calls `place_order`, so a live client here would place real orders.
"""

from __future__ import annotations

from keel_broker_api.conformance.suite import BrokerConformanceTests
from keel_broker_coinbase import CoinbaseAdapter

from tests.broker_coinbase.test_adapter import FakeTransport, load_fixture


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
