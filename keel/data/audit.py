"""`audit_events`: the append-only, hash-chained record of what the book did (#721).

#703's activity export shipped with a `row_hash` column whose every cell read `NOT RECORDED`.
That was honest and it was the whole of what could be said: none of the four stores the timeline
merges hashed its rows. This module is the record that column was built to read.

── WHY EVENTS AND NOT THE ROWS ────────────────────────────────────────────────────────────────

`orders` rows are MUTATED. `update_order` writes status, fills and fees as a venue reports them,
sometimes minutes after placement, sometimes days later via `execution.reconcile`. A hash chained
over the order row itself would therefore break on every legitimate fill -- and a chain that
cries wolf on ordinary operation is a chain an operator learns to ignore, which is worse than no
chain at all.

So the chain is over IMMUTABLE STATEMENTS about the book, not over the book. `insert_order`
appends "this order was placed, with these fields"; each `update_order` appends "these fields
changed". Neither event is ever rewritten. The book stays mutable and queryable; the event stream
is what an auditor verifies.

The same shape covers `transactions` (whose upsert rewrites an imported line in place -- two
events for one `coinbase_id` is the record that the line was re-imported, which is precisely what
should be visible) and both attestation tables (where a re-attestation is a fresh human claim).

── ONE CHAIN, NOT ONE PER TABLE ───────────────────────────────────────────────────────────────

Every event goes into one sequence. A per-table chain would let an event be moved between streams
undetectably, and would mean a removed order event was only visible to someone who thought to
verify the order chain specifically. One chain means any removal anywhere is visible from every
event after it.

── WHAT THIS IS NOT ───────────────────────────────────────────────────────────────────────────

**Tamper-EVIDENT, not tamper-proof.** Anyone who can write `keel.db` can rewrite the chain from
the edited row forward. What it buys is that a row cannot be changed QUIETLY.

**No backfill, ever.** Rows written before this shipped have no event. Computing hashes for them
now would produce a chain that verifies and proves nothing -- worse than an honest gap, because
it looks like evidence. `commands/timeline.py` reports those rows as not chained.

**Not the research trials ledger.** `keel/research/ledger.py` records EXPERIMENTS and is
git-tracked; this records trading activity and is per-deployment. They share
`keel_core.hashchain` -- one definition of canonical JSON, so one row can only have one hash --
and nothing else. Folding trials into the trading audit trail to borrow their hashes would be
provenance laundering.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from keel_core.hashchain import (
    ZERO_HASH,
    ChainLink,
    chain_hash,
    encode_value,
    find_breaks,
    verify_links,
)

#: Every kind of statement this chain carries, and the store each one is about.
#:
#: A CLOSED vocabulary, checked at write time, like `TIMELINE_KINDS` and `PROVENANCES` next door.
#: An event type that arrives via a typo is a stream nothing reads and nothing misses -- and on
#: an audit surface, a silently-ignored record is the failure mode that matters.
#:
#: The store name is carried because `latest_hashes` is keyed by it: an `orders.id` of `"1"` and
#: a `coinbase_id` of `"1"` are different rows, and keyed by entity alone the imported transaction
#: would hand its hash to an unrelated order.
EVENT_STORES: Mapping[str, str] = {
    "order_placed": "orders",
    "order_updated": "orders",
    "transaction_recorded": "transactions",
    "asset_attested": "asset_attestations",
    "instrument_attested": "instrument_attestations",
}


@dataclass(frozen=True)
class AuditEvent:
    """One immutable statement about the book, and its place in the chain."""

    seq_id: int
    ts: int
    event_type: str
    #: The subject store's own identifier -- an `orders.id`, a `coinbase_id`, an asset, a
    #: `venue:product_id`. As TEXT, because the five stores key on four different types.
    entity_id: str
    #: What the statement asserts. Decimals are already strings here: they were encoded on the
    #: way in (`keel_core.hashchain.encode_value`) so that what is hashed is exactly what is
    #: stored, and a reader is never handed a float rendering of a recorded money value.
    payload: dict[str, Any]
    prev_hash: str
    row_hash: str


@contextmanager
def write_transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """One write transaction covering a store row and its audit event.

    **`BEGIN IMMEDIATE`, not `BEGIN`.** A deferred transaction takes no lock until its first
    WRITE, so the chain-head SELECT inside it would still be unserialised: two writers would both
    read the same head, both write it as their `prev_hash`, and the chain would fork -- silently,
    because each row verifies against the row it believes precedes it. `IMMEDIATE` takes the
    write lock up front, which is what makes "read the head, then append" atomic.

    Nested calls JOIN the caller's transaction rather than opening a second one: sqlite raises
    "cannot start a transaction within a transaction", and more importantly a nested commit would
    publish the outer writer's half-finished work. The outermost block owns the commit.

    Rolls back on ANY exception, `BaseException` included: a `KeyboardInterrupt` between the
    store row and its event would otherwise leave the row committed and the chain silent about
    it, which is the one lie this store must not tell about itself.
    """
    if conn.in_transaction:
        yield
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.rollback()
        raise
    conn.commit()


def append_event(
    conn: sqlite3.Connection,
    *,
    ts: int,
    event_type: str,
    entity_id: str,
    payload: Mapping[str, Any],
) -> AuditEvent:
    """Chain and append one event. Does NOT commit -- the caller's `write_transaction` does.

    Not committing is the point: the store row and its event must land together or not at all.
    An order row with no event reads forever after as "written before the bump" -- an
    honest-looking gap that is in fact a failed chain write.

    The head read is inside the caller's write transaction and refuses to run outside one, which
    is the serialisation this chain rests on. `ORDER BY seq_id DESC`, never `ts`: two events
    inside one second share a timestamp, and a clock that steps backwards would silently reorder
    the chain into a false break.
    """
    if event_type not in EVENT_STORES:
        raise ValueError(f"event_type: {event_type!r} not in {sorted(EVENT_STORES)}")
    if not conn.in_transaction:
        raise RuntimeError(
            "append_event must run inside a write transaction (see `write_transaction`): "
            "reading the chain head outside one lets two writers fork the chain"
        )

    head = conn.execute("SELECT row_hash FROM audit_events ORDER BY seq_id DESC LIMIT 1").fetchone()
    prev_hash = ZERO_HASH if head is None else str(head["row_hash"])
    # Encoded BEFORE hashing and stored in the encoded form, so what is hashed is byte-for-byte
    # what a reader gets back. Encoding at read time instead would put a conversion between the
    # stored row and the hash, and every such conversion is somewhere the two can drift.
    encoded: dict[str, Any] = encode_value(dict(payload))
    body = {
        "ts": ts,
        "event_type": event_type,
        "entity_id": entity_id,
        "payload": encoded,
        "prev_hash": prev_hash,
    }
    row_hash = chain_hash(body)
    cursor = conn.execute(
        "INSERT INTO audit_events (ts, event_type, entity_id, payload_json, prev_hash, row_hash) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            ts,
            event_type,
            entity_id,
            json.dumps(encoded, sort_keys=True, separators=(",", ":")),
            prev_hash,
            row_hash,
        ),
    )
    assert cursor.lastrowid is not None
    return AuditEvent(
        seq_id=cursor.lastrowid,
        ts=ts,
        event_type=event_type,
        entity_id=entity_id,
        payload=encoded,
        prev_hash=prev_hash,
        row_hash=row_hash,
    )


def read_events(conn: sqlite3.Connection) -> list[AuditEvent]:
    """Every event, in CHAIN order (`seq_id`), which is the only order the chain verifies in."""
    rows = conn.execute(
        "SELECT seq_id, ts, event_type, entity_id, payload_json, prev_hash, row_hash "
        "FROM audit_events ORDER BY seq_id"
    ).fetchall()
    return [
        AuditEvent(
            seq_id=int(row["seq_id"]),
            ts=int(row["ts"]),
            event_type=str(row["event_type"]),
            entity_id=str(row["entity_id"]),
            payload=json.loads(row["payload_json"]),
            prev_hash=str(row["prev_hash"]),
            row_hash=str(row["row_hash"]),
        )
        for row in rows
    ]


def _event_payload(event: AuditEvent) -> dict[str, Any]:
    """Everything the row commits to -- the row minus `row_hash` itself.

    `seq_id` is deliberately NOT hashed. It is sqlite's own AUTOINCREMENT counter, assigned after
    the hash would have to be computed, and position in the chain is already committed to via
    `prev_hash`. Hashing it would add nothing and would make the hash unreproducible from the
    values a writer actually chose.
    """
    return {
        "ts": event.ts,
        "event_type": event.event_type,
        "entity_id": event.entity_id,
        "payload": event.payload,
        "prev_hash": event.prev_hash,
    }


def verify_events(events: list[AuditEvent]) -> list[str]:
    """Every break in the chain, as human-readable lines. Empty means intact.

    Reports rather than raises, so `keel doctor` and the timeline export can both STATE chain
    status instead of asserting it. **An empty list of events returns no errors, and that is not
    the same as "verified"** -- nothing was checked. Only the caller knows whether that means a
    deployment predating #721 or a deployment that has done nothing yet.
    """
    return verify_links(
        ChainLink(
            label=str(event.seq_id),
            prev_hash=event.prev_hash,
            row_hash=event.row_hash,
            recomputed_hash=chain_hash(_event_payload(event)),
        )
        for event in events
    )


def latest_hashes(conn: sqlite3.Connection) -> dict[tuple[str, str], str]:
    """`(store, entity_id) -> the row_hash of the LATEST event about it`. See `chain_state`."""
    return {key: seen.row_hash for key, seen in _latest(read_events(conn)).items()}


@dataclass(frozen=True)
class EntityHash:
    """The latest chained statement about one row, and where it sits in the chain."""

    row_hash: str
    #: The event's own `seq_id`. Carried so a caller can ask whether this statement falls in the
    #: region the chain no longer vouches for -- see `ChainState.first_broken_seq`.
    seq_id: int


@dataclass(frozen=True)
class ChainState:
    """Everything a report needs to STATE the chain's condition rather than assert it.

    Read in one pass, because the alternative -- a hash lookup here, a verification there -- means
    the hash printed beside a row and the verdict printed above it describe two different reads of
    the table, and a row appended between them would have the verdict cover a row the report never
    showed. That is the mistake `research/ledger.py::verify_records` exists to have fixed once.
    """

    #: Whether `audit_events` EXISTS on this database (schema v20).
    #:
    #: Not a theoretical case. Two readers open this database without migrating it -- the web
    #: server (`keel/web/server.py`: a view must not take a schema write lock) and `keel mcp`'s
    #: `_open_readonly_repo`. Both therefore meet pre-v20 databases, and a missing table there is
    #: an ordinary fact about an un-upgraded deployment rather than an error. #718 shipped a
    #: reader that raised on exactly this and took the whole of `gather_findings` down with it.
    table_present: bool
    #: How many events the table holds. ZERO IS A DISTINCT STATE, not "verified": a deployment
    #: that predates #721 and one that has done nothing since are both empty here, and neither has
    #: had anything checked. The caller decides which sentence to print.
    event_count: int
    errors: tuple[str, ...]
    #: The `seq_id` of the FIRST event the chain stops vouching for, or `None` when intact. Every
    #: event from here on is unverified -- not because each one is necessarily wrong, but because
    #: a chain proves the sequence, and past a break the sequence is no longer proven.
    first_broken_seq: int | None
    hashes: Mapping[tuple[str, str], EntityHash]

    @property
    def intact(self) -> bool:
        """The chain verifies AND there is something to verify.

        Both halves, deliberately. `not errors` alone is true of an empty table, and a green
        badge over a table nothing read is the exact failure this codebase keeps re-learning.
        """
        return not self.errors and self.event_count > 0


def _latest(events: list[AuditEvent]) -> dict[tuple[str, str], EntityHash]:
    """`(store, entity_id) -> the LATEST event about it`.

    The latest rather than the first because it is the most recent chained statement about that
    row: an order that was placed and then filled has two events, and the fill is the later word
    on it.

    Keyed by store as well as entity because the five stores key on four different types and their
    identifiers collide -- an `orders.id` of `"1"` and a `coinbase_id` of `"1"` are different
    rows, and a single-keyed map would hand one row's hash to the other.
    """
    latest: dict[tuple[str, str], EntityHash] = {}
    for event in events:
        store = EVENT_STORES.get(event.event_type)
        if store is None:
            # An event type this build does not know -- a row written by a NEWER keel against the
            # same database. Skipped rather than guessed at: it still chains (the chain does not
            # care what the type means), and inventing a store for it would file it against a
            # table it may have nothing to do with.
            continue
        latest[(store, event.entity_id)] = EntityHash(row_hash=event.row_hash, seq_id=event.seq_id)
    return latest


def _table_present(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'audit_events'"
    ).fetchone()
    return row is not None


def chain_state(conn: sqlite3.Connection) -> ChainState:
    """One read of `audit_events`, verified, indexed by the identifiers a report prints.

    What `commands/timeline.py` calls. The `hashes` map is keyed by `(store, entity_id)` to match
    that module's own `source`/`reference` pair, so the hash shown against a row is looked up by
    the identifier printed beside it rather than by one a reader has to reconstruct.
    """
    if not _table_present(conn):
        # Checked rather than caught. A pre-v20 database is a legitimate deployment state, and a
        # `try`/`except OperationalError` around the read would swallow a genuine "database disk
        # image is malformed" under the same clause -- reporting an un-upgraded database where
        # the truth is a corrupt one.
        return ChainState(
            table_present=False, event_count=0, errors=(), first_broken_seq=None, hashes={}
        )
    events = read_events(conn)
    breaks = find_breaks(
        ChainLink(
            label=str(event.seq_id),
            prev_hash=event.prev_hash,
            row_hash=event.row_hash,
            recomputed_hash=chain_hash(_event_payload(event)),
        )
        for event in events
    )
    # `index` is 1-based POSITION IN THE WALK, and `events` is read in `seq_id` order, so this is
    # the event the first break lands on. Looked up rather than parsed out of the message text.
    first_broken_seq = events[breaks[0].index - 1].seq_id if breaks else None
    return ChainState(
        table_present=True,
        event_count=len(events),
        errors=tuple(found.message for found in breaks),
        first_broken_seq=first_broken_seq,
        hashes=_latest(events),
    )
