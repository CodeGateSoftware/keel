"""`keel research` -- one front door over the thirteen evidence modules in `keel/research/`
(issue #601).

Every one of those modules already exists on `main`, and half of them already have a CLI:
`keel trials` fronts `ledger.py` (`record`/`list`/`verify`), `cscv.py` + `matrix.py` (`pbo`),
`deflate.py` (`deflate`), `montecarlo.py` (`monte-carlo`) and `walkforward.py`
(`walk-forward`); `keel rules lookahead` fronts `bias.py`. The other six --
`significance.py`, `cts_factors.py`, `independence.py`, `throughput.py`, `tuning.py` and
`pooled_review.py` -- have no CLI at all yet. So the actual gap `keel research` closes is not
"thirteen modules with no code path"; it is "no single place that says here is the evidence
toolkit". The tools were scattered across three groups and 30+ ad-hoc drivers in
`docs/experiments/*.py`, discoverable only by reading source.

`keel research index` is that place: for every module it states the question it answers,
what it CANNOT answer, and the exact command line (or, for a module still waiting on its own
subcommand, the pre-registered driver that runs it today) that gets you the number. The five
commands that already exist are registered a SECOND TIME under this group -- the same click
command objects, never copies (see the alias block below) -- so `keel research pbo` and
`keel trials pbo` are, byte for byte, the same code running.

**A refusal is a result here, not an error.** Every module in this package can legitimately
say "there is not enough evidence to answer that" -- `significance.py`'s docstring states the
discipline plainly: "a significance tool here must be able to say 'not distinguishable from
zero' and mean it. A tool that cannot say no is a flattery tool." That principle is why the
aliased commands in `keel.commands.trials` were edited alongside this file: an evidence-shaped
refusal now prints on stdout and exits 0, the same as any other measurement this group
reports. Reserve non-zero exits and `click.ClickException` for OPERATOR error -- a rule id
that does not exist, a ledger that cannot be read, a db that will not open -- never for "the
question was well-formed and the evidence cannot answer it".

**The Strathern rail.** `cscv.py`, `deflate.py` and `walkforward.py` each carry a ⛔ comment:
a score may report, and may gate, but may NEVER be a sweep's ranking key. This module is the
newest surface built over those three, which makes it the newest place the rail could leak
into a ranking -- so nothing in here sorts, ranks, or picks a "best" configuration by a score.
`tests/commands/test_research_front_door.py` enforces that with a source scan, and the index
below states the rail sentence next to every rail-bearing module so a reader meets it exactly
where they are about to run one.

**ADR 0003** (`docs/decisions/0003-commands-layer-survey.md`) is the record this module answers
to: its standing rule 3 requires that "a decision-bearing path added to `commands/` must
delegate its deciding comparison to a compute module and cite this record." Every subcommand
below -- the five aliases and the six Wave B additions (`significance`, `pooled-review`,
`throughput`, `tuning`, `factors`, `independence`) -- assembles inputs, calls a function in
`keel/research/*`, and prints what comes back; the comparison that decides a verdict, a refusal,
or a number always lives in the module it fronts, never here.
"""

from __future__ import annotations

import bisect
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import click

from keel.commands import rules as rules_mod
from keel.commands._common import _open_repo
from keel.commands.rules import rules_group
from keel.commands.trials import trials_group
from keel.research import cts_factors as cts_factors_mod
from keel.research import independence as independence_mod
from keel.research import pooled_review as pooled_review_mod
from keel.research import significance as significance_mod
from keel.research import throughput as throughput_mod
from keel.research import tuning as tuning_mod
from keel.types import Granularity

#: The sentence a reader must meet next to every rail-bearing module (pinned by test: this
#: exact wording is what `cscv.py`/`deflate.py`/`walkforward.py` themselves state as their
#: ⛔ STRATHERN RAIL). Declared once so the index, the tests, and any future renderer quote
#: the same words rather than three independent paraphrases drifting apart.
RAIL_SENTENCE = (
    "a score may report, and may gate, but may NEVER be a sweep's ranking key -- nothing "
    "here returns the identity of a best-performing configuration"
)


@dataclass(frozen=True)
class ResearchModuleEntry:
    """One row of the front door: a module in `keel/research/`, the question it answers,
    what it structurally cannot answer, and where an operator actually runs it.

    `module` is the bare filename (`"significance.py"`), matched against
    `keel/research/*.py` by the completeness pin in
    `tests/commands/test_research_front_door.py` -- a fourteenth module with no row here
    fails that test. `runs_as` is either a full `keel ...` command line, when one is
    registered in the CLI, or a `docs/experiments/*.py` path, when the module's own
    subcommand has not landed yet; the same pin resolves whichever it is.
    """

    module: str
    question: str
    cannot_answer: str
    runs_as: str
    rail: bool = False


