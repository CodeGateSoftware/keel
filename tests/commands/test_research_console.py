"""Tests for `keel.commands.research_console` -- the Research menu and the O5 evidence
readers (issue #390 C4; PRD O5).

Pinned here:

* **The sub-menu** -- PRD §3's Research branch: experiments, research docs, promotion
  reports, the trials ledger.
* **The corpus readers** -- a directory listing newest-first (mtime, then name), a BOUNDED
  read of the chosen document (a runaway file can never cost the console an unbounded read),
  and the compliance console's mtime-cache lesson: an unchanged file is not re-read on
  repaint.
* **The trials reader** -- `trials list`'s own rendering over `read_trials`/`trial_counts`
  plus `verify_chain`'s verdict, both read-only service calls.
* **The simulate-report reachability** -- the promotion-reports corpus is the directory
  `run_simulation` writes into (`default_report_path`), so a just-run simulation's report is
  in the list, newest-first.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from keel.commands import research_console as rc
from keel.commands.simulate import default_report_path
from keel.research import ledger as trials_ledger

NOW_TS = 1_800_000_000


# -- the sub-menu (PRD §3's Research branch) -------------------------------------------------------


def test_the_research_menu_is_the_prd_research_branch() -> None:
    assert [entry.label for entry in rc.RESEARCH_MENU] == [
        "experiments",
        "research docs",
        "promotion reports",
        "trials ledger",
    ]
    trials = rc.research_entry(4)
    assert trials is not None
    assert trials.kind == "trials"


def test_the_corpus_directories_are_the_engines_own_paths() -> None:
    """Single-sourced, never a TUI-side path table: the experiments dir is the trials
    ledger's own parent (resolved at CALL time, so the test-isolation patch and any
    relocated ledger relocate the reader too), and the reports dir is where
    `run_simulation` writes."""
    assert rc.corpus_path("experiments") == trials_ledger.DEFAULT_LEDGER_PATH.parent
    assert rc.corpus_path("reports") == default_report_path(NOW_TS).parent
    assert rc.corpus_path("research") == Path("docs/research")


# -- the corpus readers ----------------------------------------------------------------------------


def _write(path: Path, text: str, *, mtime: float | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def test_list_documents_is_newest_first_and_calm_on_an_absent_directory(tmp_path: Path) -> None:
    older = tmp_path / "2026-01-01-old.md"
    newer = tmp_path / "2026-02-01-new.md"
    _write(older, "old", mtime=1_000_000.0)
    _write(newer, "new", mtime=2_000_000.0)
    files = rc.list_documents(tmp_path)
    assert [f.path.name for f in files] == ["2026-02-01-new.md", "2026-01-01-old.md"]
    # An absent directory is an empty list, never a traceback (the scout browser's contract).
    assert rc.list_documents(tmp_path / "nope") == ()


def test_list_documents_filters_by_suffix(tmp_path: Path) -> None:
    _write(tmp_path / "a.md", "x", mtime=1.0)
    _write(tmp_path / "b.py", "x", mtime=2.0)
    _write(tmp_path / "c.jsonl", "x", mtime=3.0)
    names = {f.path.name for f in rc.list_documents(tmp_path)}
    assert names == {"a.md", "b.py", "c.jsonl"}
    only_md = {f.path.name for f in rc.list_documents(tmp_path, ".md")}
    assert only_md == {"a.md"}


def test_read_document_lines_is_bounded(tmp_path: Path) -> None:
    """A file past the byte bound is read only up to the bound, with a loud truncation note
    -- never an unbounded read of whatever a runaway writer produced."""
    big = tmp_path / "big.md"
    big.write_text("x" * (rc.MAX_DOC_BYTES + 10))
    lines = rc.read_document_lines(big)
    joined = "\n".join(lines)
    assert "truncated" in joined
    assert len(joined) < rc.MAX_DOC_BYTES + 10_000


def test_read_document_lines_renders_text_verbatim(tmp_path: Path) -> None:
    doc = tmp_path / "doc.md"
    doc.write_text("# Title\n\nbody line\n")
    lines = rc.read_document_lines(doc)
    assert "# Title" in lines
    assert "body line" in lines


def test_cached_document_lines_skips_the_read_for_an_unchanged_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mtime-cache lesson: the reader repaints per poll; an UNCHANGED document is
    served from the cache, and a changed mtime re-reads."""
    doc = tmp_path / "doc.md"
    doc.write_text("v1")
    cache: dict[tuple[str, int], list[str]] = {}
    reads: list[str] = []

    real_read = rc.read_document_lines

    def counting_read(path: Path) -> list[str]:
        reads.append(str(path))
        return real_read(path)

    monkeypatch.setattr(rc, "read_document_lines", counting_read)
    first = rc.cached_document_lines(doc, cache)
    second = rc.cached_document_lines(doc, cache)
    assert first == second == ["v1"]
    assert reads == [str(doc)]
    # A changed mtime refreshes.
    doc.write_text("v2")
    third = rc.cached_document_lines(doc, cache)
    assert third == ["v2"]
    assert reads == [str(doc), str(doc)]


