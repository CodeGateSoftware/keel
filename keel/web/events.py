"""Server-sent events for the browser client (#537) -- liveness and freshness, and no figures.

`keel serve` polls itself today: `main.js` re-reads its endpoint every fifteen seconds. That is
enough to keep a page current and it is not enough to make a DEAD server visible -- a browser
whose `fetch` is failing looks exactly like one whose report has not changed, and a dashboard
that silently goes stale in front of an operator is the one failure this whole interface exists
to prevent. `EventSource` closes that gap because it is a connection rather than a request: when
the process it is attached to goes away, the browser knows within a heartbeat instead of within
however long nobody happened to look.

── WHAT THIS STREAM CARRIES, AND THE THING IT DELIBERATELY DOES NOT ─────────────────────────────

A tick is an `envelope` -- the same four keys `keel/web/payload.py` puts on every `GET /api/*`
answer -- whose `data` holds exactly one string: a **revision marker**. No equity, no positions,
no counts, no report of any kind.

Two reasons, and the second is the one that decided it:

  * **The agent runs daily.** There is no ticking price feed here to stream; what actually
    changes between one second and the next is whether keel is alive and whether anything has
    been written since the page last looked. A stream carrying a full status report every five
    seconds would be building a report four hundred times an hour so that a number could stay the
    same.

  * **`api.js` stays the only place data enters this client.** Its module docstring sells one
    property -- "the interface is provably incapable of sending positions, equity or trade
    history anywhere but the local process", audited by a reader who opens one file. A second
    transport that also carried figures would make that two files, and the audit would become a
    search. `live.js` opens the `EventSource`; when a tick says something changed, the client
    re-reads through `api.js`'s single `fetch` like it does for everything else.

`engine` rides on the tick anyway, because it is not a figure: it is the answer to "is keel
running", it is one short sentence, and it is the thing the page's one `aria-live` region holds.
A stream that could tell you the server is up but not tell you the deployment is gone would be
answering the easier half of the question.

── THE REVISION MARKER: WHAT IT WATCHES, AND WHAT IT CANNOT SEE ─────────────────────────────────

`revision()` is `os.stat` on the deployment's two files plus the background job's state. It is
CHEAP by construction -- two stats and a dict read, microseconds -- which is what lets it run on
every heartbeat where `deployment_state`'s 3.6 ms probe would not.

It is deliberately a marker and not a timestamp: the client compares it to the last one it saw
and re-reads on inequality, so its only contract is that it CHANGES when something has been
written. A monotonic clock reading would invite arithmetic at the other end.

**It watches the `-wal` file too, and the first draft did not.** `keel serve` runs SQLite in WAL
mode (#470), so a committed write lands in `keel.db-wal` and leaves `keel.db`'s size and mtime
exactly where they were until a checkpoint. Watching only the database file was therefore a marker
that never moved: driven in a real browser, a trade written while the page was open produced ticks
for twelve seconds and no refresh at all. The earlier revision of this note called that a
documented blind spot covered by the fifteen-second poll -- which was wrong twice over, because
the poll deliberately does not rebuild the view while a subscription is running. The feature was
inert and the note said so approvingly.

`-shm` is deliberately NOT watched: it is the shared-memory index, and it is touched by READERS.
Including it would move the marker every time this endpoint itself opened the database, which is a
refresh loop rather than a change notification.

What remains missed is narrow and worth stating: a write that is checkpointed and truncates the
`-wal` back to a size it has held before, within the same `st_mtime_ns` tick, would look
unchanged. Nanosecond timestamps make that essentially unreachable, and the fifteen-second poll's
banner refresh is what would surface a page that had somehow gone quiet.

── WHY THIS IS STILL A READ, AND STILL BEHIND THE SAME DOOR ─────────────────────────────────────

`/api/events` is a GET, handled in `server.do_GET` AFTER the same `Host` check and the same
session cookie every other path goes through -- `keel/web/__init__.py`'s guarantee ("the JSON API
is reads only") is unchanged, and `test_the_event_stream_is_behind_the_same_admission` asserts it
over a socket rather than by inspection. It answers no POST, for the same reason nothing else
under `API_PREFIX` does.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from keel.web import api, payload

if TYPE_CHECKING:  # pragma: no cover - typing only
    from keel.web.server import ServeConfig

#: The stream's content type, and the only one `EventSource` accepts.
CONTENT_TYPE = "text/event-stream; charset=utf-8"

#: Seconds between ticks.
#:
#: Five, against `server._REFRESH_SEC`'s fifteen, and the two are answering different questions.
#: Fifteen is how stale a FIGURE may get; five is how long a dead server may look alive, and that
#: is the number an operator is standing in front of. It is also the heartbeat that keeps the
#: connection from being reaped by anything in between -- there is nothing in between on loopback
#: today, but a stream whose liveness depends on there being no proxy is a stream that breaks the
#: first time someone forwards the port over SSH, which the design spec explicitly endorses.
HEARTBEAT_SEC = 5

#: How long one connection is allowed to live before the server closes it and lets the browser
#: reconnect.
#:
#: Ten minutes. `ThreadingHTTPServer` gives every open connection a thread, and a streamed
#: response holds one for as long as it runs -- so an unbounded stream turns "how many tabs has
#: this operator left open since Tuesday" into "how many threads is this process holding". A
#: bounded lifetime makes that a ceiling instead of a trend. `EventSource` reconnects on its own
#: when a stream ends, so the cost of the ceiling is one reconnection per tab per ten minutes and
#: the client cannot tell the difference -- which is exactly why `RETRY_MS` below is short.
MAX_STREAM_SEC = 600

#: What the browser is told to wait before reconnecting, in milliseconds (the `retry:` field).
#:
#: Two seconds. The default is browser-defined and around three, which is fine for a dropped
#: connection and wrong for the ordinary case here: every stream ends on purpose after
#: `MAX_STREAM_SEC`, so this delay is paid on a healthy connection too. Short enough that the
#: banner's "reconnecting" state is a blink rather than a fault, long enough that a server which
#: is genuinely down is not reconnected against in a tight loop.
RETRY_MS = 2000

#: The tick event's name. Named rather than left as the default `message` so that a later event
#: type -- a job finishing, say -- is an addition rather than a change of meaning for listeners
#: that already exist.
TICK_EVENT = "tick"


def revision(cfg: ServeConfig, *, job_state: str = "") -> str:
    """A marker that changes when something the client is showing may have changed.

    Built from `os.stat` rather than from a database read on purpose: the client polls this at
    `HEARTBEAT_SEC`, and a read that opened SQLite would be a read that can block behind the
    agent's own write. A `stat` of a file that is not there is not an error either -- a machine
    with no deployment is the first-run case, and it gets the stable marker `"-"` for that file
    rather than an exception.
    """
    # `-wal` alongside the database, because in WAL mode that is where a commit actually lands --
    # see the module note. `-shm` is left out on purpose: readers touch it, so watching it would
    # make this endpoint's own reads look like writes.
    parts = [job_state]
    for path in (cfg.db_path, f"{cfg.db_path}-wal", cfg.config_path):
        try:
            info = os.stat(path)
        except OSError:
            parts.append("-")
            continue
        parts.append(f"{info.st_mtime_ns}.{info.st_size}")
    return "|".join(parts)


def tick_document(cfg: ServeConfig, now_ts: int) -> dict[str, Any]:
    """One tick, as the same envelope every `GET /api/*` answers with.

    Reusing `payload.envelope` rather than inventing a stream-shaped message is what lets
    `live.js` hand a tick to the very same banner code a `fetch` reading goes through. The client
    already has one function for "what does this answer say about the engine"; a second message
    shape would have needed a second one, and the two would have drifted the first time a state
    word was added.
    """
    try:
        from keel.commands import jobs

        state = api.deployment_state(cfg)
        running = bool(state.has_usable_database)
        job = jobs.status()
        job_state = f"{job.key}:{job.state}" if job is not None else ""
    except Exception:  # pragma: no cover - `inspect` is total; this is the belt to its braces
        # The same reading `api.respond` takes when the probe itself fails: we could not tell
        # whether keel is set up, so do not claim it is.
        running, job_state = False, ""
    return payload.envelope(
        now_ts,
        running=running,
        data={"revision": revision(cfg, job_state=job_state)},
        sort=None,
    )


def frame(event: str, document: dict[str, Any]) -> str:
    """One SSE frame: an event name, a single `data:` line, and the blank line that ends it.

    The JSON is written with no newline in it -- `json.dumps` produces none by default and the
    payload's leaves are all strings -- so one `data:` line is always enough. A multi-line body
    would need every line prefixed, and getting that wrong produces a stream that parses as
    silence rather than as an error, which is the worst failure mode available here.
    """
    # A plain `json.dumps`, with no `default=` and no `cls=`, for the reason `server._send_json`
    # gives at its own call: `payload.py` normalises every leaf to a string before it gets here,
    # so there is nothing for an encoder to convert -- and the encoder a hurried author reaches
    # for is `default=float`, which is the whole money contract dying in one keyword. Nothing
    # monetary rides this stream today, and the habit is worth keeping anyway.
    body = json.dumps(document, ensure_ascii=False)
    return f"event: {event}\ndata: {body}\n\n"


def stream(
    cfg: ServeConfig,
    *,
    now: Any = time.time,
    sleep: Any = time.sleep,
    max_sec: int = MAX_STREAM_SEC,
    heartbeat_sec: int = HEARTBEAT_SEC,
) -> Iterator[str]:
    """The frames of one connection, ending when `max_sec` is up.

    The FIRST tick is emitted before any wait. A stream that opened and then said nothing for five
    seconds would leave the page showing whatever it had, with no way to tell a slow connection
    from a dead one -- and the first thing a reconnecting client needs is the current revision, so
    it can find out whether anything moved while it was away.

    `now` and `sleep` are injected so a test can run the whole lifetime of a connection without
    spending it. They are not a configuration surface: nothing but a test passes them.
    """
    yield f"retry: {RETRY_MS}\n\n"
    started = now()
    while True:
        yield frame(TICK_EVENT, tick_document(cfg, int(now())))
        if now() - started >= max_sec:
            return
        sleep(heartbeat_sec)
