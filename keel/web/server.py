"""The loopback HTTP server behind `keel serve` -- routing, one bounded read per response.

Four surfaces, and the split is the whole of this module's job: the rendered HTML pages in
`ROUTES`, the static assets under `staticfiles.STATIC_PREFIX` (#535), the JSON read API under
`API_PREFIX` (#534, routed by `keel/web/api.py`), and the closed write surface -- `API_SETUP_PREFIX`
(#437) and `API_GATES_PREFIX` (#781). Each gets its own header set, because they need different
values for the SAME header rather than merely different extra ones.

**Why the standard library and not a web framework.** This surface is a handful of pages
and endpoints served to one person on one machine. FastAPI/uvicorn would bring a dependency
subtree (pydantic, starlette, anyio, h11, ...) into a wheel that today depends on `click` and its
own workspace siblings -- and D5 has to freeze that tree into a signed, notarised app bundle, where
every dynamic import is a hook to write and every megabyte is download the user waits through. It
would also enlarge the supply-chain surface of a project whose proposition is auditability, to buy
routing for a couple of dozen paths and a templating engine used zero times. `http.server` is the
smaller, more honest answer here, and it is genuinely the wrong answer the moment this serves more
than one local user -- at which point the framework, not this module, is the thing to reach for.

**The write surface is a closed set, and that is a better guarantee than the one it replaced.**
This handler used to implement `do_GET`/`do_HEAD` and nothing else, so a POST died in the stdlib.
That was a clean property and it was also satisfied by a server that could not set anything up --
which is the whole problem #437 exists to solve, because a first-run user on a machine with no
terminal has to be able to create a deployment somehow.

So `do_POST` exists, and it routes through exactly two tables. `/api/setup/<key>` reaches only
`keel.commands.setup.ACTIONS`: idempotent, non-destructive steps, every one of them declared
`MECHANICAL` in the same module's step list. `/api/gates/<key>` reaches `halt` (engage the kill
switch, ungated on purpose -- refusing a stop is the one failure it must not have) and the gated
halt releases in `gates.TIER1_ACTIONS` (`resume`, `resume-entries`), which demand a loopback
session on a loopback bind, the gate token (which no endpoint serves, #968) and a typed phrase;
the halt takes a halt token of its own. The guarantee is that **eight of the
ten capability-increasing actions in `keel/capabilities.py` are unreachable from this package and
the other two only as that gate table declares them**, asserted by a test that scans this source
rather than by inspection. "No POST" said the server could not write; this says it cannot arm,
attest or spend -- which is the property anyone actually cares about.

Attesting, promoting, arming autonomy and the other gated actions remain CLI-only, behind the TTY
gate. The console client draws no release control; only the halt has a button.

**Each request opens its own SQLite connection.** A `ThreadingHTTPServer` hands requests to
different threads, and a `sqlite3` connection belongs to the thread that made it. Per-request is
also what the CLI does per invocation, which keeps the page a snapshot of committed state rather
than a long-lived reader that could sit inside someone else's transaction.
"""

from __future__ import annotations

import json
import re
import signal
import socket
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from keel.web import api, events, runtime, staticfiles
from keel.web.security import (
    CSRF_HEADER,
    GATES_HEADER,
    HALT_HEADER,
    REMOTE_SESSION_MAX_AGE_SECONDS,
    SESSION_COOKIE,
    HostPolicy,
    csrf_token,
    gated_action_permitted,
    gates_token,
    halt_token,
    parse_cookie_header,
    session_cookie,
    tokens_match,
)

#: Cap on a form body. `rfile.read(n)` with an attacker-supplied `n` is a memory-exhaustion
#: primitive and there is no proxy in front of this server to impose a limit. A setup form carries
#: an action key and a token.
_MAX_BODY_BYTES = 8 * 1024
# More digits than any honest `Content-Length` has; a longer one is closed on, never `int()`-ed
# (CPython raises ValueError past 4300 digits).
_MAX_CONTENT_LENGTH_DIGITS = 18

#: Seconds any ONE socket read or write may block before the connection is dropped (#981).
#:
#: Without it, a client that declared `Content-Length: 100` and sent nothing held its thread
#: forever: `_refuse` drains before it answers, so an unauthenticated request never got its
#: refusal and never let go. An idle kept-alive connection did the same in the stdlib's
#: `readline`. Enough of either starves the server of threads, and the halt with it.
#:
#: **What it cannot cost the halt.** The timeout is per operation, and an honest client's request
#: line, headers and body arrive at once; the halt reads no body, answers before `do_POST`'s
#: drain, and writes a response of a few hundred bytes. The one new failure is the universal
#: keep-alive race -- this server closing an idle connection as the browser reuses it -- and
#: browsers resend a request whose reused connection closed before any response byte, which is
#: what makes every server's idle close (nginx's is 75 s, Apache's 5 s) safe to have.
#:
#: **Why a minute.** It bounds a `sendall` too, as a total since Python 3.5, and the largest
#: response here -- the whole audit trail as CSV -- has to reach a phone over a tunnel inside it.
#: A shorter bound buys little: it limits how long a connection may sit SILENT, not how long
#: it can be held. The timeout is per socket operation, so a client that trickles one byte inside
#: every window never trips it (slowloris). That is accepted: threads are not pooled and an
#: attacker can open more connections anyway, so no per-connection bound would cap the cost. Read
#: by `build_server`, so a test can shorten it.
REQUEST_TIMEOUT_SECONDS = 60.0


@dataclass(frozen=True)
class ServeConfig:
    """Everything the server needs, resolved once by the command before anything binds."""

    host: str
    port: int
    token: str
    db_path: str
    config_path: str
    #: The build identity as one human line, for the page footer: `keel 0.1.0+9f2c1a [checkout]`.
    build: str = ""
    #: The same build as STRUCTURE -- a `keel.version.BuildInfo`, or `None` where one could not be
    #: resolved. `/api/config` needs the version and the commit as separate fields (#538 keys a
    #: service-worker cache to one, #539 puts the other in a `?v=`), and parsing them back out of
    #: `build` would be a display string being read as data.
    #:
    #: Resolved ONCE by `serve_cmd`, never per request: `keel.version.build_info()` shells out to
    #: git twice, and an endpoint a service worker polls must not fork a subprocess to answer.
    #: `Any` rather than the real type for the same reason `api.load_config` returns `Any` -- this
    #: module names service objects loosely so that importing `keel/web/` stays cheap.
    build_info: Any = None
    #: Hostnames a reverse proxy may present that this server never bound (#648). Empty by
    #: default -- loopback-only is the posture, and remaining the posture is the point.
    external_hosts: frozenset[str] = frozenset()
    #: When this run began, for the remote session lifetime (#648). Defaults to the moment the
    #: config is built, which is the moment `keel serve` mints the token it bounds.
    started_at: float = field(default_factory=time.time)
    #: #856: `/api/dca-plan`'s cached admission screen, scoped to THIS deployment. `ServeConfig`
    #: is frozen and built once per `keel serve` process (`build_server`), then shared by every
    #: thread `ThreadingHTTPServer` hands a request to via `BoundKeelHandler.cfg` -- exactly the
    #: lifetime a 5-minute cache needs, and `default_factory` gives a fresh, empty `ScreenCache`
    #: to every `ServeConfig` built (so two servers, or two deployments in one test session,
    #: never share one). `ScreenCache` itself is a plain class in `keel/web/api.py`, not a
    #: dataclass field with a mutable default -- `field(default_factory=api.ScreenCache)` is what
    #: keeps a frozen `ServeConfig` correct here: the FIELD is never reassigned, only the cache
    #: object it names is ever mutated internally, through its own lock.
    screen_cache: api.ScreenCache = field(default_factory=api.ScreenCache)

    @property
    def session_expired_at(self) -> float | None:
        """When this session stops authenticating, or `None` if it does not expire.

        `None` on a loopback-only server, and that is a decision rather than an omission --
        `REMOTE_SESSION_MAX_AGE_SECONDS` carries the argument, and the one request it does not
        bind, a halt with its token (#983). The population who could use a
        leaked token on loopback is software already running as this operator, and no session
        lifetime helps against that; a remote origin changes the population to anyone who can
        reach the tunnel, and the 30-day cookie is a browser hint such an attacker ignores.
        """
        if not self.external_hosts:
            return None
        return self.started_at + REMOTE_SESSION_MAX_AGE_SECONDS

    @property
    def host_policy(self) -> HostPolicy:
        return HostPolicy(bound_host=self.host, port=self.port, external_hosts=self.external_hosts)

    def url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}/?token={self.token}"


# -- the reads ---------------------------------------------------------------------------------
#
# Each of these is a thin adapter over `keel/commands/*`. Nothing below computes anything: the
# service layer returns a frozen report and `keel/web/render.py` turns it into HTML. That is the
# seam `tests/commands/test_console_thinness.py` pins, now extended over this package.
#
# `open_repo`, `load_config`, `deployment_state` and `close_repo` moved to `keel/web/api.py` when
# the JSON endpoints arrived (#534), unchanged and with their reasoning intact. Both front-ends
# read keel through them, and a copy in each file would be two places deciding whether a view may
# migrate a live database. The dependency runs one way -- this module imports `api`, `api` imports
# nothing from this one at runtime -- so there is no cycle to reason about.


