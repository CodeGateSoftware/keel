"""`keel.execution.sleeve`: the ledger-side quantities `ledger.drift` compares (#799, plan R2)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.execution import sleeve
from keel.types import Side


def test_ledger_qty_sums_what_is_still_held() -> None:
    rows = [{"qty": Decimal("0.0005")}, {"qty": Decimal("0.00049")}]
    assert sleeve.ledger_qty(rows) == Decimal("0.00099")


def test_ledger_qty_of_nothing_is_zero_not_none() -> None:
    assert sleeve.ledger_qty([]) == Decimal("0")


def _repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    return Repository(conn)


def _order(*, mode: str, side: str, qty: Decimal, status: str = "filled") -> dict[str, Any]:
    return dict(
        mode=mode,
        product_id="BTC-USD",
        side=side,
        order_type="market",
        qty=qty,
        limit_price=Decimal("100000"),
        status=status,
        fee=Decimal("0"),
        expected_fill=Decimal("100000"),
        actual_fill=Decimal("100000"),
        created_at=0,
        updated_at=0,
    )


def test_orders_qty_reads_the_requested_mode_only() -> None:
    """#881: on a paper profile every fill is `mode='paper'`. `orders_qty` must be told which
    mode to read, unlike `executor._held_position`, which hardcodes `mode='live'` on purpose."""
    repo = _repo()
    repo.insert_order(_order(mode="paper", side=Side.BUY.value, qty=Decimal("0.001")))
    repo.insert_order(_order(mode="live", side=Side.BUY.value, qty=Decimal("5")))
    assert sleeve.orders_qty(repo, "BTC-USD", mode="paper") == Decimal("0.001")
    assert sleeve.orders_qty(repo, "BTC-USD", mode="live") == Decimal("5")


def test_orders_qty_nets_sells_and_floors_at_zero() -> None:
    repo = _repo()
    repo.insert_order(_order(mode="paper", side=Side.BUY.value, qty=Decimal("0.001")))
    repo.insert_order(_order(mode="paper", side=Side.SELL.value, qty=Decimal("0.002")))
    assert sleeve.orders_qty(repo, "BTC-USD", mode="paper") == Decimal("0")


def test_orders_qty_counts_what_the_venue_delivered_when_it_said() -> None:
    """#900: `filled_quantity` before `qty`, on both sides -- the reading R33
    (`guards._open_exposure_by_asset`) and `executor._held_position` use. A quote-sized BUY
    ordered 0.001 and delivered 0.00099 (fee out of the quote); a row the venue never sized
    counts its ordered `qty`."""
    repo = _repo()
    repo.insert_order(
        _order(mode="live", side=Side.BUY.value, qty=Decimal("0.001"))
        | {"filled_quantity": Decimal("0.00099")}
    )
    repo.insert_order(_order(mode="live", side=Side.BUY.value, qty=Decimal("0.002")))
    repo.insert_order(
        _order(mode="live", side=Side.SELL.value, qty=Decimal("0.0005"))
        | {"filled_quantity": Decimal("0.0004")}
    )
    assert sleeve.orders_qty(repo, "BTC-USD", mode="live") == Decimal("0.00259")


def test_orders_qty_counts_filled_rows_only() -> None:
    """A `pending` BUY is not a holding -- the same filter `_held_position` applies."""
    repo = _repo()
    repo.insert_order(_order(mode="live", side=Side.BUY.value, qty=Decimal("0.001")))
    repo.insert_order(_order(mode="live", side=Side.BUY.value, qty=Decimal("7"), status="pending"))
    assert sleeve.orders_qty(repo, "BTC-USD", mode="live") == Decimal("0.001")


# --- holding_of: the Holding over the positions ledger (#857, plan Task 5.4) ----------------


def test_holding_of_is_the_ledger_and_its_qty_is_doctors_ledger_qty() -> None:
    """Every OPEN tranche of the product, no rule filter (R8), oldest first -- the turtle row is
    inserted first but opened later, so the order is the ledger's FIFO, not insertion order. A
    closed tranche and another product's tranche are not part of it."""
    repo = _repo()
    turtle = repo.open_position(
        product_id="BTC-USD",
        rule_name="turtle_breakout",
        opened_at=2,
        qty=Decimal("0.0004"),
        entry_fill=Decimal("110000"),
        entry_fee=Decimal("0.5"),
    )
    dca = repo.open_position(
        product_id="BTC-USD",
        rule_name="dca",
        opened_at=1,
        qty=Decimal("0.0005"),
        entry_fill=Decimal("100000"),
        entry_fee=Decimal("0.45"),
    )
    closed = repo.open_position(
        product_id="BTC-USD",
        rule_name="dca",
        opened_at=0,
        qty=Decimal("1"),
        entry_fill=Decimal("1"),
        entry_fee=Decimal("0"),
    )
    repo.close_position(closed, closed_at=3)
    repo.open_position(
        product_id="PAXG-USD",
        rule_name="dca",
        opened_at=1,
        qty=Decimal("1"),
        entry_fill=Decimal("4000"),
        entry_fee=Decimal("0"),
    )

    held = sleeve.holding_of(repo, "BTC-USD", mark=Decimal("120000"))

    assert [(lot.position_id, lot.rule_name) for lot in held.lots] == [
        (dca, "dca"),
        (turtle, "turtle_breakout"),
    ]
    assert held.qty == sleeve.ledger_qty(repo.get_open_positions("BTC-USD"))
    assert held.qty == Decimal("0.0009")
    assert (held.product_id, held.mark) == ("BTC-USD", Decimal("120000"))


def test_holding_of_a_product_with_no_open_tranche_is_empty() -> None:
    held = sleeve.holding_of(_repo(), "BTC-USD")
    assert (held.lots, held.qty, held.vwae, held.mark) == ((), Decimal("0"), None, None)