# The declared order below is a LITERAL list, not a computed sort (see the module docstring
# and `test_research_front_door.py`'s Strathern-rail pin for why this group never sorts,
# ranks or maxes over anything, including its own table of contents): it is the order issue
# #601 itself first enumerated the thirteen modules in. Keeping that order legible here means
# a diff that reorders this tuple is a deliberate editorial choice, not a side effect of some
# key function silently changing its mind.
RESEARCH_INDEX: tuple[ResearchModuleEntry, ...] = (
    ResearchModuleEntry(
        module="significance.py",
        question=(
            "Is a rule family's edge distinguishable from zero at the fee actually paid -- "
            "a one-proportion test of the observed win rate against the payoff-implied "
            "break-even, corrected to effective (n_eff, not raw) observations."
        ),
        cannot_answer=(
            "Cannot say an edge EXISTS -- only whether this much evidence could distinguish "
            "one from zero at the stated power. 'Not distinguishable from zero' is a real, "
            "printed answer here, not a failure to compute one."
        ),
        runs_as="keel research significance",
    ),
    ResearchModuleEntry(
        module="montecarlo.py",
        question=(
            "Trade-reshuffle and moving-block candle-bootstrap resampling -- did one lucky "
            "PATH produce this equity curve, distinct from asking whether the family is net "
            "positive (significance.py) or whether the selection process was overfit "
            "(cscv.py)."
        ),
        cannot_answer=(
            "The reshuffle's final-equity percentile is EXACTLY 1/2 BY CONSTRUCTION -- a "
            "permutation of a multiset always sums to the same number -- so it can never "
            "tell you whether the final equity was lucky. Only the path shape between start "
            "and end (drawdown depth, time underwater) carries information."
        ),
        runs_as="keel research monte-carlo",
    ),
    ResearchModuleEntry(
        module="cscv.py",
        question=(
            "Probability of Backtest Overfitting via CSCV (model-free, non-parametric, "
            "deterministic) over the matrix of per-period P&L across every configuration "
            "tried."
        ),
        cannot_answer=(
            "Nothing here returns the identity of a best-performing configuration -- PBO "
            "evaluates the quality of a SELECTION PROCESS, never names the selection."
        ),
        runs_as="keel research pbo",
        rail=True,
    ),
    ResearchModuleEntry(
        module="deflate.py",
        question=(
            "Turns 'we tried N configurations' into a number: the Deflated Sharpe Ratio, "
            "the expected maximum Sharpe of N zero-skill trials, and the Minimum Backtest "
            "Length the observed performance needs to clear selection bias."
        ),
        cannot_answer=(
            "Reporting only -- none of E[max SR_n], SR_0, DSR or MinBTL may ever rank or "
            "select a configuration; they price the bar one already-chosen strategy has to "
            "clear."
        ),
        runs_as="keel research deflate",
        rail=True,
    ),
    ResearchModuleEntry(
        module="walkforward.py",
        question=(
            "Rolling-origin walk-forward validation of ONE parameter set, fixed before any "
            "fold runs: does it hold up out-of-sample across a rolling series of train/test "
            "windows, and does performance degrade as the data moves away from the period "
            "the set was conceived on."
        ),
        cannot_answer=(
            "Cannot tell you which fold or window WON -- no public function returns a fold, "
            "window or parameter set to favour, because none is ever computed. It validates "
            "a GIVEN rule across GIVEN folds; it never compares alternatives."
        ),
        runs_as="keel research walk-forward",
        rail=True,
    ),
    ResearchModuleEntry(
        module="independence.py",
        question=(
            "Whether two rules (or two horizons of one rule) are actually independent: "
            "position-vector overlap (Jaccard), signal correlation, entry-timing distance, "
            "and P&L correlation."
        ),
        cannot_answer=(
            "Cannot tell you a correlated pair is WRONG to run together -- only that it is "
            "not contributing N independent observations' worth of evidence. Whether that "
            "correlation is acceptable is left to the operator, not decided here."
        ),
        runs_as="keel research independence",
    ),
    ResearchModuleEntry(
        module="throughput.py",
        question=(
            "Allowance-throughput planning: how many signals a venue's fee-free volume "
            "allowance can actually carry per month, and how long -- in EFFECTIVE "
            "observations, via the herding design effect -- the evidence honestly takes to "
            "gather."
        ),
        cannot_answer=(
            "The allocator moves trades INTO an existing allowance; it can never enlarge "
            "one. A product that does not fit is deferred with a reason, never squeezed "
            "through the cap."
        ),
        runs_as="keel research throughput",
    ),
    ResearchModuleEntry(
        module="cts_factors.py",
        question=(
            "Do the 11 CTS confluence factors carry independent evidence, or is one "
            "momentum read counted three times -- pairwise correlation/collinearity over "
            "both an unconditional (every bar) and a conditional (only fired, gate-cleared "
            "bars) replay sample."
        ),
        cannot_answer=(
            "Measures collinearity only; it is never imported by the live path and cannot "
            "say whether the CTS score itself is profitable -- that is a different question "
            "this module does not ask."
        ),
        runs_as="keel research factors",
    ),
    ResearchModuleEntry(
        module="ledger.py",
        question=(
            "The append-only, hash-chained record of *experiments* (never money): what was "
            "tried, its provenance (a_priori/fitted), and whether the chain has been "
            "tampered with since."
        ),
        cannot_answer=(
            "Carries no statistics of its own -- it cannot say whether a trial's result was "
            "good, only whether the record of it is intact, and how many decision trials "
            "(N) sit inside the total row count (M)."
        ),
        runs_as="keel trials list",
    ),
    ResearchModuleEntry(
        module="pooled_review.py",
        question=(
            "The pre-registered 2026-09-30 pooled forward-trades review: the pooled win "
            "rate against break-even at the n_eff-corrected interval, with the honest power "
            "sentence -- 'at this n_eff, this review can only see an edge of X points or "
            "larger' -- always printed beside it."
        ),
        cannot_answer=(
            "Renders NO pass/fail verdict on the edge -- the only verdict-shaped statement "
            "is about POWER, never about whether the edge is real. A pool with nothing "
            "counted refuses rather than emit a degenerate report."
        ),
        runs_as="keel research pooled-review",
    ),
    ResearchModuleEntry(
        module="bias.py",
        question=(
            "Lookahead and recursive-bias detection: does a stored rule's decision at bar N "
            "change once bars after N become visible, replayed through the exact detect-on-"
            "growing-prefix seam the backtester and live engine both use."
        ),
        cannot_answer=(
            "Says nothing about whether parameters were over-selected across a trial matrix "
            "(cscv.py's question) -- a clean lookahead verdict is not evidence the strategy "
            "has a genuine edge, only that it is not reading the future to get one."
        ),
        runs_as="keel research lookahead",
    ),
    ResearchModuleEntry(
        module="matrix.py",
        question=(
            "Assembles the CSCV (T x N) matrix from ledger trials, enforcing the one "
            "condition PBO itself does not check: a TRUE matrix, same rows for every "
            "column, observations synchronous across trials."
        ),
        cannot_answer=(
            "Cannot say whether the assembled matrix indicates overfitting -- that is "
            "cscv.py's question entirely. This module only says whether the columns are "
            "assemblable at all, refusing (not silently dropping) any column whose per-bar "
            "series was never kept."
        ),
        runs_as="keel research pbo",
    ),
    ResearchModuleEntry(
        module="tuning.py",
        question=(
            "An Optuna parameter study over a rule's own DECLARED parameter space -- train/"
            "held-out split, then PBO/CSCV over the study's own trials -- proposing "
            "CANDIDATES for the promotion gauntlet."
        ),
        cannot_answer=(
            "Cannot auto-tune a live or paper profile and cannot itself promote a rule -- a "
            "winner here is a hypothesis that still has to clear the unchanged gauntlet. Its "
            "most important output is the refusal line: 'no candidate may be proposed.'"
        ),
        runs_as="keel research tuning",
    ),
)

