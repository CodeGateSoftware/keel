"""Cached-candle freshness assessment -- pure, offline, no network.

`history.coverage()` answers "what is cached?"; this answers "is what is cached CURRENT
ENOUGH, and where are the holes?" Kept separate from `history.py` because every function here
is a pure computation over a `CoverageInfo` and a clock reading, which makes the staleness
policy testable without a broker, a DB, or a network -- and the CLI's `--check` mode depends
on that being true.

**Why a tolerance instead of demanding zero lag.** The most recent bar is still FORMING: at
14:30 the 1-hour bar stamped 14:00 is incomplete and the venue may not serve it at all, so a
correctly-updated cache is normally one bar behind and sometimes two across a fetch boundary.
Alerting at zero lag would fire constantly and train everyone to ignore it.
"""

from __future__ import annotations

from dataclasses import dataclass

from keel.data.history import GRANULARITY_SECONDS, CoverageInfo
from keel.types import Granularity

#: Bars of lag tolerated before a series is called stale. See the module docstring: one bar is
#: the normal forming-bar lag, two absorbs a fetch straddling a bar boundary.
DEFAULT_TOLERANCE_BARS = 2


@dataclass(frozen=True)
class Freshness:
    """One `(product, granularity)` series' freshness verdict."""

    product: str
    granularity: Granularity
    n_candles: int
    last_ts: int | None
    bars_behind: int
    gaps: int
    missing: bool
    stale: bool

    @property
    def needs_fetch(self) -> bool:
        """Is this ACTIONABLE by running a fetch?

        Deliberately excludes `gaps`. `history.ensure_history` fills forward from the newest
        cached bar and probes backward from the oldest; it does not repair holes in the middle,
        so a gapped-but-current series would report "needs fetch" forever and fetching would
        change nothing. Failing an alert on a condition the command cannot fix trains you to
        ignore the alert -- the same argument as the lag tolerance. Gaps are reported
        prominently instead, and `any_gaps()` is available for a caller that wants to be strict.
        """
        return self.missing or self.stale


def expected_last_ts(now_ts: int, granularity: Granularity) -> int:
    """The timestamp of the most recent bar that could possibly be COMPLETE.

    `now_ts` floored to the granularity step is the bar currently forming, so the newest
    complete bar is one step below it.
    """
    step = GRANULARITY_SECONDS[Granularity(granularity)]
    return (now_ts // step) * step - step


def assess(
    info: CoverageInfo, now_ts: int, tolerance_bars: int = DEFAULT_TOLERANCE_BARS
) -> Freshness:
    """Assess one series. Nothing cached at all counts as both `missing` and `stale`."""
    granularity = Granularity(info.granularity)
    step = GRANULARITY_SECONDS[granularity]

    if info.last_ts is None or info.n_candles == 0:
        return Freshness(
            product=info.product,
            granularity=granularity,
            n_candles=0,
            last_ts=None,
            bars_behind=-1,  # unknown: there is no last bar to measure from
            gaps=info.gaps,
            missing=True,
            stale=True,
        )

    behind = max(0, (expected_last_ts(now_ts, granularity) - info.last_ts) // step)
    return Freshness(
        product=info.product,
        granularity=granularity,
        n_candles=info.n_candles,
        last_ts=info.last_ts,
        bars_behind=behind,
        gaps=info.gaps,
        missing=False,
        stale=behind > tolerance_bars,
    )


def any_needs_fetch(items: list[Freshness]) -> bool:
    """Any series a fetch could actually help. See `Freshness.needs_fetch`."""
    return any(item.needs_fetch for item in items)


def any_gaps(items: list[Freshness]) -> bool:
    """Any series with internal holes. NOT repairable by `ensure_history` -- see above."""
    return any(item.gaps > 0 for item in items)
