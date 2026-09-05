"""The unified activity timeline -- issue #703.

Four stores that never knew about each other, merged into one chronology: the engine's JSONL log,
the `orders` table, the `transactions` ledger, and the attestation tables.

**The property under test throughout is that they merge WITHOUT blurring.** A venue-reported
fill, a line imported from a venue's CSV export, and a sentence a human typed and signed are
three different kinds of evidence. A feed that showed them identically would be less useful than
the four separate tables it replaced -- so every row carries its provenance, and the tests below
care more about that than about the ordering.
"""

from __future__ import annotations

import csv
import io
import sqlite3
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from keel.commands import timeline
from keel.commands.timeline import PROVENANCES, TIMELINE_KINDS, gather_timeline
from keel.data import audit
from keel.data.db import connect, migrate
from keel.data.repository import Repository

NOW_TS = 1_800_000_000
DAY = 86_400
TODAY_START = 1_799_971_200  # 2027-01-15T00:00:00Z


@pytest.fixture()
def db_conn(tmp_path: Path) -> sqlite3.Connection:
    """The CONNECTION, for the tests that reach past the repository to edit `audit_events` in
    place -- which is the only way to write the tampering the chain exists to detect."""
    conn = connect(str(tmp_path / "keel.db"))
    migrate(conn)
    return conn


@pytest.fixture()
def repo(db_conn: sqlite3.Connection) -> Repository:
    return Repository(db_conn)


def _order(repo: Repository, **overrides: Any) -> int:
    row: dict[str, Any] = {
        "mode": "live",
        "product_id": "BTC-USD",
        "side": "buy",
        "order_type": "market",
        "qty": Decimal("0.01"),
        "status": "filled",
        "fee": Decimal("1.18"),
        "expected_fill": Decimal("100000"),
        "actual_fill": Decimal("100050"),
        "created_at": NOW_TS - 3600,
        "updated_at": NOW_TS - 3600,
    }
    row.update(overrides)
    return repo.insert_order(row)


def _transaction(repo: Repository, **overrides: Any) -> None:
    row: dict[str, Any] = {
        "coinbase_id": "cb-1",
        "source": "coinbase",
        "type": "deposit",
        "asset": "USD",
        "ts": NOW_TS - 7200,
        "qty": Decimal("500"),
        "total": Decimal("500"),
        "notes": "",
    }
    row.update(overrides)
    repo.upsert_transaction(row)


def _attestation(repo: Repository, **overrides: Any) -> None:
    row: dict[str, Any] = {
        "asset": "BTC",
        "sector": "payments",
        "backing": "none",
        "pays_yield": False,
        "source": "whitepaper",
        "attested_by": "operator",
        "attested_at": NOW_TS - 10800,
    }
    row.update(overrides)
    repo.upsert_asset_attestation(**row)


# -- the merge keeps the sources apart ---------------------------------------------------------


def test_the_four_sources_merge_into_one_chronology(repo: Repository, tmp_path: Path) -> None:
    _order(repo)
    _transaction(repo)
    _attestation(repo)

    report = gather_timeline(repo, now_ts=NOW_TS, scope="all")

    kinds = {row.kind for row in report.rows}
    assert {"trade", "flow", "attestation"} <= kinds


def test_rows_are_newest_first(repo: Repository, tmp_path: Path) -> None:
    """One chronology, and the newest thing that happened is the thing an operator opened this
    page to see.

    The timestamps are SCRAMBLED relative to the order the sources are concatenated in
    (`_order_rows`, then `_transaction_rows`, then `_attestation_rows`). The first version of
    this test seeded oldest-to-newest, which happened to match that concatenation -- so deleting
    the sort entirely left it green. Here the orders are the OLDEST and the attestation the
    newest, so an unsorted merge comes back ascending and fails.
    """
    _order(repo, created_at=NOW_TS - 10_000)
    _order(repo, created_at=NOW_TS - 9_000)
    _transaction(repo, ts=NOW_TS - 5_000)
    _attestation(repo, attested_at=NOW_TS - 100)

    timestamps = [row.ts for row in gather_timeline(repo, now_ts=NOW_TS, scope="all").rows]

    assert timestamps == [NOW_TS - 100, NOW_TS - 5_000, NOW_TS - 9_000, NOW_TS - 10_000]