#: Names accepted by `--module`, in the same declared order as `RESEARCH_INDEX` -- derived
#: once here (never re-sorted) so the refusal listing below and the completeness pin read
#: the identical order a human sees in `keel research index`.
_MODULE_NAMES: tuple[str, ...] = tuple(entry.module.removesuffix(".py") for entry in RESEARCH_INDEX)


def _find_entry(name: str) -> ResearchModuleEntry | None:
    for entry in RESEARCH_INDEX:
        if entry.module.removesuffix(".py") == name:
            return entry
    return None


def render_index(entries: tuple[ResearchModuleEntry, ...]) -> list[str]:
    """Human-readable form of the index: module, question, what it cannot answer, where it
    runs -- and, for the three rail-bearing modules, the rail sentence stated right where a
    reader is about to go run one."""
    lines: list[str] = [
        "keel research -- the evidence toolkit index (issue #601)",
        "",
        "Every module below MEASURES. A refusal ('not enough evidence to answer that') is "
        "one of its results, printed on stdout, never an error.",
        "",
    ]
    for entry in entries:
        lines.append(f"{entry.module}")
        lines.append(f"  answers        : {entry.question}")
        lines.append(f"  cannot answer  : {entry.cannot_answer}")
        lines.append(f"  runs as        : {entry.runs_as}")
        if entry.rail:
            lines.append(f"  ⛔ Strathern rail: {RAIL_SENTENCE}")
        lines.append("")
    return lines


@click.group("research")
def research_group() -> None:
    """One front door over the thirteen evidence modules in `keel/research/` (issue #601).

    `keel research index` names all thirteen, what each answers, what each CANNOT answer,
    and the command (or pre-registered `docs/experiments/` driver) that runs it. The five
    commands aliased into this group below are the SAME objects `keel trials`/`keel rules`
    already register -- running `keel research pbo` runs exactly the code
    `keel trials pbo` does, not a second copy of it. Nothing here computes a new statistic;
    this group assembles inputs, calls into `keel/research/*`, and prints what comes back,
    including the refusal.
    """


@research_group.command("index")
@click.option("--json", "as_json", is_flag=True, default=False, help="Emit machine-readable JSON.")
@click.option(
    "--module",
    "module_name",
    default=None,
    help="Print just one module's entry (e.g. `significance`, `cscv`, `walkforward`).",
)
@click.pass_context
def research_index(ctx: click.Context, as_json: bool, module_name: str | None) -> None:
    """Print the front door: every module in `keel/research/`, what it answers, what it
    cannot answer, and where to run it.

    `--module NAME` narrows to one entry. An unknown NAME is itself a lookup that found
    nothing -- it prints the known names and exits 0, the same discipline this whole group
    applies to every other refusal: a well-formed question the index cannot answer is a
    result, not an error.
    """
    entries = RESEARCH_INDEX
    if module_name is not None:
        entry = _find_entry(module_name)
        if entry is None:
            known = ", ".join(_MODULE_NAMES)
            click.echo(
                f"refused: {module_name!r} is not one of the {len(_MODULE_NAMES)} research "
                f"modules. Known names: {known}"
            )
            return
        entries = (entry,)

    if as_json:
        click.echo(json.dumps([asdict(entry) for entry in entries], indent=2, default=str))
        return

    for line in render_index(entries):
        click.echo(line)


# -- significance: is a family's edge distinguishable from zero, at the fee actually paid -------
#
# Two ways to get `OutcomeRow` tuples for `significance.significance()`: the deployment's own
# `trade_outcomes` ledger (`--from deployment`, the default), or one stored rule's own backtest
# (`--from rule`). The deployment path reuses `pooled_review.ledger_round_trips` +
# `RoundTrip.outcome()` for the win/loss/scratch classification rather than re-deriving it from
# `pnl_net`'s sign a second time -- one classifier, shared with the pooled review below.


@research_group.command("significance")
@click.option(
    "--from",
    "source",
    type=click.Choice(["deployment", "rule"]),
    default="deployment",
    show_default=True,
    help="deployment: this db's own trade_outcomes ledger. rule: backtest one stored rule.",
)
@click.option(
    "--rule",
    "rule_id",
    type=int,
    default=None,
    help="Stored rule id (required for --from rule).",
)
@click.option(
    "--granularity",
    default=None,
    help="Candle granularity for --from rule (default: the rule's own, else ONE_HOUR).",
)
@click.option(
    "--family",
    default=None,
    help="Label for the report (default: 'deployment', or the rule's own kind).",
)
@click.option(
    "--fee-regime",
    type=click.Choice(sorted(significance_mod.FEE_REGIMES)),
    default=None,
    help="Restrict to one fee regime (default: BOTH, never an average -- significance.py's "
    "own rule).",
)
@click.pass_context
def research_significance(
    ctx: click.Context,
    source: str,
    rule_id: int | None,
    granularity: str | None,
    family: str | None,
    fee_regime: str | None,
) -> None:
    """Is a rule family's edge distinguishable from zero at the fee actually paid?

    `significance.significance()` is the whole measurement; this command only assembles the
    `OutcomeRow` sequence it reads. "Not distinguishable from zero" and "insufficient_n" are
    both legitimate, printed verdicts (issue #601's second bullet) -- neither is a failure of
    this command, and both come straight out of `render_family`. What IS a refusal here, printed
    before any regime is priced, is having no closed trades to test at all.
    """
    if source == "rule" and rule_id is None:
        raise click.ClickException("--from rule requires --rule ID")

    repo = _open_repo(ctx)
    outcomes: list[significance_mod.OutcomeRow]
    if source == "deployment":
        rows = repo.get_trade_outcomes()
        ledger_rows: list[pooled_review_mod.LedgerRow] = [
            pooled_review_mod.LedgerRow(
                product_id=row["product_id"],
                rule_name=row["rule_name"],
                opened_at=row["opened_at"],
                closed_at=row["closed_at"],
                qty=row["qty"],
                entry_fill=row["entry_fill"],
                exit_fill=row["exit_fill"],
                fees=row["fees"],
                pnl_net=row["pnl_net"],
            )
            for row in rows
        ]
        trips = pooled_review_mod.ledger_round_trips("deployment", ledger_rows)
        outcomes = [trip.outcome() for trip in trips]
        label = family or "deployment"
    else:
        assert rule_id is not None  # guarded above
        config = rules_mod._optional_cfg(ctx)
        try:
            resolved = rules_mod.resolve_rule_backtest(
                repo, config, rule_id, granularity_opt=granularity
            )
        except rules_mod.RulesRefused as exc:
            raise click.ClickException(str(exc)) from exc
        result = rules_mod.backtest_resolved(resolved)
        outcomes = [
            (trade.outcome, trade.pnl, trade.r_multiple)
            for trade in result.trades
            if trade.outcome != "open"
        ]
        label = family or resolved.row["kind"]

    if not outcomes:
        click.echo(f"refused: no closed trades for {label!r} -- nothing to test")
        return

    regimes = (fee_regime,) if fee_regime is not None else tuple(significance_mod.FEE_REGIMES)
    for regime in regimes:
        stat = significance_mod.significance(
            label, regime, significance_mod.FEE_REGIMES[regime], outcomes
        )
        for line in significance_mod.render_family(stat):
            click.echo(line)
        click.echo("")


