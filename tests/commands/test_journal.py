"""`keel journal` -- the CLI that is the ONLY way in (#705).

The constitution's line is that attestations are human-sourced or refused. This is the purest
case of it in the codebase: the entry is a person's account of their own conduct, and there is no
other source it could come from. So there is no web form, no `--json` input, no pipe, and no
default that would let an unattended process record a feeling on someone's behalf.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from click.testing import CliRunner

from keel.cli import cli
from keel.commands import journal as journal_mod
from keel.data.db import connect, migrate
from keel.data.repository import Repository

NOW = 1_756_000_000


@pytest.fixture()
def deployment(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """A migrated database and a CLI that believes it is at a terminal."""
    db_path = tmp_path / "keel.db"
    conn = connect(str(db_path))
    migrate(conn)
    conn.close()
    monkeypatch.setattr("keel.commands._common._is_interactive", lambda: True)
    return db_path


def _run(deployment, args: list[str], stdin: str = "") -> object:
    return CliRunner().invoke(cli, ["--db", str(deployment), *args], input=stdin)


def _entries(deployment) -> list[dict]:
    conn = connect(str(deployment))
    migrate(conn)
    return Repository(conn).get_journal_entries()


# -- the refusals ------------------------------------------------------------------------------


def test_adding_an_entry_off_a_terminal_is_refused(deployment, monkeypatch) -> None:
    """Fails closed off a TTY, so cron, a pipe and a script can never write a feeling into
    someone's journal. The same gate `resume` and `assets attest` sit behind."""
    monkeypatch.setattr("keel.commands._common._is_interactive", lambda: False)
    result = _run(deployment, ["journal", "add"])

    assert result.exit_code != 0
    assert "interactive terminal" in result.output
    assert _entries(deployment) == []


def test_the_add_command_takes_no_value_options_at_all(deployment) -> None:
    """Not merely "it prompts": a `--emotion 3` would make the whole entry scriptable, and the
    TTY gate would then guard a ceremony that no longer needed a human to supply anything."""
    result = _run(deployment, ["journal", "add", "--help"])
    for smuggled in ("--emotion", "--rules-followed", "--errors", "--impact", "--note", "--json"):
        assert smuggled not in result.output, f"`journal add` accepts {smuggled}"


def test_nothing_outside_the_cli_can_reach_the_writer() -> None:
    """No web write path, by construction. `keel serve` has one write surface (`/api/setup`), and
    the journal is not on it -- pinned here rather than trusted, because the day someone adds a
    "quick note" box to the console is the day this record stops being an attestation."""
    from keel.web import api

    source = (api.__file__ or "").replace(".pyc", ".py")
    text = open(source, encoding="utf-8").read()
    assert "append_journal_entry" not in text


# -- what it writes ----------------------------------------------------------------------------


def test_a_full_entry_is_recorded_exactly_as_typed(deployment, monkeypatch) -> None:
    monkeypatch.setattr(journal_mod.time, "time", lambda: NOW)
    result = _run(
        deployment,
        ["journal", "add"],
        stdin="3\ny\nentered before the close confirmed\n-42.50\nrange top\n~/shot.png\n",
    )
    assert result.exit_code == 0, result.output

    (entry,) = _entries(deployment)
    assert entry["ts"] == NOW
    assert entry["emotion_score"] == "3"
    assert entry["rules_followed"] is True
    assert entry["errors_made"] == "entered before the close confirmed"
    assert entry["dollar_impact"] == Decimal("-42.50")
    assert entry["chart_note"] == "range top"
    assert entry["screenshot_ref"] == "~/shot.png"


def test_a_skipped_field_is_recorded_as_unsaid_not_as_a_zero(deployment) -> None:
    """The refusal in the issue: no pre-filled emotion scores. An empty answer must reach the
    database as NULL, because a `0` impact and a `3` emotion are claims the operator did not
    make."""
    result = _run(deployment, ["journal", "add"], stdin="\n\n\n\n\n\n")
    assert result.exit_code == 0, result.output

    (entry,) = _entries(deployment)
    for field in ("emotion_score", "rules_followed", "errors_made", "dollar_impact"):
        assert entry[field] is None, f"{field} was invented from a blank answer"


