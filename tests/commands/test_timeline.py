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
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from keel.commands.timeline import PROVENANCES, TIMELINE_KINDS, gather_timeline
from keel.data.db import connect, migrate
from keel.data.repository import Repository

NOW_TS = 1_800_000_000
DAY = 86_400
TODAY_START = 1_799_971_200  # 2027-01-15T00:00:00Z


@pytest.fixture()
def repo(tmp_path: Path) -> Repository:
    conn = connect(str(tmp_path / "keel.db"))
    migrate(conn)
    return Repository(conn)


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

    first = [row.reference for row in gather_timeline(repo, now_ts=NOW_TS, scope="all").rows]
    again = [row.reference for row in gather_timeline(repo, now_ts=NOW_TS, scope="all").rows]

    assert first == again
    assert len(set(first)) == 3, "three distinct events, not one collapsed row"


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
    """#703 asked for tamper-evidence. None of these four stores hashes its rows, so every row
    says NOT RECORDED -- never blank, which a reader takes as "nothing to report", and never a
    hash computed here, which would be this module attesting to its own output."""
    from keel.commands.timeline import HASH_NOT_RECORDED

    _order(repo)
    _transaction(repo)
    _attestation(repo)

    for row in gather_timeline(repo, now_ts=NOW_TS, scope="all").rows:
        assert row.row_hash == HASH_NOT_RECORDED


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

    _transaction(
        repo,
        coinbase_id="=cmd|ref",
        type="=cmd|kind",
        asset="=cmd|asset",
        notes="",
        total=Decimal("-500.25"),
    )

    text = to_csv(gather_timeline(repo, now_ts=NOW_TS, scope="all"))
    header, row = list(csv.reader(io.StringIO(text)))[:2]
    cells = dict(zip(header, row, strict=True))

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