# -- pooled-review: the 2026-09-30 standing event (#427), through the front door -----------------
#
# `_connect_ro`/`read_orders`/`read_ledger` used to live only in the pre-registered driver
# (`docs/experiments/2026-09-30-pooled-review.py`); they now live HERE and the driver imports
# them, so there is exactly one reader of a deployment database and the CLI and the pre-
# registered driver structurally cannot diverge on what "the pool" means (#601). Every
# connection is `mode=ro`: this touches live deployment databases and must never write to one.
#
# The driver's own exit contract is UNCHANGED and stays that way on purpose: it prints a refusal
# to stderr and exits 2, because that contract was pre-registered before the review event and is
# frozen -- rewriting it now would be rewriting the pre-registration after the fact. This command
# is new surface, built after #601 decided a refusal is a result: it prints the same "nothing to
# review" finding to stdout and exits 0. Two exit shapes for the same finding, deliberately, and
# each one is honest about which contract it is answering to.

#: The three pre-registered deployment profiles (#353), read-only. Moved here from the driver so
#: both the driver and this command default to literally the same three paths.
DEFAULT_POOLED_REVIEW_DBS: tuple[str, ...] = (
    str(Path.home() / "keel" / "keel.db"),
    str(Path.home() / "keel" / "keel-live.db"),
    str(Path.home() / "keel" / "keel-paperhourly.db"),
)


def _connect_ro(db_path: str) -> sqlite3.Connection:
    """The house read-only connection (`mode=ro`) -- a deployment db is read, never written."""
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def read_orders(db_path: str) -> tuple[list[pooled_review_mod.OrderRow], dict[int, str]]:
    """The profile's `orders` rows (money as Decimal) and its `rule_id -> rules.kind` map.

    Ascending id -- the ledger's own event sequencing, which the matcher relies on because the
    live `created_at` values demonstrably disagree with it.
    """
    connection = _connect_ro(db_path)
    try:
        order_rows = connection.execute(
            "SELECT id, mode, product_id, side, qty, status, actual_fill, fee, rule_id, "
            "created_at FROM orders ORDER BY id"
        ).fetchall()
        rule_rows = connection.execute("SELECT id, kind FROM rules ORDER BY id").fetchall()
    finally:
        connection.close()
    orders = [
        pooled_review_mod.OrderRow(
            id=int(row["id"]),
            mode=str(row["mode"]),
            product_id=str(row["product_id"]),
            side=str(row["side"]),
            qty=Decimal(str(row["qty"])),
            status=str(row["status"]),
            actual_fill=None if row["actual_fill"] is None else Decimal(str(row["actual_fill"])),
            fee=None if row["fee"] is None else Decimal(str(row["fee"])),
            rule_id=None if row["rule_id"] is None else int(row["rule_id"]),
            created_at=int(row["created_at"]),
        )
        for row in order_rows
    ]
    return orders, {int(row["id"]): str(row["kind"]) for row in rule_rows}


def read_ledger(db_path: str) -> list[pooled_review_mod.LedgerRow]:
    """The profile's `trade_outcomes` rows, oldest first (the ledger reader's convention)."""
    connection = _connect_ro(db_path)
    try:
        rows = connection.execute(
            "SELECT product_id, rule_name, opened_at, closed_at, qty, entry_fill, "
            "exit_fill, fees, pnl_net FROM trade_outcomes ORDER BY closed_at, id"
        ).fetchall()
    finally:
        connection.close()
    return [
        pooled_review_mod.LedgerRow(
            product_id=str(row["product_id"]),
            rule_name=str(row["rule_name"]),
            opened_at=int(row["opened_at"]),
            closed_at=int(row["closed_at"]),
            qty=Decimal(str(row["qty"])),
            entry_fill=Decimal(str(row["entry_fill"])),
            exit_fill=Decimal(str(row["exit_fill"])),
            fees=Decimal(str(row["fees"])),
            pnl_net=Decimal(str(row["pnl_net"])),
        )
        for row in rows
    ]


def _pooled_review_db_reachable(db_path: str) -> bool:
    try:
        connection = _connect_ro(db_path)
    except sqlite3.Error:
        return False
    connection.close()
    return True


