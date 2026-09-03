"""Paginated backward candle-history backfill against Coinbase, cached via `Repository`.

`ensure_history` fills the local `candles` table for each `(product, granularity)` pair back
to `years` years before `now_ts`, paging backward in windows of `MAX_CANDLES_PER_REQUEST`
candles and stopping the moment a window comes back empty -- that is the asset's inception,
there is nothing older to fetch. Re-running over already-fully-cached data is idempotent: it
issues at most a couple of cheap probe requests (confirming the cached range still reaches
`now_ts` and still-confirmed inception) before making zero further real fetches.

Never calls `time.time()`/`datetime.now()` -- `now_ts` is always passed in by the caller (the
CLI boundary), keeping this module deterministic and network-call-free under test.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from keel.data.feed_scope import volume_feed_of
from keel.types import Candle, Granularity

GRANULARITY_SECONDS: dict[Granularity, int] = {
    Granularity.ONE_MINUTE: 60,
    Granularity.FIVE_MINUTE: 300,
    Granularity.FIFTEEN_MINUTE: 900,
    Granularity.ONE_HOUR: 3600,
    Granularity.SIX_HOUR: 21600,
    Granularity.ONE_DAY: 86400,
}

MAX_CANDLES_PER_REQUEST = 300  # conservative under Coinbase's ~350-candle response cap

_DAYS_PER_YEAR = 365


@dataclass(frozen=True)
class CoverageInfo:
    """Snapshot of cached-candle coverage for one `(product, granularity)` pair."""

    product: str
    granularity: Granularity
    first_ts: int | None
    last_ts: int | None
    n_candles: int
    requested_start_ts: int
    gaps: int


class _Client(Protocol):
    def get_candles(
        self, product_id: str, granularity: Granularity, start: int, end: int
    ) -> list[Candle]: ...


class _Repo(Protocol):
    def get_candles(
        self,
        product_id: str,
        granularity: Granularity,
        start_ts: int | None = None,
        end_ts: int | None = None,
    ) -> list[Candle]: ...

    def upsert_candles(
        self,
        product_id: str,
        granularity: Granularity,
        candles: list[Candle],
        *,
        feed: str | None = None,
    ) -> int: ...


def coverage(
    repo: _Repo, product: str, granularity: Granularity, requested_start_ts: int
) -> CoverageInfo:
    """Summarize cached coverage for `product`/`granularity` at/after `requested_start_ts`."""
    step = GRANULARITY_SECONDS[Granularity(granularity)]
    candles = repo.get_candles(product, granularity, requested_start_ts, None)
    if not candles:
        return CoverageInfo(
            product=product,
            granularity=granularity,
            first_ts=None,
            last_ts=None,
            n_candles=0,
            requested_start_ts=requested_start_ts,
            gaps=0,
        )
    first_ts = candles[0].ts
    last_ts = candles[-1].ts
    n_candles = len(candles)
    expected = (last_ts - first_ts) // step + 1
    gaps = max(0, expected - n_candles)
    return CoverageInfo(
        product=product,
        granularity=granularity,
        first_ts=first_ts,
        last_ts=last_ts,
        n_candles=n_candles,
        requested_start_ts=requested_start_ts,
        gaps=gaps,
    )


def _fill_forward(
    client: _Client,
    repo: _Repo,
    product: str,
    granularity: Granularity,
    step: int,
    latest_cached: int,
    now_ts: int,
    sleep_fn,
    sleep_sec: float,
    feed: str | None = None,
) -> None:
    """Fetch any bars strictly newer than `latest_cached`, up to `now_ts` (recent-bar gaps)."""
    window_start = latest_cached + step
    while window_start <= now_ts:
        # Inclusive range: [a, a + N*step] spans N+1 candles, so the cap needs the -1 or a
        # request for MAX_CANDLES_PER_REQUEST candles actually asks for one more than that.
        window_end = min(now_ts, window_start + (MAX_CANDLES_PER_REQUEST - 1) * step)
        batch = client.get_candles(product, granularity, window_start, window_end)
        if batch:
            repo.upsert_candles(product, granularity, batch, feed=feed)
        sleep_fn(sleep_sec)
        window_start = window_end + step


def _fill_backward(
    client: _Client,
    repo: _Repo,
    product: str,
    granularity: Granularity,
    step: int,
    window_end: int,
    start_floor: int,
    sleep_fn,
    sleep_sec: float,
    feed: str | None = None,
) -> None:
    """Page backward from `window_end` down to `start_floor`, stopping at the first empty
    window -- that window is either the asset's inception or already-covered territory."""
    while window_end >= start_floor:
        # Same inclusive-range arithmetic as `_fill_forward`: N*step would span N+1 candles.
        window_start = max(start_floor, window_end - (MAX_CANDLES_PER_REQUEST - 1) * step)
        batch = client.get_candles(product, granularity, window_start, window_end)
        if not batch:
            break  # inception (or a confirmed-empty probe) -- nothing older to fetch
        repo.upsert_candles(product, granularity, batch, feed=feed)
        sleep_fn(sleep_sec)
        window_end = window_start - step


def ensure_history(
    client: _Client,
    repo: _Repo,
    products: list[str],
    granularities: list[Granularity],
    years: int,
    now_ts: int,
    sleep_fn=lambda s: None,
    sleep_sec: float = 0.2,
    refresh: bool = False,
) -> dict[tuple[str, Granularity], CoverageInfo]:
    """Ensure `years` years of candle history is cached for every `(product, granularity)`.

    For each pair: if not `refresh` and candles are already cached, only fetch the missing
    recent bars (forward, above the latest cached bar) and probe backward from below the
    earliest cached bar (confirming inception or picking up any still-missing older bars).
    Otherwise (no cache, or `refresh=True`) page backward from `now_ts` down to the `years`
    floor, stopping the instant a window comes back empty.
    """
    # Asked once, at the top: the client cannot change feed mid-run, and asking per call would
    # invite a caller to pass a different one for the forward and backward halves of one series.
    feed = volume_feed_of(client)
    result: dict[tuple[str, Granularity], CoverageInfo] = {}
    for product in products:
        for granularity in granularities:
            step = GRANULARITY_SECONDS[Granularity(granularity)]
            start_floor = now_ts - years * _DAYS_PER_YEAR * 86400

            cached = [] if refresh else repo.get_candles(product, granularity)
            if cached:
                earliest_cached = cached[0].ts
                latest_cached = cached[-1].ts
                _fill_forward(
                    client, repo, product, granularity, step, latest_cached, now_ts,
                    sleep_fn, sleep_sec,
                    feed=feed,
)
                _fill_backward(
                    client, repo, product, granularity, step,
                    earliest_cached - step, start_floor, sleep_fn, sleep_sec,
                    feed=feed,
)
            else:
                _fill_backward(
                    client, repo, product, granularity, step,
                    now_ts, start_floor, sleep_fn, sleep_sec,
                    feed=feed,
)

            result[(product, granularity)] = coverage(repo, product, granularity, start_floor)
    return result
