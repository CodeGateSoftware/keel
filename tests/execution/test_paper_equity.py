"""Tests for `keel.execution.equity.mark_positions`, the mark-to-market helper shared
between `PaperTrader.equity()` and the live agent's equity computation.
"""

from __future__ import annotations

from decimal import Decimal

from keel.execution.equity import mark_positions


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