def test_two_events_at_one_instant_keep_a_stable_order(repo: Repository, tmp_path: Path) -> None:
    """The tie-break. Without it two rows sharing a second come back in whatever order the merge
    happened to produce, and the page reshuffles them between polls under a reader's cursor."""
    _order(repo, created_at=NOW_TS - 60)
    _transaction(repo, ts=NOW_TS - 60)
    _attestation(repo, attested_at=NOW_TS - 60)

    references = [row.reference for row in gather_timeline(repo, now_ts=NOW_TS, scope="all").rows]

    # The tie-break's CONTENT, not merely that two calls agree: `list.sort` is stable and the
    # merge order deterministic, so a report with NO tie-break also returns the same list twice.
    # Asserting repeatability alone passed against the missing tie-break.
    assert references == sorted(references, reverse=True), (
        "at one instant, `reference` descending is the order -- and it is what makes the page "
        "stop reshuffling between polls"
    )
    assert len(set(references)) == 3, "three distinct events, not one collapsed row"


def test_a_live_fill_is_venue_reported_and_a_paper_fill_is_not(
    repo: Repository, tmp_path: Path
) -> None:
    """THE distinction this feed exists to preserve. A paper fill was written by the paper trader
    with no venue involved; filing it under the same word as a real one would put synthetic and
    real evidence in one bucket, which is the thing four separate tables at least never did."""
    _order(repo, mode="live", created_at=NOW_TS - 60)
    _order(repo, mode="paper", created_at=NOW_TS - 120)

    rows = gather_timeline(repo, now_ts=NOW_TS, scope="all").rows
    by_provenance = {row.provenance for row in rows}

    assert "venue-reported" in by_provenance
    assert "simulated" in by_provenance


def test_an_imported_ledger_line_is_not_venue_reported(repo: Repository, tmp_path: Path) -> None:
    """A `transactions` row came out of a CSV the operator downloaded. That the venue produced
    the CSV does not make the row a venue REPORT -- nothing verified it on the way in."""
    _transaction(repo)

    row = gather_timeline(repo, now_ts=NOW_TS, scope="all").rows[0]

    assert row.kind == "flow"
    assert row.provenance == "imported-ledger"


def test_an_attestation_is_marked_as_human(repo: Repository, tmp_path: Path) -> None:
    _attestation(repo)

    row = gather_timeline(repo, now_ts=NOW_TS, scope="all").rows[0]

    assert row.kind == "attestation"
    assert row.provenance == "human-attested"
    assert "operator" in row.summary, "who swore to it belongs in the line"


def test_every_provenance_is_from_the_closed_vocabulary(repo: Repository, tmp_path: Path) -> None:
    """Same discipline as the payload's `state` words: a provenance a renderer has to interpret
    is one a renderer can get wrong."""
    _order(repo)
    _transaction(repo)
    _attestation(repo)

    for row in gather_timeline(repo, now_ts=NOW_TS, scope="all").rows:
        assert row.provenance in PROVENANCES
        assert row.kind in TIMELINE_KINDS


def test_each_row_names_the_store_it_came_from(repo: Repository, tmp_path: Path) -> None:
    """A reader chasing a row needs to know which table to open. `reference` is that store's own
    identifier, so the row is findable rather than merely described."""
    order_id = _order(repo)

    row = gather_timeline(repo, now_ts=NOW_TS, scope="all").rows[0]

    assert row.source == "orders"
    assert row.reference == str(order_id)


# -- the hash column is honest ------------------------------------------------------------------


