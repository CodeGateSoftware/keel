"""The gauntlet, as it was RECORDED -- issue #708, view 3.

**This view computes nothing, and that is the design.** Every component of the gauntlet as #708
scopes it needs work a read-only page cannot do, measured before this module was written:

* **DSR is impossible**, not merely expensive -- `keel trials deflate` takes `--sharpe` as a
  REQUIRED operator input because the ledger stores no observed annualised Sharpe, so a view
  computing it would have to synthesise the input;
* **PBO raises** over the ledger as a whole (`build_matrix`: "columns are not synchronous: found
  lengths [1819, 1828]") and costs 11.9-14.3 s per session where it does run -- ~39 s of CPU
  against a 15 s poll;
* **Monte Carlo and the DCA benchmark both run a backtest**, and `keel serve` is a loopback
  reader over SQLite on every route.

So this reads the outcomes the gauntlet wrote down when an operator ran it. Six of 93 trials
carry gauntlet fields; the other 87 are marked NOT RUN rather than implied to have passed
anything. #726 is the engine issue for the distributional half.

**The exhibit:** every candidate that reached the gauntlet failed it.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from keel.commands.gauntlet import gather_gauntlet

GAUNTLET_NOW_TS = 1_800_000_000


def _trial(**overrides: Any) -> dict[str, Any]:
    from tests.commands.test_research_record import _trial as base

    return base(**overrides)


def _ledger(tmp_path: Path, *rows: dict[str, Any]) -> Path:
    from tests.commands.test_research_record import _ledger as build

    return build(tmp_path, *rows)


def _ran(trial_id: str, **summary: Any) -> dict[str, Any]:
    """A trial whose gauntlet RAN: `pbo_available` is 1 and a `pbo` came back.

    The summary keys are the ledger's own, verbatim from the three real rows
    (`476-optuna-*`): `pbo` as a STRING so it decodes to an exact `Decimal`, everything else as
    the ints the writer stored.
    """
    recorded: dict[str, Any] = {
        "pbo": "0.7",
        "pbo_available": 1,
        "gate_passed": 0,
        "train_expectancy": "-1.337906173823760233469001437",
        "held_out_expectancy": "-1.824534442505212091747494161",
        "seed": 476,
        "bars": 17520,
    }
    recorded.update(summary)
    return _trial(trial_id=trial_id, summary=recorded)


def _refused(trial_id: str) -> dict[str, Any]:
    """A trial whose gauntlet was ATTEMPTED and could not run -- the ledger's own third state."""
    return _trial(trial_id=trial_id, summary={"pbo_available": 0, "gate_passed": 0})


# -- the three states, which are not two -----------------------------------------------------------


def test_a_recorded_run_reports_its_result(tmp_path: Path) -> None:
    path = _ledger(tmp_path, _ran("t-1"))

    (row,) = gather_gauntlet(path, now_ts=GAUNTLET_NOW_TS).rows

    assert row.pbo == Decimal("0.7")
    assert row.available is True
    assert row.gate_passed is False
    assert row.seed == 476
    assert row.bars == 17520


def test_a_trial_the_gauntlet_could_not_run_on_is_not_a_trial_it_never_saw(
    tmp_path: Path,
) -> None:
    """`pbo_available: 0` is the ledger saying the gauntlet was attempted and refused -- every
    column was series-missing, or the grid was too thin for CSCV. That is a DIFFERENT fact from
    a trial with no gauntlet fields at all, and collapsing them would either invent an attempt
    that never happened or hide one that did."""
    path = _ledger(tmp_path, _refused("t-1"))

    (row,) = gather_gauntlet(path, now_ts=GAUNTLET_NOW_TS).rows

    assert row.available is False
    assert row.pbo is None
    # Its PRESENCE is what records the attempt -- a refusal is a gauntlet record, and the row
    # existing at all is the report saying so. A `recorded` property that said the same thing was
    # removed as dead: nothing emitted it, so no payload reader could ever have read it.
    assert row.trial_id == "t-1"


def test_a_trial_with_no_gauntlet_fields_is_not_a_row_here(tmp_path: Path) -> None:
    """NOT RUN. 87 of the ledger's 93 trials are in this state, and listing them as gauntlet rows
    with empty cells would present the gauntlet as having covered the whole record."""
    path = _ledger(tmp_path, _trial(trial_id="t-1"), _ran("t-2"))

    report = gather_gauntlet(path, now_ts=GAUNTLET_NOW_TS)

    assert [row.trial_id for row in report.rows] == ["t-2"]
    assert report.not_run_count == 1
    assert report.trials_total == 2


# -- the exhibit, as counts rather than a sentence -------------------------------------------------


