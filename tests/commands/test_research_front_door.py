"""Tests for `keel research` -- the front door over `keel/research/*` (issue #601).

Two ARCHITECTURAL pins, plus surface tests over the group itself:

* **The completeness pin** (`test_every_research_module_is_indexed`,
  `test_every_runs_as_resolves`). `RESEARCH_INDEX` is a module-level literal, so nothing
  stops it from silently falling behind `keel/research/` as that package grows -- these two
  tests are what turns "somebody forgot to add a row" into a failing build instead of a
  documentation gap nobody notices. The first globs the actual package directory (never a
  hardcoded count) and asserts every file it finds has an entry; the second walks the real
  click command tree off `keel.cli.cli` (or checks the filesystem, for a module still
  waiting on its own subcommand) so a `runs_as` string that used to work but silently broke
  -- a renamed command, a typo -- fails here too.

* **The Strathern rail pin** (`test_research_module_never_sorts_ranks_or_maxes`). `cscv.py`,
  `deflate.py` and `walkforward.py` each carry a ⛔ comment: a score may report, and may
  gate, but may NEVER be a sweep's ranking key. `keel/commands/research.py` is the newest
  surface built over those three, which makes it the newest place that guarantee could leak
  -- so this test is an AST scan that fails if the module contains ANY ranking shape at all:
  `sorted(...)`/`max(...)`/`min(...)` called with a `key=` argument, `.sort(key=...)`, or an
  import of `heapq`/`operator.itemgetter`/`operator.attrgetter`.

  The blanket ban (rather than a narrower rule that only fires when the sorted/ranked values
  look rail-bearing) is deliberate. A scanner that tries to decide which fields are
  "rail-bearing" before objecting to a sort is a scanner a rename can fool -- rename `pbo` to
  `score` and a field-aware check no longer recognises it. The front door's job is to place
  values it was given, in an order IT chooses, never in an order a value chooses for it; the
  moment this module orders configurations by a score, that score has become a ranking key,
  full stop, regardless of the field's name. Where the module legitimately needs a stable
  display order (the table of contents itself), the fix is an explicitly declared literal
  order -- `RESEARCH_INDEX`'s own comment says so -- never a computed one, so this ban costs
  nothing real and closes the door completely.

* **The refusal pin** (`test_every_evidence_subcommand_can_refuse_on_stdout_and_exit_zero`,
  Wave B). Issue #601's second acceptance criterion, in test form: every evidence
  subcommand under `keel research` must be ABLE to answer "there is not enough evidence"
  on stdout at exit 0, never a `ClickException`. The enumeration walks
  `research_group.commands` itself -- never a hardcoded list -- so a future eighth
  subcommand with no declared refusal fixture fails this test by construction rather than
  silently going unchecked; see the docstring on the test itself for the two named
  exclusions (`index`, `lookahead`) and why each is not an "insufficient evidence" case.

Every pin here was written, then deliberately broken, then restored -- see the commit
message for the exact mutation and the exact failure text each produced.
"""

from __future__ import annotations

import ast
import json
from decimal import Decimal
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from keel.cli import cli
from keel.commands.research import (
    RAIL_SENTENCE,
    RESEARCH_INDEX,
    ResearchModuleEntry,
    render_index,
    research_group,
)
from keel.commands.rules import rules_group
from keel.commands.trials import trials_group
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.types import Candle, Granularity

REPO_ROOT = Path(__file__).resolve().parents[2]
RESEARCH_PKG = REPO_ROOT / "keel" / "research"
RESEARCH_MODULE_PATH = REPO_ROOT / "keel" / "commands" / "research.py"


def _indexed_names() -> set[str]:
    return {entry.module for entry in RESEARCH_INDEX}


def _actual_module_files() -> set[str]:
    return {
        path.name
        for path in RESEARCH_PKG.glob("*.py")
        if path.name not in {"__init__.py"} and "__pycache__" not in path.parts
    }


# -- pin (a): completeness -----------------------------------------------------------------------


def test_every_research_module_is_indexed():
    """Every `.py` file actually sitting in `keel/research/` (glob, never a hardcoded
    count) must have a row in `RESEARCH_INDEX`. A fourteenth module with no row fails here.

    Mutation-verified: adding a throwaway `keel/research/zzz_probe.py` made this fail with
    `AssertionError: {'zzz_probe.py'}` (the file existed, then the test removed it) -- see
    the commit message for the exact command and output.
    """
    on_disk = _actual_module_files()
    indexed = _indexed_names()
    missing = on_disk - indexed
    assert not missing, f"{missing} exist in keel/research/ but have no RESEARCH_INDEX row"
    # And the reverse should never happen either: an index row for a module that was deleted
    # is a stale row nobody will notice.
    stale = indexed - on_disk
    assert not stale, f"{stale} are indexed but no longer exist in keel/research/"


