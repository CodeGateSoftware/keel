"""Gap detection and targeted repair."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from keel.data import gaps as gaps_mod
from keel.data import repair as repair_mod
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.types import Candle, Granularity

_DAY = 86400
_BASE = 1_700_000_000 // _DAY * _DAY


def _candle(ts: int) -> Candle:
    return Candle(
        ts=ts,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("1"),
    )


@pytest.fixture
def repo(tmp_path: Path) -> Repository:
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    return Repository(conn)


# -- detection ----------------------------------------------------------------


def test_contiguous_series_has_no_gaps():
    candles = [_candle(_BASE + i * _DAY) for i in range(10)]
    assert gaps_mod.detect(candles, "BTC-USD", Granularity.ONE_DAY) == []


def test_single_missing_bar_is_one_window():
    candles = [_candle(_BASE + i * _DAY) for i in range(10) if i != 4]
    (window,) = gaps_mod.detect(candles, "BTC-USD", Granularity.ONE_DAY)
    assert window.start_ts == _BASE + 4 * _DAY
    assert window.end_ts == _BASE + 4 * _DAY
    assert window.n_missing == 1


def test_a_run_of_missing_bars_is_one_window_not_many():
    candles = [_candle(_BASE + i * _DAY) for i in range(10) if i not in (4, 5, 6)]
    (window,) = gaps_mod.detect(candles, "BTC-USD", Granularity.ONE_DAY)
    assert window.start_ts == _BASE + 4 * _DAY
    assert window.end_ts == _BASE + 6 * _DAY
    assert window.n_missing == 3


def test_multiple_separate_gaps():
    candles = [_candle(_BASE + i * _DAY) for i in range(12) if i not in (2, 7, 8)]
    windows = gaps_mod.detect(candles, "BTC-USD", Granularity.ONE_DAY)
    assert len(windows) == 2
    assert gaps_mod.total_missing(windows) == 3


def test_a_short_series_is_not_a_gapped_one():
    """Starting late or ending early is `freshness`'s business (missing/stale), not this."""
    candles = [_candle(_BASE + i * _DAY) for i in range(3)]
    assert gaps_mod.detect(candles, "BTC-USD", Granularity.ONE_DAY) == []


def test_empty_and_single_candle_series():
    assert gaps_mod.detect([], "BTC-USD", Granularity.ONE_DAY) == []
    assert gaps_mod.detect([_candle(_BASE)], "BTC-USD", Granularity.ONE_DAY) == []


# -- repository round-trip ----------------------------------------------------


def test_gap_probe_roundtrip_and_idempotence(repo):
    repo.record_gap_probe("BTC-USD", Granularity.ONE_DAY, _BASE, _BASE + _DAY, 2, 111)
    repo.record_gap_probe("BTC-USD", Granularity.ONE_DAY, _BASE, _BASE + _DAY, 2, 222)
    probes = repo.get_gap_probes("BTC-USD", Granularity.ONE_DAY)
    assert probes == [("BTC-USD", "ONE_DAY", _BASE, _BASE + _DAY)]


def test_gap_probes_are_scoped_by_product_and_granularity(repo):
    repo.record_gap_probe("BTC-USD", Granularity.ONE_DAY, _BASE, _BASE, 1, 1)
    repo.record_gap_probe("ETH-USD", Granularity.ONE_DAY, _BASE, _BASE, 1, 1)
    repo.record_gap_probe("BTC-USD", Granularity.ONE_HOUR, _BASE, _BASE, 1, 1)

    assert len(repo.get_gap_probes()) == 3
    assert len(repo.get_gap_probes("BTC-USD")) == 2
    assert len(repo.get_gap_probes("BTC-USD", Granularity.ONE_DAY)) == 1


def test_clear_gap_probes(repo):
    repo.record_gap_probe("BTC-USD", Granularity.ONE_DAY, _BASE, _BASE, 1, 1)
    repo.record_gap_probe("ETH-USD", Granularity.ONE_DAY, _BASE, _BASE, 1, 1)
    assert repo.clear_gap_probes("BTC-USD") == 1
    assert len(repo.get_gap_probes()) == 1
    assert repo.clear_gap_probes() == 1
    assert repo.get_gap_probes() == []


# -- repair -------------------------------------------------------------------


class _Venue:
    """Serves whichever timestamps it was told it has. Records every window asked for."""

    def __init__(self, available: set[int]):
        self.available = available
        self.calls: list[tuple[int, int]] = []

    def get_candles(self, product, granularity, start, end):
        self.calls.append((start, end))
        return [_candle(ts) for ts in sorted(self.available) if start <= ts <= end]


def _seed(repo, missing: set[int], n: int = 10, product="BTC-USD"):
    repo.upsert_candles(
        product,
        Granularity.ONE_DAY,
        [_candle(_BASE + i * _DAY) for i in range(n) if i not in missing],
    )


def test_repair_recovers_a_hole_the_venue_has(repo):
    _seed(repo, missing={4, 5})
    venue = _Venue({_BASE + i * _DAY for i in range(10)})

    result = repair_mod.repair_series(venue, repo, "BTC-USD", Granularity.ONE_DAY, now_ts=999)

    assert result.windows_found == 1
    assert result.windows_probed == 1
    assert result.bars_recovered == 2
    assert result.remaining == []
    assert result.windows_absent_at_source == 0
    # Nothing recorded as absent -- the hole was real and got filled.
    assert repo.get_gap_probes("BTC-USD") == []


