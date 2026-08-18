"""The activity feed -- a chronological, newest-first reconstruction of what the agent has
actually been *doing*, grouped one row per engine cycle, built from the structured JSONL engine
log. The pure substrate for `keel tui`'s `v` overlay, kept out of `tui.py` for exactly the reason
`insights.py` and `admission.py` are: grouping and summarising are ordinary, thoroughly testable
functions, and curses rendering is not.

**Why the log, and not the database.** The dashboard already reports *state* -- equity, positions,
freshness, rails -- and state is precisely what looks dead when nothing trades: a deployment that
has run flawlessly for three weeks and correctly declined every setup shows the same zeroes as one
that crashed on day one. What distinguishes them is the *narrative*, and the narrative already
exists: every cycle emits `agent.cycle_start`, `agent.feed_polled`, `agent.signals_evaluated`,
`engine.setup_detected`/`setup_rejected`, `guards.check_failed`, `agent.enter_evaluated` ... all
correlated by the `cycle_id` `keel/agent.py` binds via `keel_core.telemetry.bind_cycle`. A new DB
table would have been the tidier design and the wrong one: it would start EMPTY on the very
deployment this feature exists to explain, reproducing the "nothing is happening" impression for
however many weeks it took to fill. The log is already months deep. No schema change, no
migration, no engine change -- this module only *reads*.

**Why this is admissible under the TUI's iron rule.** `keel tui`'s overlays are "DB reads only;
never builds a broker, never touches the network". Reading a local file is neither: nothing here
constructs a broker, opens a socket, or resolves a name. The rule's purpose is that opening an
overlay can never place an order, spend money, or hang on a remote host, and a bounded read of a
file on the same disk violates none of that. It is the same latitude `p` propose already takes to
read `config.proposals_dir`.

**The read is BOUNDED, on purpose.** The log grows without limit between rotations
(`LoggingConfig.max_file_mb` defaults to 25 MB per file), and this overlay is rebuilt on EVERY
poll while it is open -- typically every 5 seconds. Parsing the whole file would make the
dashboard's responsiveness a function of how long the deployment has been running, which is the
one thing an operator dashboard must never do. So three hard caps, all named constants below:
`_MAX_BYTES` (1 MiB) of the file's TAIL is read, at most `_MAX_LINES` (5000) lines are parsed
from it, and at most `_MAX_CYCLES` (200) cycles are retained. 1 MiB is chosen to comfortably
exceed a real deployment's whole current log (815 KB and ~330 lines, because each ERROR record
carries a full traceback) while staying a fixed, few-millisecond read against a 25 MB one. When a
cap bites, the feed SAYS so rather than silently pretending the window is the whole history.

**Everything degrades to a sentence, never a traceback.** This reads a file this process does not
own, which another process is appending to and may rotate out from under it at any moment. A
missing file, an empty one, a permissions error, a partial JSON line from a crash mid-write, a
line predating `cycle_id` entirely, an event carrying fields no version of this code has seen --
each has a defined, tested outcome, and none of them is an exception escaping to the live loop or
(worse) a blank overlay that looks exactly like the dead dashboard this feature exists to
disprove. `build_activity_feed` is total: it returns an `ActivityFeed` for every input, including
inputs that are not a log at all.

**Uncorrelated events still count.** `cycle_id` is bound per engine cycle, so events emitted
outside one -- a `keel balance` invocation's `cb_client.accounts_fetch_failed`, or anything logged
by a build that predates the correlation id -- carry none. Dropping them would hide the single
most informative thing in one real deployment's log (64 consecutive account-fetch failures). They
are instead gathered into synthetic groups: a contiguous run of uncorrelated events whose
neighbours are within `_UNCORRELATED_GAP_SEC` of each other becomes one row, which is what such
events actually are -- one failed attempt at something.

**The default view is TODAY, and that is delicate.** The feed answers "what has keel been doing",
and the honest default answer is "what it has done today" -- an operator opening the dashboard at
lunchtime wants this morning, not a fortnight of scrollback. So `build_activity_feed` scopes to
the local CALENDAR day by default (`scope_start_ts`, midnight-to-now in the same local clock
`_stamp` renders timestamps in -- NOT a rolling 24 hours, which would put "yesterday 09:00" and
"today 09:00" on the same screen every morning and neither on it every afternoon).

The delicacy is that the real deployment runs ONCE A DAY, at 09:00. "Today" therefore holds at
most one row, and before 09:00 it holds none -- and a blank panel is worse than the dead-looking
dashboard this whole feature exists to fix. Two things follow, and both are load-bearing:

* `apply_scope` retains the newest cycle that fell OUTSIDE the scope as
  `ActivityFeed.last_cycle_before_scope`, so the empty state can say *when keel last ran* and
  when the next run is due. That is one status line answering "is it alive", not a history feed --
  the distinction the "today only" requirement actually cares about.
* Scope is a parameter, never a persisted preference. `apply_scope` can widen it to `"7d"` or
  `"all"` on demand (the overlay's `t` key), but every fresh open starts at `"today"` again.

**Today interacts with the bounded read, and the interaction is reported.** The window is a
bounded TAIL, so in principle a day's cycles could sit outside it -- a busy log could push even
this morning past the 1 MiB / 5000-line cap. A feed that filtered such a window to "today" and
came back empty would be asserting something it cannot know. `ActivityFeed.scope_fully_covered`
records whether the window PROVED it reached back past the scope boundary (it did if it saw any
cycle older than that boundary, or if it read the file whole), and `footer_notes` says so out
loud when it did not, rather than letting an unread morning read as a quiet one.
"""

from __future__ import annotations

import datetime
import json
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

# -- bounds (see the module docstring) -----------------------------------------------------------

#: Bytes of the log's TAIL that are ever read in one build. The whole point of the tail read is
#: that cost is independent of how long the deployment has been running.
_MAX_BYTES = 1024 * 1024

#: Lines parsed from that window, newest kept. A second, independent cap: one MiB of a log whose
#: records are short (no tracebacks) could be tens of thousands of lines, and `json.loads` per
#: line is the expensive part, not the read.
_MAX_LINES = 5_000

#: Cycles retained after grouping, newest kept. Bounds the overlay itself, not just the parse --
#: an operator scrolls tens of rows, never thousands.
_MAX_CYCLES = 200

#: Events retained per cycle for the expanded view. Counts in the collapsed row are computed over
#: ALL of a cycle's events first, so a cycle that hits this cap still reports honest totals; only
#: the expansion is trimmed (to the newest), and it says so.
_MAX_EVENTS_PER_CYCLE = 400

#: Seconds between two consecutive `cycle_id`-less events beyond which they are treated as two
#: separate attempts rather than one. The real deployment emits these in tight pairs
#: (`cb_client.accounts_fetch_failed` + `executor.quote_fetch_failed`, same second) roughly every
#: 16 minutes, so anything between "same second" and "minutes" separates them correctly; 60s is
#: comfortably inside that gap on both sides.
_UNCORRELATED_GAP_SEC = 60.0

#: Cap on one rendered event-detail string. `exc` fields carry whole tracebacks and `violation`
#: strings carry full-precision Decimals; neither may be allowed to define the width of a row.
_DETAIL_MAX_CHARS = 220

#: How many notable markers a collapsed row shows before it summarises the rest as "+N more".
_MAX_HIGHLIGHTS = 3

#: The window of epoch seconds a `ts` is allowed to fall in -- 1970-01-01 to 2100-01-01. `ts` is
#: written by another process, and `time.localtime`/`time.strftime` raise (`OverflowError`,
#: `ValueError`) rather than degrade on a value outside the platform's `time_t`, on a NaN, or on a
#: year past 9999. A single such record -- a bad clock, a hand-edited line, a corrupted byte --
#: would otherwise reach the RENDERER, which is called from the live loop's repaint path and would
#: take the whole dashboard down with it. A timestamp outside this window is treated exactly like
#: a missing one (the neighbouring record's time is borrowed), which is both safe and, for a
#: garbled line in an append-ordered file, very nearly right.
_MIN_TS = 0.0
_MAX_TS = 4_102_444_800.0