def _pooled_review_jsonl_row(review: pooled_review_mod.DescriptiveReview) -> dict[str, Any]:
    """The command's own one-row summary. Presentation only -- every field is already computed
    by `pooled_review.py`; this is not the driver's `jsonl_row` (which the driver still owns
    unchanged) because the two artifacts answer different callers and are not required to share
    a schema, unlike `_connect_ro`/`read_orders`/`read_ledger`, which are the DATA the pool
    means and must never have two copies."""
    sample = review.sample
    stat = review.stat
    return {
        "run_date": review.run_date,
        "event_date": review.event_date,
        "profiles": list(review.profiles),
        "pooled_n": sample.n_pooled(),
        "counted_n": sample.counted(),
        "win_rate": str(stat.win_rate),
        "edge": str(stat.edge),
        "n_effective": str(stat.n_effective),
        "fee_pct": str(review.fee_pct),
        "power_sentence": review.sentence,
    }


@research_group.command("pooled-review")
@click.option(
    "--db",
    "dbs",
    multiple=True,
    help=f"Deployment profile db, repeatable (default: {list(DEFAULT_POOLED_REVIEW_DBS)}).",
)
@click.option(
    "--run-date",
    default=None,
    help=f"Run date label, ISO (default: today UTC). A date before "
    f"{pooled_review_mod.EVENT_DATE} labels the report a preview of the event.",
)
@click.option(
    "--out",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Optional markdown report path to also write.",
)
@click.option(
    "--jsonl",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Optional one-row JSON summary path to also write.",
)
def research_pooled_review(
    dbs: tuple[str, ...], run_date: str | None, out: Path | None, jsonl: Path | None
) -> None:
    """The pre-registered 2026-09-30 pooled forward-trades review (#427, tracked in #353).

    Reads every listed profile db READ-ONLY, pools the closed round trips per the frozen
    pre-registration in `docs/experiments/2026-09-30-pooled-review.py`'s own docstring, and
    prints `pooled_review.render_report` verbatim -- or, when the pool has nothing counted,
    the refusal, on stdout, exit 0 (see the module docstring for why that differs from the
    driver's stderr/exit-2 contract).
    """
    db_list = list(dbs) if dbs else list(DEFAULT_POOLED_REVIEW_DBS)
    resolved_run_date = run_date or datetime.now(UTC).date().isoformat()

    unreachable = [db for db in db_list if not _pooled_review_db_reachable(db)]
    if unreachable:
        raise click.ClickException(
            "pre-registered profile db(s) not reachable read-only: "
            + ", ".join(unreachable)
            + " -- the pool cannot be read as pre-registered"
        )

    per_profile: list[
        tuple[str, pooled_review_mod.OrdersRead, list[pooled_review_mod.LedgerRow]]
    ] = []
    for db in db_list:
        try:
            orders, rule_kinds = read_orders(db)
            read = pooled_review_mod.round_trips_from_orders(db, orders, rule_kinds)
            ledger = read_ledger(db)
        except (ValueError, sqlite3.Error) as exc:
            raise click.ClickException(f"{db} cannot be read as pre-registered: {exc}") from exc
        per_profile.append((db, read, ledger))

    sample = pooled_review_mod.build_sample(per_profile)
    review = pooled_review_mod.descriptive_review(sample, run_date=resolved_run_date)

    if pooled_review_mod.is_refused(sample):
        refusal = review.refusal or ("nothing to review",)
        click.echo(f"refused: {refusal[0]}")
        for line in refusal[1:]:
            click.echo(line)
        return

    lines = pooled_review_mod.render_report(review)
    for line in lines:
        click.echo(line)

    if out is not None:
        out.write_text("\n".join(lines) + "\n")
        click.echo(f"\nwrote {out}")
    if jsonl is not None:
        jsonl.write_text(json.dumps(_pooled_review_jsonl_row(review)) + "\n")
        click.echo(f"wrote {jsonl}")


# -- throughput: pure arithmetic, no db needed ----------------------------------------------------


