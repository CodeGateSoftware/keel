"""Tests for `keel_broker_alpaca.translate`.

`translate.py` is where keel's order model becomes Alpaca's order-body and status
vocabulary, mirroring `keel_broker_coinbase.translate` and `keel_broker_robinhood.translate`.
Every Alpaca-specific spelling (symbol without a quote leg, `notional` vs `qty` market
sizing, `time_in_force` per order type, the cancelled-order state spelling) is pinned here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from keel_broker_alpaca.translate import (
    STATE_TO_PORT_STATUS,
    TIMEFRAME_BY_GRANULARITY,
    to_order_body,
    to_port_status,
    to_rfc3339,
    to_side,
    to_symbol,
    to_timeframe,
    to_unix_seconds,
)
from keel_broker_api.orders import LimitGTC, MarketIOCByBase, MarketIOCByQuote, StopLimitGTC
from keel_broker_api.port import UnsupportedOrder
from keel_core.types import Granularity, Side


def test_to_symbol_strips_the_usd_quote_leg() -> None:
    """Alpaca's equity symbols carry no quote leg: keel's `AAPL-USD` is Alpaca's `AAPL`."""
    assert to_symbol("AAPL-USD") == "AAPL"
    assert to_symbol("aapl-usd") == "AAPL"


def test_to_symbol_refuses_a_non_usd_quote_leg() -> None:
    """Rewriting `AAPL-EUR` to a USD-quoted symbol would swap the settlement asset under
    the caller, exactly the substitution `keel_broker_robinhood.translate.to_symbol` refuses."""
    with pytest.raises(UnsupportedOrder, match="USD"):
        to_symbol("AAPL-EUR")


def test_to_symbol_refuses_a_product_id_that_is_not_base_quote_shaped() -> None:
    with pytest.raises(UnsupportedOrder, match="BASE-QUOTE"):
        to_symbol("AAPL")


@pytest.mark.parametrize(
    ("granularity", "timeframe"),
    [
        (Granularity.FIFTEEN_MINUTE, "15Min"),
        (Granularity.ONE_HOUR, "1Hour"),
        (Granularity.ONE_DAY, "1Day"),
    ],
)
def test_keel_granularities_map_to_their_alpaca_timeframes(
    granularity: Granularity, timeframe: str
) -> None:
    """The PRD's three confirmation/trading/bias series map exactly onto Alpaca's v2
    timeframe strings (docs.alpaca.markets, "Stock Bars": `15Min`, `1Hour`, `1Day`)."""
    assert to_timeframe(granularity) == timeframe
    assert TIMEFRAME_BY_GRANULARITY[granularity] == timeframe


@pytest.mark.parametrize(
    "granularity",
    [Granularity.ONE_MINUTE, Granularity.FIVE_MINUTE, Granularity.SIX_HOUR],
)
def test_every_other_granularity_is_refused_not_approximated(granularity: Granularity) -> None:
    """A venue that cannot serve a timeframe must say so (`ValueError` is the port's
    sanctioned refusal -- see `FakeAdapter.get_candles`), never silently substitute one."""
    with pytest.raises(ValueError, match="timeframe"):
        to_timeframe(granularity)


def test_market_quote_order_becomes_a_notional_market_order() -> None:
    """`MarketIOCByQuote` ("spend N USD") maps directly onto Alpaca's `notional` market
    order -- the PRD's FR-3 "Alpaca's notional market orders map directly".

    `notional` is documented to work ONLY with `type: market` and `time_in_force: day`,
    so all three are pinned together.
    """
    spec = MarketIOCByQuote(product_id="AAPL-USD", side=Side.BUY, quote_size=Decimal("100"))
    body = to_order_body(spec, client_order_id="c1")

    assert body["symbol"] == "AAPL"
    assert body["notional"] == "100"
    assert "qty" not in body
    assert body["side"] == "buy"
    assert body["type"] == "market"
    assert body["time_in_force"] == "day"
    assert body["client_order_id"] == "c1"
    assert body["extended_hours"] is False


def test_market_base_order_becomes_a_qty_market_order() -> None:
    """Fractional shares ride through `qty` unchanged: Alpaca accepts fractionable
    quantities, and a rounding step here would change the position size the caller asked for."""
    spec = MarketIOCByBase(product_id="AAPL-USD", side=Side.SELL, base_size=Decimal("0.7577533"))
    body = to_order_body(spec, client_order_id="c2")

    assert body["symbol"] == "AAPL"
    assert body["qty"] == "0.7577533"
    assert "notional" not in body
    assert body["side"] == "sell"
    assert body["type"] == "market"
    assert body["time_in_force"] == "day"
    assert body["extended_hours"] is False


