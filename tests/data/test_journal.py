"""The discretionary journal's storage layer (#705).

The `journal` table has been declared in the schema since the beginning and had NO repository
method and no read or write path anywhere in the code -- dead schema, which is worse than no
schema because a reader assumes a declared table is a used one.

What it records is unlike anything else in this database: the operator's own account of their own
conduct. Everything else here is a machine's observation or a claim about the world that a
document could contradict. A self-assessment cannot be checked at all, and the whole point of
keeping it is that it stays visibly separate from the things that can.
"""

from __future__ import annotations

import sqlite3
from decimal import Decimal

import pytest

from keel.data import audit, db
from keel.data.repository import Repository


@pytest.fixture()
def repo() -> Repository:
    conn = db.connect(":memory:")
    db.migrate(conn)
    return Repository(conn)


def test_an_entry_round_trips_every_field(repo: Repository) -> None:
    repo.append_journal_entry(
        ts=1_000,
        emotion_score="3",
        rules_followed=True,
        errors_made="entered before the close confirmed",
        dollar_impact=Decimal("-42.50"),
        chart_note="range top, thin book",
        screenshot_ref="~/shots/2026-09-05.png",
    )
    (entry,) = repo.get_journal_entries()

    assert entry["ts"] == 1_000
    assert entry["emotion_score"] == "3"
    assert entry["rules_followed"] is True
    assert entry["errors_made"] == "entered before the close confirmed"
    assert entry["dollar_impact"] == Decimal("-42.50")
    assert entry["chart_note"] == "range top, thin book"
    assert entry["screenshot_ref"] == "~/shots/2026-09-05.png"


def test_dollar_impact_keeps_its_scale_and_its_sign(repo: Repository) -> None:
    """Money is `Decimal` stored as TEXT, like every other money column here. A float would
    make a self-reported dollar impact disagree with itself between writes."""
    repo.append_journal_entry(ts=1, dollar_impact=Decimal("-0.10"))
    (entry,) = repo.get_journal_entries()
    assert entry["dollar_impact"] == Decimal("-0.10")
    assert str(entry["dollar_impact"]) == "-0.10"


def test_every_field_but_the_timestamp_may_be_absent(repo: Repository) -> None:
    """The schema makes only `ts` NOT NULL, and that is the right shape for this table: an
    operator who wants to record one sentence about one day must not have to invent an emotion
    score to do it. `None` is "did not say", never a zero or an empty string."""
    repo.append_journal_entry(ts=1)
    (entry,) = repo.get_journal_entries()

    for field in ("emotion_score", "rules_followed", "errors_made", "dollar_impact"):
        assert entry[field] is None, f"{field} invented a value"


def test_rules_followed_is_three_valued(repo: Repository) -> None:
    """`bool(None)` is `False`, and `False` here is a POSITIVE claim -- "I broke my rules". An
    operator who skipped the question has not confessed to anything."""
    repo.append_journal_entry(ts=1, rules_followed=None)
    repo.append_journal_entry(ts=2, rules_followed=False)
    repo.append_journal_entry(ts=3, rules_followed=True)

    assert [e["rules_followed"] for e in repo.get_journal_entries()] == [None, False, True]


def test_entries_come_back_oldest_first(repo: Repository) -> None:
    for ts in (300, 100, 200):
        repo.append_journal_entry(ts=ts)
    assert [e["ts"] for e in repo.get_journal_entries()] == [100, 200, 300]


def test_two_entries_at_one_instant_keep_a_stable_order(repo: Repository) -> None:
    """`id` breaks the tie. A journal is a sequence of what someone wrote, and two notes on one
    day must not swap places between reads."""
    first = repo.append_journal_entry(ts=1, chart_note="first")
    second = repo.append_journal_entry(ts=1, chart_note="second")
    assert first < second
    assert [e["chart_note"] for e in repo.get_journal_entries()] == ["first", "second"]


def test_the_date_bounds_are_inclusive_of_since_and_exclusive_of_until(repo: Repository) -> None:
    """Half-open, matching every other window in this codebase (`get_candles`,
    `scope_start_ts`), so two adjacent windows cover a range without double-counting the seam."""
    for ts in (100, 200, 300):
        repo.append_journal_entry(ts=ts)

    assert [e["ts"] for e in repo.get_journal_entries(since_ts=200)] == [200, 300]
    assert [e["ts"] for e in repo.get_journal_entries(until_ts=300)] == [100, 200]
    assert [e["ts"] for e in repo.get_journal_entries(since_ts=200, until_ts=300)] == [200]


def test_the_limit_keeps_the_NEWEST_entries(repo: Repository) -> None:
    """A capped read of a journal wants the recent end -- and still returns them oldest-first, so
    the cap changes how many entries a caller sees and never which way they read."""
    for ts in (100, 200, 300):
        repo.append_journal_entry(ts=ts, chart_note=str(ts))

    assert [e["ts"] for e in repo.get_journal_entries(limit=2)] == [200, 300]


def test_an_entry_is_chained_like_every_other_attestation(repo: Repository) -> None:
    """#721's chain covers what a human swore to. A journal entry is the most purely
    human-sourced record in this database, and the export shows it beside orders and fills."""
    repo.append_journal_entry(ts=1_000, chart_note="a note")

    conn = repo._conn  # noqa: SLF001 - the chain is read by store, not by repository method
    events = audit.read_events(conn)
    assert [e.event_type for e in events] == ["journal_recorded"]
    assert audit.chain_state(conn).errors == ()


def test_the_event_is_filed_under_the_rows_own_id(repo: Repository) -> None:
    """`journal` has no natural key -- no `coinbase_id`, no asset, no venue pair. The row id is
    the only identifier, and it is what `commands/timeline.py` prints as the reference."""
    entry_id = repo.append_journal_entry(ts=1_000)
    conn = repo._conn  # noqa: SLF001
    assert [e.entity_id for e in audit.read_events(conn)] == [str(entry_id)]


def test_an_entry_and_its_chain_row_land_together_or_not_at_all(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr("keel.data.repository.append_event", _boom)
    with pytest.raises(sqlite3.OperationalError):
        repo.append_journal_entry(ts=1_000)

    assert repo.get_journal_entries() == []


def test_the_journal_table_is_no_longer_dead_schema(repo: Repository) -> None:
    """The condition that made #705 necessary, pinned so it cannot return.

    A declared table with no repository method is worse than no table: a reader assumes a declared
    one is used, and `journal` sat in the schema from the beginning with no read or write path
    anywhere in the code. This asserts the methods exist AND that they reach the real table --
    a method that wrote somewhere else would satisfy a name check and nothing else.
    """
    assert hasattr(repo, "append_journal_entry")
    assert hasattr(repo, "get_journal_entries")

    repo.append_journal_entry(ts=1, chart_note="proof")
    stored = repo._conn.execute("SELECT chart_note FROM journal").fetchall()  # noqa: SLF001
    assert [row["chart_note"] for row in stored] == ["proof"]


def test_there_is_no_way_to_edit_or_delete_an_entry(repo: Repository) -> None:
    """Append-only, and the absence is the design: a journal you can go back and change is a
    journal that records what you wish you had thought. Pinned by name, because the natural thing
    for a later contributor to add beside two existing methods is a third that updates."""
    for forbidden in ("update_journal_entry", "delete_journal_entry", "set_journal_entry"):
        assert not hasattr(repo, forbidden), f"Repository grew {forbidden}"
