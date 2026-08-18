"""Alpaca held to the shared `Broker` contract, driven entirely by canned fixtures.

The transport is the same `FakeTransport` the adapter's own tests use -- never a live
`AlpacaTransport`. The suite calls `place_order`, so a live transport here would place
orders against a real (paper) venue; the fixture-driven design is the whole point.
"""

from __future__ import annotations

from keel_broker_alpaca import AlpacaAdapter
from keel_broker_api.conformance.suite import BrokerConformanceTests

from tests.broker_alpaca.test_adapter import FakeTransport, load_fixture


class TestAlpacaConformance(BrokerConformanceTests):
    def broker(self) -> AlpacaAdapter:
        return AlpacaAdapter(
            FakeTransport(
                account=load_fixture("alpaca_account.json"),
                positions=load_fixture("alpaca_positions.json"),
                clock=load_fixture("alpaca_clock_open.json"),
                placed=load_fixture("alpaca_order_placed.json"),
                order=load_fixture("alpaca_order_filled.json"),
                bars_pages=[
                    load_fixture("alpaca_bars_page1.json"),
                    load_fixture("alpaca_bars_page2.json"),
                ],
                quote=load_fixture("alpaca_quote_latest.json"),
            )
        )
