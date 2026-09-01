#!/bin/bash
# Daily equities paper runner -- ~/keel deployment (keel release install), issue #370 B2.
# One agent cycle per UTC DAY in PAPER mode against Alpaca's PAPER API; it places NOTHING
# real. Drives the daily turtle rules (rows stored with params.granularity="ONE_DAY") on US
# equities in their OWN database, keel-equities.db, pinned to config.paper-equities.yaml.
#
# Self-contained: calls the deployment's own venv binary, so no `uv`/asdf PATH is needed.
# Lives OUTSIDE ~/Documents for the same TCC reason as the sibling runners (launchd-spawned
# processes are not granted access to ~/Documents; home root is not TCC-protected).
#
# EXACTLY ONCE PER **UTC DAY**. The stamp is the UTC date (`date -u '+%Y-%m-%d'`) because
# the venue's own daily bars are keyed to the UTC session date: Alpaca stamps a session's
# ONE_DAY bar inside that session's UTC day, so "today's UTC date" is the trading-day
# identity year-round on an Eastern host, and the 19:00/20:00 local UTC rollover is always
# AFTER the window below. Every trigger that finds no stamp for the UTC day runs the cycle
# and stamps it; later triggers the same UTC day are no-ops. The next day always needs, and
# gets, its own cycle.
#
# THE WINDOW GUARD, and why this runner refuses to run outside its window -- 10:00 inclusive
# to 16:00 exclusive, LOCAL hours (the code reads: hour >= 10 and hour < 16, so the 15:00
# trigger runs). The US regular session is 09:30 to 16:00 ET and the engine's session gate
# (#370 B1) skips the WHOLE cycle whenever the venue clock answers closed, exiting 0. So:
#   - BEFORE 10:00 (pre-open boots, RunAtLoad on an early login): a cycle would skip closed
#     and exit 0, and stamping that skip would record the day as done and suppress the real
#     evaluation at 10:00. Refuse, run nothing, stamp nothing.
#   - AT/AFTER 16:00 (after the close): same closed-market skip, same false stamp. Refuse.
# The plist's six triggers (10:00-15:00 local, on the hour) sit inside the session by
# construction; the guard exists for RunAtLoad and any manual cron that misses the point.
# Known edge, documented not fixed: an early-close half-day (13:00 ET close) whose morning
# cycles all failed could have the day stamped by a 14:00 closed-skip -- a paper-evidence
# gap, visible in the log, never a money event.
#
# SESSION-AWARENESS COMES FREE from B1: weekends and holidays need no calendar here. The
# agent itself reads the venue clock, records the session (which keeps `fetch --check`
# non-alerting through the weekend) and skips with reason market_closed; that skip exits 0
# and stamping it is CORRECT cadence bookkeeping -- nothing more can happen that day.
#
# THE TWO SKIP KINDS ARE STAMPED DIFFERENTLY. A clock that cannot be READ (no network on
# wake, the clock endpoint erroring) is not the day's work either: the agent exits
# MARKET_CLOCK_UNAVAILABLE_EXIT (nonzero) for exactly that skip, so `set -e` below stops
# this script short of the stamp and the next trigger retries once the clock answers --
# a transient outage costs an hour of delay, never the trading day.
#
# The stamp is written only AFTER a successful cycle (`set -e`), so a failed run (no network
# on wake, the venue late publishing the bar, blocked entries or an unreadable clock
# returning nonzero) is retried by the next trigger rather than being recorded as done. Note
# what a retry can and cannot do: it evaluates the NEWEST closed bar at its own time, so a
# day whose cycle failed through is covered by a later retry only in what that later
# evaluation sees.
#
# Authored in the dev repo. DEPLOY (copy) to ~/keel and schedule via
# com.keel.paper-equities.plist.
set -euo pipefail

DIR="/Users/elmehdiaitbrahim/keel"
cd "$DIR"

STAMP="$DIR/logs/.paper-equities-last-run"
WINDOW_START_HOUR=10
WINDOW_END_HOUR=16

# The one seam every macOS notification in this script goes through -- same shape and purpose
# as `keel-live-run.sh`'s (#642), so the SAME test-harness rewrite works unmodified here too.
# This script places nothing real, so a notification here is advisory, never load-bearing.
OSASCRIPT="/usr/bin/osascript"
notify() {
  "$OSASCRIPT" -e "display notification \"$1\" with title \"keel-paper-equities\" subtitle \"paper\" sound name \"Glass\"" 2>/dev/null || true
}

TODAY="$(date -u '+%Y-%m-%d')"
# 10# forces base 10: `date +%H` yields 08/09, which arithmetic would otherwise read as octal.
HOUR="$((10#$(date '+%H')))"
STAMPED="$(cat "$STAMP" 2>/dev/null || true)"

