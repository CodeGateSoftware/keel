"""Tests for `keel.data.market_feed`.

`market_feed` wires an injected `CoinbaseClient` (#7) to `Repository` (#2) to keep the
`candles` table populated. Every test here injects a `FakeClient` that serves canned,
in-memory candle series instead of a real transport -- no live network calls are made.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from keel.data.db import connect, migrate
from keel.data.market_feed import backfill, is_fresh, poll_once
from keel.data.repository import Repository
from keel.types import Candle, Granularity

GRAN_SEC = 3600  # ONE_HOUR
NOW = 1_700_100_000  # fixed, aligned to an hour boundary
LATEST_CLOSED = (NOW // GRAN_SEC) * GRAN_SEC - GRAN_SEC  # 1_700_096_400
HISTORY_DAYS = 2
WINDOW_START = NOW - HISTORY_DAYS * 86400  # already hour-aligned
EXPECTED_TS = list(range(WINDOW_START, LATEST_CLOSED + 1, GRAN_SEC))  # 48 hourly candles


def _candle(ts: int, price: str = "100") -> Candle:
    p = Decimal(price)
    return Candle(ts=ts, open=p, high=p, low=p, close=p, volume=Decimal("1"))


@pytest.fixture
def repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    return Repository(conn)


class FakeClient:
    """Fake `CoinbaseClient` -- serves candles from an in-memory series per (product, gran).

    `get_candles(product_id, granularity, start, end)` returns the subset of the configured
    series with `start <= ts <= end`, matching the real client's inclusive-range semantics.
    Records every call so tests can assert *which* ranges were actually requested (the
    gap-only contract).
    """

    def __init__(self, series: dict[tuple[str, Granularity], list[Candle]]) -> None:
        self._series = series
        self.calls: list[tuple[str, Granularity, int, int]] = []

    def get_candles(
        self, product_id: str, granularity: Granularity, start: int, end: int
    ) -> list[Candle]:
        self.calls.append((product_id, granularity, start, end))
        series = self._series.get((product_id, granularity), [])
        return [c for c in series if start <= c.ts <= end]


def _full_series(product_id: str = "BTC-USD") -> dict[tuple[str, Granularity], list[Candle]]:
    # Include one extra still-forming "current" candle at ts=NOW to verify it's never
    # persisted by backfill/poll_once (only *closed* candles are).
    ts_values = EXPECTED_TS + [NOW]
    return {(product_id, Granularity.ONE_HOUR): [_candle(ts) for ts in ts_values]}


# -- backfill -----------------------------------------------------------------


def test_backfill_writes_all_closed_candles_in_history_window(repo):
    client = FakeClient(_full_series())

    written = backfill(
        client, repo, ["BTC-USD"], [Granularity.ONE_HOUR], HISTORY_DAYS, now_ts=NOW
    )

    assert written == len(EXPECTED_TS)
    stored = repo.get_candles("BTC-USD", Granularity.ONE_HOUR)
    assert [c.ts for c in stored] == EXPECTED_TS
    # the in-progress candle at ts=NOW must never be persisted
    assert NOW not in {c.ts for c in stored}


def test_backfill_is_idempotent_on_rerun(repo):
    client = FakeClient(_full_series())
    backfill(client, repo, ["BTC-USD"], [Granularity.ONE_HOUR], HISTORY_DAYS, now_ts=NOW)

    written_again = backfill(
        client, repo, ["BTC-USD"], [Granularity.ONE_HOUR], HISTORY_DAYS, now_ts=NOW
    )

    assert written_again == 0
    assert len(repo.get_candles("BTC-USD", Granularity.ONE_HOUR)) == len(EXPECTED_TS)


def test_backfill_only_fetches_missing_gaps(repo):
    """Pre-populate the first half of the window; backfill should only fetch/write the rest."""
    midpoint = len(EXPECTED_TS) // 2
    already_have = EXPECTED_TS[:midpoint]
    still_missing = EXPECTED_TS[midpoint:]
    repo.upsert_candles(
        "BTC-USD", Granularity.ONE_HOUR, [_candle(ts) for ts in already_have]
    )
    client = FakeClient(_full_series())

    written = backfill(
        client, repo, ["BTC-USD"], [Granularity.ONE_HOUR], HISTORY_DAYS, now_ts=NOW
    )

    assert written == len(still_missing)
    stored_ts = {c.ts for c in repo.get_candles("BTC-USD", Granularity.ONE_HOUR)}
    assert stored_ts == set(EXPECTED_TS)
    # the fake client should never have been asked for a range covering already-stored ts
    for _, _, start, end in client.calls:
        requested = set(range(start, end + 1, GRAN_SEC))
        assert requested.isdisjoint(already_have)


def test_backfill_covers_multiple_products_and_granularities(repo):
    series = _full_series("BTC-USD")
    series[("ETH-USD", Granularity.ONE_HOUR)] = [_candle(ts) for ts in EXPECTED_TS + [NOW]]
    client = FakeClient(series)

    written = backfill(
        client, repo, ["BTC-USD", "ETH-USD"], [Granularity.ONE_HOUR], HISTORY_DAYS, now_ts=NOW
    )

    assert written == 2 * len(EXPECTED_TS)
    assert len(repo.get_candles("BTC-USD", Granularity.ONE_HOUR)) == len(EXPECTED_TS)
    assert len(repo.get_candles("ETH-USD", Granularity.ONE_HOUR)) == len(EXPECTED_TS)


def test_backfill_returns_zero_when_client_has_no_data_for_gap(repo):
    client = FakeClient({("BTC-USD", Granularity.ONE_HOUR): []})

    written = backfill(
        client, repo, ["BTC-USD"], [Granularity.ONE_HOUR], HISTORY_DAYS, now_ts=NOW
    )

    assert written == 0
    assert repo.get_candles("BTC-USD", Granularity.ONE_HOUR) == []


# -- poll_once ----------------------------------------------------------------


def test_poll_once_appends_only_new_closed_candles(repo):
    # repo already has everything except the last 2 closed candles
    have = EXPECTED_TS[:-2]
    repo.upsert_candles("BTC-USD", Granularity.ONE_HOUR, [_candle(ts) for ts in have])
    client = FakeClient(_full_series())

    written = poll_once(client, repo, ["BTC-USD"], [Granularity.ONE_HOUR], now_ts=NOW)

    assert written == 2
    stored_ts = {c.ts for c in repo.get_candles("BTC-USD", Granularity.ONE_HOUR)}
    assert stored_ts == set(EXPECTED_TS)
    assert NOW not in stored_ts  # still-forming candle never persisted


def test_poll_once_is_a_noop_when_already_up_to_date(repo):
    repo.upsert_candles(
        "BTC-USD", Granularity.ONE_HOUR, [_candle(ts) for ts in EXPECTED_TS]
    )
    client = FakeClient(_full_series())

    written = poll_once(client, repo, ["BTC-USD"], [Granularity.ONE_HOUR], now_ts=NOW)

    assert written == 0
    assert len(repo.get_candles("BTC-USD", Granularity.ONE_HOUR)) == len(EXPECTED_TS)


def test_poll_once_starts_from_scratch_when_repo_is_empty(repo):
    client = FakeClient(_full_series())

    written = poll_once(client, repo, ["BTC-USD"], [Granularity.ONE_HOUR], now_ts=NOW)

    assert written == 1
    stored = repo.get_candles("BTC-USD", Granularity.ONE_HOUR)
    assert [c.ts for c in stored] == [LATEST_CLOSED]


# -- is_fresh -------------------------------------------------------------------


def test_is_fresh_true_within_max_age(repo):
    repo.upsert_candles("BTC-USD", Granularity.ONE_HOUR, [_candle(LATEST_CLOSED)])

    assert is_fresh(
        repo, "BTC-USD", Granularity.ONE_HOUR, now_ts=LATEST_CLOSED + 100, max_age_sec=200
    )


def test_is_fresh_false_when_stale(repo):
    repo.upsert_candles("BTC-USD", Granularity.ONE_HOUR, [_candle(LATEST_CLOSED)])

    assert not is_fresh(
        repo, "BTC-USD", Granularity.ONE_HOUR, now_ts=LATEST_CLOSED + 1000, max_age_sec=200
    )


def test_is_fresh_false_when_no_candles_stored(repo):
    assert not is_fresh(
        repo, "BTC-USD", Granularity.ONE_HOUR, now_ts=NOW, max_age_sec=200
    )