def test_the_doc_list_renders_files_and_an_honest_empty_state(tmp_path: Path) -> None:
    _write(tmp_path / "only.md", "x", mtime=1.0)
    files = rc.list_documents(tmp_path)
    lines = rc.build_doc_list_lines("experiments", files, tmp_path)
    texts = [line.text for line in lines]
    joined = "\n".join(texts)
    assert any("only.md" in t for t in texts)
    # The directory is named, wrapped to the budget (a long tmp path breaks across lines,
    # so the assertion reads the JOINED body, not one line).
    assert tmp_path.name in joined or str(tmp_path) in joined
    assert "newest first" in joined

    empty = rc.build_doc_list_lines("experiments", (), tmp_path)
    joined = "\n".join(line.text for line in empty)
    assert "no documents" in joined


def test_the_doc_screen_renders_the_documents_own_lines(tmp_path: Path) -> None:
    doc = tmp_path / "experiment.md"
    doc.write_text("# What we tried\n\nresult: net negative\n")
    lines = rc.build_doc_lines("experiments", doc, ["# What we tried", "", "result: net negative"])
    texts = [line.text for line in lines]
    assert any("# What we tried" in t for t in texts)
    assert any("result: net negative" in t for t in texts)
    assert any("experiment.md" in t for t in texts)


def test_the_promotion_reports_corpus_holds_a_just_run_simulation_report(tmp_path: Path) -> None:
    """`run_simulation` writes into the reports corpus (`default_report_path`), so the
    report a console simulate run just wrote is immediately listable there, newest-first."""
    report = tmp_path / default_report_path(NOW_TS).name
    _write(report, "verdict: TRAIN-MORE", mtime=float(NOW_TS))
    _write(tmp_path / "2020-01-01-older.md", "old", mtime=1.0)
    files = rc.list_documents(tmp_path)
    assert files[0].path.name == report.name


# -- the trials reader (list + verify, read-only) ---------------------------------------------


def _ledger_with_two_trials(tmp_path: Path) -> Path:
    path = tmp_path / "trials-ledger.jsonl"
    trials_ledger.append_trial(
        path,
        trial_id="t1",
        session="sweep-a",
        rule="turtle_breakout",
        params={"entry_lookback": 40},
        provenance="a_priori",
        kind="sweep_node",
        decision="rejected",
        series_missing=True,
    )
    trials_ledger.append_trial(
        path,
        trial_id="t2",
        session="sweep-a",
        rule="turtle_breakout",
        params={"entry_lookback": 55},
        provenance="fitted",
        kind="sweep_node",
        decision="selected",
        series_missing=True,
    )
    return path


def test_the_trials_view_lists_the_ledger_and_renders_the_chain_verdict(tmp_path: Path) -> None:
    path = _ledger_with_two_trials(tmp_path)
    lines = rc.build_trials_lines(path)
    texts = [line.text for line in lines]
    joined = "\n".join(texts)
    assert any("t1" in t for t in texts)
    assert any("t2" in t for t in texts)
    assert "M=" in joined
    assert "chain intact" in joined


