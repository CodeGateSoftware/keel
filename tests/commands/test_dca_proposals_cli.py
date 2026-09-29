"""`keel dca proposals list` / `show <id>` -- the sell-proposals log, read-only (#857, plan P6
Task 6.3), and `sleeve_report.render_proposal`, the one renderer P8's notification and P18's
confirm banner reuse.

Read-only ALWAYS, not only off a TTY: these commands never write, so they open the database
through `_common._open_repo_ro` whatever the terminal, which refuses a missing `--db` path or a
stale schema before anything could be created or migrated (R20).
"""

from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest
from click.testing import CliRunner

from keel.cli import cli
from keel.commands import _common, sleeve_report
from keel.data import db as keel_db
from tests.commands.test_dca_cli import _repo, deployment  # noqa: F401 - the fixture
from tests.data.test_sell_proposals import _row

#: 2026-09-28T12:00:00Z and 2026-09-29T12:00:00Z.
_TS1 = 1_790_596_800
_TS2 = _TS1 + 86_400


def _invoke(deployment_pair, *args: str):
    db, config = deployment_pair
    return CliRunner().invoke(
        cli, ["--db", str(db), "--config", str(config), "dca", "proposals", *args]
    )


def _proposal_lines(output: str) -> list[str]:
    return [line for line in output.splitlines() if line.startswith("#")]


# -- render_proposal ---------------------------------------------------------------------------


def _stored(**over) -> dict:
    """A row as `get_sell_proposal` returns it: id and the nullable columns present."""
    row = _row(ts=_TS1, **over)
    row.setdefault("id", 3)
    for key in ("superseded_by", "order_id", "reviewed_ts"):
        row.setdefault(key, None)
    return row


def test_the_summary_line_is_the_documented_format() -> None:
    line = sleeve_report.render_proposal_line(_stored())
    assert line == (
        "#3 2026-09-28 BTC-USD reverse_dca (rule 7, live) sell 0.00092 @ 110000"
        "  gross $101.15  fee $1.21 (venue_preview)  net $7.11  legs 1  -> preview"
    )


def test_superseded_and_reviewed_are_appended_only_when_set() -> None:
    line = sleeve_report.render_proposal_line(
        _stored(decision="superseded", superseded_by="sleeve_exit", reviewed_ts=_TS2)
    )
    head, _, tail = line.partition("  -> ")
    assert head.endswith("legs 1")
    assert tail.split("  ") == ["superseded", "superseded by sleeve_exit", "reviewed 2026-09-29"]
    assert sleeve_report.render_proposal_line(_stored()).partition("  -> ")[2] == "preview"


def test_an_unrecorded_figure_reads_as_unrecorded_never_as_zero() -> None:
    line = sleeve_report.render_proposal_line(_stored(rule_id=None, expected_net_pnl=None))
    assert "(rule unrecorded, live)" in line.split(" sell ")[0]
    fields = line.split("  ")
    assert "net unrecorded" in fields
    assert not any(f.startswith("net $") for f in fields)


def test_vwae_and_cost_basis_print_unrecorded_when_null() -> None:
    row = _stored(vwae=None, cost_basis=None)
    lines = sleeve_report.render_proposal(row)
    detail = dict(line.strip().split(": ", 1) for line in lines[1:])
    assert detail["vwae"] == "unrecorded"
    assert detail["cost basis"] == "unrecorded"
    assert "  vwae: unrecorded" in lines
    assert "  cost basis: unrecorded" in lines


def test_render_proposal_leads_with_the_line_then_the_detail() -> None:
    row = _stored(
        fee_source="fallback:config.fees.taker_pct",
        rails={"violations": ["kill_switch: engaged"], "skipped": []},
    )
    lines = sleeve_report.render_proposal(row)
    assert lines[0] == sleeve_report.render_proposal_line(row)
    detail = dict(line.strip().split(": ", 1) for line in lines[1:])
    assert detail["fee source"] == "fallback:config.fees.taker_pct"
    assert detail["legs"] == "1"
    assert detail["rails"] == '{"skipped": [], "violations": ["kill_switch: engaged"]}'
    assert detail["trigger"] == '{"cadence_day": 20000}'
    assert detail["vwae"] == "100900"
    assert detail["cost basis"] == "$92.83"
    assert detail["order"] == "none"


def test_render_proposal_prints_the_order_id_when_placed() -> None:
    row = _stored(decision="placed", order_id=12)
    lines = sleeve_report.render_proposal(row)
    detail = dict(line.strip().split(": ", 1) for line in lines[1:])
    assert detail["order"] == "#12"


# -- the CLI -----------------------------------------------------------------------------------


