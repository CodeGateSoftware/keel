"""`audit_events`: the append-only, hash-chained record of what the book did (#721).

`orders` rows are UPDATED as a venue reports fills, so a chain over the order row itself would
break on every legitimate update -- a chain that cries wolf is a chain nobody reads. So the chain
is over EVENTS: `insert_order` and each `update_order` append an immutable statement of what
changed, and it is the event stream, not the mutable book, that is tamper-evident.

What these tests pin is the property that makes it evidence: an edit or a deletion cannot be made
quietly. Everything else here -- the vocabulary, the atomicity, the pre-bump gap -- exists to
keep that property true in the presence of the rest of the engine.
"""

from __future__ import annotations

import sqlite3
from decimal import Decimal

import pytest
from keel_core.hashchain import ZERO_HASH

from keel.data import audit, db
from keel.data.repository import Repository


@pytest.fixture()
def conn() -> sqlite3.Connection:
    connection = db.connect(":memory:")
    db.migrate(connection)
    return connection


def _append(connection: sqlite3.Connection, **kwargs: object) -> audit.AuditEvent:
    """One event in its own write transaction, as a repository writer does around a store row."""
    with audit.write_transaction(connection):
        return audit.append_event(connection, **kwargs)  # type: ignore[arg-type]


def _order(**overrides: object) -> dict[str, object]:
    """A minimally legal `orders` row -- every NOT NULL column and nothing else."""
    row: dict[str, object] = {
        "mode": "live",
        "product_id": "BTC-USD",
        "side": "buy",
        "qty": Decimal("1"),
        "created_at": 100,
    }
    row.update(overrides)
    return row


def _transaction(**overrides: object) -> dict[str, object]:
    """A minimally legal `transactions` row."""
    row: dict[str, object] = {
        "coinbase_id": "cb-1",
        "source": "coinbase",
        "type": "deposit",
        "asset": "USD",
        "ts": 100,
        "qty": Decimal("1"),
    }
    row.update(overrides)
    return row


# -- the chain itself -----------------------------------------------------------------------


def test_the_first_event_anchors_to_the_zero_hash(conn: sqlite3.Connection) -> None:
    event = _append(conn, ts=100, event_type="order_placed", entity_id="1", payload={"a": 1})
    assert event.prev_hash == ZERO_HASH
    assert len(event.row_hash) == 64


def test_each_event_commits_to_its_predecessor(conn: sqlite3.Connection) -> None:
    first = _append(conn, ts=100, event_type="order_placed", entity_id="1", payload={"a": 1})
    second = _append(conn, ts=101, event_type="order_updated", entity_id="1", payload={"a": 2})
    assert second.prev_hash == first.row_hash
    assert audit.verify_events(audit.read_events(conn)) == []


def test_the_chain_spans_event_types_not_one_chain_per_table(conn: sqlite3.Connection) -> None:
    """One chain over everything, so an event cannot be moved between streams, and so a removed
    order event is visible from a later transaction event rather than only from its own kind."""
    _append(conn, ts=100, event_type="order_placed", entity_id="1", payload={"a": 1})
    second = _append(
        conn, ts=101, event_type="transaction_recorded", entity_id="cb-1", payload={"a": 2}
    )
    events = audit.read_events(conn)
    assert second.prev_hash == events[0].row_hash
    assert audit.verify_events(events) == []


def test_editing_a_payload_in_place_breaks_that_row_and_no_other(conn: sqlite3.Connection) -> None:
    for index in range(3):
        _append(
            conn,
            ts=100 + index,
            event_type="order_placed",
            entity_id=str(index),
            payload={"a": index},
        )
    conn.execute("UPDATE audit_events SET payload_json = ? WHERE seq_id = 2", ('{"a":99}',))
    conn.commit()

    errors = audit.verify_events(audit.read_events(conn))
    assert len(errors) == 1
    assert "row 2" in errors[0]
    assert "row_hash" in errors[0]