def test_no_row_claims_a_hash_it_does_not_have(repo: Repository, tmp_path: Path) -> None:
    """Never blank, which a reader takes as "nothing to report", and never a hash computed HERE,
    which would be this module attesting to its own output.

    #703 shipped this as "every row says NOT RECORDED", which was the whole truth then. #721 made
    the three stores chain their writes, so the invariant is now the pairing rather than the
    constant: a hash and a `chained` status travel together, and the absence of one is the
    absence of both. A row showing a hash while claiming `not chained`, or claiming `chained`
    with nothing to show, would be a row asserting something the chain never said.
    """
    from keel.commands.timeline import CHAIN_STATUSES, HASH_NOT_RECORDED

    _order(repo)
    _transaction(repo)
    _attestation(repo)

    rows = gather_timeline(repo, now_ts=NOW_TS, scope="all").rows
    assert rows
    for row in rows:
        assert row.chain_status in CHAIN_STATUSES
        assert (row.row_hash == HASH_NOT_RECORDED) == (row.chain_status == "not chained")
        assert row.row_hash != ""


# -- filtering and scoping ----------------------------------------------------------------------


def test_the_kind_filter_narrows_server_side(repo: Repository, tmp_path: Path) -> None:
    _order(repo)
    _transaction(repo)
    _attestation(repo)

    report = gather_timeline(repo, now_ts=NOW_TS, scope="all", kind="flow")

    assert [row.kind for row in report.rows] == ["flow"]
    assert report.kind == "flow"


def test_the_scoped_count_counts_the_window_and_filtered_counts_the_chip(
    repo: Repository, tmp_path: Path
) -> None:
    """Two denominators, as on the Orders view: "3 of 12" under a Flows chip has to count flows,
    and `scoped_count` has to keep meaning the window or nothing can say the window is empty."""
    _order(repo)
    _transaction(repo)
    _attestation(repo)

    report = gather_timeline(repo, now_ts=NOW_TS, scope="all", kind="flow")

    assert report.scoped_count == 3
    assert report.filtered_count == 1


def test_the_scope_excludes_older_rows(repo: Repository, tmp_path: Path) -> None:
    _order(repo, created_at=TODAY_START + 60)
    _order(repo, created_at=TODAY_START - DAY)

    assert gather_timeline(repo, now_ts=NOW_TS, scope="today").scoped_count == 1


def test_the_kinds_present_drive_the_chips(repo: Repository, tmp_path: Path) -> None:
    """A chip bar built from what the window actually holds, in the declared order -- not one
    chip per kind keel can produce, which would invite a reader into three empty tabs."""
    _order(repo)
    _attestation(repo)

    assert gather_timeline(repo, now_ts=NOW_TS, scope="all").kinds_present == (
        "trade",
        "attestation",
    )


def test_the_chips_survive_having_one_of_them_selected(repo: Repository, tmp_path: Path) -> None:
    """A chip bar built from the SHOWN rows deletes its own alternatives: click Flows and the
    Trades chip vanishes, leaving no way back except knowing the empty string means all. It has
    to be built from the scoped set, before the chip narrows it."""
    _order(repo)
    _transaction(repo)
    _attestation(repo)

    filtered = gather_timeline(repo, now_ts=NOW_TS, scope="all", kind="flow")

    assert filtered.kinds_present == ("trade", "flow", "attestation")


def test_the_chips_survive_the_cap(repo: Repository, tmp_path: Path) -> None:
    """Same failure through the other narrowing: a kind whose only rows fell past the limit is
    still a kind this window holds."""
    _attestation(repo, attested_at=NOW_TS - 10800)
    for index in range(3):
        _order(repo, created_at=NOW_TS - index * 60)

    capped = gather_timeline(repo, now_ts=NOW_TS, scope="all", limit=2)

    assert capped.shown_count == 2
    assert "attestation" in capped.kinds_present


def test_the_limit_caps_the_rows_but_not_the_counts(repo: Repository, tmp_path: Path) -> None:
    """The merged feed reads four unbounded stores; the cap is what keeps one request bounded.
    The counts still describe the whole window, so a reader can see there is more."""
    for index in range(5):
        _order(repo, created_at=NOW_TS - index * 60)

    report = gather_timeline(repo, now_ts=NOW_TS, scope="all", limit=2)

    assert report.shown_count == 2
    assert report.scoped_count == 5
    assert report.rows[0].ts == NOW_TS, "the cap keeps the NEWEST rows"


def test_an_empty_book_is_an_empty_timeline(repo: Repository, tmp_path: Path) -> None:
    report = gather_timeline(repo, now_ts=NOW_TS, scope="all")

    assert report.rows == ()
    assert report.scoped_count == 0