def _decimal_or_none(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


@research_group.command("throughput")
@click.option(
    "--venues-json",
    required=True,
    help='JSON array of venue inputs: [{"venue": "coinbase", "monthly_allowance": "500" '
    '(or null for unlimited), "mean_trade_notional": "4212", "expected_signals_per_month": "1"}]',
)
@click.option(
    "--target-edge",
    default="0.05",
    show_default=True,
    help="Win-rate edge (fraction, e.g. 0.05 = 5 points) months_to_target should price.",
)
@click.option(
    "--products-json",
    default=None,
    help='Optional JSON array of products to also run the allocator over: [{"symbol": "SOL-USD",'
    ' "venues": ["coinbase"], "mean_trade_notional": "..", "expected_signals_per_month": ".."}]',
)
@click.option(
    "--allowances-json",
    default=None,
    help='JSON object {"venue": allowance-or-null}, required together with --products-json.',
)
def research_throughput(
    venues_json: str,
    target_edge: str,
    products_json: str | None,
    allowances_json: str | None,
) -> None:
    """Allowance-throughput planning: how many signals a fee-free allowance can carry a month,
    and how long the evidence honestly takes to accumulate (throughput.py).

    Pure arithmetic -- no db, no candles, no rule. `--venues-json` states what
    `render_report`/`months_to_target` need to know about each venue; `--products-json` +
    `--allowances-json`, when both given, additionally run the allocator.

    ONE failure here is evidence-shaped and prints as a refusal at exit 0 (#601): nothing is
    flowing, so no time-to-detection can be stated. It is caught by its own named type,
    `throughput.InsufficientThroughput`. Everything else this command can hit is an OPERATOR
    MISTAKE and exits non-zero: a non-positive `mean_trade_notional` (validated below, before
    the module is called at all), a `--target-edge` outside (0, 1), and a product eligible on
    no listed venue -- which `throughput.allocate`'s own docstring calls "an error the caller
    must fix in the eligibility table, not silently droppable inventory". Reporting that as a
    refusal would be this command overruling the callee's stated claim about itself.
    """
    try:
        venues = [
            throughput_mod.VenueThroughput(
                venue=str(row["venue"]),
                monthly_allowance=_decimal_or_none(row.get("monthly_allowance")),
                mean_trade_notional=Decimal(str(row["mean_trade_notional"])),
                expected_signals_per_month=Decimal(str(row["expected_signals_per_month"])),
            )
            for row in json.loads(venues_json)
        ]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise click.ClickException(f"--venues-json is malformed: {exc}") from exc

    # An operator mistake, checked HERE rather than left to surface from inside the module:
    # `VenueThroughput.trades_per_month` raises a bare ValueError for this, and a typo in
    # --venues-json is a wrong request, not thin evidence. Same shape as the --target-edge
    # check below, which this file already validated this way.
    for venue in venues:
        if venue.monthly_allowance is not None and venue.mean_trade_notional <= 0:
            raise click.ClickException(
                f"--venues-json: {venue.venue} has mean_trade_notional "
                f"{venue.mean_trade_notional} -- must be > 0 to divide an allowance by it"
            )

    edge = Decimal(str(target_edge))
    if not (Decimal(0) < edge < Decimal(1)):
        raise click.ClickException("--target-edge must be a fraction in (0, 1), e.g. 0.05")

    try:
        lines = throughput_mod.render_report(venues, edge)
    except throughput_mod.InsufficientThroughput as exc:
        # The ONE evidence-shaped failure in throughput.py, caught by its own named type
        # rather than by a bare `except ValueError` around the whole call. render_report also
        # reaches `trades_per_month` and `required_n_eff`, and every ValueError THOSE raise is
        # an operator mistake; a wide catch here would print an operator's typo as `refused:`
        # and exit 0, which is the failure `trials.py`'s walk-forward comment forbids and
        # which this command committed until #601 review caught it.
        click.echo(f"refused: {exc}")
        return
    for line in lines:
        click.echo(line)

    if products_json is None:
        return
    if allowances_json is None:
        raise click.ClickException("--products-json requires --allowances-json")
    try:
        products = [
            throughput_mod.Product(
                symbol=str(row["symbol"]),
                venues=tuple(row["venues"]),
                mean_trade_notional=Decimal(str(row["mean_trade_notional"])),
                expected_signals_per_month=Decimal(str(row["expected_signals_per_month"])),
            )
            for row in json.loads(products_json)
        ]
        allowances = {
            str(venue): _decimal_or_none(allowance)
            for venue, allowance in json.loads(allowances_json).items()
        }
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise click.ClickException(f"--products-json/--allowances-json malformed: {exc}") from exc

    try:
        plans = throughput_mod.allocate(products, allowances)
    except ValueError as exc:
        # NOT a refusal. `allocate` raises only for a product eligible on no listed venue,
        # and its own docstring calls that "an error the caller must fix in the eligibility
        # table, not silently droppable inventory". The callee states what its failure means;
        # a front door that relabels it "refused: ..." and exits 0 is the caller overruling
        # that claim, and would hide a mismatched --products-json/--allowances-json pair.
        raise click.ClickException(str(exc)) from exc

    click.echo("")
    click.echo("allocation:")
    for plan in plans:
        cap = "unlimited" if plan.allowance is None else f"{plan.allowance}/month"
        enabled = [product.symbol for product in plan.enabled]
        deferred = [product.symbol for product in plan.deferred]
        click.echo(
            f"  {plan.venue} (cap {cap}): enabled={enabled} deferred={deferred} "
            f"spend={plan.spend_per_month} trades/month={plan.trades_per_month}"
        )


# -- tuning: the declared search spaces, never optuna ----------------------------------------------
#
# `keel/research/tuning.py` imports optuna lazily, inside `run_study` only, so `import
# keel.research.tuning` (the line above) stays clean without it. This command must hold to the
# same discipline for the exact same reason (#601's fourth bullet): optuna is a dev-only
# dependency (`pyproject.toml`'s dev group) and the shipped CLI must import cleanly without it.
# `tests/commands/test_research_front_door.py` pins that with an AST scan of this file for any
# `import optuna`/`from optuna import ...` -- there is none, on purpose: everything below reads
# `tuning.SEARCH_SPACES`/`declared_cells`/`explored_vs_declared`, none of which touch optuna.


@research_group.command("tuning")
@click.option(
    "--rule-kind",
    default=None,
    help="Narrow to one family's declared search space (default: every declared family).",
)
@click.option(
    "--explored-json",
    default=None,
    help='JSON {"dimension": [min, max]} actually swept; requires --rule-kind.',
)
@click.option(
    "--run",
    is_flag=True,
    default=False,
    help="Run an optuna parameter study (always refused -- see below).",
)
def research_tuning(rule_kind: str | None, explored_json: str | None, run: bool) -> None:
    """The declared per-family search spaces and exploration/gate vocabulary (tuning.py) --
    never a study.

    `--run` is always refused: running a study needs optuna, a dev-only dependency this shipped
    command must not import (#601's fourth bullet), so the refusal names the pre-registered
    driver that runs one instead, `docs/experiments/2026-08-22-optuna-parameter-study.py`.
    """
    if run:
        click.echo(
            "refused: keel research tuning reports the declared search spaces only -- optuna "
            "is a dev-only dependency (pyproject.toml) and the shipped CLI must import "
            "cleanly without it. Run a study with "
            "`docs/experiments/2026-08-22-optuna-parameter-study.py` instead."
        )
        return

    if explored_json is not None and rule_kind is None:
        raise click.ClickException("--explored-json requires --rule-kind")

    kinds = [rule_kind] if rule_kind is not None else list(tuning_mod.SEARCH_SPACES)
    for kind in kinds:
        if kind not in tuning_mod.SEARCH_SPACES:
            raise click.ClickException(
                f"unknown rule kind {kind!r}; declared families: "
                f"{sorted(tuning_mod.SEARCH_SPACES)}"
            )
        space = tuning_mod.SEARCH_SPACES[kind]
        cells = tuning_mod.declared_cells(kind)
        click.echo(f"{kind}: declared search space ({cells} cells)")
        for name, bounds in space.items():
            click.echo(f"  {name}: {bounds}")

    if explored_json is not None:
        assert rule_kind is not None  # guarded above
        try:
            explored = {
                name: (bounds[0], bounds[1])
                for name, bounds in json.loads(explored_json).items()
            }
        except (json.JSONDecodeError, AttributeError, KeyError, TypeError, IndexError) as exc:
            # Same treatment the sibling --venues-json/--products-json options already get: a
            # malformed option value is an OPERATOR mistake and exits non-zero with a clean
            # message, never a traceback. Without this, 'not json' raised JSONDecodeError,
            # '{"period": 5}' TypeError, and '{"period": [5]}' IndexError, each straight
            # through to the user as a stack trace.
            raise click.ClickException(
                f"--explored-json is malformed: {exc} -- expected an object mapping a "
                'parameter name to a [low, high] pair, e.g. \'{"entry": [20, 40]}\''
            ) from exc
        try:
            check = tuning_mod.explored_vs_declared(explored, rule_kind)
        except ValueError as exc:
            # The sweep as described does not fit the rule's own declared space (an out-of-
            # bounds range, or a dimension the rule never declared) -- an honesty check the
            # data fails, not an operator typo. Print it, exit 0.
            click.echo(f"refused: {exc}")
            return
        click.echo(
            f"  explored {check.explored_cells} of {check.declared_cells} declared cells"
        )


# -- factors: do the 11 CTS confluence factors carry independent evidence? -----------------------


def _render_factors(
    product_id: str,
    sample: cts_factors_mod.FactorSample,
    stats: list[cts_factors_mod.PairStat],
    clusters: list[cts_factors_mod.ClusterReport],
    variance: cts_factors_mod.VarianceReport,
) -> list[str]:
    """Formatting only: every field below was computed by `cts_factors.py`. Printed in the
    order those functions already returned it (`pair_stats`/`holm_adjust` sort by |phi|
    themselves; nothing here re-sorts)."""
    lines = [
        f"CTS factor collinearity -- {product_id}, n={sample.n} observations, "
        f"{len(sample.varying())} varying factor(s) of {len(cts_factors_mod.FACTOR_NAMES)}",
        "",
        "pairwise (Holm-Bonferroni adjusted; '*' = significant after correction):",
    ]
    for stat in stats:
        flag = " *" if stat.significant else ""
        lines.append(
            f"  {stat.a} x {stat.b}: phi={stat.phi} jaccard={stat.jaccard} lift={stat.lift} "
            f"p={stat.p_value:.4g} p_holm={stat.p_holm:.4g}{flag}"
        )
    lines.append("")
    lines.append("pre-declared clusters (cts_factors.SUSPECTED_CLUSTERS):")
    for cluster in clusters:
        lines.append(
            f"  {cluster.name} {cluster.members}: mean_within_phi={cluster.mean_within_phi} "
            f"max_within_phi={cluster.max_within_phi} mean_other_phi={cluster.mean_other_phi} "
            f"mean_within_jaccard={cluster.mean_within_jaccard} weight_share={cluster.weight_share}"
        )
    lines += [
        "",
        f"CTS total variance: observed={variance.observed} independent={variance.independent} "
        f"ratio={variance.ratio} mean_total={variance.mean_total}",
    ]
    return lines


@research_group.command("factors")
@click.option("--product", "product_id", required=True, help="Product id to replay (e.g. BTC-USD).")
@click.option(
    "--granularity",
    default="ONE_DAY",
    show_default=True,
    help="Candle granularity (cts_factors.py's primary arm: ONE_DAY, expanding window).",
)
@click.option("--warmup", default=cts_factors_mod.DEFAULT_WARMUP, show_default=True, type=int)
@click.option(
    "--window",
    default=None,
    type=int,
    help="Fixed lookback (default: expanding from the first cached bar -- the live path).",
)
@click.option("--step", default=1, show_default=True, type=int, help="Bar-index thinning.")
@click.option(
    "--alpha",
    default=0.05,
    show_default=True,
    type=float,
    help="Holm-Bonferroni family-wise alpha.",
)
@click.pass_context
def research_factors(
    ctx: click.Context,
    product_id: str,
    granularity: str,
    warmup: int,
    window: int | None,
    step: int,
    alpha: float,
) -> None:
    """Do the 11 CTS confluence factors carry independent evidence, unconditionally replayed
    over one product's own candle cache (cts_factors.py, #208)?

    The UNCONDITIONAL sample (`replay_every_bar`) is what carries the headline per the module's
    own docstring -- the conditional (fired-signal) sample is a collider and is not offered
    here. A sample where no factor varies has no correlation to report and refuses.
    """
    try:
        gran = Granularity(granularity)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    repo = _open_repo(ctx)
    candles = repo.get_candles(product_id, gran)
    if not candles:
        click.echo(f"refused: no cached candles for {product_id} {gran.value} -- nothing to replay")
        return

    sample = cts_factors_mod.replay_every_bar(
        product_id, candles, warmup=warmup, window=window, step=step
    )
    varying = sample.varying()
    if not varying:
        click.echo(
            f"refused: n={sample.n} observations replayed and no factor varies (every factor "
            "was constantly present or constantly absent) -- a constant vector has no "
            "correlation to report"
        )
        return

    stats = cts_factors_mod.pair_stats(sample, varying)
    adjusted = cts_factors_mod.holm_adjust(stats, alpha=alpha)
    clusters = cts_factors_mod.cluster_report(sample, adjusted)
    variance = cts_factors_mod.variance_report(sample)
    for line in _render_factors(product_id, sample, adjusted, clusters, variance):
        click.echo(line)


# -- independence: are two rules actually independent? (§80.16) ----------------------------------


def _render_independence(
    rule_a_id: int, rule_b_id: int, report: independence_mod.IndependenceReport
) -> list[str]:
    """Formatting only: every field is `independence.compare()`'s own output."""
    return [
        f"independence -- rule {rule_a_id} vs rule {rule_b_id} over {report.n_periods} "
        "common bars (§80.16)",
        f"  active bars: rule {rule_a_id}={report.a_active} rule {rule_b_id}={report.b_active} "
        f"both={report.both_active}",
        f"  jaccard overlap        : {report.jaccard}",
        f"  position correlation   : {report.position_correlation}",
        f"  pnl correlation        : {report.pnl_correlation}",
        f"  median entry distance  : {report.median_entry_distance}",
        f"  entry distances (n={len(report.entry_distances)}): {report.entry_distances}",
    ]


@research_group.command("independence")
@click.option("--rule-a", "rule_a_id", required=True, type=int, help="First stored rule id.")
@click.option("--rule-b", "rule_b_id", required=True, type=int, help="Second stored rule id.")
@click.option(
    "--granularity",
    default=None,
    help="Override both rules' candle granularity (default: each rule's own, else ONE_HOUR).",
)
@click.pass_context
def research_independence(
    ctx: click.Context, rule_a_id: int, rule_b_id: int, granularity: str | None
) -> None:
    """Are two rules (or two horizons of one rule) actually independent (independence.py,
    §80.16)? Correlated rules inflate N without adding independent evidence (§73.5).

    Position and per-bar P&L vectors are built here over the two rules' COMMON bar index.
    `compare()` has no opinion on how its input vectors are assembled, only on what to compute
    once they are aligned onto one calendar -- but "just bookkeeping" undersold it, so
    `_vectors` below now states the two choices it makes (closed trades only; a timestamp off
    the common index maps to the nearest bar INSIDE the trade, never to the end of history)
    and why each is the conservative one.
    """
    repo = _open_repo(ctx)
    config = rules_mod._optional_cfg(ctx)
    try:
        resolved_a = rules_mod.resolve_rule_backtest(
            repo, config, rule_a_id, granularity_opt=granularity
        )
        resolved_b = rules_mod.resolve_rule_backtest(
            repo, config, rule_b_id, granularity_opt=granularity
        )
    except rules_mod.RulesRefused as exc:
        raise click.ClickException(str(exc)) from exc

    result_a = rules_mod.backtest_resolved(resolved_a)
    result_b = rules_mod.backtest_resolved(resolved_b)

    closed_a = [trade for trade in result_a.trades if trade.outcome != "open"]
    closed_b = [trade for trade in result_b.trades if trade.outcome != "open"]
    if not closed_a or not closed_b:
        empty = [
            f"rule {rule_id}"
            for rule_id, closed in ((rule_a_id, closed_a), (rule_b_id, closed_b))
            if not closed
        ]
        click.echo(f"refused: {' and '.join(empty)} closed no trades -- nothing to compare")
        return

    # The COMMON bar index: the intersection of both rules' cached candle timestamps, ascending
    # (plain `sorted()`, no key -- a chronological ordering, never a ranking). See the docstring
    # above: this block is bookkeeping, `compare()` still does every actual measurement.
    ts_a = {candle.ts for candle in resolved_a.candles}
    ts_b = {candle.ts for candle in resolved_b.candles}
    common_ts = sorted(ts_a & ts_b)
    if not common_ts:
        click.echo(
            f"refused: rule {rule_a_id} and rule {rule_b_id} share no common bar timestamps "
            "-- nothing to compare"
        )
        return
    n = len(common_ts)

    def _vectors(trades: list[Any]) -> tuple[list[int], list[Decimal], list[int]]:
        """Project CLOSED trades onto the common index. Bookkeeping only -- but bookkeeping
        with two decisions in it, both made the conservative way after #601 review:

        * **Closed trades only**, the same population the emptiness guard above tests. An
          open trade has no realised P&L, so it would add occupied bars to `positions` while
          contributing nothing to `pnl`, and `compare()` correlates those two series against
          each other -- they must describe the same set of trades or the correlation is
          between mismatched populations. It would also have no exit bar, so counting it
          would mean asserting occupancy through the end of history on the strength of a
          position that has not resolved.
        * **A timestamp missing from the common index is mapped to the nearest common bar
          INSIDE the trade, never to the end of history.** The two rules' caches can differ
          in depth or have gaps, so `index_of` can miss either end. Defaulting a missing
          exit to `n - 1` (as this did before review) marks the position occupied to the
          last bar of the common window, which inflates the Jaccard overlap `compare()`
          reports whenever the caches disagree -- a fabricated agreement, in the one
          direction that flatters the answer. `bisect` instead: the entry becomes the first
          common bar at or after it, the exit the last common bar at or before it, and a
          trade whose whole span falls outside the common window is dropped rather than
          stretched to fill it.
        """
        positions = [0] * n
        pnl = [Decimal(0)] * n
        entries: list[int] = []
        for trade in trades:
            # First common bar at or after the entry; past the end means the trade opened
            # after the shared history stops, so there is nothing to place.
            start = bisect.bisect_left(common_ts, trade.entry_ts)
            if start >= n:
                continue
            # Last common bar at or before the exit. A closed trade always has an exit_ts
            # (only an open trade omits one, and those are excluded above).
            end = bisect.bisect_right(common_ts, trade.exit_ts) - 1
            if end < start:
                # The trade opened and closed between two common bars, or entirely before
                # the window: no common bar observes it.
                continue
            entries.append(start)
            for i in range(start, end + 1):
                positions[i] = 1
            if trade.pnl is not None:
                pnl[end] += trade.pnl
        return positions, pnl, entries

    pos_a, pnl_a, entries_a = _vectors(closed_a)
    pos_b, pnl_b, entries_b = _vectors(closed_b)

    if not any(pos_a) or not any(pos_b):
        click.echo(
            f"refused: rule {rule_a_id} and rule {rule_b_id} never occupy the common bar "
            "index -- nothing to compare"
        )
        return

    report = independence_mod.compare(pos_a, pos_b, pnl_a, pnl_b, entries_a, entries_b)
    for line in _render_independence(rule_a_id, rule_b_id, report):
        click.echo(line)


# -- aliases: the five commands `keel trials`/`keel rules` already front ------------------------
#
# These are the SAME click command objects registered a second time, via `Group.add_command`,
# never a reimplementation. A front door that reimplements is a front door that drifts: two
# copies of `keel ... pbo` would mean two places `cscv.py`'s call signature could be threaded
# differently, two places a bugfix could land in one and not the other, and two places the
# Strathern rail's "reports probabilities, never a configuration" guarantee could be honoured
# in one copy and quietly broken in the other. Registering the object itself makes that
# divergence structurally impossible: there is exactly one implementation, reachable under two
# names. `tests/commands/test_research_front_door.py` pins the object identity.
research_group.add_command(trials_group.commands["pbo"], "pbo")
research_group.add_command(trials_group.commands["deflate"], "deflate")
research_group.add_command(trials_group.commands["monte-carlo"], "monte-carlo")
research_group.add_command(trials_group.commands["walk-forward"], "walk-forward")
research_group.add_command(rules_group.commands["lookahead"], "lookahead")
