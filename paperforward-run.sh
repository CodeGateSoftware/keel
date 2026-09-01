#!/bin/bash
# Daily paper-forward runner -- ~/keel deployment (keel release install).
# One agent cycle in PAPER mode against the live READ-ONLY Coinbase API; it places NOTHING real.
# Self-contained: calls the deployment's own venv binary, so no `uv`/asdf PATH is needed.
#
# Lives OUTSIDE ~/Documents on purpose: launchd-spawned processes are not granted TCC access to
# ~/Documents, so a copy under there fails to exec with "Operation not permitted" and the daily
# cycle silently never runs. Home root is not TCC-protected.
#
# EXACTLY ONCE PER CALENDAR DAY, and it CATCHES UP. launchd re-runs a missed
# StartCalendarInterval when the machine wakes from SLEEP, but NOT when the trigger time passed
# while the machine was powered OFF -- a shutdown over 09:00 silently skipped the day (observed
# 2026-07-28: booted 09:24, `launchctl print` showed runs = 0, and the paper-forward lost the
# day). So the plist now also fires hourly through the day and on load, and the day-stamp below
# is what keeps that from running more than one cycle: the daily Turtle wants exactly one.
#
# Two guards, in order:
#   1. already stamped for today -> skip (the hourly triggers are RETRIES, not extra cycles)
#   2. before SCHED_HOUR         -> skip (an early boot must not consume the day; leave it to
#                                  the scheduled run so the cadence stays put)
# The stamp is written only AFTER a successful cycle, so a failed run (no network on wake, say)
# is retried by the next trigger rather than being recorded as done.
#
# Authored in the dev repo (gitignored). DEPLOY (copy) to ~/keel and schedule via
# com.keel.paperforward.plist.
set -euo pipefail

DIR="/Users/elmehdiaitbrahim/keel"
cd "$DIR"

STAMP="$DIR/logs/.paperforward-last-run"
SCHED_HOUR=9

# The one seam every macOS notification in this script goes through -- same shape and purpose
# as `keel-live-run.sh`'s (#642), so the SAME test-harness rewrite (`tests/test_schedule.py`'s
# `_sandbox`, and the sibling harnesses in this file's own tests) works unmodified here too.
# This script places nothing real, so a notification here is advisory, never load-bearing.
OSASCRIPT="/usr/bin/osascript"
notify() {
  "$OSASCRIPT" -e "display notification \"$1\" with title \"keel-paperforward\" subtitle \"paper\" sound name \"Glass\"" 2>/dev/null || true
}

TODAY="$(date '+%Y-%m-%d')"
# 10# forces base 10: `date +%H` yields 08/09, which arithmetic would otherwise read as octal.
HOUR="$((10#$(date '+%H')))"
STAMPED="$(cat "$STAMP" 2>/dev/null || true)"

if [ "$STAMPED" = "$TODAY" ]; then
    printf '%s [paperforward] cycle already ran today -- skipping\n' "$(date '+%Y-%m-%d %H:%M')"
    exit 0
fi

if [ "$HOUR" -lt "$SCHED_HOUR" ]; then
    printf '%s [paperforward] before %02d:00 -- leaving today to the scheduled run\n' \
        "$(date '+%Y-%m-%d %H:%M')" "$SCHED_HOUR"
    exit 0
fi

# B (#640/#642). FETCH, then DOCTOR, then the cycle, then DOCTOR again -- same shape as
# `keel-live-run.sh`; see that script's block comment for the full argument. In short: doctor
# is a REPORT here, never a gate -- keel/agent.py's whole-cycle admission bit already withholds
# every entry, on every product, the instant any rule is blocked (deliberate, closes a
# real-money duplicate-order hazard on the live path; see
# tests/test_agent.py::test_a_ready_products_order_placed_before_a_blocked_products_own_check_is_the_regression),
# so a per-product gate here would be finer than the engine and would change nothing this
# paper profile does. Neither call may abort this script (`set -e` would otherwise do exactly
# that) or change the cycle's own exit status, hence the explicit `|| STATUS=$?` capture below
# instead of letting a nonzero exit propagate.
FETCH_STATUS=0
FETCH_OUT="$(./.venv/bin/keel --config config.paperforward.yaml fetch 2>&1)" || FETCH_STATUS=$?
printf '%s\n' "$FETCH_OUT"
if [ "$FETCH_STATUS" -ne 0 ]; then
    notify "keel-paperforward: fetch failed ahead of this cycle (exit ${FETCH_STATUS}) -- the cycle will still run against whatever cache it already has."
    printf '%s [paperforward] pre-cycle fetch exited %d -- continuing with the existing cache\n' \
        "$(date '+%Y-%m-%d %H:%M')" "$FETCH_STATUS"
fi

# Plain human output, not `--json`: see keel-live-run.sh's identical comment (no jq on macOS,
# and the `[FAIL]`/detail lines already carry per-product identity for what matters here).
DOCTOR_STATUS=0
DOCTOR_OUT="$(./.venv/bin/keel --config config.paperforward.yaml doctor 2>&1)" || DOCTOR_STATUS=$?
printf '%s\n' "$DOCTOR_OUT"
if [ "$DOCTOR_STATUS" -ne 0 ]; then
    DOCTOR_FAILS="$(printf '%s\n' "$DOCTOR_OUT" | grep -A2 '^\[FAIL\]' || true)"
    notify "keel-paperforward: doctor reported FAIL ahead of this cycle -- ${DOCTOR_FAILS//$'\n'/ | } -- report only."
fi

# One cycle per day; the LaunchAgent supplies the cadence and the retries. Paper mode + funded
# config. Captured explicitly (rather than left to `set -e` to abort the script outright) so
# the POST-CYCLE doctor below still runs on a failed cycle; the stamp write is still skipped
# and the script still exits with the cycle's own status, exactly as before.
CYCLE_STATUS=0
./.venv/bin/keel --config config.paperforward.yaml agent || CYCLE_STATUS=$?

# POST-CYCLE DOCTOR -- runs regardless of CYCLE_STATUS; report only, never touches it. Catches
# a cycle that returned 0 having quietly withheld every entry on stale data, which otherwise
# reads identically to a quiet day with no signals.
POST_DOCTOR_STATUS=0
POST_DOCTOR_OUT="$(./.venv/bin/keel --config config.paperforward.yaml doctor 2>&1)" || POST_DOCTOR_STATUS=$?
printf '%s\n' "$POST_DOCTOR_OUT"
if [ "$POST_DOCTOR_STATUS" -ne 0 ]; then
    POST_DOCTOR_FAILS="$(printf '%s\n' "$POST_DOCTOR_OUT" | grep -A2 '^\[FAIL\]' || true)"
    notify "keel-paperforward: doctor reported FAIL after this cycle -- ${POST_DOCTOR_FAILS//$'\n'/ | } -- report only."
fi

if [ "$CYCLE_STATUS" -ne 0 ]; then
    exit "$CYCLE_STATUS"
fi

printf '%s\n' "$TODAY" > "$STAMP"
