from __future__ import annotations

from decimal import Decimal

import pytest
from keel_broker_api.orders import (
    ORDER_KINDS,
    BracketGTC,
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
        BracketGTC.kind,
    }
    assert kinds == {
        "market_ioc_quote",
        "market_ioc_base",
        "limit_gtc",
        "stop_limit_gtc",
        "bracket_gtc",
    }


def test_order_kinds_lists_every_variant() -> None:
    """ORDER_KINDS is what capabilities are declared against -- it must not drift."""
    assert ORDER_KINDS == frozenset(
        {"market_ioc_quote", "market_ioc_base", "limit_gtc", "stop_limit_gtc", "bracket_gtc"}
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


def test_bracket_gtc_joins_the_sum_type() -> None:
    """A bracket is a kind of its own, not a `StopLimitGTC` with an extra price bolted on."""
    assert BracketGTC.kind == "bracket_gtc"
    assert BracketGTC.initial_status == "open"
    assert "bracket_gtc" in ORDER_KINDS


def test_bracket_gtc_carries_both_exit_prices_under_keels_own_names() -> None:
    """`take_profit_price`, not Coinbase's `limit_price`.

    The port's vocabulary is keel's, so a second venue's translation does not start from
    Coinbase's spelling -- and so the take-profit cannot be read as `LimitGTC.limit_price`,
    which is a different price on a different order.
    """
    spec = BracketGTC(
        product_id="BTC-USD",
        side=Side.SELL,
        base_size=Decimal("0.5"),
        take_profit_price=Decimal("70000"),
        stop_trigger_price=Decimal("60000"),
    )
    assert spec.take_profit_price == Decimal("70000")
    assert spec.stop_trigger_price == Decimal("60000")
    assert not hasattr(spec, "limit_price")


def test_bracket_gtc_has_no_stop_direction_field() -> None:
    """Direction is derivable from `side`, exactly as `StopLimitGTC`'s is.

    A field would make a SELL bracket that triggers UPWARD representable, which is the class of
    nonsense the sum type exists to prevent.
    """
    spec = BracketGTC(
        product_id="BTC-USD",
        side=Side.SELL,
        base_size=Decimal("0.5"),
        take_profit_price=Decimal("70000"),
        stop_trigger_price=Decimal("60000"),
    )
    assert not hasattr(spec, "stop_direction")


def test_bracket_gtc_rejects_non_positive_numerics() -> None:
    with pytest.raises(ValueError, match="base_size must be positive"):
        BracketGTC(
            product_id="BTC-USD",
            side=Side.SELL,
            base_size=Decimal("0"),
            take_profit_price=Decimal("70000"),
            stop_trigger_price=Decimal("60000"),
        )
    with pytest.raises(ValueError, match="take_profit_price must be positive"):
        BracketGTC(
            product_id="BTC-USD",
            side=Side.SELL,
            base_size=Decimal("1"),
            take_profit_price=Decimal("0"),
            stop_trigger_price=Decimal("60000"),
        )
    with pytest.raises(ValueError, match="stop_trigger_price must be positive"):
        BracketGTC(
            product_id="BTC-USD",
            side=Side.SELL,
            base_size=Decimal("1"),
            take_profit_price=Decimal("70000"),
            stop_trigger_price=Decimal("-1"),
        )


def test_sell_bracket_refuses_an_inverted_pair() -> None:
    """A SELL bracket exits a long: the stop is below, the target above."""
    with pytest.raises(ValueError, match="must be below take_profit_price"):
        BracketGTC(
            product_id="BTC-USD",
            side=Side.SELL,
            base_size=Decimal("1"),
            take_profit_price=Decimal("60000"),
            stop_trigger_price=Decimal("70000"),
        )


def test_sell_bracket_refuses_an_equal_pair() -> None:
    """Equal is not a degenerate bracket, it is a stop and a target racing at one price.

    Whichever the venue evaluates first decides whether the position took a profit or a loss --
    a coin flip dressed as a protective order. The inverted case at least looks wrong; this one
    reads as a valid pair of numbers, which is why it gets its own test.
    """
    with pytest.raises(ValueError, match="must be below take_profit_price"):
        BracketGTC(
            product_id="BTC-USD",
            side=Side.SELL,
            base_size=Decimal("1"),
            take_profit_price=Decimal("65000"),
            stop_trigger_price=Decimal("65000"),
        )


def test_buy_bracket_mirrors_the_sell_check() -> None:
    """A BUY bracket exits a short: the stop is above, the target below."""
    ok = BracketGTC(
        product_id="BTC-USD",
        side=Side.BUY,
        base_size=Decimal("1"),
        take_profit_price=Decimal("60000"),
        stop_trigger_price=Decimal("70000"),
    )
    assert ok.stop_trigger_price > ok.take_profit_price

    with pytest.raises(ValueError, match="must be above take_profit_price"):
        BracketGTC(
            product_id="BTC-USD",
            side=Side.BUY,
            base_size=Decimal("1"),
            take_profit_price=Decimal("70000"),
            stop_trigger_price=Decimal("60000"),
        )
    with pytest.raises(ValueError, match="must be above take_profit_price"):
        BracketGTC(
            product_id="BTC-USD",
            side=Side.BUY,
            base_size=Decimal("1"),
            take_profit_price=Decimal("65000"),
            stop_trigger_price=Decimal("65000"),
        )
