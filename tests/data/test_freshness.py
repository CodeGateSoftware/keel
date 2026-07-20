"""Freshness assessment: pure, offline, no broker and no DB."""

from __future__ import annotations

from keel.data.freshness import (
    DEFAULT_TOLERANCE_BARS,
    Freshness,
    any_gaps,
    any_needs_fetch,
    assess,
    expected_last_ts,
)
from keel.data.history import CoverageInfo
from keel.types import Granularity

_DAY = 86400
_HOUR = 3600

# A round daily boundary so the arithmetic in these tests is legible.
_NOW = 1_784_505_600  # 2026-07-20T00:00:00Z


def _info(last_ts, *, n=100, gaps=0, granularity=Granularity.ONE_DAY, product="BTC-USD"):
    return CoverageInfo(
        product=product,
        granularity=granularity,
        first_ts=None if last_ts is None else last_ts - n * _DAY,
        last_ts=last_ts,
        n_candles=0 if last_ts is None else n,
        requested_start_ts=0,
        gaps=gaps,
    )


def test_expected_last_ts_is_the_bar_below_the_forming_one():
    """At exactly midnight, the day bar stamped today is forming; yesterday's is the newest
    complete one."""
    assert expected_last_ts(_NOW, Granularity.ONE_DAY) == _NOW - _DAY
    # Mid-day: still yesterday's bar, because today's has not closed.
    assert expected_last_ts(_NOW + 13 * _HOUR, Granularity.ONE_DAY) == _NOW - _DAY


def test_a_series_at_the_newest_complete_bar_is_fresh():
    result = assess(_info(_NOW - _DAY), _NOW)
    assert result.bars_behind == 0
    assert result.stale is False
    assert result.needs_fetch is False


def test_one_bar_of_lag_is_tolerated():
    """The forming-bar lag is normal and must not alert (see the module docstring)."""
    result = assess(_info(_NOW - 2 * _DAY), _NOW)
    assert result.bars_behind == 1
    assert result.stale is False


def test_lag_beyond_the_tolerance_is_stale():
    result = assess(_info(_NOW - 5 * _DAY), _NOW)
    assert result.bars_behind == 4
    assert result.stale is True
    assert result.needs_fetch is True


def test_tolerance_boundary_is_inclusive():
    at_tolerance = assess(_info(_NOW - (DEFAULT_TOLERANCE_BARS + 1) * _DAY), _NOW)
    assert at_tolerance.bars_behind == DEFAULT_TOLERANCE_BARS
    assert at_tolerance.stale is False

    one_past = assess(_info(_NOW - (DEFAULT_TOLERANCE_BARS + 2) * _DAY), _NOW)
    assert one_past.bars_behind == DEFAULT_TOLERANCE_BARS + 1
    assert one_past.stale is True


def test_nothing_cached_is_missing_and_stale():
    result = assess(_info(None), _NOW)
    assert result.missing is True
    assert result.stale is True
    assert result.needs_fetch is True
    # There is no last bar to measure lag from; -1 says "unknown", not "zero behind".
    assert result.bars_behind == -1


def test_a_future_last_ts_clamps_to_zero_rather_than_going_negative():
    """A venue serving the forming bar must not read as 'negatively stale'."""
    result = assess(_info(_NOW + 5 * _DAY), _NOW)
    assert result.bars_behind == 0
    assert result.stale is False


def test_internal_gaps_are_reported_but_are_NOT_actionable_by_fetching():
    """`ensure_history` fills forward and probes backward; it cannot repair middle holes.

    Reporting them as `needs_fetch` would make the alert permanently red and therefore
    worthless -- the same reasoning as the lag tolerance.
    """
    result = assess(_info(_NOW - _DAY, gaps=7), _NOW)
    assert result.stale is False
    assert result.gaps == 7
    assert result.needs_fetch is False


def test_hourly_lag_is_measured_in_hourly_bars():
    result = assess(
        _info(_NOW - 10 * _HOUR, granularity=Granularity.ONE_HOUR), _NOW
    )
    assert result.bars_behind == 9
    assert result.stale is True


def test_custom_tolerance_is_honoured():
    info = _info(_NOW - 5 * _DAY)
    assert assess(info, _NOW, tolerance_bars=10).stale is False
    assert assess(info, _NOW, tolerance_bars=1).stale is True


def test_any_needs_fetch_and_any_gaps_are_independent():
    fresh = assess(_info(_NOW - _DAY), _NOW)
    stale = assess(_info(_NOW - 30 * _DAY), _NOW)
    gapped = assess(_info(_NOW - _DAY, gaps=3), _NOW)

    assert any_needs_fetch([fresh]) is False
    assert any_needs_fetch([fresh, stale]) is True
    assert any_needs_fetch([]) is False

    # A current-but-gapped series is NOT a fetch target, but IS a gap report.
    assert any_needs_fetch([gapped]) is False
    assert any_gaps([gapped]) is True
    assert any_gaps([fresh]) is False


def test_freshness_is_frozen():
    result = assess(_info(_NOW - _DAY), _NOW)
    assert isinstance(result, Freshness)