def ensure_schema(db_path: str) -> None:
    """Bring an EXISTING database up to date once, at startup. Called by `serve`, not by a page.

    A missing database is a first run, not an error, and this used to treat it as one. On a
    machine with no deployment the app-data directory does not exist either, so `sqlite3.connect`
    raised `unable to open database file` and `keel serve` refused to start -- before serving the
    setup page that exists to fix exactly that. Found by running a frozen bundle on a clean
    machine; it reproduces unfrozen too, with `KEEL_HOME` pointed anywhere that does not exist.

    So: migrate what is there, and leave what is not to the setup action, which creates the
    parent directory and the schema together. Creating the database HERE would be worse -- a
    read-only view would bring a deployment into existence merely by being started, and every
    page would then report a healthy empty install rather than offering to set one up.
    """
    from keel.data.db import connect, migrate

    if not Path(db_path).exists():
        return
    conn = connect(db_path)
    try:
        migrate(conn)
    finally:
        conn.close()


#: The JSON API's prefix (#534). Everything under it answers in `application/json`, admitted or
#: refused; nothing under it is a browsing context, so nothing under it carries a CSP.
API_PREFIX = "/api/"

#: The one path under `API_PREFIX` that is a STREAM rather than a document (#537).
#:
#: It is not in `api.API_ROUTES` because it does not have that table's shape: every entry there
#: maps to `(status, document)` through `api.respond`, and an `EventSource` connection is a
#: response that never finishes. Bolting a "this one streams" flag onto `ApiRoute` would have put
#: a branch into the one function whose uniformity is the reason the client's `fetch` wrapper
#: needs no per-endpoint branch of its own.
#:
#: It is a GET, behind `_admitted()`, and answers no POST: `do_POST` reaches only
#: `API_SETUP_PREFIX`.
EVENTS_PATH = "/api/events"

#: The profile switcher's one route (#814): `/switch/<port>` moves the browser to another console
#: serving from this deployment root. A GET that redirects and changes nothing -- navigation, the
#: one thing the session chip's docstring allows it -- and the reason the page never needs a
#: peer's token: see `_switch`. `sw.js` declines these navigations by the same prefix, or it
#: would answer them from its cached shell and the server would never see one.
SWITCH_PREFIX = "/switch/"

#: The write surface, in full, today. A path here maps to one `keel.commands.setup.Action`; there
#: is no other POST this server answers, and `keel/web/__init__.py` is the file to read before
#: adding one.
#:
#: **It moved under `/api/` at #540**, from `/setup/`, when the HTML form that used to submit to it
#: was deleted along with the rest of the rendered pages. The move is what let `X-Keel-Client` --
#: a header a plain form can never set -- finally cover the write path too: `_api_client_header_ok`
#: recorded that widening as "#536's call to make, once (and only once) the forms it replaces are
#: gone", and they are gone.
#:
#: The action SET is unchanged by all of this, and that is the invariant that matters more than the
#: path: `keel.commands.setup.ACTIONS` still contains only idempotent, non-destructive,
#: `MECHANICAL` steps, and a test still asserts it is disjoint from the ten capability-
#: increasing actions in `keel/capabilities.py`. A browser can set a deployment up. It still cannot
#: arm a rule, attest an asset or enable autonomy.
API_SETUP_PREFIX = "/api/setup/"

#: The gated write surface (#781). A prefix of its own rather than a key under `/api/setup/`,
#: because the two carry different tokens, answer to different checks and have different blast
#: radii -- and `keel.commands.setup.ACTIONS` is asserted disjoint from every gated action, which
#: a shared prefix would make a confusing thing to state.
API_GATES_PREFIX = "/api/gates/"

#: The one ungated key under that prefix. Not a `Tier1Action`: it takes no phrase and no locality
#: check, because refusing a STOP is the one failure this surface must not have.
HALT_KEY = "halt"

#: One sentence for the missing-gate-token refusal, in one place, because two spellings of one
#: refusal is how a reader comes to think they are two different failures. It no longer says
#: "reload the page": no page carries the gate token (#968), so the remedy is the terminal.
_NO_GATE_TOKEN = (
    "That request did not carry this session's gate token, which no page is given. Release it "
    "with `{command}` at a terminal."
)

#: The CLI command that releases each Tier 1 route, because the refusal above answers both and
#: `keel resume` is the wrong remedy for `resume-entries`. Keyed like `gates.TIER1_ACTIONS`.
_RELEASE_COMMAND = {"resume": "keel resume", "resume-entries": "keel resume-entries"}

#: The halt's refusal, for `_NO_GATE_TOKEN`'s reason: the halt checks a different token in a
#: different header (#968), and a refusal that named the wrong one would send the reader looking
#: for the wrong failure.
_NO_HALT_TOKEN = "That request did not carry this session's halt token. Reload the page."


def run_setup_action(cfg: ServeConfig, key: str, form: dict[str, str]) -> Any:
    """Perform one declared setup action. Returns its `ActionResult`, or `None` for a key that is
    not in the closed set -- never a lookup that falls through to something else.

    Only the fields the action DECLARES are passed through. A submitted field the action did not
    ask for is dropped rather than forwarded: the form is attacker-shaped input the moment anyone
    can craft a POST, and an action should never receive a key it has no name for."""
    from keel.commands.setup import action_for

    action = action_for(key)
    if action is None:
        return None
    values = {field.name: form.get(field.name, "") for field in action.inputs}
    return action.run(Path(cfg.config_path), Path(cfg.db_path), values)


# -- the handler -------------------------------------------------------------------------------

#: The header set for the static tree -- which, since #540, is the whole application.
#:
#: There used to be a second set beside this one (`_SECURITY_HEADERS`) for the server-rendered
#: pages, whose `default-src 'none'` was correct for markup that shipped no script, no style file
#: and no image. Those pages are deleted and it went with them. What is left is the client, which
#: is exactly what `'none'` forbids: its own JS, its own CSS, its own icons, all same-origin.
#: `'self'` is the tightest policy that still allows that, and `connect-src 'self'` on top of it
#: is the specific guarantee the design spec asks for: the interface is provably incapable of
#: sending positions, equity or trade history anywhere but this local process, checkable in the
#: response headers rather than merely promised.
#:
#: `X-Frame-Options`, `Referrer-Policy` and `X-Content-Type-Options` are unconditional -- all
#: three are meaningful (and harmless) on any content type, exactly as they are for the rendered
#: pages above. CSP is NOT: RFC-wise it is a response header with no defined meaning outside a
#: browsing context, so it is applied only where the content type IS one -- `text/html`, and
#: `image/svg+xml` (see `_CSP_CONTENT_TYPES` and `_static_headers` below) -- matching the design
#: spec's "CSP belongs on `text/html` responses only; it is invalid and discouraged on other
#: content types."
_STATIC_BASE_HEADERS: tuple[tuple[str, str], ...] = (
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    # `_serve_static` writes its own headers rather than going through `_send` (below), which is
    # what let this go missing initially: `_send` puts `Cache-Control: no-store` on every
    # rendered page, and that omission here meant a browser was free to heuristically cache a
    # static asset with NO explicit directive at all. Harmless for today's one placeholder file,
    # but #536 ships a real client next, and a stale shell surviving an engine upgrade in the
    # HTTP cache is exactly the failure the design spec's service-worker cache key (keyed to
    # `/api/config`'s build version) exists to prevent one layer up -- this is the layer below
    # it. `no-store` for now is the conservative match to the rendered pages; a `CacheFirst`
    # strategy with content-hashed filenames is #536's to add once there is a build version to
    # key it to.
    ("Cache-Control", "no-store, max-age=0"),
)

#: `default-src 'self'; connect-src 'self'` alone was the shape reviewed in and it was
#: incomplete: `form-action`, `base-uri` and `frame-ancestors` do NOT fall back to `default-src`
#: under CSP3 -- each is independently permissive (`form-action` defaults to "anywhere",
#: `base-uri` to "anywhere", `frame-ancestors` to "anywhere") unless named explicitly. Without
#: them, `connect-src 'self'` still stops `fetch`/`XHR`/`EventSource` leaving the origin, but a
#: `<form method=post action="https://evil.example">` is not a connection and would have sailed
#: through, an injected `<base href="https://evil.example/">` could retarget every relative URL
#: on the page, and the page could still be framed by a hostile origin -- the exact DNS-rebinding
#: half the unconditional headers above already close (`frame-ancestors 'none'` is the other half
#: of the DNS-rebinding defence, and this is the same half for the route that hosts the client).
_STATIC_CSP = (
    "default-src 'self'; connect-src 'self'; form-action 'self'; base-uri 'none'; "
    "frame-ancestors 'none'"
)

#: The content types CSP applies to. `text/html` for the obvious reason; `image/svg+xml` because
#: SVG is ACTIVE content -- a same-origin `.svg` opened directly (not `<img>`-embedded, which
#: does not execute it) runs any inline `<script>` it contains in keel's own origin, with no
#: policy at all unless CSP explicitly covers this content type too.
_CSP_CONTENT_TYPES: tuple[str, ...] = ("text/html", "image/svg+xml")

