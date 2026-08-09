"""Unit tests for `keel_broker_robinhood.translate` -- the one place keel's order model becomes
Robinhood's order-body and state vocabulary.

These are pure-function tests: no transport, no network, no adapter. They exist to pin the exact
shape Robinhood's API demands (and the two-gate refusal of `MarketIOCByQuote`) independently of
whatever the adapter does with the result.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from keel_broker_api.orders import LimitGTC, MarketIOCByBase, MarketIOCByQuote, StopLimitGTC
from keel_broker_api.port import UnsupportedOrder
from keel_broker_robinhood.translate import (
    STATE_TO_PORT_STATUS,
    to_order_body,
    to_port_status,
    to_price_side,
    to_side,
    to_symbol,
)
from keel_core.types import Side


def test_to_symbol_uppercases_both_legs() -> None:
    assert to_symbol("btc-usd") == "BTC-USD"
    assert to_symbol("BTC-usd") == "BTC-USD"


def test_to_symbol_refuses_a_non_usd_quote_leg_and_names_it() -> None:
    """Rewriting `BTC-USDC` to `BTC-USD` would silently settle against a different asset than the
    caller asked for, on the live-money path -- and passing it through unchanged would be a
    rejection indistinguishable from an outage. Refusing by name, with the offending currency in
    the message, is the only option that is honest about what happened."""
    with pytest.raises(UnsupportedOrder, match="USDC"):
        to_symbol("BTC-USDC")


def test_to_symbol_refuses_a_malformed_product_id() -> None:
    """A product id that is not `BASE-QUOTE` shaped must not be guessed at -- guessing how to
    split it risks the exact same silent-substitution failure as rewriting the quote leg."""
    with pytest.raises(UnsupportedOrder):
        to_symbol("BTCUSD")


def test_to_side_renders_lowercase() -> None:
    assert to_side(Side.BUY) == "buy"
    assert to_side(Side.SELL) == "sell"


def test_to_price_side_buy_prices_off_the_ask() -> None:
    """A BUY fills at the ask -- what a seller will take. Pricing it off the bid would show the
    optimistic side of the spread, making a synthesized preview look better than the fill it is
    meant to estimate."""
    assert to_price_side(Side.BUY) == "ask"


def test_to_price_side_sell_prices_off_the_bid() -> None:
    assert to_price_side(Side.SELL) == "bid"


def test_market_ioc_base_body() -> None:
    spec = MarketIOCByBase(product_id="BTC-USD", side=Side.SELL, base_size=Decimal("0.1"))
    body = to_order_body(spec, client_order_id="c1")

    assert body["symbol"] == "BTC-USD"
    assert body["client_order_id"] == "c1"
    assert body["side"] == "sell"
    assert body["type"] == "market"
    assert body["market_order_config"] == {"asset_quantity": "0.1"}


def test_limit_gtc_body() -> None:
    spec = LimitGTC(
        product_id="BTC-USD", side=Side.SELL, base_size=Decimal("1"), limit_price=Decimal("70000")
    )
    body = to_order_body(spec, client_order_id="c1")

    assert body["type"] == "limit"
    assert body["limit_order_config"] == {
        "asset_quantity": "1",
        "limit_price": "70000",
        "time_in_force": "gtc",
    }


def test_stop_limit_gtc_body() -> None:
    spec = StopLimitGTC(
        product_id="BTC-USD",
        side=Side.SELL,
        base_size=Decimal("1"),
        stop_price=Decimal("60000"),
        limit_price=Decimal("59900"),
    )
    body = to_order_body(spec, client_order_id="c1")

    assert body["type"] == "stop_limit"
    assert body["stop_limit_order_config"] == {
        "asset_quantity": "1",
        "limit_price": "59900",
        "stop_price": "60000",
        "time_in_force": "gtc",
    }


def test_decimals_are_rendered_as_exact_strings_not_floats() -> None:
    """A float round-trip here would silently change an order's size, price, or stop on the
    live-money path."""
    spec = LimitGTC(
        product_id="BTC-USD",
        side=Side.BUY,
        base_size=Decimal("0.123456789"),
        limit_price=Decimal("64000.10"),
    )
    body = to_order_body(spec, client_order_id="c1")

    assert body["limit_order_config"]["asset_quantity"] == "0.123456789"
    assert body["limit_order_config"]["limit_price"] == "64000.10"


def test_market_ioc_by_quote_is_refused_with_the_real_reason() -> None:
    """This is the second gate: the adapter's capability declaration already excludes this kind,
    so a future bug that routes a `MarketIOCByQuote` past the first gate must still be unable to
    place an order here. The message must name the actual constraint -- `market_order_config`
    accepts only `asset_quantity` -- not a generic "unsupported" with no explanation, since
    whoever reads this exception at 2am needs to know it is not a bug to retry."""
    spec = MarketIOCByQuote(product_id="BTC-USD", side=Side.BUY, quote_size=Decimal("100"))

    with pytest.raises(UnsupportedOrder, match="asset_quantity"):
        to_order_body(spec, client_order_id="c1")


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("open", "OPEN"),
        ("canceled", "CANCELLED"),
        ("filled", "FILLED"),
        ("failed", "FAILED"),
        ("pending", "PENDING"),
    ],
)
def test_to_port_status_maps_every_known_state(state: str, expected: str) -> None:
    assert to_port_status(state) == expected
    assert STATE_TO_PORT_STATUS[state] == expected


def test_to_port_status_defaults_an_unknown_state_to_pending_not_failed() -> None:
    """An unrecognised state means the adapter does not know the outcome -- not that the order
    failed. `PENDING` keeps the order under observation; `FAILED` would declare a terminal
    outcome nobody observed, and could let the engine re-enter a position that is, for all this
    adapter knows, still live at the venue."""
    assert to_port_status("a_future_state_this_adapter_predates") == "PENDING"


def test_to_port_status_defaults_none_to_pending() -> None:
    """A missing `state` is the same "I don't know" as an unrecognised one, and must resolve the
    same way -- under observation, not declared dead."""
    assert to_port_status(None) == "PENDING"
