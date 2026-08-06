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
# THE INVARIANT, AND WHAT IT DEPENDS ON. For any UTC date X the newest VISIBLE daily bar is
# constant -- it is X-1 -- across the whole eligible window [01:00 UTC, 24:00 UTC). So whichever
# eligible trigger fires first on UTC date X evaluates bar X-1 and stamps X, and every later
# trigger that UTC day is a no-op: every daily bar evaluated exactly once, no missed day and no
# double day. That only holds because the stamp and the gate are on the SAME clock, AND because
# the machine is powered on for at least one eligible trigger during UTC date X. launchd does NOT
# re-run a StartCalendarInterval that passed while the machine was OFF (see CATCH-UP below), so a
# machine that is off from just after 01:00 UTC through the rest of UTC date X gets no eligible
# trigger at all that day, and nothing catches it up -- that UTC date is simply skipped. "No
# missed day" is conditional on power, not an unconditional guarantee. A LOCAL date straddles two
# UTC dates, so the old gate could run twice within one UTC day on the catch-up path (machine off
# until late in the local day) -- see tests/test_schedule.py.
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
# EXIT CODES this script produces itself (keel's own exit code otherwise passes straight through
# via `exit "$STATUS"` at the very end -- see the COROLLARY above for why that must never be
# masked; a masked exit code would hide the fact that a cycle failed and needs retrying):
#   64 -- `date -u` returned something that is not a well-formed date/hour (empty output counts:
#         on this hardware `$((10#$(date -u '+%H')))` on empty input evaluates to 0, not an
#         error, so an unvalidated empty clock silently reads as HOUR=0 and can make a missing
#         stamp compare equal to an empty TODAY -- "already ran", forever, with no alert).
#   65 -- the on-disk stamp is non-empty but is not a well-formed ISO date. A malformed stamp
#         must never be silently COMPARED: string `<`/`=` against garbage can come out either
#         way, and the "looks like already ran" direction disables the detector forever, the same
#         failure class as 64.
#   66 -- the day-stamp could not be proven persistable before running a cycle (pre-flight), or
#         could not be verified written back after one (post-cycle). See the PRE-FLIGHT and
#         ATOMIC STAMP WRITE comments below for why there are two layers and which one matters.
#   67 -- the on-disk stamp is a well-formed date but is STRICTLY AHEAD of today. That is not the
#         ordinary "already ran" case (stamp equal to today) -- it means either the clock read
#         forward before NTP settled, or the stamp file is otherwise corrupt, and treating it as
#         "already ran" would silently disable the detector until the real clock catches up to the
#         bogus stamp, which could be years. Distinct from 65 (stamp is not a date at all) so an
#         operator can tell the two apart at a glance -- see A2, part two, below.
#
# NOTIFICATION POLICY. A macOS notification fires for two kinds of thing:
#   (a) a condition the machine cannot self-heal without a human touching it -- exit codes
#       64/65/66/67 above; and
#   (b) a detector that has been failing an ORDINARY nonzero exit from keel itself for
#       $ESCALATE_EVERY consecutive triggers or more, and on every further multiple of
#       $ESCALATE_EVERY after that (see the consecutive-failure counter near the bottom of this
#       script). A SINGLE ordinary nonzero exit (e.g. the venue has not yet published the bar this
#       cycle needs) is EXPECTED and SELF-HEALING -- one of the remaining hourly triggers retries
#       it, and the OUTLOG line below is enough of a record -- so it does NOT notify by itself.
#       Notifying on every one of 23 triggers a day for a condition that usually resolves itself
#       would train the operator to ignore notifications, which defeats the ones that actually
#       need a human. But a detector still failing after that many tries in a row is no longer
#       "usual publication lag", and a chronically stuck detector that never notifies is
#       indistinguishable from a quiet day with no signals -- which is its own silent-failure mode,
#       the same class as 64/65/66/67, just reached by a different door.
#
# TOCTOU (Finding 8, OPTIONAL, not closed). Between reading $STAMPED and writing $STAMP, two
# CONCURRENT invocations of this script could both pass the gate and both run a cycle -- `flock`
# would close this, but it is NOT installed on this machine (verified: `which flock` fails), so
# this is documented rather than fixed. The realistic exposure is small: launchd will not start a
# job that is already running, so the SCHEDULED path cannot race itself. The actual race is a
# MANUAL `keel agent` run, or a manual invocation of this script, racing the scheduled one --
# which the COROLLARY paragraph above already warns bypasses the stamp regardless of any lock, so
# a TOCTOU fix here would not have closed the exposure that actually matters.
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
# A8 (Defect 2, MED). Consecutive-failure counter, next to $STAMP rather than inside it, so the
# stamp's own format -- a single ISO date, load-bearing for the A2 compare below -- never has to
# also carry an integer. See the increment/reset sites near the bottom of this script and the
# NOTIFICATION POLICY paragraph above.
FAILCOUNT="$STAMP.failures"
# The one seam every macOS notification in this script goes through. Same purpose as the `DIR=`
# rewrite tests/test_schedule.py::_sandbox already relies on: it lets the schedule tests swap in a
# recorder and assert BOTH that a machine-is-broken condition alerts and that an ordinary
# self-healing one does NOT -- without ever firing a real notification during a test run.
OSASCRIPT="/usr/bin/osascript"
# UTC hour at or after which a cycle is allowed: 01:00 UTC is the instant _completed_days stops
# withholding the daily bar that closed at 00:00 UTC. See the header. This is a UTC hour, and it
# is only meaningful because TODAY below is a UTC date too -- change one and you must change both.
SCHED_HOUR=1
# A8 threshold: notify once a stuck detector has failed this many triggers IN A ROW, and again on
# every further multiple (6, 9, ...). See the increment site near the bottom of this script for why
# a repeating multiple, not a single one-shot alert.
ESCALATE_EVERY=3

