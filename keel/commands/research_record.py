"""The research record, as a report -- issue #708.

keel's trials ledger is already append-only, already hash-chained and already public. This module
does not add rigour; it adds the READING of it, so the record reaches an operator's screen instead
of only a file in the repository.

**Why this is a separate module from `keel/commands/research.py`.** That one is the `keel
research` CLI group -- the front door over the thirteen evidence modules, thirteen hundred lines
of click commands. This is a pure report in the shape `positions.py`, `balances.py` and
`timeline.py` already established: no click, no I/O beyond one file read, one `gather_*` function
returning frozen dataclasses for `keel/web/payload.py` to place. `keel trials list` and `keel
trials verify` are the terminal's faces on the same file; this is the browser's.

**Why the view is worth building at all.** No broker, SaaS platform or open-source competitor
publishes its rejected trials, because their business models depend on trading looking easy.
keel's does not. The rejected rows sitting in the same table as the selected one are the evidence
that the selected one was not cherry-picked out of a hundred attempts -- which is a claim every
backtest makes and almost none can support.

**THE STRATHERN RAIL, IN THE SERVICE.** Nothing here orders trials by performance. Rows come back
in LEDGER ORDER -- the order they were run -- and the view groups by rule family. A research
record that can be sorted best-first is a leaderboard, and a leaderboard turns a record of what
was tried into an argument for what to trade, which is the exact reversal the ledger exists to
prevent. The rail is enforced here rather than only in the renderer, because a service that
offered a `sort_by_profit_factor` would eventually be called by something.
`tests/commands/test_research_record.py` scans this file for the idiomatic ways to write one,
mirroring the scan `test_research_front_door.py` runs over the CLI group.

**Read-only, no broker, no network, and no database either** -- this is the one report in
`keel/commands/` whose source is a file rather than SQLite.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from keel.research.ledger import read_trials, trial_counts, verify_chain


@dataclass(frozen=True)
class TrialRow:
    """One trial, projected onto what a reader needs.

    `summary` crosses as the ledger recorded it -- a mapping of already-decoded figures -- rather
    than being unpacked into named fields here. The ledger's summary shape is the driver's, it
    varies by trial kind, and inventing a fixed schema over it in a read path would misreport the
    trials whose shape does not match.
    """

    trial_id: str
    timestamp: int
    session: str
    rule: str
    kind: str
    decision: str
    provenance: str
    params: dict[str, Any]
    summary: dict[str, Any]
    row_hash: str
    #: True when the trial recorded no per-trade or per-bar series. Carried because the gauntlet
    #: machinery (`cscv`, `deflate`) REFUSES a series-missing trial, so a reader comparing two
    #: trials needs to know which of them the statistics could even be computed for.
    series_missing: bool


@dataclass(frozen=True)
class RuleExploration:
    """How much of a rule's declared space this ledger has trials for.

    Two numbers, deliberately not a ratio and deliberately not a verdict. `research.tuning`'s
    `explored_vs_declared` is the thing that judges a sweep -- and it RAISES when an explored
    range exceeds its declaration, because it is a driver's refusal gate. Calling it from a
    read-only page would 500 on exactly the drift it exists to flag, so this reports what it can
    state plainly and leaves the judgement where it belongs.

    Pricing the swept BOX against the declaration is also more than a read: it needs the box in
    the declaration's own dimension names, and `pullback_continuation`'s declaration uses fan
    slots (`ema_fast`/`ema_mid`/`ema_slow`) while its params carry the packed `ema_periods`. That
    assembly is its own piece of work.
    """

    rule: str
    #: Trials for this rule IN THIS LEDGER -- not cells explored, which is a different count
    #: (`explored_cells` prices a box at declared steps and never enumerates what was visited).
    trials: int
    #: The rule's own declaration, from `research.tuning.declared_cells`. `0` when the rule
    #: declares nothing this module can read -- never a guess.
    declared_cells: int


@dataclass(frozen=True)
class TrialsReport:
    now_ts: int

    #: Whether the ledger FILE was there to read. `DEFAULT_LEDGER_PATH` is repo-relative
    #: (`docs/experiments/trials-ledger.jsonl`), and `keel serve` runs from a deployment
    #: directory where it will not exist -- a deployment without the research repo beside it,
    #: which is an ordinary state and not a broken one.
    ledger_present: bool

    rows: tuple[TrialRow, ...]

    #: `trial_counts`' (M, N): trials run, and trials that produced a DECISION. Two numbers
    #: because they answer different questions -- M is the multiple-comparisons denominator DSR
    #: corrects against, and a single "trials" figure would hide it.
    trials_run: int
    decisions: int

    #: `verify_chain`'s verdict and its findings. `False` with no errors means nothing was
    #: verified (no ledger), which is NOT the same as verified-and-intact -- so the two are
    #: reported together and a reader is never shown a green badge for an absent file.
    chain_intact: bool
    chain_errors: tuple[str, ...]

    exploration: tuple[RuleExploration, ...]

    @property
    def rules(self) -> tuple[str, ...]:
        """Every rule family in the ledger, in first-seen order.

        What the view groups on INSTEAD of ranking. First-seen rather than sorted, so the page
        does not reorder itself as trials arrive -- and never by any performance figure, which is
        the rail this module exists under.
        """
        seen: list[str] = []
        for row in self.rows:
            if row.rule not in seen:
                seen.append(row.rule)
        return tuple(seen)

    @property
    def shown_count(self) -> int:
        """How many trials this report carries. Derived, and held here because
        `keel/web/payload.py` may not call `len()` (Rule 6e)."""
        return len(self.rows)


def _declared_for(rule: str) -> int:
    """The rule's declared cell count, or `0` when it declares nothing readable.

    `declared_cells` raises for a rule kind it does not know -- a ledger can hold trials for a
    rule that has since been renamed or removed, and a research record must still render when
    its subject is gone.
    """
    from keel.research.tuning import declared_cells

    try:
        return int(declared_cells(rule))
    except Exception:
        return 0


def gather_trials(path: Path | str, *, now_ts: int) -> TrialsReport:
    """The trials ledger at `path`, read and verified.

    The chain is verified on every read rather than cached: the whole value of the badge is that
    it reflects the file as it is right now, and a cached verdict would go on saying "intact"
    about a file someone had since edited.
    """
    ledger = Path(path)
    if not ledger.exists():
        return TrialsReport(
            now_ts=now_ts,
            ledger_present=False,
            rows=(),
            trials_run=0,
            decisions=0,
            # NOT intact: nothing was checked. A green badge over an absent file would be the
            # page asserting a verification that never happened.
            chain_intact=False,
            chain_errors=(),
            exploration=(),
        )

    trials = list(read_trials(ledger))
    errors = tuple(verify_chain(ledger))
    ran, decided = trial_counts(trials)

    rows = tuple(
        TrialRow(
            trial_id=trial.trial_id,
            timestamp=trial.timestamp,
            session=trial.session,
            rule=trial.rule,
            kind=trial.kind,
            decision=trial.decision,
            provenance=trial.provenance,
            params=dict(trial.params),
            summary=dict(trial.summary),
            row_hash=trial.row_hash,
            series_missing=trial.series_missing,
        )
        for trial in trials
    )

    counts: dict[str, int] = {}
    for row in rows:
        counts[row.rule] = counts.get(row.rule, 0) + 1
    exploration = tuple(
        RuleExploration(rule=rule, trials=count, declared_cells=_declared_for(rule))
        for rule, count in counts.items()
    )

    return TrialsReport(
        now_ts=now_ts,
        ledger_present=True,
        rows=rows,
        trials_run=ran,
        decisions=decided,
        chain_intact=not errors,
        chain_errors=errors,
        exploration=exploration,
    )
