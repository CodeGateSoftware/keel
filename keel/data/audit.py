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

from keel_core.hashchain import ZERO_HASH, ChainLink, canonical_json, chain_hash, find_breaks

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

    **The commit is unconditional, and the first cut of this got it wrong in a way that reached
    the venue.** It read `if conn.in_transaction: yield; return` -- "somebody outside opened a
    transaction, so they own the commit". Nothing in this codebase nests these, so that branch
    was never reached by the nesting it was written for. What it WAS reached by is the accident:
    `sqlite3` runs in legacy implicit-transaction mode, so `in_transaction` is also True after any
    DML that has not been committed -- INCLUDING one that raised and was swallowed.
    `execution/equity.py` swallows a failed `record_cycle_balance` by design (a diagnostic write
    must never abort a cycle), and that left the connection mid-transaction. The next
    `insert_order` then took the join branch, returned a real `order_id`, and never committed --
    so `broker.place_order` sent an order to the venue with no durable row behind it, which is
    precisely the row `execution.reconcile` exists to find. Measured end to end: `orders visible
    to another connection: 0`.

    So this always commits on a clean exit, which is exactly what the unconditional
    `self._conn.commit()` in each writer did before this module existed. Skipping the `BEGIN` on
    an already-open transaction is only about sqlite refusing to nest one; it never means
    skipping the commit. (The lock invariant survives that path: legacy mode opens a transaction
    implicitly only on DML, so a connection that is already `in_transaction` has already taken
    the write lock, and the head read is serialised either way. `equity.py` now rolls its
    swallowed write back, so the path should not arise at all -- this is the second belt.)

    Rolls back on ANY exception, `BaseException` included: a `KeyboardInterrupt` between the
    store row and its event would otherwise leave the row committed and the chain silent about
    it, which is the one lie this store must not tell about itself.
    """
    if not conn.in_transaction:
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
    # The payload is canonicalised ONCE and commits AS THAT STRING -- the exact bytes the column
    # holds, not a re-serialisation of what a reader parsed back out of it.
    #
    # Two things follow, and the first is the reason. A hash taken over a parsed-then-re-encoded
    # object puts a decode/encode round trip between the stored row and its hash, and every such
    # conversion is somewhere the two can drift: a payload shape that does not survive the round
    # trip byte-for-byte (a non-string key, a tuple, anything `json` normalises) would store one
    # thing and attest to another. Hashing the bytes removes the question rather than answering it
    # shape by shape. The second is that verification then never PARSES: measured over 20,000
    # events, `chain_state` went from 277 ms to 134 ms, on the read a 15-second console poll makes.
    payload_json = canonical_json(payload)
    row_hash = chain_hash(_hash_body(ts, event_type, entity_id, payload_json, prev_hash))
    cursor = conn.execute(
        "INSERT INTO audit_events (ts, event_type, entity_id, payload_json, prev_hash, row_hash) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (ts, event_type, entity_id, payload_json, prev_hash, row_hash),
    )
    assert cursor.lastrowid is not None
    return AuditEvent(
        seq_id=cursor.lastrowid,
        ts=ts,
        event_type=event_type,
        entity_id=entity_id,
        payload=json.loads(payload_json),
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


def _hash_body(
    ts: int, event_type: str, entity_id: str, payload_json: str, prev_hash: str
) -> dict[str, Any]:
    """Everything a row commits to -- the row minus `row_hash` itself.

    `payload_json` is the STORED STRING, not a parsed object: see `append_event` for why the
    bytes rather than a re-encoding of them. Taken as five scalars rather than as an `AuditEvent`
    so the writer and the verifier compute the identical body from the identical inputs, with no
    parse on the verification path.

    `seq_id` is deliberately NOT hashed. It is sqlite's own AUTOINCREMENT counter, assigned after
    the hash would have to be computed, and position in the chain is already committed to via
    `prev_hash`. Hashing it would add nothing and would make the hash unreproducible from the
    values a writer actually chose.
    """
    return {
        "ts": ts,
        "event_type": event_type,
        "entity_id": entity_id,
        "payload": payload_json,
        "prev_hash": prev_hash,
    }


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


def _latest(rows: list[sqlite3.Row]) -> dict[tuple[str, str], EntityHash]:
    """`(store, entity_id) -> the LATEST event about it`.

    The latest rather than the first because it is the most recent chained statement about that
    row: an order that was placed and then filled has two events, and the fill is the later word
    on it.

    Keyed by store as well as entity because the five stores key on four different types and their
    identifiers collide -- an `orders.id` of `"1"` and a `coinbase_id` of `"1"` are different
    rows, and a single-keyed map would hand one row's hash to the other.
    """
    latest: dict[tuple[str, str], EntityHash] = {}
    for row in rows:
        store = EVENT_STORES.get(str(row["event_type"]))
        if store is None:
            # An event type this build does not know -- a row written by a NEWER keel against the
            # same database. Skipped rather than guessed at: it still chains (the chain does not
            # care what the type means), and inventing a store for it would file it against a
            # table it may have nothing to do with.
            continue
        latest[(store, str(row["entity_id"]))] = EntityHash(
            row_hash=str(row["row_hash"]), seq_id=int(row["seq_id"])
        )
    return latest


def _table_present(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'audit_events'"
    ).fetchone()
    return row is not None


def chain_state(conn: sqlite3.Connection) -> ChainState:
    """One read of `audit_events`, verified, indexed by the identifiers a report prints.

    What `commands/timeline.py` and `commands/doctor.py` call, and the only production path into
    this module. The `hashes` map is keyed by `(store, entity_id)` to match `timeline.py`'s own
    `source`/`reference` pair, so the hash shown against a row is looked up by the identifier
    printed beside it rather than by one a reader has to reconstruct.

    **The whole chain, every time, and that is not an oversight.** A chain proves a SEQUENCE, so
    a verdict over a suffix is a verdict that has not looked at the rows most likely to have been
    quietly edited, and a cached prefix verdict assumes precisely the thing being checked. The
    cost is real and is stated rather than hidden, in the manner `DEFAULT_TIMELINE_LIMIT` and
    `export_rows` already use next door: measured on a 2026 laptop, 20,000 events verify in about
    135 ms, and the console polls `/api/timeline` every 15 seconds. Event volume is dominated by
    `upsert_transaction` -- one event per imported CSV line -- so a long Coinbase history is what
    puts a deployment into that band.

    If it ever stops fitting, the change is to BOUND WHAT IS CLAIMED and say so on the page -- a
    verdict over the last N events, labelled as one. Never to cache the verdict, which would put
    a green badge over rows nothing read: the exact failure the rest of this module is built to
    refuse.
    """
    if not _table_present(conn):
        # Checked rather than caught. A pre-v20 database is a legitimate deployment state, and a
        # `try`/`except OperationalError` around the read would swallow a genuine "database disk
        # image is malformed" under the same clause -- reporting an un-upgraded database where
        # the truth is a corrupt one.
        return ChainState(
            table_present=False, event_count=0, errors=(), first_broken_seq=None, hashes={}
        )
    # Raw rows, deliberately not `read_events`: verification needs the stored `payload_json`
    # STRING, and parsing 20,000 payloads to re-encode them would double the cost of the check
    # for a value nothing on this path uses.
    rows = conn.execute(
        "SELECT seq_id, ts, event_type, entity_id, payload_json, prev_hash, row_hash "
        "FROM audit_events ORDER BY seq_id"
    ).fetchall()
    breaks = find_breaks(
        ChainLink(
            label=str(row["seq_id"]),
            prev_hash=str(row["prev_hash"]),
            row_hash=str(row["row_hash"]),
            recomputed_hash=chain_hash(
                _hash_body(
                    int(row["ts"]),
                    str(row["event_type"]),
                    str(row["entity_id"]),
                    str(row["payload_json"]),
                    str(row["prev_hash"]),
                )
            ),
        )
        for row in rows
    )
    # `index` is 1-based POSITION IN THE WALK, and the rows are read in `seq_id` order, so this is
    # the event the first break lands on. Looked up rather than parsed out of the message text.
    first_broken_seq = int(rows[breaks[0].index - 1]["seq_id"]) if breaks else None
    return ChainState(
        table_present=True,
        event_count=len(rows),
        errors=tuple(found.message for found in breaks),
        first_broken_seq=first_broken_seq,
        hashes=_latest(rows),
    )
