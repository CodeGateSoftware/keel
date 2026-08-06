#!/bin/bash
# SUPERVISED-LIVE daily DETECTOR for the 5-trend Turtle sandbox (keel v0.2, ~/keel).
#
# Runs ONE agent cycle HEADLESS against the live money path. Whether that cycle PLACES an order
# is decided by the AUTONOMY flag on the profile row, which `_effective_mode` reads fresh from the
# database every cycle -- not by this script, and not by config.auto_trade.mode:
#
#   autonomy ON  -- the executor runs in "autonomous" mode, the confirm gate is skipped, and a
#                   signal is PLACED here, unattended. Real money moves with nobody watching.
#   autonomy OFF -- the confirm gate runs and FAILS CLOSED with no TTY (keel's
#                   _interactive_confirm declines when stdin is not a terminal), so the cycle
#                   places nothing and only DETECTS. A macOS notification then tells you to run
#                   the agent INTERACTIVELY to approve.
#
# Check which one is live before assuming: `keel --db keel-live.db --config config.live-sandbox.yaml
# tui --once` prints the autonomy line, and each cycle logs `agent.mode_resolved`.
#
# This header used to claim the cycle "NEVER places an order". That was only ever true with
# autonomy OFF, and the deployment has run with it ON -- the comment described a safeguard that
# was not in force. Do not restore that wording without also checking the profile row.
#
# `keel autonomy off` takes effect on the NEXT cycle (a cycle already in flight can still place);
# `keel kill` is what stops trading immediately. Under BOTH settings `guards.check` runs FIRST and
# is un-overridable -- autonomy changes who is asked, never what is allowed.
#
# IMPORTANT (autonomy OFF): approve SAME DAY -- a daily breakout can fade, so a signal seen today
# may not still be there tomorrow. In that mode this is a detector + reminder, not the thing that
# trades.
#
# EXACTLY ONCE PER **UTC** DAY, and it CATCHES UP.
#
# READ THIS BEFORE TOUCHING THE STAMP. The day-stamp below is a CORRECTNESS mechanism, not
# notification hygiene. It is the ONLY thing that stops the live money path entering the same
# daily signal twice, because NOTHING DOWNSTREAM DEDUPES AN ENTRY:
#   * `get_open_positions` gates exits, reconciliation and status -- never entry;
#   * the `signals` table is written but never read back;
#   * `client_order_id` is a fresh uuid4 on every call, so the exchange cannot dedupe either;
#   * the rails in execution/guards.py are DOLLAR CAPS, not per-day counters -- a second entry
#     inside the caps passes every one of them.
# The PAPER path does gate (strategy/paper.py refuses a second entry while the product is already
# open). The LIVE path does not. So two cycles in one UTC day = two entries off one daily bar.
# This header previously described the stamp as merely avoiding a duplicate notification. That
# was wrong, and it made the stamp look optional. It is not. tests/test_schedule.py and the
# characterization tests in tests/test_agent.py pin the consequences; read them before editing.
#
# COROLLARY, and it is not theoretical: a manual `keel agent` run BYPASSES this script entirely
# and therefore bypasses the stamp. Running the agent by hand on a day the detector has already
# run can place a SECOND entry for the same breakout. If you want to re-run a cycle by hand, know
# that going in.
#
# WHY UTC, AND WHY 01:00. Daily candles close at 00:00 UTC, but turtle_breakout._completed_days
# withholds the just-closed daily bar until the 00:00-01:00 UTC HOURLY bar has closed (that guard
# stops the account simulator consuming a still-forming day; see its docstring). So 01:00 UTC is
# the earliest instant a cycle sees fresh data -- hence SCHED_HOUR=1, on the UTC clock. This used
# to gate and stamp on the LOCAL date at 09:00, i.e. 13:00/14:00 UTC, roughly twelve hours of
# avoidable lag on every breakout.
#
# THE INVARIANT. For any UTC date X the newest VISIBLE daily bar is constant -- it is X-1 --
# across the whole eligible window [01:00 UTC, 24:00 UTC). So whichever eligible trigger fires
# first on UTC date X evaluates bar X-1 and stamps X, and every later trigger that UTC day is a
# no-op: every daily bar evaluated exactly once, no missed day and no double day. That only holds
# because the stamp and the gate are on the SAME clock. A LOCAL date straddles two UTC dates, so
# the old gate could run twice within one UTC day on the catch-up path (machine off until late in
# the local day) -- see tests/test_schedule.py.
#
# CATCH-UP. launchd re-runs a missed StartCalendarInterval on wake from SLEEP but NOT when the
# trigger passed while the machine was powered OFF, so a shutdown over the scheduled hour silently
# skipped the detector for the day (observed 2026-07-28). The plist therefore fires every hour and
# on load; the stamp keeps that to one cycle. The trigger count is catch-up BREADTH, not cadence,
# and the window is now 23h rather than 12h.
#
# NOTE: paperforward-run.sh used to share this scheme verbatim and no longer does -- it is still
# LOCAL-anchored at SCHED_HOUR=9. That is a deliberate non-change, not an oversight: the paper
# path already refuses a second entry while a product is open (strategy/paper.py), so a duplicate
# cycle there is inert, and it is not worth touching a runner that places nothing real. Do not
# assume the two scripts still match.
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
# UTC hour at or after which a cycle is allowed: 01:00 UTC is the instant _completed_days stops
# withholding the daily bar that closed at 00:00 UTC. See the header. This is a UTC hour, and it
# is only meaningful because TODAY below is a UTC date too -- change one and you must change both.
SCHED_HOUR=1

cd "$DIR" || exit 1

TODAY="$(date -u '+%Y-%m-%d')"
# 10# forces base 10: `date +%H` yields 08/09, which arithmetic would otherwise read as octal.
HOUR="$((10#$(date -u '+%H')))"
STAMPED="$(cat "$STAMP" 2>/dev/null || true)"

if [ "$STAMPED" = "$TODAY" ]; then
  printf '%s [keel-live] detector already ran this UTC day (%s) -- skipping\n' \
    "$(date -u '+%Y-%m-%d %H:%M UTC')" "$TODAY"
  exit 0
fi

if [ "$HOUR" -lt "$SCHED_HOUR" ]; then
  printf '%s [keel-live] before %02d:00 UTC -- the fresh daily bar is not visible yet\n' \
    "$(date -u '+%Y-%m-%d %H:%M UTC')" "$SCHED_HOUR"
  exit 0
fi

# One headless cycle on the live money path. With autonomy ON this PLACES orders unattended; with
# it OFF the confirm gate declines for want of a TTY and nothing is placed. Captures the
# LoopResult line, which reads e.g.:  [ts] mode=confirm polled=.. products=[..] signals=N entered=0 ..
# `mode=` there is the CONFIG's mode; the cycle's effective mode is in the `agent.mode_resolved`
# event in keel-live.log, which is where you can see autonomy having taken effect.
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

# Only a clean cycle counts as "this UTC day is done"; anything else leaves the day open for one
# of the remaining hourly triggers to retry.
if [ "$STATUS" -eq 0 ]; then
  printf '%s\n' "$TODAY" > "$STAMP"
else
  printf '%s [keel-live] cycle exited %d -- not stamping, will retry\n' \
    "$(date -u '+%Y-%m-%d %H:%M UTC')" "$STATUS" >> "$OUTLOG"
fi
exit "$STATUS"
