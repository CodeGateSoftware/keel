"""Targeted re-fetch of interior candle gaps.

`history.ensure_history` fills FORWARD from the newest cached bar and probes BACKWARD from the
oldest. Neither motion touches a hole in the middle, so a series can be perfectly current and
still be missing bars. This module closes that: locate each hole (`gaps.detect`), ask the venue
for exactly that window, and re-check.

**The important part is what happens when the venue has nothing.** Some windows are permanently
empty -- exchange downtime, a thin book, a listing boundary. Retrying them on every scheduled
run is a treadmill, and it would leave `--fail-on-gaps` permanently red and therefore useless.
So a window that comes back still-incomplete after a real probe is recorded in
`candle_gap_probes` as *absent at source*, and subsequent runs skip it.

⚠️ That record is an ASSERTION ABOUT AN OBSERVATION ("we asked and it had nothing"), not an
assumption. It is only ever written after an actual fetch attempt, never inferred -- which is
why the v5 migration deliberately backfills nothing.

**A gap window can be arbitrarily large.** `gaps.detect` puts no cap on `n_missing` -- an
interior hole just accumulates for as long as the venue was unreachable or the asset was thin.
Coinbase rejects any single request spanning more than ~350 candles, so a large-enough hole
requested in one call 400s. Left unchunked, that failure is swallowed into `result.errors` and
the pass moves on -- which means the same oversized request gets retried, and 400s again, on
every single scheduled run forever, and *silently* since a logged error is easy to miss. So the
widened window is paged in `MAX_CANDLES_PER_REQUEST`-sized chunks, each upserted as it arrives:
a fetch that fails partway through still leaves the earlier chunks persisted, and the next run
resumes further along instead of repeating the whole doomed request.

That chunking is PREVENTATIVE. No cached series is anywhere near the cap today -- the largest
interior gap across the deployment is 15 bars -- but a multi-day venue outage, a delisting and
relisting, or a product added back after a long absence would each clear ~349 in a single hole,
and the trap only announces itself as a series that quietly never repairs.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from keel.data import gaps as gaps_mod
from keel.data.history import GRANULARITY_SECONDS, MAX_CANDLES_PER_REQUEST
from keel.types import Granularity


@dataclass
class RepairResult:
    """What one `(product, granularity)` repair pass actually achieved."""

    product: str
    granularity: Granularity
    windows_found: int = 0
    windows_skipped_known_absent: int = 0
    windows_probed: int = 0
    bars_recovered: int = 0
    windows_absent_at_source: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def bars_still_missing(self) -> int:
        return sum(w.n_missing for w in self.remaining)

    remaining: list[gaps_mod.GapWindow] = field(default_factory=list)


def _fetch_window_chunked(
    client,
    repo,
    product: str,
    granularity: Granularity,
    window: gaps_mod.GapWindow,
    step: int,
    sleep_fn,
    sleep_sec: float,
) -> None:
    """Fetch one gap window's widened range, one `MAX_CANDLES_PER_REQUEST`-sized chunk at a
    time, upserting each chunk as it arrives.

    The ±`step` widening (venues are inconsistent about endpoint inclusivity) applies only to
    these OUTER edges -- internal chunk boundaries are contiguous, not themselves widened.
    Upserting per chunk, rather than batching the whole window, is what lets an interior chunk
    failure leave the earlier chunks persisted instead of losing the whole fetch.
    """
    fetch_start = window.start_ts - step
    fetch_end = window.end_ts + step
    chunk_start = fetch_start
    while chunk_start <= fetch_end:
        chunk_end = min(fetch_end, chunk_start + (MAX_CANDLES_PER_REQUEST - 1) * step)
        fetched = client.get_candles(product, granularity, chunk_start, chunk_end)
        if fetched:
            repo.upsert_candles(product, granularity, fetched)
        sleep_fn(sleep_sec)
        chunk_start = chunk_end + step


def repair_series(
    client,
    repo,
    product: str,
    granularity: Granularity,
    now_ts: int | None = None,
    reprobe_known_absent: bool = False,
    sleep_fn=lambda s: None,
    sleep_sec: float = 0.2,
) -> RepairResult:
    """Locate and re-fetch interior gaps for one series.

    `reprobe_known_absent=True` ignores previously recorded absences -- for when you suspect a
    transient venue failure poisoned the record rather than a genuine hole in history.
    """
    granularity = Granularity(granularity)
    step = GRANULARITY_SECONDS[granularity]
    now_ts = int(time.time()) if now_ts is None else now_ts
    result = RepairResult(product=product, granularity=granularity)

    before = repo.get_candles(product, granularity)
    windows = gaps_mod.detect(before, product, granularity)
    result.windows_found = len(windows)
    if not windows:
        return result

    if reprobe_known_absent:
        todo = list(windows)
    else:
        known = set(repo.get_gap_probes(product, granularity))
        todo = gaps_mod.subtract_known_absent(windows, known)
        result.windows_skipped_known_absent = len(windows) - len(todo)

    probed_ok: list[gaps_mod.GapWindow] = []
    for window in todo:
        result.windows_probed += 1
        try:
            # Widen by one step each side: venues are inconsistent about endpoint inclusivity,
            # and over-asking costs nothing because `upsert_candles` is idempotent. The window
            # itself may be far larger than one request can hold, so this pages internally.
            _fetch_window_chunked(
                client, repo, product, granularity, window, step, sleep_fn, sleep_sec
            )
        except Exception as exc:  # noqa: BLE001 -- one bad window must not abort the pass
            # NOT recorded as absent: a fetch that never completed proves nothing about
            # whether the venue holds the data. Chunks that DID complete before the failure
            # were already upserted, so a later run resumes past them rather than restarting.
            result.errors.append(f"{window.start_ts}-{window.end_ts}: {exc}")
            continue

        # Only a window whose EVERY chunk completed can testify that the venue lacks the data.
        probed_ok.append(window)

    after = repo.get_candles(product, granularity)
    result.bars_recovered = max(0, len(after) - len(before))
    result.remaining = gaps_mod.detect(after, product, granularity)

    # Anything we actually probed and that is STILL missing is absent at the venue. Record it
    # so the next scheduled run does not re-ask.
    probed_keys = {w.key() for w in probed_ok}
    for window in result.remaining:
        if window.key() in probed_keys:
            repo.record_gap_probe(
                product,
                granularity,
                window.start_ts,
                window.end_ts,
                window.n_missing,
                now_ts,
            )
            result.windows_absent_at_source += 1

    return result


def unexplained_gap_count(
    repo, product: str, granularity: Granularity, start_ts: int | None = None
) -> int:
    """Missing bars NOT yet proven absent at the venue -- what `--fail-on-gaps` should judge.

    `start_ts` exists because the fetch display subtracts this number from `coverage()`'s gap
    count, and `coverage()` counts gaps over `get_candles(.., requested_start_ts, None)` -- a
    window-bounded slice. The two counts must describe the SAME window or the subtraction goes
    negative: on 2026-08-17 `keel fetch` printed `5 internal gaps (-1 proven absent at venue)`
    for a series whose whole-series unexplained count (6) exceeded its window-bounded gap
    count (5) because bars were missing older than the fetch window. Default `None` reads the
    whole series, exactly the pre-parameter behavior -- which is the scope `--fail-on-gaps`
    keeps, since `repair_series` probes holes wherever they sit, not only inside a fetch
    window.

    Honest boundary caveat: a hole that STRADDLES `start_ts` is seen by the bounded read only
    in its in-window remainder, which is not interior to the bounded slice (the bar before the
    hole falls outside the window) -- so neither this count nor `coverage()`'s can see it, and
    a `candle_gap_probes` record keyed by the whole-series window applies to nothing the
    bounded view detects. Such a hole therefore displays as neither gapped nor unexplained.
    That is the conservative direction (the window can only under-report a hole crossing its
    own start boundary, never claim one absent) and it is rare: it requires a hole crossing
    the fetch window's start exactly.
    """
    granularity = Granularity(granularity)
    candles = repo.get_candles(product, granularity, start_ts, None)
    windows = gaps_mod.detect(candles, product, granularity)
    known = set(repo.get_gap_probes(product, granularity))
    return gaps_mod.total_missing(gaps_mod.subtract_known_absent(windows, known))