def test_deleting_a_row_breaks_every_row_after_it(conn: sqlite3.Connection) -> None:
    """The property the whole store exists for: a deletion is not a quiet deletion."""
    for index in range(4):
        _append(
            conn,
            ts=100 + index,
            event_type="order_placed",
            entity_id=str(index),
            payload={"a": index},
        )
    conn.execute("DELETE FROM audit_events WHERE seq_id = 2")
    conn.commit()

    errors = audit.verify_events(audit.read_events(conn))
    assert len(errors) == 1, errors
    assert "row 2" in errors[0]
    assert "does not chain" in errors[0]


def test_events_are_read_in_chain_order_not_timestamp_order(conn: sqlite3.Connection) -> None:
    """`seq_id`, never `ts`. Two events inside one second share a timestamp, and a clock that
    steps backwards would reorder the chain into a false break."""
    # THREE, and the timestamps deliberately out of order. With two, the head read has only one
    # candidate and any ordering picks it -- which is why the two-event version of this test
    # passed against a head read ordered by `ts`. The third event is the one whose predecessor
    # the two orderings disagree about: by `seq_id` it chains to the second, by `ts DESC` it
    # would chain to the FIRST, and the chain would fork.
    _append(conn, ts=500, event_type="order_placed", entity_id="1", payload={"a": 1})
    _append(conn, ts=100, event_type="order_updated", entity_id="1", payload={"a": 2})
    _append(conn, ts=200, event_type="order_updated", entity_id="1", payload={"a": 3})

    events = audit.read_events(conn)
    assert [event.seq_id for event in events] == [1, 2, 3]
    assert events[2].prev_hash == events[1].row_hash
    assert audit.verify_events(events) == []


def test_a_decimal_in_the_payload_survives_the_round_trip(conn: sqlite3.Connection) -> None:
    """Money is the point. A payload whose Decimals came back as floats would verify against a
    hash of a value the row never held."""
    event = _append(
        conn,
        ts=100,
        event_type="order_placed",
        entity_id="1",
        payload={"qty": Decimal("0.10"), "fee": None},
    )
    stored = audit.read_events(conn)[0]
    assert stored.payload["qty"] == "0.10"
    assert stored.row_hash == event.row_hash
    assert audit.verify_events([stored]) == []


# -- the vocabulary and the guards ------------------------------------------------------------


def test_an_unrecognised_event_type_is_refused(conn: sqlite3.Connection) -> None:
    """A closed vocabulary, like every other `kind`/`provenance` set in this codebase: a typo
    that lands as a new event type is a stream nothing reads and nothing misses."""
    with pytest.raises(ValueError, match="event_type"):
        _append(conn, ts=100, event_type="order_plased", entity_id="1", payload={})


def test_appending_outside_a_write_transaction_is_refused(conn: sqlite3.Connection) -> None:
    """The head read and the insert MUST be one transaction. Read the head in autocommit and two
    writers racing both see the same head, both write it as their `prev_hash`, and the chain
    forks -- silently, because each row verifies against the row it thinks precedes it."""
    with pytest.raises(RuntimeError, match="transaction"):
        audit.append_event(conn, ts=100, event_type="order_placed", entity_id="1", payload={})


def test_the_head_read_takes_a_write_lock_before_reading(conn: sqlite3.Connection) -> None:
    """`BEGIN IMMEDIATE`, not `BEGIN`. A deferred transaction takes no lock until its first
    write, so the head SELECT would still be unserialised against a concurrent writer."""
    with audit.write_transaction(conn):
        assert conn.in_transaction
        # A second connection to the same file cannot begin writing while this one holds the
        # write lock. In-memory connections do not share a database, so this asserts the
        # statement issued rather than the lock's effect -- the effect is asserted below.
    assert not conn.in_transaction


def test_two_connections_cannot_hold_the_write_lock_at_once(tmp_path) -> None:
    """The lock, on a real file. Without `IMMEDIATE` the second writer would be admitted here and
    would read the same chain head as the first."""
    path = tmp_path / "keel.db"
    first = db.connect(path)
    db.migrate(first)
    second = db.connect(path)
    second.execute("PRAGMA busy_timeout = 50")

    with audit.write_transaction(first):
        # BEFORE any write. A DEFERRED transaction would still be admitted here and would take
        # its lock only at the INSERT -- by which point it has already read a chain head that a
        # racing writer may have moved. Appending first and then checking the lock passes under
        # `BEGIN` too, and so proves nothing: the insert itself takes the lock either way.
        with pytest.raises(sqlite3.OperationalError, match="locked|busy"):
            second.execute("BEGIN IMMEDIATE")
        audit.append_event(first, ts=100, event_type="order_placed", entity_id="1", payload={})


