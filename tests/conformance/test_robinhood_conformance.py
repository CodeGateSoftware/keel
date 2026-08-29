"""Robinhood held to the shared `Broker` contract, driven entirely by canned fixtures.

Robinhood ships NO SANDBOX -- there is no test environment to point a real client at, unlike
Coinbase. A canned, in-memory `FakeTransport` is therefore the only safe way to run a suite that
calls `place_order`: pointing this suite at `RobinhoodTransport` with real credentials would place
real orders, and there is no lower-stakes venue to redirect it to first.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from keel_broker_api.conformance.suite import BrokerConformanceTests
from keel_broker_api.orders import MarketIOCByBase
from keel_broker_api.port import TradeScopeDenied
from keel_broker_robinhood import RobinhoodAdapter
from keel_core.types import Side

from tests.broker_robinhood.test_adapter import (
    _NO_PERMISSION_BODY,
    FakeTransport,
    _ScopeHTTPError,
    _ScopeRaisingTransport,
    _ScopeResponse,
    load_fixture,
)


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
                # Wired so `test_fee_summary_matches_its_declaration` exercises the real
                # order-history sweep `fees_usd` is summed from (#197). Leaving it out would let
                # the conformance run assert against an EMPTY sweep -- which returns
                # `Decimal("0")` and is indistinguishable from the hardcoded zero that issue
                # closed, so the one suite held out as this venue's end-to-end signal would pass
                # just as happily on the bug as on the fix.
                orders=load_fixture("rh_orders.json"),
            )
        )

    def test_the_observed_403_crosses_the_port_as_TradeScopeDenied(self) -> None:
        """#233's motivating case, held as this venue's contract. `403 {"detail": "You do not
        have permission to perform this action."}` was observed on a live probe under a
        credential whose every READ succeeded, and nothing recorded it."""
        exc = _ScopeHTTPError("403 Client Error", _ScopeResponse(403, _NO_PERMISSION_BODY))

        with pytest.raises(TradeScopeDenied) as caught:
            self._refusing(exc).place_order(self._scope_spec())

        assert str(caught.value) == "You do not have permission to perform this action."

    def test_a_5xx_does_NOT_cross_the_port_as_TradeScopeDenied(self) -> None:
        """A refusal latches `REFUTED` and halts live entries until a human re-attests; a venue
        outage must never do that. The body carries the venue's permission sentence so an
        implementation that matched on prose instead of status fails here."""
        exc = _ScopeHTTPError("503 Server Error", _ScopeResponse(503, _NO_PERMISSION_BODY))

        with pytest.raises(_ScopeHTTPError):
            self._refusing(exc).place_order(self._scope_spec())

    @staticmethod
    def _refusing(exc: Exception) -> RobinhoodAdapter:
        return RobinhoodAdapter(
            _ScopeRaisingTransport(
                exc,
                trading_pairs=load_fixture("rh_trading_pairs.json"),
                estimated_price=load_fixture("rh_estimated_price.json"),
                best_bid_ask=load_fixture("rh_best_bid_ask.json"),
            )
        )

    @staticmethod
    def _scope_spec() -> MarketIOCByBase:
        return MarketIOCByBase(
            product_id="BTC-USD", side=Side.SELL, base_size=Decimal("0.001")
        )