# `Strict-Transport-Security` is deliberately absent from both header sets above, on every
# response this server ever sends -- recorded here so nobody adds it back reading only the
# acceptance checklist. `keel serve` binds loopback HTTP by design
# (`docs/superpowers/specs/2026-08-23-web-ui-rewrite-design.md`'s secure-context argument:
# `http://127.0.0.1` is a secure context by specification, so the service worker, manifest and
# browser install all work with no TLS decision required). HSTS exists to upgrade a site that
# COULD be intercepted on the way to a plaintext connection; there is no "on the way" here, and
# pinning `max-age` on loopback would only ever matter if this process later bound a
# non-loopback address (an operator's own choice, already warned about loudly in `serve()`
# below) -- at which point a stale HSTS pin from an earlier loopback run would be actively
# wrong: forcing HTTPS at an address that was never issued a certificate.


#: The header set for `/api/*` (#534). The same three unconditional headers the static route
#: sends, plus the `no-store` that matters more here than anywhere else on this server.
#:
#: **`Cache-Control: no-store` is the layer BELOW the service worker's promise.** The design spec
#: routes `/api/*` as `NetworkOnly`, "no exceptions", because "opening the app to last week's
#: equity styled as current is worse than an error" -- but a service worker is a thing that may
#: not be installed, may have been unregistered, or may be a version behind. `no-store` on the
#: response means the browser's ordinary HTTP cache cannot hold an account balance either, whether
#: or not any worker is in the picture.
#:
#: **No CSP, deliberately**, for the reason `_STATIC_BASE_HEADERS` already records: CSP is a
#: response header with no defined meaning outside a browsing context, and `application/json` is
#: not one. `nosniff` is what carries the weight for this content type instead -- a JSON body a
#: browser is free to sniff as HTML is a stored-XSS primitive wearing a `Content-Type`.
_API_HEADERS: tuple[tuple[str, str], ...] = (
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    ("Cache-Control", "no-store, max-age=0"),
)

#: What every `/api/*` response is labelled. `charset=utf-8` explicitly, even though JSON's
#: default encoding is UTF-8 by RFC 8259: the payload carries `—`, `▲`, `▼` and `−` (#532's
#: non-colour gain/loss signal), and a client that guessed Latin-1 would render the whole contract
#: as mojibake.
_JSON_CONTENT_TYPE = "application/json; charset=utf-8"


def _static_headers(content_type: str) -> tuple[tuple[str, str], ...]:
    """`_STATIC_BASE_HEADERS` plus CSP, but ONLY when `content_type` is one of
    `_CSP_CONTENT_TYPES` -- see the comments on `_STATIC_BASE_HEADERS` and `_CSP_CONTENT_TYPES`
    for why the other static content types get no CSP at all, not a looser one."""
    if any(content_type.startswith(kind) for kind in _CSP_CONTENT_TYPES):
        return (
            ("Content-Security-Policy", _STATIC_CSP),
            *_STATIC_BASE_HEADERS,
        )
    return _STATIC_BASE_HEADERS


