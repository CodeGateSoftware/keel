"""Keep the `candles` table populated: historical backfill + periodic polling.

`market_feed` wires an injected `CoinbaseClient` (#7) to `Repository` (#2) -- it never talks
to the network itself and never opens its own DB connection, so tests exercise it against a
fake client and an in-memory `Repository` with zero network calls.

Only *closed* candles are ever fetched or persisted: the candle currently forming as of
`now_ts` has OHLC values that are still changing, so both `backfill` and `poll_once` stop at
the most recently closed candle boundary for each granularity.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from halal_cb.types import Candle, Granularity

if TYPE_CHECKING:
    from halal_cb.data.cb_client import CoinbaseClient
    from halal_cb.data.repository import Repository

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
                fetched = client.get_candles(product_id, granularity, range_start, range_end)
                gap_candles = [
                    c
                    for c in fetched
                    if window_start <= c.ts <= latest_closed and c.ts not in existing
                ]
                if gap_candles:
                    total_written += repo.upsert_candles(product_id, granularity, gap_candles)

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
    already stored (and no later than the most recently closed candle) are written.

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
            fetched = client.get_candles(product_id, granularity, fetch_start, latest_closed)
            new_candles: list[Candle] = [
                c
                for c in fetched
                if c.ts <= latest_closed and (last_ts is None or c.ts > last_ts)
            ]
            if new_candles:
                total_written += repo.upsert_candles(product_id, granularity, new_candles)

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
