"""The Evidence Matrix reads recorded CSCV runs; it never computes one (#708 view 2).

Building the matrix on request costs 11.9 / 12.9 / 14.3 seconds per session on the real ledger --
~39 s of CPU for three sessions, on a page the console re-polls every 15 seconds -- and over the
ledger as a whole it raises, because `build_matrix` requires synchronous columns and a PBO is only
defined within one session. So #726 made `trials pbo` record its whole `PBOResult`, and this reads
it.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from keel.commands.evidence_matrix import gather_matrix
from keel.research import ledger

NOW = 1_800_000_000


def _cscv(path: Path, *, session: str = "s1", trial_id: str = "cscv-s1-s4", **summary):
    fields = {
        "pbo": Decimal("0.8810"),
        "degradation_slope": Decimal("-0.42"),
        "degradation_intercept": Decimal("0.05"),
        "prob_loss": Decimal("0.71"),
        "dominance_1st": False,
        "dominance_2nd": True,
        "n_columns": 12,
        "n_blocks": 16,
        "n_combinations": 12870,
        "rows_used": 1819,
        "rows_dropped": 9,
        "columns_refused": 2,
    }
    fields.update(summary)
    return ledger.append_trial(
        path,
        trial_id=trial_id,
        session=session,
        rule="(cscv over the recorded columns)",
        provenance="a_priori",
        kind="cscv",
        decision="diagnostic_only",
        series_missing=True,
        summary=fields,
    )


def _column(path: Path, *, session: str = "s1", trial_id: str = "col-1"):
    return ledger.append_trial(
        path,
        trial_id=trial_id,
        session=session,
        rule="turtle_breakout",
        params={"n": 20},
        provenance="fitted",
        kind="sweep_node",
        decision="rejected",
        per_bar_pnl=[Decimal("1"), Decimal("-1")],
    )


# -- what it reads ---------------------------------------------------------------------------------


def test_every_recorded_field_reaches_the_row(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    _cscv(path)
    (row,) = gather_matrix(path, now_ts=NOW).rows

    assert row.pbo == Decimal("0.8810")
    assert row.degradation_slope == Decimal("-0.42")
    assert row.prob_loss == Decimal("0.71")
    assert row.dominance_1st is False
    assert row.dominance_2nd is True
    assert (row.n_columns, row.n_blocks, row.n_combinations) == (12, 16, 12870)
    assert (row.rows_used, row.rows_dropped, row.columns_refused) == (1819, 9, 2)


def test_a_dominance_flag_is_three_valued(tmp_path: Path) -> None:
    """`bool(None)` is `False`, and `False` here is a positive claim -- "the in-sample
    distribution did not dominate" -- which is not what an absent field says."""
    path = tmp_path / "ledger.jsonl"
    _cscv(path, dominance_1st=None, dominance_2nd=None)
    (row,) = gather_matrix(path, now_ts=NOW).rows

    assert row.dominance_1st is None
    assert row.dominance_2nd is None


def test_a_count_reader_never_returns_a_bool(tmp_path: Path) -> None:
    """`bool` is a subclass of `int` in Python, so an unguarded `isinstance(value, int)` reads
    `true` as the number 1.

    Tested at the READER rather than through a fixture: no writer stores a bool under a count key
    today, so a round-trip test would pass with or without the guard and would be asserting
    nothing. This is a defensive check and the honest way to pin one is directly.
    """
    from keel.commands.evidence_matrix import _int_or_none

    assert _int_or_none({"n_columns": True}, "n_columns") is None
    assert _int_or_none({"n_columns": False}, "n_columns") is None
    assert _int_or_none({"n_columns": 12}, "n_columns") == 12
    assert _int_or_none({"n_columns": None}, "n_columns") is None