def _resolve_cli_command(command_line: str) -> click.Command | None:
    """Walk the real command tree off `keel.cli.cli` for a `"keel a b c"` string. Returns
    None if any token along the way is not a registered subcommand."""
    tokens = command_line.split()
    assert tokens and tokens[0] == "keel", command_line
    node: click.Command = cli
    for token in tokens[1:]:
        if not isinstance(node, click.Group) or token not in node.commands:
            return None
        node = node.commands[token]
    return node


def _driver_imports_module(driver_path: Path, module_stem: str) -> bool:
    """True when `driver_path` imports `keel.research.<module_stem>` -- by name, at module
    scope (`import keel.research.X` or `from keel.research.X import ...`). AST-based rather
    than a text search so a docstring that merely MENTIONS the module name (every driver's
    module docstring names half the toolkit in prose) can never satisfy this check."""
    tree = ast.parse(driver_path.read_text(encoding="utf-8"), filename=str(driver_path))
    target = f"keel.research.{module_stem}"
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == target:
            return True
        if isinstance(node, ast.Import) and any(alias.name == target for alias in node.names):
            return True
    return False


def test_every_runs_as_resolves():
    """Every `runs_as` either names a real command reachable from `keel.cli.cli` (walked,
    not guessed) or a `docs/experiments/*.py` driver that exists on disk AND actually
    imports the module it is named as the `runs_as` for -- never a string that used to be
    true, and never a driver that happens to exist but drives a DIFFERENT module.

    The second half is Wave B's tightening (#601): before it, this test only checked that
    the named driver FILE existed, which is exactly why `throughput.py`'s `runs_as` was able
    to silently point at `docs/experiments/2026-09-30-pooled-review.py` -- the pooled-review
    driver, not a throughput one -- and pass. `keel research throughput` closed that slip by
    giving `throughput.py` a real command to name instead; this tightening is what makes
    sure the NEXT such slip cannot pass silently again.

    Mutation-verified twice: (1) pointing one entry's `runs_as` at
    `"keel research not-a-real-command"` made this fail with `AssertionError: keel research
    not-a-real-command does not resolve to a registered CLI command`; (2) pointing
    `independence.py`'s `runs_as` at `docs/experiments/2026-08-09-cts-factor-collinearity.py`
    (a real, existing driver -- just the WRONG one, exactly `throughput.py`'s original bug)
    made this fail with `AssertionError: docs/experiments/2026-08-09-cts-factor-
    collinearity.py exists but does not import keel.research.independence -- it is not
    actually the driver for independence.py`. See the commit message for both diffs and both
    exact failures.
    """
    for entry in RESEARCH_INDEX:
        if entry.runs_as.startswith("keel "):
            resolved = _resolve_cli_command(entry.runs_as)
            assert resolved is not None, (
                f"{entry.runs_as} does not resolve to a registered CLI command "
                f"(module={entry.module})"
            )
        else:
            driver = REPO_ROOT / entry.runs_as
            assert driver.is_file(), (
                f"{entry.runs_as} names a docs/experiments driver that does not exist on "
                f"disk (module={entry.module})"
            )
            module_stem = entry.module.removesuffix(".py")
            assert _driver_imports_module(driver, module_stem), (
                f"{entry.runs_as} exists but does not import keel.research.{module_stem} "
                f"-- it is not actually the driver for {entry.module}"
            )


# -- pin (b): the Strathern rail survives the front door -----------------------------------------


