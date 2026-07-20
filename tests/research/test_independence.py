"""§80.16 independence measurements."""

from __future__ import annotations

from decimal import Decimal

import pytest

from keel.research.independence import compare, entry_distances, jaccard


def _pnl(positions):
    """A P&L series that is non-zero exactly where the rule is in the market."""
    return [Decimal(v) for v in positions]


def test_identical_streams_are_maximally_dependent():
    positions = [0, 1, 1, 0, 1, 1, 0, 0]
    report = compare(positions, positions, _pnl(positions), _pnl(positions))
    assert report.jaccard == Decimal(1)
    # Decimal.sqrt() is correctly rounded, not exact, so a perfect correlation lands a few
    # ulps from 1. Assert closeness rather than identity -- the arithmetic is right.
    assert report.position_correlation == pytest.approx(Decimal(1), abs=Decimal("1e-20"))
    assert report.pnl_correlation == pytest.approx(Decimal(1), abs=Decimal("1e-20"))
    assert report.median_entry_distance == 0


def test_disjoint_streams_have_zero_overlap():
    a = [1, 1, 0, 0, 0, 0]
    b = [0, 0, 0, 1, 1, 0]
    report = compare(a, b, _pnl(a), _pnl(b))
    assert report.jaccard == Decimal(0)
    assert report.both_active == 0
    assert report.position_correlation < Decimal(0)  # anti-aligned by construction


def test_jaccard_is_intersection_over_union():
    # both: index 2 only. either: 1,2,3 -> 1/3
    assert jaccard([0, 1, 1, 0], [0, 0, 1, 1]) == Decimal(1) / Decimal(3)


def test_jaccard_of_two_never_trading_streams_is_zero_not_an_error():
    assert jaccard([0, 0, 0], [0, 0, 0]) == Decimal(0)


def test_partial_overlap_lands_between():
    a = [1, 1, 1, 1, 0, 0, 0, 0]
    b = [0, 0, 1, 1, 1, 1, 0, 0]
    report = compare(a, b, _pnl(a), _pnl(b))
    assert Decimal(0) < report.jaccard < Decimal(1)
    assert report.both_active == 2


def test_entry_distance_finds_the_nearest_not_the_first():
    assert entry_distances([10], [0, 8, 30]) == [2]
    assert entry_distances([10], [30, 8, 0]) == [2]


def test_entry_distances_are_empty_when_either_side_never_enters():
    assert entry_distances([], [1, 2]) == []
    assert entry_distances([1, 2], []) == []


def test_entries_default_to_rising_edges():
    """A run of in-market days is ONE entry, not one per day."""
    a = [0, 1, 1, 1, 0, 1, 0]
    b = [0, 0, 0, 0, 0, 1, 0]
    report = compare(a, b, _pnl(a), _pnl(b))
    # A enters at index 1 and 5; B only at 5 -> distances [4, 0]
    assert report.entry_distances == [4, 0]
    assert report.median_entry_distance == 2


def test_a_constant_series_yields_zero_correlation_rather_than_raising():
    """Undefined, not zero -- but a report must not abort over one degenerate pair."""
    a = [1, 1, 1, 1]
    b = [0, 1, 0, 1]
    report = compare(a, b, _pnl(a), _pnl(b))
    assert report.position_correlation == Decimal(0)


def test_pnl_correlation_is_independent_of_the_position_vectors():
    """Two rules can be in the market together yet make money at different times."""
    positions = [1, 1, 1, 1]
    a_pnl = [Decimal("1"), Decimal("-1"), Decimal("1"), Decimal("-1")]
    b_pnl = [Decimal("-1"), Decimal("1"), Decimal("-1"), Decimal("1")]
    report = compare(positions, positions, a_pnl, b_pnl)
    assert report.jaccard == Decimal(1)
    assert report.pnl_correlation == pytest.approx(Decimal(-1), abs=Decimal("1e-20"))


def test_unequal_lengths_are_truncated_to_the_shortest():
    report = compare([1, 1, 1], [1, 1], _pnl([1, 1, 1]), _pnl([1, 1]))
    assert report.n_periods == 2
