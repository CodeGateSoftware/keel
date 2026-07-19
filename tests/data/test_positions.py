"""Tests for the per-tranche position ledger.

`agent_state["position_rule:<product>"]` carried entry context as one JSON blob keyed by
PRODUCT, so it was last-write-wins: a second entry into the same product overwrote the first's
entry price, qty and fee. A bracket belonging to the FIRST tranche filling later then computed
its P&L against the SECOND tranche's entry -- booking a loss that never happened and feeding it
to `trade_outcomes` and rail 16's live-money breaker. This table is one row per tranche.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from keel.data.db import connect, migrate
from keel.data.repository import Repository

PRODUCT = "BTC-USD"


@pytest.fixture
def repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    return Repository(conn)


def _order(repo: Repository, product_id: str = PRODUCT) -> int:
    """A real resting SELL row to hang a tranche's `bracket_order_id` on.

    `positions.bracket_order_id` carries a FOREIGN KEY to `orders(id)`, so a tranche cannot name
    an order that does not exist -- the database refuses the orphaned linkage rather than letting
    it surface later as a silently dropped outcome. Tests therefore use real ids, not invented
    ones: a linkage that could never exist in production should not be assertable here either.
    """
    return repo.insert_order(
        dict(mode="live", product_id=product_id, side="SELL", order_type="market",
             qty=Decimal("1"), limit_price=None, status="pending", fee=None,
             expected_fill=Decimal("1"), actual_fill=None, created_at=0, updated_at=0)
    )


def test_two_tranches_in_one_product_are_separately_addressable(repo: Repository) -> None:
    """The whole point. `position_rule:<product>` was last-write-wins, so a second entry
    overwrote the first's entry price and qty -- and a bracket from the FIRST tranche filling
    later computed its P&L against the SECOND tranche's entry. That inflated loss fed rail 16's
    counter and the trade_outcomes ledger."""
    o1, o2 = _order(repo), _order(repo)
    a = repo.open_position(product_id=PRODUCT, rule_name="turtle_breakout", opened_at=1_000,
                           qty=Decimal("0.01"), entry_fill=Decimal("50000"),
                           entry_fee=Decimal("3"), bracket_order_id=o1)
    b = repo.open_position(product_id=PRODUCT, rule_name="turtle_breakout", opened_at=2_000,
                           qty=Decimal("0.01"), entry_fill=Decimal("52000"),
                           entry_fee=Decimal("3.1"), bracket_order_id=o2)

    assert a != b
    rows = repo.get_open_positions(PRODUCT)
    assert [r["entry_fill"] for r in rows] == [Decimal("50000"), Decimal("52000")]
    assert [r["bracket_order_id"] for r in rows] == [o1, o2]


def test_money_round_trips_as_decimal_and_ids_stay_int(repo: Repository) -> None:
    """Money is TEXT-encoded and must come back `Decimal`; ids are INTEGER and must NOT go
    through the money decoder, or `bracket_order_id` reads back as `Decimal("11")` and every
    identity comparison against an `orders.id` silently fails."""
    o = _order(repo)
    repo.open_position(product_id=PRODUCT, rule_name="dca", opened_at=1_000,
                       qty=Decimal("0.005"), entry_fill=Decimal("50000.123456789"),
                       entry_fee=Decimal("0.30"), bracket_order_id=o)

    row = repo.get_open_positions(PRODUCT)[0]
    assert row["entry_fill"] == Decimal("50000.123456789")   # exact, no float rounding
    assert isinstance(row["qty"], Decimal)
    assert isinstance(row["entry_fee"], Decimal)
    assert isinstance(row["bracket_order_id"], int)
    assert isinstance(row["id"], int)
    assert isinstance(row["opened_at"], int)


def test_get_open_positions_is_oldest_first(repo: Repository) -> None:
    """FIFO is the attribution order a later exit uses, so the order is part of the contract."""
    second = repo.open_position(product_id=PRODUCT, rule_name="r", opened_at=9_000,
                                qty=Decimal("1"), entry_fill=Decimal("2"),
                                entry_fee=Decimal("0"))
    first = repo.open_position(product_id=PRODUCT, rule_name="r", opened_at=1_000,
                               qty=Decimal("1"), entry_fill=Decimal("1"),
                               entry_fee=Decimal("0"))

    assert [r["id"] for r in repo.get_open_positions(PRODUCT)] == [first, second]


def test_a_closed_tranche_leaves_the_open_set(repo: Repository) -> None:
    a = repo.open_position(product_id=PRODUCT, rule_name="r", opened_at=1_000,
                           qty=Decimal("1"), entry_fill=Decimal("1"), entry_fee=Decimal("0"))
    b = repo.open_position(product_id=PRODUCT, rule_name="r", opened_at=2_000,
                           qty=Decimal("1"), entry_fill=Decimal("2"), entry_fee=Decimal("0"))

    repo.close_position(a, closed_at=5_000)

    assert [r["id"] for r in repo.get_open_positions(PRODUCT)] == [b]


def test_a_bracket_resolves_to_exactly_the_tranche_that_owns_it(repo: Repository) -> None:
    """The lookup reconciliation uses: it starts from a filled ORDER row and needs the tranche
    that owns it. A closed tranche must not answer -- its bracket id is history, and matching it
    would attribute a new fill to a trade already booked."""
    o1, o2, gone = _order(repo), _order(repo), _order(repo)
    a = repo.open_position(product_id=PRODUCT, rule_name="r", opened_at=1_000,
                           qty=Decimal("1"), entry_fill=Decimal("1"), entry_fee=Decimal("0"),
                           bracket_order_id=o1)
    repo.open_position(product_id=PRODUCT, rule_name="r", opened_at=2_000,
                       qty=Decimal("1"), entry_fill=Decimal("2"), entry_fee=Decimal("0"),
                       bracket_order_id=o2)

    assert repo.get_position_for_bracket(o1)["id"] == a
    assert repo.get_position_for_bracket(gone) is None

    repo.close_position(a, closed_at=5_000)
    assert repo.get_position_for_bracket(o1) is None


def test_set_position_bracket_repoints_a_tranche(repo: Repository) -> None:
    """A re-placed bracket must re-point its tranche. Without this the tranche keeps naming the
    DEAD order, so when the replacement fills `get_position_for_bracket` finds nothing and the
    outcome is skipped -- the position closes with no `trade_outcomes` row at all."""
    dead, replacement = _order(repo), _order(repo)
    a = repo.open_position(product_id=PRODUCT, rule_name="r", opened_at=1_000,
                           qty=Decimal("1"), entry_fill=Decimal("1"), entry_fee=Decimal("0"),
                           bracket_order_id=dead)

    repo.set_position_bracket(a, replacement)

    assert repo.get_position_for_bracket(dead) is None
    assert repo.get_position_for_bracket(replacement)["id"] == a


def test_get_open_positions_without_a_product_spans_every_product(repo: Repository) -> None:
    repo.open_position(product_id="BTC-USD", rule_name="r", opened_at=1_000,
                       qty=Decimal("1"), entry_fill=Decimal("1"), entry_fee=Decimal("0"))
    repo.open_position(product_id="ETH-USD", rule_name="r", opened_at=2_000,
                       qty=Decimal("1"), entry_fill=Decimal("2"), entry_fee=Decimal("0"))

    assert len(repo.get_open_positions()) == 2
    assert len(repo.get_open_positions("BTC-USD")) == 1