# -- the writers ----------------------------------------------------------------------------


def test_insert_order_records_a_chained_placement_event(conn: sqlite3.Connection) -> None:
    repo = Repository(conn)
    order_id = repo.insert_order(_order(product_id="BTC-USD", qty=Decimal("0.5")))
    events = audit.read_events(conn)
    assert [event.event_type for event in events] == ["order_placed"]
    assert events[0].entity_id == str(order_id)
    assert events[0].payload["product_id"] == "BTC-USD"
    assert audit.verify_events(events) == []


def test_update_order_records_what_changed_not_the_whole_row(conn: sqlite3.Connection) -> None:
    """An event says what this statement asserted. Re-hashing the whole mutated row would make
    each event a snapshot, and a reader could not tell an update from a re-write."""
    repo = Repository(conn)
    order_id = repo.insert_order(_order(product_id="BTC-USD"))
    repo.update_order(order_id, status="filled", actual_fill=Decimal("42.5"))

    events = audit.read_events(conn)
    assert [event.event_type for event in events] == ["order_placed", "order_updated"]
    assert events[1].payload == {"status": "filled", "actual_fill": "42.5"}
    assert audit.verify_events(events) == []


def test_an_update_with_no_fields_records_nothing(conn: sqlite3.Connection) -> None:
    """`update_order` already returns early for an empty change set. An event asserting that
    nothing changed is chain noise."""
    repo = Repository(conn)
    repo.insert_order(_order(product_id="BTC-USD"))
    repo.update_order(1)
    assert [event.event_type for event in audit.read_events(conn)] == ["order_placed"]


def test_upsert_transaction_records_a_flow_event(conn: sqlite3.Connection) -> None:
    repo = Repository(conn)
    repo.upsert_transaction(_transaction(coinbase_id="cb-1", total=Decimal("250.00")))
    events = audit.read_events(conn)
    assert [event.event_type for event in events] == ["transaction_recorded"]
    assert events[0].entity_id == "cb-1"
    assert audit.verify_events(events) == []


def test_re_importing_a_transaction_appends_rather_than_rewrites(conn: sqlite3.Connection) -> None:
    """`upsert_transaction` UPDATES the book row in place. The chain must not: two events for one
    `coinbase_id` is the record that the line was re-imported with different content, which is
    exactly what an auditor wants visible."""
    repo = Repository(conn)
    for total in (Decimal("250.00"), Decimal("260.00")):
        repo.upsert_transaction(
            _transaction(coinbase_id="cb-1", total=total)
        )
    events = audit.read_events(conn)
    assert len(events) == 2
    assert events[0].row_hash != events[1].row_hash
    assert audit.verify_events(events) == []


def test_both_attestation_upserts_record_a_human_claim(conn: sqlite3.Connection) -> None:
    repo = Repository(conn)
    repo.upsert_asset_attestation(
        asset="BTC", sector="tech", backing="none", pays_yield=False,
        source="prospectus", attested_by="operator", attested_at=100,
    )
    repo.upsert_instrument_attestation(
        venue="coinbase", product_id="BTC-USD", wrapper="spot",
        source="venue docs", attested_by="operator", attested_at=101,
    )
    events = audit.read_events(conn)
    assert [event.event_type for event in events] == ["asset_attested", "instrument_attested"]
    assert events[0].entity_id == "BTC"
    assert events[1].entity_id == "coinbase:BTC-USD"
    assert audit.verify_events(events) == []


