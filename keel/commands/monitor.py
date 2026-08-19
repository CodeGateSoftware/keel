"""The polling service behind `keel monitor` -- one cycle, or the loop that repeats it.

Issue #387 C1 (the TUI-operator-console PRD, O2): the FR-9 session-aware poll loop lived inline
in `keel/cli.py`'s command body, so a second front-end (the TUI's Trading menu, C5) would have
had to re-implement the skip-while-closed cadence or lose it. It lives here now; the CLI wrapper
parses options, builds the broker at its `_build_broker` seam, and echoes.

`monitor_cycle` is the unit: record the venue session, then either skip (a shut venue mints no
bars) or poll fresh candles for every product, returning the line to show and the state the
once-per-state-change dedup needs. `run_monitor` is the loop: the same cycle repeated, with the
skip line emitted only on a session STATE CHANGE (a weekend is ~60 hourly ticks of the same
fact, and repeating it would train an operator to stop reading monitor output). Sleep and clock
are injected (`sleep_fn`/`now_fn`) so the loop is drivable under test without real time.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from keel_broker_api.results import SessionState

from keel import agent
from keel.config import Config
from keel.data import market_feed
from keel.data.repository import Repository
from keel.types import Granularity


@dataclass(frozen=True)
class MonitorCycle:
    """What one poll cycle did: the line to show, and the session bookkeeping for the next one."""

    #: The exact line the CLI prints for this cycle (`"[ts] polled N new candle row(s) ..."` or
    #: the once-per-state-change skip line). Never `None` -- a cycle always has something to
    #: say, because "did nothing and why" IS the output.
    line: str
    #: The session state this cycle recorded, to compare against the next cycle's (that
    #: comparison is the skip-line dedup). `None` for a venue with no session surface (24/7).
    session: SessionState | None
    session_bound: bool
    #: Candle rows the poll wrote (0 on a skipped cycle).
    written: int


def monitor_cycle(
    broker: Any,
    repo: Repository,
    config: Config,
    products: list[str],
    granularities: list[Granularity],
    now_ts: int,
    interval_sec: float,
) -> MonitorCycle:
    """One poll: record the session, then skip while a session-bound venue reports closed.

    FR-9 at the polling surface: while a session-bound venue reports closed, there is nothing
    to poll (a shut venue mints no bars), so the cycle skips -- but the session is still
    recorded, so `fetch --check` stays quiet over the weekend even when monitor is the only
    loop cycling. Crypto venues (no session surface) poll exactly as before --
    `record_market_session` records nothing for them.
    """
    session, session_bound = agent.record_market_session(
        broker, repo, config, now_ts, interval_sec=interval_sec
    )
    if session_bound and session is not SessionState.OPEN:
        reason = (
            "market closed -- skipping poll"
            if session is SessionState.CLOSED
            else "market clock unreadable (fail-closed) -- skipping poll"
        )
        return MonitorCycle(
            line=f"[{now_ts}] {reason}", session=session, session_bound=True, written=0
        )
    written = market_feed.poll_once(broker, repo, products, granularities, now_ts=now_ts)
    return MonitorCycle(
        line=f"[{now_ts}] polled {written} new candle row(s) across {products}",
        session=session,
        session_bound=session_bound,
        written=written,
    )


def run_monitor(
    broker: Any,
    repo: Repository,
    config: Config,
    products: list[str],
    granularities: list[Granularity],
    interval: float,
    *,
    loop: bool = False,
    max_cycles: int | None = None,
    echo: Callable[[str], None] = lambda _message: None,
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], int] = lambda: int(time.time()),
) -> list[MonitorCycle]:
    """Poll once (`loop=False`) or repeatedly, echoing each cycle's line as it happens.

    Returns every cycle's result, so a front-end that renders its own view can replay the same
    facts the CLI printed. `max_cycles` bounds the loop exactly as `--max-cycles` does.
    """
    cycles: list[MonitorCycle] = []
    last_session: SessionState | None = None
    while True:
        now_ts = now_fn()
        result = monitor_cycle(broker, repo, config, products, granularities, now_ts, interval)
        # The skip line is emitted ONCE PER STATE CHANGE: `last_session` tracks what was last
        # SAID, so an unchanged weekend stays quiet while a closed -> open transition (or the
        # reverse) always speaks. An open/non-session-bound cycle resets the tracker, so the
        # next closure announces itself again.
        if result.session_bound and result.session is not SessionState.OPEN:
            if result.session is not last_session:
                echo(result.line)
                last_session = result.session
        else:
            last_session = result.session if result.session_bound else None
            echo(result.line)
        cycles.append(result)
        if not loop or (max_cycles is not None and len(cycles) >= max_cycles):
            break
        sleep_fn(interval)
    return cycles
