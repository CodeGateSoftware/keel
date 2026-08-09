"""Robinhood held to the shared `Broker` contract, driven entirely by canned fixtures.

Robinhood ships NO SANDBOX -- there is no test environment to point a real client at, unlike
Coinbase. A canned, in-memory `FakeTransport` is therefore the only safe way to run a suite that
calls `place_order`: pointing this suite at `RobinhoodTransport` with real credentials would place
real orders, and there is no lower-stakes venue to redirect it to first.
"""

from __future__ import annotations

from keel_broker_api.conformance.suite import BrokerConformanceTests
from keel_broker_robinhood import RobinhoodAdapter

from tests.broker_robinhood.test_adapter import FakeTransport, load_fixture


class TestRobinhoodConformance(BrokerConformanceTests):
    def broker(self) -> RobinhoodAdapter:
        return RobinhoodAdapter(
            FakeTransport(
                accounts=load_fixture("rh_accounts.json"),
                holdings=load_fixture("rh_holdings.json"),
                trading_pairs=load_fixture("rh_trading_pairs.json"),
                best_bid_ask=load_fixture("rh_best_bid_ask.json"),
                estimated_price=load_fixture("rh_estimated_price.json"),
                placed=load_fixture("rh_order_open.json"),
                order=load_fixture("rh_order_open.json"),
            )
        )
