"""`sell_proposals` reads and writes (#857, spec §3.8; plan P6 Task 6.2).

A proposal is an audit record of what a sleeve-sell rule asked for, so every write is chained
(`sell_proposal_recorded` / `sell_proposal_updated`, the chain's `<store>_<verb>` vocabulary) in
the same transaction as the row. Readers hand back money as `Decimal` and `trigger`/`rails` as
dicts, newest first, and an unmigrated database reads as "no proposals" rather than raising.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from keel.data import audit
from keel.data.db import connect, migrate
from keel.data.repository import Repository


def _repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    return Repository(conn)


def _row(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        ts=100,
        product_id="BTC-USD",
        rule_id=7,
        rule_kind="reverse_dca",
        rule_status="live",
        qty=Decimal("0.00092"),
        expected_price=Decimal("110000"),
        vwae=Decimal("100900"),
        cost_basis=Decimal("92.83"),
        expected_gross=Decimal("101.15"),
        expected_fee=Decimal("1.21"),
        fee_source="venue_preview",
        expected_net_pnl=Decimal("7.11"),
        legs=1,
        trigger={"cadence_day": 20_000},
        rails={"violations": [], "skipped": []},
        decision="preview",
    )
    base.update(over)
    return base


_MONEY = (
    "qty",
    "expected_price",
    "vwae",
    "cost_basis",
    "expected_gross",
    "expected_fee",
    "expected_net_pnl",
)


def test_a_proposal_round_trips_with_money_as_decimal_and_json_as_dict() -> None:
    repo = _repo()
    pid = repo.insert_sell_proposal(_row())
    got = repo.get_sell_proposal(pid)
    assert got is not None
    assert got["id"] == pid
    for field in _MONEY:
        assert isinstance(got[field], Decimal), field
        assert got[field] == _row()[field], field
    assert got["trigger"] == {"cadence_day": 20_000}
    assert got["rails"] == {"violations": [], "skipped": []}
    assert (got["legs"], got["decision"], got["rule_id"]) == (1, "preview", 7)
    assert got["reviewed_ts"] is None and got["order_id"] is None
    assert got["superseded_by"] is None


def test_money_is_stored_as_its_exact_text() -> None:
    """TEXT holding `str(Decimal)`, the schema's money convention -- never a float."""
    repo = _repo()
    pid = repo.insert_sell_proposal(_row(qty=Decimal("0.000920")))
    raw = repo._conn.execute(
        "SELECT qty, typeof(qty) AS t FROM sell_proposals WHERE id = ?", (pid,)
    )  # noqa: SLF001
    row = raw.fetchone()
    assert (row["qty"], row["t"]) == ("0.000920", "text")


def test_an_unrecorded_figure_reads_back_as_None_not_zero() -> None:
    repo = _repo()
    pid = repo.insert_sell_proposal(_row(vwae=None, cost_basis=None, expected_net_pnl=None))
    got = repo.get_sell_proposal(pid)
    assert got is not None
    assert (got["vwae"], got["cost_basis"], got["expected_net_pnl"]) == (None, None, None)


def test_every_write_is_chained_in_the_audit_log() -> None:
    repo = _repo()
    pid = repo.insert_sell_proposal(_row())
    repo.update_sell_proposal(pid, reviewed_ts=200)
    conn = repo._conn  # noqa: SLF001 - the chain is read by store, not by repository method
    events = [e for e in audit.read_events(conn) if e.entity_id == str(pid)]
    assert [e.event_type for e in events] == ["sell_proposal_recorded", "sell_proposal_updated"]
    recorded, updated = events
    assert recorded.ts == 100
    assert recorded.payload["id"] == pid and recorded.payload["expected_fee"] == "1.21"
    assert recorded.payload["decision"] == "preview"
    assert updated.payload == {"reviewed_ts": 200}
    state = audit.chain_state(conn)
    assert state.errors == ()
    # Filed under its own store, so a proposal id never hands its hash to an `orders.id`.
    assert state.hashes[("sell_proposals", str(pid))].seq_id == updated.seq_id


def test_an_update_lands_on_the_row_with_its_money_encoded() -> None:
    repo = _repo()
    pid = repo.insert_sell_proposal(_row())
    other = repo.insert_sell_proposal(_row(ts=101))
    repo.update_sell_proposal(
        pid,
        decision="vetoed",
        expected_fee=Decimal("1.30"),
        rails={"violations": ["kill_switch: engaged"]},
    )
    got = repo.get_sell_proposal(pid)
    assert got is not None
    assert got["decision"] == "vetoed" and got["expected_fee"] == Decimal("1.30")
    assert got["rails"] == {"violations": ["kill_switch: engaged"]}
    untouched = repo.get_sell_proposal(other)
    assert untouched is not None and untouched["decision"] == "preview"


