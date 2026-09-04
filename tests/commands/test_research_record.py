"""The trials ledger, as a report -- issue #708, view 1.

keel's research record is already public, already append-only and already hash-chained. What
this adds is the reading of it: how many trials were run, how many produced a decision, whether
the chain still verifies, and -- the part no competitor publishes -- the rejected trials sitting
in the same table as the selected ones.

**The Strathern rail applies to this view.** Nothing here ranks by profit factor. A research
record that could be sorted best-first is a leaderboard, and a leaderboard is the thing that
turns a record of what was tried into an argument for what to trade.
"""

from __future__ import annotations

import ast
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from keel.commands.research_record import gather_trials

NOW_TS = 1_800_000_000


def _trial(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "trial_id": "t-1",
        "timestamp": NOW_TS - 3600,
        "session": "sweep-2026-09",
        "rule": "turtle_breakout",
        "params": {"entry_lookback": 20},
        "provenance": "a_priori",
        # One of the seven the ledger declares -- `kind` is a closed vocabulary and the writer
        # refuses anything else, which is why the fixture goes through the real `append_trial`.
        "kind": "sweep_node",
        "decision": "rejected",
        "per_trade_pnl": ["1.5", "-2.0"],
        "per_bar_pnl": [],
        "series_missing": False,
        "summary": {"profit_factor": "0.31", "n_trades": "58"},
        "prev_hash": "0" * 64,
        "row_hash": "",
    }
    row.update(overrides)
    return row


def _ledger(tmp_path: Path, *rows: dict[str, Any]) -> Path:
    """A ledger file with a VALID chain, built through the real writer so the hashes are real."""
    from keel.research.ledger import append_trial

    path = tmp_path / "trials-ledger.jsonl"
    for row in rows:
        append_trial(
            path,
            trial_id=row["trial_id"],
            session=row["session"],
            rule=row["rule"],
            params=row["params"],
            provenance=row["provenance"],
            kind=row["kind"],
            decision=row["decision"],
            summary=row["summary"],
            timestamp=row["timestamp"],
            # The ledger refuses a trial with no series unless it says `series_missing` --
            # so the CSCV matrix can refuse it later rather than silently scoring nothing.
            per_trade_pnl=[Decimal(v) for v in row["per_trade_pnl"]],
            series_missing=row["series_missing"],
        )
    return path


# -- the record, read -----------------------------------------------------------------------------


def test_every_trial_is_reported_including_the_rejected_ones(tmp_path: Path) -> None:
    """THE point of the view. A research record that showed only the selected trials would be a
    highlight reel, and the rejected rows are the evidence that the selected one was not
    cherry-picked out of a hundred attempts."""
    path = _ledger(
        tmp_path,
        _trial(trial_id="t-1", decision="rejected"),
        _trial(trial_id="t-2", decision="selected"),
        _trial(trial_id="t-3", decision="diagnostic_only"),
    )

    report = gather_trials(path, now_ts=NOW_TS)

    assert [row.decision for row in report.rows] == ["rejected", "selected", "diagnostic_only"]


def test_the_counts_are_trials_and_decisions_not_one_number(tmp_path: Path) -> None:
    """`trial_counts` returns (M, N): trials run, and trials that produced a DECISION. They are
    different numbers and the difference is the point -- M/N is the multiple-comparisons
    denominator that DSR corrects for."""
    path = _ledger(
        tmp_path,
        _trial(trial_id="t-1", decision="rejected"),
        _trial(trial_id="t-2", decision="diagnostic_only"),
    )

    report = gather_trials(path, now_ts=NOW_TS)

    assert report.trials_run == 2
    assert report.decisions == 1, "a diagnostic-only trial is not a decision"


def test_an_intact_chain_verifies(tmp_path: Path) -> None:
    path = _ledger(tmp_path, _trial(trial_id="t-1"), _trial(trial_id="t-2"))

    report = gather_trials(path, now_ts=NOW_TS)

    assert report.chain_intact is True
    assert report.chain_errors == ()


def test_a_tampered_row_breaks_the_chain_and_the_break_is_reported(tmp_path: Path) -> None:
    """The acceptance criterion. Editing any row must flip the badge -- that is what makes the
    ledger evidence rather than a claim, and a badge that could not go red would be decoration."""
    path = _ledger(tmp_path, _trial(trial_id="t-1"), _trial(trial_id="t-2"))

    lines = path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["summary"]["profit_factor"] = "9.99"  # the edit an author would want to make
    lines[0] = json.dumps(first)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = gather_trials(path, now_ts=NOW_TS)

    assert report.chain_intact is False
    assert report.chain_errors, "a broken chain must say WHICH rows broke"


def test_a_missing_ledger_is_an_honest_state_not_an_error(tmp_path: Path) -> None:
    """`DEFAULT_LEDGER_PATH` is repo-relative, and `keel serve` runs from a deployment directory
    where `docs/experiments/` does not exist. That is not a broken deployment -- it is a
    deployment without the research repo beside it -- and the page says so rather than failing."""
    report = gather_trials(tmp_path / "absent.jsonl", now_ts=NOW_TS)

    assert report.rows == ()
    assert report.ledger_present is False
    assert report.chain_intact is False, "nothing was verified, so nothing is verified"