class KeelHandler(BaseHTTPRequestHandler):
    """GET and HEAD read; POST is the closed write surface (`do_POST`). No other verb is
    implemented, so no other verb reaches keel."""

    server_version = "keel"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    #: Set by `build_server`.
    cfg: ServeConfig

    #: Whether this request's body has already been read off the socket.
    #:
    #: Per REQUEST, not per connection, and reset at the top of each verb: a handler instance
    #: serves every request on a keep-alive connection, so a flag left set by one write would make
    #: the next refusal skip its drain and re-create the desynchronisation this exists to prevent.
    #: `_drain_request_body` reads and sets it; `_read_json_object` sets it.
    body_consumed: bool = False

    # -- logging --
    def log_message(self, fmt: str, *args: Any) -> None:
        """Overridden to a near-silence, and NEVER with the query string.

        The default implementation writes the raw request line to stderr, and the raw request
        line contains `?token=...` on the very first load. A server that prints its own session
        token into the terminal scrollback -- and into whatever collects that terminal's output --
        has published the credential it just minted."""
        return

    def _send(
        self,
        code: int,
        body: str,
        *,
        content_type: str = "text/plain; charset=utf-8",
        extra: tuple[tuple[str, str], ...] = (),
    ) -> None:
        payload = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        # `_STATIC_BASE_HEADERS`, not a set of its own: the two responses this method now sends
        # are the token-exchange redirect and a plain-text refusal, and neither is a browsing
        # context. The header set that used to live here carried a CSP written for the rendered
        # pages (`style-src 'unsafe-inline'`, for their inline stylesheet), and keeping a policy
        # that describes a page nobody serves any more would be a comment pretending to be a
        # defence. The shell keeps its CSP through `_static_headers`, which applies one to
        # `text/html` -- and that is where the shell is served from now.
        for name, value in _STATIC_BASE_HEADERS:
            self.send_header(name, value)
        for name, value in extra:
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _send_csv(self, text: str, filename: str) -> None:
        """One CSV export, with its own headers (#703).

        `nosniff` matters MORE here than on the JSON routes, not less: this body is a file a
        browser is being told to save, and a sniffing browser that decided some other type for it
        would be deciding what a downloaded file IS.

        `Content-Disposition: attachment` is the second half of that. Without it a browser may
        render the CSV inline, and an inline-rendered document from this origin is a different
        security question from a saved file -- `attachment` keeps it a download, and names it so
        the operator has a dated artefact rather than `export.csv` among ten others.

        `no-store` for the same reason every API response carries it: this is the operator's
        whole audit trail, and a copy of it in a shared cache is a copy nobody chose to make.

        The filename is server-generated and never echoed from the query string -- a
        caller-supplied one would put attacker-controlled text into a response header, which is
        the header-injection version of the formula injection `csv_safe` already defends the body
        against.
        """
        body = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        for name, value in _API_HEADERS:
            self.send_header(name, value)
        self.end_headers()
        # The same guard `_send`, `_send_json` and `_serve_static` all carry. `do_HEAD` delegates
        # to `do_GET`, so without it a HEAD sends the headers AND the whole file -- and on a
        # keep-alive connection those bytes are framed as the next response, which is a
        # same-origin response desync rather than merely a wasted transfer.
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, code: int, document: dict[str, Any]) -> None:
        """One JSON response, with its own headers.

        Writes them itself rather than going through `_send` for the same reason `_serve_static`
        does: the two senders need different values for the SAME headers, and sharing one method
        would have meant a parameter with a default -- which is how a header set silently changes
        for a route nobody was thinking about. `no-store` matters more here than anywhere else on
        this server (see `_API_HEADERS`).

        A plain `json.dumps`: `keel/web/payload.py` normalises every leaf to a string before it
        gets here, so there is nothing for an encoder to convert -- and the encoder a hurried
        author reaches for is `default=float`, which is the whole money contract dying in one
        keyword. Rule 6d of `test_console_thinness.py` fails the build on it in the serialiser;
        there is no `default=` here for the same reason.

        `ensure_ascii=False` because the payload is UTF-8 and the glyphs carrying #532's
        non-colour gain/loss signal have no business becoming escape sequences.
        """
        body = json.dumps(document, ensure_ascii=False)
        payload = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", _JSON_CONTENT_TYPE)
        self.send_header("Content-Length", str(len(payload)))
        for name, value in _API_HEADERS:
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _refuse(
        self, code: int, heading: str, detail: str, *, halt: dict[str, str] | None = None
    ) -> None:
        """A refusal, in the media type the caller asked for by the path it used.

        **Path-scoped, not method-scoped, and not content-negotiated.** A refusal handed to a
        `fetch()` client's `res.json()` in some other format is a parse error in the client, which
        is a strictly worse diagnostic than the 403 it is hiding -- so everything under
        `API_PREFIX` refuses in JSON.

        **Everything else refuses in PLAIN TEXT, since #540.** It used to refuse in HTML, through
        `render.page`, and that module is gone: the server generates no markup at all now. Plain
        text rather than JSON for these because the requests that land here are NAVIGATIONS -- a
        person has opened `http://127.0.0.1:8765/` in a browser without the token, and the useful
        answer is the sentence telling them to use the URL keel printed. A JSON envelope would
        wrap that sentence in punctuation for a reader who is not a program. It is not HTML and
        there is no template: two lines joined by a blank one.

        `Accept`-based negotiation was the alternative and it is worse here: a client that forgets
        the header would get the wrong media type from a JSON endpoint, and the one thing this
        server can be certain about is which path was requested.

        `halt` is `None` on every refusal but one: the expired session's on `/api/pulse`, for
        this session's cookie (`_expired_pulse_halt`, #985). It rides the JSON document only.
        """
        # **Before anything is written.** See `_drain_request_body`: a refusal that leaves a
        # request body unread poisons the next request on the same connection.
        self._drain_request_body()
        if urlsplit(self.path).path.startswith(API_PREFIX):
            self._send_json(code, api.refusal_document(code, heading, detail, halt=halt))
            return
        self._send(code, f"{heading}\n\n{detail}\n")

    def _declared_body_length(self) -> int | None:
        """The request body's length, or `None` when the framing cannot be trusted -- and then the
        connection is already marked to close.

        **One parser, so the drain and the reader cannot disagree** about where a body ends (#955
        review). Only ONE shape is trusted: no `Transfer-Encoding`, and either no `Content-Length`
        (a body of zero) or exactly one, made only of ASCII digits. Everything else is a request
        this server cannot size, and the safe answer is to stop using the connection rather than
        guess: `int()` happily reads `"+2"`, `"-2"` and `"1_0"` (as 10); a negative length reads
        as "no body"; a chunked body has no length at all; and two `Content-Length` headers (or one
        beside a `Transfer-Encoding`) are the request-smuggling shape, where this server and
        whatever sits in front of it measure the body differently. Draining by a wrong measure
        leaves body bytes to be parsed as the next request line.

        The halt never calls this to decide anything -- it reads no body and engages first.
        """
        # A header line the stdlib parser could not read (`Content-Length : 2`, a space before the
        # colon) ends the header parse early: the length never reaches `self.headers`, and what is
        # left sits in the payload. Absence of a length is then NOT "a body of zero".
        # By design this also closes (after answering) a `multipart/*` or `message/*` POST, whose
        # header block the stdlib parses as a MIME container. The console sends only JSON or no
        # body (`static/js/api.js`), and no route here takes a multipart upload.
        if self.headers.defects or self.headers.get_payload():
            self.close_connection = True
            return None
        if self.headers.get("Transfer-Encoding") is not None:
            self.close_connection = True
            return None
        values = self.headers.get_all("Content-Length") or []
        if not values:
            return 0
        # Bounded BEFORE `int()`, which raises ValueError past 4300 digits. A few leading zeros
        # ("01") still work; an absurdly padded or huge value closes like any unsizeable length.
        if (
            len(values) > 1
            or len(values[0]) > _MAX_CONTENT_LENGTH_DIGITS
            or re.fullmatch(r"[0-9]+", values[0]) is None
        ):
            self.close_connection = True
            return None
        return int(values[0])

    def _drain_request_body(self) -> None:
        """Consume an unread request body, or close the connection rather than consume it.

        **This is keep-alive correctness, and its absence was a real bug.** `protocol_version` is
        HTTP/1.1, so a connection is reused by default. A POST refused before its body is read
        leaves those bytes sitting in the socket, and the stdlib then parses them as the NEXT
        request line -- which is not a request line, so the next request dies as a 501. The symptom
        is the worst kind: the refusal itself is correct, and the request AFTER it fails, with a
        status that has nothing to do with either.

        It could not happen before #540 because the CSRF token was a field in the form body, so
        every write read its body before it could refuse. Moving the token into a header (which is
        what let it prove something a body cannot) put the refusal in front of the read. Found by
        driving a browser, which reuses connections; every test in the suite opened a fresh one and
        could not have seen it.

        **An oversized body is not drained, it is disconnected.** Reading it to be polite would
        hand an attacker exactly the memory-exhaustion primitive `_MAX_BODY_BYTES` exists to deny.
        Closing costs the client one reconnection and costs this server nothing.
        """
        if self.body_consumed:
            # Already off the socket -- `do_POST` reads the body before it can refuse for a reason
            # that depends on the body's CONTENT (an unknown action key, an unreadable object).
            # Reading it a second time blocks until the client's timeout, which is a hang rather
            # than an error and was exactly what the first version of this did.
            return
        length = self._declared_body_length()
        if length is None:
            return
        if length == 0:
            return
        if length > _MAX_BODY_BYTES:
            self.close_connection = True
            return
        # Before the read: whatever the read does, this request's body is no longer ours to read,
        # and `do_POST`'s closing drain must not wait on it a second time.
        self.body_consumed = True
        try:
            self.rfile.read(length)
        except OSError:
            # A client that vanished mid-body, or one that declared a body and then went silent:
            # the read gives up after `REQUEST_TIMEOUT_SECONDS` without a byte (#981), and a
            # refusal still answers. A client that keeps trickling is not cut off here.
            self.close_connection = True

    def handle_one_request(self) -> None:
        """One request, and then a GET's or HEAD's body leaves the socket (#981).

        **A GET ends at a request boundary too.** A GET may carry a body -- pointless, but legal
        -- and the success path never read one, so on a kept-alive connection the stdlib parsed it
        as the next request line: #955's shape on the read path, answered 501. `do_HEAD` delegates
        to `do_GET`, so both are covered here.

        **Why here, not in a `finally` like `do_POST`'s.** `do_GET` is left exactly as it was, so
        the dataflow from `self.path` to what it reaches is unchanged; wrapping its body would
        have put a new call on that path. The request boundary is the stdlib's own, and the drain
        costs a no-op for the common case, a GET with no body.

        **Skipped when `close_connection` is already set**: EOF, a timed-out `readline`, an SSE
        stream, or framing already distrusted. There is no next request to protect, and reading
        on would only wait. That is also the only time `self.command` is stale (the previous
        request's, or unset), so it is read only after that check.

        `do_GET` resets `body_consumed` at its top, so the drain starts clean; a refusal already
        drained its own body, which makes this a no-op for it. An exception out of `do_GET` skips
        the drain, which does not matter: the stdlib then drops the connection.
        """
        super().handle_one_request()
        if not self.close_connection and self.command in ("GET", "HEAD"):
            self._drain_request_body()

    def parse_request(self) -> bool:
        """The stdlib's parse, and then an HTTP/0.9 request is refused before any verb sees it
        (#989).

        **No route here can answer 0.9 correctly.** A versionless request line (`GET /x`) gets no
        header block parsed, so `self.headers` is a plain `dict` and `_declared_body_length` --
        reached by every `_refuse` -- raised on it: a reset instead of an answer, and a traceback
        on the stderr `log_message` keeps quiet. The explicit form (`GET /x HTTP/0.9`) does get
        headers, so it could be admitted, and was then answered in 0.9 framing: a body with no
        status line and no length, which on a kept-alive connection sat in front of the next
        response. Both are the same check -- the stdlib's own test for "answer without a status
        line" -- so after this every request a verb sees has a parsed header block.

        **Answered, then closed.** A 505 with a status line, as the stdlib frames every other
        request line it refuses, rather than a silent close: a client that sent a version this
        server will not speak is told so. `request_version` is moved off 0.9 only so that the
        refusal carries its status line; `send_error` marks the connection to close. No browser
        sends 0.9, the console's `fetch` included, so the halt is never refused by this.
        """
        if not super().parse_request():
            return False
        if self.request_version != "HTTP/0.9":
            return True
        self.request_version = "HTTP/1.0"
        self.send_error(
            HTTPStatus.HTTP_VERSION_NOT_SUPPORTED, explain="keel speaks HTTP/1.0 and HTTP/1.1."
        )
        return False

    # -- shared admission --
    def _admitted(self, *, outlives_the_lifetime: bool = False) -> bool:
        """Host header, then the remote session lifetime, then session cookie. `False` means a
        refusal has already been sent.

        Factored out so GET and POST cannot drift into two admission policies -- a write path
        with a laxer check than the read path is exactly the shape of a bug nobody notices.

        `outlives_the_lifetime` skips the LIFETIME and nothing else, and exactly one caller sets
        it: `_post`, for a halt that carries this session's halt token (`_is_a_tokened_halt`,
        #983). The one other request that skips it is decided here rather than by `do_GET`, so
        that handler is untouched: a read of the shell carrying this session's cookie
        (`_is_a_sessioned_shell_read`, #1006). Every other request -- a release, a setup write,
        every `/api/` read -- keeps it.
        """
        if not self.cfg.host_policy.permits(self.headers.get("Host")):
            # DNS rebinding lands exactly here: the packet arrived on loopback, so the bind check
            # passed, and only the header tells the truth about who the browser thinks it is
            # talking to.
            self._refuse(
                403,
                "Refused",
                "This request did not come from the address keel is serving on.",
            )
            return False
        # #648: an ENFORCED lifetime, checked before the token so an expired session cannot be
        # distinguished from a wrong one by which refusal comes back -- with two exceptions. Since
        # #985, on a GET or HEAD of `/api/pulse` a request bearing THIS session's cookie gets a
        # refusal that carries `halt`, and a wrong or missing cookie gets `halt: null`. Since
        # #1006, a GET or HEAD of the shell bearing THIS session's cookie is served it, and a
        # wrong or missing cookie is refused as expired. Both are acceptable because only the
        # holder of the real 256-bit session token can see the difference, and it tells them
        # nothing they could not already use (`_is_a_tokened_halt` admits that halt anyway, and
        # the shell is the package's files). Every other request still cannot tell the two
        # apart. Only a remote-configured server has a lifetime -- see
        # `ServeConfig.session_expired_at`.
        #
        # #983, the owner's decision: the halt is exempt. A console left running past the lifetime
        # refused its own stop, and refusing a stop is the one failure mode this surface must not
        # have. What the exemption gives a leaked token is a halt -- which `keel resume` at a
        # terminal undoes -- and never a release: the halt token opens no other route (#968). The
        # cookie below is still checked, and so is the halt token, by `_gated_action`.
        #
        # It serves a console still showing a Pulse from before the lifetime ended (the token is
        # in its memory), or any client that holds the token. A RELOADED console gets the token
        # back from this refusal itself, on `/api/pulse` and for this session's cookie only
        # (#985, `_expired_pulse_halt`). A browser with no cached shell gets the page from this
        # server past the lifetime too, for this session's cookie only (#1006,
        # `_is_a_sessioned_shell_read`), so it reaches that refusal. The refusal still names
        # `keel kill`, for a browser without the cookie, which is refused the page as before.
        expires = self.cfg.session_expired_at
        if (
            expires is not None
            and not outlives_the_lifetime
            and time.time() >= expires
            and not self._is_a_sessioned_shell_read()
        ):
            self._refuse(
                403,
                "Session expired",
                "This session has reached its lifetime. keel enforces one when it is configured "
                "to answer a remote hostname, because a token that leaked into a URL, a "
                "screenshot or terminal scrollback should not still work tomorrow. Restart "
                "`keel serve` and open the address it prints. To stop trading now, run "
                "`keel kill` at the terminal.",
                halt=self._expired_pulse_halt(),
            )
            return False
        cookies = parse_cookie_header(self.headers.get("Cookie"))
        if not tokens_match(cookies.get(SESSION_COOKIE), self.cfg.token):
            self._refuse(
                403,
                "Not authorised",
                # #634: the old text's only instruction was "open the address keel printed",
                # which is an instruction an installed app -- a window with no address bar --
                # cannot follow, and which does not say WHY a link that worked yesterday stopped.
                # Both halves are fixed here: the cause is named, and the action named is one
                # that can be taken from wherever this is being read.
                "keel is running and refused this browser. The address keel prints when it "
                "starts carries the token for that run, and the token is new every run and is "
                "never written to disk -- so if keel has been restarted since this browser last "
                "worked, the address printed THIS time is the one that admits it. Paste that "
                "address into the address bar here, or paste just its token into the field the "
                "console offers when it is refused. "
                "If you opened that address and still see this, an installed service worker "
                "from a build before this one is answering the page from its cache, so the "
                "token never reached the server: unregister it once (browser devtools, "
                "Application, Service Workers, Unregister) or reload with the cache emptied, "
                "then open the printed address again.",
            )
            return False
        return True

    def _is_a_sessioned_shell_read(self) -> bool:
        """Whether this request is a GET (or HEAD) of the client shell carrying this session's
        cookie -- the read the remote session lifetime does not bind (#1006).

        **Why.** Since #985 an expired `/api/pulse` refusal carries the HALT control to this
        session's cookie, but only a console that is already loaded can read it. A browser with no
        cached console -- a first visit after the lifetime, through the printed `?token=` address,
        or a browser whose cached files were cleared -- was refused the page itself, so it never
        drew the button and its only stop was `keel kill` at a terminal.

        **What it re-opens: files, never data.** The shell is `staticfiles.STATIC_ROOT` byte for
        byte -- `_serve_static` reads and sends it unaltered, there is no template -- so it carries
        no reading of the ledger and no token; the halt token still comes only from the
        cookie-matched Pulse refusal (#985), and `gates_token` is served nowhere (#968). The test
        is membership in `staticfiles.shell_request_paths`, the paths `_serve_static` answers with
        a file: a shipped asset or a name in `CLIENT_ROUTES` / `RETIRED_CLIENT_ROUTES`. The request
        path is only compared against that package-side list, never passed to a filesystem call,
        so this adds no second user-controlled path flow. The list is walked per call, which is
        acceptable because this is reached only once the lifetime has expired. A spelling
        `_serve_static` would still resolve (`/js/../js/main.js`) is not in the list, so it is
        NOT exempted and is refused as expired: stricter, and fine. Everything under `API_PREFIX`
        (`EVENTS_PATH` included) and `SWITCH_PREFIX` is excluded by name first, so no later file
        under the static root can shadow one; an unmapped path keeps the lifetime and is refused as
        expired, not 404ed.

        **Why this session's cookie, and not any browser (#1006's held question, ruled here).**
        Before the lifetime, a request without the cookie is refused the shell
        (`test_static_assets_are_behind_the_same_admission_as_every_other_page`: "not exempted
        ... just because it holds no secrets today"). Serving it to anyone after expiry would make
        an EXPIRED session more open to a stranger than a live one, and a browser without the
        cookie gains nothing from the page: the Pulse refusal draws HALT only for the cookie. A
        browser that has only the printed address gets the cookie from the token exchange, which
        `do_GET` performs before admission, so the first visit is covered. So, as for #985: only
        the holder of the real session token can see the difference, and it tells them nothing
        they could not already use.
        """
        if self.command not in ("GET", "HEAD"):
            return False
        path = urlsplit(self.path).path
        if path.startswith((API_PREFIX, SWITCH_PREFIX)):
            return False
        if unquote(path) not in staticfiles.shell_request_paths(staticfiles.STATIC_ROOT):
            return False
        cookies = parse_cookie_header(self.headers.get("Cookie"))
        return tokens_match(cookies.get(SESSION_COOKIE), self.cfg.token)

    def _expired_pulse_halt(self) -> dict[str, str] | None:
        """The HALT control's inputs for the expired-session refusal, or `None` (#985).

        **Owner-approved design.** Since #983 a halt carrying the halt token outlives the remote
        session lifetime, but the token reaches a browser only on `/api/pulse`, which keeps the
        lifetime -- so a console reloaded after it had no button. The expiry refusal on that one
        read now carries #958's `{halt_token, resume_command}` (`api.pulse_halt`), the same shape
        a failed Pulse carries, so the client draws the same control.

        Three conditions, all of them: a GET (or HEAD) -- the read, never a write to the same
        path; a path whose route is the one that halts (`ApiRoute.halt_on_failure`, `/api/pulse`
        alone); and THIS session's cookie. Without the cookie the refusal is the #648 one with
        nothing extra: the halt token is derived from the session token, and a request that has
        not shown the session is not handed a credential. With it, nothing is handed that the
        cookie's holder could not already use -- `_is_a_tokened_halt` admits exactly that halt.
        This is the one place an expired session's refusal differs from a wrong token's (see the
        #648 note in `_admitted`); only the holder of the real session token can see it.

        Nothing else about the expired session changes: the refusal is still a 403 with
        `data: null`, no release and no setup write is admitted, no other read answers, and no
        other endpoint serves the halt token. Never `gates_token` (#968), here as on every path.
        The one ledger read is `pulse_halt`'s kill-switch reading, read-only, as on #958's path.
        """
        if self.command not in ("GET", "HEAD"):
            return None
        route = api.API_ROUTES.get(urlsplit(self.path).path)
        if route is None or not route.halt_on_failure:
            return None
        cookies = parse_cookie_header(self.headers.get("Cookie"))
        if not tokens_match(cookies.get(SESSION_COOKIE), self.cfg.token):
            return None
        return api.pulse_halt(self.cfg)

    def _sec_fetch_site_ok(self) -> bool:
        """Checked on EVERY `POST`, `/setup/*` included -- unlike `_api_client_header_ok` below,
        a plain HTML `<form method=post>` DOES carry `Sec-Fetch-Site`: it is Fetch Metadata, set
        by the browser on every request the page itself initiates, forms included, and page
        JavaScript can neither set nor override it.

        That asymmetry is exactly why a WRONG value is refused while a MISSING one is not: a
        wrong value (`cross-site`, `same-site`) is the browser itself reporting that this request
        did not originate on this page, which nothing here could otherwise learn. A missing value
        proves nothing -- the header is a comparatively recent Fetch Metadata addition (Safari
        shipped it later than Chrome/Firefox) -- so refusing its absence would turn a
        defence-in-depth layer into an availability bug for exactly the users who most need every
        OTHER layer (host validation, the session cookie, the CSRF token) to hold."""
        sec_fetch_site = self.headers.get("Sec-Fetch-Site")
        if sec_fetch_site is not None and sec_fetch_site != "same-origin":
            self._refuse(
                403,
                "Refused",
                "This request's Sec-Fetch-Site header says it did not originate on this page.",
            )
            return False
        return True

    def _api_client_header_ok(self) -> bool:
        """The third CSRF layer (#535). Scoped to `API_PREFIX`, which since #540 includes
        the write path. This was gated at the top of `do_POST`, over every write, in an
        earlier version of this change, and that was a defect, not a stricter check: the shipped
        UI's entire write surface WAS a plain HTML `<form method=post action="/setup/...">`, and
        the rendered pages shipped no `script-src` at all -- so there was no code path by which
        that form could set a custom request header, and gating it here would have refused every
        legitimate submission the shipped client could make, with no fallback: the desktop bundle
        has no terminal.

        **That form is deleted (#540) and this check now covers the write path**, which is what
        the last paragraph below said should happen "once, and only once, the forms it replaces
        are gone".

        `X-Keel-Client: 1` is a real defence where it CAN apply: a custom header forces a CORS
        preflight a hostile origin cannot satisfy, closing the one gap `SameSite=Strict` and the
        HMAC CSRF token both assume shut -- a plain form POST, which is never preflighted, in any
        browser. "A custom header" and "a `fetch()` client" are the same requirement, and every
        write now comes from a `fetch()` client."""
        if self.headers.get("X-Keel-Client") != "1":
            self._refuse(
                403,
                "Refused",
                "This request is missing the header keel's own API client always sends.",
            )
            return False
        return True

    def _serve_events(self) -> None:
        """The `EventSource` stream (#537), for as long as the browser holds the connection.

        **`Connection: close` and no `Content-Length`.** This handler speaks HTTP/1.1, where a
        response with neither a length nor chunked framing has to be delimited by the connection
        ending -- and keep-alive is what would otherwise be assumed. The alternative is chunked
        transfer-encoding, hand-framed on top of `wfile`; it buys the ability to reuse a socket
        that this endpoint holds open for ten minutes anyway, which is nothing, in exchange for a
        second framing layer to get wrong.

        **Every write is flushed.** A buffered `wfile` is a stream that arrives in bursts when the
        buffer happens to fill, which for frames this small means "never" -- the page would show
        nothing at all and the failure would look exactly like a server that is not sending.

        **A HEAD gets the headers and no body.** `do_HEAD` delegates to `do_GET` here as it does
        everywhere else, and a HEAD that opened a ten-minute stream would be a way to hold a
        thread without ever reading from it.

        **A disconnect is not an error.** The browser closing the tab, navigating away, or being
        killed all surface as a broken pipe on the next write; that is the normal end of a
        subscription and it is caught and dropped rather than logged as a failure or allowed to
        reach `BaseHTTPRequestHandler`'s 500 path, which would try to write a body to a socket
        that is already gone.
        """
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", events.CONTENT_TYPE)
        self.send_header("Connection", "close")
        for name, value in _API_HEADERS:
            self.send_header(name, value)
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            for chunk in events.stream(self.cfg):
                self.wfile.write(chunk.encode("utf-8"))
                self.wfile.flush()
        except BrokenPipeError, ConnectionResetError:
            return

    def _serve_static(self, url_path: str) -> None:
        """One file under `staticfiles.STATIC_PREFIX` (#535), or the same 404 an unmapped
        `ROUTES` path gets -- containment and the Content-Type table are `staticfiles`'s job
        (`tests/web/test_staticfiles.py` pins the resolver in isolation); this method's only
        responsibility is refusing anything it returns `None` for, uniformly, so a missing
        static file and a missing page look identical to a client probing the server.

        **A file wins over a client route, always** (#536). `resolve_client_route` is consulted
        only where `resolve_static_asset` found nothing, so no name in `CLIENT_ROUTES` can shadow
        a shipped asset -- and, more importantly, the reverse cannot happen either: a `.js` file
        that is missing or misspelled stays a 404 rather than becoming a 200 of HTML that the
        browser then refuses to execute under `nosniff`, which is a MIME-type error several steps
        removed from its cause. See `CLIENT_ROUTES`'s own note on why that list is closed.
        """
        resolved = staticfiles.resolve_static_asset(staticfiles.STATIC_ROOT, url_path)
        if resolved is None:
            resolved = staticfiles.resolve_client_route(staticfiles.STATIC_ROOT, url_path)
        content_type = staticfiles.content_type_for(resolved) if resolved is not None else None
        if resolved is None or content_type is None:
            self._refuse(404, "No such page", f"Nothing is served at {url_path}.")
            return

        try:
            # A second filesystem race between `resolve_static_asset`'s existence check and this
            # read (the file removed, a permission change) must be a clean 500 like any other
            # broken page (`test_a_broken_page_does_not_take_the_server_down`'s guarantee),
            # never an uncaught exception that leaves the connection hanging.
            payload = resolved.read_bytes()
        except OSError as exc:
            self._refuse(500, "That file could not be read", f"{type(exc).__name__}: {exc}")
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        for name, value in _static_headers(content_type):
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    # -- the request --
    def do_HEAD(self) -> None:  # noqa: N802 - stdlib's naming, not ours
        self.do_GET()

    def do_POST(self) -> None:  # noqa: N802 - stdlib's naming, not ours
        """The ENTIRE write surface (#437, moved under `/api/` at #540). Read
        `keel/web/__init__.py` before extending it.

        Four checks apply to every write, in this order -- admission (`_admitted`),
        `Sec-Fetch-Site`, the `/api/` prefix and the `X-Keel-Client` header -- and then the path
        splits. Admission is the same for every write but one: a halt carrying its halt token is
        not bound by the remote session lifetime (#983, `_is_a_tokened_halt`); it still needs the
        session cookie, and every other check. A setup action must also carry the session's CSRF
        write token; a gated action is `_gated_action`'s: the halt carries `halt_token` in
        `HALT_HEADER` and nothing more, and a release carries `gates_token` in `GATES_HEADER`
        plus locality and the typed phrase -- three tokens, three headers, no route accepting
        another's (#968). Read its own docstring for why. That every write now passes the
        `X-Keel-Client` gate is the change #540 brought.
        While the write surface was an HTML form at `/setup/*`, the `X-Keel-Client` gate could
        not cover it (a form cannot set a header) and the two paths had to differ;
        `_api_client_header_ok`'s docstring called that "the fix for one" defect and said the
        widening was to happen "once, and only once, the forms it replaces are gone". They are
        gone.

        There is no dynamic dispatch here and no getattr on a user-supplied name. A write reaches
        keel through exactly two tables: `keel.commands.setup.ACTIONS` -- which contains only
        idempotent, non-destructive, `MECHANICAL` steps and cannot contain anything else without
        failing a test -- or `gates.TIER1_ACTIONS` plus the halt, behind `_gated_action`.

        **Every POST ends at a request boundary (#955).** A branch that answers without reading
        the body -- the halt, which takes no phrase and no values -- used to leave whatever a
        client sent anyway in the socket, and on a kept-alive connection the stdlib parsed it as
        the next request line (`{}GET /api/pulse`, answered 501). The drain is in a `finally`
        here rather than in each branch, so a branch added later cannot forget it; it is bounded
        and closes instead of reading an oversized or unreadable length (`_drain_request_body`).
        Drain rather than always close: the console's next read rides the same connection, and
        closing on every halt would cost each one a reconnect for a body nobody needs.

        AFTER the answer, on purpose: the halt has already engaged and been reported by then, so a
        client that declares a body it never sends stalls only its own connection, never the stop
        -- stopping is never slower than starting (`keel/web/gates.py`)."""
        self.body_consumed = False
        try:
            self._post()
        finally:
            self._drain_request_body()

    def _post(self) -> None:
        """`do_POST`'s routing, minus the drain that every branch shares. Read `do_POST`."""
        parsed = urlsplit(self.path)
        if not self._admitted(outlives_the_lifetime=self._is_a_tokened_halt(parsed.path)):
            return

        if not self._sec_fetch_site_ok():
            return

        if not parsed.path.startswith(API_PREFIX):
            # Not "method not allowed" -- there is no write surface at this path at all, and
            # saying so is both true and less informative to someone probing.
            self._refuse(404, "No such action", f"Nothing accepts a POST at {parsed.path}.")
            return

        if not self._api_client_header_ok():
            return

        if parsed.path.startswith(API_GATES_PREFIX):
            self._gated_action(parsed.path[len(API_GATES_PREFIX) :])
            return

        if not parsed.path.startswith(API_SETUP_PREFIX):
            self._refuse(404, "No such action", f"Nothing accepts a POST at {parsed.path}.")
            return

        # The HMAC layer, "the layer that does not depend on the browser being current". It rides
        # in a HEADER rather than in the body, so a request that reaches this line has proved the
        # same thing twice -- a form can set neither header, and a cross-origin `fetch` cannot get
        # past the preflight that either of them triggers.
        if not tokens_match(self.headers.get(CSRF_HEADER), csrf_token(self.cfg.token)):
            self._refuse(
                403,
                "Refused",
                "That request did not carry this session's write token. Reload the page and try "
                "again.",
            )
            return

        values = self._read_json_object()
        if values is None:
            self._refuse(400, "Unreadable request", "The body was not a JSON object of fields.")
            return

        key = parsed.path[len(API_SETUP_PREFIX) :]
        try:
            result = run_setup_action(self.cfg, key, values)
        except Exception as exc:
            # A failed step is a stated 500 with a JSON body, never a traceback down the socket
            # and never a 200 whose body says it went fine.
            self._refuse(500, "That step could not be completed", f"{type(exc).__name__}: {exc}")
            return
        if result is None:
            self._refuse(404, "No such action", f"{key!r} is not a setup step keel performs.")
            return

        # No POST/redirect/GET any more, and nothing to redirect TO: the page that used to be
        # reloaded is a client view that re-reads `/api/setup` itself. The actions are idempotent,
        # so a repeated submission is harmless by construction rather than by a redirect.
        self._send_json(200, api.action_document(result))

    def _is_a_tokened_halt(self, path: str) -> bool:
        """Whether this POST is the halt AND carries this session's halt token -- the one request
        the remote session lifetime does not bind (#983, `_admitted`).

        **The token is part of the test, not only checked later.** An expired session that sends
        the halt WITHOUT it is refused as "Session expired", exactly as before, rather than
        reaching the halt-token refusal; so the exemption tells a caller without the token nothing
        it could not learn already. `_gated_action` still checks the token itself, so nothing
        about the halt's own refusal rests on this.
        """
        return path == API_GATES_PREFIX + HALT_KEY and tokens_match(
            self.headers.get(HALT_HEADER), halt_token(self.cfg.token)
        )

    def _gated_action(self, key: str) -> None:
        """One Tier 1 release, or the halt (#781). Four checks, and the order is the contract.

        `_admitted` and `_api_client_header_ok` are already behind us -- this method is reached
        only from `do_POST`, after them. What it adds:

        1. **The halt is separated first, and takes no locality check.** `keel kill` is ungated
           because a ceremony in front of the stop makes the stop slower than the start, and the
           same argument refuses to add a LOCALITY condition to it: an operator whose deployment
           is behind a tunnel must be able to halt from the tunnel. It still needs the session
           and `halt_token` in `HALT_HEADER`, because it is a write; it needs nothing else,
           because refusing a stop is the one failure mode this surface must not have. The halt
           token is the one `/api/pulse` serves, and it is checked HERE ONLY: it opens no release
           (#968). For the same reason a halt carrying it outlives the remote session lifetime,
           which `_post` lifts for it alone (`_is_a_tokened_halt`, #983) -- the session cookie
           itself is still required.

        2. **Locality, for a release.** `gated_action_permitted` wants a loopback peer, a
           loopback bind and no declared remote origin -- see its own docstring for why the peer
           alone is not enough (a tunnel forwards to loopback) and why the bind is read too (a
           declaration is something an operator types, and two ordinary deployments never do).

        3. **The gate token** in `GATES_HEADER`, derived apart from the setup token and from the
           halt token, so a value captured from `/api/setup`'s or `/api/pulse`'s document cannot
           release a rail. No endpoint serves it, so a browser session cannot produce it -- which
           is what makes "no browser resume" a property of the server and not only of the client.

        4. **The typed phrase**, checked in `gates.run_gated_action`, which opens no database
           until it matches.

        An unknown key is a 404 and a failed check is a 403, and neither ever echoes the phrase:
        a refusal that quoted it back would put a credential-shaped string into logs and into
        whatever the operator pastes while asking for help.
        """
        from keel.web import gates

        if key == HALT_KEY:
            if not tokens_match(self.headers.get(HALT_HEADER), halt_token(self.cfg.token)):
                self._refuse(403, "Refused", _NO_HALT_TOKEN)
                return
            try:
                halted = gates.run_halt(self.cfg)
            except Exception as exc:
                # A halt that could not be written -- the database is gone (`connect_existing`
                # creates none, #981), or locked past its busy timeout -- is a stated 500 the
                # console shows beside the button, never a reset connection that reads as nothing
                # having happened. `keel kill` at a terminal is the fallback it names.
                self._refuse(
                    500,
                    "The halt could not be recorded",
                    f"{type(exc).__name__}: {exc}. Run `keel kill` at the terminal.",
                )
                return
            self._send_json(200, api.gate_document(HALT_KEY, halted))
            return

        if key not in gates.TIER1_ACTIONS:
            self._refuse(404, "No such action", f"{key!r} is not an action this gate performs.")
            return

        if not gated_action_permitted(
            self.client_address,
            external_hosts=self.cfg.external_hosts,
            bound_host=self.cfg.host,
        ):
            self._refuse(
                403,
                "Refused",
                "Gated actions are restricted to a loopback session on a loopback bind. This "
                "deployment is reachable from elsewhere, so keel refuses them here and they "
                "remain available at the terminal.",
            )
            return

        if not tokens_match(self.headers.get(GATES_HEADER), gates_token(self.cfg.token)):
            self._refuse(403, "Refused", _NO_GATE_TOKEN.format(command=_RELEASE_COMMAND[key]))
            return

        values = self._read_json_object()
        if values is None:
            self._refuse(400, "Unreadable request", "The body was not a JSON object of fields.")
            return

        done = gates.run_gated_action(self.cfg, key, values.get("phrase"))
        if done is None:
            self._refuse(403, "Refused", "That was not the phrase this action asks for.")
            return
        self._send_json(200, api.gate_document(key, done))

    def _read_json_object(self) -> dict[str, str] | None:
        """The JSON request body as `{field: string}`, or `None` for anything else.

        Bounded, because `rfile.read(n)` with an attacker-supplied `n` is a memory-exhaustion
        primitive and this server has no proxy in front of it to impose a limit. A setup action
        carries a handful of named fields; anything past a few kilobytes is not one.

        **Every value is coerced to `str`, and a nested object or array makes the whole body
        `None`.** `run_setup_action` hands these to an `Action.run`, and a field that arrived as a
        list or a dict would reach code written for a string -- the shape of bug that shows up as
        a `TypeError` deep inside a step that has already half-run. JSON replaced urlencoded form
        bodies at #540 along with the form that sent them; this is the one place that difference
        can be exploited, so it is the one place it is refused.
        """
        length = self._declared_body_length()
        if length is None or length > _MAX_BODY_BYTES:
            # Untrusted framing has already closed the connection; an oversized length is closed
            # by the drain that every refusal runs.
            return None
        if length == 0:
            # An action with no declared inputs is submitted with an empty body, and that is a
            # legitimate request rather than a malformed one.
            return {}
        self.body_consumed = True
        raw = self.rfile.read(length).decode("utf-8", "replace")
        try:
            parsed = json.loads(raw)
        except ValueError:
            return None
        if not isinstance(parsed, dict):
            return None
        values: dict[str, str] = {}
        for name, value in parsed.items():
            if isinstance(value, (dict, list)):
                return None
            values[str(name)] = "" if value is None else str(value)
        return values

    def _switch(self, target: str) -> None:
        """Send the browser into another console serving from this deployment root (#814).

        **The token travels the way `keel open` already sends it, and no other way.** The page
        offers `/switch/<port>` and nothing more; this reads the peer's record NOW -- so a peer
        restarted since the page loaded, with a new token, is still reached -- and answers with a
        `303` to that peer's `?token=` address. The peer exchanges the token for its own cookie
        and strips it from the URL (`do_GET`'s hand-off). Only the browser's navigation carries
        it: no page script can read a redirect's `Location`, so the page never holds a session
        token, its own or anyone's, which #815's `<option value>` did.

        **Local only, by the floor `gated_action_permitted` holds for a release.** Entering the
        live console from the paper one hands over the live console's session, so this requires
        what reading the `0600` record directly would: this machine. A declared remote origin or
        an off-loopback bind refuses, and so does a peer that is not loopback -- a tunnel presents
        as loopback, which is why the deployment's posture is checked as well.

        Behind `_admitted` (the caller's), so without this console's own session nothing about
        any other is revealed, not even whether it is running. An unknown or dead port is a 404
        and names no console.
        """
        if not gated_action_permitted(
            self.client_address,
            external_hosts=self.cfg.external_hosts,
            bound_host=self.cfg.host,
        ):
            self._refuse(
                403,
                "Refused",
                "Switching consoles is restricted to a loopback session on a loopback bind. Use "
                "`keel open` at the terminal of the machine running keel.",
            )
            return
        port = int(target) if target.isdigit() else None
        if port == self.cfg.port:
            self._send(303, "", extra=(("Location", "/"),))
            return
        record = runtime.live_record(port) if port is not None else None
        if record is None:
            self._refuse(
                404,
                "No such console",
                "No keel console is running on that port now. Reload this page for the current "
                "list.",
            )
            return
        self._send(303, "", extra=(("Location", runtime.url_for(record)),))

    def do_GET(self) -> None:  # noqa: N802 - stdlib's naming, not ours
        self.body_consumed = False
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query, keep_blank_values=True)

        if not self.cfg.host_policy.permits(self.headers.get("Host")):
            # DNS rebinding lands exactly here: the packet arrived on loopback, so the bind check
            # passed, and only the header tells the truth about who the browser thinks it is
            # talking to. Checked before the token exchange below, so a rebinding attempt cannot
            # probe token validity by watching which refusal it gets.
            self._refuse(
                403,
                "Refused",
                "This request did not come from the address keel is serving on.",
            )
            return

        presented = (query.get("token") or [""])[0]
        if tokens_match(presented, self.cfg.token):
            # Exchange the token for a cookie and get it out of the URL, so it stops appearing in
            # history, in a bookmark, and in anything the user pastes when asking for help.
            self._send(
                303,
                "",
                extra=(
                    ("Location", parsed.path or "/"),
                    ("Set-Cookie", session_cookie(self.cfg.token)),
                ),
            )
            return

        if not self._admitted():
            return

        if parsed.path.startswith(SWITCH_PREFIX):
            self._switch(parsed.path.removeprefix(SWITCH_PREFIX))
            return

        if parsed.path == EVENTS_PATH:
            # Live updates (#537). Checked BEFORE the `API_PREFIX` branch below, because that
            # branch ends in `api.respond`, which would answer this path with the 404 it gives
            # every name absent from its table -- correctly, since this endpoint is not in it.
            self._serve_events()
            return

        if parsed.path.startswith(API_PREFIX):
            # The JSON API (#534). Reads only: `api.respond` maps a path to one bounded read and
            # returns `(status, document)` -- it never raises, so a broken report becomes a stated
            # 500 with a JSON body rather than an HTML error page a `fetch()` client cannot parse,
            # and never an empty payload that a view would render as zeros.
            #
            # Same admission as every rendered page, checked above and never weakened: an API is
            # not exempt from the loopback-plus-session model for being machine-readable. What it
            # does NOT additionally require is `X-Keel-Client` -- that header gates POSTs, and its
            # docstring explains why a GET is not the gap it closes.
            # #703's CSV export is the one path under `/api/` that does not answer JSON. It is
            # checked HERE, inside the same admission the JSON routes passed, so it inherits the
            # loopback-plus-session model unchanged -- an export of the whole audit trail is the
            # last thing that should be reachable more easily than the page it came from.
            if parsed.path == api.CSV_EXPORT_PATH:
                # Wrapped, because `respond` is what normally guarantees this server never
                # answers a GET by raising: it turns an `ApiRefusal` into a 400 and anything else
                # into a 500 envelope. This branch does not go through it, so without this a
                # `?limit=abc` propagated out of the handler -- the client saw a reset connection
                # with no response at all, and a traceback of absolute source paths reached the
                # stderr that `log_message` is overridden to keep quiet.
                try:
                    text, filename = api.export_timeline_csv(self.cfg, query)
                except Exception as exc:  # noqa: BLE001 - a failed export must still answer
                    # One arm, because nothing on the export path raises `ApiRefusal`: it reads
                    # no `?limit=` and no `?sort=`, which are the two refusing helpers. A second
                    # arm for it was written and removed -- an unreachable error path is one
                    # nothing exercises, and it rots. Re-add it the day a refusing helper joins
                    # this path.
                    code, document = api.export_failure_envelope(exc)
                    self._send_json(code, document)
                    return
                self._send_csv(text, filename)
                return
            code, document = api.respond(self.cfg, parsed.path, query)
            self._send_json(code, document)
            return

        # Everything else is the client: a file under the static root, or one of the names
        # `CLIENT_ROUTES` (or `RETIRED_CLIENT_ROUTES`) answers with the shell. `_serve_static`
        # 404s anything that is neither, which is what keeps a mistyped asset a 404 rather than a
        # 200 carrying HTML.
        #
        # This is the last branch because the prefix stopped narrowing anything when #540 moved
        # the mount to `/`: `staticfiles.STATIC_PREFIX` matches every path now, so the ORDER of
        # these branches -- events, then `/api/`, then the client -- is what separates them, not
        # the prefixes themselves.
        self._serve_static(parsed.path)


class KeelServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    #: IPv6 needs the family set before `server_bind`; `::1` is a legitimate loopback address and
    #: a `--host ::1` that silently bound IPv4 would be a lie.
    address_family = socket.AF_INET


def build_server(cfg: ServeConfig) -> KeelServer:
    """A server bound to `cfg`, with the address family its host implies.

    **Nested subclasses rather than three-argument `type()`.** Both bind a per-run config onto a
    handler class and an address family onto a server class, and both are correct at runtime --
    but `type(name, bases, namespace)` is declared to return plain `type`, so calling it produced
    an `Any` that flowed straight out of a function annotated `-> KeelServer`. The
    `# type: ignore[return-value]` that used to sit here did not even name the right error: mypy
    reported `no-any-return` and noted the code was not covered, which is a suppression that
    silences nothing while looking like it silences something. Written as classes, the types are
    checked rather than asserted, and `keel/web/` type-checks under full `--strict`.

    A fresh pair per call is deliberate: `address_family` and `cfg` are class attributes that
    `http.server` reads during `__init__`, so binding them to the shared classes would make two
    concurrently-built servers overwrite each other's configuration.
    """
    bound_cfg = cfg

    class BoundKeelHandler(KeelHandler):
        cfg = bound_cfg
        # `StreamRequestHandler.setup` hands this to `settimeout` on every accepted socket (#981).
        timeout = REQUEST_TIMEOUT_SECONDS

    class BoundKeelServer(KeelServer):
        address_family = socket.AF_INET6 if ":" in cfg.host else socket.AF_INET

    return BoundKeelServer((cfg.host, cfg.port), BoundKeelHandler)


