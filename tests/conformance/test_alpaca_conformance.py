"""Alpaca held to the shared `Broker` contract, driven entirely by canned fixtures.

The transport is the same `FakeTransport` the adapter's own tests use -- never a live
`AlpacaTransport`. The suite calls `place_order`, so a live transport here would place
orders against a real (paper) venue; the fixture-driven design is the whole point.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from keel_broker_alpaca import AlpacaAdapter
from keel_broker_alpaca.transport import AlpacaAPIError
from keel_broker_api.conformance.suite import BrokerConformanceTests
from keel_broker_api.orders import MarketIOCByQuote
from keel_broker_api.port import TradeScopeDenied
from keel_core.types import Side

from tests.broker_alpaca.test_adapter import (
    FakeTransport,
    _RejectingCreateTransport,
    load_fixture,
)


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

    @pytest.mark.parametrize("status", [401, 403, 422, 429, 500, 503])
    def test_no_status_crosses_the_port_as_TradeScopeDenied(self, status: int) -> None:
        """**This venue's #233 entry is a REFUSAL, and it is deliberate.**

        Every sibling adapter maps an observed permission refusal onto `TradeScopeDenied`. Alpaca
        has none to map: its `403` is documented and pinned in this repository as INSUFFICIENT
        BUYING POWER -- an ordinary cash shortfall -- and routing that to a refusal would latch
        `REFUTED`, veto every live entry through rail 20, and demand an operator at a terminal to
        clear an outage manufactured out of a low balance. `422` is a bad body, `401` a credential
        the venue does not recognise, and the rest are the network.

        #233's own design forbids exactly the shortcut this test blocks: "the design must not
        pre-classify it from documentation, and must record it when it finally arrives." So this
        venue records nothing until a real refusal is observed. If one ever is, map THAT shape and
        delete this test -- do not map a status because the other adapters map one.
        """
        transport = _RejectingCreateTransport(
            AlpacaAPIError(status, "whatever the venue said"),
            placed=load_fixture("alpaca_order_placed.json"),
        )
        spec = MarketIOCByQuote(product_id="BTC-USD", side=Side.BUY, quote_size=Decimal("100"))

        try:
            AlpacaAdapter(transport).place_order(spec)
        except TradeScopeDenied:  # pragma: no cover - the regression this test exists to catch
            pytest.fail(f"alpaca mapped HTTP {status} to a trade-scope refusal on no evidence")
        except AlpacaAPIError:
            pass
