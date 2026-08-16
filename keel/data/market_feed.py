"""Keep the `candles` table populated: historical backfill + periodic polling.

`market_feed` wires an injected `CoinbaseClient` (#7) to `Repository` (#2) -- it never talks
to the network itself and never opens its own DB connection, so tests exercise it against a
fake client and an in-memory `Repository` with zero network calls.

Only *closed* candles are ever fetched or persisted: the candle currently forming as of
`now_ts` has OHLC values that are still changing, so both `backfill` and `poll_once` stop at
the most recently closed candle boundary for each granularity.

`poll_once`'s catch-up range is requested in windows of at most `MAX_CANDLES_PER_REQUEST`
candles, not as one single request: Coinbase rejects any range over ~350 candles with
`400 INVALID_ARGUMENT`. Without chunking, a product that ever falls behind that cap (e.g. after
downtime) would fail every poll forever -- the request only gets bigger over time, never
smaller -- so it can never self-heal. Chunking makes catch-up always succeed eventually,
however far behind a product has fallen.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from keel.data.history import MAX_CANDLES_PER_REQUEST
from keel.types import Candle, Granularity

if TYPE_CHECKING:
    from keel.data.cb_client import CoinbaseClient
    from keel.data.repository import Repository

_GRANULARITY_SECONDS: dict[Granularity, int] = {
    Granularity.ONE_MINUTE: 60,
    Granularity.FIVE_MINUTE: 5 * 60,
    Granularity.FIFTEEN_MINUTE: 15 * 60,
    Granularity.ONE_HOUR: 60 * 60,
    Granularity.SIX_HOUR: 6 * 60 * 60,
    Granularity.ONE_DAY: 24 * 60 * 60,
}

_SECONDS_PER_DAY = 24 * 60 * 60


def _granularity_seconds(granularity: Granularity) -> int:
    return _GRANULARITY_SECONDS[Granularity(granularity)]


def _latest_closed_ts(now_ts: int, gran_sec: int) -> int:
    """Open-time of the most recently *closed* candle as of `now_ts`.

    The candle spanning `[floor(now_ts/gran), floor(now_ts/gran) + gran)` is still forming
    (or has just opened), so the last closed candle is one full period before that.
    """
    return (now_ts // gran_sec) * gran_sec - gran_sec


def _align_up(ts: int, gran_sec: int) -> int:
    """Round `ts` up to the next multiple of `gran_sec`."""
    return ((ts + gran_sec - 1) // gran_sec) * gran_sec


def _request_windows(start: int, end: int, gran_sec: int) -> list[tuple[int, int]]:
    """Tile the inclusive `[start, end]` range into windows under the venue's candle cap.

    One definition of the arithmetic for every candle request this module makes. The `- 1`
    keeps each inclusive `[window_start, window_end]` at exactly `MAX_CANDLES_PER_REQUEST`
    candles -- an inclusive range of `+ step` would be one over, which is the off-by-one #271
    had to correct in `history.py`, and having it written twice here is how that happens.

    Returns a single window when the range already fits, so a small gap still costs one request.
    """
    windows: list[tuple[int, int]] = []
    window_start = start
    while window_start <= end:
        window_end = min(end, window_start + (MAX_CANDLES_PER_REQUEST - 1) * gran_sec)
        windows.append((window_start, window_end))
        window_start = window_end + gran_sec
    return windows


def _missing_ranges(expected: list[int], present: set[int], gran_sec: int) -> list[tuple[int, int]]:
    """Group the `expected` ts values not in `present` into contiguous `(start, end)` ranges."""
    missing = [ts for ts in expected if ts not in present]
    if not missing:
        return []

    ranges: list[tuple[int, int]] = []
    range_start = missing[0]
    prev = missing[0]
    for ts in missing[1:]:
        if ts == prev + gran_sec:
            prev = ts
            continue
        ranges.append((range_start, prev))
        range_start = ts
        prev = ts
    ranges.append((range_start, prev))
    return ranges


def backfill(
    client: CoinbaseClient,
    repo: Repository,
    products: list[str],
    granularities: list[Granularity],
    history_days: int,
    *,
    now_ts: int | None = None,
) -> int:
    """Backfill `history_days` of closed candles for every product x granularity.

    Resumable: for each `(product, granularity)` pair, only the timestamps in the desired
    window that are *missing* from `repo` are requested from `client` and upserted. Re-running
    `backfill` once a window is fully populated fetches and writes nothing (returns `0`).

    Returns the total number of candle rows written across all products/granularities.
    """
    now_ts = int(time.time()) if now_ts is None else now_ts
    total_written = 0

    for product_id in products:
        for granularity in granularities:
            gran_sec = _granularity_seconds(granularity)
            latest_closed = _latest_closed_ts(now_ts, gran_sec)
            window_start = _align_up(now_ts - history_days * _SECONDS_PER_DAY, gran_sec)
            if window_start > latest_closed:
                continue

            expected = list(range(window_start, latest_closed + 1, gran_sec))
            existing = {
                c.ts for c in repo.get_candles(product_id, granularity, window_start, latest_closed)
            }

            for range_start, range_end in _missing_ranges(expected, existing, gran_sec):
                # A contiguous missing range is itself unbounded -- an empty repo makes the
                # whole history window one range -- so page it under the venue's candle cap.
                # Upserted per window, so a mid-range failure leaves earlier windows persisted.
                for req_start, req_end in _request_windows(range_start, range_end, gran_sec):
                    fetched = client.get_candles(product_id, granularity, req_start, req_end)
                    gap_candles = [
                        c
                        for c in fetched
                        if window_start <= c.ts <= latest_closed and c.ts not in existing
                    ]
                    if gap_candles:
                        total_written += repo.upsert_candles(product_id, granularity, gap_candles)

    return total_written


def _poll_catch_up(
    client: CoinbaseClient,
    repo: Repository,
    product_id: str,
    granularity: Granularity,
    gran_sec: int,
    fetch_start: int,
    latest_closed: int,
    last_ts: int | None,
) -> int:
    """Fetch and upsert `[fetch_start, latest_closed]`, chunked under the venue's candle cap.

    Mirrors `history._fill_forward`'s windowing idiom: page forward in windows of at most
    `MAX_CANDLES_PER_REQUEST` candles each (see `_request_windows`), upserting per window for
    incremental durability (if a later window raises, earlier windows are already persisted).
    An empty window does *not* stop the loop -- a mid-history hole must not block catch-up of
    newer candles.
    """
    total_written = 0
    seen: set[int] = set()
    for window_start, window_end in _request_windows(fetch_start, latest_closed, gran_sec):
        fetched = client.get_candles(product_id, granularity, window_start, window_end)
        new_candles: list[Candle] = [
            c
            for c in fetched
            if c.ts <= latest_closed and (last_ts is None or c.ts > last_ts) and c.ts not in seen
        ]
        if new_candles:
            seen.update(c.ts for c in new_candles)
            total_written += repo.upsert_candles(product_id, granularity, new_candles)

    return total_written


def poll_once(
    client: CoinbaseClient,
    repo: Repository,
    products: list[str],
    granularities: list[Granularity],
    *,
    now_ts: int | None = None,
) -> int:
    """Fetch and upsert any newly closed candles since the last poll.

    For each `(product, granularity)` pair, only candles strictly newer than the latest one
    already stored (and no later than the most recently closed candle) are written. The
    catch-up range is requested in windows of at most `MAX_CANDLES_PER_REQUEST` candles (see
    module docstring) so a product that has fallen far behind -- even hundreds of candles, as
    happened in production -- still fully catches up in one call to `poll_once`, instead of
    failing outright and staying stuck.

    Returns the total number of new candle rows written.
    """
    now_ts = int(time.time()) if now_ts is None else now_ts
    total_written = 0

    for product_id in products:
        for granularity in granularities:
            gran_sec = _granularity_seconds(granularity)
            latest_closed = _latest_closed_ts(now_ts, gran_sec)

            existing = repo.get_candles(product_id, granularity)
            last_ts = existing[-1].ts if existing else None
            if last_ts is not None and last_ts >= latest_closed:
                continue

            fetch_start = last_ts + gran_sec if last_ts is not None else latest_closed
            total_written += _poll_catch_up(
                client, repo, product_id, granularity, gran_sec, fetch_start, latest_closed, last_ts
            )

    return total_written


def is_fresh(
    repo: Repository,
    product: str,
    granularity: Granularity,
    now_ts: int,
    max_age_sec: int,
) -> bool:
    """True if `product`/`granularity` has a stored candle no older than `max_age_sec`.

    Used as the stale-data guard input by later phases (§10.4) -- `False` when there is no
    stored candle at all, or when the newest one is older than `max_age_sec`.
    """
    candles = repo.get_candles(product, granularity)
    if not candles:
        return False
    latest_ts = candles[-1].ts
    return (now_ts - latest_ts) <= max_age_sec
