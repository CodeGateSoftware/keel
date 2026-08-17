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

THIS_HOUR="$(date -u '+%Y-%m-%dT%H')"
STAMPED="$(cat "$STAMP" 2>/dev/null || true)"

if [ "$STAMPED" = "$THIS_HOUR" ]; then
    printf '%s [paper-hourly] cycle already ran this UTC hour (%s) -- skipping\n' \
        "$(date '+%Y-%m-%d %H:%M')" "$THIS_HOUR"
    exit 0
fi

# One cycle per UTC hour; the LaunchAgent supplies the cadence and the retries. Paper mode,
# hourly rules, the profile's own database. A failure here stops short of the stamp
# (`set -e`), so the next trigger retries.
./.venv/bin/keel --config config.paper-hourly.yaml --db keel-paperhourly.db agent

printf '%s\n' "$THIS_HOUR" > "$STAMP"