@pytest.mark.parametrize("interactive", [False, True])
def test_list_prints_newest_first_and_writes_nothing(deployment, monkeypatch, interactive) -> None:  # noqa: F811
    monkeypatch.setattr(_common, "_is_interactive", lambda: interactive)
    db, _config = deployment
    repo = _repo(db)
    first = repo.insert_sell_proposal(_row(ts=_TS1))
    second = repo.insert_sell_proposal(
        _row(ts=_TS2, decision="vetoed", rails={"violations": ["kill_switch: engaged"]})
    )
    watcher = sqlite3.connect(str(db))
    before = watcher.execute("PRAGMA data_version").fetchone()[0]

    result = _invoke(deployment, "list")

    assert result.exit_code == 0, result.output
    lines = _proposal_lines(result.output)
    assert len(lines) == 2
    assert lines[0] == sleeve_report.render_proposal_line(repo.get_sell_proposal(second))
    assert lines[1] == sleeve_report.render_proposal_line(repo.get_sell_proposal(first))
    assert lines[0].endswith("-> vetoed") and lines[1].endswith("-> preview")
    assert watcher.execute("PRAGMA data_version").fetchone()[0] == before
    watcher.close()


def test_list_filters_by_product(deployment) -> None:  # noqa: F811
    db, _config = deployment
    repo = _repo(db)
    repo.insert_sell_proposal(_row(ts=_TS1))
    paxg = repo.insert_sell_proposal(_row(ts=_TS2, product_id="PAXG-USD"))

    result = _invoke(deployment, "list", "--product", "PAXG-USD")

    assert result.exit_code == 0, result.output
    assert _proposal_lines(result.output) == [
        sleeve_report.render_proposal_line(repo.get_sell_proposal(paxg))
    ]


def test_an_empty_log_says_so(deployment) -> None:  # noqa: F811
    result = _invoke(deployment, "list")
    assert result.exit_code == 0, result.output
    assert _proposal_lines(result.output) == []
    assert "no sell proposals recorded" in result.output


def test_show_prints_the_fee_source_and_the_rails(deployment) -> None:  # noqa: F811
    db, _config = deployment
    repo = _repo(db)
    pid = repo.insert_sell_proposal(
        _row(ts=_TS1, fee_source="fallback:config.fees.taker_pct", expected_fee=Decimal("1.33"))
    )

    result = _invoke(deployment, "show", str(pid))

    assert result.exit_code == 0, result.output
    rendered = sleeve_report.render_proposal(repo.get_sell_proposal(pid))
    body = [line for line in result.output.splitlines() if line in rendered]
    assert body == rendered
    assert "  fee source: fallback:config.fees.taker_pct" in rendered
    assert "  legs: 1" in rendered


def test_show_of_a_missing_id_exits_1(deployment) -> None:  # noqa: F811
    result = _invoke(deployment, "show", "99")
    assert result.exit_code == 1
    assert "no sell proposal #99" in result.output


@pytest.mark.parametrize("interactive", [False, True])
def test_a_missing_database_is_refused_not_created(
    tmp_path: Path, valid_config_path, monkeypatch, interactive
) -> None:
    monkeypatch.setattr(_common, "_is_interactive", lambda: interactive)
    db = tmp_path / "absent.db"
    result = _invoke((db, valid_config_path), "list")
    assert result.exit_code == 1
    assert "read-only command" in result.output
    assert not db.exists()


@pytest.mark.parametrize("command", [("list",), ("show", "1")])
@pytest.mark.parametrize("interactive", [False, True])
def test_a_stale_schema_is_refused_not_migrated(
    tmp_path: Path, valid_config_path, monkeypatch, interactive, command
) -> None:
    """A v21 database has no `sell_proposals`; migrating it is an operator's `keel migrate`,
    never a side effect of asking to read the log -- at a terminal as much as off one."""
    monkeypatch.setattr(_common, "_is_interactive", lambda: interactive)
    db = tmp_path / "v21.db"
    conn = keel_db.connect(db)
    keel_db.migrate(conn)
    conn.execute("DROP TABLE sell_proposals")
    conn.execute("UPDATE schema_version SET version = 21")
    conn.commit()
    conn.close()

    result = _invoke((db, valid_config_path), *command)

    assert result.exit_code == 1
    assert "schema version 21" in result.output
    after = keel_db.connect(db)
    assert after.execute("SELECT version FROM schema_version").fetchone()["version"] == 21
    assert not keel_db.table_present(after, "sell_proposals")


# -- keel dca proposals review <id> (P12 Task 12.4, R23) ----------------------------------------
#
# A `[y/N]` at a terminal, NOT a capability row: marking a proposal reviewed releases no order and
# places nothing -- it is the paper -> live gate's input (spec §3.7), and in this build `live` is
# preview-only too. Off a terminal it prints and writes nothing, opening the database read-only.