def test_the_counts_separate_ran_from_refused_from_never_attempted(tmp_path: Path) -> None:
    """Three numbers because there are three states. `recorded_count` is the honest denominator
    for `gate_passed_count`: counting passes out of every trial in the ledger would report a
    pass rate for 87 trials the gauntlet never looked at."""
    path = _ledger(
        tmp_path,
        _ran("t-1"),
        _ran("t-2"),
        _refused("t-3"),
        _trial(trial_id="t-4"),
        _trial(trial_id="t-5"),
    )

    report = gather_gauntlet(path, now_ts=GAUNTLET_NOW_TS)

    assert report.trials_total == 5
    assert report.recorded_count == 3
    assert report.available_count == 2
    assert report.not_run_count == 2
    assert report.gate_passed_count == 0


def test_a_passing_gate_is_counted_as_one(tmp_path: Path) -> None:
    """The count must be able to move. Every real row in the ledger today has `gate_passed: 0`,
    and a counter that could only ever report zero would be decoration rather than evidence --
    it would keep saying "none passed" on the day one did."""
    path = _ledger(tmp_path, _ran("t-1", gate_passed=1), _ran("t-2"))

    report = gather_gauntlet(path, now_ts=GAUNTLET_NOW_TS)

    assert report.gate_passed_count == 1
    assert [row.gate_passed for row in report.rows] == [True, False]


# -- what makes a recorded number evidence ---------------------------------------------------------


def test_the_seed_travels_with_the_result(tmp_path: Path) -> None:
    """A resampled statistic without its seed is a number to trust. With it, the run is
    reproducible -- which is the difference between a scorecard and an assertion, and the reason
    the seed is on the row rather than in a footnote."""
    path = _ledger(tmp_path, _ran("t-1", seed=1234))

    (row,) = gather_gauntlet(path, now_ts=GAUNTLET_NOW_TS).rows

    assert row.seed == 1234


def test_both_expectancies_cross_because_the_pair_is_the_finding(tmp_path: Path) -> None:
    """Train and held-out together. Either alone says nothing about overfitting: the gap between
    them IS the measurement, and a page showing only the held-out figure would leave a reader
    unable to see how far it fell."""
    path = _ledger(tmp_path, _ran("t-1", train_expectancy="220.98", held_out_expectancy="0"))

    (row,) = gather_gauntlet(path, now_ts=GAUNTLET_NOW_TS).rows

    assert row.train_expectancy == Decimal("220.98")
    assert row.held_out_expectancy == Decimal("0")


def test_a_recorded_run_missing_a_field_reports_it_absent_never_zero(tmp_path: Path) -> None:
    """An older row that recorded a PBO but no seed must not read as `seed 0` -- which is a
    valid seed, and would make an unreproducible run look reproducible."""
    row_summary = {"pbo": "0.7", "pbo_available": 1, "gate_passed": 0}
    path = _ledger(tmp_path, _trial(trial_id="t-1", summary=row_summary))

    (row,) = gather_gauntlet(path, now_ts=GAUNTLET_NOW_TS).rows

    assert row.seed is None
    assert row.bars is None
    assert row.train_expectancy is None
    assert row.held_out_expectancy is None


# -- the rail --------------------------------------------------------------------------------------


def test_rows_are_never_ordered_by_pbo(tmp_path: Path) -> None:
    """`cscv.py` carries the rail comment in its own source: a score may report, and may gate,
    but may NEVER be a sweep's ranking key. Storing PBO makes it easier to rank by, so the rail
    is restated wherever it is read -- rows come back in ledger order."""
    path = _ledger(tmp_path, _ran("t-worst", pbo="0.95"), _ran("t-best", pbo="0.10"))

    report = gather_gauntlet(path, now_ts=GAUNTLET_NOW_TS)

    assert [row.trial_id for row in report.rows] == ["t-worst", "t-best"]


def test_the_report_names_no_winning_configuration(tmp_path: Path) -> None:
    """`PBOResult` deliberately carries no configuration field -- PBO evaluates the quality of a
    SELECTION PROCESS and must never become the objective selection relies on. Nothing read here
    reintroduces one."""
    path = _ledger(tmp_path, _ran("t-1"))

    report = gather_gauntlet(path, now_ts=GAUNTLET_NOW_TS)
    fields = set(vars(report.rows[0]))

    for banned in ("best", "winner", "rank", "chosen", "recommended"):
        assert not any(banned in name for name in fields), f"a gauntlet row must carry no {banned}"


# -- a missing ledger, same discipline as view 1 ---------------------------------------------------


def test_a_missing_ledger_reports_no_rows_and_says_so(tmp_path: Path) -> None:
    report = gather_gauntlet(tmp_path / "absent.jsonl", now_ts=GAUNTLET_NOW_TS)

    assert report.ledger_present is False
    assert report.rows == ()
    assert report.trials_total == 0


# -- absent is not a verdict ----------------------------------------------------------------------
#
# The hole an independent review found, and it is the rule this whole view is built on.
# `seed`, `bars` and `pbo` were all given the `| None` treatment; `gate_passed` and
# `available` were left as bare `bool`, so a row that recorded no verdict rendered "did not
# pass the promotion gate" -- absent data as a negative judgement about somebody's rule.


