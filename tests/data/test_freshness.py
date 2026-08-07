"""Freshness assessment: pure, offline, no broker and no DB."""

from __future__ import annotations

from decimal import Decimal

from keel.data.freshness import (
    DEFAULT_TOLERANCE_BARS,
    BarReadiness,
    Freshness,
    any_gaps,
    any_needs_fetch,
    assess,
    entry_bar_ready,
    expected_last_ts,
)
from keel.data.history import CoverageInfo
from keel.types import Candle, Granularity

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


def _candle(ts: int, price: str = "100") -> Candle:
    p = Decimal(price)
    return Candle(ts=ts, open=p, high=p, low=p, close=p, volume=Decimal("1"))


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


# -- entry_bar_ready (the real-money entry gate; see keel/agent.py's wiring) -------------------
#
# This is deliberately a SEPARATE predicate from `assess`/`DEFAULT_TOLERANCE_BARS` -- that
# tolerance exists so an operator-facing staleness ALERT does not fire on the normal
# forming-bar lag. An entry gate on real money needs `bars_behind == 0`: a 1-bar-late hourly
# series is exactly the condition that produces a duplicate order (Finding 1), and the alert
# tolerance would wave it through.
#
# All the "01:20 UTC" scenarios below use `_NOW` as UTC day X's 00:00 -- so `_NOW + _HOUR +
# 20*60` is X's 01:20, matching the live LaunchAgent's first eligible trigger.

_AT_0120 = _NOW + _HOUR + 20 * 60  # UTC day X, 01:20 -- the first live trigger of the day.
_AT_1420 = _NOW + 14 * _HOUR + 20 * 60  # UTC day X, 14:20 -- well into the trading day.

# The ts `_completed_days` (turtle_breakout.py) waits for: the 00:00-01:00 UTC hourly bar's
# OPEN ts. At `_AT_0120`, `expected_last_ts(ONE_DAY)` is `_NOW - _DAY` (day X-1's bar) and
# `expected_ts + step` is `_NOW` (day X 00:00) -- exactly this.
_DAY_CLOSE_HOURLY_TS = _NOW


def test_entry_bar_ready_missing_when_nothing_stored():
    """No cached bar at all for the gated granularity -- there is nothing to confirm, so this
    must never read as "ready". Mirrors `assess`'s `missing`/`bars_behind=-1` convention.
    """
    result = entry_bar_ready({}, Granularity.ONE_DAY, _AT_0120)
    assert result.ready is False
    assert result.reason == "missing"
    assert result.bars_behind == -1
    assert result.stored_ts is None

    # An explicitly-empty list reads the same as an absent key.
    empty = entry_bar_ready({Granularity.ONE_DAY: []}, Granularity.ONE_DAY, _AT_0120)
    assert empty.ready is False
    assert empty.reason == "missing"


def test_entry_bar_ready_behind_when_the_gated_series_itself_lags():
    """The gated granularity's own bar is stale (bars_behind > 0) -- e.g. the daily fetch
    itself hasn't run yet. This must block regardless of any finer series' state; `_completed_days`
    would return an empty/short series in the equivalent live scenario.
    """
    candles_by_tf = {Granularity.ONE_DAY: [_candle(_NOW - 2 * _DAY)]}  # X-2: one bar behind
    result = entry_bar_ready(candles_by_tf, Granularity.ONE_DAY, _AT_0120)
    assert result.ready is False
    assert result.reason == "behind"
    assert result.bars_behind == 1


def test_entry_bar_ready_unconfirmed_when_the_finer_series_has_not_crossed_the_close():
    """THE REGRESSION CASE. At 01:20 UTC, `_completed_days` withholds the just-closed daily bar
    until the 00:00-01:00 UTC hourly bar has closed -- i.e. until an hourly bar stamped `_NOW`
    (day X 00:00) exists. A one-bar-late hourly series (newest stamped `_NOW - _HOUR`, the
    PRIOR day's last hour) has not crossed that boundary: `_completed_days` would drop an extra
    daily bar and the rule would re-evaluate a bar already traded (Finding 1's duplicate order).
    This predicate must call that exact condition "not ready".
    """
    candles_by_tf = {
        Granularity.ONE_DAY: [_candle(_NOW - _DAY)],  # X-1: current, NOT itself behind
        Granularity.ONE_HOUR: [_candle(_NOW - _HOUR)],  # one hour short of the day close
    }
    result = entry_bar_ready(candles_by_tf, Granularity.ONE_DAY, _AT_0120)
    assert result.ready is False
    assert result.reason == "unconfirmed"
    assert result.bars_behind == 0  # the daily bar itself is current -- the hourly is what blocks
    assert result.blocked_by == Granularity.ONE_HOUR
    assert result.blocked_by_ts == _NOW - _HOUR


