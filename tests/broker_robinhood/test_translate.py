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


#: Decimal inputs paired with the exact positional text Robinhood's JSON order body must carry.
#:
#: `str(Decimal)` is NOT a positional renderer. It switches to scientific notation whenever the
#: value's adjusted exponent leaves a narrow band, so `str(Decimal("0.00000001"))` is `"1E-8"` --
#: and Robinhood's order body has no exponent form. This is not a contrived edge case: BTC's
#: `asset_increment` is exactly `0.00000001` (see `tests/fixtures/rh_trading_pairs.json`), which
#: makes one satoshi the SMALLEST ORDER THIS VENUE ACCEPTS and therefore a size a dust-sized exit
#: genuinely produces. `format(d, "f")` renders positionally at every magnitude.
#:
#: The first three entries all render wrongly under `str()`; the last two already rendered
#: correctly and are kept so this parametrisation also proves the fix does not perturb the
#: ordinary case.
_EXPONENT_HAZARDS: list[tuple[str, str]] = [
    # One satoshi -- the venue's own minimum increment for BTC. `str()` gives "1E-8".
    ("0.00000001", "0.00000001"),
    # Sub-satoshi precision, to prove the fix is about the exponent form and not about a single
    # magic value. `str()` gives "1.2345E-8".
    ("0.000000012345", "0.000000012345"),
    # A value carrying a POSITIVE exponent. `Decimal("1E+2")` is 100, and `str()` gives "1E+2".
    # Nobody writes this literal, but arithmetic inside the engine -- a size divided by a price
    # and re-multiplied, say -- produces exactly this shape without anyone intending it.
    ("1E+2", "100"),
    ("0.123456789", "0.123456789"),
    ("64000.10", "64000.10"),
]


def _rendered_values(body: dict[str, object]) -> list[str]:
    """Every string leaf inside `body`'s `*_order_config` block.

    Reaching into the config block rather than naming fields one at a time means a money or size
    field added to a body later is covered by the exponent assertion below automatically, instead
    of silently escaping it until a dust-sized order is rejected on the live-money path.
    """
    config = next(v for k, v in body.items() if k.endswith("_order_config"))
    assert isinstance(config, dict)
    return [v for v in config.values() if isinstance(v, str)]


@pytest.mark.parametrize(("raw", "expected"), _EXPONENT_HAZARDS)
def test_market_order_size_renders_positionally_never_in_scientific_notation(
    raw: str, expected: str
) -> None:
    """A size rendered as `"1E-8"` is a malformed order body, not a rounding nuisance.

    Robinhood's `asset_quantity` is parsed as a decimal string with no exponent form. A dust-sized
    exit -- one satoshi, which is exactly BTC's `asset_increment` -- would go out as `"1E-8"` and
    be rejected, and a rejected EXIT is a position that stays open when the engine believes it
    closed. `str()` cannot be trusted to render a `Decimal` positionally; `format(d, "f")` can.
    """
    spec = MarketIOCByBase(product_id="BTC-USD", side=Side.SELL, base_size=Decimal(raw))
    body = to_order_body(spec, client_order_id="c1")

    assert body["market_order_config"] == {"asset_quantity": expected}


@pytest.mark.parametrize(("raw", "expected"), _EXPONENT_HAZARDS)
def test_limit_order_size_and_price_render_positionally(raw: str, expected: str) -> None:
    """Both the size and the limit price ride through the same renderer, so both must be pinned.

    A limit PRICE in exponent form is the more dangerous of the two: rejected outright it is
    merely a failed take-profit, but it is also the field a venue is most likely to parse
    leniently and differently than intended."""
    spec = LimitGTC(
        product_id="BTC-USD",
        side=Side.BUY,
        base_size=Decimal(raw),
        limit_price=Decimal(raw),
    )
    body = to_order_body(spec, client_order_id="c1")

    assert body["limit_order_config"] == {
        "asset_quantity": expected,
        "limit_price": expected,
        "time_in_force": "gtc",
    }


@pytest.mark.parametrize(("raw", "expected"), _EXPONENT_HAZARDS)
def test_stop_limit_order_size_price_and_stop_render_positionally(
    raw: str, expected: str
) -> None:
    """The protective-stop path, which is the one that must never be malformed.

    A stop-limit rejected for a malformed `stop_price` leaves a position with NO protective stop
    at the venue while the engine's local state records one. That is the worst shape this bug can
    take, so the stop leg gets its own assertion rather than riding on the limit test."""
    spec = StopLimitGTC(
        product_id="BTC-USD",
        side=Side.SELL,
        base_size=Decimal(raw),
        stop_price=Decimal(raw),
        limit_price=Decimal(raw),
    )
    body = to_order_body(spec, client_order_id="c1")

    assert body["stop_limit_order_config"] == {
        "asset_quantity": expected,
        "limit_price": expected,
        "stop_price": expected,
        "time_in_force": "gtc",
    }


@pytest.mark.parametrize(("raw", "_expected"), _EXPONENT_HAZARDS)
def test_no_rendered_order_field_ever_contains_an_exponent_marker(
    raw: str, _expected: str
) -> None:
    """The property itself, asserted structurally across all three body shapes.

    The tests above pin exact strings, which is what catches a regression precisely. This one
    catches a money or size field added to a body LATER that quietly reintroduces `str()` -- it
    reads every string leaf of the config block and refuses any `e`/`E`, which no legitimate
    positional decimal rendering ever contains.
    """
    value = Decimal(raw)
    specs = [
        MarketIOCByBase(product_id="BTC-USD", side=Side.SELL, base_size=value),
        LimitGTC(
            product_id="BTC-USD", side=Side.BUY, base_size=value, limit_price=value
        ),
        StopLimitGTC(
            product_id="BTC-USD",
            side=Side.SELL,
            base_size=value,
            stop_price=value,
            limit_price=value,
        ),
    ]
    for spec in specs:
        for rendered in _rendered_values(to_order_body(spec, client_order_id="c1")):
            assert "e" not in rendered.lower(), (
                f"{spec.kind} rendered {rendered!r} with an exponent"
            )


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
