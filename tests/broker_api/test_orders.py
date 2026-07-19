from __future__ import annotations

from decimal import Decimal

import pytest
from keel_broker_api.orders import (
    ORDER_KINDS,
    LimitGTC,
    MarketIOCByBase,
    MarketIOCByQuote,
    StopLimitGTC,
)
from keel_core.types import Side


def test_each_variant_has_a_distinct_kind() -> None:
    kinds = {
        MarketIOCByQuote.kind,
        MarketIOCByBase.kind,
        LimitGTC.kind,
        StopLimitGTC.kind,
    }
    assert kinds == {"market_ioc_quote", "market_ioc_base", "limit_gtc", "stop_limit_gtc"}


def test_order_kinds_lists_every_variant() -> None:
    """ORDER_KINDS is what capabilities are declared against -- it must not drift."""
    assert ORDER_KINDS == frozenset(
        {"market_ioc_quote", "market_ioc_base", "limit_gtc", "stop_limit_gtc"}
    )


def test_variants_are_frozen() -> None:
    spec = MarketIOCByQuote(product_id="BTC-USD", side=Side.BUY, quote_size=Decimal("100"))
    with pytest.raises(Exception):
        spec.quote_size = Decimal("200")  # type: ignore[misc]


def test_market_orders_carry_only_their_own_sizing_field() -> None:
    """The whole point of the sum type: a market order cannot carry a limit price."""
    by_quote = MarketIOCByQuote(product_id="BTC-USD", side=Side.BUY, quote_size=Decimal("100"))
    by_base = MarketIOCByBase(product_id="BTC-USD", side=Side.SELL, base_size=Decimal("0.5"))
    assert not hasattr(by_quote, "limit_price")
    assert not hasattr(by_quote, "base_size")
    assert not hasattr(by_base, "quote_size")


def test_initial_status_per_variant() -> None:
    """Replaces executor._initial_status, which string-matched Coinbase's config key."""
    assert (
        MarketIOCByQuote(
            product_id="BTC-USD", side=Side.BUY, quote_size=Decimal("100")
        ).initial_status
        == "filled_or_rejected"
    )
    assert (
        LimitGTC(
            product_id="BTC-USD",
            side=Side.SELL,
            base_size=Decimal("1"),
            limit_price=Decimal("70000"),
        ).initial_status
        == "open"
    )
    assert (
        StopLimitGTC(
            product_id="BTC-USD",
            side=Side.SELL,
            base_size=Decimal("1"),
            stop_price=Decimal("60000"),
            limit_price=Decimal("59900"),
        ).initial_status
        == "open"
    )


def test_rejects_non_positive_size() -> None:
    with pytest.raises(ValueError, match="quote_size must be positive"):
        MarketIOCByQuote(product_id="BTC-USD", side=Side.BUY, quote_size=Decimal("0"))
    with pytest.raises(ValueError, match="base_size must be positive"):
        MarketIOCByBase(product_id="BTC-USD", side=Side.BUY, base_size=Decimal("-1"))
