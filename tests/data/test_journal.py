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

    # And the SQL, over the whole package -- a name sweep misses `amend_`, `edit_`, a generic
    # executor, and anything that reaches the table without a method at all. This is the property;
    # the names above are the readable half of it.
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "keel"
    offenders = [
        path
        for path in root.rglob("*.py")
        for text in [path.read_text(encoding="utf-8")]
        if "UPDATE journal" in text or "DELETE FROM journal" in text
    ]
    assert offenders == [], f"the journal is mutated in {offenders}"


def test_the_limit_breaks_a_timestamp_tie_by_id(repo: Repository) -> None:
    """With three entries in one second and a cap of two, "newest" means the two written LAST.

    **This test's own blind spot, named.** It pins the OUTCOME, not the `id DESC` clause that
    guarantees it: measured on this SQLite build, dropping `id DESC` from the subquery returns the
    same two rows, because a bare `ORDER BY ts DESC` happens to fall back to rowid order. That is
    an implementation detail of one engine and not a promise, so the clause stays -- the sibling
    `get_equity_points` states the reasoning ("applied in BOTH directions so the newest-N and the
    oldest-first re-order agree about which of two same-second readings is the newer") -- but no
    test in this suite can currently make its absence fail, and saying so beats implying otherwise.
    """
    for note in ("first", "second", "third"):
        repo.append_journal_entry(ts=1_000, chart_note=note)

    assert [e["chart_note"] for e in repo.get_journal_entries(limit=2)] == ["second", "third"]


def test_the_count_is_the_window_before_the_limit(repo: Repository) -> None:
    """What lets a bounded read SAY what it left out -- the rule `get_equity_points`' docstring
    states and `count_equity_points` exists to serve."""
    for ts in (100, 200, 300):
        repo.append_journal_entry(ts=ts)

    assert repo.count_journal_entries() == 3
    assert len(repo.get_journal_entries(limit=1)) == 1
    assert repo.count_journal_entries(since_ts=200) == 2
    assert repo.count_journal_entries(since_ts=200, until_ts=300) == 1


def test_an_empty_string_is_read_back_as_unsaid(repo: Repository) -> None:
    """`label(None)` is `absent()` and `label("")` is an empty cell, so a column holding `""`
    would render as a second, different-looking spelling of "did not say"."""
    from keel.commands.journal import gather_journal

    repo.append_journal_entry(ts=1, chart_note="", errors_made="", emotion_score="")
    (entry,) = gather_journal(repo, now_ts=2).entries

    assert entry.chart_note is None
    assert entry.errors_made is None
    assert entry.emotion_score is None


def test_a_zero_or_negative_limit_is_refused_rather_than_obeyed(repo: Repository) -> None:
    """SQLite reads a NEGATIVE `LIMIT` as unbounded, so `--limit -1` would silently print
    everything; a zero returns no rows, and an empty result is indistinguishable from an empty
    journal on both front-ends. That is the hazard `keel/web/api.py::_journal_limit` was written
    to name, and refusing is the only reading that cannot lie.

    Refused in `gather_journal` rather than only at the click option, so the guard travels with
    the function instead of with one of its callers.
    """
    from keel.commands.journal import gather_journal

    repo.append_journal_entry(ts=1)
    for bad in (0, -1):
        with pytest.raises(ValueError, match="limit"):
            gather_journal(repo, now_ts=2, limit=bad)


def test_a_report_knows_when_it_is_a_page_of_a_longer_journal(repo: Repository) -> None:
    """The flag that lets both front-ends say "50 of 301" instead of showing a short list that
    reads as a complete one."""
    from keel.commands.journal import gather_journal

    for ts in (100, 200, 300):
        repo.append_journal_entry(ts=ts)

    whole = gather_journal(repo, now_ts=400)
    assert whole.truncated is False
    assert (whole.entry_count, whole.total_count) == (3, 3)

    page = gather_journal(repo, now_ts=400, limit=2)
    assert page.truncated is True
    assert (page.entry_count, page.total_count) == (2, 3)


def test_an_empty_window_is_not_the_same_as_an_empty_journal(repo: Repository) -> None:
    """`any_recorded` reads `total_count`, not `entries`. A cap or a date bound that excluded
    everything is not a deployment with no journal, and the renderers say different things."""
    from keel.commands.journal import JournalReport, gather_journal

    assert gather_journal(repo, now_ts=1).any_recorded is False
    repo.append_journal_entry(ts=100)
    assert gather_journal(repo, now_ts=200).any_recorded is True
    assert JournalReport(now_ts=1, entries=(), total_count=7).any_recorded is True