def test_a_dominance_flag_survives_beside_an_absent_count(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    _cscv(path, n_columns=None, dominance_1st=True)
    (row,) = gather_matrix(path, now_ts=NOW).rows

    assert row.n_columns is None
    assert row.dominance_1st is True


def test_an_absent_figure_is_none_and_never_zero(tmp_path: Path) -> None:
    """A `pbo` of 0 is the strongest possible statement about a selection process; a missing one
    is no statement at all."""
    path = tmp_path / "ledger.jsonl"
    _cscv(path, pbo=None, prob_loss=None)
    (row,) = gather_matrix(path, now_ts=NOW).rows

    assert row.pbo is None
    assert row.prob_loss is None


def test_the_six_pre_726_gauntlet_rows_are_not_read_as_matrices(tmp_path: Path) -> None:
    """They carry a `pbo` and are NOT CSCV runs -- they are per-trial gauntlet outcomes, which
    #708's view 3 shows. Selecting on the presence of a `pbo` key would put two different
    measurements in one table under one heading."""
    path = tmp_path / "ledger.jsonl"
    ledger.append_trial(
        path,
        trial_id="476-optuna-turtle_breakout",
        session="optuna",
        rule="turtle_breakout",
        provenance="fitted",
        kind="sweep_node",
        decision="rejected",
        series_missing=True,
        summary={"pbo": Decimal("0.7"), "pbo_available": 1, "gate_passed": 0},
    )
    report = gather_matrix(path, now_ts=NOW)

    assert report.rows == ()
    assert report.ledger_present is True


def test_rows_read_in_ledger_order(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    _cscv(path, trial_id="first")
    _cscv(path, trial_id="second")
    assert [row.trial_id for row in gather_matrix(path, now_ts=NOW).rows] == ["first", "second"]


# -- the three empty states ------------------------------------------------------------------------


def test_a_missing_ledger_is_distinct_from_an_empty_one(tmp_path: Path) -> None:
    report = gather_matrix(tmp_path / "absent.jsonl", now_ts=NOW)
    assert report.ledger_present is False
    assert report.any_recorded is False
    assert report.candidate_sessions == ()


def test_a_ledger_with_columns_and_no_run_names_a_session_that_would_work(tmp_path: Path) -> None:
    """The guidance has to name a session that ACTUALLY HAS COLUMNS.

    `keel trials pbo --session all` is the obvious thing to suggest and would filter to a session
    literally named "all", find nothing, and print a refusal -- teaching an operator that the page
    does not know what it is talking about.
    """
    path = tmp_path / "ledger.jsonl"
    _column(path, session="sweep-a", trial_id="a1")
    _column(path, session="sweep-a", trial_id="a2")

    report = gather_matrix(path, now_ts=NOW)
    assert report.any_recorded is False
    assert report.ledger_present is True
    assert report.suggested_session == "sweep-a"


def test_a_session_of_backfilled_rows_is_not_suggested(tmp_path: Path) -> None:
    """`build_matrix` refuses `series_missing` trials, so suggesting that session would hand the
    operator a command that refuses."""
    path = tmp_path / "ledger.jsonl"
    for index in range(4):
        ledger.append_trial(
            path,
            trial_id=f"backfilled-{index}",
            session="historic",
            rule="turtle_breakout",
            provenance="fitted",
            kind="sweep_node",
            decision="rejected",
            series_missing=True,
        )

    report = gather_matrix(path, now_ts=NOW)
    assert report.candidate_sessions == ()
    assert report.suggested_session == ""


def test_a_session_with_one_column_is_not_suggested(tmp_path: Path) -> None:
    """One column is no combinatorial split. `MIN_COLUMNS_FOR_A_RUN` is the floor at which the
    question is even well-formed."""
    path = tmp_path / "ledger.jsonl"
    _column(path, session="lonely", trial_id="only-one")

    assert gather_matrix(path, now_ts=NOW).candidate_sessions == ()


def test_reading_the_matrix_never_builds_one(tmp_path: Path, monkeypatch) -> None:
    """The property this whole module exists for. `build_matrix` is where the 12 seconds live,
    and a page that polls every 15 of them must not call it."""
    from keel.research import matrix as matrix_mod

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("gather_matrix called build_matrix -- it must only READ")

    monkeypatch.setattr(matrix_mod, "build_matrix", _boom)

    path = tmp_path / "ledger.jsonl"
    _column(path, trial_id="c1")
    _column(path, trial_id="c2")
    _cscv(path)

    report = gather_matrix(path, now_ts=NOW)
    assert report.recorded_count == 1


def test_it_reads_the_real_tracked_ledger_and_finds_no_run_yet(tmp_path: Path) -> None:
    """The state this ships in, stated rather than assumed: nobody has run `trials pbo` since
    #726 taught it to record, so the page shows its guidance -- naming one of the three sessions
    that genuinely hold columns."""
    tracked = Path(__file__).resolve().parents[2] / "docs/experiments/trials-ledger.jsonl"
    report = gather_matrix(tracked, now_ts=NOW)

    assert report.ledger_present is True
    assert report.any_recorded is False
    assert report.suggested_session in report.candidate_sessions
    assert report.candidate_sessions, "the tracked ledger holds sessions a run could work on"
