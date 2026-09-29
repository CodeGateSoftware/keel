"""`keel.strategy.reduction`: the sell side's pure value types (spec §3.2, §3.3; plan R6-R8)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from keel.strategy.reduction import Holding, Lot, Reduction, SellCosts

D = Decimal


def _lot(pid, qty, fill, fee, realized="0", rule="dca", opened=0):
    return Lot(pid, rule, opened, D(qty), D(fill), D(fee), D(realized))


def test_vwae_includes_entry_fees_so_break_even_is_honest() -> None:
    """Spec Q4: 0.0005 BTC @ 100000 with a $0.45 fee costs $50.45, so break-even is 100900."""
    h = Holding("BTC-USD", (_lot(1, "0.0005", "100000", "0.45"),))
    assert h.cost_basis == D("50.45")
    assert h.vwae == D("100900")


def test_a_partly_scaled_out_tranche_prorates_its_entry_fee() -> None:
    """qty is what is STILL held; qty + realized_qty is the original size (#502)."""
    lot = _lot(1, "0.0003", "100000", "0.50", realized="0.0002")
    assert lot.entry_fee_share == D("0.30")
    assert lot.cost == D("30.30")


def test_fifo_legs_consume_the_oldest_lot_first_and_stop_inside_one() -> None:
    old = _lot(3, "0.0132", "4673.23", "0.73", rule="turtle_breakout")
    new = _lot(9, "0.01", "4400", "0.4")
    legs = Holding("PAXG-USD", (old, new)).fifo_legs(D("0.015"))
    assert [(lot.position_id, q) for lot, q in legs] == [(3, D("0.0132")), (9, D("0.0018"))]


def test_fifo_legs_never_exceed_what_is_held() -> None:
    h = Holding("BTC-USD", (_lot(1, "0.001", "100000", "0"),))
    legs = h.fifo_legs(D("5"))
    assert [(lot.position_id, q) for lot, q in legs] == [(1, D("0.001"))]


def test_fifo_legs_stop_once_the_sale_is_satisfied_inside_the_first_lot() -> None:
    """A sale that never reaches the second lot must not add a `(lot, 0)` leg for it."""
    first = _lot(1, "0.01", "100000", "0")
    second = _lot(2, "0.01", "110000", "0")
    legs = Holding("BTC-USD", (first, second)).fifo_legs(D("0.004"))
    assert [(lot.position_id, q) for lot, q in legs] == [(1, D("0.004"))]


def test_fifo_cost_prorates_each_consumed_lot() -> None:
    h = Holding("BTC-USD", (_lot(1, "0.001", "100000", "1.00"), _lot(2, "0.001", "110000", "1.10")))
    assert h.fifo_cost(D("0.0015")) == D("100") + D("1.00") + D("55") + D("0.55")


def test_a_zero_qty_lot_has_no_entry_fee_share_or_cost() -> None:
    """`entry_fee_share`'s `original <= 0` guard: a lot with nothing held and nothing realized
    would otherwise divide by zero."""
    lot = _lot(1, "0", "100000", "0.50", realized="0")
    assert lot.entry_fee_share == D("0")
    assert lot.cost == D("0")


def test_fifo_cost_skips_a_zero_qty_lot_before_and_between_real_lots() -> None:
    """`fifo_cost`'s `if lot.qty > 0` filter: a zero-qty lot always takes 0, and without the
    filter `entry_fee_share * take / lot.qty` would divide zero by zero."""
    lots = (
        _lot(0, "0", "0", "0"),
        _lot(1, "0.001", "100000", "1.00"),
        _lot(4, "0", "0", "0"),
        _lot(2, "0.001", "110000", "1.10"),
    )
    h = Holding("BTC-USD", lots)
    assert h.fifo_cost(D("0.0015")) == D("100") + D("1.00") + D("55") + D("0.55")


def test_an_empty_holding_has_no_average_rather_than_a_zero_one() -> None:
    assert Holding("BTC-USD", ()).vwae is None
    assert Holding("BTC-USD", (), mark=D("1")).unrealised == D("0")
    assert Holding("BTC-USD", (_lot(1, "1", "1", "0"),)).unrealised is None


def test_unrealised_is_marked_value_less_the_fee_inclusive_basis() -> None:
    h = Holding("BTC-USD", (_lot(1, "0.0005", "100000", "0.45"),), mark=D("120000"))
    assert h.unrealised == D("60") - D("50.45")


def test_from_rows_keeps_the_ledger_order_and_decodes_every_field() -> None:
    """`get_open_positions` is FIFO by contract and `book_exit` consumes in that order, so
    `from_rows` must not re-sort -- here the rows arrive with the LATER `opened_at` first."""
    rows = [
        {
            "id": 7,
            "rule_name": "turtle_breakout",
            "opened_at": 20,
            "qty": D("0.2"),
            "entry_fill": D("50"),
            "entry_fee": D("0.1"),
            "realized_qty": D("0.1"),
        },
        {
            "id": 2,
            "rule_name": "dca",
            "opened_at": 10,
            "qty": D("0.3"),
            "entry_fill": D("40"),
            "entry_fee": None,
            "realized_qty": None,
        },
    ]
    h = Holding.from_rows("PAXG-USD", rows, mark=D("45"))
    assert h.lots == (
        Lot(7, "turtle_breakout", 20, D("0.2"), D("50"), D("0.1"), D("0.1")),
        Lot(2, "dca", 10, D("0.3"), D("40"), D("0"), D("0")),
    )
    assert h.product_id == "PAXG-USD"
    assert h.mark == D("45")


@pytest.mark.parametrize("qty,price", [(D("0"), D("1")), (D("-1"), D("1")), (D("1"), D("0"))])
def test_a_reduction_refuses_a_non_positive_size_or_price(qty, price) -> None:
    with pytest.raises(ValueError):
        Reduction("BTC-USD", qty, "reverse_dca", {}, price, 0)


def test_a_reduction_must_name_its_rule_kind() -> None:
    with pytest.raises(ValueError):
        Reduction("BTC-USD", D("1"), "", {}, D("1"), 0)


@pytest.mark.parametrize(
    "fee,slip",
    [(D("-0.01"), D("0")), (D("0"), D("-0.01")), (D("0.6"), D("0.5")), (D("0.5"), D("0.5"))],
    ids=["negative-fee", "negative-slippage", "over-one", "exactly-one"],
)
def test_sell_costs_refuse_rates_that_make_net_from_gross_undefined(fee, slip) -> None:
    with pytest.raises(ValueError):
        SellCosts(fee, slip, "fallback:config.fees.taker_pct")


def test_sell_costs_accept_the_fallback_rate() -> None:
    costs = SellCosts(D("0.012"), D("0.0005"), "fallback:config.fees.taker_pct")
    assert (costs.fee_pct, costs.slippage_pct) == (D("0.012"), D("0.0005"))
