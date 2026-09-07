"""The Evidence Matrix: every recorded CSCV run, read rather than computed (#708 view 2).

── WHY THIS IS A READ AND NOT A COMPUTATION ─────────────────────────────────────────────────────

The obvious implementation is to build the matrix on request. Measured on the real ledger, that
costs 11.9 / 12.9 / 14.3 seconds per session -- roughly 39 seconds of CPU for three sessions, on a
page the console re-polls every 15 seconds. And over the ledger as a WHOLE it does not run at all:

    ValueError: columns are not synchronous: found lengths [1819, 1828];
    §78.6 requires a true matrix with the same rows for every column

`matrix.build_matrix` requires synchronous columns, so a PBO is only ever defined WITHIN a session
whose trials share a bar count. A page cannot pick that scope for the operator without inventing
their decision.

So #726 made `trials pbo` record every field of its `PBOResult`, and this reads them. The
distinction is the whole design: **the console displays results an operator ran, and never runs
one on their behalf.**

── AN UNRUN MATRIX IS NOT AN EMPTY ONE ──────────────────────────────────────────────────────────

Three states, and the middle one is the reason this module has a `candidate_sessions` field at
all:

* **no ledger** -- a deployment without the research repository beside it.
* **a ledger with columns and no recorded run** -- the gauntlet has simply not been run here yet,
  and the page can name the exact command that would change that.
* **a ledger with recorded runs** -- the matrix.

The guidance names a session that ACTUALLY HAS COLUMNS. `keel trials pbo --session all` looks like
the obvious thing to suggest and would filter to a session literally named "all", find nothing, and
print a refusal -- teaching an operator that the page does not know what it is talking about.

⛔ THE STRATHERN RAIL. Every figure here is a diagnostic and none of them is sortable, on the route
or in the view. A matrix ordered by PBO is a leaderboard of overfitting scores, and PBO's own
module carries the warning: it "evaluates the quality of a selection process and must never become
the objective that selection relies on".
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from keel.research.ledger import read_trials

#: The kind #726 writes a recorded CSCV run under.
CSCV_KIND = "cscv"

#: The command that would populate this page. Composed in Python and placed by the client (the
#: rule #707's cancel modal follows), and it names a REAL session: `--session all` filters to a
#: session literally called "all", finds nothing, and prints a refusal.
MATRIX_INVOCATION = "keel trials pbo --session {session}"

#: How many synchronous columns a session needs before `trials pbo` can say anything about it.
#: `matrix.build_matrix` warns below `MIN_RECOMMENDED_COLUMNS` and refuses at zero; two is the
#: floor at which a combinatorial split exists at all.
MIN_COLUMNS_FOR_A_RUN = 2


@dataclass(frozen=True)
class MatrixRow:
    """One recorded CSCV run -- every field `PBOResult` carries, as it was recorded.

    Absent figures are `None`, never zero. A `pbo` of `0` is the strongest possible statement
    about a selection process and a missing one is no statement at all; the six pre-#726 gauntlet
    rows carry neither, and this page says so rather than rendering them as perfect.
    """

    trial_id: str
    timestamp: int
    session: str
    pbo: Decimal | None
    degradation_slope: Decimal | None
    degradation_intercept: Decimal | None
    prob_loss: Decimal | None
    dominance_1st: bool | None
    dominance_2nd: bool | None
    n_columns: int | None
    n_blocks: int | None
    n_combinations: int | None
    rows_used: int | None
    rows_dropped: int | None
    columns_refused: int | None


@dataclass(frozen=True)
class MatrixReport:
    now_ts: int
    ledger_present: bool
    rows: tuple[MatrixRow, ...]
    #: Sessions whose trials could form a matrix, whether or not one has been run over them.
    #: What the empty state names, so its command is one that would actually work.
    candidate_sessions: tuple[str, ...]

    @property
    def recorded_count(self) -> int:
        """Held on the report because `keel/web/payload.py` may not call `len()` (Rule 6e)."""
        return len(self.rows)

    @property
    def any_recorded(self) -> bool:
        return bool(self.rows)

    @property
    def suggested_session(self) -> str:
        """The session the empty state tells an operator to run against, or `""` when none could.

        The FIRST candidate rather than a chosen one: choosing would be this page ranking sessions
        by something, and there is nothing here it may rank by.
        """
        return self.candidate_sessions[0] if self.candidate_sessions else ""


def _decimal_or_none(summary: dict[str, Any], key: str) -> Decimal | None:
    value = summary.get(key)
    return value if isinstance(value, Decimal) else None


def _int_or_none(summary: dict[str, Any], key: str) -> int | None:
    value = summary.get(key)
    # `bool` is an `int` in Python and is never one of these counts. Checked first, because
    # `isinstance(True, int)` would otherwise render a dominance flag as a column count.
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


def _flag_or_none(summary: dict[str, Any], key: str) -> bool | None:
    """THREE-VALUED. `bool(None)` is `False`, and `False` on a dominance flag is a positive claim
    -- "the in-sample distribution did not dominate" -- which is not what an absent field says."""
    value = summary.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    return None


def _candidate_sessions(trials: list[Any]) -> tuple[str, ...]:
    """Sessions holding enough usable columns for `trials pbo` to run over them.

    Counts the trials `matrix.build_matrix` would ACCEPT -- a per-bar series, not `series_missing`
    -- rather than every trial with the session label, because a session of six backfilled rows
    would otherwise be suggested and the suggested command would refuse.

    It does NOT check synchronicity. Doing so means reading every series, which is most of the
    cost this module exists to avoid, and a session whose columns turn out to be ragged gets a
    clear refusal from the command itself. Suggesting a session that might not work is a much
    smaller harm than a page that costs 12 seconds to render.
    """
    usable: dict[str, int] = {}
    for trial in trials:
        if trial.series_missing or not trial.per_bar_pnl:
            continue
        usable[trial.session] = usable.get(trial.session, 0) + 1
    return tuple(
        session for session, count in usable.items() if count >= MIN_COLUMNS_FOR_A_RUN
    )


def gather_matrix(path: Path | str, *, now_ts: int) -> MatrixReport:
    """Every recorded CSCV run in the ledger at `path`, oldest first. No computation."""
    ledger = Path(path)
    if not ledger.exists():
        return MatrixReport(
            now_ts=now_ts, ledger_present=False, rows=(), candidate_sessions=()
        )

    trials = list(read_trials(ledger))
    rows = tuple(
        MatrixRow(
            trial_id=trial.trial_id,
            timestamp=trial.timestamp,
            session=trial.session,
            pbo=_decimal_or_none(trial.summary, "pbo"),
            degradation_slope=_decimal_or_none(trial.summary, "degradation_slope"),
            degradation_intercept=_decimal_or_none(trial.summary, "degradation_intercept"),
            prob_loss=_decimal_or_none(trial.summary, "prob_loss"),
            dominance_1st=_flag_or_none(trial.summary, "dominance_1st"),
            dominance_2nd=_flag_or_none(trial.summary, "dominance_2nd"),
            n_columns=_int_or_none(trial.summary, "n_columns"),
            n_blocks=_int_or_none(trial.summary, "n_blocks"),
            n_combinations=_int_or_none(trial.summary, "n_combinations"),
            rows_used=_int_or_none(trial.summary, "rows_used"),
            rows_dropped=_int_or_none(trial.summary, "rows_dropped"),
            columns_refused=_int_or_none(trial.summary, "columns_refused"),
        )
        for trial in trials
        # The KIND, not the presence of a `pbo` key: the six pre-#726 gauntlet rows carry a `pbo`
        # and are not CSCV runs -- they are per-trial gauntlet outcomes, which #708's view 3 shows.
        # Reading them here would put two different measurements in one table under one heading.
        if trial.kind == CSCV_KIND
    )
    return MatrixReport(
        now_ts=now_ts,
        ledger_present=True,
        rows=rows,
        candidate_sessions=_candidate_sessions(trials),
    )
