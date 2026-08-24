"""The live-update stream (#537) -- what it carries, what it refuses, and when it lets go.

Two halves, and the split matters because only one of them can be proved here:

  * **The generator**, `events.stream`, tested with an injected clock and an injected sleep. That
    is what makes a ten-minute connection's whole lifetime a sub-millisecond test rather than a
    ten-minute one, and it is why `stream` takes those two parameters at all -- nothing but a test
    passes them.
  * **The socket**, driven against a real `keel serve` through `tests/web/conftest.py`'s `running`
    fixture, so the stream is admitted, refused and labelled by the same code path as every other
    response on this server rather than by a second one stood up for its convenience.

**What is NOT proved here, stated once so a green run is not read as more than it is:** that a
browser's `EventSource` parses these frames, that it reconnects after `MAX_STREAM_SEC`, that
`live.js`'s watchdog fires, or that a dropped connection reaches the banner. None of those exists
without a DOM, and this repository ships no browser to run one in -- the same line
`tests/web/test_client_assets.py` draws for the rest of the client. They are checked by hand
against a running `keel serve`; the procedure is in the PR body.
"""

from __future__ import annotations

import json
import socket
from typing import Any

import pytest

from keel.web import events
from keel.web import server as web_server
from keel.web.security import SESSION_COOKIE
from tests.web.test_server import _session

# -- the generator, on an injected clock ----------------------------------------------------------


class _Clock:
    """A clock that only moves when `sleep` is called, so a stream's lifetime costs no real time."""

    def __init__(self) -> None:
        self.now = 1_000_000.0
        self.slept: list[int] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: int) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _cfg(tmp_path: Any) -> web_server.ServeConfig:
    return web_server.ServeConfig(
        host="127.0.0.1",
        port=0,
        token="t",
        db_path=str(tmp_path / "keel.db"),
        config_path=str(tmp_path / "config.yaml"),
    )


def test_the_stream_opens_with_a_retry_directive_and_a_tick(tmp_path: Any) -> None:
    """The first tick is emitted BEFORE any wait.

    A stream that opened and then said nothing for five seconds would leave the page showing
    whatever it already had, with no way to tell a slow connection from a dead one -- and the
    first thing a RECONNECTING client needs is the current revision, so it can find out whether
    anything moved while it was away.
    """
    clock = _Clock()
    frames = list(
        events.stream(_cfg(tmp_path), now=clock.time, sleep=clock.sleep, max_sec=0)
    )

    assert frames[0] == f"retry: {events.RETRY_MS}\n\n"
    assert frames[1].startswith(f"event: {events.TICK_EVENT}\ndata: ")
    assert frames[1].endswith("\n\n")
    assert clock.slept == [], "a stream that ends immediately must not have waited first"


def test_a_tick_is_the_same_envelope_every_endpoint_answers_with(tmp_path: Any) -> None:
    """One document shape, so `live.js` can hand a tick to the same reader a `fetch` goes through.

    A stream-shaped message would have needed a second normaliser in the client, and the two would
    have drifted the first time a key was added.
    """
    clock = _Clock()
    frames = list(events.stream(_cfg(tmp_path), now=clock.time, sleep=clock.sleep, max_sec=0))
    document = json.loads(frames[1].split("data: ", 1)[1])

    assert set(document) == {"as_of", "engine", "data", "sort"}
    assert document["as_of"].endswith("Z")
    assert document["engine"]["value"] in {"running", "stopped"}


def test_a_tick_carries_a_revision_marker_and_nothing_else(tmp_path: Any) -> None:
    """**The stream carries no figures.**

    `api.js`'s docstring sells one property -- that a reader can audit where this interface sends
    data by opening one file. A second transport carrying equity, positions or counts would make
    that two files and turn the audit into a search. So `data` holds exactly one key, and this is
    what stops a report being added to it later "just for the dashboard".
    """
    clock = _Clock()
    frames = list(events.stream(_cfg(tmp_path), now=clock.time, sleep=clock.sleep, max_sec=0))
    document = json.loads(frames[1].split("data: ", 1)[1])

    assert list(document["data"]) == ["revision"]
    assert isinstance(document["data"]["revision"], str)