cd "$DIR" || exit 1

# Single seam for every macOS alert this script can raise, so the alert-worthy paths
# (64/65/66/67, plus the A8 escalation below) all read the same and none of them can forget the
# `2>/dev/null || true` that keeps a notify failure from ever masking the exit code it is
# reporting on.
notify() {
  "$OSASCRIPT" -e "display notification \"$1\" with title \"keel-live\" subtitle \"supervised live\" sound name \"Glass\"" 2>/dev/null || true
}

# A8. Read the consecutive-failure counter, guarded so ANY problem reading it -- the common case
# of the file not existing yet (a healthy detector has none), a permissions problem, or someone
# having hand-edited it to non-numeric junk -- reads as 0 rather than aborting the script or
# corrupting the retry path this file exists to support. This function, and both call sites below,
# must never be able to change $STATUS or block a retry: the failure counter is an ALERTING
# convenience, not a second correctness mechanism, and a bug in it must degrade to "stops
# escalating" rather than "stops trading" or "hides that a cycle failed".
read_failcount() {
  local n
  n="$(cat "$FAILCOUNT" 2>/dev/null || true)"
  if [[ "$n" =~ ^[0-9]+$ ]]; then
    printf '%s' "$n"
  else
    printf '0'
  fi
}

TODAY_RAW="$(date -u '+%Y-%m-%d')"
HOUR_RAW="$(date -u '+%H')"