def test_an_emotion_score_outside_the_scale_is_refused_not_stored(deployment) -> None:
    """A score needs a scale or it cannot be compared with itself next week. 1-5, stated in the
    prompt, and a value off it is rejected rather than quietly kept as free text."""
    result = _run(deployment, ["journal", "add"], stdin="9\n\n\n\n\n\n")

    assert result.exit_code != 0
    assert "1" in result.output and "5" in result.output
    assert _entries(deployment) == []


def test_an_unparseable_dollar_impact_is_refused_not_rounded(deployment) -> None:
    result = _run(deployment, ["journal", "add"], stdin="\n\n\nlots\n\n\n")

    assert result.exit_code != 0
    assert _entries(deployment) == []


# -- reading it back ---------------------------------------------------------------------------


def test_list_reads_the_entries_back_oldest_first(deployment) -> None:
    conn = connect(str(deployment))
    migrate(conn)
    repo = Repository(conn)
    repo.append_journal_entry(ts=200, chart_note="second")
    repo.append_journal_entry(ts=100, chart_note="first")
    conn.close()

    result = _run(deployment, ["journal", "list"])
    assert result.exit_code == 0, result.output
    assert result.output.index("first") < result.output.index("second")


def test_list_says_so_when_there_is_nothing_rather_than_printing_a_bare_header(
    deployment,
) -> None:
    result = _run(deployment, ["journal", "list"])
    assert result.exit_code == 0
    assert "No journal entries" in result.output


def test_the_report_marks_every_entry_self_reported(deployment) -> None:
    """The marker the issue asks for, on the CLI too. An operator reading their own journal
    beside `keel insights journal` -- which is closed TRADES, a different thing wearing a similar
    name -- must not have to remember which is which."""
    conn = connect(str(deployment))
    migrate(conn)
    Repository(conn).append_journal_entry(ts=100, chart_note="a note")
    conn.close()

    result = _run(deployment, ["journal", "list"])
    assert "SELF-REPORTED" in result.output


# -- the acceptance round trip ------------------------------------------------------------------


def test_cli_add_then_web_view_then_csv_export_all_carry_the_marker(deployment, monkeypatch):
    """The issue's acceptance criterion, end to end and in one test.

    Three surfaces read this record and each could lose the provenance separately: the CLI that
    wrote it, the console that renders it, and the CSV an operator hands to someone else. The
    export is the one that leaves the machine, so it is the one where a self-assessment sitting
    unmarked beside venue-reported fills would do real harm.
    """
    from keel.commands.journal import SELF_REPORTED
    from keel.commands.timeline import export_rows, to_csv
    from keel.web import payload

    monkeypatch.setattr(journal_mod.time, "time", lambda: NOW)
    written = _run(deployment, ["journal", "add"], stdin="2\nn\nchased it\n-42.50\nthin\n\n")
    assert written.exit_code == 0, written.output

    conn = connect(str(deployment))
    migrate(conn)
    repo = Repository(conn)

    # 1. The CLI reads it back with the marker.
    listed = _run(deployment, ["journal", "list"])
    assert SELF_REPORTED in listed.output
    assert "NO" in listed.output, "a broken rule must be legible, not merely stored"

    # 2. The web payload carries the marker and grades the CLAIM rather than the operator.
    #
    # `_discretionary_journal_payload` directly rather than the whole `journal_payload`: the
    # closed-trade half needs a full `StatusReport` this test has no business building, and that
    # the two halves are composed onto one route is pinned by `test_client_assets.py`'s parity
    # scan over `/api/journal`'s `notes.entries`.
    notes = payload._discretionary_journal_payload(journal_mod.gather_journal(repo, now_ts=NOW))
    assert notes["marker"] == SELF_REPORTED
    (row,) = notes["entries"]
    assert row["dollar_impact"]["state"] == "warn", "a self-reported figure must not read as a fill"
    assert row["rules_followed"]["state"] == "warn"
    assert "BROKE" in row["rules_followed"]["display"]

    # 3. The export names it as self-reported beside the venue-reported rows.
    text = to_csv(export_rows(repo, now_ts=NOW + 10))
    lines = [line for line in text.splitlines() if "journal" in line]
    assert len(lines) == 1
    assert "self-reported" in lines[0]
    assert "chased it" in lines[0]