def test_the_store_row_and_its_event_land_together_or_not_at_all(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One transaction, both writes. An order row with no event would read forever after as
    "written before the bump" -- an honest-looking gap that is in fact a failed chain write, and
    the one lie this store must never tell about itself."""

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr("keel.data.repository.append_event", _boom)
    repo = Repository(conn)
    with pytest.raises(sqlite3.OperationalError):
        repo.insert_order(_order(product_id="BTC-USD"))

    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 0


# -- the honest gap ---------------------------------------------------------------------------


def test_rows_written_before_the_bump_leave_no_event_and_that_is_not_a_break(
    conn: sqlite3.Connection,
) -> None:
    """NO BACKFILL. A row inserted by a pre-#721 build has no event, and computing one now would
    produce a chain that verifies and proves nothing -- worse than a gap, because it looks like
    evidence. The chain over the events that DO exist stays intact."""
    conn.execute(
        "INSERT INTO orders (mode, product_id, side, qty, status, created_at) "
        "VALUES ('live','BTC-USD','buy','1','filled',50)"
    )
    conn.commit()
    repo = Repository(conn)
    repo.insert_order(_order(product_id="ETH-USD"))

    events = audit.read_events(conn)
    assert len(events) == 1
    assert events[0].entity_id == "2"
    assert audit.verify_events(events) == []


def test_latest_hashes_reports_the_newest_event_per_entity(conn: sqlite3.Connection) -> None:
    """What the timeline reads: the most recent CHAINED statement about a row. An order that was
    placed and then filled has two events, and the fill is the later word on it."""
    repo = Repository(conn)
    order_id = repo.insert_order(_order(product_id="BTC-USD"))
    repo.update_order(order_id, status="filled")

    latest = audit.latest_hashes(conn)
    events = audit.read_events(conn)
    assert latest[("orders", str(order_id))] == events[1].row_hash


def test_latest_hashes_is_keyed_by_store_so_two_stores_cannot_collide(
    conn: sqlite3.Connection,
) -> None:
    """`orders.id` is `"1"` and so is a `coinbase_id` of `"1"`. Keyed by entity alone, an
    imported transaction would hand its hash to an unrelated order row."""
    repo = Repository(conn)
    repo.insert_order(_order(product_id="BTC-USD"))
    repo.upsert_transaction(_transaction(coinbase_id="1"))

    latest = audit.latest_hashes(conn)
    assert latest[("orders", "1")] != latest[("transactions", "1")]


# -- readers that never migrate ----------------------------------------------------------------
#
# Two readers open a database WITHOUT migrating it: `keel/web/server.py` (a view must not take a
# schema write lock) and `keel/mcp/tools.py::_open_readonly_repo`. Both therefore meet pre-v20
# databases with no `audit_events` table at all. #718 shipped a reader that raised on exactly this
# shape and took the whole of `gather_findings` down with it; these are that lesson, pinned.


def _pre_chain_database() -> sqlite3.Connection:
    """A database with the book but not the chain -- what an un-upgraded deployment looks like."""
    connection = db.connect(":memory:")
    db.migrate(connection)
    connection.execute("DROP TABLE audit_events")
    connection.commit()
    return connection


def test_chain_state_reports_a_missing_table_rather_than_raising() -> None:
    connection = _pre_chain_database()
    state = audit.chain_state(connection)

    assert state.table_present is False
    assert state.event_count == 0
    assert state.errors == ()
    assert state.intact is False


def test_the_timeline_still_renders_on_a_database_without_the_chain() -> None:
    """The page an operator opens right after upgrading keel and before running `keel migrate`.
    Every row reads `not chained`, which is true, and nothing raises."""
    from keel.commands import timeline

    connection = _pre_chain_database()
    connection.execute(
        "INSERT INTO orders (mode, product_id, side, qty, status, created_at) "
        "VALUES ('live','BTC-USD','buy','1','filled',100)"
    )
    connection.commit()

    report = timeline.gather_timeline(Repository(connection), now_ts=200)
    assert [row.chain_status for row in report.rows] == ["not chained"]
    assert report.chain_recorded is False
    assert report.chain_errors == ()


def test_appending_to_a_missing_table_still_fails_loudly() -> None:
    """Tolerance is for READERS. A write that cannot record its event must not quietly succeed:
    that is the failed chain write masquerading as an honest gap, and the one lie this store must
    never tell about itself."""
    connection = _pre_chain_database()
    repo = Repository(connection)
    with pytest.raises(sqlite3.OperationalError):
        repo.insert_order(_order())
    assert connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