# A1 (Finding 3, MED). Validate the RAW clock output before computing anything from it. An empty
# `date -u` -- which this hardware can produce -- would otherwise silently give TODAY="" and, via
# `$((10#$(date -u '+%H')))` on empty input evaluating to 0 (not an error), HOUR=0. A MISSING
# stamp file also reads as "" (`cat ... 2>/dev/null || true`), so `[ "" = "" ]` would read as
# "already ran" and the detector would be dead FOREVER with no alert -- the worst failure mode
# available, because it looks like success. Reject anything that is not exactly a 4-digit-hyphen
# date / 2-digit hour BEFORE touching the stamp at all: notify, and exit 64 having run nothing.
if ! [[ "$TODAY_RAW" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || ! [[ "$HOUR_RAW" =~ ^[0-9]{2}$ ]]; then
  notify "keel-live: date -u returned malformed output (date='${TODAY_RAW}' hour='${HOUR_RAW}') -- refusing to run a cycle. The system clock needs investigating."
  printf '%s [keel-live] clock invalid -- date -u gave date=%s hour=%s -- refusing to run, stamp untouched -- exit 64\n' \
    "$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || printf 'unknown')" "$TODAY_RAW" "$HOUR_RAW"
  exit 64
fi

# 10# forces base 10: `date +%H` yields 08/09, which arithmetic would otherwise read as octal.
HOUR="$((10#$HOUR_RAW))"
if [ "$HOUR" -gt 23 ]; then
  notify "keel-live: date -u returned an out-of-range hour ('${HOUR_RAW}') -- refusing to run a cycle. The system clock needs investigating."
  printf '%s [keel-live] clock invalid -- hour %s out of range -- refusing to run, stamp untouched -- exit 64\n' \
    "$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || printf 'unknown')" "$HOUR_RAW"
  exit 64
fi
TODAY="$TODAY_RAW"

STAMPED="$(cat "$STAMP" 2>/dev/null || true)"

# A2 (Finding 4, MED), part one. A non-empty stamp that is not a well-formed ISO date must never
# be silently COMPARED: `"garbage" < "2026-08-06"` is FALSE in a lexicographic string compare,
# which reads exactly like "already ran" and disables the detector forever -- the same failure
# class as A1, just entered from the stamp file instead of the clock. Refuse loudly instead.
if [ -n "$STAMPED" ] && ! [[ "$STAMPED" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  notify "keel-live: the day-stamp is corrupt ('${STAMPED}') -- refusing to run a cycle. An operator needs to inspect ${STAMP}."
  printf '%s [keel-live] stamp malformed -- %s does not read back as a date -- refusing to run, stamp not overwritten -- exit 65\n' \
    "$(date -u '+%Y-%m-%d %H:%M UTC')" "$STAMP"
  exit 65
fi

# A2, part two: a stamp that is not BEFORE today splits into two outcomes, not one. A Mac that
# boots with a bad RTC before NTP settles (RunAtLoad fires immediately, before the clock is
# trustworthy) can read a date in the PAST. With the OLD `=` compare that read as "not today", the
# cycle RAN and stamped the bogus past date; when the clock then corrected forward, the real date
# differed from that bogus stamp so the cycle ran AGAIN -- re-evaluating a bar it already traded, a
# second entry on the live money path. Requiring the stamp to be STRICTLY BEFORE today (`<`, not
# `=`) closed that: a stamp AHEAD of today (clock rolled back after a correct stamp) now reads as
# "done, do not run", same as a stamp equal to today.
#
# But collapsing "ahead" and "equal" into a single silent skip is itself a regression this same fix
# must not repeat, because a bad RTC is not only ever wrong in the PAST direction. RunAtLoad firing
# before NTP settles can just as easily read the clock FORWARD -- e.g. a default/garbage RTC date
# of 2035. That runs a cycle and stamps a date years ahead of real time; every real trigger
# afterwards then has a correctly-read TODAY that is, and stays, BEHIND that bogus stamp, so
# `! [[ "$STAMPED" < "$TODAY" ]]` is true FOREVER -- a silent, self-perpetuating outage that looks
# exactly like "already ran today", every single trigger, until the real clock catches up to the
# bogus stamp years later. Under the OLD `=` compare this self-healed the very next real day
# (STAMPED != TODAY, so the cycle ran and re-stamped the correct date); the `<` compare above traded
# that self-healing away for the rollback fix, and nothing caught the trade until now.
#
# So: a stamp STRICTLY AHEAD of today is no longer treated as "already ran". It is corrupt state,
# the same family as a stamp that is not a date at all (A2 part one, exit 65) -- something is wrong
# with the clock or the stamp file, and it needs a human, not a silent skip. A distinct exit code
# (67, not a reuse of 65) lets an operator tell the two apart at a glance: 65 says "go inspect the
# stamp file", 67 says "go check the clock (and, only if that is sane, then the stamp file)" --
# collapsing them into one code would cost that distinction for free. The rollback protection is
# unaffected: the stamp still never moves backwards and a rolled-back clock still never re-runs the
# cycle -- it now ALERTS instead of skipping silently, which is strictly better for the identical
# behaviour.
#
# ISO-8601 dates sort lexicographically, so a plain string comparison gives us date comparison for
# free. `<`/`>`/`=` inside `[[ ]]` are bash STRING comparisons, which is what we want here; `<`/`>`
# inside a POSIX `[ ]` are output/input redirection and would silently mangle a file instead of
# comparing -- `[[ ]]` is not a style choice on these lines, it is the only correct spelling.
if [ -n "$STAMPED" ] && [[ "$STAMPED" > "$TODAY" ]]; then
  notify "keel-live: the day-stamp (${STAMPED}) is AHEAD of today (${TODAY}) -- refusing to run a cycle. This means either the clock read forward before NTP settled, or the stamp file is corrupt; investigate ${STAMP} and the system clock before the next trigger. Nothing has been run and the stamp has not been touched."
  printf '%s [keel-live] stamp (%s) is AHEAD of today (%s) -- refusing to run, stamp not overwritten -- exit 67\n' \
    "$(date -u '+%Y-%m-%d %H:%M UTC')" "$STAMPED" "$TODAY"
  exit 67
fi

if [ -n "$STAMPED" ] && [ "$STAMPED" = "$TODAY" ]; then
  printf '%s [keel-live] stamp (%s) equals today (%s) -- already ran this UTC day -- skipping\n' \
    "$(date -u '+%Y-%m-%d %H:%M UTC')" "$STAMPED" "$TODAY"
  exit 0
fi

if [ "$HOUR" -lt "$SCHED_HOUR" ]; then
  printf '%s [keel-live] before %02d:00 UTC -- the fresh daily bar is not visible yet\n' \
    "$(date -u '+%Y-%m-%d %H:%M UTC')" "$SCHED_HOUR"
  exit 0
fi

# A3 (Finding 2, HIGH), layer one: PRE-FLIGHT, before invoking keel at all. Prove the stamp is
# PERSISTABLE by writing a throwaway probe file next to $STAMP, reading it back, and removing it.
# Reproduced by the reviewer with a read-only logs dir: the OLD script ran the cycle, the stamp
# write then failed SILENTLY, rc was 0, and the next trigger ran a SECOND full cycle. Exiting
# nonzero AFTER a cycle has already run does not prevent that duplicate -- if autonomy is ON the
# order is already placed, and the next trigger will still re-run because the write already
# failed once and nothing detected it. The only way to turn "duplicate real order" into "no
# trading plus a loud alert" is to refuse to trade when we cannot record that we traded. That is
# failing CLOSED, and it is the correct direction here even though it costs a trading day: a
# missed day is recoverable, a duplicate live entry is not.
PREFLIGHT_PROBE="$STAMP.preflight.$$"
if { printf 'preflight-probe\n' >"$PREFLIGHT_PROBE"; } 2>/dev/null \
    && [ "$(cat "$PREFLIGHT_PROBE" 2>/dev/null || true)" = "preflight-probe" ] \
    && rm -f "$PREFLIGHT_PROBE" 2>/dev/null; then
  : # persistable -- proceed
else
  rm -f "$PREFLIGHT_PROBE" 2>/dev/null || true
  notify "keel-live: cannot persist the day-stamp (logs directory or disk problem near ${STAMP}) -- refusing to run a cycle, because an unstamped success would duplicate on the next trigger."
  printf '%s [keel-live] pre-flight FAILED -- could not write/read/remove a probe file next to %s -- refusing to run a cycle -- exit 66\n' \
    "$(date -u '+%Y-%m-%d %H:%M UTC')" "$STAMP"
  exit 66
fi

# One headless cycle on the live money path. With autonomy ON this PLACES orders unattended; with
# it OFF the confirm gate declines for want of a TTY and nothing is placed. Captures the
# LoopResult line, which reads e.g.:  [ts] mode=confirm polled=.. products=[..] signals=N entered=0 ..
# `mode=` there is the CONFIG's mode; the cycle's effective mode is in the `agent.mode_resolved`
# event in keel-live.log, which is where you can see autonomy having taken effect.
OUT="$("$KEEL" --config "$CONFIG" --db "$DB" agent 2>&1)"
STATUS=$?
printf '%s\n' "$OUT" >>"$OUTLOG"

# Everything below is gated on a CLEAN cycle. A4 (the PENDING notification) and A3's layer two
# (the stamp write) both used to run unconditionally; both are wrong to run on a failed cycle,
# for related but distinct reasons -- see each comment below.
if [ "$STATUS" -eq 0 ]; then
  # Parse `signals=N` from the LoopResult (default 0 if the line is absent, e.g. a kill-switch
  # skip). Only trustworthy here, inside the STATUS==0 branch: on a failed cycle the captured
  # output may be partial or garbled, so any `signals=N` found in it is not something to act on.
  SIGNALS="$(printf '%s\n' "$OUT" | grep -oE 'signals=[0-9]+' | tail -1 | cut -d= -f2)"
  SIGNALS="${SIGNALS:-0}"

  if [ "$SIGNALS" -gt 0 ]; then
    # A4. Reachable ONLY when STATUS -eq 0. Per the COROLLARY at the top of this file, "run the
    # agent interactively" BYPASSES this script's stamp entirely, so firing that prompt off a
    # FAILED cycle's output would tell the operator to do by hand the exact thing the stamp
    # exists to prevent -- a direct path to a duplicate order. The old script parsed `signals=N`
    # and notified regardless of exit status; that was the bug.
    MSG="${SIGNALS} Turtle signal(s) PENDING -- run the agent interactively to approve (same day)."
    notify "$MSG"
    # A5 (Finding 10). UTC, and labelled as such, like every other timestamp in this script.
    # This used to be `date` (no `-u`) -- LOCAL, unlabelled time next to lines that are all UTC.
    printf '%s [keel-live] %s\n' "$(date -u '+%Y-%m-%d %H:%M UTC')" "${MSG}" >>"$PENDLOG"
  fi

  # A3, layer two: belt-and-braces, NOT the primary defence -- the pre-flight above is. Write to
  # a TEMP file and `mv -f` it onto $STAMP: a rename is atomic within one filesystem, so a torn
  # write can never leave $STAMP truncated or empty. (Plain `> "$STAMP"` TRUNCATES the target
  # FIRST and writes after, so a write that dies partway through -- disk full, a yanked volume --
  # leaves an EMPTY stamp, and an empty stamp reads as "never ran" and re-runs the day: the
  # truncation bug this replaces.) Then read $STAMP back and require it to equal $TODAY. This can
  # only fail if something changed between the pre-flight probe succeeding and now, but "can only
  # fail rarely" is not "cannot fail" -- and by this point a cycle has ALREADY RUN. If autonomy is
  # ON, an order may already be placed, so the notification here is not optional: failing to
  # record the stamp will not by itself stop a second cycle, only a human acting on this alert
  # before the next trigger will.
  STAMP_TMP="$STAMP.tmp.$$"
  if { printf '%s\n' "$TODAY" >"$STAMP_TMP"; } 2>/dev/null \
      && mv -f "$STAMP_TMP" "$STAMP" 2>/dev/null \
      && [ "$(cat "$STAMP" 2>/dev/null || true)" = "$TODAY" ]; then
    # A8, reset. A clean, VERIFIED stamp write means the detector is healthy again -- zero the
    # consecutive-failure counter so a later, unrelated single failure starts counting from zero
    # rather than continuing from wherever a much earlier, already-resolved outage left off. A
    # missing counter file reads as 0 (see read_failcount), so `rm -f` IS the reset; guarded the
    # same way as everything else touching $FAILCOUNT -- if the remove itself fails, the next
    # escalation check simply starts from a stale (higher) count, which only means "alerts a little
    # sooner than strictly necessary next time", never "misses" or "blocks" anything.
    rm -f "$FAILCOUNT" 2>/dev/null || true
  else
    rm -f "$STAMP_TMP" 2>/dev/null || true
    notify "keel-live: the day-stamp write FAILED after a cycle already ran -- an order may already be placed. INVESTIGATE ${STAMP} IMMEDIATELY before the next trigger."
    printf '%s [keel-live] stamp write FAILED after a clean cycle -- %s does not read back as %s -- exit 66\n' \
      "$(date -u '+%Y-%m-%d %H:%M UTC')" "$STAMP" "$TODAY" >>"$OUTLOG"
    exit 66
  fi
else
  # Only a clean cycle counts as "this UTC day is done"; anything else leaves the day open for
  # one of the remaining hourly triggers to retry. Deliberately NOT a notification by itself -- see
  # NOTIFICATION POLICY at the top: a single nonzero keel exit is an expected, self-healing
  # condition (e.g. the venue has not published the bar yet), and the OUTLOG line is enough of a
  # record for that case.
  printf '%s [keel-live] cycle exited %d -- not stamping, will retry\n' \
    "$(date -u '+%Y-%m-%d %H:%M UTC')" "$STATUS" >>"$OUTLOG"

  # A8, increment (Defect 2, MED). A single failed trigger is expected and self-healing, but
  # NOTHING ELSE in this script ever escalates a detector that keeps failing -- 23 consecutive
  # nonzero exits in one UTC day would otherwise produce zero notifications, indistinguishable from
  # a quiet day with no signals. Count consecutive failures and alert once the run is long enough
  # that "publication lag" stops being a plausible explanation.
  #
  # The read-modify-write below is guarded end to end: `read_failcount` cannot return anything but
  # a plain non-negative integer, and the write goes through the same temp-file-then-`mv -f` pattern
  # as the day-stamp itself so a torn write can never leave $FAILCOUNT truncated into something
  # read_failcount would misparse as a smaller number and under-count. If the write fails outright,
  # this cycle's failure simply goes uncounted -- the counter under-reports rather than corrupts,
  # and, critically, NONE of this can reach `exit`: a bug here must degrade to "the escalation alert
  # is late or missing", never to "the retry did not happen" or "the exit status changed".
  FAILS="$(($(read_failcount) + 1))"
  FAILCOUNT_TMP="$FAILCOUNT.tmp.$$"
  if { printf '%s\n' "$FAILS" >"$FAILCOUNT_TMP"; } 2>/dev/null \
      && mv -f "$FAILCOUNT_TMP" "$FAILCOUNT" 2>/dev/null; then
    : # counter persisted
  else
    rm -f "$FAILCOUNT_TMP" 2>/dev/null || true
  fi

  # Escalate on the Nth consecutive failure, and again on every FURTHER multiple of N (2N, 3N, ...)
  # -- deliberately not once-and-done. A one-shot alert that then goes quiet forever would let a
  # detector stuck for a week look, after the first night, exactly like one that self-healed after
  # three tries: the operator has no way to tell "resolved" from "still broken, already told you"
  # without going and checking. Repeating on a multiple keeps a genuinely stuck detector shouting
  # for as long as it stays stuck, while the modulo means it still only fires once per N tries, not
  # once per trigger -- 23 failures a day still produces a handful of alerts, not 23.
  if [ "$((FAILS % ESCALATE_EVERY))" -eq 0 ]; then
    notify "keel-live: ${FAILS} consecutive cycles have failed (latest exit ${STATUS}) -- this is no longer ordinary publication lag. Check ${OUTLOG}."
    printf '%s [keel-live] escalation: %d consecutive failures -- notified\n' \
      "$(date -u '+%Y-%m-%d %H:%M UTC')" "$FAILS" >>"$OUTLOG"
  fi
fi
exit "$STATUS"