# -- every cell of the export is neutralised, not only the one with a test (#703 review) --------


def test_every_text_column_of_the_export_is_neutralised(repo: Repository, tmp_path: Path) -> None:
    """`csv_safe` is applied to all ten cells; before this, only `reference` was pinned.

    Removing it from the other nine left the whole suite green -- so the module's load-bearing
    claim ("applied to EVERY text cell, not to a list of the risky ones") was untested for nine
    columns, including `summary`, which carries an imported `notes` field verbatim.

    Every hostile value below lands at the START of its own cell, which is the only position a
    spreadsheet evaluates.
    """
    from keel.commands.timeline import to_csv

    # Hostile values in every cell whose content this module does NOT write: the reference, the
    # summary's ingredients, and the amount. A fixture that leaves the rest keel-written pins
    # three cells while the docstring claims ten -- which is how the previous version of this
    # test passed while six columns were unprotected.
    _order(
        repo,
        product_id="=cmd|product",
        side="=cmd|side",
        status="=cmd|status",
        created_at=NOW_TS - 30,
    )
    _transaction(
        repo,
        coinbase_id="=cmd|ref",
        type="=cmd|kind",
        asset="=cmd|asset",
        notes="",
        total=Decimal("-500.25"),
    )

    rows = list(csv.reader(io.StringIO(to_csv(gather_timeline(repo, now_ts=NOW_TS, scope="all")))))
    header = rows[0]
    by_source = {r[header.index("source")]: dict(zip(header, r, strict=True)) for r in rows[1:]}

    order_cells = by_source["orders"]
    assert order_cells["product_id"].startswith("'"), "product_id"
    assert order_cells["summary"].startswith("'"), "summary -- it begins with the status"

    cells = by_source["transactions"]
    assert cells["reference"].startswith("'"), "reference"
    assert cells["summary"].startswith("'"), "summary -- it begins with the transaction type"
    # A negative amount is a formula trigger and a real figure. Quoted, and still legible.
    assert cells["amount"] == "'-500.25", cells["amount"]
    # And nothing that was safe got mangled.
    assert cells["kind"] == "flow"
    assert cells["provenance"] == "imported-ledger"


def test_the_export_carries_one_row_per_event_plus_a_header(
    repo: Repository, tmp_path: Path
) -> None:
    """The shape an auditor's spreadsheet sees."""
    from keel.commands.timeline import to_csv

    _order(repo)
    _transaction(repo)
    _attestation(repo)

    rows = list(csv.reader(io.StringIO(to_csv(gather_timeline(repo, now_ts=NOW_TS, scope="all")))))

    assert rows[0][0] == "ts"
    assert len(rows) == 4


# -- a log that could not be read is a STATED gap, not a failed page (#703 review round 2) ------


@pytest.mark.parametrize("status", ["ok", "missing", "empty", "oversized", "unreadable"])
def test_every_log_read_outcome_still_produces_a_report(status: str, repo, tmp_path) -> None:
    """Four sources, and one of them having nothing to say must not take out the other three.

    The first fix for this raised on any status but `ok`/`missing` -- which made an EMPTY log
    (an ordinary state: a freshly created handler, or the moment after a rotation) 500 the whole
    Timeline page, losing orders, flows and attestations to report a non-problem. `read_log_window`
    also returns `oversized` for one very long record, which is likewise not a read failure.

    The report carries the outcome instead, so the page and the CSV can SAY the log was
    unreadable rather than either failing or silently under-reporting.
    """
    _order(repo)

    report = gather_timeline(repo, now_ts=NOW_TS, scope="all", log_status=status)

    assert report.rows, "the other sources still report"
    assert report.log_status == status


def test_a_healthy_log_reports_no_gap(repo, tmp_path) -> None:
    _order(repo)

    assert gather_timeline(repo, now_ts=NOW_TS, scope="all", log_status="ok").log_gap is False


@pytest.mark.parametrize("status", ["empty", "oversized", "unreadable"])
def test_an_unhealthy_log_is_reported_as_a_gap(status: str, repo, tmp_path) -> None:
    """`missing` is not a gap -- a deployment that has never run has no log, and that is an
    ordinary fact rather than a hole in the record. The rest are: the file is there and its
    contents did not reach this report, which is exactly what an auditor needs told."""
    _order(repo)

    assert gather_timeline(repo, now_ts=NOW_TS, scope="all", log_status=status).log_gap is True