def test_the_stream_heartbeats_and_then_ends_so_a_thread_is_never_held_forever(
    tmp_path: Any,
) -> None:
    """`MAX_STREAM_SEC` is a ceiling on held threads, not a timeout on the feature.

    `ThreadingHTTPServer` gives every open connection a thread and a streamed response holds one
    for as long as it runs, so an unbounded stream turns "how many tabs are open" into "how many
    threads is this process holding". `EventSource` reconnects on its own when a stream ends, so
    the cost is one reconnection per tab per ten minutes.
    """
    clock = _Clock()
    frames = list(
        events.stream(
            _cfg(tmp_path), now=clock.time, sleep=clock.sleep, max_sec=20, heartbeat_sec=5
        )
    )

    ticks = [frame for frame in frames if frame.startswith("event: ")]
    # t=0, 5, 10, 15, 20 -- and then it stops, rather than running for the rest of the day.
    assert len(ticks) == 5
    assert clock.slept == [5, 5, 5, 5]


def test_a_frame_is_one_data_line_so_it_cannot_parse_as_silence(tmp_path: Any) -> None:
    """SSE prefixes EVERY line of a multi-line body with `data:`, and a body with a raw newline in
    it therefore parses as a truncated message rather than as an error.

    That is the worst failure mode available here -- a stream that looks alive and says nothing --
    so the property is asserted on the frame itself rather than trusted to `json.dumps` never
    growing an `indent=`.
    """
    frame = events.frame("tick", {"a": "1", "b": {"c": "2"}})

    body = frame.split("data: ", 1)[1]
    assert body.count("\n") == 2, frame
    assert body.endswith("\n\n")


def test_the_revision_marker_moves_when_the_database_is_written(tmp_path: Any) -> None:
    """The marker's ONE contract: it changes when something has been written.

    Asserted with a real write and a real `stat`, because the failure mode of getting this wrong
    is a stream that ticks forever and never says anything has changed -- which looks exactly like
    a deployment where nothing is happening, and a daily agent's deployment usually is one.
    """
    cfg = _cfg(tmp_path)
    before = events.revision(cfg)

    db = tmp_path / "keel.db"
    db.write_bytes(b"x" * 64)
    after = events.revision(cfg)

    assert before != after
    # And a marker for a machine with nothing on it is a value, not an exception: the first-run
    # path reaches this function too.
    assert before


def test_the_revision_marker_notices_a_background_job(tmp_path: Any) -> None:
    """A setup job starting and finishing must move the marker, or `/setup` would sit on a
    finished fetch showing "running" until the next fifteen-second poll caught it.

    This is the one input to the marker that is not a file, which is why it is asserted
    separately: it is the input a future edit is most likely to drop.
    """
    cfg = _cfg(tmp_path)
    assert events.revision(cfg, job_state="fetch:running") != events.revision(
        cfg, job_state="fetch:done"
    )


# -- over the wire --------------------------------------------------------------------------------


def _stream_head(cfg: web_server.ServeConfig, *, cookie: str | None, read_bytes: int = 700) -> Any:
    """Open `/api/events` and read the beginning of the response, then hang up.

    A raw socket rather than `_request`, because that helper reads to end-of-body and this
    response deliberately has no end for ten minutes. Hanging up mid-stream is also the case worth
    exercising: it is what a closed browser tab does, and the server must treat it as the normal
    end of a subscription rather than as a failure.
    """
    conn = socket.create_connection((cfg.host, cfg.port), timeout=5)
    try:
        # `Connection: close` so a REFUSAL is a complete, finished response rather than a short
        # one on a socket the server then waits on: hanging up on a keep-alive connection with
        # bytes still unread sends an RST, which `socketserver` reports as an unhandled
        # `ConnectionResetError` on stderr. That is noise from this test client, not from the
        # endpoint -- a browser abandoning a live stream lands in `_serve_events`'s own
        # `BrokenPipeError` guard instead -- but noise in a test log is how a real error later
        # gets scrolled past.
        request = [
            "GET /api/events HTTP/1.1",
            f"Host: {cfg.host}:{cfg.port}",
            "Connection: close",
        ]
        if cookie is not None:
            request.append(f"Cookie: {cookie}")
        conn.sendall(("\r\n".join([*request, "", ""])).encode("ascii"))
        # Read until there is something to assert on and then stop, rather than to end-of-body.
        # There are two "enough" conditions and both are needed: a REFUSAL is a short response on
        # a connection the server keeps alive, so waiting for a tick that will never come times
        # out; a stream has no end at all, so waiting for the socket to close times out too.
        raw = b""
        while len(raw) < read_bytes:
            piece = conn.recv(read_bytes)
            if not piece:
                break
            raw += piece
            if b"\r\n\r\n" not in raw:
                continue
            if not raw.startswith(b"HTTP/1.1 200"):
                break
            if b"event: tick" in raw:
                break
        return raw.decode("utf-8", "replace")
    finally:
        conn.close()


