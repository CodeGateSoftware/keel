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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from keel.data.history import GRANULARITY_SECONDS, CoverageInfo
from keel.types import Candle, Granularity

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


@dataclass(frozen=True)
class BarReadiness:
    """Is the bar `keel.agent`'s entry gate needs to trade on ACTUALLY DONE -- not merely
    cached, but confirmed closed by a finer series?

    This is a different question from `Freshness.stale`, and deliberately does not reuse
    `assess()`/`DEFAULT_TOLERANCE_BARS`: that tolerance exists so an operator-facing staleness
    ALERT does not fire on the normal forming-bar lag (see the module docstring). An ENTRY GATE
    on real money needs `bars_behind == 0` -- a 1-bar-late hourly series is exactly the condition
    that produces a duplicate real-money order (see `entry_bar_ready`'s docstring), and the
    alert tolerance would wave it through.
    """

    granularity: Granularity
    expected_ts: int
    stored_ts: int | None
    #: `-1` when nothing is stored at all, matching `Freshness.bars_behind`'s "unknown" sentinel.
    bars_behind: int
    #: The blocking FINER-than-`granularity` series that failed to confirm the close, or `None`
    #: if nothing blocked (either everything confirmed, or `granularity` itself was
    #: missing/behind and confirmation was never reached). When several finer series fail, this
    #: names the COARSEST of them -- see `entry_bar_ready`'s docstring for why.
    blocked_by: Granularity | None
    blocked_by_ts: int | None
    ready: bool
    #: `"missing" | "behind" | "unconfirmed" | None` (the last only when `ready`).
    reason: str | None


def entry_bar_ready(
    candles_by_tf: Mapping[Granularity, Sequence[Candle]],
    granularity: Granularity,
    now_ts: int,
) -> BarReadiness:
    """Is the newest `granularity` bar in `candles_by_tf` both CACHED and CONFIRMED CLOSED --
    safe for `keel.agent` to gate a real-money ENTRY on?

    **Why this exists (Finding 1, HIGH).** The live LaunchAgent fires at :20 past every UTC
    hour; the runner gates on UTC hour >= 1, so the first eligible trigger is 01:20 UTC.
    `turtle_breakout.py::_completed_days` withholds the just-closed ONE_DAY bar until the
    00:00-01:00 UTC ONE_HOUR bar has closed at 01:00 UTC -- normally a 20-minute margin. If the
    ONE_HOUR series is even one bar late (a common publication lag), `_completed_days` withholds
    ONE MORE daily bar than it should and the rule re-evaluates a bar it already traded
    yesterday. Nothing else on the live path dedupes an ENTRY (see `keel/agent.py`'s module
    docstring), so that re-evaluation is a DUPLICATE REAL-MONEY ORDER, not a delayed one.

    **The confirmation condition is `_completed_days`'s own condition, generalized.** At 01:20
    UTC, `expected_ts` for ONE_DAY is day X-1's 00:00, and `expected_ts + step` is day X's
    00:00 -- precisely the ts of the 00:00-01:00 UTC hourly bar `_completed_days` waits for. So
    requiring every FINER configured series to have a newest ts `>= expected_ts + step` is
    exactly `_completed_days`'s own wait, stated generically over whatever finer series the
    agent happens to poll (today ONE_HOUR; a future FIFTEEN_MINUTE-only deployment would confirm
    against that instead). It is therefore STRICTLY STRONGER than `_completed_days` -- it cannot
    read "ready" while `_completed_days` would still withhold the bar.

    **It is NOT over-strict.** The confirmation only demands that the finer series has crossed
    the boundary -- not that the finer series is itself at its own newest expected bar. At 14:20
    UTC an hourly series five bars behind ITS OWN expectation still confirms the daily bar fine,
    because it crossed the boundary hours ago. A `bars_behind == 0` requirement on ONE_HOUR (or
    FIFTEEN_MINUTE) would have been far too strict -- the 01:20 trigger gives only 5 minutes of
    margin on a 15-minute bar -- and would block entries routinely rather than only in the
    narrow post-midnight window this predicate targets.

    Order of checks, each short-circuiting the next:
      1. `stored_ts is None` (the key is absent, or the series is empty) -> `"missing"`.
      2. `bars_behind = max(0, (expected_ts - stored_ts) // step)`; `> 0` -> `"behind"`. The
         floor division is deliberately forgiving of a series whose bars are not aligned to the
         granularity boundary -- see `assess()`'s identical arithmetic; many existing callers
         build such series and a bar less than one full step behind is not "behind".
      3. Otherwise, every granularity `g` present in `candles_by_tf` with
         `GRANULARITY_SECONDS[g] < step` must have a newest stored ts `>= expected_ts + step`.
         A present-but-empty finer series fails this exactly like a late one -- there is nothing
         to confirm against. When several finer series fail, the COARSEST of them is reported
         (deterministic: same inputs always name the same blocker, so an operator's alert
         doesn't flap between messages across identical cycles).
    """
    gran = Granularity(granularity)
    step = GRANULARITY_SECONDS[gran]
    expected_ts = expected_last_ts(now_ts, gran)

    series = candles_by_tf.get(gran)
    stored_ts = series[-1].ts if series else None

    if stored_ts is None:
        return BarReadiness(
            granularity=gran,
            expected_ts=expected_ts,
            stored_ts=None,
            bars_behind=-1,
            blocked_by=None,
            blocked_by_ts=None,
            ready=False,
            reason="missing",
        )

    bars_behind = max(0, (expected_ts - stored_ts) // step)
    if bars_behind > 0:
        return BarReadiness(
            granularity=gran,
            expected_ts=expected_ts,
            stored_ts=stored_ts,
            bars_behind=bars_behind,
            blocked_by=None,
            blocked_by_ts=None,
            ready=False,
            reason="behind",
        )

    close_ts = expected_ts + step
    blockers: list[tuple[Granularity, int | None]] = []
    for g, candles in candles_by_tf.items():
        g = Granularity(g)
        if GRANULARITY_SECONDS[g] >= step:
            continue  # not FINER than `granularity` -- nothing to confirm against here
        newest = candles[-1].ts if candles else None
        if newest is None or newest < close_ts:
            blockers.append((g, newest))

    if blockers:
        blocker_gran, blocker_ts = max(blockers, key=lambda item: GRANULARITY_SECONDS[item[0]])
        return BarReadiness(
            granularity=gran,
            expected_ts=expected_ts,
            stored_ts=stored_ts,
            bars_behind=0,
            blocked_by=blocker_gran,
            blocked_by_ts=blocker_ts,
            ready=False,
            reason="unconfirmed",
        )

    return BarReadiness(
        granularity=gran,
        expected_ts=expected_ts,
        stored_ts=stored_ts,
        bars_behind=0,
        blocked_by=None,
        blocked_by_ts=None,
        ready=True,
        reason=None,
    )


def any_needs_fetch(items: list[Freshness]) -> bool:
    """Any series a fetch could actually help. See `Freshness.needs_fetch`."""
    return any(item.needs_fetch for item in items)


def any_gaps(items: list[Freshness]) -> bool:
    """Any series with internal holes. NOT repairable by `ensure_history` -- see above."""
    return any(item.gaps > 0 for item in items)