#: Levels that count toward a cycle's `errors`.
_ERROR_LEVELS = frozenset({"ERROR", "CRITICAL", "FATAL"})

#: Payload keys `keel_core.telemetry.JsonFormatter` writes itself -- excluded from the generic
#: `key=value` fallback detail so an unrecognised event renders its OWN fields, not the envelope
#: every event shares.
_ENVELOPE_KEYS = frozenset({"ts", "level", "logger", "event", "cycle_id", "venue"})

#: The default log path, matching `keel_core.config.LoggingConfig.file`'s own default. Used only
#: when a config object cannot supply one -- the path normally comes from `logging.file` in
#: config.yaml, so a deployment that writes its log somewhere else is already configurable and
#: needs no new setting.
_DEFAULT_LOG_PATH = "logs/keel.log"


# -- scope (see the module docstring) ------------------------------------------------------------

#: The scopes the feed can be built at, in the order the overlay's `t` key cycles them. `"today"`
#: is first because it is both the default and the state every fresh open returns to.
ACTIVITY_SCOPES: tuple[str, ...] = ("today", "7d", "all")

#: What `build_activity_feed` scopes to unless told otherwise, and what the overlay reopens at
#: every single time. Deliberately NOT persisted: a widened scope is an answer to one question
#: an operator asked once, not a new default for a dashboard they will next open tomorrow.
DEFAULT_ACTIVITY_SCOPE = "today"

#: How many local calendar days each bounded scope spans, counting the current one. `"7d"` is
#: therefore today plus the six days before it -- seven DAYS, not seven times 24 hours, for the
#: same reason `"today"` is a calendar day: the deployment's unit of work is one daily cycle, so
#: a boundary that fell mid-morning would split a day's single row off from its own date.
_SCOPE_DAYS: dict[str, int] = {"today": 1, "7d": 7}


def normalise_scope(scope: str) -> str:
    """Any unrecognised scope collapses to the default rather than raising or filtering to
    nothing -- this value can arrive from a caller, and an empty screen is the one outcome this
    module is built to never produce."""
    return scope if scope in ACTIVITY_SCOPES else DEFAULT_ACTIVITY_SCOPE


def next_activity_scope(scope: str) -> str:
    """The scope `t` moves to: `today` -> `7d` -> `all` -> `today`. Cycles rather than toggles so
    one key covers all three without a modifier."""
    try:
        index = ACTIVITY_SCOPES.index(scope)
    except ValueError:
        return DEFAULT_ACTIVITY_SCOPE
    return ACTIVITY_SCOPES[(index + 1) % len(ACTIVITY_SCOPES)]


def scope_start_ts(scope: str, now_ts: float) -> float | None:
    """The epoch second a scope begins at -- LOCAL midnight of the first calendar day it covers --
    or `None` for `"all"`, which has no lower bound.

    `now_ts` is a parameter, not a `time.time()` call buried in here, so the boundary is
    injectable and every test of it is deterministic on whatever day it happens to run.

    **Local, and derived from the same clock the rows render in.** `datetime.fromtimestamp` with
    no tzinfo yields naive LOCAL time and `.timestamp()` converts it back through the local zone,
    so the boundary lands on the same civil midnight `_stamp`'s `time.localtime` would print --
    which is the only way "today" can mean what the operator reading the timestamps thinks it
    means.

    **DST is handled, and handled in the safe direction.** Going through `date` -> naive midnight
    -> `.timestamp()` uses the offset in force on THAT day, so a 23- or 25-hour day still starts
    where the civil day starts (a fixed `now_ts - 86400` would drift an hour twice a year). In
    the few zones whose transition happens AT midnight, so that 00:00 does not exist, the naive
    conversion resolves to the instant an hour before the nominal wall reading -- i.e. slightly
    EARLIER than the true day boundary. That direction is deliberate: a boundary that errs early
    can only ever include a cycle that belongs to today, never exclude one.

    Never raises: a `now_ts` outside the platform's `time_t` returns `None`, which degrades to an
    unfiltered feed rather than an empty one."""
    days = _SCOPE_DAYS.get(scope)
    if days is None:
        return None
    try:
        day = datetime.datetime.fromtimestamp(now_ts).date() - datetime.timedelta(days=days - 1)
        return datetime.datetime.combine(day, datetime.time.min).timestamp()
    except (OSError, OverflowError, ValueError):
        return None


# -- the parsed model ----------------------------------------------------------------------------


@dataclass(frozen=True)
class ActivityEvent:
    """One structured log record, normalised. `fields` holds everything that is not envelope --
    deliberately kept whole rather than projected onto a fixed schema, because the event
    vocabulary grows (every `log_event` call site invents its own kwargs) and an overlay that
    silently dropped a field it had not been taught about would be worse than one that renders it
    as `key=value`."""

    ts: float
    level: str
    event: str
    cycle_id: str | None
    fields: Mapping[str, Any]


@dataclass(frozen=True)
class ActivityCycle:
    """One engine cycle (or one synthetic group of uncorrelated events), summarised into the
    counts a collapsed row shows plus the events an expanded one lists.

    `cycle_id is None` marks a synthetic group -- see the module docstring. `key` is what the
    overlay's expansion set stores, and is stable across repaints so an expanded cycle stays
    expanded while the feed is rebuilt underneath it every poll."""

    cycle_id: str | None
    started_ts: float
    ended_ts: float
    mode: str | None
    products: tuple[str, ...]
    rules: tuple[str, ...]
    signals: int
    blocked: int
    entered: int
    exited: int
    errors: int
    highlights: tuple[str, ...]
    events: tuple[ActivityEvent, ...]
    events_dropped: int = 0

    @property
    def key(self) -> str:
        """Stable identity for the overlay's expansion set. Uncorrelated groups have no id of
        their own, so they borrow their start time -- unique enough in practice (two distinct
        groups are separated by at least `_UNCORRELATED_GAP_SEC`) and, crucially, stable across
        repaints, which is the property the expansion set actually needs."""
        if self.cycle_id is not None:
            return self.cycle_id
        return f"uncorrelated@{self.started_ts:.3f}"

    @property
    def is_uncorrelated(self) -> bool:
        return self.cycle_id is None

    @property
    def is_quiet(self) -> bool:
        """A cycle in which the agent looked at the market and nothing happened -- no signal, no
        block, no fill, no error. The overwhelming majority of rows, by design, and the reason
        the feed answers "is it alive": a long run of quiet cycles is a *positive* observation,
        not an absence of one, so quiet rows are rendered muted but never omitted."""
        return not (self.signals or self.blocked or self.entered or self.exited or self.errors)