def _review(deployment_pair, pid, input=None):
    db, config = deployment_pair
    return CliRunner().invoke(
        cli,
        ["--db", str(db), "--config", str(config), "dca", "proposals", "review", str(pid)],
        input=input,
    )


def _review_events(db: Path, pid: int) -> list[dict]:
    conn = keel_db.connect(str(db))
    try:
        from keel.data.audit import read_events

        return [
            e.payload
            for e in read_events(conn)
            if e.event_type == "sell_proposal_updated" and e.entity_id == str(pid)
        ]
    finally:
        conn.close()


def test_off_a_tty_review_writes_nothing(deployment, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setattr(_common, "_is_interactive", lambda: False)
    db, _config = deployment
    pid = _repo(db).insert_sell_proposal(_row())
    watcher = sqlite3.connect(str(db))
    before = watcher.execute("PRAGMA data_version").fetchone()[0]

    result = _review(deployment, pid)

    assert result.exit_code == 0, result.output
    assert "not a terminal: nothing written." in result.output.splitlines()
    assert _repo(db).get_sell_proposal(pid)["reviewed_ts"] is None
    assert watcher.execute("PRAGMA data_version").fetchone()[0] == before
    watcher.close()


@pytest.mark.parametrize(("answer", "reviewed"), [("y\n", True), ("n\n", False), ("\n", False)])
def test_at_a_tty_the_answer_decides(deployment, monkeypatch, answer, reviewed) -> None:  # noqa: F811
    monkeypatch.setattr(_common, "_is_interactive", lambda: True)
    db, _config = deployment
    pid = _repo(db).insert_sell_proposal(_row())

    result = _review(deployment, pid, input=answer)

    assert result.exit_code == 0, result.output
    row = _repo(db).get_sell_proposal(pid)
    assert (row["reviewed_ts"] is not None) is reviewed
    # The write is on the audit chain, or it did not happen: one event, carrying exactly it.
    events = _review_events(db, pid)
    assert len(events) == (1 if reviewed else 0)
    if reviewed:
        assert events[0] == {"reviewed_ts": row["reviewed_ts"]}


def test_the_prompt_shows_the_proposal_it_is_about(deployment, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setattr(_common, "_is_interactive", lambda: True)
    db, _config = deployment
    pid = _repo(db).insert_sell_proposal(_row(ts=_TS1))
    stored = _repo(db).get_sell_proposal(pid)

    result = _review(deployment, pid, input="n\n")

    lines = result.output.splitlines()
    rendered = sleeve_report.render_proposal(stored)
    start = lines.index(rendered[0])
    assert lines[start : start + len(rendered)] == rendered


def test_a_superseded_proposal_cannot_be_reviewed(deployment, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setattr(_common, "_is_interactive", lambda: True)
    db, _config = deployment
    pid = _repo(db).insert_sell_proposal(_row(decision="superseded"))

    result = _review(deployment, pid, input="y\n")

    assert result.exit_code == 1
    assert "superseded" in result.output
    assert _repo(db).get_sell_proposal(pid)["reviewed_ts"] is None
    assert _review_events(db, pid) == []


def test_an_already_reviewed_proposal_keeps_its_first_review(deployment, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setattr(_common, "_is_interactive", lambda: True)
    db, _config = deployment
    repo = _repo(db)
    pid = repo.insert_sell_proposal(_row())
    repo.update_sell_proposal(pid, reviewed_ts=_TS1)

    result = _review(deployment, pid, input="y\n")

    assert result.exit_code == 0, result.output
    assert _repo(db).get_sell_proposal(pid)["reviewed_ts"] == _TS1
    assert len(_review_events(db, pid)) == 1  # the seeding update only


@pytest.mark.parametrize("interactive", [False, True])
def test_review_of_a_missing_id_exits_1(deployment, monkeypatch, interactive) -> None:  # noqa: F811
    monkeypatch.setattr(_common, "_is_interactive", lambda: interactive)
    result = _review(deployment, 999, input="y\n")
    assert result.exit_code == 1
    assert "no sell proposal #999" in result.output


def test_off_a_tty_review_refuses_a_missing_database_rather_than_creating_it(
    tmp_path: Path, valid_config_path, monkeypatch
) -> None:
    """R20: off a terminal review is a read, so it opens read-only -- a missing `--db` path is
    refused, not created (a read-write opener would create and migrate it)."""
    monkeypatch.setattr(_common, "_is_interactive", lambda: False)
    db = tmp_path / "absent.db"
    result = _review((db, valid_config_path), 1)
    assert result.exit_code == 1
    assert "read-only command" in result.output
    assert not db.exists()
