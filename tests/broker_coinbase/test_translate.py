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


# `test_bracket_gtc_is_byte_identical_to_what_the_executor_ships_today` stood here, and #524
# deleted it along with the thing it was pinning.
#
# It existed because the tree carried TWO Coinbase order renderers: this translation, and
# `executor._bracket_order_configuration`, which built the same dict by hand for the live path.
# #502 stage 1 could not delete the second one -- the executor was not on the port yet -- so it
# pinned them byte-identical instead, and said so: "The test imports both; production code does
# not."
#
# The executor now builds a `BracketGTC` and hands it to `CoinbaseClient.place_order`, which
# renders it through THIS function. There is one renderer, so there is nothing left to hold in
# agreement, and a test comparing a function to itself would pass forever without saying anything.
#
# What the bracket's wire shape still owes is covered where it belongs: the cases below pin the
# three keys and the deliberate absence of `stop_direction`, and
# `tests/data/test_cb_client.py::test_place_order_renders_a_bracket_through_the_one_renderer`
# proves the live client sends exactly what this function returns.