def test_a_window_the_venue_lacks_is_recorded_absent_and_not_re_probed(repo):
    _seed(repo, missing={4, 5})
    venue = _Venue({_BASE + i * _DAY for i in range(10) if i not in (4, 5)})

    first = repair_mod.repair_series(venue, repo, "BTC-USD", Granularity.ONE_DAY, now_ts=999)
    assert first.windows_probed == 1
    assert first.bars_recovered == 0
    assert first.windows_absent_at_source == 1
    assert len(repo.get_gap_probes("BTC-USD")) == 1

    # Second pass: still gapped, but the window is proven empty so we must not re-ask.
    venue.calls.clear()
    second = repair_mod.repair_series(venue, repo, "BTC-USD", Granularity.ONE_DAY, now_ts=1000)
    assert second.windows_found == 1
    assert second.windows_skipped_known_absent == 1
    assert second.windows_probed == 0
    assert venue.calls == []


def test_reprobe_known_absent_overrides_the_record(repo):
    _seed(repo, missing={4})
    empty_venue = _Venue({_BASE + i * _DAY for i in range(10) if i != 4})
    repair_mod.repair_series(empty_venue, repo, "BTC-USD", Granularity.ONE_DAY, now_ts=1)

    # The venue later backfills its own history.
    full_venue = _Venue({_BASE + i * _DAY for i in range(10)})
    result = repair_mod.repair_series(
        full_venue, repo, "BTC-USD", Granularity.ONE_DAY, now_ts=2, reprobe_known_absent=True
    )
    assert result.windows_probed == 1
    assert result.bars_recovered == 1
    assert result.remaining == []


def test_a_failed_fetch_is_NOT_recorded_as_absent(repo):
    """A request that never completed proves nothing about whether the venue holds the data."""

    class _BrokenVenue:
        def get_candles(self, *a, **k):
            raise RuntimeError("connection reset")

    _seed(repo, missing={4})
    result = repair_mod.repair_series(
        _BrokenVenue(), repo, "BTC-USD", Granularity.ONE_DAY, now_ts=999
    )

    assert result.errors and "connection reset" in result.errors[0]
    assert result.windows_absent_at_source == 0
    assert repo.get_gap_probes("BTC-USD") == []


def test_one_bad_window_does_not_abort_the_others(repo):
    class _FlakyVenue(_Venue):
        def get_candles(self, product, granularity, start, end):
            if start <= _BASE + 2 * _DAY <= end:
                raise RuntimeError("boom")
            return super().get_candles(product, granularity, start, end)

    _seed(repo, missing={2, 7})
    venue = _FlakyVenue({_BASE + i * _DAY for i in range(10)})

    result = repair_mod.repair_series(venue, repo, "BTC-USD", Granularity.ONE_DAY, now_ts=9)

    assert len(result.errors) == 1
    assert result.bars_recovered == 1  # the good window still got filled
    assert len(result.remaining) == 1


def test_no_gaps_means_no_network_calls(repo):
    _seed(repo, missing=set())
    venue = _Venue(set())
    result = repair_mod.repair_series(venue, repo, "BTC-USD", Granularity.ONE_DAY, now_ts=1)
    assert result.windows_found == 0
    assert venue.calls == []


def test_unexplained_gap_count_excludes_proven_absences(repo):
    _seed(repo, missing={4, 5})
    assert repair_mod.unexplained_gap_count(repo, "BTC-USD", Granularity.ONE_DAY) == 2

    venue = _Venue({_BASE + i * _DAY for i in range(10) if i not in (4, 5)})
    repair_mod.repair_series(venue, repo, "BTC-USD", Granularity.ONE_DAY, now_ts=1)

    # Still physically missing, but now PROVEN absent -- so it must not keep an alert red.
    assert repair_mod.unexplained_gap_count(repo, "BTC-USD", Granularity.ONE_DAY) == 0


def test_a_shifted_window_is_treated_as_new_and_re_probed(repo):
    """Conservative on purpose: exact-key matching only.

    If a neighbouring bar gets filled the window boundaries move, and the old absence record
    no longer applies. Re-probing costs one request; silently suppressing a real hole does not
    announce itself.
    """
    _seed(repo, missing={4, 5})
    venue = _Venue({_BASE + i * _DAY for i in range(10) if i not in (4, 5)})
    repair_mod.repair_series(venue, repo, "BTC-USD", Granularity.ONE_DAY, now_ts=1)

    # The venue now supplies bar 4 only, shrinking the window to {5}.
    venue.available.add(_BASE + 4 * _DAY)
    venue.calls.clear()
    result = repair_mod.repair_series(
        venue, repo, "BTC-USD", Granularity.ONE_DAY, now_ts=2, reprobe_known_absent=True
    )
    assert result.bars_recovered == 1

    venue.calls.clear()
    third = repair_mod.repair_series(venue, repo, "BTC-USD", Granularity.ONE_DAY, now_ts=3)
    assert third.windows_skipped_known_absent == 0
    assert third.windows_probed == 1