def test_research_module_never_sorts_ranks_or_maxes():
    """AST scan over `keel/commands/research.py` itself (see the module docstring for why
    the ban is blanket, not field-aware).

    Mutation-verified: inserting `sorted(RESEARCH_INDEX, key=lambda r: r.module)` into the
    module made this fail with `AssertionError: sorted()/max()/min() called with key= at
    keel/commands/research.py:<line>` before being removed again; see the commit message
    for the exact snippet and failure line.
    """
    source = RESEARCH_MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(RESEARCH_MODULE_PATH))

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            callee = node.func
            # sorted(...)/max(...)/min(...) with a key= keyword.
            if isinstance(callee, ast.Name) and callee.id in {"sorted", "max", "min"}:
                has_key = any(kw.arg == "key" for kw in node.keywords)
                assert not has_key, (
                    f"{callee.id}() called with key= at "
                    f"{RESEARCH_MODULE_PATH}:{node.lineno} -- a keyed sort/max/min IS a "
                    "ranking; the Strathern rail forbids it here unconditionally"
                )
            # anything.sort(key=...)
            if isinstance(callee, ast.Attribute) and callee.attr == "sort":
                has_key = any(kw.arg == "key" for kw in node.keywords)
                assert not has_key, (
                    f".sort(key=...) at {RESEARCH_MODULE_PATH}:{node.lineno} -- an in-place "
                    "keyed sort IS a ranking; forbidden here unconditionally"
                )
            # operator.itemgetter(...) / operator.attrgetter(...), however `operator` got
            # into scope (bare `import operator`, an alias, etc).
            if isinstance(callee, ast.Attribute) and callee.attr in {
                "itemgetter",
                "attrgetter",
            }:
                pytest.fail(
                    f"operator.{callee.attr} used at {RESEARCH_MODULE_PATH}:{node.lineno} "
                    "-- ranking-by-field machinery is forbidden here unconditionally"
                )
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "heapq", (
                    f"import heapq at {RESEARCH_MODULE_PATH}:{node.lineno} -- a priority "
                    "queue IS ranking machinery; forbidden here unconditionally"
                )
        if isinstance(node, ast.ImportFrom):
            if node.module == "operator":
                imported = {alias.name for alias in node.names}
                banned = imported & {"itemgetter", "attrgetter"}
                assert not banned, (
                    f"from operator import {sorted(banned)} at "
                    f"{RESEARCH_MODULE_PATH}:{node.lineno} -- forbidden here unconditionally"
                )


def test_rail_marked_on_exactly_the_three_strathern_modules():
    railed = {entry.module for entry in RESEARCH_INDEX if entry.rail}
    assert railed == {"cscv.py", "deflate.py", "walkforward.py"}


def test_index_output_states_the_rail_sentence_where_a_reader_meets_it():
    """Not just that the rail is MENTIONED somewhere -- the exact sentence
    (`RAIL_SENTENCE`) must appear, and it must appear beside a rail-bearing module's own
    block, not floating disconnected in a preamble."""
    lines = render_index(RESEARCH_INDEX)
    rendered = "\n".join(lines)
    assert RAIL_SENTENCE in rendered

    # "Where a reader meets it": the sentence sits in the block for cscv.py, immediately
    # after that module's "runs as" line, not merely somewhere in the whole document.
    cscv_start = rendered.index("cscv.py")
    next_module_start = rendered.index("deflate.py")
    assert RAIL_SENTENCE in rendered[cscv_start:next_module_start]


# -- surface: the group itself --------------------------------------------------------------------


def test_index_exits_zero_and_names_all_thirteen():
    result = CliRunner().invoke(cli, ["research", "index"])
    assert result.exit_code == 0, result.output
    for entry in RESEARCH_INDEX:
        assert entry.module in result.output


