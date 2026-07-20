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
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from keel.data import gaps as gaps_mod
from keel.data.history import GRANULARITY_SECONDS
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
            # and over-asking costs nothing because `upsert_candles` is idempotent.
            fetched = client.get_candles(
                product, granularity, window.start_ts - step, window.end_ts + step
            )
        except Exception as exc:  # noqa: BLE001 -- one bad window must not abort the pass
            # NOT recorded as absent: a fetch that never completed proves nothing about
            # whether the venue holds the data.
            result.errors.append(f"{window.start_ts}-{window.end_ts}: {exc}")
            continue

        # Only a COMPLETED request can testify that the venue lacks the data.
        probed_ok.append(window)
        if fetched:
            repo.upsert_candles(product, granularity, fetched)
        sleep_fn(sleep_sec)

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


def unexplained_gap_count(repo, product: str, granularity: Granularity) -> int:
    """Missing bars NOT yet proven absent at the venue -- what `--fail-on-gaps` should judge."""
    granularity = Granularity(granularity)
    windows = gaps_mod.detect(repo.get_candles(product, granularity), product, granularity)
    known = set(repo.get_gap_probes(product, granularity))
    return gaps_mod.total_missing(gaps_mod.subtract_known_absent(windows, known))