@dataclass(frozen=True)
class ActivityFeed:
    """The whole overlay's input. Total by construction -- there is an `ActivityFeed` for a
    missing log, an empty one, and one that is not JSON at all.

    `status` is one of:

    * `"ok"` -- the log was read and at least one line parsed.
    * `"missing"` -- no file at `source`. The commonest case by far, and NOT an error: `keel tui`
      run from a directory that is not the deployment root resolves `logging.file`'s relative
      default against the wrong cwd, and the overlay says exactly that.
    * `"empty"` -- the file exists but the window held no non-blank line.
    * `"unparseable"` -- lines were read but not one of them was a JSON object. Distinguished
      from `"empty"` because the two have completely different fixes.
    * `"oversized"` -- the bounded tail read landed entirely inside a single record, so nothing
      whole survived the boundary trim. Also distinguished from `"empty"`: the file is anything
      but.
    * `"unreadable"` -- an `OSError` (permissions, a directory where a file was expected, a file
      deleted between the open and the read). `detail` carries the reason.

    `lines_skipped` counts lines that were read but could not be used -- the partial JSON a crash
    mid-write leaves behind, a line that is valid JSON but not an object, a record with no usable
    timestamp. It is surfaced in the overlay rather than swallowed: silently discarding input is
    how a feed comes to under-report reality while looking healthy.

    The `scope_*` fields describe the day-scoping `apply_scope` applied (see the module
    docstring). They default to an UNSCOPED feed -- `scope="all"`, nothing hidden, coverage
    trivially complete -- so that `feed_from_lines` stays exactly the "parse everything in the
    window" function it was, and scoping is a separate, separately-testable pass over its
    result."""

    status: str
    source: str
    cycles: tuple[ActivityCycle, ...] = ()
    detail: str | None = None
    lines_read: int = 0
    lines_skipped: int = 0
    window_truncated: bool = False
    cycles_dropped: int = 0

    #: Which scope produced `cycles` -- one of `ACTIVITY_SCOPES`.
    scope: str = "all"

    #: Local midnight the scope begins at, or `None` for `"all"` (and for a `now_ts` so broken
    #: that no boundary could be computed, which degrades to showing everything).
    scope_start_ts: float | None = None

    #: The "now" the scope was computed against. Carried so the empty state can say how long ago
    #: the last cycle was without re-reading a clock that has moved on since the feed was built.
    now_ts: float | None = None

    #: Cycles the window held that fall BEFORE the scope -- hidden, not lost. Nonzero is the
    #: normal state of a "today" view on a deployment with any history at all.
    cycles_out_of_scope: int = 0

    #: The newest cycle before the scope boundary, kept for ONE purpose: so an empty "today" can
    #: name when keel last ran. That is a status line, not a history feed -- it is what turns a
    #: blank panel into "keel has not run yet today; last cycle yesterday 09:00".
    last_cycle_before_scope: ActivityCycle | None = None

    #: Whether the bounded read PROVED it reached back past the scope boundary. False means the
    #: window may not cover the whole scope, so an empty or short feed cannot be read as "the
    #: scope was quiet" -- `footer_notes` and the empty state both say so when it is False.
    scope_fully_covered: bool = True


@dataclass(frozen=True)
class LogWindow:
    """The bounded tail read of the log file, before any parsing. Split out from
    `build_activity_feed` so the read (the only I/O in this module) and the grouping (the part
    worth testing exhaustively) never have to be exercised together."""

    lines: tuple[str, ...] = ()
    status: str = "ok"
    detail: str | None = None
    truncated: bool = False
    size_bytes: int = 0


# -- path resolution -----------------------------------------------------------------------------


def resolve_log_path(config: Any) -> Path:
    """The engine log this deployment writes, taken from `logging.file` in config.yaml (parsed
    into `keel_core.config.LoggingConfig`) rather than hardcoded -- so the overlay reads whatever
    file `keel_core.logging_setup.configure_logging` actually attached its `RotatingFileHandler`
    to, and a deployment that moved its log needs no second setting to keep this working.

    That default (`logs/keel.log`) is RELATIVE, which is a feature here and not an oversight: the
    engine resolves it against its working directory, so a `keel tui` run from the deployment root
    (`~/keel`, where the LaunchAgents run the agent and where the log therefore lives) resolves it
    to the same file. Run from anywhere else, it resolves somewhere else -- which is why a missing
    file is reported with the resolved ABSOLUTE path, so the mismatch is visible at a glance
    instead of looking like an empty history.

    Never raises: a config object without a readable `logging.file` falls back to the same default
    `LoggingConfig` itself declares."""
    raw: Any = None
    try:
        raw = config.logging.file
    except Exception:
        raw = None
    if not isinstance(raw, str) or not raw.strip():
        raw = _DEFAULT_LOG_PATH
    try:
        return Path(raw).expanduser().resolve()
    except OSError:
        # `resolve()` can raise on a path with a broken symlink component on some platforms.
        return Path(raw)


# -- the bounded read (the only I/O here) ----------------------------------------------------------