def test_an_empty_update_writes_no_event() -> None:
    """An event asserting nothing changed is noise in the one record that must not have any."""
    repo = _repo()
    pid = repo.insert_sell_proposal(_row())
    repo.update_sell_proposal(pid)
    kinds = [e.event_type for e in audit.read_events(repo._conn)]  # noqa: SLF001
    assert kinds == ["sell_proposal_recorded"]


def test_an_update_refuses_a_column_the_table_does_not_have() -> None:
    """The keys are spliced into the SET clause, so they are checked against the table's own
    columns first -- and `id` is the proposal's identity, not a field to rewrite."""
    repo = _repo()
    pid = repo.insert_sell_proposal(_row())
    for bad in ("nonsense", "id"):
        with pytest.raises(ValueError, match=bad):
            repo.update_sell_proposal(pid, **{bad: 1})
    assert [e.event_type for e in audit.read_events(repo._conn)] == ["sell_proposal_recorded"]  # noqa: SLF001


def test_an_insert_refuses_a_key_the_table_does_not_have() -> None:
    """The insert's sibling rule (P7 carried item b): `update_sell_proposal` refuses a key that
    is not a column, and so does the insert. Silently dropping one would record a proposal that
    looks complete while a figure its writer meant to store -- a misspelt `expected_net_pnl`,
    say -- is NULL, which this table reads as "not recorded". `id` is the row's identity, which
    the database assigns. A refused row leaves neither row nor event."""
    repo = _repo()
    for bad in ("expected_net_pnI", "id"):
        with pytest.raises(ValueError, match=bad):
            repo.insert_sell_proposal(_row(**{bad: 1}))
    assert repo.get_sell_proposals() == []
    assert audit.read_events(repo._conn) == []  # noqa: SLF001


def test_a_decision_outside_the_vocabulary_is_refused() -> None:
    """`decision` is a closed vocabulary (spec §3.8: preview / superseded / vetoed / declined /
    placed, plus R34's failed). A typo is a proposal no reader recognises."""
    repo = _repo()
    with pytest.raises(ValueError, match="decision"):
        repo.insert_sell_proposal(_row(decision="previewed"))
    assert repo.get_sell_proposals() == []
    pid = repo.insert_sell_proposal(_row())
    with pytest.raises(ValueError, match="decision"):
        repo.update_sell_proposal(pid, decision="done")
    got = repo.get_sell_proposal(pid)
    assert got is not None and got["decision"] == "preview"


def test_newest_first_and_filterable() -> None:
    repo = _repo()
    a = repo.insert_sell_proposal(_row(ts=1))
    b = repo.insert_sell_proposal(_row(ts=2, product_id="PAXG-USD", rule_id=9))
    c = repo.insert_sell_proposal(_row(ts=2))
    assert [p["id"] for p in repo.get_sell_proposals()] == [c, b, a]
    assert [p["id"] for p in repo.get_sell_proposals(product_id="BTC-USD")] == [c, a]
    assert [p["id"] for p in repo.get_sell_proposals(rule_id=9)] == [b]
    assert [p["id"] for p in repo.get_sell_proposals(rule_id=7)] == [c, a]
    assert [p["id"] for p in repo.get_sell_proposals(since_ts=2)] == [c, b]
    assert [p["id"] for p in repo.get_sell_proposals(limit=2)] == [c, b]
    assert [p["id"] for p in repo.get_sell_proposals(product_id="BTC-USD", since_ts=2)] == [c]
    listed = repo.get_sell_proposals()[0]
    assert isinstance(listed["qty"], Decimal) and listed["trigger"] == {"cadence_day": 20_000}


def test_a_missing_id_reads_as_None() -> None:
    assert _repo().get_sell_proposal(999) is None


def test_an_unmigrated_database_reads_as_no_proposals() -> None:
    conn = connect(":memory:")
    repo = Repository(conn)
    assert repo.get_sell_proposals() == []
    assert repo.get_sell_proposal(1) is None


def test_an_update_to_a_missing_proposal_raises_and_chains_nothing() -> None:
    """An `updated` event about a row that does not exist would be a chained statement the table
    contradicts. The transaction rolls back with the refusal."""
    repo = _repo()
    with pytest.raises(LookupError, match="42"):
        repo.update_sell_proposal(42, reviewed_ts=200)
    assert audit.read_events(repo._conn) == []  # noqa: SLF001


def test_legs_defaults_to_one_order() -> None:
    """A sale that fits one order is one leg; only rail 2's slicing (P7) records more."""
    repo = _repo()
    row = _row()
    del row["legs"]
    got = repo.get_sell_proposal(repo.insert_sell_proposal(row))
    assert got is not None and got["legs"] == 1
