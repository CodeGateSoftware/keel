#!/bin/bash
# Hourly paper runner -- ~/keel deployment (keel release install), issue #337.
# One agent cycle per UTC HOUR in PAPER mode against the live READ-ONLY Coinbase API; it
# places NOTHING real. Drives the HOURLY turtle rows (turtle rules stored with
# params.granularity="ONE_HOUR") in their OWN database, keel-paperhourly.db.
#
# Self-contained: calls the deployment's own venv binary, so no `uv`/asdf PATH is needed.
# Lives OUTSIDE ~/Documents for the same TCC reason as paperforward-run.sh (launchd-spawned
# processes are not granted access to ~/Documents; home root is not TCC-protected).
#
# EXACTLY ONCE PER **UTC HOUR**, not per day. paperforward-run.sh's day-stamp is deliberately
# daily-grained (the daily Turtle wants one cycle per day); this profile's evidence is the
# hourly bar, so the stamp here is the UTC HOUR (`date -u '+%Y-%m-%dT%H'`). Every trigger at
# or after hh:20 that finds no stamp for hour hh runs the cycle and stamps it; later triggers
# in the same hour (a repeated load, fall-back's repeated local hour, a wake-catch-up rerun)
# are no-ops. The NEXT hour always needs, and gets, its own cycle.
#
# THE STAMP IS CADENCE BOOKKEEPING HERE, NOT THE CORRECTNESS MECHANISM IT IS ON THE LIVE
# PATH. The paper path refuses a second entry while a product is already open
# (strategy/paper.py), so a duplicate cycle cannot double a position. It still guards the
# EVIDENCE: two cycles in one hour would evaluate the same closed bar twice and inflate the
# rail-veto and no-signal counts the profile exists to collect.
#
# The stamp is written only AFTER a successful cycle (`set -e`), so a failed run (no network
# on wake, say) is retried by the next hourly trigger rather than being recorded as done.
# Note what a retry can and cannot do: it evaluates the NEWEST closed bar at its own time,
# so a bar whose hour went unstamped is covered by the next cycle's evaluation only if that
# bar is still the newest closed bar (a signal that was there is re-seen; a bar skipped
# while powered off is gone -- an hour of evidence, not an hour of money).
#
# Authored in the dev repo (tracked there since 2026-08-03). DEPLOY (copy) to ~/keel and
# schedule via com.keel.paper-hourly.plist.
set -euo pipefail

DIR="/Users/elmehdiaitbrahim/keel"
cd "$DIR"

STAMP="$DIR/logs/.paper-hourly-last-run"

# The one seam every macOS notification in this script goes through -- same shape and purpose
# as `keel-live-run.sh`'s (#642), so the SAME test-harness rewrite works unmodified here too.
# This script places nothing real, so a notification here is advisory, never load-bearing.
OSASCRIPT="/usr/bin/osascript"
notify() {
  "$OSASCRIPT" -e "display notification \"$1\" with title \"keel-paper-hourly\" subtitle \"paper\" sound name \"Glass\"" 2>/dev/null || true
}

THIS_HOUR="$(date -u '+%Y-%m-%dT%H')"
STAMPED="$(cat "$STAMP" 2>/dev/null || true)"

if [ "$STAMPED" = "$THIS_HOUR" ]; then
    printf '%s [paper-hourly] cycle already ran this UTC hour (%s) -- skipping\n' \
        "$(date '+%Y-%m-%d %H:%M')" "$THIS_HOUR"
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
FETCH_OUT="$(./.venv/bin/keel --config config.paper-hourly.yaml --db keel-paperhourly.db fetch 2>&1)" || FETCH_STATUS=$?
printf '%s\n' "$FETCH_OUT"
if [ "$FETCH_STATUS" -ne 0 ]; then
    notify "keel-paper-hourly: fetch failed ahead of this cycle (exit ${FETCH_STATUS}) -- the cycle will still run against whatever cache it already has."
    printf '%s [paper-hourly] pre-cycle fetch exited %d -- continuing with the existing cache\n' \
        "$(date '+%Y-%m-%d %H:%M')" "$FETCH_STATUS"
fi

# Plain human output, not `--json`: see keel-live-run.sh's identical comment (no jq on macOS,
# and the `[FAIL]`/detail lines already carry per-product identity for what matters here).
DOCTOR_STATUS=0
DOCTOR_OUT="$(./.venv/bin/keel --config config.paper-hourly.yaml --db keel-paperhourly.db doctor 2>&1)" || DOCTOR_STATUS=$?
printf '%s\n' "$DOCTOR_OUT"
if [ "$DOCTOR_STATUS" -ne 0 ]; then
    DOCTOR_FAILS="$(printf '%s\n' "$DOCTOR_OUT" | grep -A2 '^\[FAIL\]' || true)"
    notify "keel-paper-hourly: doctor reported FAIL ahead of this cycle -- ${DOCTOR_FAILS//$'\n'/ | } -- report only."
fi

# One cycle per UTC hour; the LaunchAgent supplies the cadence and the retries. Paper mode,
# hourly rules, the profile's own database. Captured explicitly (rather than left to `set -e`
# to abort the script outright) so the POST-CYCLE doctor below still runs on a failed cycle;
# the stamp write is still skipped and the script still exits with the cycle's own status,
# exactly as before.
CYCLE_STATUS=0
./.venv/bin/keel --config config.paper-hourly.yaml --db keel-paperhourly.db agent || CYCLE_STATUS=$?

# POST-CYCLE DOCTOR -- runs regardless of CYCLE_STATUS; report only, never touches it. Catches
# a cycle that returned 0 having quietly withheld every entry on stale data, which otherwise
# reads identically to a quiet hour with no signals.
POST_DOCTOR_STATUS=0
POST_DOCTOR_OUT="$(./.venv/bin/keel --config config.paper-hourly.yaml --db keel-paperhourly.db doctor 2>&1)" || POST_DOCTOR_STATUS=$?
printf '%s\n' "$POST_DOCTOR_OUT"
if [ "$POST_DOCTOR_STATUS" -ne 0 ]; then
    POST_DOCTOR_FAILS="$(printf '%s\n' "$POST_DOCTOR_OUT" | grep -A2 '^\[FAIL\]' || true)"
    notify "keel-paper-hourly: doctor reported FAIL after this cycle -- ${POST_DOCTOR_FAILS//$'\n'/ | } -- report only."
fi

if [ "$CYCLE_STATUS" -ne 0 ]; then
    exit "$CYCLE_STATUS"
fi

printf '%s\n' "$THIS_HOUR" > "$STAMP"
