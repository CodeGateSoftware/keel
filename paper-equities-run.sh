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
# THE WINDOW GUARD, and why this runner refuses to run outside local hours 10 to 15. The US
# regular session is 09:30 to 16:00 ET and the engine's session gate (#370 B1) skips the
# WHOLE cycle whenever the venue clock answers closed, exiting 0. So:
#   - BEFORE 10:00 (pre-open boots, RunAtLoad on an early login): a cycle would skip closed
#     and exit 0, and stamping that skip would record the day as done and suppress the real
#     evaluation at 10:00. Refuse, run nothing, stamp nothing.
#   - AT/AFTER 16:00 (after the close): same closed-market skip, same false stamp. Refuse.
# The plist's six triggers (10:00 through 15:00) sit inside the session by construction;
# the guard exists for RunAtLoad and any manual cron that misses the point. Known edge,
# documented not fixed: an early-close half-day (13:00 ET close) whose morning cycles all
# failed could have the day stamped by a 14:00 closed-skip -- a paper-evidence gap, visible
# in the log, never a money event.
#
# SESSION-AWARENESS COMES FREE from B1: weekends and holidays need no calendar here. The
# agent itself reads the venue clock, records the session (which keeps `fetch --check`
# non-alerting through the weekend) and skips with reason market_closed; that skip exits 0
# and stamping it is CORRECT cadence bookkeeping -- nothing more can happen that day.
#
# The stamp is written only AFTER a successful cycle (`set -e`), so a failed run (no network
# on wake, the venue late publishing the bar, blocked entries returning nonzero) is retried
# by the next trigger rather than being recorded as done. Note what a retry can and cannot
# do: it evaluates the NEWEST closed bar at its own time, so a day whose cycle failed through
# is covered by a later retry only in what that later evaluation sees.
#
# Authored in the dev repo. DEPLOY (copy) to ~/keel and schedule via
# com.keel.paper-equities.plist.
set -euo pipefail

DIR="/Users/elmehdiaitbrahim/keel"
cd "$DIR"

STAMP="$DIR/logs/.paper-equities-last-run"
WINDOW_START_HOUR=10
WINDOW_END_HOUR=16

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

# One cycle per UTC day; the LaunchAgent supplies the cadence and the retries. Paper mode,
# daily rules on equities, the profile's own database. A failure here stops short of the
# stamp (`set -e`), so the next trigger retries.
./.venv/bin/keel --config config.paper-equities.yaml --db keel-equities.db agent

printf '%s\n' "$TODAY" > "$STAMP"