def test_limit_order_is_gtc_with_qty_and_limit_price() -> None:
    spec = LimitGTC(
        product_id="AAPL-USD",
        side=Side.SELL,
        base_size=Decimal("0.5"),
        limit_price=Decimal("132.10"),
    )
    body = to_order_body(spec, client_order_id="c3")

    assert body["type"] == "limit"
    assert body["time_in_force"] == "gtc"
    assert body["qty"] == "0.5"
    assert body["limit_price"] == "132.10"
    assert body["extended_hours"] is False


def test_stop_limit_order_is_gtc_with_stop_and_limit_prices() -> None:
    spec = StopLimitGTC(
        product_id="AAPL-USD",
        side=Side.SELL,
        base_size=Decimal("0.5"),
        stop_price=Decimal("125.00"),
        limit_price=Decimal("124.75"),
    )
    body = to_order_body(spec, client_order_id="c4")

    assert body["type"] == "stop_limit"
    assert body["time_in_force"] == "gtc"
    assert body["qty"] == "0.5"
    assert body["stop_price"] == "125.00"
    assert body["limit_price"] == "124.75"
    assert body["extended_hours"] is False


def test_tiny_fractional_quantities_render_positionally_never_scientific() -> None:
    """`str(Decimal)` emits scientific notation at small magnitudes (`1E-8`), and an
    exponent in `qty`/`notional` is a malformed order body. `keel_broker_robinhood`'s
    `_render` documents the same failure; this pins it for fractional shares."""
    spec = MarketIOCByBase(product_id="AAPL-USD", side=Side.SELL, base_size=Decimal("0.00000001"))
    body = to_order_body(spec, client_order_id="c5")

    assert body["qty"] == "0.00000001"


def test_side_renders_lowercase() -> None:
    assert to_side(Side.BUY) == "buy"
    assert to_side(Side.SELL) == "sell"


@pytest.mark.parametrize(
    ("venue_status", "port_status"),
    [
        ("new", "OPEN"),
        ("accepted", "OPEN"),
        ("accepted_for_bidding", "OPEN"),
        ("partially_filled", "OPEN"),
        ("pending_new", "PENDING"),
        ("pending_cancel", "PENDING"),
        ("pending_replace", "PENDING"),
        ("done_for_day", "PENDING"),
        ("calculated", "PENDING"),
        ("held", "PENDING"),
        ("filled", "FILLED"),
        ("canceled", "CANCELLED"),
        ("expired", "EXPIRED"),
        ("replaced", "CANCELLED"),
        ("rejected", "FAILED"),
        ("stopped", "FAILED"),
        ("suspended", "FAILED"),
    ],
)
def test_alpaca_statuses_map_onto_the_port_vocabulary(
    venue_status: str, port_status: str
) -> None:
    """Alpaca's order-status enum (docs.alpaca.markets, "Order" schema) meets the port's
    vocabulary in exactly one place. `canceled` is the venue's single-`l` spelling; the
    port's is `CANCELLED` -- they must never be compared directly downstream."""
    assert STATE_TO_PORT_STATUS[venue_status] == port_status
    assert to_port_status(venue_status) == port_status


def test_an_unknown_status_is_pending_never_failed() -> None:
    """A status this table does not know means the adapter does not know the order's
    outcome. `PENDING` keeps reconciliation polling; `FAILED` would declare a terminal
    outcome nobody observed (the `keel_broker_robinhood.translate.to_port_status` rule)."""
    assert to_port_status("brand_new_status") == "PENDING"
    assert to_port_status(None) == "PENDING"


def test_rfc3339_and_epoch_round_trip() -> None:
    """Bar timestamps arrive RFC3339 (`t`) while `start`/`end` are sent RFC3339 from epoch
    seconds; both directions must be exact at second precision."""
    ts = int(datetime(2026, 8, 14, 14, 30, tzinfo=UTC).timestamp())
    assert to_rfc3339(ts) == "2026-08-14T14:30:00Z"
    assert to_unix_seconds("2026-08-14T14:30:00Z") == ts
    # Fractional seconds must truncate, not fail: Alpaca timestamps carry them.
    assert to_unix_seconds("2026-08-14T14:30:00.999Z") == ts
    # ...and an explicit offset must parse too, not only a trailing Z.
    assert to_unix_seconds("2026-08-14T10:30:00-04:00") == ts
