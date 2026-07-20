"""Detect and describe holes inside a cached candle series -- pure, offline.

`history.coverage()` counts gaps; this locates them. A `GapWindow` is a contiguous run of
timestamps that *should* exist between two cached bars and does not, which is exactly the
input a targeted re-fetch needs.

**Not every gap is repairable.** A venue genuinely has no data for some windows -- exchange
downtime, a thin book, a listing boundary. Re-fetching those forever is a treadmill, and it is
why `repair.py` records a probed-and-still-empty window as *absent at source* rather than
retrying it on every scheduled run. Without that distinction `--fail-on-gaps` could never be
satisfied and would be useless as a default.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from keel.data.history import GRANULARITY_SECONDS
from keel.types import Candle, Granularity


@dataclass(frozen=True)
class GapWindow:
    """A contiguous run of missing bars, inclusive of both endpoints."""

    product: str
    granularity: Granularity
    start_ts: int  # first MISSING bar
    end_ts: int  # last MISSING bar
    n_missing: int

    def key(self) -> tuple[str, str, int, int]:
        return (self.product, self.granularity.value, self.start_ts, self.end_ts)


def detect(
    candles: Sequence[Candle], product: str, granularity: Granularity
) -> list[GapWindow]:
    """Find every interior hole in an ascending-by-ts candle series.

    Only INTERIOR holes: a series that simply starts late or ends early is not gapped, it is
    short, and that is `freshness.py`'s business (missing/stale), not this module's.
    """
    step = GRANULARITY_SECONDS[Granularity(granularity)]
    windows: list[GapWindow] = []
    for previous, current in zip(candles, candles[1:]):
        delta = current.ts - previous.ts
        if delta <= step:
            continue
        first_missing = previous.ts + step
        last_missing = current.ts - step
        windows.append(
            GapWindow(
                product=product,
                granularity=Granularity(granularity),
                start_ts=first_missing,
                end_ts=last_missing,
                n_missing=(last_missing - first_missing) // step + 1,
            )
        )
    return windows


def total_missing(windows: Sequence[GapWindow]) -> int:
    return sum(window.n_missing for window in windows)


def subtract_known_absent(
    windows: Sequence[GapWindow], known_absent: set[tuple[str, str, int, int]]
) -> list[GapWindow]:
    """Drop windows already probed and confirmed empty at the venue.

    Exact-key matching only. A window whose boundaries shifted (because a neighbouring bar was
    since filled) is deliberately treated as NEW and re-probed -- the cheap, conservative
    direction, since the alternative is silently suppressing a real hole.
    """
    return [window for window in windows if window.key() not in known_absent]
