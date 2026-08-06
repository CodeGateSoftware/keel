"""The live detector's SCHEDULE -- `keel-live-run.sh`'s gate and `com.keel.live.plist`'s triggers.

**Why this file exists at all.** The schedule is not notification hygiene, it is a CORRECTNESS
mechanism, and until now nothing tested it. `keel-live-run.sh`'s day-stamp is the ONLY thing
standing between the live money path and entering the same daily signal twice: nothing downstream
dedupes an entry (`get_open_positions` gates exit/reconcile/status but never entry, the `signals`
table is never read back, `client_order_id` is a fresh uuid4 per call, and the rails in
`execution/guards.py` are dollar caps, not per-day counters). The paper path DOES gate
(`strategy/paper.py` refuses a second entry while the product is already open); the live path does
not. So "how many times can a trigger fire and still produce one entry" is a property worth
pinning in tests, not a shell script nobody exercises.

**What changed, and why the schedule is anchored to UTC.** Daily candles close at 00:00 UTC, but
`turtle_breakout._completed_days` will not surface the just-closed daily bar until the
00:00-01:00 UTC HOURLY bar has closed -- i.e. not before 01:00 UTC (that guard exists to stop the
account simulator consuming a still-forming day as if it were complete; see its docstring). So
01:00 UTC is the earliest instant at which a cycle sees fresh data. The runner used to stamp and
gate on the LOCAL date with `SCHED_HOUR=9`, which put the first eligible cycle at 09:00
America/New_York = 13:00/14:00 UTC -- roughly twelve hours of avoidable lag on every breakout.

**The invariant these tests protect.** For any UTC date X, the newest STORED daily bar is
constant -- it is X-1 -- across the whole eligible window [01:00 UTC, 24:00 UTC). So whichever
eligible trigger fires first on UTC date X evaluates bar X-1 and stamps X; every later trigger
that UTC day is a no-op. Every daily bar is evaluated exactly once: no missed day, no double day.
Anchoring the stamp to the UTC date is what makes the window and the bar line up; anchoring it to
the LOCAL date does not, because a local day straddles two UTC dates (see
`test_the_old_local_date_gate_could_double_run_within_one_utc_day`).

That widening is only safe BECAUSE the stamp holds. The plist now fires 24 times a day instead of
12; the trigger count is CATCH-UP BREADTH, not cadence. launchd re-runs a missed
StartCalendarInterval on wake from sleep but NOT when the trigger passed while the machine was
powered off, so the only defence against a shutdown eating a day is having another trigger later
-- and the catch-up window grew from 12h to 23h.

**Publication lag (the premise nothing tested before this file).** All of the above assumes
instantaneous, never-failing candle publication. `_stored_series` used to hard-code that
assumption; it no longer does (`hourly_lag_bars`/`daily_lag_bars`), and
`test_publication_lag_can_regress_the_effective_bar_except_at_the_old_late_schedule` proves the
premise of the whole late-candle bug class: a one-bar-late feed at 01:20 UTC REGRESSES the
effective daily bar by a full day (re-entering a bar yesterday's cycle already traded), while the
same lag at the OLD 13:05 UTC schedule does not, because that schedule traded ~13h of publication
margin for the ~20 minutes this one runs on.

**Pinning the model to the shipped script (Finding 6).** The gate-simulation tests below
(`_run_gate` and everything built on it) are a PURE PYTHON MODEL of `keel-live-run.sh`'s two
guards -- deliberately, so a 13-month/9600-trigger simulation costs milliseconds instead of
minutes. That is only trustworthy to the extent the model and the shipped script actually agree,
which nothing enforced until `test_the_simulated_gate_matches_the_real_script`: it drives the REAL
script, under a shimmed clock, over a curated adversarial sequence (normal days, the sub-threshold
trigger, both DST transitions, and the boot-after-outage catch-up path) and asserts the model's
prediction matches the script's actual behaviour at every single trigger. Everything else in this
file that exercises the real script (`_sandbox`/`_run`) also runs it VERBATIM -- never a
reimplementation -- through a harness that shims `date` (so tests do not depend on, or wait on,
the wall clock) and records rather than fires macOS notifications (`$OSASCRIPT` is redirected to a
recorder, and every invocation additionally runs under `sandbox-exec` denying the real binary
outright, so a test run can never pop a real notification on a machine that also trades real
money).
"""

from __future__ import annotations

import os
import plistlib
import re
import stat
import subprocess
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from keel.strategy.rules.turtle_breakout import _completed_days
from keel.types import Candle, Granularity

REPO_ROOT = Path(__file__).resolve().parent.parent
PLIST = REPO_ROOT / "com.keel.live.plist"
RUN_SCRIPT = REPO_ROOT / "keel-live-run.sh"

#: The wall clock the LaunchAgent actually runs on. launchd interprets StartCalendarInterval in
#: the machine's LOCAL zone, and this deployment's machine is America/New_York -- so that is the
#: zone the schedule has to survive, DST transitions and all.
DEPLOYMENT_TZ = "America/New_York"

#: The gate `keel-live-run.sh` applies, mirrored here. Kept in sync by
#: `test_run_script_gate_constants_match_the_simulated_gate`, which parses it out of the script.
SCHED_HOUR = 1

_HOUR = 3600
_DAY = 86_400

# US DST transitions during the simulated window, in the deployment zone. Spring-forward skips
# local 02:00-02:59 (one trigger cannot happen); fall-back repeats local 01:00-01:59 (one trigger
# happens twice). Both are asserted explicitly -- they are the two days the schedule is most
# likely to be wrong on, so a test that only checked "roughly one run a day" would miss them.
SPRING_FORWARD = date(2026, 3, 8)
FALL_BACK = date(2026, 11, 1)


# -- the plist's triggers, parsed rather than hardcoded ---------------------------------------


def _plist_triggers() -> list[tuple[int, int]]:
    """Every `StartCalendarInterval` entry in the shipped plist as `(hour, minute)`.

    PARSED, never hardcoded: if these tests carried their own copy of the trigger list, the plist
    and the property it is supposed to satisfy could drift apart silently, which is exactly the
    failure this file exists to prevent.
    """
    data = plistlib.loads(PLIST.read_bytes())
    return [(entry["Hour"], entry["Minute"]) for entry in data["StartCalendarInterval"]]


def test_plist_fires_every_hour_on_the_hour_twenty() -> None:
    """24 triggers, one per local hour, all at :20.

    :20 rather than :00 buys twenty minutes of margin for Coinbase to publish and for
    `data.market_feed` to persist the 00:00-01:00 UTC hourly candle, which is the candle that
    releases the fresh daily bar. A trigger at exactly 01:00 UTC would race the very bar it is
    there to consume.

    The COUNT is catch-up breadth, not cadence -- `keel-live-run.sh`'s stamp is what keeps 24
    triggers to one cycle. More triggers only means a shorter outage is survivable.
    """
    triggers = _plist_triggers()
    assert sorted(triggers) == [(hour, 20) for hour in range(24)]


def test_plist_is_well_formed_xml() -> None:
    """The plist must parse with a STRICT XML parser, not merely with Apple's lenient one.

    Found the hard way while writing this file. XML forbids a double hyphen inside a comment, and
    this repo's prose style puts one in every other sentence. `plutil -lint` said OK, because
    CFPropertyList tolerates it, so the malformed file shipped and launchd never complained -- but
    `plistlib` (expat) rejected it outright. Anything that reads the file with an ordinary XML
    parser, this test included, breaks on it.

    `_plist_triggers` already fails if this regresses; the point of a dedicated test is that the
    failure then says WHAT is wrong instead of pointing at a trigger-count assertion.
    """
    plistlib.loads(PLIST.read_bytes())


def test_plist_still_runs_at_load() -> None:
    """`RunAtLoad` is half the catch-up story and must not be dropped along with the rest.

    A boot that happens between triggers must catch the day up immediately rather than waiting up
    to an hour; the stamp makes a repeated load harmless.
    """
    data = plistlib.loads(PLIST.read_bytes())
    assert data["RunAtLoad"] is True


# -- the 01:00 UTC boundary the schedule is anchored to ---------------------------------------


def _candle(ts: int) -> Candle:
    price = 100
    return Candle(ts=ts, open=price, high=price, low=price, close=price, volume=1)