def _stop_on_sigterm() -> Any:
    """Make SIGTERM unwind the stack instead of killing the process outright (#760 review).

    `launchctl bootout` -- the documented way to stop a console daemon -- sends SIGTERM, and
    Python's default disposition for it terminates WITHOUT running `finally`. So `serve`'s cleanup
    never fired on the one stop gesture an operator is told to use: the runtime record survived
    every deliberate shutdown, holding a dead token, while the plists, the runbook and
    `security.py` all said the file is deleted on shutdown. Measured before this existed, with a
    probe process carrying the same `try`/`finally`: the marker was still there afterwards.

    Raising `KeyboardInterrupt` rather than inventing an exception, because that is precisely what
    this is -- an operator asking the server to stop -- and `serve` already prints "stopped." for
    it. `server.shutdown()` would be the other candidate and it deadlocks here: it blocks until
    the serve loop exits, and the loop cannot exit while the main thread is inside this handler.

    Returns the previous handler so the caller can put it back; `serve` is a library function as
    well as a command, and leaving a handler installed would change the behaviour of whatever
    called it. `None` when no handler could be installed at all -- `signal.signal` refuses to run
    off the main thread, and a server started from a worker thread is not a reason to fail to
    serve.
    """

    def _raise(_signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt

    try:
        return signal.signal(signal.SIGTERM, _raise)
    except ValueError, OSError, AttributeError:
        return None


def serve(cfg: ServeConfig, *, echo: Callable[[str], None] = print) -> int:
    """Bind, announce, and run until interrupted. Returns a process exit code."""
    try:
        ensure_schema(cfg.db_path)
    except Exception as exc:
        echo(f"could not open the database at {cfg.db_path}: {exc}")
        return 1

    try:
        server = build_server(cfg)
    except OSError as exc:
        echo(f"could not bind {cfg.host}:{cfg.port}: {exc}")
        return 1

    bound_port = server.server_address[1]
    # `replace`, never a hand-listed constructor: the port is the ONE thing binding learns, and
    # every other field must reach the handler as `serve_cmd` resolved it. The constructor this
    # replaced copied seven fields by name and dropped `external_hosts` and `started_at` (#971),
    # so every `--external-host` run refused its own declared Host, never expired a remote
    # session, and read "no declared remote origin" in the release locality check. A field added
    # to `ServeConfig` later is carried here without anyone having to remember this line.
    running = replace(cfg, port=int(bound_port))
    server.RequestHandlerClass.cfg = running  # type: ignore[attr-defined]

    if not running.host_policy.is_loopback:
        echo("")
        echo(f"  WARNING: serving on {running.host}, which is NOT loopback.")
        echo("  Anyone who can reach this address can read your positions, equity and history.")
        echo("  The session token is the only thing in the way, and it travels in cleartext.")
        echo("")
    echo(f"keel is serving its console at:\n\n    {running.url()}\n")
    echo("This link contains a one-time token for this session. Press Ctrl-C to stop.")
    # #634: the browser keeps this session across a restart of its own, which is what makes the
    # installed console usable at all -- and which means closing the browser has stopped being a
    # way to sign out. Said here rather than only in a docstring, because the operator who needs
    # to revoke is the operator reading this terminal, and the gesture is the one they already
    # have: stopping keel invalidates the token, so every browser holding it is out.
    # #756. The sentence an operator is owed depends on which of these two runs this is, and
    # printing the wrong one would be a false safety assurance about a live credential -- the
    # class of thing `payload._session_banner` refuses to do about mode.
    interactive = runtime.stdout_is_interactive()
    recorded = runtime.record_serving(
        host=running.host,
        port=running.port,
        token=running.token,
        interactive=interactive,
        profile=api._profile_name(running.db_path),
    )
    if recorded is None:
        echo("Stopping keel revokes it: the token is new every run and is never written to disk.")
    else:
        echo("Stopping keel revokes it: the token is new every run.")
        echo(
            f"Nothing is reading this output, so the address is also in {recorded} (0600) -- "
            f"run `keel open --port {running.port}` to get it back. It is deleted on shutdown."
        )

    previous_sigterm = _stop_on_sigterm()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        echo("")
        echo("stopped.")
    finally:
        # Put the handler back before anything else in this block: the cleanup below must run
        # under the caller's own signal disposition, not under one this function installed.
        if previous_sigterm is not None:
            try:
                signal.signal(signal.SIGTERM, previous_sigterm)
            except ValueError, OSError:
                pass
        # Before `server_close`, so a record never outlives the port it names by more than the
        # instant between these two lines. A crash skips this entirely, which is what
        # `runtime.live_record`'s pid check is for.
        runtime.forget(running.port)
        server.server_close()
    return 0


def _stderr(message: str) -> None:  # pragma: no cover - trivial
    print(message, file=sys.stderr)
