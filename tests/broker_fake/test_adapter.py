"""Each test here pins one deliberate divergence from Coinbase.

If one of these starts failing because the fake was "fixed" to match Coinbase, the fake has
stopped doing its job -- it exists to make Coinbase-shaped assumptions in the port fail loudly.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from keel_broker_api.orders import LimitGTC, MarketIOCByBase, MarketIOCByQuote, StopLimitGTC
from keel_broker_api.port import UnsupportedOrder
from keel_broker_api.results import Balance, PlaceResult
from keel_broker_fake import FakeAdapter
from keel_broker_fake.adapter import MAX_CANDLES_PER_CALL
from keel_core.types import Granularity, Side


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