def test_index_json_round_trips_and_carries_every_module():
    result = CliRunner().invoke(cli, ["research", "index", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert {row["module"] for row in payload} == _indexed_names()
    for row in payload:
        assert set(row) == {"module", "question", "cannot_answer", "runs_as", "rail"}


def test_index_module_filter_prints_exactly_one_entry():
    result = CliRunner().invoke(cli, ["research", "index", "--module", "significance"])
    assert result.exit_code == 0, result.output
    assert "significance.py" in result.output
    for entry in RESEARCH_INDEX:
        if entry.module != "significance.py":
            assert entry.module not in result.output


def test_index_unknown_module_refuses_but_exits_zero():
    """Per #601's second bullet, applied to the index's own lookup: an unknown `--module`
    name is a well-formed question ("tell me about X") the index cannot answer, not an
    operator error -- it refuses on stdout and exits 0, listing the names that DO exist."""
    result = CliRunner().invoke(cli, ["research", "index", "--module", "not-a-real-module"])
    assert result.exit_code == 0, result.output
    assert "refused" in result.output
    for entry in RESEARCH_INDEX:
        assert entry.module.removesuffix(".py") in result.output


@pytest.mark.parametrize(
    ("research_name", "source_group", "source_name"),
    [
        ("pbo", trials_group, "pbo"),
        ("deflate", trials_group, "deflate"),
        ("monte-carlo", trials_group, "monte-carlo"),
        ("walk-forward", trials_group, "walk-forward"),
        ("lookahead", rules_group, "lookahead"),
    ],
)
def test_alias_is_the_same_object_reachable_under_two_names(
    research_name, source_group, source_name
):
    """Not a reimplementation: `keel research X` and its source command must be the exact
    same click `Command` object, so there is one implementation running under two names."""
    aliased = research_group.commands[research_name]
    original = source_group.commands[source_name]
    assert aliased is original


def test_each_alias_is_reachable_via_cli_invoke_under_both_names():
    for research_name, group_name, source_name in (
        ("pbo", "trials", "pbo"),
        ("deflate", "trials", "deflate"),
        ("monte-carlo", "trials", "monte-carlo"),
        ("walk-forward", "trials", "walk-forward"),
        ("lookahead", "rules", "lookahead"),
    ):
        via_research = CliRunner().invoke(cli, ["research", research_name, "--help"])
        via_source = CliRunner().invoke(cli, [group_name, source_name, "--help"])
        assert via_research.exit_code == 0
        assert via_source.exit_code == 0
        # Same object -> click renders identical help text either way, except the "Usage:"
        # line, which necessarily names the path it was invoked through.
        research_body = via_research.output.split("\n", 1)[1]
        source_body = via_source.output.split("\n", 1)[1]
        assert research_body == source_body


def test_research_index_entry_is_a_frozen_dataclass_tuple():
    assert isinstance(RESEARCH_INDEX, tuple)
    for entry in RESEARCH_INDEX:
        assert isinstance(entry, ResearchModuleEntry)
        with pytest.raises(Exception):
            entry.module = "mutated.py"  # type: ignore[misc]


# -- pin (c): `keel research tuning` never imports optuna at module scope ------------------------


def test_research_module_never_imports_optuna():
    """`keel/research/tuning.py` imports optuna lazily, inside `run_study` only, so `import
    keel.research.tuning` stays clean without it; `keel/commands/research.py` must hold to
    the same discipline (#601's fourth bullet) -- optuna is a dev-only dependency
    (`pyproject.toml`'s dev group) and the shipped CLI must import cleanly without it.

    AST-based (not a text `"optuna" in source` grep) so the word can still appear in prose --
    `research_tuning`'s own refusal message and docstring both name
    `docs/experiments/2026-08-22-optuna-parameter-study.py` and the word "optuna" -- without
    tripping this pin; only an actual `import`/`from ... import` statement does.

    Mutation-verified: adding `import optuna  # noqa: F401` at the top of
    `keel/commands/research.py` (line 51, right after `import json`) made this fail with
    `AssertionError: import optuna at keel/commands/research.py:51 -- optuna is a dev-only
    dependency and this module must import cleanly without it` before being removed again;
    see the commit message for the exact diff.
    """
    source = RESEARCH_MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(RESEARCH_MODULE_PATH))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] != "optuna", (
                    f"import optuna at {RESEARCH_MODULE_PATH}:{node.lineno} -- optuna is a "
                    "dev-only dependency and this module must import cleanly without it"
                )
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert module.split(".")[0] != "optuna", (
                f"from {module} import ... at {RESEARCH_MODULE_PATH}:{node.lineno} -- optuna "
                "is a dev-only dependency and this module must import cleanly without it"
            )


# -- pin (d): the refusal pin ----------------------------------------------------------------------
#
# Issue #601's second acceptance criterion. Every EVIDENCE subcommand under `keel research`
# must be able to answer "there is not enough evidence to answer that" on stdout, exit 0 --
# never a `ClickException`. This test enumerates `research_group.commands` itself (never a
# hardcoded list of the six Wave B names) and actually invokes every one of them against a
# deliberately empty/underpowered fixture db -- it does not grep source for the word
# "refused"; it runs the command and reads what it printed.


def _refusal_fixture_db(tmp_path: Path) -> Path:
    """One tiny db built to make EVERY evidence subcommand refuse: two stored `turtle_breakout`
    rules with no cached candles (rules 1/2 -- `significance --from rule`, `monte-carlo`,
    `independence`), and a third WITH a handful of candles too few for any real train/test
    window (`walk-forward`'s own refusal needs candles to exist, just not enough of them).
    `trade_outcomes`/`orders` stay empty (`significance --from deployment`, `pooled-review`).
    """
    path = tmp_path / "refusal-fixture.db"
    conn = connect(str(path))
    migrate(conn)
    repo = Repository(conn)
    now = 1_800_000_000
    repo.insert_rule("turtle_breakout", {"product_id": "BTC-USD"}, status="candidate", now_ts=now)
    repo.insert_rule("turtle_breakout", {"product_id": "BTC-USD"}, status="candidate", now_ts=now)
    rule_wf = repo.insert_rule(
        "turtle_breakout", {"product_id": "ETH-USD"}, status="candidate", now_ts=now
    )
    assert rule_wf == 3
    tiny_candles = [
        Candle(
            ts=1_700_000_000 + i * 86400,
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100"),
            volume=Decimal("1"),
        )
        for i in range(5)
    ]
    repo.upsert_candles("ETH-USD", Granularity.ONE_DAY, tiny_candles)
    conn.close()
    return path