def test_an_empty_ledger_is_distinct_from_a_missing_one(tmp_path: Path) -> None:
    """A file that exists and holds nothing is a different fact from no file at all: the first
    says this deployment has run no trials, the second says the record is not here to read."""
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")

    report = gather_trials(path, now_ts=NOW_TS)

    assert report.ledger_present is True
    assert report.rows == ()
    assert report.trials_run == 0


# -- the Strathern rail ---------------------------------------------------------------------------


def test_the_report_never_orders_trials_by_performance(tmp_path: Path) -> None:
    """The constitutional guardrail, in the service rather than only the view. Rows come back in
    LEDGER ORDER -- the order they were run -- because any performance ordering turns a record of
    what was tried into an argument for what to trade, which is the reversal the trials ledger
    exists to prevent."""
    path = _ledger(
        tmp_path,
        _trial(trial_id="t-worst", summary={"profit_factor": "0.10"}),
        _trial(trial_id="t-best", summary={"profit_factor": "3.00"}),
        _trial(trial_id="t-mid", summary={"profit_factor": "1.00"}),
    )

    report = gather_trials(path, now_ts=NOW_TS)

    assert [row.trial_id for row in report.rows] == ["t-worst", "t-best", "t-mid"]


def test_trials_are_grouped_by_rule_family_for_the_view(tmp_path: Path) -> None:
    """What the view groups on instead of ranking. First-seen order, so the page does not
    reorder itself as trials arrive."""
    path = _ledger(
        tmp_path,
        _trial(trial_id="t-1", rule="turtle_breakout"),
        _trial(trial_id="t-2", rule="pullback_continuation"),
        _trial(trial_id="t-3", rule="turtle_breakout"),
    )

    assert gather_trials(path, now_ts=NOW_TS).rules == ("turtle_breakout", "pullback_continuation")


def test_explored_against_declared_is_reported_without_the_refusing_helper(
    tmp_path: Path,
) -> None:
    """`research.tuning.explored_vs_declared` RAISES when a swept range exceeds its declaration --
    it is a driver's refusal gate, not a reporting helper, and calling it here would 500 the page
    on exactly the drift it exists to flag.

    So the view reports the two numbers it can state without judging them: how many trials this
    ledger holds for a rule, and how many cells that rule DECLARES. Pricing the swept box against
    the declaration needs the box in the declaration's own dimension names -- which for
    `pullback_continuation` are fan slots rather than the packed `ema_periods` its params carry --
    and that assembly is its own piece of work, not a line in a read path.
    """
    path = _ledger(
        tmp_path,
        _trial(trial_id="t-1", rule="turtle_breakout"),
        _trial(trial_id="t-2", rule="turtle_breakout"),
    )

    exploration = gather_trials(path, now_ts=NOW_TS).exploration

    assert exploration[0].rule == "turtle_breakout"
    assert exploration[0].trials == 2
    assert exploration[0].declared_cells > 0, "the rule's own declaration, from `declared_cells`"


# -- the rail, scanned mechanically ---------------------------------------------------------------


RECORD_MODULE_PATH = Path("keel/commands/research_record.py")


def test_the_record_module_never_sorts_ranks_or_maxes() -> None:
    """The same AST scan `test_research_front_door.py` runs over the CLI group, over this module.

    Its own docstring records what a shape check like this does and does not buy, and both halves
    apply here. It sees only ranking written in THIS file, and it matches on syntax, so a ranking
    spelled without `key=` (`sorted([(pf, row) for row in rows])[0]`) slips through. What it does
    is make the cheap, idiomatic way to introduce one fail loudly, and force anything else to be
    written conspicuously enough that a reader notices.

    It earns its place here specifically: this module is the newest surface over the ledger, it is
    read by a web page rather than a terminal, and "let the operator sort the table" is the single
    most natural feature request a research view will ever attract.
    """
    source = RECORD_MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(RECORD_MODULE_PATH))

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            callee = node.func
            if isinstance(callee, ast.Name) and callee.id in {"sorted", "max", "min"}:
                assert not any(kw.arg == "key" for kw in node.keywords), (
                    f"{callee.id}() called with key= at {RECORD_MODULE_PATH}:{node.lineno} -- "
                    "a keyed sort/max/min IS a ranking; the Strathern rail forbids it here "
                    "unconditionally"
                )
            if isinstance(callee, ast.Attribute) and callee.attr == "sort":
                assert not any(kw.arg == "key" for kw in node.keywords), (
                    f".sort(key=...) at {RECORD_MODULE_PATH}:{node.lineno} -- an in-place keyed "
                    "sort IS a ranking; forbidden here unconditionally"
                )
            if isinstance(callee, ast.Attribute) and callee.attr in {"itemgetter", "attrgetter"}:
                pytest.fail(
                    f"operator.{callee.attr} used at {RECORD_MODULE_PATH}:{node.lineno} -- "
                    "ranking-by-field machinery is forbidden here unconditionally"
                )
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "heapq", (
                    f"import heapq at {RECORD_MODULE_PATH}:{node.lineno} -- a priority queue IS "
                    "ranking machinery; forbidden here unconditionally"
                )
        if isinstance(node, ast.ImportFrom) and node.module == "operator":
            banned = {alias.name for alias in node.names} & {"itemgetter", "attrgetter"}
            assert not banned, (
                f"from operator import {sorted(banned)} at "
                f"{RECORD_MODULE_PATH}:{node.lineno} -- forbidden here unconditionally"
            )
