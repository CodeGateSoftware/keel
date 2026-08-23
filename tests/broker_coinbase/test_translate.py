from __future__ import annotations

from decimal import Decimal

import pytest
from keel_broker_api.orders import (
    BracketGTC,
    LimitGTC,
    MarketIOCByBase,
    MarketIOCByQuote,
    StopLimitGTC,
)
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


def test_bracket_gtc() -> None:
    spec = BracketGTC(
        product_id="BTC-USD",
        side=Side.SELL,
        base_size=Decimal("0.5"),
        take_profit_price=Decimal("70000"),
        stop_trigger_price=Decimal("60000"),
    )
    assert to_order_configuration(spec) == {
        "trigger_bracket_gtc": {
            "base_size": "0.5",
            "limit_price": "70000",
            "stop_trigger_price": "60000",
        }
    }


def test_bracket_gtc_carries_no_stop_direction() -> None:
    """Unlike `stop_limit_stop_limit_gtc`, Coinbase's trigger bracket takes no direction.

    The bracket's two prices already say which way each side triggers, and the shipped
    `executor._bracket_order_configuration` has never sent one. Adding a key the venue-accepted
    dict does not carry would be a change to the wire shape dressed as a port migration.
    """
    spec = BracketGTC(
        product_id="BTC-USD",
        side=Side.SELL,
        base_size=Decimal("0.5"),
        take_profit_price=Decimal("70000"),
        stop_trigger_price=Decimal("60000"),
    )
    assert "stop_direction" not in to_order_configuration(spec)["trigger_bracket_gtc"]


def test_bracket_gtc_is_byte_identical_to_what_the_executor_ships_today() -> None:
    """Parity with the shipped, venue-accepted dict IS the contract for this kind.

    `executor._bracket_order_configuration` is what Coinbase has actually been accepting on the
    live path. The port's job here is to reach the same wire shape through a typed spec, not to
    improve on it -- so this test pins the two together and will fail the moment either side
    drifts.

    The TEST imports both; production code must not. `keel_broker_coinbase` is a standalone
    package that knows nothing about `keel.execution`, and the day Stage 2 switches the live
    caller over, this assertion is what says the switch changed no bytes on the wire.
    """
    from keel.execution.executor import _bracket_order_configuration

    qty, target, stop = Decimal("0.12345678"), Decimal("70123.45"), Decimal("60987.65")
    spec = BracketGTC(
        product_id="BTC-USD",
        side=Side.SELL,
        base_size=qty,
        take_profit_price=target,
        stop_trigger_price=stop,
    )
    assert to_order_configuration(spec) == _bracket_order_configuration(qty, target, stop)