def test_the_trials_view_renders_a_broken_chain_fail_loud(tmp_path: Path) -> None:
    path = _ledger_with_two_trials(tmp_path)
    # Tamper with the first row: the chain must NOT verify.
    rows = path.read_text().splitlines()
    import json as _json

    first = _json.loads(rows[0])
    first["rule"] = "tampered"
    rows[0] = _json.dumps(first)
    path.write_text("\n".join(rows) + "\n")
    lines = rc.build_trials_lines(path)
    joined = "\n".join(line.text for line in lines)
    assert "chain intact" not in joined
    assert "CHAIN BROKEN" in joined or "error" in joined.lower()


def test_the_trials_view_is_calm_about_an_absent_ledger(tmp_path: Path) -> None:
    lines = rc.build_trials_lines(tmp_path / "absent.jsonl")
    joined = "\n".join(line.text for line in lines)
    assert "no trials" in joined


# -- the menu screen ----------------------------------------------------------------------------


def test_the_menu_screen_renders_every_entry() -> None:
    lines = rc.build_research_menu_lines(cursor=0)
    texts = [line.text for line in lines]
    for entry in rc.RESEARCH_MENU:
        assert any(entry.label in t for t in texts), entry.label


# -- the loop wiring (fake curses): the readers are reachable and read-only ----------------------


def test_run_live_research_readers_are_reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Loop-level: Research -> trials renders the ledger view with its chain verdict, and
    Research -> experiments lists the (test-isolated) corpus with its calm empty state --
    both read-only."""
    import sys
    from decimal import Decimal

    import click

    from keel.commands import tui as tui_mod
    from keel.commands.console import ConsoleBinding
    from keel.config import (
        AutoTradeConfig,
        Caps,
        Config,
        DcaConfig,
        MarketDataConfig,
        MoneyMgmtConfig,
    )
    from keel.data.db import connect, migrate
    from keel.data.repository import Repository
    from tests.commands.test_tui import _fake_curses, _KeySequenceStdscr

    conn = connect(":memory:")
    migrate(conn)
    repo = Repository(conn)
    config = Config(
        allowlist=["BTC"],
        target_weights={},
        risk_pct=Decimal("0.01"),
        caps=Caps(
            max_per_order_usd=Decimal("100000"),
            max_per_day_usd=Decimal("300000"),
            max_exposure_usd=Decimal("1000000"),
            max_per_asset_pct=Decimal("1"),
        ),
        market_data=MarketDataConfig(granularities=[], history_days=365),
        auto_trade=AutoTradeConfig(mode="paper", interval_sec=900),
        money_mgmt=MoneyMgmtConfig(
            max_total_dd_pct=Decimal("0.20"), max_weekly_dd_pct=Decimal("0.08")
        ),
        dca=DcaConfig(budget_usd=Decimal("50"), cadence_days=7),
    )

    # m -> menu; 7 -> Research; 4 -> trials ledger; poll; Esc back to Research; 1 ->
    # experiments (empty -- the corpus directory follows the patched ledger's parent,
    # so test isolation empties it); poll; Esc; q quits.
    keys = [ord("m"), ord("7"), ord("4"), -1, 27, ord("1"), -1, 27]
    stdscr = _KeySequenceStdscr(height=24, width=80, keys=keys)
    fake = _fake_curses()
    fake.wrapper = lambda fn: fn(stdscr)
    monkeypatch.setitem(sys.modules, "curses", fake)

    ctx = click.Context(
        click.Command("tui"), obj={"config_path": "config.yaml", "db_path": "keel.db"}
    )
    binding = ConsoleBinding(ctx, config_path="config.yaml", db_path="keel.db")
    binding.open_state = lambda: (repo, config)  # type: ignore[method-assign]
    tui_mod.run_live(
        binding.open_state, lambda: NOW_TS, interval=0.01, console_binding=binding
    )

    painted = "\n".join(call[2] for call in stdscr.calls)
    assert "trials ledger" in painted
    assert "chain intact" in painted or "no trials" in painted
    assert "no documents" in painted  # the isolated experiments corpus, calmly empty