def test_entry_bar_ready_unconfirmed_when_the_finer_series_is_present_but_empty():
    """A configured-but-empty finer series (e.g. the very first poll) must block exactly like a
    late one -- there is nothing to confirm the daily bar closed against."""
    candles_by_tf = {
        Granularity.ONE_DAY: [_candle(_NOW - _DAY)],
        Granularity.ONE_HOUR: [],
    }
    result = entry_bar_ready(candles_by_tf, Granularity.ONE_DAY, _AT_0120)
    assert result.ready is False
    assert result.reason == "unconfirmed"
    assert result.blocked_by == Granularity.ONE_HOUR
    assert result.blocked_by_ts is None


def test_entry_bar_ready_when_the_finer_series_has_crossed_the_day_close():
    """The positive case this exists to unblock: the 00:00-01:00 UTC hourly bar (ts == `_NOW`)
    has closed, so the daily bar it confirms is genuinely done -- this is `_completed_days`'s own
    condition, generalized, and it must pass the instant that condition would."""
    candles_by_tf = {
        Granularity.ONE_DAY: [_candle(_NOW - _DAY)],
        Granularity.ONE_HOUR: [_candle(_DAY_CLOSE_HOURLY_TS)],
    }
    result = entry_bar_ready(candles_by_tf, Granularity.ONE_DAY, _AT_0120)
    assert result.ready is True
    assert result.reason is None
    assert result.blocked_by is None


def test_entry_bar_ready_with_no_finer_series_configured_is_ready():
    """A config that only ever polls `ONE_DAY` (no hourly key at all) has nothing to confirm
    against -- the gate must not invent a requirement the deployment never asked for."""
    candles_by_tf = {Granularity.ONE_DAY: [_candle(_NOW - _DAY)]}
    result = entry_bar_ready(candles_by_tf, Granularity.ONE_DAY, _AT_0120)
    assert result.ready is True
    assert result.blocked_by is None


def test_entry_bar_ready_is_not_over_strict_about_how_far_behind_the_finer_series_is():
    """NOT the over-strict version: at 14:20 UTC an hourly series that is FIVE bars behind its
    OWN expectation (last stamped 08:00, when 13:00 is expected) still confirms the daily bar,
    because it has long since crossed the day-close boundary the daily bar needs. A
    `bars_behind == 0` requirement on ONE_HOUR would have been far too strict -- the 01:20
    trigger gives only 5 minutes of margin on a 15-minute bar -- and would block entries
    routinely rather than only in the narrow post-midnight window this gate targets.
    """
    candles_by_tf = {
        Granularity.ONE_DAY: [_candle(_NOW - _DAY)],
        Granularity.ONE_HOUR: [_candle(_NOW + 8 * _HOUR)],  # 5 bars behind ONE_HOUR's own 13:00
    }
    result = entry_bar_ready(candles_by_tf, Granularity.ONE_DAY, _AT_1420)
    assert result.ready is True
    assert result.reason is None


def test_entry_bar_ready_reports_the_coarsest_blocking_series_deterministically():
    """When several finer series are all unconfirmed, the reported `blocked_by` must be
    deterministic (same inputs -> same diagnostic) so an operator's alert doesn't flap between
    two different "which bar is missing" messages across identical cycles. The COARSEST
    offender is reported -- it is the one closest to actually confirming, so it is also the one
    likely to clear soonest and is the most actionable to page on."""
    candles_by_tf = {
        Granularity.ONE_DAY: [_candle(_NOW - _DAY)],
        Granularity.ONE_HOUR: [_candle(_NOW - _HOUR)],
        Granularity.FIFTEEN_MINUTE: [_candle(_NOW - 15 * 60)],
    }
    result = entry_bar_ready(candles_by_tf, Granularity.ONE_DAY, _AT_0120)
    assert result.ready is False
    assert result.blocked_by == Granularity.ONE_HOUR


def test_entry_bar_readiness_is_frozen():
    result = entry_bar_ready(
        {Granularity.ONE_DAY: [_candle(_NOW - _DAY)]}, Granularity.ONE_DAY, _AT_0120
    )
    assert isinstance(result, BarReadiness)