def test_a_missing_log_is_not_a_gap(repo, tmp_path) -> None:
    _order(repo)

    assert gather_timeline(repo, now_ts=NOW_TS, scope="all", log_status="missing").log_gap is False


def test_the_kind_collapse_is_applied_and_echoed(repo, tmp_path) -> None:
    """An unrecognised kind is collapsed to "every kind", not applied -- `?kind=trades` (the
    plural typo, since the chips read "trade") would otherwise return a page that looks like an
    empty deployment. Unpinned until now: reverting the collapse left every test green."""
    _order(repo)
    _transaction(repo)

    report = gather_timeline(repo, now_ts=NOW_TS, scope="all", kind="trades")

    assert report.kind == "", "the applied value is echoed, so the substitution is visible"
    assert report.shown_count == 2, "and nothing was filtered out"


# -- the hash column, once the engine records one (#721) ----------------------------------------
#
# #703 shipped `row_hash` reading NOT RECORDED on every row because none of the four stores hashed
# anything. `keel/data/audit.py` now does. These pin the three readings the column has to keep
# apart: a hash the chain vouches for, an honest gap where no event was ever written, and a hash
# that exists inside the region a break has invalidated. The third is the one that must not be
# allowed to look like the first.


def _chained_repo(conn) -> Repository:
    """A repository whose writers have recorded events for everything they wrote."""
    repo = Repository(conn)
    repo.insert_order(
        {
            "mode": "live",
            "product_id": "BTC-USD",
            "side": "buy",
            "qty": Decimal("1"),
            "status": "filled",
            "created_at": 1_000,
        }
    )
    repo.upsert_transaction(
        {
            "coinbase_id": "cb-1",
            "source": "coinbase",
            "type": "deposit",
            "asset": "USD",
            "qty": Decimal("250"),
            "ts": 1_100,
        }
    )
    repo.upsert_asset_attestation(
        asset="BTC",
        sector="tech",
        backing="none",
        pays_yield=False,
        source="prospectus",
        attested_by="operator",
        attested_at=1_200,
    )
    return repo


def test_a_chained_row_carries_its_recorded_hash(db_conn) -> None:
    """The swap #703 built the column for: a real hash where the engine recorded one."""
    repo = _chained_repo(db_conn)
    report = timeline.gather_timeline(repo, now_ts=2_000)

    by_source = {row.source: row for row in report.rows}
    for source in ("orders", "transactions", "asset_attestations"):
        assert by_source[source].row_hash != timeline.HASH_NOT_RECORDED
        assert len(by_source[source].row_hash) == 64
        assert by_source[source].chain_status == "chained"


def test_the_hash_shown_is_the_hash_recorded_for_that_row(db_conn) -> None:
    """Looked up by `(source, reference)` -- the pair printed beside it. A lookup keyed on the
    entity alone would hand an `orders.id` of "1" the hash of a `coinbase_id` of "1"."""
    repo = _chained_repo(db_conn)
    report = timeline.gather_timeline(repo, now_ts=2_000)

    recorded = audit.latest_hashes(db_conn)
    for row in report.rows:
        if row.chain_status == "chained":
            assert row.row_hash == recorded[(row.source, row.reference)]


def test_a_row_written_before_the_chain_shipped_reads_as_not_chained(db_conn) -> None:
    """NO BACKFILL, on the surface that shows it. An honest gap -- and crucially NOT a break: a
    deployment upgrading into #721 must not open its timeline to a page of red."""
    db_conn.execute(
        "INSERT INTO orders (mode, product_id, side, qty, status, created_at) "
        "VALUES ('live','BTC-USD','buy','1','filled',900)"
    )
    db_conn.commit()
    repo = _chained_repo(db_conn)
    report = timeline.gather_timeline(repo, now_ts=2_000)

    unchained = [row for row in report.rows if row.reference == "1" and row.source == "orders"]
    assert len(unchained) == 1
    assert unchained[0].row_hash == timeline.HASH_NOT_RECORDED
    assert unchained[0].chain_status == "not chained"
    assert report.chain_intact is True