def read_log_window(
    path: Path, *, max_bytes: int = _MAX_BYTES, max_lines: int = _MAX_LINES
) -> LogWindow:
    """Read at most `max_bytes` from the END of `path`, keep at most the last `max_lines` lines.

    Three details carry the robustness:

    * **The partial first line is dropped.** Seeking to `size - max_bytes` lands mid-record
      whenever the cap bites, so everything up to and including the first newline is discarded --
      the alternative is feeding half a JSON object to the parser and counting a phantom
      malformed line on every single repaint.
    * **Decoding never raises.** `errors="replace"` -- a log with a byte sequence that is not
      UTF-8 (a truncated multi-byte character at a rotation boundary, say) degrades to a visible
      replacement character in one field, not an overlay that refuses to render.
    * **Rotation is a non-event.** The file is opened by path on every build, so once
      `RotatingFileHandler` renames the old file aside, the next build simply reads the new, small
      one. The rotated-away history is deliberately NOT chased into `keel.log.1`: an operator
      dashboard reading files the running process has already closed would be guessing at
      ordering, and the feed reports a short history honestly instead.

    Returns a `LogWindow` for every outcome. The only exceptions it does not catch are the ones
    that must not be caught (`KeyboardInterrupt`, `MemoryError` -- neither is an `OSError`)."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            truncated = size > max_bytes
            fh.seek(size - max_bytes if truncated else 0)
            blob = fh.read()
    except FileNotFoundError:
        return LogWindow(status="missing", detail=f"no engine log at {path}")
    except OSError as exc:
        return LogWindow(status="unreadable", detail=f"{type(exc).__name__}: {exc}")

    if truncated:
        newline = blob.find(b"\n")
        blob = b"" if newline < 0 else blob[newline + 1 :]

    lines = blob.decode("utf-8", errors="replace").splitlines()
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
        truncated = True

    if not any(line.strip() for line in lines):
        if truncated:
            # The window landed inside ONE record -- an ERROR line carrying a traceback can be
            # kilobytes on its own -- so the boundary trim left nothing whole behind. Reporting
            # this as "empty" would print "engine log is empty (815357 bytes)", which is
            # self-contradictory and sends an operator hunting for the wrong problem.
            return LogWindow(
                status="oversized",
                detail=(
                    f"no complete record in the newest {max_bytes // 1024} KiB of {path} "
                    f"({size} bytes total) -- one record spans the whole read window"
                ),
                truncated=True,
                size_bytes=size,
            )
        return LogWindow(
            status="empty",
            detail=f"engine log is empty ({size} bytes at {path})",
            truncated=truncated,
            size_bytes=size,
        )
    return LogWindow(lines=tuple(lines), truncated=truncated, size_bytes=size)


# -- parsing (pure) --------------------------------------------------------------------------------


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _as_timestamp(value: Any) -> float | None:
    """A `ts` this module is willing to hand to `time.localtime` -- see `_MIN_TS`/`_MAX_TS`.

    `nan` falls out for free: every comparison against it is `False`, so it fails the range test
    without needing a separate `math.isnan` check. `inf` fails it the same way."""
    ts = _as_float(value)
    if ts is None or not (_MIN_TS <= ts <= _MAX_TS):
        return None
    return ts


def _as_int(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value))
        except (ValueError, OverflowError):
            # `float("inf")` PARSES -- it is `int()` that then raises `OverflowError`, which is
            # not a `ValueError`. Letting that escape would abort the whole grouping pass over a
            # single garbled `signal_count`, discarding every other, perfectly good cycle in the
            # window: one bad line would empty the entire feed.
            return 0
    return 0


def _as_bool(value: Any) -> bool:
    """`placed` is written as a real JSON bool today, but a string `"false"` (from an older build,
    or a field that went through an f-string on the way in) must never read as True -- which is
    precisely what a bare `bool(value)` would do, and would have this feed report an entry that
    never happened."""
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


def parse_events(lines: Iterable[str]) -> tuple[list[ActivityEvent], int]:
    """Parse JSONL into `ActivityEvent`s, returning them with the count of lines that could not
    be used. PURE -- takes an iterable of strings, so every degradation is testable without a
    filesystem.

    A record with no usable `ts` inherits the previous record's timestamp rather than being
    dropped: the log is append-ordered, so its neighbour's time is very nearly right, and showing
    an event slightly imprecisely beats hiding it. Only a record with no usable timestamp AND no
    predecessor to borrow one from is unplaceable, and is counted as skipped."""
    events: list[ActivityEvent] = []
    skipped = 0
    last_ts: float | None = None

    for raw in lines:
        text = raw.strip()
        if not text:
            continue
        try:
            obj = json.loads(text)
        except ValueError:
            # A partial line from a crash mid-write, a rotation boundary, or simply not JSON.
            skipped += 1
            continue
        if not isinstance(obj, dict):
            skipped += 1
            continue

        ts = _as_timestamp(obj.get("ts"))
        if ts is None:
            if last_ts is None:
                skipped += 1
                continue
            ts = last_ts
        last_ts = ts

        raw_event = obj.get("event")
        event = raw_event if isinstance(raw_event, str) and raw_event else "(unnamed event)"
        raw_level = obj.get("level")
        level = raw_level.upper() if isinstance(raw_level, str) and raw_level else "INFO"
        raw_cycle = obj.get("cycle_id")
        cycle_id = raw_cycle if isinstance(raw_cycle, str) and raw_cycle else None

        events.append(
            ActivityEvent(
                ts=ts,
                level=level,
                event=event,
                cycle_id=cycle_id,
                fields={k: v for k, v in obj.items() if k not in _ENVELOPE_KEYS},
            )
        )
    return events, skipped


# -- grouping + summarising (pure -- the part worth testing hardest) -------------------------------


def _short_num(value: Any, places: int = 4) -> str:
    """Trim a full-precision numeric string for display. The engine logs Decimals verbatim
    (`stop="4197.09381782563408"`), which is right for a machine-readable log and wrong for a row
    an operator scans -- but truncating in the LOG would lose real precision, so it is done here,
    at the only place that has a column budget.

    `places` is a floor, not a ceiling, and that matters: keel trades sub-dollar assets alongside
    four-figure ones, so a fixed two-decimal trim that reads perfectly as `4197.09` for PAXG turns
    XLM's `0.161297` into `0.16` and ADA's entry and stop into the same number. Whenever the
    integer part is zero, four more decimals are kept, so the small-priced products stay legible
    without widening the big-priced ones."""
    text = str(value).strip()
    if "." not in text:
        return text
    head, _, tail = text.partition(".")
    if head.lstrip("-") in {"", "0"}:
        places += 4
    tail = tail[:places].rstrip("0")
    return f"{head}.{tail}" if tail else head


def _first_clause(text: str) -> str:
    """The leading identifier of a `violation` string -- `"per_asset_concentration_cap: PAXG
    exposure ... exceeds ..."` -> `"per_asset_concentration_cap"`. Which rail said no is what
    belongs in a one-line summary; the arithmetic behind it belongs in the expansion."""
    head = text.split(":", 1)[0].strip()
    return head or text.strip()


def _last_exc_line(text: str) -> str:
    """The final line of a traceback -- the exception type and message. The frames above it are
    the single largest thing in this log (they are why 330 lines occupy 815 KB) and the least
    useful part of it on a dashboard."""
    for line in reversed(text.splitlines()):
        if line.strip():
            return line.strip()
    return text.strip()


def _add(seq: list[str], value: Any) -> None:
    """Append `value` to `seq` if it is a non-empty string not already present -- order-preserving
    de-duplication, used for the product/rule breadth and the highlight markers."""
    if isinstance(value, str) and value and value not in seq:
        seq.append(value)


def summarise_cycle(cycle_id: str | None, events: Sequence[ActivityEvent]) -> ActivityCycle:
    """Turn one cycle's events into the row the overlay shows. PURE.

    The five counts are deliberately the ones `keel agent`'s own human run-log one-liner already
    taught an operator to read (`signals=0 blocked=0 entered=0 exited=0`), plus `errors`:

    * `signals` -- setups the rules produced, summed from `agent.signals_evaluated.signal_count`
      (which is `len(enter_signals)`, exactly what the run-log line counts). Falls back to
      counting `engine.setup_detected` for a cycle whose `signals_evaluated` records fell outside
      the read window.
    * `blocked` -- entries that did NOT become an order: an `agent.enter_evaluated` with
      `placed=false` (rails vetoed it, the routing-time entry-spread gate refused the live
      BUY's book, or the confirm gate declined), plus an
      `agent.entry_bar_not_ready` (withheld before it was ever evaluated). One per entry, never
      one per *reason* -- the PAXG cycle of 2026-08-08 trips two guards on a single signal, and
      reporting `blocked=2` there would imply two setups where there was one.
    * `entered`/`exited` -- `agent.enter_evaluated`/`agent.exit_evaluated` with `placed=true`.
    * `errors` -- ERROR-or-worse records, whatever emitted them.

    `highlights` is what makes a notable cycle notable at a glance, and is the reason the counts
    alone were not enough: `blocked=1` says an entry did not happen, but not that
    `per_asset_concentration_cap` is why, and the difference between those two is the difference
    between "keel is broken" and "keel is working exactly as designed"."""
    signals = 0
    saw_signal_counts = False
    setups = 0
    blocked = 0
    entered = 0
    exited = 0
    errors = 0
    mode: str | None = None
    products: list[str] = []
    rules: list[str] = []
    highlights: list[str] = []

    for ev in events:
        fields = ev.fields
        name = ev.event
        if ev.level in _ERROR_LEVELS:
            errors += 1
            _add(highlights, f"error: {name}")

        _add(products, fields.get("product"))
        _add(rules, fields.get("rule"))
        polled = fields.get("products")
        if isinstance(polled, list):
            for item in polled:
                _add(products, item)

        if name == "agent.signals_evaluated":
            saw_signal_counts = True
            signals += max(0, _as_int(fields.get("signal_count")))
        elif name == "engine.setup_detected":
            setups += 1
        elif name == "agent.mode_resolved":
            raw_mode = fields.get("mode")
            if isinstance(raw_mode, str) and raw_mode:
                mode = raw_mode
        elif name == "agent.enter_evaluated":
            if _as_bool(fields.get("placed")):
                entered += 1
                _add(highlights, f"ENTERED {fields.get('product', '?')}")
            else:
                blocked += 1
                reason = fields.get("reason")
                if isinstance(reason, str) and reason:
                    _add(highlights, f"not placed: {reason}")
                else:
                    _add(highlights, f"not placed: {fields.get('product', '?')}")
        elif name == "agent.exit_evaluated":
            if _as_bool(fields.get("placed")):
                exited += 1
                _add(highlights, f"EXITED {fields.get('product', '?')}")
        elif name == "agent.entry_bar_not_ready":
            blocked += 1
        elif name == "guards.check_failed":
            violation = fields.get("violation")
            if isinstance(violation, str) and violation:
                _add(highlights, f"rail veto: {_first_clause(violation)}")
            else:
                _add(highlights, "rail veto")
        elif name == "engine.setup_rejected":
            _add(
                highlights,
                f"gate rejected: {fields.get('gate', '?')} ({fields.get('product', '?')})",
            )
        elif name == "agent.entries_withheld":
            _add(highlights, f"entries withheld ({_as_int(fields.get('blocked_count'))} blocked)")
        elif name == "agent.cycle_skipped":
            _add(highlights, f"cycle skipped: {fields.get('reason', '?')}")
        elif name == "agent.feed_stale":
            _add(highlights, f"stale feed: {fields.get('product', '?')}")
        elif name == "equity.external_flow_recorded":
            _add(highlights, f"external flow {fields.get('amount', '?')}")
        elif name == "equity.unexplained_jump":
            _add(highlights, "UNEXPLAINED equity jump")

    if not saw_signal_counts:
        signals = setups

    kept = list(events[-_MAX_EVENTS_PER_CYCLE:])
    return ActivityCycle(
        cycle_id=cycle_id,
        started_ts=events[0].ts if events else 0.0,
        ended_ts=events[-1].ts if events else 0.0,
        mode=mode,
        products=tuple(sorted(products)),
        rules=tuple(sorted(rules)),
        signals=signals,
        blocked=blocked,
        entered=entered,
        exited=exited,
        errors=errors,
        highlights=tuple(highlights),
        events=tuple(kept),
        events_dropped=max(0, len(events) - len(kept)),
    )


def group_cycles(events: Sequence[ActivityEvent]) -> list[ActivityCycle]:
    """Group events into cycles, in first-seen order. PURE.

    Correlated events are bucketed by `cycle_id` through a dict, not by contiguity, so a cycle
    whose events are interleaved with another process's writes (two `keel` processes share one
    log file) still comes back as one row. Uncorrelated events -- see the module docstring -- are
    grouped by contiguity plus a `_UNCORRELATED_GAP_SEC` time gap instead, because contiguity is
    the only signal they carry."""
    by_cycle: dict[str, list[ActivityEvent]] = {}
    order: list[tuple[str | None, list[ActivityEvent]]] = []

    for ev in events:
        if ev.cycle_id is None:
            tail = order[-1] if order else None
            if (
                tail is not None
                and tail[0] is None
                and ev.ts - tail[1][-1].ts <= _UNCORRELATED_GAP_SEC
            ):
                tail[1].append(ev)
            else:
                order.append((None, [ev]))
            continue
        bucket = by_cycle.get(ev.cycle_id)
        if bucket is None:
            bucket = []
            by_cycle[ev.cycle_id] = bucket
            order.append((ev.cycle_id, bucket))
        bucket.append(ev)

    return [summarise_cycle(cycle_id, evs) for cycle_id, evs in order]


def feed_from_lines(
    lines: Iterable[str],
    *,
    source: str = "",
    truncated: bool = False,
    max_cycles: int = _MAX_CYCLES,
) -> ActivityFeed:
    """THE pure core: JSONL lines in, a newest-first `ActivityFeed` out. Everything the overlay
    shows is decided here, with no filesystem, no config, and no curses in sight.

    Newest-first is the whole point of the view -- "what has keel been doing" is answered from the
    top down -- so cycles are sorted by start time descending, tie-broken by their position in the
    file descending, which keeps two cycles that share a timestamp in a stable, reverse-file
    order rather than an arbitrary one."""
    events, skipped = parse_events(lines)
    cycles = group_cycles(events)
    ordered = [
        cycle
        for _, cycle in sorted(
            enumerate(cycles), key=lambda pair: (pair[1].started_ts, pair[0]), reverse=True
        )
    ]
    dropped = max(0, len(ordered) - max_cycles)
    kept = ordered[:max_cycles]

    if not events:
        status = "unparseable" if skipped else "empty"
    else:
        status = "ok"

    return ActivityFeed(
        status=status,
        source=source,
        cycles=tuple(kept),
        detail=None,
        lines_read=len(events) + skipped,
        lines_skipped=skipped,
        window_truncated=truncated or dropped > 0,
        cycles_dropped=dropped,
    )


def apply_scope(
    feed: ActivityFeed, scope: str = DEFAULT_ACTIVITY_SCOPE, *, now_ts: float | None = None
) -> ActivityFeed:
    """Narrow an already-built feed to a scope. PURE, and a SEPARATE pass over `feed_from_lines`'s
    result rather than a parameter threaded through it -- which keeps "parse the window" and
    "decide which days to show" independently testable, and keeps `feed_from_lines` the unscoped
    function every existing caller and test already relies on.

    `now_ts` is injected (defaulting to `time.time()` only at this one seam) so every test of the
    day boundary is deterministic regardless of the day it runs on.

    Three things are recorded besides the filtered `cycles`, and each exists because dropping it
    would let the overlay assert something it cannot know:

    * `cycles_out_of_scope` -- how much was hidden, so the header can say so instead of
      implying the window is the whole log.
    * `last_cycle_before_scope` -- the NEWEST cycle just outside the boundary, the one fact that
      turns an empty "today" into an answer to "is keel alive".
    * `scope_fully_covered` -- True if the window contained anything older than the boundary (so
      the boundary itself was inside the window and nothing before it can be missing), or if the
      read was not truncated at all. False means the bounded tail begins somewhere inside the
      scope, and an empty result there means "not seen", not "did not happen".

    Filtering is on `started_ts`, the timestamp the row itself renders, and the boundary is
    INCLUSIVE (`>= start`) so a cycle at exactly local midnight belongs to the day beginning
    then -- the same convention every calendar uses, and the only one under which two adjacent
    days cannot both claim it or both disown it.

    There is deliberately NO upper bound at `now_ts`. "Today" is the calendar day, and in live
    use `now_ts` is the current instant, so midnight-to-now and midnight-to-midnight contain
    exactly the same records -- the only thing an upper bound could ever exclude is a record
    stamped in the future, i.e. one written by a process whose clock is ahead. Hiding that would
    be the wrong call in this module of all modules: an operator is far better served by seeing
    the anomalous row, with its odd timestamp on display, than by a panel that quietly shrinks
    and offers no hint why."""
    scope = normalise_scope(scope)
    if now_ts is None:
        now_ts = time.time()
    start = scope_start_ts(scope, now_ts)
    if start is None:
        return replace(
            feed,
            scope=scope,
            scope_start_ts=None,
            now_ts=now_ts,
            cycles_out_of_scope=0,
            last_cycle_before_scope=None,
            scope_fully_covered=True,
        )

    in_scope = tuple(c for c in feed.cycles if c.started_ts >= start)
    # `feed.cycles` is newest-first, so the first cycle below the boundary is the most recent one
    # outside the scope -- exactly the "when did it last run" the empty state needs.
    before = [c for c in feed.cycles if c.started_ts < start]
    return replace(
        feed,
        cycles=in_scope,
        scope=scope,
        scope_start_ts=start,
        now_ts=now_ts,
        cycles_out_of_scope=len(before),
        last_cycle_before_scope=before[0] if before else None,
        scope_fully_covered=bool(before) or not feed.window_truncated,
    )


def build_activity_feed(
    config: Any,
    *,
    max_bytes: int = _MAX_BYTES,
    max_lines: int = _MAX_LINES,
    max_cycles: int = _MAX_CYCLES,
    scope: str = DEFAULT_ACTIVITY_SCOPE,
    now_ts: float | None = None,
) -> ActivityFeed:
    """Resolve the log path from config, read its bounded tail, and build the feed. The thin I/O
    seam over `feed_from_lines`.

    TOTAL, and the outer `except Exception` is not defensive padding: this is called from the
    live loop's repaint path, where an escaping exception would take the dashboard down for a
    problem in a file this process does not own. `read_log_window` already turns every
    `OSError` into a status; this catches whatever a config object shaped differently than
    expected could still throw, and turns it into the same kind of readable sentence.

    `scope` defaults to TODAY -- the local calendar day -- because that is what "what has keel
    been doing" means to someone opening the dashboard now. `now_ts` is injectable so the day
    boundary is a parameter of this call rather than a hidden clock read, which is what makes the
    whole scoping layer deterministic under test. A non-ok window is scope-stamped too, so the
    overlay's header reads the same whether the log parsed or not."""
    scope = normalise_scope(scope)
    try:
        if now_ts is None:
            now_ts = time.time()
        path = resolve_log_path(config)
        window = read_log_window(path, max_bytes=max_bytes, max_lines=max_lines)
        if window.status != "ok":
            base = ActivityFeed(status=window.status, source=str(path), detail=window.detail)
        else:
            base = feed_from_lines(
                window.lines,
                source=str(path),
                truncated=window.truncated,
                max_cycles=max_cycles,
            )
        return apply_scope(base, scope, now_ts=now_ts)
    except Exception as exc:
        return ActivityFeed(
            status="unreadable",
            source="",
            detail=f"{type(exc).__name__}: {str(exc)[:160]}",
            scope=scope,
        )


