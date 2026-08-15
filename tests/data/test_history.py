"""Tests for `keel.data.history` -- paginated backward candle-history backfill.

`history` wires an injected `CoinbaseClient` (#7) to `Repository` (#2), backfilling ~N years
of candle history for each `(product, granularity)` pair. Every test here injects a
`FakeClient` that serves canned, in-memory candle series instead of a real transport -- no
live network calls are made, and no test ever sleeps (`sleep_fn` defaults to a no-op).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from keel.data import history as history_mod
from keel.data.db import connect, migrate
from keel.data.history import (
    GRANULARITY_SECONDS,
    MAX_CANDLES_PER_REQUEST,
    _fill_backward,
    _fill_forward,
    coverage,
    ensure_history,
)
from keel.data.repository import Repository
from keel.types import Candle, Granularity


class FakeClient:
    """Serves candles from an in-memory dict keyed by product; honors [start,end]."""

    def __init__(self, series: dict[str, list[Candle]]) -> None:
        self.series = series
        self.calls: list[tuple] = []

    def get_candles(self, product_id, granularity, start, end):
        self.calls.append((product_id, granularity, start, end))
        return [c for c in self.series.get(product_id, []) if start <= c.ts <= end]


def _mk(ts: int) -> Candle:
    return Candle(
        ts=ts, open=Decimal(1), high=Decimal(1), low=Decimal(1), close=Decimal(1),
        volume=Decimal(1),
    )


@pytest.fixture
def repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    return Repository(conn)


def test_paginates_backward_and_caches_all(repo):
    step = GRANULARITY_SECONDS[Granularity.ONE_HOUR]
    now = 1_000_000 * step
    full = [_mk(now - i * step) for i in range(1000)]  # 1000 hourly candles
    client = FakeClient({"BTC-USD": full})
    cov = ensure_history(client, repo, ["BTC-USD"], [Granularity.ONE_HOUR], years=1, now_ts=now)
    stored = repo.get_candles("BTC-USD", Granularity.ONE_HOUR)
    # every candle within the 1yr window got cached, de-duplicated, ascending
    assert len(stored) == len(
        {c.ts for c in full if c.ts >= now - GRANULARITY_SECONDS[Granularity.ONE_HOUR] * 24 * 365}
    )
    assert stored == sorted(stored, key=lambda c: c.ts)
    assert cov[("BTC-USD", Granularity.ONE_HOUR)].n_candles == len(stored)


def test_stops_at_inception_on_empty_window(repo):
    step = GRANULARITY_SECONDS[Granularity.ONE_HOUR]
    now = 500 * step
    # only 50 candles exist (asset "born" recently)
    born = [_mk(now - i * step) for i in range(50)]
    client = FakeClient({"NEW-USD": born})
    ensure_history(client, repo, ["NEW-USD"], [Granularity.ONE_HOUR], years=5, now_ts=now)
    # once a backward window returns empty, it stops (bounded number of calls, not 5yr worth)
    assert len(client.calls) < 5  # 50 candles => ~1 full window + 1 empty
    assert repo.get_candles("NEW-USD", Granularity.ONE_HOUR)  # got what existed


def test_idempotent_resume_only_fetches_missing(repo):
    step = GRANULARITY_SECONDS[Granularity.ONE_HOUR]
    now = 2000 * step
    full = [_mk(now - i * step) for i in range(200)]
    client = FakeClient({"BTC-USD": full})
    ensure_history(client, repo, ["BTC-USD"], [Granularity.ONE_HOUR], years=1, now_ts=now)
    calls_first = len(client.calls)
    ensure_history(client, repo, ["BTC-USD"], [Granularity.ONE_HOUR], years=1, now_ts=now)
    # second run already fully cached => far fewer (ideally 0-1) new fetches
    assert len(client.calls) - calls_first <= 1


def test_coverage_reports_range_count_and_gaps(repo):
    step = GRANULARITY_SECONDS[Granularity.ONE_HOUR]
    now = 100 * step
    candles = [_mk(now - i * step) for i in range(10)]
    repo.upsert_candles("BTC-USD", Granularity.ONE_HOUR, candles)
    cov = coverage(repo, "BTC-USD", Granularity.ONE_HOUR, requested_start_ts=now - 9 * step)
    assert cov.n_candles == 10
    assert cov.first_ts == now - 9 * step
    assert cov.last_ts == now
    assert cov.gaps == 0


def test_coverage_empty_when_nothing_cached(repo):
    cov = coverage(repo, "BTC-USD", Granularity.ONE_HOUR, requested_start_ts=0)
    assert cov.n_candles == 0
    assert cov.first_ts is None
    assert cov.last_ts is None
    assert cov.gaps == 0


def test_max_candles_per_request_is_conservative_under_coinbase_cap():
    assert MAX_CANDLES_PER_REQUEST == 300


# -- inclusive-range off-by-one: [a, a + N*step] holds N+1 candles, not N ------
#
# Harmless at MAX_CANDLES_PER_REQUEST=300 (301 candles still clears Coinbase's ~350 ceiling),
# but it silently activates the same incident as the repair-side hole in `repair.py` the moment
# the constant is raised toward 350. Each window must hold at most MAX_CANDLES_PER_REQUEST
# candles: `(end - start) // step + 1 <= MAX_CANDLES_PER_REQUEST`.


def test_fill_forward_never_requests_more_than_the_cap_per_call(repo):
    step = GRANULARITY_SECONDS[Granularity.ONE_HOUR]
    latest_cached = 0
    now = 1000 * step
    full = [_mk(i * step) for i in range(1, 1001)]  # 1000 candles strictly newer than cached
    client = FakeClient({"BTC-USD": full})

    _fill_forward(
        client, repo, "BTC-USD", Granularity.ONE_HOUR, step, latest_cached, now,
        sleep_fn=lambda s: None, sleep_sec=0,
    )

    sizes = [(end - start) // step + 1 for (_, _, start, end) in client.calls]
    assert sizes == [300, 300, 300, 100]
    assert max(sizes) <= MAX_CANDLES_PER_REQUEST


def test_fill_backward_never_requests_more_than_the_cap_per_call(repo):
    step = GRANULARITY_SECONDS[Granularity.ONE_HOUR]
    now = 2000 * step
    window_end = now
    start_floor = now - 999 * step
    full = [_mk(now - i * step) for i in range(1000)]  # exactly [start_floor, window_end]
    client = FakeClient({"BTC-USD": full})

    _fill_backward(
        client, repo, "BTC-USD", Granularity.ONE_HOUR, step, window_end, start_floor,
        sleep_fn=lambda s: None, sleep_sec=0,
    )

    sizes = [(end - start) // step + 1 for (_, _, start, end) in client.calls]
    assert sizes == [300, 300, 300, 100]
    assert max(sizes) <= MAX_CANDLES_PER_REQUEST


def test_window_sizing_tracks_the_cap_constant_so_raising_it_toward_350_stays_safe(
    monkeypatch, repo
):
    """The whole point of the -1 fix: window sizing must be `(MAX_CANDLES_PER_REQUEST - 1) *
    step`, derived from the constant, not a number that happens to match it today. Raise the cap
    toward Coinbase's real ~350 ceiling and the per-call size must track it exactly, not drift
    a candle over."""
    monkeypatch.setattr(history_mod, "MAX_CANDLES_PER_REQUEST", 350)
    step = GRANULARITY_SECONDS[Granularity.ONE_HOUR]
    latest_cached = 0
    now = 1000 * step
    full = [_mk(i * step) for i in range(1, 1001)]
    client = FakeClient({"BTC-USD": full})

    history_mod._fill_forward(
        client, repo, "BTC-USD", Granularity.ONE_HOUR, step, latest_cached, now,
        sleep_fn=lambda s: None, sleep_sec=0,
    )

    sizes = [(end - start) // step + 1 for (_, _, start, end) in client.calls]
    assert max(sizes) <= 350
