"""Gap detection and targeted repair."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from keel.data import gaps as gaps_mod
from keel.data import repair as repair_mod
from keel.data.db import connect, migrate
from keel.data.history import MAX_CANDLES_PER_REQUEST
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


class _CappedVenue(_Venue):
    """Like `_Venue`, but actually enforces Coinbase's real per-request ceiling.

    `_Venue` alone never rejects an oversized ask, so it can't tell an unchunked repair apart
    from a chunked one. This is what turns "the request would 400 in production" into something
    a test can observe: any single call spanning >349 candles blows up, same as the real venue.
    """

    _CAP = 349  # Coinbase's real ceiling; MAX_CANDLES_PER_REQUEST (300) must stay under it

    def get_candles(self, product, granularity, start, end):
        self.calls.append((start, end))
        n_requested = (end - start) // _DAY + 1
        if n_requested > self._CAP:
            raise RuntimeError(
                "400 INVALID_ARGUMENT: number of candles requested should be less than 350"
            )
        return [_candle(ts) for ts in sorted(self.available) if start <= ts <= end]


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


def test_unexplained_gap_count_default_still_reads_the_whole_series(repo):
    """Pin the default: no `start_ts` means the WHOLE series, exactly as before the parameter
    existed.

    `--fail-on-gaps` judges this default. A hole older than any fetch window is still a hole
    `repair_series` will probe (it reads the whole series itself), so the flag's count must
    keep seeing it -- narrowing the default here would silently narrow the flag.
    """
    _seed(repo, missing={2, 7})
    assert repair_mod.unexplained_gap_count(repo, "BTC-USD", Granularity.ONE_DAY) == 2


def test_unexplained_gap_count_bounded_ignores_holes_before_start_ts(repo):
    """With `start_ts`, only holes at/after the boundary count.

    The fetch display subtracts this number from `coverage()`'s gap count, and `coverage()`
    counts gaps over `get_candles(.., requested_start_ts, None)` -- so the two counts must
    describe the SAME slice or the subtraction goes negative (see the field repro in
    `tests/data/test_fetch_cli.py`).
    """
    _seed(repo, missing={2, 7})
    boundary = _BASE + 5 * _DAY  # between the two holes
    bounded = repair_mod.unexplained_gap_count(
        repo, "BTC-USD", Granularity.ONE_DAY, start_ts=boundary
    )
    assert bounded == 1  # only the hole at index 7; the one at index 2 is before the window


def test_a_hole_straddling_start_ts_is_invisible_to_the_bounded_count(repo):
    """The honest boundary caveat, pinned.

    A hole whose span crosses `start_ts` is seen by the bounded read only in its in-window
    remainder, which is NOT interior to the bounded slice (the bar before the hole falls
    outside the window), so neither the bounded gap count nor this count can see it -- and a
    `candle_gap_probes` record keyed by the whole-series window does not apply to any window
    the bounded view does see. The conservative direction: the window display can only
    UNDER-report a hole that crosses its own start boundary, never claim one absent, and the
    whole-series default above still sees it for `--fail-on-gaps`.
    """
    _seed(repo, missing={4, 5, 6})
    boundary = _BASE + 5 * _DAY  # inside the hole
    bounded = repair_mod.unexplained_gap_count(
        repo, "BTC-USD", Granularity.ONE_DAY, start_ts=boundary
    )
    assert bounded == 0
    # The docstring's closing claim, asserted for THIS fixture: the whole-series default
    # still sees all three bars, so `--fail-on-gaps` keeps its un-narrowed scope.
    assert repair_mod.unexplained_gap_count(repo, "BTC-USD", Granularity.ONE_DAY) == 3


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


# -- chunking a gap window that exceeds the per-request cap -------------------
#
# PREVENTATIVE, not a reproduction: no cached series has an interior gap anywhere near the cap
# today (the largest across the deployment is 15 bars, against a ~349 limit). But an interior
# gap that big is entirely reachable -- a multi-day venue outage, a delisting-and-relisting, a
# product added back after a long absence -- and the failure mode if it ever happens is the bad
# kind: one request for the whole window 400s ("number of candles requested should be less than
# 350"), `repair_series` swallows it into `result.errors` and continues, and every scheduled run
# thereafter retries the same doomed request. Silent and permanent. These tests pin the fix:
# page the widened outer range in contiguous, non-overlapping chunks of at most
# `MAX_CANDLES_PER_REQUEST` candles. 553 is used as the oversized-gap figure throughout.


def test_a_large_hole_is_fetched_in_capped_chunks(repo):
    """The widened outer range spans 555 candles (553 missing + one step on each side), so it
    must page as 300 then 255 -- never one request the venue would reject."""
    n_missing = 553
    _seed(repo, missing=set(range(1, n_missing + 1)), n=n_missing + 2)
    venue = _CappedVenue({_BASE + i * _DAY for i in range(n_missing + 2)})

    repair_mod.repair_series(venue, repo, "BTC-USD", Granularity.ONE_DAY, now_ts=999)

    assert len(venue.calls) == 2
    counts = [(end - start) // _DAY + 1 for start, end in venue.calls]
    assert counts == [300, 255]
    assert all(count <= MAX_CANDLES_PER_REQUEST for count in counts)


def test_a_large_hole_is_fully_recovered_with_no_duplicates_or_drops(repo):
    n_missing = 553
    _seed(repo, missing=set(range(1, n_missing + 1)), n=n_missing + 2)
    venue = _CappedVenue({_BASE + i * _DAY for i in range(n_missing + 2)})

    result = repair_mod.repair_series(venue, repo, "BTC-USD", Granularity.ONE_DAY, now_ts=999)

    assert result.bars_recovered == n_missing
    assert result.remaining == []
    stored = repo.get_candles("BTC-USD", Granularity.ONE_DAY)
    ts_values = [c.ts for c in stored]
    assert ts_values == sorted(set(ts_values))  # ascending, no duplicates
    assert len(stored) == n_missing + 2  # nothing dropped either


def test_chunk_windows_tile_the_outer_range_without_widening_interior_boundaries(repo):
    """The ±step widening is load-bearing only at the OUTER edges (venues disagree about
    endpoint inclusivity); internal chunk boundaries must stay contiguous, not overlap, and
    must not themselves be widened."""
    n_missing = 553
    _seed(repo, missing=set(range(1, n_missing + 1)), n=n_missing + 2)
    venue = _CappedVenue({_BASE + i * _DAY for i in range(n_missing + 2)})

    repair_mod.repair_series(venue, repo, "BTC-USD", Granularity.ONE_DAY, now_ts=999)

    counts = [(end - start) // _DAY + 1 for start, end in venue.calls]
    assert all(count <= MAX_CANDLES_PER_REQUEST for count in counts)
    assert venue.calls[0][0] == _BASE + 0 * _DAY  # window.start_ts - step
    assert venue.calls[-1][1] == _BASE + (n_missing + 1) * _DAY  # window.end_ts + step
    for (_, prev_end), (next_start, _) in zip(venue.calls, venue.calls[1:]):
        assert next_start == prev_end + _DAY


def test_one_bad_chunk_is_not_recorded_absent_and_a_later_window_still_repairs(repo):
    """A partially-fetched window proves nothing about whether the venue holds the rest of it --
    it must not be recorded absent, and a later, separate gap in the same pass must still get
    probed and filled rather than the whole pass aborting."""

    class _FlakySecondChunk(_CappedVenue):
        def get_candles(self, product, granularity, start, end):
            if start == _BASE + 300 * _DAY:  # the big window's second chunk
                raise RuntimeError("boom")
            return super().get_candles(product, granularity, start, end)

    n_missing = 553
    missing = set(range(1, n_missing + 1)) | {700, 701}
    _seed(repo, missing=missing, n=706)
    venue = _FlakySecondChunk({_BASE + i * _DAY for i in range(706)})

    result = repair_mod.repair_series(venue, repo, "BTC-USD", Granularity.ONE_DAY, now_ts=999)

    assert len(result.errors) == 1
    assert result.windows_absent_at_source == 0
    assert repo.get_gap_probes("BTC-USD") == []

    # The later, separate gap still got probed and filled in the same pass.
    stored_ts = {c.ts for c in repo.get_candles("BTC-USD", Granularity.ONE_DAY)}
    assert _BASE + 700 * _DAY in stored_ts
    assert _BASE + 701 * _DAY in stored_ts

    # Exactly the failed chunk's span -- not the whole original window -- remains missing.
    (remaining,) = result.remaining
    assert remaining.start_ts == _BASE + 300 * _DAY
    assert remaining.end_ts == _BASE + 553 * _DAY
    assert remaining.n_missing == 254


def test_a_window_whose_FIRST_chunk_fails_is_not_recorded_absent(repo):
    """The discriminating case for "every chunk must complete before recording absent".

    Its sibling above fails the SECOND chunk, which means the first chunk lands 299 bars and the
    surviving gap window's key shifts from (BASE+1d, BASE+553d) to (BASE+300d, BASE+553d). The
    `probed_keys` match in `repair.py` is by EXACT key, so that test's `windows_absent_at_source`
    and `get_gap_probes` assertions hold no matter what `probed_ok` contains -- they cannot see a
    regression. Failing the FIRST chunk instead means nothing is upserted, the remaining window
    keeps its original key, and the gate is genuinely exercised.

    Without this, a plausible refactor -- "partial progress means the window was probed" -- passes
    the whole suite while permanently writing off a hole the venue was never fully asked about.
    """

    class _FlakyFirstChunk(_CappedVenue):
        def get_candles(self, product, granularity, start, end):
            if start == _BASE + 0 * _DAY:  # the widened window's FIRST chunk
                raise RuntimeError("boom")
            return super().get_candles(product, granularity, start, end)

    n_missing = 553
    _seed(repo, missing=set(range(1, n_missing + 1)), n=706)
    venue = _FlakyFirstChunk({_BASE + i * _DAY for i in range(706)})

    result = repair_mod.repair_series(venue, repo, "BTC-USD", Granularity.ONE_DAY, now_ts=999)

    assert len(result.errors) == 1
    assert result.bars_recovered == 0, "the first chunk failed, so nothing should have landed"

    # The key is UNCHANGED, which is what makes the next two assertions meaningful rather than
    # vacuous: `probed_keys` is consulted for exactly this window.
    (remaining,) = result.remaining
    assert remaining.start_ts == _BASE + 1 * _DAY
    assert remaining.end_ts == _BASE + n_missing * _DAY

    assert result.windows_absent_at_source == 0
    assert repo.get_gap_probes("BTC-USD") == []


def test_a_small_hole_still_takes_exactly_one_request(repo):
    """Regression guard: chunking must not fragment requests that already fit under the cap."""
    _seed(repo, missing={4, 5})
    venue = _CappedVenue({_BASE + i * _DAY for i in range(10)})

    result = repair_mod.repair_series(venue, repo, "BTC-USD", Granularity.ONE_DAY, now_ts=999)

    assert len(venue.calls) == 1
    assert result.bars_recovered == 2


def test_sleep_fn_is_called_once_per_chunk_not_once_per_window(repo):
    n_missing = 553
    _seed(repo, missing=set(range(1, n_missing + 1)), n=n_missing + 2)
    venue = _CappedVenue({_BASE + i * _DAY for i in range(n_missing + 2)})
    sleeps: list[float] = []

    repair_mod.repair_series(
        venue, repo, "BTC-USD", Granularity.ONE_DAY, now_ts=999, sleep_fn=sleeps.append
    )

    assert len(sleeps) == 2


def test_a_multi_chunk_window_with_nothing_at_venue_is_still_recorded_absent(repo):
    """`probed_ok` requires every chunk to COMPLETE, not to return data. A large hole the venue
    genuinely has none of must still end up recorded absent at source, exactly like a
    single-chunk one would -- chunking is a fetch-mechanics detail, not a change in what counts
    as a real probe."""
    n_missing = 553
    _seed(repo, missing=set(range(1, n_missing + 1)), n=n_missing + 2)
    venue = _CappedVenue(set())  # the venue has none of the missing timestamps

    result = repair_mod.repair_series(venue, repo, "BTC-USD", Granularity.ONE_DAY, now_ts=999)

    assert result.windows_absent_at_source == 1
    assert len(repo.get_gap_probes("BTC-USD")) == 1
    assert len(venue.calls) > 1
