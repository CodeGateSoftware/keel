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

# One cycle per day; the LaunchAgent supplies the cadence and the retries. Paper mode + funded
# config. `set -e` means a failure here stops short of the stamp, so the next trigger retries.
./.venv/bin/keel --config config.paperforward.yaml agent

printf '%s\n' "$TODAY" > "$STAMP"
