"""The deterministic dev venue held to the shared contract -- and to #233's refusal path.

This is the only adapter here that can be MADE to refuse without a live credential lacking trade
scope at a real venue, which is what makes it the fixture the executor's confirm/refute write is
driven against end to end.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from keel_broker_api.conformance.suite import BrokerConformanceTests
from keel_broker_api.orders import MarketIOCByBase
from keel_broker_api.port import TradeScopeDenied
from keel_broker_fake import FakeAdapter
from keel_core.types import Side


class TestFakeConformance(BrokerConformanceTests):
    def broker(self) -> FakeAdapter:
        # No knob: the conformance run must see the venue every other caller sees. The refusal
        # is opt-in below, per test, so nothing in the shared contract is evaluated against a
        # fake that refuses everything.
        return FakeAdapter()

    def test_the_refusal_knob_crosses_the_port_as_TradeScopeDenied(self) -> None:
        adapter = FakeAdapter(trade_scope_denied="the venue said no")

        with pytest.raises(TradeScopeDenied) as caught:
            adapter.place_order(
                MarketIOCByBase(product_id="BTC-USD", side=Side.BUY, base_size=Decimal("0.1"))
            )

        assert str(caught.value) == "the venue said no"

    def test_the_default_venue_places_and_refuses_nothing(self) -> None:
        """The knob is opt-in. Every existing construction site passes no arguments, and the
        conformance run above is one of them."""
        assert FakeAdapter().place_order(
            MarketIOCByBase(product_id="BTC-USD", side=Side.BUY, base_size=Decimal("0.1"))
        ).success