def _stored_series(
    now_utc: datetime, *, hourly_lag_bars: int = 0, daily_lag_bars: int = 0
) -> dict[Granularity, list[Candle]]:
    """The candle series `data.market_feed` would have persisted as of `now_utc`.

    market_feed stores only CLOSED candles, so a bar with timestamp `t` and width `w` is present
    exactly once `now >= t + w`. Deriving both series from that one rule -- rather than hand-
    writing a series per parametrised time -- is what makes this test about the CLOCK and not
    about my arithmetic.

    `hourly_lag_bars`/`daily_lag_bars` model a feed that is N bars BEHIND what instantaneous
    publication would have produced -- e.g. Coinbase, or `data.market_feed` itself, running late.
    Zero (the default, and today's assumption everywhere this was called before) reproduces
    instantaneous, never-failing publication. That assumption is exactly what made the whole
    late-candle bug class invisible: see
    `test_publication_lag_can_regress_the_effective_bar_except_at_the_old_late_schedule`.
    """
    now = int(now_utc.timestamp())
    last_hour = (now // _HOUR - 1 - hourly_lag_bars) * _HOUR
    last_day = (now // _DAY - 1 - daily_lag_bars) * _DAY
    return {
        Granularity.ONE_DAY: [_candle(last_day - n * _DAY) for n in reversed(range(5))],
        Granularity.ONE_HOUR: [_candle(last_hour - n * _HOUR) for n in reversed(range(5))],
    }


@pytest.mark.parametrize(
    ("hour", "minute", "sees_fresh_bar"),
    [
        (0, 5, False),   # the old "just after midnight" instinct -- still a day behind
        (0, 59, False),  # one minute short: the 00:00-01:00 UTC hourly bar has not closed
        (1, 0, True),    # the boundary itself: that hourly bar closes AT 01:00
        (1, 20, True),   # the plist's first eligible trigger, with its publication margin
        (2, 20, True),
        (13, 5, True),   # where the OLD schedule sat -- correct, but ~12h late
    ],
)
def test_effective_bar_does_not_advance_until_0100_utc(
    hour: int, minute: int, sees_fresh_bar: bool
) -> None:
    """`_completed_days` releases the just-closed daily bar at 01:00 UTC, not at 00:00 UTC.

    This is the fact the whole schedule is built on, so it is pinned here rather than inferred.
    The daily bar for UTC date X-1 closes at 00:00 UTC on X and is stored immediately -- but
    `_completed_days` drops the newest daily bar until the newest HOURLY bar opens at or after
    that day's close, and the 00:00-01:00 UTC hourly bar does not close until 01:00 UTC. So a
    cycle run at 00:05 UTC is still deciding on bar X-2: newer than the old 13:05 UTC schedule
    saw, but a full day stale.

    `SCHED_HOUR=1` in `keel-live-run.sh` is exactly this boundary. If this test ever fails, the
    schedule's premise has moved and `SCHED_HOUR` must move with it -- do not just re-baseline
    the expectations here. Uses instantaneous publication (`_stored_series`'s default of zero
    lag); the effect of LATE publication on this same boundary is
    `test_publication_lag_can_regress_the_effective_bar_except_at_the_old_late_schedule`.
    """
    now = datetime(2026, 6, 15, hour, minute, tzinfo=UTC)
    series = _stored_series(now)

    newest_stored_day = series[Granularity.ONE_DAY][-1].ts
    effective = _completed_days(series)[-1].ts

    if sees_fresh_bar:
        assert effective == newest_stored_day
    else:
        assert effective == newest_stored_day - _DAY


def test_publication_lag_can_regress_the_effective_bar_except_at_the_old_late_schedule() -> None:
    """The premise of the WHOLE late-candle problem, against the REAL `_completed_days`.

    Everything above this test assumes `data.market_feed` publishes instantly. It does not always:
    Coinbase can be slow, or the feed job can lag. This test proves what happens when it does, at
    the two schedules that matter:

      - At 01:20 UTC with both series CURRENT, the effective newest daily bar is X-1 (pinned by
        `test_effective_bar_does_not_advance_until_0100_utc` already).
      - At 01:20 UTC with the HOURLY series one bar behind, it REGRESSES to X-2.
      - At 01:20 UTC with the DAILY series one bar behind, it ALSO regresses to X-2.
      - At 13:05 UTC -- where the OLD local-anchored schedule sat -- the SAME one-bar hourly lag
        does NOT regress it; there is enough margin (~13h) to absorb it.

    That last case is the point of this test existing: the PR that moved the schedule to 01:20 UTC
    traded roughly thirteen hours of publication margin for twenty minutes of it. A regression to
    X-2 is not a cosmetic staleness issue -- re-evaluating bar X-2 means re-entering the SAME
    breakout that YESTERDAY's 01:20 UTC cycle already evaluated and (if it broke out) already
    traded, on the live money path, where nothing downstream dedupes an entry (see the module
    docstring). `keel/agent.py::run_once` is gaining a gate (by another worker, on this same
    branch) that refuses to re-enter while a position from that same bar is already open --
    referenced here by BEHAVIOUR, not by import, since that file is not owned by this change --
    and that gate is exactly the backstop this test's regression case would otherwise defeat.
    """
    at_0120 = datetime(2026, 6, 15, 1, 20, tzinfo=UTC)
    at_1305 = datetime(2026, 6, 15, 13, 5, tzinfo=UTC)

    current_at_0120 = _completed_days(_stored_series(at_0120))[-1].ts

    hourly_lagged_at_0120 = _completed_days(_stored_series(at_0120, hourly_lag_bars=1))[-1].ts
    assert hourly_lagged_at_0120 == current_at_0120 - _DAY, (
        "a one-bar-late HOURLY feed at 01:20 UTC must regress the effective daily bar by a full "
        "day -- that is the entry point of the whole late-candle bug"
    )

    daily_lagged_at_0120 = _completed_days(_stored_series(at_0120, daily_lag_bars=1))[-1].ts
    assert daily_lagged_at_0120 == current_at_0120 - _DAY, (
        "a one-bar-late DAILY feed at 01:20 UTC must regress the effective daily bar too"
    )

    current_at_1305 = _completed_days(_stored_series(at_1305))[-1].ts
    hourly_lagged_at_1305 = _completed_days(_stored_series(at_1305, hourly_lag_bars=1))[-1].ts
    assert hourly_lagged_at_1305 == current_at_1305, (
        "the SAME one-bar hourly lag at 13:05 UTC -- the old schedule's hour -- must NOT regress "
        "the effective bar: ~13h of margin absorbs it. This is the margin the 01:20 UTC schedule "
        "gave up in exchange for evaluating breakouts ~13h sooner every day it is NOT late."
    )


# -- the schedule gate, simulated over a full year --------------------------------------------
#
# Deliberately PURE: no keel imports, no database, no clock. The gate is four lines of shell and
# the only interesting thing about it is how it behaves across thousands of triggers and two DST
# transitions, which is a property you can only see by simulating it. It is a MODEL, not the
# shipped script -- trustworthy only because `test_the_simulated_gate_matches_the_real_script`,
# below, pins it to the real `keel-live-run.sh` over a curated adversarial sequence. If you change
# the gate's shape, that is the test that must keep passing; do not "fix" a mismatch by editing
# the model in isolation.


def _utc_instants(local_naive: datetime, tz: ZoneInfo) -> list[datetime]:
    """Every real UTC instant at which a wall clock in `tz` reads `local_naive`.

    Three cases, and the DST ones are the whole point:
      - normal        -> exactly one instant.
      - AMBIGUOUS     -> two (fall-back repeats an hour). Modelled as firing TWICE, the
                         pessimistic reading of launchd: assuming one firing would let the test
                         pass while the real scheduler double-fired.
      - NON-EXISTENT  -> none (spring-forward skips an hour, and a wall clock never shows it).

    `fold` disambiguates: for a real ambiguous time both folds round-trip back to `local_naive`;
    for a non-existent time neither does.
    """
    first = local_naive.replace(tzinfo=tz, fold=0).astimezone(UTC)
    second = local_naive.replace(tzinfo=tz, fold=1).astimezone(UTC)
    if first == second:
        return [first]
    if first.astimezone(tz).replace(tzinfo=None) == local_naive:
        return [first, second]  # ambiguous: both firings are real
    return []  # non-existent: this wall-clock time never occurs


def _triggers(
    start: date, end: date, tz: ZoneInfo, schedule: list[tuple[int, int]]
) -> list[tuple[datetime, datetime]]:
    """Every `(utc_instant, local_instant)` the LaunchAgent fires, in UTC order.

    Sorted by UTC instant because that is the order the machine actually executes them in; the
    gate is a fold over that sequence and would be meaningless in local order across a fall-back.
    """
    fired: list[tuple[datetime, datetime]] = []
    day = start
    while day <= end:
        for hour, minute in schedule:
            local_naive = datetime(day.year, day.month, day.day, hour, minute)
            for instant in _utc_instants(local_naive, tz):
                fired.append((instant, local_naive.replace(tzinfo=tz)))
        day += timedelta(days=1)
    return sorted(fired, key=lambda pair: pair[0])


def _run_gate(
    triggers: list[tuple[datetime, datetime]], sched_hour: int, *, utc_anchored: bool
) -> list[datetime]:
    """Replay `keel-live-run.sh`'s two guards over `triggers`; return the UTC instants that RAN a
    cycle (i.e. invoked keel) -- NOT whether the script exited 0, 67, or anything else; see the
    note below on why that distinction is deliberately outside what this model represents.

    The shell's gate, in Python, as of the hardened script:

        if [ -n "$STAMPED" ] && [[ "$STAMPED" > "$TODAY" ]]; then exit 67; fi  # ahead: alert
        if [ -n "$STAMPED" ] && [ "$STAMPED" = "$TODAY" ]; then exit 0; fi     # already ran
        if [ "$HOUR" -lt "$SCHED_HOUR" ]; then exit 0; fi                     # too early

    Both `STAMPED > TODAY` (Defect 1's forward-excursion alert, exit 67) and `STAMPED == TODAY`
    (the ordinary already-ran case, exit 0) leave the stamp untouched and never invoke keel, so
    both collapse into the same `not (stamp < clock.date())` test below for the purpose of "did a
    cycle run" -- this model is deliberately blind to WHICH of the two no-run outcomes fired, since
    only the shipped script's exit code and notifications (not this pure model) distinguish them.
    That distinction is covered against the REAL script by
    `test_forward_clock_excursion_escalates_instead_of_silently_skipping` and
    `test_clock_rollback_does_not_rerun_or_move_the_stamp_backwards`.

    `utc_anchored=False` reproduces the OLD (pre-UTC-anchoring) behaviour (both `TODAY` and `HOUR`
    from LOCAL time), so the two can be compared on identical trigger lists. Every cycle here is
    assumed to succeed -- a failed cycle writes no stamp and is retried, which only ever ADDS a
    later run on the same date, never removes one.

    CORRECTION: an earlier version of this docstring claimed the strict `<` compare (this PR) and
    the pre-PR-174 `==` compare "diverge only under a clock rollback". That was FALSE, and the
    false claim is precisely why nothing here covered the other direction before now: a FORWARD
    clock excursion (a bad RTC reading a bogus future date before NTP settles) also makes the two
    diverge, and in the more dangerous direction -- `==` self-heals the very next real day (the
    bogus stamp no longer equals today, so the cycle runs and re-stamps correctly), `<` does not
    self-heal at all, ever, until the real clock catches up to the bogus future stamp, which could
    be years. See `test_forward_clock_excursion_escalates_instead_of_silently_skipping` for that
    case driven against the real script; both directions of divergence (rollback and forward
    excursion) are covered now.
    """
    ran: list[datetime] = []
    stamp: date | None = None
    for utc_instant, local_instant in triggers:
        clock = utc_instant if utc_anchored else local_instant
        if stamp is not None and not (stamp < clock.date()):
            continue
        if clock.hour < sched_hour:
            continue
        ran.append(utc_instant)
        stamp = clock.date()
    return ran


@pytest.mark.parametrize(
    "tz_name",
    [
        DEPLOYMENT_TZ,      # the one that actually matters
        "Pacific/Auckland",  # southern-hemisphere DST, transitions on the other side of the year
        "Asia/Kolkata",      # a half-hour offset and no DST at all
    ],
)
def test_exactly_one_run_per_utc_day_over_a_full_year(tz_name: str) -> None:
    """Over 13 months of real triggers, every UTC date gets exactly one cycle -- no more, no less.

    THIS TESTS A MODEL, not the shipped script (see the section banner above and the module
    docstring) -- driving the real script over 13 months/~9600 triggers was measured to add
    minutes to a sub-second suite. Trusting this test's result as a statement about
    `keel-live-run.sh` is only valid because `test_the_simulated_gate_matches_the_real_script`
    pins `_run_gate` to the real script over a curated adversarial sequence; if that test and this
    one ever disagree in spirit, believe the real-script test.

    Combined with `test_effective_bar_does_not_advance_until_0100_utc` (the newest visible daily
    bar is constant across the entire eligible window), "exactly one run per UTC date" means
    "every daily bar evaluated exactly once". Two runs on one UTC date would be a DUPLICATE ENTRY
    on the live money path, because nothing downstream dedupes one. Zero runs would be a silently
    skipped trading day.

    Extra zones beyond the deployment's are there to show the property comes from the STRUCTURE of
    the schedule -- 24 triggers a day means every UTC hour gets one, so an eligible hour always
    exists -- and not from a lucky UTC offset.
    """
    tz = ZoneInfo(tz_name)
    triggers = _triggers(date(2026, 1, 1), date(2027, 1, 31), tz, _plist_triggers())
    ran = _run_gate(triggers, SCHED_HOUR, utc_anchored=True)

    assert all(instant.hour >= SCHED_HOUR for instant in ran), (
        "a cycle ran before 01:00 UTC, when `_completed_days` is still withholding the fresh "
        "daily bar -- it would have re-evaluated yesterday's bar"
    )

    ran_dates = [instant.date() for instant in ran]
    assert len(ran_dates) == len(set(ran_dates)), "some UTC date ran more than once"

    # The first and last UTC dates the window touches are only PARTIALLY covered by it (a local
    # day straddles two UTC dates, so the ends are ragged), so nothing is claimed about them
    # either way. Every UTC date strictly inside must have run, and nothing outside the window
    # may have.
    covered = sorted({instant.date() for instant, _ in triggers})
    interior = covered[1:-1]
    assert set(interior) <= set(ran_dates), "a UTC date inside the window got no cycle at all"
    assert set(ran_dates) <= set(covered)

    # ...and `interior` itself must be an unbroken run of dates, or "every date in it ran" would
    # be vacuously satisfiable by a set with holes in it.
    assert interior == [
        interior[0] + timedelta(days=n) for n in range((interior[-1] - interior[0]).days + 1)
    ]
    assert len(interior) > 365, "the window must span more than a year to cover both transitions"


def test_spring_forward_loses_a_trigger_and_still_runs_exactly_once() -> None:
    """The day local 02:00-02:59 does not exist: 23 triggers, still one cycle.

    Losing a trigger is harmless here because the gate needs only ONE eligible trigger per UTC
    date and the day still supplies 23. It is asserted explicitly anyway -- a schedule that
    happened to place its only eligible trigger inside the skipped hour would fail silently, and
    the year-long simulation above would report it as "a UTC date got no cycle" without ever
    naming DST as the cause.
    """
    tz = ZoneInfo(DEPLOYMENT_TZ)
    triggers = _triggers(SPRING_FORWARD, SPRING_FORWARD, tz, _plist_triggers())

    assert len(triggers) == 23
    assert not any(local.hour == 2 for _, local in triggers)

    # Replayed from the previous UTC date so the stamp is realistically warm, then counted only
    # over the UTC dates this local day touches.
    window = _triggers(SPRING_FORWARD - timedelta(days=1), SPRING_FORWARD, tz, _plist_triggers())
    ran = _run_gate(window, SCHED_HOUR, utc_anchored=True)
    dates = [instant.date() for instant in ran]
    assert dates == sorted(set(dates))


def test_fall_back_repeats_a_trigger_and_still_runs_exactly_once() -> None:
    """The day local 01:00-01:59 happens TWICE: 25 triggers, still one cycle.

    This is the dangerous direction. Local 01:20 occurs at two distinct UTC instants -- 05:20 UTC
    (EDT) and 06:20 UTC (EST) -- and both land on the SAME UTC date, so a naive gate keyed on
    anything but the UTC date can run the cycle twice within one UTC day and enter the same signal
    twice on the live money path. The UTC-anchored stamp collapses them: the first stamps the
    date, the second is a no-op.
    """
    tz = ZoneInfo(DEPLOYMENT_TZ)
    triggers = _triggers(FALL_BACK, FALL_BACK, tz, _plist_triggers())

    assert len(triggers) == 25
    repeated = [utc for utc, local in triggers if local.hour == 1]
    assert len(repeated) == 2, "the repeated local hour must be modelled as firing twice"
    assert {utc.hour for utc in repeated} == {5, 6}
    assert repeated[0].date() == repeated[1].date(), (
        "both firings land on one UTC date -- which is precisely why the stamp must be UTC-keyed"
    )

    window = _triggers(FALL_BACK - timedelta(days=1), FALL_BACK, tz, _plist_triggers())
    ran = _run_gate(window, SCHED_HOUR, utc_anchored=True)
    dates = [instant.date() for instant in ran]
    assert len(dates) == len(set(dates)), "the repeated hour produced a second cycle"


def test_the_old_local_date_gate_could_double_run_within_one_utc_day() -> None:
    """Regression: the OLD local-date gate ran twice on one UTC date after an outage.

    Kept so the reason the stamp became UTC-keyed is a failing-if-reverted test rather than a
    paragraph in a commit message.

    In steady state the old gate looked fine -- 09:05 local is 13:05/14:05 UTC, same date, one run
    a day. It broke on the CATCH-UP path, which is the entire reason the hourly triggers exist. If
    the machine is off until late in a local day, the old gate's first eligible trigger is a late
    local hour, and a late local hour in America/New_York is already the NEXT UTC date. The
    following local morning then runs again -- on that same UTC date. Two cycles, one daily bar,
    and nothing downstream to dedupe the second entry. The mirror-image bug comes free: the UTC
    date before it got no cycle at all.

    The UTC-anchored gate collapses the pair to one run per UTC date on the identical triggers.
    """
    tz = ZoneInfo(DEPLOYMENT_TZ)
    schedule = _plist_triggers()

    # Machine powered off through 2026-01-15 local; the first trigger it ever sees is 20:20 local,
    # which is already 2026-01-16 01:20 UTC.
    all_triggers = _triggers(date(2026, 1, 15), date(2026, 1, 16), tz, schedule)
    boot = datetime(2026, 1, 16, 1, 20, tzinfo=UTC)
    after_boot = [pair for pair in all_triggers if pair[0] >= boot]

    old = _run_gate(after_boot, 9, utc_anchored=False)
    old_dates = [instant.date() for instant in old]
    assert old_dates == [date(2026, 1, 16), date(2026, 1, 16)], (
        "the old local-date gate is expected to run twice on 2026-01-16 UTC -- if this no longer "
        "reproduces, the schedule changed and this regression needs rewriting, not deleting"
    )

    new = _run_gate(after_boot, SCHED_HOUR, utc_anchored=True)
    new_dates = [instant.date() for instant in new]
    assert new_dates == sorted(set(new_dates))
    assert new_dates[0] == date(2026, 1, 16)


# -- the shipped shell script ------------------------------------------------------------------


def _script_constant(name: str) -> str:
    """Pull a top-level `NAME="value"` or `NAME=value` assignment out of `keel-live-run.sh`."""
    match = re.search(rf'^{name}="?([^"\n]*)"?$', RUN_SCRIPT.read_text(), re.MULTILINE)
    assert match is not None, f"{name} is no longer assigned at the top level of {RUN_SCRIPT.name}"
    return match.group(1)


def test_run_script_gate_constants_match_the_simulated_gate() -> None:
    """The script's `SCHED_HOUR` and the one this file simulates are the same number.

    Without this, every simulation above could keep passing while the deployed script gated on a
    different hour entirely.
    """
    assert int(_script_constant("SCHED_HOUR")) == SCHED_HOUR


def test_run_script_reads_the_clock_in_utc() -> None:
    """The RAW clock reads (`TODAY_RAW`, `HOUR_RAW`) -- and therefore `TODAY`/`HOUR` -- come from
    `date -u`, never local time.

    The stamp and the hour gate MUST agree on which clock they are on. A UTC hour compared against
    a locally-stamped date would gate on one calendar and dedupe on another -- the worst of both,
    and it would not show up in the pure simulation above because that simulation gets its clock
    from a parameter rather than from the script. `TODAY`/`HOUR` are validated derivatives of
    `TODAY_RAW`/`HOUR_RAW` (see A1 in the script's header), not independent `date` calls, so
    pinning the two RAW reads is what actually pins the clock source.
    """
    source = RUN_SCRIPT.read_text()
    assert re.search(r"^TODAY_RAW=\"\$\(date -u '\+%Y-%m-%d'\)\"$", source, re.MULTILINE)
    assert re.search(r"^HOUR_RAW=\"\$\(date -u '\+%H'\)\"$", source, re.MULTILINE)
    # 10# forces base 10: `date +%H` yields 08/09, which arithmetic would otherwise read as octal.
    assert re.search(r'^HOUR="\$\(\(10#\$HOUR_RAW\)\)"$', source, re.MULTILINE)


# -- harness: run the REAL script under a shimmed clock, with notifications recorded -----------
#
# Every test below this point executes `keel-live-run.sh` VERBATIM (only `DIR=` and the literal
# osascript path are rewritten) so nothing here can drift from what actually ships. `_sandbox`
# builds the sandbox; `_run` fires one invocation of it at a chosen UTC instant.


@dataclass(frozen=True)
class Sandbox:
    """Everything one `_sandbox(...)` call sets up, handed to `_run` for each invocation."""

    script: Path
    stamp: Path
    outlog: Path
    pendlog: Path
    calls_log: Path         # every argument string any "notification" call made, one per line
    invocations_log: Path   # one line per time the $KEEL stub actually ran, OUTSIDE logs/ on
    #                         purpose -- the pre-flight test makes logs/ unwritable, and "was
    #                         keel invoked at all" must stay observable even then.
    env: dict[str, str]


#: Denies exec of the REAL notification binary at the OS level, independent of whatever the
#: script calls it by. This is defense IN DEPTH on top of the `/usr/bin/osascript` rewrite in
#: `_sandbox`: even if that rewrite ever fails to match (as it briefly must while TDD-red-testing
#: a not-yet-fixed script that does not use the `$OSASCRIPT` seam at all), this is what stands
#: between a test run and a real notification popping up on a machine that also trades real money.
_SANDBOX_EXEC = "/usr/bin/sandbox-exec"
_DENY_OSASCRIPT_PROFILE = (
    '(version 1)(allow default)(deny process-exec (literal "/usr/bin/osascript"))'
)


def _run_script(script: Path, *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run `script` the way launchd would, except sandboxed against ever notifying for real.

    Every real-script invocation in this file goes through here (via `_run`) rather than calling
    `subprocess.run` directly, so there is exactly one place that could forget the sandbox.
    """
    return subprocess.run(
        [_SANDBOX_EXEC, "-p", _DENY_OSASCRIPT_PROFILE, "/bin/bash", str(script)],
        capture_output=True,
        text=True,
        env=env,
    )


def _install_date_shim(bin_dir: Path) -> None:
    """Install a `date` on `PATH` that reads its instant from `$KEEL_TEST_NOW` (epoch seconds)
    instead of the wall clock, plus two failure modes A1 needs: `KEEL_TEST_DATE_MODE=empty`
    (produces no output -- the empty-`date -u` case this hardware can produce) and `=garbage`
    (produces non-date text).

    Honours ONLY the invocation forms `keel-live-run.sh` actually uses (`date -u '+FORMAT'`) --
    this is deliberately not a general `date(1)` replacement.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "date"
    shim.write_text(
        "#!/bin/bash\n"
        'case "${KEEL_TEST_DATE_MODE:-fixed}" in\n'
        "  empty) exit 0 ;;\n"
        '  garbage) printf "%s\\n" "${KEEL_TEST_DATE_GARBAGE:-not-a-date}"; exit 0 ;;\n'
        '  *) exec /bin/date -u -r "$KEEL_TEST_NOW" "$@" ;;\n'
        "esac\n"
    )
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)


def _redirect_osascript(source: str, recorder: Path) -> str:
    """Point the script's ONE reference to the real notification binary at a recorder instead.

    A plain string substitution rather than a regex on an `OSASCRIPT=` assignment, deliberately:
    it works identically whether the script hardcodes the literal path inline (the pre-fix shape)
    or holds it in the `$OSASCRIPT` constant (the fixed shape, A6) -- so the SAME harness code
    runs the TDD-red captures against the unmodified script and the green checks against the
    fixed one, with no separate code path to trust.
    """
    count = source.count("/usr/bin/osascript")
    assert count == 1, (
        "expected exactly one literal reference to /usr/bin/osascript -- if this drifted, a new "
        "notification path may call the real binary unrewritten, which must never happen in a test"
    )
    return source.replace("/usr/bin/osascript", str(recorder))


def _sandbox(
    tmp_path: Path,
    keel_exit_code: int,
    *,
    signals: int = 0,
    keel_stub_extra: str = "",
) -> Sandbox:
    """Copy `keel-live-run.sh` into `tmp_path`, repointed at the sandbox and wired for testing.

    Two rewrites, both load-bearing for safety, not just convenience:
      - `DIR="..."` -> `tmp_path`: the gate, the stamp and the exit handling all run VERBATIM,
        which is the point -- a test that reimplemented them would prove nothing about the script
        that actually ships.
      - the literal `/usr/bin/osascript` -> a recorder script (see `_redirect_osascript`), so
        tests can assert BOTH that a machine-is-broken condition alerts and that an ordinary
        self-healing one does not, without ever risking a real notification.

    `signals`/`keel_stub_extra` customise the `$KEEL` stub: `signals` controls the `signals=N` the
    stub's fake LoopResult line reports, and `keel_stub_extra` is shell text spliced in right
    before the stub exits, so a test can make "a cycle ran" also DO something observable -- the
    atomic-stamp tests use it to `chflags uchg` the stamp mid-cycle, simulating a write that fails
    only after the pre-flight probe (a differently-named file) already passed.
    """
    source = RUN_SCRIPT.read_text()
    patched, count = re.subn(
        r'^DIR="[^"]*"$', f'DIR="{tmp_path}"', source, count=1, flags=re.MULTILINE
    )
    assert count == 1, "could not repoint DIR -- refusing to run a script aimed at the deployment"

    calls_log = tmp_path / "osascript-calls.log"
    recorder = tmp_path / "osascript-recorder.sh"
    recorder.write_text(f'#!/bin/bash\nprintf "%s\\n" "$*" >> "{calls_log}"\nexit 0\n')
    recorder.chmod(recorder.stat().st_mode | stat.S_IEXEC)
    patched = _redirect_osascript(patched, recorder)

    # Belt and braces. This test executes a shell script that, unmodified, drives REAL MONEY
    # against ~/keel. If any reference to that path survives the rewrite, do not run it.
    assert "/Users/elmehdiaitbrahim/keel" not in patched

    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    stub_dir = tmp_path / ".venv" / "bin"
    stub_dir.mkdir(parents=True, exist_ok=True)
    stub = stub_dir / "keel"
    invocations_log = stub_dir / "keel.invocations"
    stub.write_text(
        "#!/bin/bash\n"
        f'printf "%s\\n" "$*" >> "{invocations_log}"\n'
        f"printf 'mode=confirm signals={signals}\\n'\n"
        f"{keel_stub_extra}\n"
        f"exit {keel_exit_code}\n"
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

    script = tmp_path / "keel-live-run.sh"
    script.write_text(patched)

    date_bin = tmp_path / "shim-bin"
    _install_date_shim(date_bin)

    env = dict(os.environ)
    env["PATH"] = f"{date_bin}:{env.get('PATH', '')}"

    return Sandbox(
        script=script,
        stamp=tmp_path / "logs" / ".keel-live-last-run",
        outlog=tmp_path / "logs" / "keel-live.out.log",
        pendlog=tmp_path / "logs" / "keel-live.pending.log",
        calls_log=calls_log,
        invocations_log=invocations_log,
        env=env,
    )


def _run(
    sb: Sandbox, now_utc: datetime, *, date_mode: str = "fixed"
) -> subprocess.CompletedProcess[str]:
    """Fire one invocation of the sandboxed script as though the clock read `now_utc`."""
    env = dict(sb.env)
    env["KEEL_TEST_DATE_MODE"] = date_mode
    env["KEEL_TEST_NOW"] = str(int(now_utc.timestamp()))
    return _run_script(sb.script, env=env)


def _count_lines(path: Path) -> int:
    """0 for a file that does not exist yet -- most of these logs start out absent."""
    if not path.exists():
        return 0
    return len(path.read_text().splitlines())


def test_the_sandbox_never_points_at_the_live_deployment(tmp_path: Path) -> None:
    """Guard on the guard: prove `_sandbox`'s rewrites really do relocate everything.

    Covers both rewrites every test in this file depends on for safety: `DIR=` (the money path)
    and the literal `/usr/bin/osascript` (the notification path). If either survives, the tests
    below are executing the live runner against real money, or could pop a real notification.
    """
    sb = _sandbox(tmp_path, keel_exit_code=0)
    body = sb.script.read_text()
    assert "/Users/elmehdiaitbrahim/keel" not in body
    assert f'DIR="{tmp_path}"' in body
    assert "/usr/bin/osascript" not in body
    assert os.path.commonpath([str(tmp_path), str(sb.script)]) == str(tmp_path)


# -- B2: pin the pure-Python model to the shipped script ---------------------------------------


def test_the_simulated_gate_matches_the_real_script(tmp_path: Path) -> None:
    """Pins `_run_gate` (the pure-Python model) to the REAL `keel-live-run.sh`.

    `test_exactly_one_run_per_utc_day_over_a_full_year` only proves a property of the MODEL; this
    is the test that makes trusting the model for that job legitimate. It replays a curated,
    adversarial trigger sequence through the ACTUAL shipped script (via the `_sandbox`/`_run`
    clock harness) and asserts, at EVERY trigger, that "did the real script run a cycle" equals
    "did the model say it runs".

    A full 13-month/~9600-trigger real-script run was measured at roughly +2.5 minutes on a
    ~10s suite (bash invocation ~3.3ms, the shimmed `date` ~5ms per call, several `date` calls per
    invocation) -- not acceptable, so this sequence is curated rather than exhaustive. It covers:
    three consecutive normal UTC days; the 00:20 UTC trigger below `SCHED_HOUR`; the full
    spring-forward day (2026-03-08, 23 triggers, the hour-lost case); the full fall-back day
    (2026-11-01, 25 triggers, including BOTH firings of the repeated local hour); the
    boot-after-outage catch-up sequence from
    `test_the_old_local_date_gate_could_double_run_within_one_utc_day`; and (Defect 1) a forward
    clock excursion followed by a return to real dates. Before Defect 1 was found, NEITHER this
    differential test NOR the pure model covered a forward excursion at all -- which is precisely
    how the regression got through review: nothing here would have caught "the script now returns
    the wrong exit code and stays silent forever", because this test only ever asserted whether a
    cycle ran, and a forward excursion never runs one, on the buggy script OR the fixed one (see
    `_run_gate`'s docstring). The excursion is added for completeness and to guard the "did a cycle
    run" property specifically, not to catch Defect 1 itself --
    `test_forward_clock_excursion_escalates_instead_of_silently_skipping` is what proves the exit
    code and notification, which this coarser differential test cannot see.

    Most of it runs through ONE sandbox, replayed as a single sequence sorted by UTC instant (the
    order a real machine would execute them in), so the stamp evolves exactly as it would across
    all these cases back to back -- one continuous timeline is cheaper than one sandbox per
    scenario and just as faithful, since the gate only ever compares ISO-date strings, never
    wall-clock elapsed time. The forward-excursion mini-sequence is the one exception: it is
    APPENDED after the sort, in its own literal firing order, rather than sorted in with the rest,
    because "jump forward then return to real dates" is deliberately NOT monotonic in the clock's
    own reading -- sorting it back into chronological order would erase the exact anomaly under
    test. It is kept small (4 extra triggers) to keep the added wall-clock cost tiny.
    """
    sb = _sandbox(tmp_path, keel_exit_code=0)
    tz = ZoneInfo(DEPLOYMENT_TZ)
    schedule = _plist_triggers()

    pairs: list[tuple[datetime, datetime]] = []

    # The 00:20 UTC trigger, below SCHED_HOUR, then three consecutive normal UTC days at 01:20.
    below_threshold = datetime(2026, 6, 15, 0, 20, tzinfo=UTC)
    pairs.append((below_threshold, below_threshold))
    for day in (15, 16, 17):
        t = datetime(2026, 6, day, 1, 20, tzinfo=UTC)
        pairs.append((t, t))

    # Spring-forward: local 02:00-02:59 does not exist -- 23 triggers.
    pairs.extend(_triggers(SPRING_FORWARD, SPRING_FORWARD, tz, schedule))

    # Fall-back: local 01:00-01:59 happens TWICE -- 25 triggers, both firings included.
    pairs.extend(_triggers(FALL_BACK, FALL_BACK, tz, schedule))

    # Boot-after-outage catch-up: identical to the dedicated regression test above.
    all_triggers = _triggers(date(2026, 1, 15), date(2026, 1, 16), tz, schedule)
    boot = datetime(2026, 1, 16, 1, 20, tzinfo=UTC)
    pairs.extend(pair for pair in all_triggers if pair[0] >= boot)

    pairs.sort(key=lambda pair: pair[0])

    # Defect 1: a forward clock excursion, then a return to real dates -- appended AFTER the sort,
    # in this exact order, deliberately not re-sorted with the rest (see the docstring above). The
    # last chronological trigger sorted in above is the fall-back day, 2026-11-01, so this segment
    # starts a month later to land on an ordinary already-stamped day first.
    forward_excursion_and_return = [
        (datetime(2026, 12, 1, 1, 20, tzinfo=UTC), datetime(2026, 12, 1, 1, 20, tzinfo=UTC)),
        (datetime(2035, 1, 1, 3, 20, tzinfo=UTC), datetime(2035, 1, 1, 3, 20, tzinfo=UTC)),
        (datetime(2026, 12, 2, 1, 20, tzinfo=UTC), datetime(2026, 12, 2, 1, 20, tzinfo=UTC)),
        (datetime(2026, 12, 3, 1, 20, tzinfo=UTC), datetime(2026, 12, 3, 1, 20, tzinfo=UTC)),
    ]
    pairs.extend(forward_excursion_and_return)

    model_ran = set(_run_gate(pairs, SCHED_HOUR, utc_anchored=True))

    for utc_instant, _local in pairs:
        before = _count_lines(sb.invocations_log)
        _run(sb, utc_instant)
        script_ran = _count_lines(sb.invocations_log) > before
        model_says_ran = utc_instant in model_ran
        assert script_ran == model_says_ran, (
            f"model/script disagreement at {utc_instant.isoformat()}: "
            f"script ran={script_ran}, model said ran={model_says_ran}"
        )


# -- B4: the real script's failure-handling behaviour -------------------------------------------


def test_a_clean_cycle_stamps_the_utc_date_and_the_next_run_is_a_no_op(tmp_path: Path) -> None:
    """A successful cycle stamps today's UTC date; a second invocation does nothing.

    This is the dedupe, end to end, in the real shell -- the mechanism every characterization test
    in `tests/test_agent.py` shows the consequences of losing. Clock-shimmed rather than relying
    on the wall clock (previously this test SILENTLY SKIPPED whenever CI happened to run inside
    the 00:00-01:00 UTC window -- on the exact mechanism that is the sole barrier to duplicate
    orders -- which is exactly backwards for a test this important).
    """
    sb = _sandbox(tmp_path, keel_exit_code=0)
    now = datetime(2026, 6, 15, 1, 20, tzinfo=UTC)

    first = _run(sb, now)
    assert first.returncode == 0
    assert sb.stamp.read_text().strip() == "2026-06-15"

    second = _run(sb, now)
    assert second.returncode == 0
    assert "already ran" in second.stdout
    assert "UTC" in second.stdout, "the skip message must say which calendar it is talking about"


def test_a_failed_cycle_writes_no_stamp_so_the_next_trigger_retries(tmp_path: Path) -> None:
    """A nonzero exit leaves the UTC day OPEN.

    Deliberate, and the direction to fail in: a detector that died on a network blip must not be
    recorded as a quiet "no signals today", and the next of the day's 23 remaining triggers must
    pick it up. The cost of getting this backwards is a silently skipped trading day.
    """
    sb = _sandbox(tmp_path, keel_exit_code=3)
    now = datetime(2026, 6, 15, 1, 20, tzinfo=UTC)

    result = _run(sb, now)
    assert result.returncode == 3, "the script must surface the cycle's exit code, not mask it"
    assert not sb.stamp.exists(), "a failed cycle must leave the day unstamped so it is retried"

    retried = _run(sb, now)
    assert retried.returncode == 3
    assert "already ran" not in retried.stdout


def test_unwritable_logs_dir_means_the_cycle_never_runs(tmp_path: Path) -> None:
    """Finding 2 (HIGH), the reviewer's exact reproduction, inverted.

    With a read-only logs dir the OLD script ran the cycle anyway, left the stamp ABSENT, exited
    0, and the next trigger ran a SECOND full cycle -- a duplicate real-money order, since nothing
    downstream dedupes an entry (see the module docstring). The pre-flight probe must catch this
    BEFORE `$KEEL` is ever invoked: a lost stamp then costs a missed trading day (recoverable), not
    a duplicate order (not recoverable).
    """
    sb = _sandbox(tmp_path, keel_exit_code=0)
    logs_dir = tmp_path / "logs"
    original_mode = logs_dir.stat().st_mode
    logs_dir.chmod(0o555)
    try:
        result = _run(sb, datetime(2026, 6, 15, 1, 20, tzinfo=UTC))
        assert result.returncode != 0, "an unpersistable stamp must not exit 0"
        assert _count_lines(sb.invocations_log) == 0, "keel must never be invoked pre-flight"
        assert sb.calls_log.exists() and sb.calls_log.read_text().strip(), (
            "a condition the machine cannot self-heal must alert a human"
        )
    finally:
        logs_dir.chmod(original_mode)


def test_atomic_stamp_write_leaves_yesterdays_stamp_intact_on_failure(tmp_path: Path) -> None:
    """Finding 2 (HIGH): a torn/failed post-cycle write must never leave the stamp EMPTY or PARTIAL.

    Plain `> "$STAMP"` truncates before it writes, so a write that dies partway leaves an EMPTY
    stamp -- and an empty stamp reads as "never ran", re-running a UTC day that already traded.
    Here the write is forced to fail AFTER the pre-flight probe (a differently-named file) has
    already passed, by making the stamp file itself immutable partway through the "cycle" -- and
    the pre-existing YESTERDAY stamp must survive completely unchanged: not empty, not today, not
    partial. That is what the temp-file-then-`mv -f` design buys: a rename either fully happens or
    fully does not.
    """
    stamp_path = tmp_path / "logs" / ".keel-live-last-run"
    yesterday = "2026-06-14"
    sb = _sandbox(tmp_path, keel_exit_code=0, keel_stub_extra=f'chflags uchg "{stamp_path}"')
    stamp_path.write_text(yesterday + "\n")
    try:
        result = _run(sb, datetime(2026, 6, 15, 1, 20, tzinfo=UTC))
        assert result.returncode != 0
        assert stamp_path.read_text().strip() == yesterday, (
            "the stamp must never be corrupted by a failed write -- it must read exactly what it "
            "read before the cycle ran"
        )
    finally:
        subprocess.run(["chflags", "nouchg", str(stamp_path)], check=False)


def test_stamp_write_failure_is_not_swallowed(tmp_path: Path) -> None:
    """Finding 2 (HIGH): a failed stamp write must be LOUD, not swallowed.

    The old script discarded `> "$STAMP"`'s exit status entirely and always `exit`ed with keel's
    own (successful) status, so a write failure looked identical to a normal, correctly-stamped
    day. The only way an operator finds out is a notification and a nonzero exit; without both,
    the next trigger runs a second cycle believing today needs (re-)trading, when an order from
    today's first cycle may already be sitting on the exchange.
    """
    stamp_path = tmp_path / "logs" / ".keel-live-last-run"
    sb = _sandbox(tmp_path, keel_exit_code=0, keel_stub_extra=f'chflags uchg "{stamp_path}"')
    stamp_path.write_text("2026-06-14\n")
    try:
        result = _run(sb, datetime(2026, 6, 15, 1, 20, tzinfo=UTC))
        assert result.returncode != 0, "a stamp write failure must not exit 0"
        assert sb.calls_log.exists() and sb.calls_log.read_text().strip(), (
            "a stamp write failure must alert a human -- silence here IS the Finding-2 bug"
        )
    finally:
        subprocess.run(["chflags", "nouchg", str(stamp_path)], check=False)


def test_empty_clock_output_refuses_to_run(tmp_path: Path) -> None:
    """Finding 3 (MED): `date -u` returning EMPTY must not silently read as "already ran, forever".

    On this hardware `$((10#$(date -u '+%H')))` on empty input evaluates to 0, not an error, so an
    unvalidated empty clock gives `TODAY=""`/`HOUR=0` -- and a missing stamp file ALSO reads as ""
    (`cat ... 2>/dev/null || true`), so `[ "" = "" ]` (or the new `[[ "" < "" ]]` -> false, same
    effect) reads as "already ran". That makes the detector dead FOREVER, silently, because the
    failure mode looks exactly like success.
    """
    sb = _sandbox(tmp_path, keel_exit_code=0)
    result = _run(sb, datetime(2026, 6, 15, 1, 20, tzinfo=UTC), date_mode="empty")
    assert result.returncode != 0
    assert _count_lines(sb.invocations_log) == 0, "a broken clock must never reach a cycle"
    assert not sb.stamp.exists(), "a broken clock must not be recorded as a completed day"
    assert "already ran" not in result.stdout, (
        "the empty-clock bug makes this claim true BY ACCIDENT -- it must never be printed here"
    )
    assert sb.calls_log.exists() and sb.calls_log.read_text().strip()


def test_garbage_clock_output_refuses_to_run(tmp_path: Path) -> None:
    """Finding 3 (MED): `date -u` returning GARBAGE must be rejected the same way EMPTY is.

    Not every clock failure produces empty output -- a broken locale, a corrupted `/etc/localtime`,
    or a flaky `date` binary could just as easily produce text that is not a date at all. Anything
    that is not exactly `^[0-9]{4}-[0-9]{2}-[0-9]{2}$` / `^[0-9]{2}$` must be rejected, not just
    the empty case.
    """
    sb = _sandbox(tmp_path, keel_exit_code=0)
    result = _run(sb, datetime(2026, 6, 15, 1, 20, tzinfo=UTC), date_mode="garbage")
    assert result.returncode != 0
    assert _count_lines(sb.invocations_log) == 0
    assert not sb.stamp.exists()
    assert "already ran" not in result.stdout
    assert sb.calls_log.exists() and sb.calls_log.read_text().strip()


def test_malformed_stamp_content_refuses_to_run(tmp_path: Path) -> None:
    """Finding 4 (MED): a corrupt on-disk stamp must never be silently COMPARED against today.

    `"garbage" < "2026-06-15"` is FALSE in a plain string compare, which reads exactly like
    "already ran" and disables the detector forever -- the same failure class as an unvalidated
    empty clock, just entered from the stamp file instead of `date`.
    """
    sb = _sandbox(tmp_path, keel_exit_code=0)
    sb.stamp.write_text("not-a-date\n")
    result = _run(sb, datetime(2026, 6, 15, 1, 20, tzinfo=UTC))
    assert result.returncode != 0
    assert _count_lines(sb.invocations_log) == 0
    assert sb.stamp.read_text().strip() == "not-a-date", "a malformed stamp must not be overwritten"
    assert sb.calls_log.exists() and sb.calls_log.read_text().strip()


def test_clock_rollback_does_not_rerun_or_move_the_stamp_backwards(tmp_path: Path) -> None:
    """Finding 4 (MED) / Defect 1 (MED): a clock reading a PAST date must not re-run -- and now
    alerts instead of skipping silently.

    `RunAtLoad` fires immediately on boot, potentially before NTP has corrected a bad real-time
    clock. With the OLD `=` compare, a bogus past date was "not today", so the cycle RAN and
    stamped the bogus date; when the clock then corrected forward, the real date no longer matched
    that bogus stamp, so the cycle ran AGAIN -- re-evaluating, and potentially re-entering, a bar
    it already traded. Requiring the stamp to be STRICTLY BEFORE today closes that: a stamp equal
    to OR ahead of today both read as "done, do not run" -- and the stamp itself must never move
    backwards.

    What changed under Defect 1: a rolled-back clock makes today's stamp read as AHEAD of "today",
    which is now exit 67 (notify, refuse) rather than the old silent exit 0 -- the two no-run
    outcomes used to be one path and are now two, see `_run_gate`'s docstring. The behaviour this
    test exists to pin -- never re-run, never move the stamp backwards -- is unchanged; only the
    exit code and the fact that a human now hears about it are new.
    """
    sb = _sandbox(tmp_path, keel_exit_code=0)
    x = datetime(2026, 6, 15, 1, 20, tzinfo=UTC)
    months_in_the_past = datetime(2026, 1, 10, 1, 20, tzinfo=UTC)
    x_plus_1 = datetime(2026, 6, 16, 1, 20, tzinfo=UTC)

    first = _run(sb, x)
    assert first.returncode == 0
    assert sb.stamp.read_text().strip() == "2026-06-15"
    ran_once = _count_lines(sb.invocations_log)

    rolled_back = _run(sb, months_in_the_past)
    assert rolled_back.returncode == 67, "a stamp ahead of today must exit 67, not exit 0"
    assert _count_lines(sb.invocations_log) == ran_once, "a clock in the past must not run a cycle"
    assert sb.stamp.read_text().strip() == "2026-06-15", "the stamp must never move backwards"
    assert sb.calls_log.exists() and sb.calls_log.read_text().strip(), (
        "a rolled-back clock must alert a human now, not skip silently forever"
    )

    calls_after_rollback = _count_lines(sb.calls_log)
    same_day = _run(sb, x)
    assert same_day.returncode == 0, (
        "the stamp's own date, re-seen, is the ORDINARY already-ran case"
    )
    assert _count_lines(sb.invocations_log) == ran_once, (
        "the stamp's own date, re-seen, must not re-run either"
    )
    assert sb.stamp.read_text().strip() == "2026-06-15"
    assert _count_lines(sb.calls_log) == calls_after_rollback, (
        "re-seeing the stamp's own date is the ordinary already-ran path -- it must stay silent, "
        "not alert again"
    )

    _run(sb, x_plus_1)
    assert _count_lines(sb.invocations_log) == ran_once + 1, "the day after must run exactly once"
    assert sb.stamp.read_text().strip() == "2026-06-16"


def test_forward_clock_excursion_escalates_instead_of_silently_skipping(tmp_path: Path) -> None:
    """Defect 1 (MED), a REGRESSION PR #174 itself introduced: a forward clock excursion must not
    become a permanent silent outage.

    Reproduces the reviewer's exact sequence through this PR's own sandbox harness: the detector
    runs normally and stamps a real UTC date; a bad RTC before NTP settles then reads YEARS into
    the future (`RunAtLoad` firing on a not-yet-corrected boot clock), the cycle runs and stamps
    that bogus future date; every real trigger afterwards then reads a correctly-dated TODAY that
    is, and stays, behind the bogus stamp.

    Under the OLD `=` compare (pre-PR-174) this SELF-HEALED the very next real day: the bogus
    stamp no longer equalled today, so the cycle ran again and re-stamped the correct date. PR
    #174's `<` compare fixed clock ROLLBACK but broke this direction instead: `! [[ "$STAMPED" <
    "$TODAY" ]]` stays true for every subsequent real day until the real clock catches up to the
    bogus stamp -- which could be years -- so this is a SILENT outage, rc=0 throughout, zero
    notifications. This test is therefore a regression test for THIS FIX, not a test of the
    original Finding 4: the monotonic `<` compare did not exist before PR #174, so there is no
    "before PR #174" behaviour to regress against here.

    The fix: a stamp strictly AHEAD of today is corrupt state, not "already ran" -- exit 67,
    notify, and never overwrite the stamp. Checked over SEVERAL consecutive real days, not just
    one, to prove the alert keeps firing rather than notifying once and then going quiet again
    exactly like the bug it replaces.
    """
    sb = _sandbox(tmp_path, keel_exit_code=0)

    real_run = datetime(2026, 8, 6, 1, 20, tzinfo=UTC)
    first = _run(sb, real_run)
    assert first.returncode == 0
    assert sb.stamp.read_text().strip() == "2026-08-06"
    ran_once = _count_lines(sb.invocations_log)

    # Bad RTC before NTP settles: RunAtLoad fires with a clock reading years in the future.
    bad_rtc = datetime(2035, 1, 1, 3, 20, tzinfo=UTC)
    jumped = _run(sb, bad_rtc)
    assert jumped.returncode == 0, (
        "the bogus future date is, from the stamp's point of view, just another unstamped day -- "
        "it runs normally, which is exactly how the bogus stamp gets written in the first place"
    )
    assert sb.stamp.read_text().strip() == "2035-01-01"
    assert _count_lines(sb.invocations_log) == ran_once + 1

    calls_before_return = _count_lines(sb.calls_log)
    for day in (7, 8, 9, 10, 11):
        back_to_real = datetime(2026, 8, day, 1, 20, tzinfo=UTC)
        result = _run(sb, back_to_real)
        assert result.returncode == 67, (
            f"2026-08-{day:02d} must exit 67 (stamp ahead of today) -- the OLD `=` compare would "
            "have exited 0 and RUN a (duplicate) cycle here instead; the regression this PR "
            "introduced exited 0 and skipped SILENTLY instead of either"
        )
        assert _count_lines(sb.invocations_log) == ran_once + 1, (
            "a stamp ahead of today must never run a cycle, on any of these days"
        )
        assert sb.stamp.read_text().strip() == "2035-01-01", (
            "the bogus future stamp must not move just because more real days went by"
        )

    assert _count_lines(sb.calls_log) == calls_before_return + 5, (
        "all 5 subsequent real days must alert -- one notification and then silence would be no "
        "better than the regression this fix closes, just with a one-day grace period"
    )


def test_ordinary_already_ran_stays_silent(tmp_path: Path) -> None:
    """The control for Defect 1's fix: an ordinary same-UTC-day second trigger must still exit 0
    with NO notification.

    Splitting "stamp ahead of today" (exit 67, alert) out from "stamp equal to today" (exit 0,
    silent) is only a correct fix if the ordinary, overwhelmingly common case -- one of the day's
    23 remaining triggers finding the day already done -- does not start alerting too. A version of
    the fix that over-eagerly notified on every non-run, not just the forward-excursion one, would
    defeat the NOTIFICATION POLICY this script depends on (see its header): training the operator
    to expect routine noise on the ordinary path is exactly what makes the operator ignore the
    alerts that matter.
    """
    sb = _sandbox(tmp_path, keel_exit_code=0)
    now = datetime(2026, 6, 15, 1, 20, tzinfo=UTC)

    first = _run(sb, now)
    assert first.returncode == 0
    assert sb.stamp.read_text().strip() == "2026-06-15"
    assert _count_lines(sb.calls_log) == 0, "a normal first-of-the-day run must not alert either"

    second = _run(sb, now)
    assert second.returncode == 0
    assert "already ran" in second.stdout
    assert _count_lines(sb.calls_log) == 0, (
        "the ordinary already-ran path must stay exactly as silent as before this fix"
    )


def test_stuck_detector_escalates_after_consecutive_failures(tmp_path: Path) -> None:
    """Defect 2 (MED): nothing previously escalated a detector that keeps failing every trigger.

    A nonzero exit from keel is deliberately NOT notified on its own (NOTIFICATION POLICY: it is
    normally an expected, self-healing publication lag that the next hourly trigger retries). But
    that means 23 consecutive failing triggers in one UTC day produced ZERO notifications, and a
    persistently stuck detector was indistinguishable from a quiet day with no signals -- which
    matters more now that another change on this branch (the entry gate in `keel/agent.py`) makes
    ANY one chronically-lagging product withhold the WHOLE cycle's entries, so a single stuck
    product could keep the day permanently unstamped with nothing ever shouting about it.

    Proves all four pieces of the fix: N-1 consecutive failures stay silent, the Nth (N=3) alerts,
    the stamp is never written on any of the failing cycles, and a subsequent CLEAN cycle resets
    the counter so a later isolated failure is silent again rather than continuing to count from a
    long-resolved outage.
    """
    now = datetime(2026, 6, 15, 1, 20, tzinfo=UTC)
    failcount = tmp_path / "logs" / ".keel-live-last-run.failures"

    failing = _sandbox(tmp_path, keel_exit_code=3)

    first = _run(failing, now)
    assert first.returncode == 3
    assert not failing.stamp.exists(), "a failing cycle must never fabricate a stamp"
    assert _count_lines(failing.calls_log) == 0, "one failure is ordinary publication lag -- silent"

    second = _run(failing, now)
    assert second.returncode == 3
    assert not failing.stamp.exists()
    assert _count_lines(failing.calls_log) == 0, (
        "two failures in a row is still not 'stuck' -- silent"
    )

    third = _run(failing, now)
    assert third.returncode == 3
    assert not failing.stamp.exists(), (
        "escalating must never fabricate a stamp -- the day stays open"
    )
    assert _count_lines(failing.calls_log) == 1, "the third consecutive failure must escalate"
    assert "3 consecutive" in failing.calls_log.read_text()

    # A clean, verified cycle heals the detector -- the counter must reset, not just pause.
    clean = _sandbox(tmp_path, keel_exit_code=0)
    healed = _run(clean, now)
    assert healed.returncode == 0
    assert clean.stamp.read_text().strip() == "2026-06-15"
    assert not failcount.exists(), (
        "a clean, verified stamp write must reset the consecutive-failure counter, not merely "
        "pause it -- a missing counter file reads as 0"
    )

    # One more failure, on a later day so the fresh stamp does not itself block it: must be silent,
    # proving the counter actually reset rather than continuing on from 3.
    calls_before_next_failure = _count_lines(clean.calls_log)
    failing_again = _sandbox(tmp_path, keel_exit_code=5)
    later = datetime(2026, 6, 16, 1, 20, tzinfo=UTC)
    single_failure = _run(failing_again, later)
    assert single_failure.returncode == 5
    assert _count_lines(failing_again.calls_log) == calls_before_next_failure, (
        "the counter reset means a single later failure is silent again, not counted onward from "
        "the earlier, already-resolved outage"
    )


def test_pendlog_is_utc_labelled(tmp_path: Path) -> None:
    """Finding 10: PENDLOG's timestamp must say UTC, like every other line in this script.

    It used to be plain `date` (LOCAL, unlabelled) next to lines that are otherwise all `date -u`
    -- a trap for an operator reading logs at 2am, unsure which midnight a PENDING signal is even
    relative to.
    """
    sb = _sandbox(tmp_path, keel_exit_code=0, signals=2)
    result = _run(sb, datetime(2026, 6, 15, 1, 20, tzinfo=UTC))
    assert result.returncode == 0
    pending = sb.pendlog.read_text()
    assert "UTC" in pending
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC", pending)


def test_no_pending_notification_on_a_failed_cycle(tmp_path: Path) -> None:
    """A4: acting on a PENDING prompt from a FAILED cycle bypasses the stamp and duplicates entry.

    Per the header's COROLLARY, "run the agent interactively to approve" tells the operator to do
    the one thing that skips this script's stamp entirely. Firing that prompt off a cycle that did
    not even complete -- whose `signals=N` may be parsed from partial or garbled output -- is a
    direct path to a duplicate live order. The fix only takes the PENDING-notification path when
    the cycle's own exit status is clean; an ordinary nonzero keel exit still gets an OUTLOG line
    (see `test_a_failed_cycle_writes_no_stamp_so_the_next_trigger_retries`), just not this prompt.
    """
    sb = _sandbox(tmp_path, keel_exit_code=3, signals=2)
    result = _run(sb, datetime(2026, 6, 15, 1, 20, tzinfo=UTC))
    assert result.returncode == 3
    calls = sb.calls_log.read_text() if sb.calls_log.exists() else ""
    assert "run the agent interactively" not in calls