def test_a_row_with_no_gate_verdict_does_not_report_a_failed_gate(tmp_path: Path) -> None:
    """`bool(None)` is `False`, and `False` here is a VERDICT: "did not pass the promotion gate".
    A run that recorded a PBO but no gate outcome has not failed anything -- it has not been
    judged, and the page must say so."""
    path = _ledger(tmp_path, _trial(trial_id="t-1", summary={"pbo_available": 1, "pbo": "0.7"}))

    (row,) = gather_gauntlet(path, now_ts=GAUNTLET_NOW_TS).rows

    assert row.gate_passed is None, "no verdict recorded is not a failed verdict"


def test_a_null_availability_is_not_a_definite_refusal(tmp_path: Path) -> None:
    """`pbo_available: null` is a real historical artifact of this ledger --
    `ledger._decode_summary` documents null summary values and passes them through. Reading it as
    `0` asserts a specific mechanical cause ("no usable series, or too thin for CSCV") for a row
    that recorded nothing at all."""
    path = _ledger(tmp_path, _trial(trial_id="t-1", summary={"pbo_available": None}))

    (row,) = gather_gauntlet(path, now_ts=GAUNTLET_NOW_TS).rows

    assert row.available is None


def test_a_recorded_refusal_is_still_a_definite_false(tmp_path: Path) -> None:
    """The other side of it: `pbo_available: 0` IS a recorded refusal and must stay one. Making
    everything optional would lose the distinction the view exists to draw."""
    path = _ledger(tmp_path, _refused("t-1"))

    (row,) = gather_gauntlet(path, now_ts=GAUNTLET_NOW_TS).rows

    assert row.available is False
    assert row.gate_passed is False


def test_the_gate_count_counts_only_recorded_passes(tmp_path: Path) -> None:
    """`None` is not a pass. A three-state field summed with `if row.gate_passed` already does the
    right thing, and this pins it so a later `is not False` does not quietly count unjudged rows."""
    path = _ledger(
        tmp_path,
        _ran("t-1", gate_passed=1),
        _trial(trial_id="t-2", summary={"pbo_available": 1}),
    )

    assert gather_gauntlet(path, now_ts=GAUNTLET_NOW_TS).gate_passed_count == 1


# -- a malformed row must not 500 the page ---------------------------------------------------------


def test_a_boolean_where_a_figure_belongs_degrades_to_absent(tmp_path: Path) -> None:
    """`bool` is an `int` subclass, so `ledger._decode_summary` passes `true` through untouched and
    `Decimal(str(True))` raises `InvalidOperation`. A read-only page must not 500 over one row
    written by something else."""
    path = _ledger(tmp_path, _trial(trial_id="t-1", summary={"pbo_available": 1, "pbo": True}))

    (row,) = gather_gauntlet(path, now_ts=GAUNTLET_NOW_TS).rows

    assert row.pbo is None


def test_a_non_finite_figure_degrades_to_absent(tmp_path: Path) -> None:
    """`int(Decimal("Infinity"))` raises `OverflowError`, which is neither `TypeError` nor
    `ValueError` -- the guard that claimed to catch malformed rows did not catch the one the
    reachable values actually produce."""
    path = _ledger(
        tmp_path,
        _trial(trial_id="t-1", summary={"pbo_available": 1, "seed": "Infinity", "bars": "NaN"}),
    )

    (row,) = gather_gauntlet(path, now_ts=GAUNTLET_NOW_TS).rows

    assert row.seed is None
    assert row.bars is None


def test_a_boolean_seed_is_absent_and_never_one(tmp_path: Path) -> None:
    """`int(True)` is `1`. Without an explicit bool branch a malformed `seed: true` would be
    recorded as seed 1 -- a plausible, wrong value that makes an unreproducible run look
    reproducible, which is worse than the crash the same value causes in the Decimal path."""
    path = _ledger(
        tmp_path,
        _trial(trial_id="t-1", summary={"pbo_available": 1, "seed": True, "bars": False}),
    )

    (row,) = gather_gauntlet(path, now_ts=GAUNTLET_NOW_TS).rows

    assert row.seed is None
    assert row.bars is None


def test_a_boolean_gate_verdict_is_not_read_as_a_verdict(tmp_path: Path) -> None:
    """Same root cause on the flags. The writer stores 0/1; a JSON `true` is off-convention, and
    reading it as "passed the promotion gate" would invent a verdict out of a malformed row."""
    path = _ledger(
        tmp_path,
        _trial(trial_id="t-1", summary={"pbo_available": True, "gate_passed": True}),
    )

    (row,) = gather_gauntlet(path, now_ts=GAUNTLET_NOW_TS).rows

    assert row.gate_passed is None
    assert row.available is None