def test_the_event_stream_answers_with_the_right_media_type(running) -> None:  # type: ignore[no-untyped-def]
    """`text/event-stream` is the only type `EventSource` accepts, and `Connection: close` is what
    delimits a body with no `Content-Length` under HTTP/1.1 -- the alternative being chunked
    framing hand-written on top of `wfile`, a second framing layer to get wrong."""
    raw = _stream_head(running, cookie=_session(running))

    assert raw.startswith("HTTP/1.1 200"), raw[:120]
    assert "Content-Type: text/event-stream; charset=utf-8" in raw
    assert "Connection: close" in raw
    assert "Content-Length" not in raw


def test_the_event_stream_sends_its_first_tick_without_being_asked_twice(running) -> None:  # type: ignore[no-untyped-def]
    """End to end: a real socket, a real handler, a real envelope."""
    raw = _stream_head(running, cookie=_session(running))

    body = raw.split("\r\n\r\n", 1)[1]
    assert body.startswith(f"retry: {events.RETRY_MS}")
    assert "event: tick" in body
    document = json.loads(body.split("data: ", 1)[1].split("\n\n", 1)[0])
    assert document["engine"]["value"] == "running", document
    assert list(document["data"]) == ["revision"]


def test_the_event_stream_is_behind_the_same_admission(running) -> None:  # type: ignore[no-untyped-def]
    """Never weakened. A stream is not exempt from the loopback-plus-session model for being a
    stream: `do_GET` runs the `Host` check and the cookie check before it ever looks at a path,
    and this asserts it over a socket rather than by reading the source."""
    raw = _stream_head(running, cookie=None)

    assert raw.startswith("HTTP/1.1 403"), raw[:120]
    assert "event: tick" not in raw


def test_the_event_stream_carries_the_api_header_set(running) -> None:  # type: ignore[no-untyped-def]
    """`no-store` matters more here than anywhere: a cached event stream would replay yesterday's
    liveness. No CSP, for the reason `_API_HEADERS` records -- it has no defined meaning outside a
    browsing context, and `nosniff` is what carries the weight for a non-HTML type."""
    raw = _stream_head(running, cookie=_session(running))

    assert "X-Content-Type-Options: nosniff" in raw
    assert "Cache-Control: no-store, max-age=0" in raw
    assert "Content-Security-Policy" not in raw
    assert "Strict-Transport-Security" not in raw


def test_the_event_stream_answers_no_post(running) -> None:  # type: ignore[no-untyped-def]
    """The read surface did not widen. `do_POST` refuses everything under `API_PREFIX` before it
    looks at a path, so a streaming endpoint added there inherits that rather than escaping it --
    asserted for this path specifically because it is the one route under the prefix that is not
    in `api.API_ROUTES`, and so is not covered by `test_no_api_route_answers_a_post`."""
    conn = socket.create_connection((running.host, running.port), timeout=5)
    try:
        conn.sendall(
            (
                "POST /api/events HTTP/1.1\r\n"
                f"Host: {running.host}:{running.port}\r\n"
                f"Cookie: {SESSION_COOKIE}={running.token}\r\n"
                "X-Keel-Client: 1\r\n"
                "Connection: close\r\n"
                "Content-Length: 0\r\n\r\n"
            ).encode("ascii")
        )
        raw = conn.recv(400).decode("utf-8", "replace")
    finally:
        conn.close()

    assert raw.startswith("HTTP/1.1 404"), raw[:120]


@pytest.mark.parametrize("path", ["/api/events"])
def test_the_stream_path_is_spelled_the_same_in_python_and_javascript(path: str) -> None:
    """Two files name this endpoint and neither can import the other.

    The consequence of drift is silent: `live.js` would open a stream against a path that 404s,
    `EventSource` would retry it forever, and the page would fall back to its fifteen-second poll
    while looking exactly like a page with live updates working."""
    assert web_server.EVENTS_PATH == path
    source = (web_server.staticfiles.STATIC_ROOT / "js" / "live.js").read_text(encoding="utf-8")
    assert f'const EVENTS_PATH = "{path}";' in source
    assert f'const TICK_EVENT = "{events.TICK_EVENT}";' in source
