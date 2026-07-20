"""Matrix assembly for CSCV (spec §5.4)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from keel.research import ledger, matrix


def _trial(trial_id, bars, series_missing=False, session="s1"):
    return ledger.TrialRecord(
        trial_id=trial_id,
        timestamp=0,
        session=session,
        rule="turtle_breakout",
        params={},
        provenance="fitted",
        kind="sweep_node",
        decision="diagnostic_only",
        per_trade_pnl=[],
        per_bar_pnl=[Decimal(str(b)) for b in bars],
        series_missing=series_missing,
        summary={},
    )


def test_builds_columns_from_trials():
    build = matrix.build_matrix([_trial("a", [1, 2, 3]), _trial("b", [3, 2, 1])])
    assert build.columns == [
        [Decimal(1), Decimal(2), Decimal(3)],
        [Decimal(3), Decimal(2), Decimal(1)],
    ]
    assert build.trial_ids == ["a", "b"]
    assert build.refused == []


def test_refuses_series_missing_rows():
    build = matrix.build_matrix(
        [_trial("a", [1, 2, 3]), _trial("backfilled", [], series_missing=True)]
    )
    assert build.trial_ids == ["a"]
    assert build.refused == ["backfilled"]


def test_requires_synchronous_rows():
    with pytest.raises(ValueError, match="synchronous"):
        matrix.build_matrix([_trial("a", [1, 2, 3]), _trial("b", [1, 2])])


def test_filters_by_session():
    build = matrix.build_matrix(
        [_trial("a", [1, 2], session="x"), _trial("b", [3, 4], session="y")], session="y"
    )
    assert build.trial_ids == ["b"]


def test_warns_below_ten_columns():
    build = matrix.build_matrix([_trial("a", [1, 2]), _trial("b", [2, 1])])
    assert build.warnings
    assert "N=2" in build.warnings[0]
