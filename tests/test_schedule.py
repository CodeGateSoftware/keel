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
"""

from __future__ import annotations

import os
import plistlib
import re
import stat
import subprocess
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


def _stored_series(now_utc: datetime) -> dict[Granularity, list[Candle]]:
    """The candle series `data.market_feed` would have persisted as of `now_utc`.

    market_feed stores only CLOSED candles, so a bar with timestamp `t` and width `w` is present
    exactly once `now >= t + w`. Deriving both series from that one rule -- rather than hand-
    writing a series per parametrised time -- is what makes this test about the CLOCK and not
    about my arithmetic.
    """
    now = int(now_utc.timestamp())
    last_hour = (now // _HOUR - 1) * _HOUR
    last_day = (now // _DAY - 1) * _DAY
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
    the expectations here.
    """
    now = datetime(2026, 6, 15, hour, minute, tzinfo=UTC)
    series = _stored_series(now)

    newest_stored_day = series[Granularity.ONE_DAY][-1].ts
    effective = _completed_days(series)[-1].ts

    if sees_fresh_bar:
        assert effective == newest_stored_day
    else:
        assert effective == newest_stored_day - _DAY


# -- the schedule gate, simulated over a full year --------------------------------------------
#
# Deliberately PURE: no keel imports, no database, no clock. The gate is four lines of shell and
# the only interesting thing about it is how it behaves across thousands of triggers and two DST
# transitions, which is a property you can only see by simulating it.


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
    """Replay `keel-live-run.sh`'s two guards over `triggers`; return the UTC instants that RAN.

    The shell, verbatim in Python:

        if [ "$STAMPED" = "$TODAY" ]; then exit 0; fi      # already ran for this date
        if [ "$HOUR" -lt "$SCHED_HOUR" ]; then exit 0; fi  # too early in the day

    `utc_anchored=False` reproduces the OLD behaviour (both `TODAY` and `HOUR` from LOCAL time),
    so the two can be compared on identical trigger lists. Every cycle here is assumed to succeed
    -- a failed cycle writes no stamp and is retried, which only ever ADDS a later run on the
    same date, never removes one.
    """
    ran: list[datetime] = []
    stamp: date | None = None
    for utc_instant, local_instant in triggers:
        clock = utc_instant if utc_anchored else local_instant
        if clock.date() == stamp:
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

    This is the invariant the whole change rests on, stated as a property rather than as prose:
    combined with `test_effective_bar_does_not_advance_until_0100_utc` (the newest visible daily
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
    """`TODAY` and `HOUR` are both derived with `date -u`.

    The stamp and the hour gate MUST agree on which clock they are on. A UTC hour compared against
    a locally-stamped date would gate on one calendar and dedupe on another -- the worst of both,
    and it would not show up in the pure simulation above because that simulation gets its clock
    from a parameter rather than from the script.
    """
    source = RUN_SCRIPT.read_text()
    assert re.search(r"^TODAY=\"\$\(date -u '\+%Y-%m-%d'\)\"$", source, re.MULTILINE)
    assert re.search(r"^HOUR=\"\$\(\(10#\$\(date -u '\+%H'\)\)\)\"$", source, re.MULTILINE)


def _sandbox(tmp_path: Path, keel_exit_code: int) -> tuple[Path, Path]:
    """Copy `keel-live-run.sh` into `tmp_path` with its deployment root repointed at the sandbox.

    Only the `DIR=` assignment is rewritten; the gate, the stamp and the exit handling all run
    VERBATIM, which is the point -- a test that reimplemented them would prove nothing about the
    script that actually ships. `KEEL` is derived from `DIR` inside the script, so repointing
    `DIR` also repoints the binary at our stub.

    Returns `(script, stamp)`.
    """
    source = RUN_SCRIPT.read_text()
    patched, count = re.subn(
        r'^DIR="[^"]*"$', f'DIR="{tmp_path}"', source, count=1, flags=re.MULTILINE
    )
    assert count == 1, "could not repoint DIR -- refusing to run a script aimed at the deployment"
    # Belt and braces. This test executes a shell script that, unmodified, drives REAL MONEY
    # against ~/keel. If any reference to that path survives the rewrite, do not run it.
    assert "/Users/elmehdiaitbrahim/keel" not in patched

    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    stub_dir = tmp_path / ".venv" / "bin"
    stub_dir.mkdir(parents=True, exist_ok=True)
    stub = stub_dir / "keel"
    # Emits a LoopResult-shaped line with signals=0, so the notification path stays untaken and
    # no osascript runs during the test.
    stub.write_text(f"#!/bin/bash\nprintf 'mode=confirm signals=0\\n'\nexit {keel_exit_code}\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

    script = tmp_path / "keel-live-run.sh"
    script.write_text(patched)
    return script, tmp_path / "logs" / ".keel-live-last-run"


def _skip_before_sched_hour() -> None:
    """Skip when the wall clock would make the script take its early-exit branch.

    These tests are about the STAMP, and the script legitimately refuses to do anything before
    01:00 UTC. Rather than override `SCHED_HOUR` in the sandbox -- which would test a script we do
    not ship -- the one UTC hour a day where the two conflict is skipped. The hour gate itself is
    covered by the pure simulation above, which does not depend on the clock.
    """
    if datetime.now(UTC).hour < SCHED_HOUR:
        pytest.skip("inside the 00:00-01:00 UTC window the script deliberately declines to run")


def test_a_clean_cycle_stamps_the_utc_date_and_the_next_run_is_a_no_op(tmp_path: Path) -> None:
    """A successful cycle stamps today's UTC date; a second invocation does nothing.

    This is the dedupe, end to end, in the real shell -- the mechanism every characterization test
    in `tests/test_agent.py` shows the consequences of losing.
    """
    _skip_before_sched_hour()
    script, stamp = _sandbox(tmp_path, keel_exit_code=0)

    first = subprocess.run(["/bin/bash", str(script)], capture_output=True, text=True)
    assert first.returncode == 0
    assert stamp.read_text().strip() == datetime.now(UTC).strftime("%Y-%m-%d")

    second = subprocess.run(["/bin/bash", str(script)], capture_output=True, text=True)
    assert second.returncode == 0
    assert "already ran" in second.stdout
    assert "UTC" in second.stdout, "the skip message must say which calendar it is talking about"


def test_a_failed_cycle_writes_no_stamp_so_the_next_trigger_retries(tmp_path: Path) -> None:
    """A nonzero exit leaves the UTC day OPEN.

    Deliberate, and the direction to fail in: a detector that died on a network blip must not be
    recorded as a quiet "no signals today", and the next of the day's 23 remaining triggers must
    pick it up. The cost of getting this backwards is a silently skipped trading day.
    """
    _skip_before_sched_hour()
    script, stamp = _sandbox(tmp_path, keel_exit_code=3)

    result = subprocess.run(["/bin/bash", str(script)], capture_output=True, text=True)

    assert result.returncode == 3, "the script must surface the cycle's exit code, not mask it"
    assert not stamp.exists(), "a failed cycle must leave the day unstamped so it is retried"

    retried = subprocess.run(["/bin/bash", str(script)], capture_output=True, text=True)
    assert retried.returncode == 3
    assert "already ran" not in retried.stdout


def test_the_sandbox_never_points_at_the_live_deployment(tmp_path: Path) -> None:
    """Guard on the guard: prove the rewrite in `_sandbox` really does relocate everything.

    If this ever fails, the two tests above are executing the live runner against real money.
    """
    script, _ = _sandbox(tmp_path, keel_exit_code=0)
    body = script.read_text()
    assert "/Users/elmehdiaitbrahim/keel" not in body
    assert f'DIR="{tmp_path}"' in body
    assert os.path.commonpath([str(tmp_path), str(script)]) == str(tmp_path)