if [ "$STAMPED" = "$TODAY" ]; then
    printf '%s [paper-equities] cycle already ran this UTC day (%s) -- skipping\n' \
        "$(date '+%Y-%m-%d %H:%M')" "$TODAY"
    exit 0
fi

if [ "$HOUR" -lt "$WINDOW_START_HOUR" ]; then
    printf '%s [paper-equities] before %02d:00 local -- the session has not been evaluated yet; leaving the day to the scheduled run\n' \
        "$(date '+%Y-%m-%d %H:%M')" "$WINDOW_START_HOUR"
    exit 0
fi

if [ "$HOUR" -ge "$WINDOW_END_HOUR" ]; then
    printf '%s [paper-equities] at/after %02d:00 local -- the US session is closed; not running or stamping\n' \
        "$(date '+%Y-%m-%d %H:%M')" "$WINDOW_END_HOUR"
    exit 0
fi

# B (#640/#642). FETCH, then DOCTOR, then the cycle, then DOCTOR again -- same shape as
# `keel-live-run.sh`; see that script's block comment for the full argument. Doctor is a
# REPORT here, never a gate -- keel/agent.py's whole-cycle admission bit already withholds
# every entry, on every product, the instant any rule is blocked (deliberate, closes a
# real-money duplicate-order hazard on the live path; see
# tests/test_agent.py::test_a_ready_products_order_placed_before_a_blocked_products_own_check_is_the_regression),
# so a per-product gate here would be finer than the engine and would change nothing this
# paper profile does. Neither call may abort this script (`set -e` would otherwise do exactly
# that) or change the cycle's own exit status, hence the explicit `|| STATUS=$?` capture below.
FETCH_STATUS=0
FETCH_OUT="$(./.venv/bin/keel --config config.paper-equities.yaml --db keel-equities.db fetch 2>&1)" || FETCH_STATUS=$?
printf '%s\n' "$FETCH_OUT"
if [ "$FETCH_STATUS" -ne 0 ]; then
    notify "keel-paper-equities: fetch failed ahead of this cycle (exit ${FETCH_STATUS}) -- the cycle will still run against whatever cache it already has."
    printf '%s [paper-equities] pre-cycle fetch exited %d -- continuing with the existing cache\n' \
        "$(date '+%Y-%m-%d %H:%M')" "$FETCH_STATUS"
fi

# Plain human output, not `--json`: see keel-live-run.sh's identical comment (no jq on macOS,
# and the `[FAIL]`/detail lines already carry per-product identity for what matters here).
DOCTOR_STATUS=0
DOCTOR_OUT="$(./.venv/bin/keel --config config.paper-equities.yaml --db keel-equities.db doctor 2>&1)" || DOCTOR_STATUS=$?
printf '%s\n' "$DOCTOR_OUT"
if [ "$DOCTOR_STATUS" -ne 0 ]; then
    DOCTOR_FAILS="$(printf '%s\n' "$DOCTOR_OUT" | grep -A2 '^\[FAIL\]' || true)"
    notify "keel-paper-equities: doctor reported FAIL ahead of this cycle -- ${DOCTOR_FAILS//$'\n'/ | } -- report only."
fi

# One cycle per UTC day; the LaunchAgent supplies the cadence and the retries. Paper mode,
# daily rules on equities, the profile's own database. Captured explicitly (rather than left
# to `set -e` to abort the script outright) so the POST-CYCLE doctor below still runs on a
# failed cycle; the stamp write is still skipped and the script still exits with the cycle's
# own status, exactly as before -- including the clock-unavailable skip (B1's
# MARKET_CLOCK_UNAVAILABLE_EXIT), which must still leave the day unstamped.
CYCLE_STATUS=0
./.venv/bin/keel --config config.paper-equities.yaml --db keel-equities.db agent || CYCLE_STATUS=$?

# POST-CYCLE DOCTOR -- runs regardless of CYCLE_STATUS; report only, never touches it. Catches
# a cycle that returned 0 having quietly withheld every entry on stale data, which otherwise
# reads identically to a quiet session with no signals.
POST_DOCTOR_STATUS=0
POST_DOCTOR_OUT="$(./.venv/bin/keel --config config.paper-equities.yaml --db keel-equities.db doctor 2>&1)" || POST_DOCTOR_STATUS=$?
printf '%s\n' "$POST_DOCTOR_OUT"
if [ "$POST_DOCTOR_STATUS" -ne 0 ]; then
    POST_DOCTOR_FAILS="$(printf '%s\n' "$POST_DOCTOR_OUT" | grep -A2 '^\[FAIL\]' || true)"
    notify "keel-paper-equities: doctor reported FAIL after this cycle -- ${POST_DOCTOR_FAILS//$'\n'/ | } -- report only."
fi

if [ "$CYCLE_STATUS" -ne 0 ]; then
    exit "$CYCLE_STATUS"
fi

printf '%s\n' "$TODAY" > "$STAMP"