def test_an_engine_log_row_is_never_chained_and_says_so(db_conn) -> None:
    """The engine log is a FILE, not a chained store. A cycle row carrying a hash would be this
    module attesting to something it only read."""
    repo = _chained_repo(db_conn)
    cycle = SimpleNamespace(
        started_ts=1_500, cycle_id="c-1", products=("BTC-USD",), signals=1, entered=1, exited=0,
        errors=0,
    )
    report = timeline.gather_timeline(repo, now_ts=2_000, cycles=[cycle])

    system = [row for row in report.rows if row.kind == "system"]
    assert len(system) == 1
    assert system[0].row_hash == timeline.HASH_NOT_RECORDED
    assert system[0].chain_status == "not chained"


def test_a_break_marks_the_rows_the_chain_no_longer_vouches_for(db_conn) -> None:
    """Editing one event does not merely fail that row: the chain proves a SEQUENCE, so every row
    from the break onward is unverified. A page that showed those later hashes as `chained` would
    be presenting unverified values as evidence -- the one thing this column exists to prevent."""
    repo = _chained_repo(db_conn)
    db_conn.execute("UPDATE audit_events SET payload_json = ? WHERE seq_id = 1", ('{"a":1}',))
    db_conn.commit()

    report = timeline.gather_timeline(repo, now_ts=2_000)
    assert report.chain_intact is False
    assert report.chain_errors
    assert {row.chain_status for row in report.rows} == {"chain broken"}


def test_a_break_leaves_earlier_rows_verified(db_conn) -> None:
    """The break is located, not global. Rows chained BEFORE it are still vouched for, and
    reporting them as broken would throw away the evidence the chain does hold."""
    repo = _chained_repo(db_conn)
    db_conn.execute("UPDATE audit_events SET payload_json = ? WHERE seq_id = 3", ('{"a":1}',))
    db_conn.commit()

    report = timeline.gather_timeline(repo, now_ts=2_000)
    statuses = {row.source: row.chain_status for row in report.rows}
    assert statuses["orders"] == "chained"
    assert statuses["transactions"] == "chained"
    assert statuses["asset_attestations"] == "chain broken"


def test_an_empty_chain_is_reported_as_unchecked_not_as_verified(db_conn) -> None:
    """`verify` over zero rows returns no errors, and that is not "verified" -- nothing was read.
    The report must not offer a green verdict over a table nothing wrote."""
    db_conn.execute(
        "INSERT INTO orders (mode, product_id, side, qty, status, created_at) "
        "VALUES ('live','BTC-USD','buy','1','filled',900)"
    )
    db_conn.commit()
    report = timeline.gather_timeline(Repository(db_conn), now_ts=2_000)
    assert report.chain_recorded is False
    assert report.chain_intact is True
    assert report.chain_errors == ()


def test_the_export_carries_the_chain_status_beside_the_hash(db_conn) -> None:
    """A hash with nothing saying whether it verifies is a number an auditor cannot use. Both
    columns, in the same file, on every row."""
    repo = _chained_repo(db_conn)
    text = timeline.to_csv(timeline.export_rows(repo, now_ts=2_000))
    rows = list(csv.reader(io.StringIO(text)))

    assert rows[0][-2:] == ["row_hash", "chain_status"]
    for row in rows[1:]:
        assert row[-1] in ("chained", "not chained", "chain broken")


def test_a_broken_chain_is_stated_above_the_header_not_only_per_row(db_conn) -> None:
    """The same reasoning as the engine-log note this file already carries: an auditor holding a
    CSV cannot ask the page anything, so a finding that changes how the whole file should be read
    belongs in the file."""
    repo = _chained_repo(db_conn)
    db_conn.execute("UPDATE audit_events SET payload_json = ? WHERE seq_id = 1", ('{"a":1}',))
    db_conn.commit()

    text = timeline.to_csv(timeline.export_rows(repo, now_ts=2_000))
    assert text.splitlines()[0].startswith("# NOTE")
    assert "chain" in text.splitlines()[0]
