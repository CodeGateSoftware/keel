"""Tests for `keel.data.history` -- paginated backward candle-history backfill.

`history` wires an injected `CoinbaseClient` (#7) to `Repository` (#2), backfilling ~N years
of candle history for each `(product, granularity)` pair. Every test here injects a
`FakeClient` that serves canned, in-memory candle series instead of a real transport -- no
live network calls are made, and no test ever sleeps (`sleep_fn` defaults to a no-op).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from keel.data.db import connect, migrate
from keel.data.history import (
    GRANULARITY_SECONDS,
    MAX_CANDLES_PER_REQUEST,
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