# -- rendering helpers (pure text; the overlay adds the styles) ---------------------------------


#: What a timestamp renders as when it cannot be rendered at all. `parse_events` already rejects
#: out-of-range values, so nothing coming through the normal path reaches this -- it is the
#: backstop for a hand-built `ActivityCycle`/`ActivityEvent` (a caller, a future CLI view) whose
#: `ts` never went through the parser. These two functions are called from the live loop's repaint
#: path, where a raised `OverflowError` would take the dashboard down; a visible `??:??:??` is a
#: far better outcome than that.
_UNRENDERABLE_TS = "??"


def _safe_strftime(fmt: str, ts: float, width: int) -> str:
    try:
        return time.strftime(fmt, time.localtime(ts))
    except (OSError, OverflowError, ValueError):
        return _UNRENDERABLE_TS * (width // len(_UNRENDERABLE_TS))


def _clock(ts: float) -> str:
    """Local `HH:MM:SS` for an expanded event row -- the cycle's own row already carries the
    date, and repeating it on every event line would cost a fifth of the width for nothing."""
    return _safe_strftime("%H:%M:%S", ts, 8)


def _stamp(ts: float) -> str:
    """Local `YYYY-MM-DD HH:MM:SS`, matching `keel/commands/tui.py`'s `_human_dt` so the feed's
    timestamps and the dashboard's read identically. `ts` is a float epoch in the log;
    `localtime` accepts it directly."""
    return _safe_strftime("%Y-%m-%d %H:%M:%S", ts, 19)


#: The column header for the collapsed rows, built with the SAME field widths
#: `render_cycle_row` uses so the two cannot drift out of alignment when either is edited.
ACTIVITY_HEADER = (
    f"   {'when':<19}  {'mode':<7} {'sig':>3}  {'blk':>3}  {'ent':>3}  "
    f"{'exi':>3}  {'err':>3}  what happened"
)


def render_cycle_row(
    cycle: ActivityCycle, *, selected: bool = False, expanded: bool = False
) -> str:
    """The collapsed one-line summary of a cycle, aligned under `ACTIVITY_HEADER`.

    Counts are abbreviated to fit five columns on an 80-column terminal WITH the notable markers
    still visible, which is the trade this row exists to make: `signals=0 blocked=0 entered=0
    exited=0 errors=0` spelled out consumes 43 columns to say "nothing happened", and would push
    the one genuinely informative part of a notable row off the right edge (`_paint` clips). The
    header spells them out once, above."""
    caret = "▾" if expanded else "▸"
    marker = ">" if selected else " "
    mode = (cycle.mode or ("--" if cycle.is_uncorrelated else "?"))[:7]
    tail_parts: list[str] = []
    if cycle.is_uncorrelated:
        tail_parts.append("uncorrelated events (no cycle_id)")
    elif cycle.products:
        breadth = f"{len(cycle.products)} products"
        if cycle.rules:
            breadth += f" / {', '.join(cycle.rules[:2])}"
        tail_parts.append(breadth)
    if cycle.highlights:
        shown = list(cycle.highlights[:_MAX_HIGHLIGHTS])
        extra = len(cycle.highlights) - len(shown)
        if extra > 0:
            shown.append(f"+{extra} more")
        tail_parts.append(" | ".join(shown))
    elif cycle.is_quiet and not cycle.is_uncorrelated:
        tail_parts.append("quiet -- looked, nothing to do")
    tail = "  ".join(tail_parts)
    return (
        f"{marker}{caret} {_stamp(cycle.started_ts)}  {mode:<7} "
        f"{cycle.signals:>3}  {cycle.blocked:>3}  {cycle.entered:>3}  "
        f"{cycle.exited:>3}  {cycle.errors:>3}  {tail}"
    )


def cycle_style(cycle: ActivityCycle) -> str:
    """Which `ScreenLine` style a collapsed row carries -- the "at a glance" half of the design.

    An error run is the loudest thing in this feed and gets `"alert"`, because on the real
    deployment it is also the true answer to "why has nothing traded": weeks of
    `cb_client.accounts_fetch_failed` are not background noise. A real fill is `"ok"`. Anything
    withheld or vetoed is `"warn"` -- the system working as designed, not an emergency, exactly
    the distinction `keel/commands/tui.py::_admission_line_style` already draws for a REJECT. A
    cycle that produced signals but placed nothing is `"normal"`, and a quiet cycle is `"muted"`
    so that a long run of them recedes into a calm background against which anything else stands
    out."""
    if cycle.errors:
        return "alert"
    if cycle.entered or cycle.exited:
        return "ok"
    if cycle.blocked or cycle.highlights:
        return "warn"
    if cycle.signals:
        return "normal"
    return "muted"


def event_style(ev: ActivityEvent) -> str:
    """Style for one expanded event line. `engine.no_signal` is muted on purpose: it is the
    single most numerous event in the log (one per product per cycle) and it reports the absence
    of news, so letting it read at the same weight as a rail veto would bury the veto."""
    if ev.level in _ERROR_LEVELS:
        return "alert"
    if ev.event in {"guards.check_failed", "engine.setup_rejected", "agent.entry_bar_not_ready"}:
        return "warn"
    if ev.event == "agent.enter_evaluated":
        return "ok" if _as_bool(ev.fields.get("placed")) else "warn"
    if ev.event == "agent.exit_evaluated":
        return "ok" if _as_bool(ev.fields.get("placed")) else "normal"
    if ev.event in {"engine.setup_detected", "equity.external_flow_recorded"}:
        return "ok"
    if ev.event in {"engine.no_signal", "agent.signals_evaluated"}:
        return "muted"
    return "normal"


def _generic_detail(ev: ActivityEvent) -> str:
    """The fallback for an event this module has never been taught about -- every non-envelope
    field as `key=value`. Deliberately NOT a "(no detail)" placeholder: the event vocabulary
    grows faster than this renderer will, and a new event showing its raw fields is useful, while
    one showing nothing is a bug that hides itself."""
    parts = []
    for key, value in ev.fields.items():
        if key == "exc" and isinstance(value, str):
            parts.append(f"exc: {_last_exc_line(value)}")
        else:
            parts.append(f"{key}={value}")
    return " ".join(parts)


def render_event_detail(ev: ActivityEvent) -> str:
    """The human-readable right-hand side of one expanded event line -- the fields that MEAN
    something for the events this system actually emits: a `violation` in full (an operator needs
    the arithmetic, which is exactly what the collapsed row's `_first_clause` drops), the `gate`
    that rejected a setup, the `reason` an entry was not placed, and a setup's entry/stop/target.

    Never raises on a missing or oddly-typed field -- every access is a `.get` with a visible `?`
    fallback, because the whole file is written by a different process and possibly by a
    different version of this codebase."""
    f = ev.fields
    name = ev.event
    detail: str

    if name == "agent.cycle_start":
        detail = "cycle begins"
    elif name == "agent.feed_polled":
        polled = f.get("products")
        listed = ", ".join(str(p) for p in polled) if isinstance(polled, list) else "?"
        detail = (
            f"polled {f.get('candles_polled', '?')} candles, "
            f"{f.get('rule_count', '?')} rules: {listed}"
        )
    elif name == "agent.mode_resolved":
        detail = f"mode={f.get('mode', '?')}"
    elif name == "agent.paper_equity":
        detail = (
            f"equity={f.get('equity', '?')} drawdown total={f.get('dd_total', '?')} "
            f"weekly={f.get('dd_weekly', '?')}"
        )
    elif name == "agent.signals_evaluated":
        detail = (
            f"{f.get('product', '?')}: {f.get('signal_count', '?')} signal(s) "
            f"from {f.get('rule_count', '?')} rule(s)"
        )
    elif name == "engine.no_signal":
        detail = (
            f"{f.get('product', '?')} {f.get('rule', '?')}: no signal at gate "
            f"'{f.get('gate', '?')}' -- close={_short_num(f.get('close', '?'))} "
            f"entry_level={_short_num(f.get('entry_level', '?'))} "
            f"gap={_short_num(f.get('gap_pct', '?'), 2)}%"
        )
    elif name == "engine.setup_detected":
        detail = (
            f"{f.get('product', '?')} {f.get('rule', '?')}/{f.get('technique', '?')} "
            f"cts={f.get('cts_score', '?')} entry={_short_num(f.get('entry', '?'), 2)} "
            f"stop={_short_num(f.get('stop', '?'), 2)} "
            f"target={_short_num(f.get('target', '?'), 2)}"
        )
    elif name == "engine.setup_rejected":
        detail = (
            f"{f.get('product', '?')} {f.get('rule', '?')}: REJECTED by gate "
            f"'{f.get('gate', '?')}'"
        )
    elif name == "guards.check_failed":
        detail = (
            f"{f.get('product', '?')} {f.get('side', '?')} VETOED -- {f.get('violation', '?')}"
        )
    elif name == "agent.enter_evaluated":
        verdict = "PLACED" if _as_bool(f.get("placed")) else "NOT PLACED"
        detail = (
            f"{f.get('product', '?')} {f.get('rule', '?')}/{f.get('technique', '?')} "
            f"cts={f.get('cts_score', '?')} -> {verdict} -- {f.get('reason', 'no reason given')}"
        )
    elif name == "agent.exit_evaluated":
        verdict = "PLACED" if _as_bool(f.get("placed")) else "not placed"
        detail = (
            f"{f.get('product', '?')} exit -> {verdict} -- "
            f"{f.get('reason', 'no reason given')}"
        )
    elif name == "agent.entry_bar_not_ready":
        detail = (
            f"{f.get('product', '?')} {f.get('rule', '?')} {f.get('granularity', '?')}: "
            f"entry WITHHELD -- {f.get('reason', '?')} (bars_behind={f.get('bars_behind', '?')})"
        )
    elif name == "agent.entries_withheld":
        detail = (
            f"every entry withheld this cycle -- {f.get('blocked_count', '?')} blocked on "
            f"{f.get('products', '?')}"
        )
    elif name == "agent.feed_stale":
        detail = (
            f"{f.get('product', '?')}: feed STALE at {f.get('finest', '?')} "
            f"(max_age_sec={f.get('max_age_sec', '?')})"
        )
    elif name == "agent.cycle_skipped":
        detail = f"cycle SKIPPED -- {f.get('reason', '?')}"
    elif name == "equity.external_flow_recorded":
        detail = (
            f"external flow {f.get('amount', '?')} -- high-water mark rebased to "
            f"{f.get('rebased_hwm', '?')}"
        )
    else:
        detail = _generic_detail(ev)

    detail = " ".join(detail.split())
    if len(detail) > _DETAIL_MAX_CHARS:
        detail = detail[: _DETAIL_MAX_CHARS - 3] + "..."
    return detail


def render_event_row(ev: ActivityEvent) -> str:
    """One expanded event line: local clock, the stable event id (never an interpolated sentence
    -- the id is what an operator greps the raw log for), then the meaningful fields."""
    return f"      {_clock(ev.ts)}  {ev.event:<30}  {render_event_detail(ev)}"


def describe_status(feed: ActivityFeed) -> list[str]:
    """The lines shown INSTEAD of a feed when there is no feed to show -- one plain statement of
    what is wrong and one of what to do about it. This is the function that keeps a broken log
    from rendering as a blank overlay, which would be indistinguishable from the dead-looking
    dashboard this whole feature exists to fix."""
    if feed.status == "missing":
        return [
            f"No engine log found at {feed.source}.",
            "",
            "The activity feed reads the structured JSONL log the engine writes -- the path comes",
            "from `logging.file` in config.yaml (default `logs/keel.log`, RESOLVED AGAINST THE",
            "WORKING DIRECTORY). Run `keel tui` from the deployment root (the directory holding",
            "config.yaml and logs/), or set `logging.file` to an absolute path.",
            "",
            "Nothing is wrong with the agent -- this overlay simply has nothing to read.",
        ]
    if feed.status == "empty":
        return [
            f"The engine log at {feed.source} is empty.",
            "",
            "It exists but holds no records yet. `logging.verbose: false` (the default) records",
            "only errors, so a healthy deployment writes nothing here until something fails --",
            "set `logging.verbose: true` in config.yaml to record each cycle's decisions, which",
            "is what this feed is built to show.",
        ]
    if feed.status == "oversized":
        return [
            f"No complete record could be read from {feed.source}.",
            "",
            f"{feed.detail or 'no further detail'}",
            "",
            "The feed reads only the TAIL of the log so that a months-old deployment can never",
            "slow this dashboard down. A single record larger than that whole window -- an error",
            "carrying a very large traceback -- leaves nothing whole to parse.",
        ]
    if feed.status == "unparseable":
        return [
            f"Nothing in {feed.source} could be parsed as a log record.",
            "",
            f"{feed.lines_skipped} line(s) were read and none was a JSON object. The engine writes",
            "one JSON object per line (`keel_core.telemetry.JsonFormatter`); a file in any other",
            "format is not the log this reads.",
        ]
    return [
        "The engine log could not be read.",
        "",
        f"{feed.detail or 'no further detail'}",
        "",
        f"Path tried: {feed.source or 'unresolved'}",
    ]


# -- scope rendering, and the empty state that must never be blank -------------------------------


#: What the feed says when it is scoped to everything and STILL has nothing -- a readable log
#: holding no groupable event at all. Distinct from `describe_status`'s cases, which are about the
#: FILE rather than its contents.
_NO_CYCLES_AT_ALL = "No cycles in the window -- the log was read, but held no grouped events."


def scope_label(scope: str, start_ts: float | None) -> str:
    """The scope named the way an operator would say it, with the date it actually begins at.
    Naming the DATE matters: "today" is ambiguous next to a row stamped `2026-08-11 09:00`
    unless the header says which day "today" is."""
    if scope == "today":
        return f"today ({_safe_strftime('%Y-%m-%d', start_ts, 10)})" if start_ts else "today"
    if scope == "7d":
        return (
            f"last 7 days (from {_safe_strftime('%Y-%m-%d', start_ts, 10)})"
            if start_ts
            else "last 7 days"
        )
    return "all history in the window"


def _scope_noun(scope: str) -> str:
    """The scope as it reads mid-sentence -- "not run yet TODAY", "cycles from THE LAST 7 DAYS"."""
    return "today" if scope == "today" else "the last 7 days"


def scope_headline(feed: ActivityFeed) -> str:
    """The one line under the overlay's title: what is being shown, how much of it, how much is
    being withheld by the scope, and the key that widens it. All four are needed together --
    "1 cycle" alone would look like a truncated log rather than a deliberate day filter."""
    shown = len(feed.cycles)
    parts = [
        f"scope: {scope_label(feed.scope, feed.scope_start_ts)}",
        f"{shown} cycle" if shown == 1 else f"{shown} cycles",
    ]
    if feed.cycles_out_of_scope:
        parts.append(f"{feed.cycles_out_of_scope} older hidden")
    parts.append("press t to widen")
    return "  ·  ".join(parts)


def _elapsed_phrase(seconds: float) -> str:
    """A duration at the precision an operator actually reads: `47m`, `2h 14m`, `3d 4h`. Never
    negative (a clock that stepped backwards reads "less than a minute", not "-3h")."""
    minutes = int(max(0.0, seconds) // 60)
    if minutes < 1:
        return "less than a minute"
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours}h {minutes:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


def _day_phrase(then_ts: float, now_ts: float) -> str:
    """`yesterday` / `3 days ago` / `earlier today`, by LOCAL CALENDAR DAY rather than by elapsed
    hours -- 23 hours ago can be yesterday or the day before, and the calendar answer is the one
    that matches the date printed beside it."""
    try:
        delta = (
            datetime.datetime.fromtimestamp(now_ts).date()
            - datetime.datetime.fromtimestamp(then_ts).date()
        ).days
    except (OSError, OverflowError, ValueError):
        return "at an unreadable time"
    if delta <= 0:
        return "earlier today"
    if delta == 1:
        return "yesterday"
    return f"{delta} days ago"


def _next_due_lines(last_ts: float, now_ts: float) -> list[str]:
    """When the next cycle is expected, inferred from the TIME OF DAY the last one started.

    This is the line that turns "nothing here" into "nothing here YET". The deployment runs once
    a day on a fixed schedule, so the previous cycle's wall-clock time is a good estimate of the
    next one's, and it is drawn from the log itself rather than from a schedule setting this
    module would otherwise have to be taught about (and could then disagree with).

    The two cases are genuinely different news, so they read differently: still to come is
    reassurance, already overdue is a prompt to go and look."""
    try:
        last = datetime.datetime.fromtimestamp(last_ts)
        today = datetime.datetime.fromtimestamp(now_ts).date()
        due = datetime.datetime.combine(
            today, datetime.time(last.hour, last.minute)
        ).timestamp()
    except (OSError, OverflowError, ValueError):
        return []
    clock = f"{last.hour:02d}:{last.minute:02d}"
    if due > now_ts:
        return [f"Next cycle due today around {clock} local -- in {_elapsed_phrase(due - now_ts)}."]
    return [
        f"Its usual start time today ({clock} local) passed {_elapsed_phrase(now_ts - due)} ago.",
        "If no row appears here shortly, check that the agent's schedule is still running.",
    ]


def _coverage_caveat(scope: str) -> list[str]:
    """Said whenever the bounded window could not prove it reached back to the scope boundary.
    Without it, "no cycles today" would be claiming the day was quiet when the truth is only that
    the read did not go back far enough -- the one way this panel could actively mislead."""
    noun = _scope_noun(scope)
    return [
        f"CAVEAT: the bounded read (newest {_MAX_BYTES // 1024} KiB / {_MAX_LINES} lines) begins",
        f"inside {noun}, so cycles from {noun} may exist in the log but sit outside what was",
        "read. This panel cannot show that nothing happened -- only that it saw nothing.",
    ]


def describe_empty_scope(feed: ActivityFeed) -> list[str]:
    """The lines shown when the log read fine but the SCOPE holds no cycle -- the single most
    important thing in the day-scoping change, and the reason it is safe at all.

    On a deployment that runs one cycle a day at 09:00, a "today" view is empty every morning
    before 09:00. A blank panel there would be strictly worse than the state dashboard this whole
    feature exists to fix, because a blank panel and a dead agent look identical. So this always
    answers "is keel alive" first, and it answers it with a FACT -- the timestamp of the last
    cycle the window saw -- rather than with reassurance.

    Naming that one timestamp is not a breach of "today only": it is a status line, not a feed.
    No historical row is rendered, nothing scrolls, and nothing about what happened on that day
    is shown beyond when it began."""
    scope = feed.scope
    now_ts = feed.now_ts if feed.now_ts is not None else time.time()

    if scope not in _SCOPE_DAYS or feed.scope_start_ts is None:
        return [
            _NO_CYCLES_AT_ALL,
            "",
            "Every record in the window was either unusable or carried no event that could be",
            "grouped into a cycle. The file was read -- there is simply nothing in it to show.",
        ]

    last = feed.last_cycle_before_scope
    lines: list[str] = []

    if last is None:
        lines.append(f"keel has not run {_scope_noun(scope)}, and this window holds no earlier")
        lines.append("cycle either.")
        lines.append("")
        lines.append("The engine log was read successfully -- it just contains no cycle at all.")
        lines.append("That is what a brand-new deployment looks like, and also what one looks")
        lines.append("like when `logging.verbose: false` (the default) keeps everything except")
        lines.append("errors out of the log.")
    else:
        lines.append(
            "keel has not run yet today."
            if scope == "today"
            else "keel has not run in the last 7 days."
        )
        lines.append(
            f"Last cycle: {_stamp(last.started_ts)} -- {_day_phrase(last.started_ts, now_ts)}, "
            f"{_elapsed_phrase(now_ts - last.started_ts)} ago."
        )
        if scope == "today":
            lines.extend(_next_due_lines(last.started_ts, now_ts))
        lines.append("")
        lines.append(
            f"That one line is all the history this panel shows: it is scoped to "
            f"{_scope_noun(scope)}."
        )

    lines.append("Press t to widen the scope: today -> 7 days -> all history in the window.")

    if not feed.scope_fully_covered:
        lines.append("")
        lines.extend(_coverage_caveat(scope))
    return lines


def footer_notes(feed: ActivityFeed) -> list[str]:
    """The honest small print under a rendered feed: what the bounded read did NOT show, and what
    it could not parse. Reported rather than swallowed -- a feed that quietly under-reports while
    looking complete is worse than one that admits its window."""
    notes: list[str] = []
    notes.append(f"source: {feed.source}  ({feed.lines_read} records in window)")
    if feed.cycles_out_of_scope:
        notes.append(
            f"scope {scope_label(feed.scope, feed.scope_start_ts)}: "
            f"{feed.cycles_out_of_scope} older cycle(s) in the window are hidden -- "
            "press t to widen"
        )
    if feed.scope in _SCOPE_DAYS and not feed.scope_fully_covered:
        noun = _scope_noun(feed.scope)
        notes.append(
            f"COVERAGE UNPROVEN: the bounded read begins inside {noun}, so earlier cycles from "
            f"{noun} may exist in the log but outside what was read -- this is NOT evidence that "
            f"{noun} was quiet"
        )
    if feed.window_truncated:
        notes.append(
            f"window BOUNDED: newest {_MAX_BYTES // 1024} KiB / {_MAX_LINES} lines / "
            f"{_MAX_CYCLES} cycles at most -- older history is in the log, not here"
            + (f" ({feed.cycles_dropped} older cycles dropped)" if feed.cycles_dropped else "")
        )
    if feed.lines_skipped:
        notes.append(
            f"{feed.lines_skipped} line(s) skipped as unparseable (a partial record from a crash "
            "mid-write, or a rotation boundary) -- everything else is shown"
        )
    return notes


__all__ = [
    "ACTIVITY_HEADER",
    "ACTIVITY_SCOPES",
    "DEFAULT_ACTIVITY_SCOPE",
    "ActivityCycle",
    "ActivityEvent",
    "ActivityFeed",
    "LogWindow",
    "apply_scope",
    "build_activity_feed",
    "cycle_style",
    "describe_empty_scope",
    "describe_status",
    "event_style",
    "feed_from_lines",
    "footer_notes",
    "group_cycles",
    "next_activity_scope",
    "normalise_scope",
    "parse_events",
    "read_log_window",
    "render_cycle_row",
    "render_event_detail",
    "render_event_row",
    "resolve_log_path",
    "scope_headline",
    "scope_label",
    "scope_start_ts",
    "summarise_cycle",
]
