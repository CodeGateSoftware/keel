"""Tests for `keel.execution.equity.mark_positions`, the mark-to-market helper shared
between `PaperTrader.equity()` and the live agent's equity computation.
"""

from __future__ import annotations

from decimal import Decimal

from keel.execution.equity import mark_positions, unrealized_on_marks


def test_mark_positions_uses_fresh_price():
    eq = mark_positions(
        cash=Decimal("1000"),
        positions=[(Decimal("2"), Decimal("100"))],  # qty=2, cost basis 100
        price_by_product={"BTC-USD": Decimal("150")},
        product_ids=["BTC-USD"],
    )
    assert eq == Decimal("1000") + Decimal("2") * Decimal("150")


def test_mark_positions_falls_back_to_cost_basis_when_price_missing():
    eq = mark_positions(
        cash=Decimal("1000"),
        positions=[(Decimal("2"), Decimal("100"))],
        price_by_product={},  # no fresh price
        product_ids=["BTC-USD"],
    )
    assert eq == Decimal("1000") + Decimal("2") * Decimal("100")  # cost-basis fallback


def test_mark_positions_falls_back_when_price_is_non_positive():
    eq = mark_positions(
        cash=Decimal("1000"),
        positions=[(Decimal("2"), Decimal("100"))],
        price_by_product={"BTC-USD": Decimal("0")},
        product_ids=["BTC-USD"],
    )
    assert eq == Decimal("1000") + Decimal("2") * Decimal("100")


def test_mark_positions_skips_non_positive_qty():
    eq = mark_positions(
        cash=Decimal("1000"),
        positions=[(Decimal("0"), Decimal("100"))],
        price_by_product={"BTC-USD": Decimal("150")},
        product_ids=["BTC-USD"],
    )
    assert eq == Decimal("1000")


# -- the unrealized leg of the same reading (#698) --------------------------------------------
#
# `equity_points.unrealized` is written from here, so it MUST answer the marks `mark_positions`
# used for the SAME cycle: two helpers reading one set of positions under different fallback
# rules would file an equity and a P&L that cannot both be true.


def test_unrealized_is_the_gain_over_cost_at_the_fresh_price():
    pnl = unrealized_on_marks(
        positions=[(Decimal("2"), Decimal("100"))],
        price_by_product={"BTC-USD": Decimal("150")},
        product_ids=["BTC-USD"],
    )
    assert pnl == Decimal("2") * (Decimal("150") - Decimal("100"))


def test_unrealized_is_zero_when_the_price_is_missing():
    """The cost-basis fallback, restated in P&L terms: `mark_positions` values that position AT
    cost, so the only unrealized figure consistent with the equity it just reported is zero.
    Anything else books a gain against a price nobody observed."""
    pnl = unrealized_on_marks(
        positions=[(Decimal("2"), Decimal("100"))],
        price_by_product={},
        product_ids=["BTC-USD"],
    )
    assert pnl == Decimal("0")


def test_unrealized_is_zero_when_the_price_is_non_positive():
    pnl = unrealized_on_marks(
        positions=[(Decimal("2"), Decimal("100"))],
        price_by_product={"BTC-USD": Decimal("0")},
        product_ids=["BTC-USD"],
    )
    assert pnl == Decimal("0")


def test_unrealized_skips_non_positive_qty():
    pnl = unrealized_on_marks(
        positions=[(Decimal("0"), Decimal("100"))],
        price_by_product={"BTC-USD": Decimal("150")},
        product_ids=["BTC-USD"],
    )
    assert pnl == Decimal("0")


def test_unrealized_is_negative_on_a_losing_position():
    pnl = unrealized_on_marks(
        positions=[(Decimal("2"), Decimal("100"))],
        price_by_product={"BTC-USD": Decimal("90")},
        product_ids=["BTC-USD"],
    )
    assert pnl == Decimal("-20")


def test_a_zero_cost_basis_position_is_all_unrealized_gain():
    """`mark_positions` values a fresh-priced position at `qty * mark` regardless of what it
    cost, so a zero-basis holding (an airdrop, a migrated row with no recorded fill) is entirely
    unrealized gain. Skipping it here would leave `cash + cost + unrealized` short of the equity
    reported for the same cycle."""
    pnl = unrealized_on_marks(
        positions=[(Decimal("2"), Decimal("0"))],
        price_by_product={"BTC-USD": Decimal("150")},
        product_ids=["BTC-USD"],
    )
    assert pnl == Decimal("300")


def test_cash_plus_cost_basis_plus_unrealized_reconstructs_the_equity():
    """The invariant that makes three columns one reading rather than three: a row whose parts
    do not add back to `equity` cannot be reconciled, and the chart's whole claim is that these
    are the numbers the engine acted on. The second position is deliberately unpriced, so the
    identity is checked across BOTH the fresh-price and the fallback leg."""
    positions = [(Decimal("2"), Decimal("100")), (Decimal("5"), Decimal("20"))]
    products = ["BTC-USD", "ETH-USD"]
    prices = {"BTC-USD": Decimal("150")}
    cash = Decimal("1000")

    equity = mark_positions(cash, positions, prices, products)
    unrealized = unrealized_on_marks(positions, prices, products)
    cost_basis_total = sum((qty * basis for qty, basis in positions), Decimal("0"))

    assert cash + cost_basis_total + unrealized == equity


def test_mark_positions_with_no_positions_returns_cash():
    eq = mark_positions(cash=Decimal("1000"), positions=[], price_by_product={}, product_ids=[])
    assert eq == Decimal("1000")


def test_mark_positions_sums_multiple_products():
    eq = mark_positions(
        cash=Decimal("1000"),
        positions=[(Decimal("2"), Decimal("100")), (Decimal("1"), Decimal("50"))],
        price_by_product={"BTC-USD": Decimal("150")},  # ETH-USD missing -> cost-basis fallback
        product_ids=["BTC-USD", "ETH-USD"],
    )
    assert eq == Decimal("1000") + Decimal("2") * Decimal("150") + Decimal("1") * Decimal("50")
