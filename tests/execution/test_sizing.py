"""Tests for keel.execution.sizing: fixed-fractional risk sizing + DCA sizing (P3 Task 2).

`size()` implements fixed-fractional position sizing: risk a fixed percentage of equity per
trade, with the stop distance (in price, never pips) determining quantity. `spend()` converts a
sized quantity back into notional dollars. `dca_size()` is the no-stop accumulation variant used
by the DCA rule: a fixed USD budget converted to quantity at the current price. All math is
`Decimal` throughout -- money/prices never touch `float`.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from keel.execution.sizing import dca_size, size, spend


def test_size_fixed_fractional_risk() -> None:
    # risk $100 (1% of $10,000 equity) over a $10 stop distance -> 10 units
    qty = size(
        equity=Decimal("10000"),
        risk_pct=Decimal("0.01"),
        entry=Decimal("100"),
        stop=Decimal("90"),
    )

    assert qty == Decimal("10")


def test_size_uses_absolute_stop_distance_regardless_of_direction() -> None:
    # short-style stop above entry: distance is still |entry - stop| = 10
    qty = size(
        equity=Decimal("10000"),
        risk_pct=Decimal("0.01"),
        entry=Decimal("100"),
        stop=Decimal("110"),
    )

    assert qty == Decimal("10")


def test_size_scales_with_risk_pct() -> None:
    qty = size(
        equity=Decimal("10000"),
        risk_pct=Decimal("0.02"),
        entry=Decimal("100"),
        stop=Decimal("90"),
    )

    assert qty == Decimal("20")


def test_size_zero_stop_distance_raises_value_error() -> None:
    with pytest.raises(ValueError, match="stop distance"):
        size(
            equity=Decimal("10000"),
            risk_pct=Decimal("0.01"),
            entry=Decimal("100"),
            stop=Decimal("100"),
        )


def test_spend_is_qty_times_entry() -> None:
    assert spend(qty=Decimal("10"), entry=Decimal("100")) == Decimal("1000")


def test_spend_fractional_qty() -> None:
    assert spend(qty=Decimal("0.5"), entry=Decimal("100")) == Decimal("50")


def test_dca_size_no_stop_accumulation() -> None:
    assert dca_size(budget_usd=Decimal("50"), entry=Decimal("100")) == Decimal("0.5")


def test_dca_size_full_budget_at_unit_price() -> None:
    assert dca_size(budget_usd=Decimal("100"), entry=Decimal("1")) == Decimal("100")


def test_dca_size_zero_entry_raises_value_error() -> None:
    with pytest.raises(ValueError, match="entry"):
        dca_size(budget_usd=Decimal("50"), entry=Decimal("0"))