#: Per-subcommand argv (after `research <name>`) that drives it into an ACTUAL refusal
#: against `_refusal_fixture_db` -- declared, not derived, so a subcommand added later with
#: no entry here fails this test immediately via the `KeyError` in the loop below, exactly
#: the "a seventh subcommand added later that cannot refuse must fail this pin" the issue
#: asks for.
_REFUSAL_ARGS: dict[str, tuple[str, ...]] = {
    "significance": ("--from", "deployment"),
    "pooled-review": ("--db", "{db}"),
    "throughput": ("--venues-json", "[]"),
    "tuning": ("--run",),
    "factors": ("--product", "NO-SUCH-PRODUCT"),
    "independence": ("--rule-a", "1", "--rule-b", "2", "--granularity", "ONE_DAY"),
    "pbo": ("--ledger", "{tmp}/empty-trials.jsonl"),
    "deflate": ("--ledger", "{tmp}/empty-trials.jsonl", "--sharpe", "1.0"),
    "monte-carlo": (
        "--rule", "1", "--seed", "1", "--ledger", "{tmp}/mc-trials.jsonl",
    ),
    "walk-forward": (
        "--rule", "3", "--train-bars", "1000", "--test-bars", "1000",
        "--ledger", "{tmp}/wf-trials.jsonl",
    ),
}

#: Subcommands under `keel research` that are NOT "insufficient evidence" refusals, with the
#: reason each is excluded named right here rather than folded silently into the loop below.
_REFUSAL_PIN_EXCLUDED: dict[str, str] = {
    "index": (
        "a lookup over the front door's own table of contents, never a measurement over "
        "evidence; its own unknown-`--module` refusal is pinned separately by "
        "`test_index_unknown_module_refuses_but_exits_zero` above"
    ),
    "lookahead": (
        "a pass/fail DIAGNOSTIC gate (issue #440), not an #601 evidence refusal: its own "
        "docstring states it 'exits 1 on LOOKAHEAD DETECTED ... like `keel doctor`' -- a "
        "real finding it fails loud on, never a 'not enough evidence' result on stdout"
    ),
}


def test_every_evidence_subcommand_can_refuse_on_stdout_and_exit_zero(tmp_path):
    """#601's second acceptance criterion. See the module docstring's pin (d) and the two
    module-level tables above for the fixtures and the named exclusions.

    Mutation-verified: changing `research_throughput`'s refusal branch from `click.echo(f"refused:
    {exc}"); return` to `raise click.ClickException(str(exc))` made this fail with
    `AssertionError: keel research throughput did not exit 0 on its refusal fixture: Error:
    pooled trades per month must be > 0` (`assert 1 == 0` -- `<Result SystemExit(1)>`) before
    being reverted; see the commit message for the exact diff and the exact failure output.
    """
    db_path = _refusal_fixture_db(tmp_path)
    config_path = tmp_path / "missing-config.yaml"  # never created: config degrades to default

    checked: set[str] = set()
    for name in research_group.commands:
        if name in _REFUSAL_PIN_EXCLUDED:
            continue
        checked.add(name)
        assert name in _REFUSAL_ARGS, (
            f"keel research {name} has no declared refusal fixture in _REFUSAL_ARGS -- add "
            "one (or a named exclusion in _REFUSAL_PIN_EXCLUDED) before this subcommand can "
            "be trusted to refuse rather than crash on thin evidence"
        )
        argv = [
            arg.format(db=str(db_path), tmp=str(tmp_path)) for arg in _REFUSAL_ARGS[name]
        ]
        result = CliRunner().invoke(
            cli,
            ["--db", str(db_path), "--config", str(config_path), "research", name, *argv],
        )
        assert result.exit_code == 0, (
            f"keel research {name} did not exit 0 on its refusal fixture: {result.output}"
        )
        assert "refus" in result.output.lower(), (
            f"keel research {name} exited 0 but printed no refusal on its underpowered "
            f"fixture: {result.output!r}"
        )

    # The dynamic enumeration is the point (#601): every non-excluded name click actually
    # registered under `research_group` was exercised above, not a hardcoded subset of it.
    assert checked == set(research_group.commands) - set(_REFUSAL_PIN_EXCLUDED)
