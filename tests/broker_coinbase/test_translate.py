from __future__ import annotations

from decimal import Decimal

import pytest
from keel_broker_api.orders import LimitGTC, MarketIOCByBase, MarketIOCByQuote, StopLimitGTC
from keel_broker_coinbase.translate import to_order_configuration
from keel_core.types import Side


def test_market_by_quote() -> None:
    spec = MarketIOCByQuote(product_id="BTC-USD", side=Side.BUY, quote_size=Decimal("100.50"))
    assert to_order_configuration(spec) == {"market_market_ioc": {"quote_size": "100.50"}}


def test_market_by_base() -> None:
    spec = MarketIOCByBase(product_id="BTC-USD", side=Side.SELL, base_size=Decimal("0.25"))
    assert to_order_configuration(spec) == {"market_market_ioc": {"base_size": "0.25"}}


def test_limit_gtc() -> None:
    spec = LimitGTC(
        product_id="BTC-USD", side=Side.SELL, base_size=Decimal("1"), limit_price=Decimal("70000")
    )
    assert to_order_configuration(spec) == {
        "limit_limit_gtc": {"base_size": "1", "limit_price": "70000"}
    }


def test_stop_limit_gtc() -> None:
    spec = StopLimitGTC(
        product_id="BTC-USD",
        side=Side.SELL,
        base_size=Decimal("1"),
        stop_price=Decimal("60000"),
        limit_price=Decimal("59900"),
    )
    config = to_order_configuration(spec)
    assert "stop_limit_stop_limit_gtc" in config
    leg = config["stop_limit_stop_limit_gtc"]
    assert leg["base_size"] == "1"
    assert leg["stop_price"] == "60000"
    assert leg["limit_price"] == "59900"


def test_decimals_are_rendered_as_exact_strings_not_floats() -> None:
    """A float round-trip here would silently change an order's size."""
    spec = MarketIOCByQuote(
        product_id="BTC-USD", side=Side.BUY, quote_size=Decimal("100.123456789")
    )
    assert to_order_configuration(spec)["market_market_ioc"]["quote_size"] == "100.123456789"


def test_unknown_spec_type_raises() -> None:
    with pytest.raises(Exception):
        to_order_configuration(object())  # type: ignore[arg-type]
