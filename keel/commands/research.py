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
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

import click

from keel.commands.rules import rules_group
from keel.commands.trials import trials_group

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
        runs_as="docs/experiments/2026-08-21-rule-family-significance.py",
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
        runs_as="docs/experiments/2026-08-08-between-family-independence.py",
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
        runs_as="docs/experiments/2026-09-30-pooled-review.py",
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
        runs_as="docs/experiments/2026-08-09-cts-factor-collinearity.py",
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
        runs_as="docs/experiments/2026-09-30-pooled-review.py",
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
        runs_as="docs/experiments/2026-08-22-optuna-parameter-study.py",
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
