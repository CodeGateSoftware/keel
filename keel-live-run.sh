#!/bin/bash
# SUPERVISED-LIVE daily DETECTOR for the 5-trend Turtle sandbox (keel v0.2, ~/keel).
#
# Runs ONE confirm-mode agent cycle HEADLESS. Because confirm mode FAILS CLOSED with no TTY
# (keel's _interactive_confirm declines when stdin is not a terminal), this NEVER places an order
# and moves no money -- it only DETECTS whether a Turtle breakout fired today. If one did, it
# posts a macOS notification telling you to run the agent INTERACTIVELY to approve it.
#
# IMPORTANT: approve SAME DAY -- a daily breakout can fade, so a signal seen today may not still
# be there tomorrow. This is a detector + reminder, not the thing that trades.
#
# EXACTLY ONCE PER CALENDAR DAY, and it CATCHES UP -- same day-stamp scheme as
# paperforward-run.sh, for the same reason: launchd re-runs a missed StartCalendarInterval on
# wake from SLEEP but NOT when the trigger passed while the machine was powered OFF, so a
# shutdown over 09:05 silently skipped the detector for the day (observed 2026-07-28). The plist
# now fires hourly and on load; the stamp keeps that to one cycle. Detecting twice would be
# harmless in itself, but it would notify twice for the same breakout.
#
# The stamp is written only after keel EXITS CLEAN, so a failed cycle is retried on the next
# trigger instead of being recorded as done -- and a detector that failed must not look like a
# quiet "no signals today".
#
# Authored in the dev repo (gitignored). DEPLOY (copy) to ~/keel and schedule via
# com.keel.live.plist. Runs from the deployment's own venv + config + db.
set -uo pipefail   # NOT -e: a nonzero exit from keel/grep must not skip the notify path

DIR="/Users/elmehdiaitbrahim/keel"
KEEL="$DIR/.venv/bin/keel"
CONFIG="config.live-sandbox.yaml"
DB="keel-live.db"
OUTLOG="$DIR/logs/keel-live.out.log"
PENDLOG="$DIR/logs/keel-live.pending.log"
STAMP="$DIR/logs/.keel-live-last-run"
SCHED_HOUR=9

cd "$DIR" || exit 1

TODAY="$(date '+%Y-%m-%d')"
# 10# forces base 10: `date +%H` yields 08/09, which arithmetic would otherwise read as octal.
HOUR="$((10#$(date '+%H')))"
STAMPED="$(cat "$STAMP" 2>/dev/null || true)"

if [ "$STAMPED" = "$TODAY" ]; then
  printf '%s [keel-live] detector already ran today -- skipping\n' "$(date '+%Y-%m-%d %H:%M')"
  exit 0
fi

if [ "$HOUR" -lt "$SCHED_HOUR" ]; then
  printf '%s [keel-live] before %02d:00 -- leaving today to the scheduled run\n' \
    "$(date '+%Y-%m-%d %H:%M')" "$SCHED_HOUR"
  exit 0
fi

# One headless confirm cycle. Places NOTHING (no TTY -> the confirm gate declines). Captures the
# LoopResult line, which reads e.g.:  [ts] mode=confirm polled=.. products=[..] signals=N entered=0 ..
OUT="$("$KEEL" --config "$CONFIG" --db "$DB" agent 2>&1)"
STATUS=$?
printf '%s\n' "$OUT" >> "$OUTLOG"

# Parse `signals=N` from the LoopResult (default 0 if the line is absent, e.g. a kill-switch skip).
SIGNALS="$(printf '%s\n' "$OUT" | grep -oE 'signals=[0-9]+' | tail -1 | cut -d= -f2)"
SIGNALS="${SIGNALS:-0}"

if [ "${SIGNALS}" -gt 0 ]; then
  MSG="${SIGNALS} Turtle signal(s) PENDING -- run the agent interactively to approve (same day)."
  # macOS notification (LaunchAgents run in your GUI session, so this shows up + plays a sound).
  /usr/bin/osascript -e "display notification \"${MSG}\" with title \"keel-live\" subtitle \"supervised live\" sound name \"Glass\"" 2>/dev/null || true
  printf '%s [keel-live] %s\n' "$(date '+%Y-%m-%d %H:%M')" "${MSG}" >> "$PENDLOG"
fi

# Only a clean cycle counts as "today is done"; anything else leaves the day open for a retry.
if [ "$STATUS" -eq 0 ]; then
  printf '%s\n' "$TODAY" > "$STAMP"
else
  printf '%s [keel-live] cycle exited %d -- not stamping, will retry\n' \
    "$(date '+%Y-%m-%d %H:%M')" "$STATUS" >> "$OUTLOG"
fi
exit "$STATUS"
