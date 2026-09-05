"""The gauntlet, as it was RECORDED -- issue #708, view 3.

**This module computes nothing. It reads what an operator's gauntlet run wrote down.** That is
not a shortcut; every component of view 3 as #708 scopes it needs work a read-only page cannot
do, and all three were measured before this file existed:

* **DSR is impossible, not merely expensive.** `keel trials deflate` takes `--sharpe` as a
  REQUIRED operator input, because the ledger stores no observed annualised Sharpe per trial. A
  view computing DSR would have to synthesise that input, which is the one thing this codebase
  refuses to do anywhere.
* **PBO raises over the ledger and costs 12-14 s per session where it runs.**
  `matrix.build_matrix` over all 93 rows fails with "columns are not synchronous: found lengths
  [1819, 1828]" -- §78.6 requires a true matrix -- so PBO is defined only WITHIN a session whose
  trials share a bar count, and a page cannot pick that scope without inventing an operator's
  decision. Measured per session: 11.9 s, 12.9 s, 14.3 s. `main.js` polls every 15 s.
* **Monte Carlo and the DCA benchmark both run a backtest.** `keel serve` is a loopback reader
  over SQLite on every route, and it stays one.

#726 is the engine issue for the half that is missing: persist the Monte Carlo quantiles, the DSR
inputs and score, and the full `PBOResult` at the moment the gauntlet runs, instead of printing
them and discarding them. Until then this reads the scalars that do survive.

**THREE STATES, NOT TWO** -- the same discipline as view 1's chain badge:

1. **ran** (`pbo_available: 1`) -- a PBO came back, with the seed that makes it reproducible;
2. **refused** (`pbo_available: 0`) -- the gauntlet was ATTEMPTED and could not run, because every
   column was series-missing or the grid was too thin for CSCV;
3. **not run** -- no gauntlet fields at all. 87 of 93 trials, and they are counted rather than
   listed: a table of empty rows would present the gauntlet as having covered the whole record.

**⛔ THE STRATHERN RAIL.** `cscv.py` carries it in its own source -- a score may report, and may
gate, but may NEVER be a sweep's ranking key. Storing PBO makes it easier to rank by, so the rail
is restated here: rows come back in LEDGER ORDER, and nothing in this module names a winning
configuration. `PBOResult` deliberately carries no configuration field; neither does `GauntletRow`.

**The exhibit:** every candidate that reached the gauntlet failed it.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from keel.research.ledger import read_trials

#: The ledger key whose PRESENCE means the gauntlet was attempted on this trial. Presence, not
#: truth: `0` records a refusal and is as much a gauntlet record as `1` is.
AVAILABILITY_KEY = "pbo_available"


@dataclass(frozen=True)
class GauntletRow:
    """One trial's recorded gauntlet outcome.

    **Every field is `None` where the ledger recorded nothing.** Not zero, and -- the part a first
    cut of this class got wrong -- not `False` either. `seed=0` is a VALID seed, so a row that
    recorded a PBO without one must not read as reproducible; and `gate_passed=False` is a
    VERDICT ("did not pass the promotion gate"), so a row that recorded no gate outcome must not
    read as having failed one. Absent data rendered as a negative judgement about somebody's rule
    is the precise failure this view exists to refuse, and `bool(None)` is how it gets in.
    """

    trial_id: str
    timestamp: int
    session: str
    rule: str
    decision: str

    #: Whether the gauntlet could actually run -- the ledger's `pbo_available`, as a bool.
    #: Whether the gauntlet could actually run -- the ledger's `pbo_available`.
    #:
    #: THREE-VALUED. `True` ran, `False` is a RECORDED refusal (every column series-missing, or
    #: the grid too thin for CSCV), `None` is `pbo_available: null` -- which `ledger._decode_
    #: summary` documents as a real historical artifact of this file. Reading null as `False`
    #: would assert that specific mechanical cause for a row that recorded nothing.
    available: bool | None

    #: `None` when it could not run. Stored in the ledger as a STRING, so it decodes to an exact
    #: `Decimal` and crosses the wire under Rule 1 without ever having been a float.
    pbo: Decimal | None

    #: Whether the promotion gate passed, or `None` where no verdict was recorded. Every row in
    #: the ledger today that HAS a verdict says no.
    gate_passed: bool | None

    #: The pair, together, because the pair is the finding: either figure alone says nothing
    #: about overfitting, and the GAP between them is the measurement.
    train_expectancy: Decimal | None
    held_out_expectancy: Decimal | None

    #: What makes a resampled statistic evidence rather than an assertion. Without it the number
    #: is one nobody can reproduce; with it the run can be repeated and checked.
    seed: int | None

    #: Bars the run covered. Carried for the same reason `SlippageRow.bars` is: a result over
    #: 17,520 bars and one over 200 are not the same evidence.
    bars: int | None


@dataclass(frozen=True)
class GauntletReport:
    now_ts: int

    ledger_present: bool

    #: Only the trials that carry gauntlet fields, in LEDGER ORDER.
    rows: tuple[GauntletRow, ...]

    #: Every trial in the ledger, gauntlet or not -- the denominator `not_run_count` is out of.
    trials_total: int

    @property
    def recorded_count(self) -> int:
        """Trials the gauntlet was attempted on, refusals included."""
        return len(self.rows)

    @property
    def available_count(self) -> int:
        """Of those, the ones it could actually run on."""
        return sum(1 for row in self.rows if row.available)

    @property
    def not_run_count(self) -> int:
        """Trials with no gauntlet record at all. Reported rather than listed, and reported
        rather than omitted: a scorecard that showed six rows and no denominator would read as
        though six trials were the whole record."""
        return self.trials_total - self.recorded_count

    @property
    def gate_passed_count(self) -> int:
        """How many recorded runs passed the promotion gate.

        `recorded_count` is the honest denominator for this, never `trials_total`: a pass rate
        out of every trial in the ledger would be a rate for 87 trials the gauntlet never
        looked at.
        """
        return sum(1 for row in self.rows if row.gate_passed)


def _decimal_or_none(summary: dict[str, Any], key: str) -> Decimal | None:
    """A summary figure as `Decimal`, or `None` for anything that is not one.

    `read_trials` has already decoded these: a string becomes `Decimal`, an `int` stays an `int`,
    `None` passes through. The `int` branch is why this cannot just annotate the type -- a value
    the writer stored as a whole number arrives as `int` and would reach a money field as one.

    **The `except` is the fix, and it is not optional.** `bool` is an `int` subclass, so
    `_decode_summary` passes `true` through untouched and `Decimal(str(True))` is
    `Decimal("True")` -- an `InvalidOperation` that reached the request handler uncaught and 500'd
    the page. `InvalidOperation` is an `ArithmeticError`, so the clause below catches it along
    with every other malformed figure.

    An explicit `isinstance(value, bool)` branch was written here first and removed: it produced
    the same answer as the `except` for every input, so no mutation could distinguish them, and a
    guard nothing can prove is a guard nobody should trust. `_int_or_none` keeps its bool branch
    because there it CHANGES the answer -- `int(True)` is `1`, a silently wrong seed rather than
    an exception.
    """
    value = summary.get(key)
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError):
        return None


def _int_or_none(summary: dict[str, Any], key: str) -> int | None:
    """A summary figure as `int`, or `None` for anything that is not one.

    `ArithmeticError` is in the catch and `ValueError` alone was not enough: the values that
    reach here have already been through `_decode_summary`, so a non-numeric string died there
    and what actually arrives is a `Decimal` -- and `int(Decimal("Infinity"))` raises
    `OverflowError`, which is an `ArithmeticError` and neither a `TypeError` nor a `ValueError`.
    The guard was catching exceptions the reachable values do not raise while missing the one
    they do.
    """
    value = summary.get(key)
    # `bool` FIRST, and here it is load-bearing rather than defensive: `bool` is an `int`
    # subclass and `int(True)` is `1`, so a `seed: true` in a malformed row would be recorded as
    # seed 1 -- a plausible, wrong, reproducible-looking value. Absent is the honest answer.
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (ArithmeticError, TypeError, ValueError):
        # A read-only page must not 500 over one malformed row written by something else.
        return None


def _flag_or_none(summary: dict[str, Any], key: str) -> bool | None:
    """A recorded 0/1 as a bool, or `None` where nothing was recorded.

    The whole point of this function is that it does NOT go through `bool()`: `bool(None)` is
    `False`, and `False` on either of this module's two flags is a positive claim -- "the
    gauntlet could not run", "the gate did not pass". Absent is a third answer.
    """
    recorded = _int_or_none(summary, key)
    if recorded is None:
        return None
    return bool(recorded)


def gather_gauntlet(path: Path | str, *, now_ts: int) -> GauntletReport:
    """Every recorded gauntlet outcome in the ledger at `path`, in the order it was run."""
    ledger = Path(path)
    if not ledger.exists():
        return GauntletReport(now_ts=now_ts, ledger_present=False, rows=(), trials_total=0)

    trials = list(read_trials(ledger))
    rows = tuple(
        GauntletRow(
            trial_id=trial.trial_id,
            timestamp=trial.timestamp,
            session=trial.session,
            rule=trial.rule,
            decision=trial.decision,
            # NOT `bool(...)`: `bool(None)` is `False`, and `False` here says "the gauntlet
            # could not run", which is a claim about the machinery rather than an absence of
            # one. `pbo_available: null` is a real artifact of this ledger and reads as None.
            available=_flag_or_none(trial.summary, AVAILABILITY_KEY),
            pbo=_decimal_or_none(trial.summary, "pbo"),
            gate_passed=_flag_or_none(trial.summary, "gate_passed"),
            train_expectancy=_decimal_or_none(trial.summary, "train_expectancy"),
            held_out_expectancy=_decimal_or_none(trial.summary, "held_out_expectancy"),
            seed=_int_or_none(trial.summary, "seed"),
            bars=_int_or_none(trial.summary, "bars"),
        )
        for trial in trials
        # PRESENCE of the availability key, not its truth: `pbo_available: 0` is the ledger
        # saying the gauntlet was attempted and refused, which is a record worth showing.
        if AVAILABILITY_KEY in trial.summary
    )
    return GauntletReport(
        now_ts=now_ts,
        ledger_present=True,
        rows=rows,
        trials_total=len(trials),
    )
