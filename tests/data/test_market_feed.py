"""Tests for `keel.data.market_feed`.

`market_feed` wires an injected `CoinbaseClient` (#7) to `Repository` (#2) to keep the
`candles` table populated. Every test here injects a `FakeClient` that serves canned,
in-memory candle series instead of a real transport -- no live network calls are made.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from keel.data.db import connect, migrate
from keel.data.history import MAX_CANDLES_PER_REQUEST
from keel.data.market_feed import backfill, is_fresh, poll_once
from keel.data.repository import Repository
from keel.types import Candle, Granularity

GRAN_SEC = 3600  # ONE_HOUR
NOW = 1_700_100_000  # fixed, aligned to an hour boundary
LATEST_CLOSED = (NOW // GRAN_SEC) * GRAN_SEC - GRAN_SEC  # 1_700_096_400
HISTORY_DAYS = 2
WINDOW_START = NOW - HISTORY_DAYS * 86400  # already hour-aligned
EXPECTED_TS = list(range(WINDOW_START, LATEST_CLOSED + 1, GRAN_SEC))  # 48 hourly candles

# A stale-poll scenario mirroring the real ZEC-USD production failure: the last stored
# candle is STALE_HOURS behind the most recently closed one, well over Coinbase's
# ~350-candle-per-request cap, so a correct `poll_once` must page the catch-up in windows.
STALE_HOURS = 552  # the real ZEC-USD gap
STALE_LAST_TS = LATEST_CLOSED - STALE_HOURS * GRAN_SEC  # the stale last-stored candle
STALE_FULL_TS = list(range(STALE_LAST_TS, LATEST_CLOSED + 1, GRAN_SEC))  # seed + all catch-up


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


def _stale_series(product_id: str = "BTC-USD") -> dict[tuple[str, Granularity], list[Candle]]:
    # Contiguous hourly run from the stale last-stored candle through LATEST_CLOSED, plus
    # the still-forming candle at ts=NOW, matching `_full_series`'s convention.
    ts_values = STALE_FULL_TS + [NOW]
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


def test_poll_once_uses_a_single_request_for_a_small_gap(repo):
    # repo already has everything except the last 2 closed candles -- unchanged small-gap path
    have = EXPECTED_TS[:-2]
    repo.upsert_candles("BTC-USD", Granularity.ONE_HOUR, [_candle(ts) for ts in have])
    client = FakeClient(_full_series())

    poll_once(client, repo, ["BTC-USD"], [Granularity.ONE_HOUR], now_ts=NOW)

    assert len(client.calls) == 1


def test_poll_once_chunks_a_gap_larger_than_the_coinbase_cap(repo):
    """A stale last candle (552h behind, like real ZEC-USD) must still be fully caught up."""
    repo.upsert_candles("BTC-USD", Granularity.ONE_HOUR, [_candle(STALE_LAST_TS)])
    client = FakeClient(_stale_series())

    written = poll_once(client, repo, ["BTC-USD"], [Granularity.ONE_HOUR], now_ts=NOW)

    assert written == STALE_HOURS
    assert len(client.calls) > 1
    stored = repo.get_candles("BTC-USD", Granularity.ONE_HOUR)
    assert [c.ts for c in stored] == STALE_FULL_TS
    stored_ts = {c.ts for c in stored}
    assert NOW not in stored_ts  # still-forming candle never persisted


def test_poll_once_never_requests_more_than_the_candle_cap(repo):
    repo.upsert_candles("BTC-USD", Granularity.ONE_HOUR, [_candle(STALE_LAST_TS)])
    client = FakeClient(_stale_series())

    poll_once(client, repo, ["BTC-USD"], [Granularity.ONE_HOUR], now_ts=NOW)

    assert client.calls
    for _, _, start, end in client.calls:
        assert start <= end
        candle_count = (end - start) // GRAN_SEC + 1
        assert candle_count <= MAX_CANDLES_PER_REQUEST


def test_poll_once_chunk_windows_are_contiguous_and_non_overlapping(repo):
    repo.upsert_candles("BTC-USD", Granularity.ONE_HOUR, [_candle(STALE_LAST_TS)])
    client = FakeClient(_stale_series())

    poll_once(client, repo, ["BTC-USD"], [Granularity.ONE_HOUR], now_ts=NOW)

    # a gap this large can only tile into >1 window under the Coinbase candle cap
    assert len(client.calls) > 1
    fetch_start = STALE_LAST_TS + GRAN_SEC
    assert client.calls[0][2] == fetch_start
    assert client.calls[-1][3] == LATEST_CLOSED
    for previous, current in zip(client.calls, client.calls[1:]):
        assert current[2] == previous[3] + GRAN_SEC


def test_poll_once_keeps_going_past_an_empty_window_so_a_mid_history_hole_cannot_wedge_catch_up(
    repo,
):
    """A window that returns zero candles must not stop catch-up.

    `_poll_catch_up`'s docstring calls this out explicitly: an empty window means a
    mid-history hole (a stretch the venue genuinely has no candles for), not proof that
    there's nothing further to fetch. If the loop broke on an empty window -- the way
    `history._fill_backward` does -- catch-up would wedge at the hole forever and never
    reach fresh data beyond it. That asymmetry with `_fill_backward` is intentional, not an
    oversight: `_fill_backward` walks backward through history it may legitimately exhaust,
    so stopping at "no more data" is correct there. `_poll_catch_up` walks forward toward
    *now*, where there is always more recent data past any hole, so it must keep paging.

    The fixture spans a gap wider than the Coinbase cap (`STALE_HOURS`, as in the chunking
    tests above), so catch-up must issue more than one windowed request. The fake client is
    configured to have real candles for *none* of the first window's range -- an empty
    window, simulating the hole -- and only serves candles for a later window. A future
    refactor that adds `if not fetched: break` to `_poll_catch_up` makes this test fail: the
    loop would stop after the first (empty) window and never reach the later, real candles.
    """
    repo.upsert_candles("BTC-USD", Granularity.ONE_HOUR, [_candle(STALE_LAST_TS)])
    fetch_start = STALE_LAST_TS + GRAN_SEC
    first_window_end = min(
        LATEST_CLOSED, fetch_start + (MAX_CANDLES_PER_REQUEST - 1) * GRAN_SEC
    )
    assert first_window_end < LATEST_CLOSED, "fixture must span >1 window for this test to hold"
    later_ts = list(range(first_window_end + GRAN_SEC, LATEST_CLOSED + 1, GRAN_SEC))
    client = FakeClient(
        {("BTC-USD", Granularity.ONE_HOUR): [_candle(ts) for ts in later_ts + [NOW]]}
    )

    written = poll_once(client, repo, ["BTC-USD"], [Granularity.ONE_HOUR], now_ts=NOW)

    # the empty first window must not have stopped the loop before a second request
    assert len(client.calls) > 1
    assert written == len(later_ts)
    stored_ts = {c.ts for c in repo.get_candles("BTC-USD", Granularity.ONE_HOUR)}
    # the pre-existing seed candle plus exactly the later window's candles -- nothing from
    # the empty (hole) window, since it had nothing to write
    assert stored_ts == {STALE_LAST_TS, *later_ts}
    assert LATEST_CLOSED in stored_ts  # catch-up reached fresh data past the hole


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


# -- backfill: the same candle-cap defect, on the one windowing site #269/#271 did not reach ----
#
# #269 chunked `poll_once` and #271 chunked `repair.py` and `history.py`. `backfill` groups
# missing timestamps into CONTIGUOUS ranges via `_missing_ranges` and asked for each range in a
# single request -- so a contiguous hole wider than the cap 400s exactly as the poll path did.
# Latent today (no production caller; `keel fetch` goes through `history.ensure_history`), but
# it is the same defect class, and #271's stated goal was that every candle-request windowing
# site in the codebase agree.

BACKFILL_HOURS = 552  # same span as the real ZEC-USD gap, comfortably over the cap
BACKFILL_DAYS = BACKFILL_HOURS // 24 + 1
_BACKFILL_RAW_START = NOW - BACKFILL_DAYS * 86400
# `backfill` aligns its window start UP to the next granularity boundary; mirrored here rather
# than importing the private helper, so the test pins the observable behaviour.
BACKFILL_WINDOW_START = ((_BACKFILL_RAW_START + GRAN_SEC - 1) // GRAN_SEC) * GRAN_SEC
BACKFILL_TS = list(range(BACKFILL_WINDOW_START, LATEST_CLOSED + 1, GRAN_SEC))


def _wide_series(product_id: str = "BTC-USD") -> dict[tuple[str, Granularity], list[Candle]]:
    return {(product_id, Granularity.ONE_HOUR): [_candle(ts) for ts in BACKFILL_TS + [NOW]]}


def test_backfill_never_requests_more_than_the_candle_cap(repo):
    """An empty repo makes the whole history window one contiguous missing range."""
    client = FakeClient(_wide_series())

    backfill(
        client, repo, ["BTC-USD"], [Granularity.ONE_HOUR],
        history_days=BACKFILL_DAYS, now_ts=NOW,
    )

    assert client.calls
    for _, _, start, end in client.calls:
        assert start <= end
        candle_count = (end - start) // GRAN_SEC + 1
        assert candle_count <= MAX_CANDLES_PER_REQUEST


def test_backfill_chunk_windows_are_contiguous_and_cover_the_gap(repo):
    client = FakeClient(_wide_series())

    written = backfill(
        client, repo, ["BTC-USD"], [Granularity.ONE_HOUR],
        history_days=BACKFILL_DAYS, now_ts=NOW,
    )

    assert len(client.calls) > 1, "a range this wide can only tile into >1 window under the cap"
    assert client.calls[0][2] == BACKFILL_WINDOW_START
    assert client.calls[-1][3] == LATEST_CLOSED
    for previous, current in zip(client.calls, client.calls[1:]):
        assert current[2] == previous[3] + GRAN_SEC
    assert written == len(BACKFILL_TS), "every closed candle in the window should be persisted"


def test_backfill_still_uses_one_request_for_a_gap_within_the_cap(repo):
    """Regression guard: chunking must not add requests to the ordinary small-window case."""
    client = FakeClient(_full_series())

    backfill(
        client, repo, ["BTC-USD"], [Granularity.ONE_HOUR],
        history_days=HISTORY_DAYS, now_ts=NOW,
    )

    assert len(client.calls) == 1
