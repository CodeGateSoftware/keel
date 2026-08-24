"""The loopback HTTP server behind `keel serve` -- routing, one bounded read per response.

Four surfaces, and the split is the whole of this module's job: the rendered HTML pages in
`ROUTES`, the static assets under `staticfiles.STATIC_PREFIX` (#535), the JSON read API under
`API_PREFIX` (#534, routed by `keel/web/api.py`), and the one closed write surface under
`SETUP_ACTION_PREFIX` (#437). Each gets its own header set, because they need different values for
the SAME header rather than merely different extra ones.

**Why the standard library and not a web framework.** This surface is a handful of read-only pages
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

So `do_POST` exists, and it routes ONLY through `keel.commands.setup.ACTIONS`: three idempotent,
non-destructive steps, every one of them declared `MECHANICAL` in the same module's step list.
The guarantee is now that **not one of the eleven capability-increasing actions in
`keel/capabilities.py` is reachable from this package**, asserted by a test that scans this
source rather than by inspection. "No POST" said the server could not write; this says it cannot
arm, release or spend -- which is the property anyone actually cares about.

Attesting, promoting, releasing a halt and arming autonomy remain CLI-only, behind the TTY gate.
D3 (#436) is where a browser gate for those would go, if it goes anywhere.

**Each request opens its own SQLite connection.** A `ThreadingHTTPServer` hands requests to
different threads, and a `sqlite3` connection belongs to the thread that made it. Per-request is
also what the CLI does per invocation, which keeps the page a snapshot of committed state rather
than a long-lived reader that could sit inside someone else's transaction.
"""

from __future__ import annotations

import functools
import json
import socket
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

from keel.web import api, events, render, staticfiles
from keel.web.security import (
    SESSION_COOKIE,
    HostPolicy,
    csrf_token,
    parse_cookie_header,
    tokens_match,
)

#: How often the live pages reload themselves. 15s matches the TUI's own poll: fast enough that
#: "is it running" is answered without a keypress, slow enough that a page being read does not
#: jump away every few seconds.
_REFRESH_SEC = 15

#: Cap on a form body. `rfile.read(n)` with an attacker-supplied `n` is a memory-exhaustion
#: primitive and there is no proxy in front of this server to impose a limit. A setup form carries
#: an action key and a token.
_MAX_FORM_BYTES = 8 * 1024

#: How often the setup page reloads WHILE a background job runs. Shorter than the dashboards'
#: 15s: someone watching a fetch wants to see it moving, and the page is a few kilobytes of local
#: HTML.
_JOB_REFRESH_SEC = 5

#: Journal rows rendered on the insights page. A cap, not a paginator: the page answers "how has
#: this been going", and the full history is what `keel insights journal` is for.
_JOURNAL_LIMIT = 50


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

    @property
    def host_policy(self) -> HostPolicy:
        return HostPolicy(bound_host=self.host, port=self.port)

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


def page_setup(cfg: ServeConfig, query: dict[str, list[str]]) -> tuple[str, str, int | None]:
    from keel.commands import jobs
    from keel.commands.setup import ACTIONS, NOT_AUTOMATED_YET

    job = jobs.status()
    return (
        "Setup",
        render.render_setup(
            api.deployment_state(cfg),
            actions=ACTIONS,
            not_automated=NOT_AUTOMATED_YET,
            csrf=csrf_token(cfg.token),
            ran=(query.get("ran") or [""])[0],
            job=job,
        ),
        # Auto-refresh ONLY while something is running. A finished page that kept reloading would
        # fight a reader, and the zero-JS meta refresh is the only progress mechanism available
        # to a page that ships no scripts.
        _JOB_REFRESH_SEC if job is not None and job.is_running else None,
    )


def needs_database(
    page: Callable[[ServeConfig, dict[str, list[str]]], tuple[str, str, int | None]],
) -> Callable[[ServeConfig, dict[str, list[str]]], tuple[str, str, int | None]]:
    """Serve the checklist instead of building a page that has no database to build from.

    Every page below this reads tables, and `sqlite3.connect` CREATES the file it cannot find --
    so without this a first-run user clicking "Activity" would get a 500 *and* leave an empty
    `keel.db` behind, brought into existence by a read-only view being looked at. Found by
    smoke-testing an empty directory; the unit tests missed it because they only exercised the
    landing page.

    The guard is on the whole set rather than on the landing page alone for the same reason the
    thinness pin globs a directory: a page added later gets the behaviour by construction, not by
    its author remembering."""

    @functools.wraps(page)
    def guarded(cfg: ServeConfig, query: dict[str, list[str]]) -> tuple[str, str, int | None]:
        if not api.deployment_state(cfg).has_usable_database:
            # The full setup page, not a bare checklist: someone who lands here has nothing set
            # up, and the actions are the reason they are being shown this instead of a 500.
            return page_setup(cfg, query)
        return page(cfg, query)

    return guarded


def page_status(cfg: ServeConfig, _query: dict[str, list[str]]) -> tuple[str, str, int | None]:
    from keel.commands.status import gather_status

    repo = api.open_repo(cfg.db_path)
    try:
        config = api.load_config(cfg.config_path)
        report = gather_status(repo, config, now_ts=int(time.time()))
    finally:
        api.close_repo(repo)
    return "Status", render.render_status(report), _REFRESH_SEC


def page_activity(cfg: ServeConfig, query: dict[str, list[str]]) -> tuple[str, str, int | None]:
    from keel.commands.activity import (
        apply_scope,
        feed_from_lines,
        normalise_scope,
        read_log_window,
        resolve_log_path,
    )

    scope = normalise_scope((query.get("scope") or [""])[0])
    config = api.load_config(cfg.config_path)
    path = resolve_log_path(config)
    window = read_log_window(path)
    feed = feed_from_lines(window.lines, source=str(path), truncated=window.truncated)
    if window.status != "ok" and feed.status == "empty":
        # A read that failed and a window that held nothing are different facts; the reader's
        # status is the more specific one and must not be flattened into "empty".
        feed = feed_from_lines((), source=str(path))
    feed = apply_scope(feed, scope, now_ts=time.time())
    body = render.render_activity(feed)
    links = " &middot; ".join(
        (
            f"<strong>{render.esc(name)}</strong>"
            if name == scope
            else f'<a href="/activity?scope={render.esc(name)}">{render.esc(name)}</a>'
        )
        for name in ("today", "7d", "all")
    )
    return "Activity", body + f'<p class="note">scope: {links}</p>', _REFRESH_SEC


def page_insights(cfg: ServeConfig, _query: dict[str, list[str]]) -> tuple[str, str, int | None]:
    from keel.commands.insights import build_insights_report, build_journal_report
    from keel.commands.status import gather_status

    repo = api.open_repo(cfg.db_path)
    try:
        config = api.load_config(cfg.config_path)
        now_ts = int(time.time())
        status_report = gather_status(repo, config, now_ts=now_ts)
        insights = build_insights_report(repo, config, status_report, now_ts)
        journal = build_journal_report(repo, status_report, now_ts, limit=_JOURNAL_LIMIT)
    finally:
        api.close_repo(repo)
    return "Insights", render.render_insights(insights, journal), None


def page_rules(cfg: ServeConfig, _query: dict[str, list[str]]) -> tuple[str, str, int | None]:
    repo = api.open_repo(cfg.db_path)
    try:
        rows = repo.get_rules(None)
    finally:
        api.close_repo(repo)
    return "Rules", render.render_rules(rows), None


def page_venues(_cfg: ServeConfig, _query: dict[str, list[str]]) -> tuple[str, str, int | None]:
    from keel.commands.brokers import list_installed_brokers

    return "Venues", render.render_venues(list_installed_brokers()), None


def page_gates(_cfg: ServeConfig, _query: dict[str, list[str]]) -> tuple[str, str, int | None]:
    """Read from `keel.capabilities`, which is a pure declaration -- no config, no database, no
    network. It describes the binary that is serving the page."""
    from keel.capabilities import CAPABILITIES, GATES

    return "Gates", render.render_gates(GATES, CAPABILITIES), None


def page_glossary(_cfg: ServeConfig, _query: dict[str, list[str]]) -> tuple[str, str, int | None]:
    from keel.commands.help_console import load_glossary

    return "Glossary", render.render_glossary(load_glossary()), None


ROUTES: dict[str, Callable[[ServeConfig, dict[str, list[str]]], tuple[str, str, int | None]]] = {
    # First-run detection (#437): every page that reads the database serves the checklist when
    # there is no database to read, rather than a 500 whose real cause is that the user has not
    # set anything up yet. `/venues`, `/gates` and `/glossary` are not wrapped -- none of them
    # touches the deployment, and all three are useful before one exists.
    "/": needs_database(page_status),
    "/setup": page_setup,
    "/activity": needs_database(page_activity),
    "/insights": needs_database(page_insights),
    "/rules": needs_database(page_rules),
    "/venues": page_venues,
    "/gates": page_gates,
    "/glossary": page_glossary,
}


#: The write surface, in full, today. A path here maps to one `keel.commands.setup.Action`; there
#: is no other way into this handler, and no other verb.
SETUP_ACTION_PREFIX = "/setup/"

#: The JSON API (#534). `GET` under this prefix routes through `keel/web/api.py`'s own table --
#: reads only, one bounded read per endpoint. `POST` under it is unchanged from #535: it clears
#: `_api_client_header_ok` (the third CSRF layer, scoped to this prefix specifically -- see that
#: method's docstring for why it must NOT also gate `SETUP_ACTION_PREFIX`) and then meets the same
#: 404 every unmapped path gets, because there is still no JSON write surface and this issue added
#: none.
API_PREFIX = "/api/"

#: The one path under `API_PREFIX` that is a STREAM rather than a document (#537).
#:
#: It is not in `api.API_ROUTES` because it does not have that table's shape: every entry there
#: maps to `(status, document)` through `api.respond`, and an `EventSource` connection is a
#: response that never finishes. Bolting a "this one streams" flag onto `ApiRoute` would have put
#: a branch into the one function whose uniformity is the reason #536's `fetch` wrapper needs no
#: per-endpoint branch of its own.
#:
#: It is still a GET, still behind `_admitted()`, and still answers no POST -- `do_POST` refuses
#: everything under `API_PREFIX` before it ever looks at a path.
EVENTS_PATH = "/api/events"


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

#: Sent on every response, success or refusal.
#:
#: `default-src 'none'` with only `style-src 'unsafe-inline'` added states exactly what the page
#: is: markup and one inline stylesheet. No scripts, no images, no fonts, no connections. If a
#: future change smuggles in a script tag, the browser refuses it and the omission is visible
#: rather than silent. `frame-ancestors 'none'` (and the legacy `X-Frame-Options`) keep the page
#: out of an iframe on a hostile origin, which is the other half of the DNS-rebinding defence.
_SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    (
        "Content-Security-Policy",
        # `form-action 'self'`, not `'none'`: the setup form posts back here, and `'none'`
        # would have the browser silently refuse it. `'self'` is still the tightest value that
        # works -- a form on this page cannot be made to submit anywhere else, which is what the
        # directive is for.
        "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; "
        "form-action 'self'; base-uri 'none'",
    ),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    ("Cache-Control", "no-store, max-age=0"),
)


#: The header set for `/static/*` (#535), separate from `_SECURITY_HEADERS` above because the
#: two routes need different values for the SAME header, not merely an additional one.
#: `_SECURITY_HEADERS`'s `default-src 'none'` is correct for the rendered pages -- they ship no
#: script, no style file, no image, nothing to permit -- but #536's client is exactly the thing
#: `'none'` forbids: its own JS, its own CSS, its own icons, all same-origin. `'self'` is the
#: tightest policy that still allows that, and `connect-src 'self'` on top of it is the specific
#: guarantee the design spec asks for: the interface is provably incapable of sending positions,
#: equity or trade history anywhere but this local process, checkable in the response headers
#: rather than merely promised.
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
#: half `_SECURITY_HEADERS` above already closes for the rendered pages
#: (`frame-ancestors 'none'` there is called "the other half of the DNS-rebinding defence"; this
#: is the same half, for the route that will host all of #536's JavaScript).
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
    """GET and HEAD. No other verb is implemented, so no other verb reaches keel."""

    server_version = "keel"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    #: Set by `build_server`.
    cfg: ServeConfig

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
        content_type: str = "text/html; charset=utf-8",
        extra: tuple[tuple[str, str], ...] = (),
    ) -> None:
        payload = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        for name, value in _SECURITY_HEADERS:
            self.send_header(name, value)
        for name, value in extra:
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _send_json(self, code: int, document: dict[str, Any]) -> None:
        """One JSON response, with its own headers.

        Writes them itself rather than going through `_send` for the same reason `_serve_static`
        does: `_send` puts `_SECURITY_HEADERS` on every response, and one of those is a CSP that
        has no meaning on `application/json` (see `_API_HEADERS`). Sharing the method would have
        meant a parameter with a default, and a default on a shared sender is how a header set
        silently changes for a route nobody was thinking about.

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

    def _refuse(self, code: int, heading: str, detail: str) -> None:
        """A refusal, in the media type the caller asked for by the path it used.

        **Path-scoped, not method-scoped, and not content-negotiated.** An HTML error page handed
        to a `fetch()` client's `res.json()` is a parse error in the client, which is a strictly
        worse diagnostic than the 403 it is hiding -- so everything under `API_PREFIX` refuses in
        JSON, including the POST that still 404s there. The GATE in front of that POST
        (`_api_client_header_ok`) is untouched by this: same trigger, same status, same ordering;
        only the body's media type follows the path.

        `Accept`-based negotiation was the alternative and it is worse here: a client that forgets
        the header would get HTML from a JSON endpoint, and the one thing this server can be
        certain about is which path was requested.
        """
        if urlsplit(self.path).path.startswith(API_PREFIX):
            self._send_json(code, api.refusal_document(code, heading, detail))
            return
        self._send(
            code,
            render.page(
                title=heading,
                path="",
                body=render.render_message(heading, detail),
                build=self.cfg.build,
            ),
        )

    # -- shared admission --
    def _admitted(self) -> bool:
        """Host header, then session cookie. `False` means a refusal has already been sent.

        Factored out so GET and POST cannot drift into two admission policies -- a write path
        with a laxer check than the read path is exactly the shape of a bug nobody notices."""
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
        cookies = parse_cookie_header(self.headers.get("Cookie"))
        if not tokens_match(cookies.get(SESSION_COOKIE), self.cfg.token):
            self._refuse(
                403,
                "Not authorised",
                "Open the address keel printed when it started -- it carries a one-time token "
                "for this session. The token is new every run and is never written to disk.",
            )
            return False
        return True

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
        """The third CSRF layer (#535), and scoped to `API_PREFIX` ONLY -- never to
        `SETUP_ACTION_PREFIX`. This was gated at the top of `do_POST`, over every write, in an
        earlier version of this change, and that was a defect, not a stricter check: the shipped
        UI's entire write surface IS a plain HTML `<form method=post action="/setup/...">`
        (`render.py`'s `_action_form`), and `_SECURITY_HEADERS` ships no `script-src` at all --
        there is no code path by which that form can set a custom request header. Gating
        `/setup/*` on it would have refused every legitimate submission the shipped client can
        make, with no fallback: the desktop bundle has no terminal.
        `test_a_browser_form_post_succeeds_without_the_api_client_header` pins that this route
        stays reachable from exactly what ships.

        `X-Keel-Client: 1` is a real defence where it CAN apply: a custom header forces a CORS
        preflight a hostile origin cannot satisfy, closing the one gap `SameSite=Strict` and the
        HMAC CSRF token both assume shut -- a plain form POST, which is never preflighted, in any
        browser. But "a custom header" and "a `fetch()` client" are the same requirement, and
        `/setup/*` has no `fetch()` client today. `API_PREFIX` is reserved for #533/#534's JSON
        API, which #536's client speaks over `fetch()` -- that is where this check belongs, and
        widening it onto `/setup/*` is #536's call to make, once (and only once) the forms it
        replaces are gone."""
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
        except (BrokenPipeError, ConnectionResetError):
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
        """The ENTIRE write surface (#437). Read `keel/web/__init__.py` before extending it.

        Admission, then `Sec-Fetch-Site` (both apply to every POST), then a PATH-SCOPED branch:
        `API_PREFIX` additionally requires `X-Keel-Client` before falling through to the 404 no
        route there answers yet; `SETUP_ACTION_PREFIX` does not, and reads its own CSRF token
        instead (see `_api_client_header_ok`'s docstring for why the two paths differ -- it is
        not an oversight, it is the fix for one). Either way there is no dynamic dispatch here,
        no getattr on a user-supplied name, and no path that reaches keel other than
        `keel.commands.setup.ACTIONS` -- which contains three idempotent, non-destructive,
        `MECHANICAL` steps and cannot contain anything else without failing a test."""
        parsed = urlsplit(self.path)
        if not self._admitted():
            return

        if not self._sec_fetch_site_ok():
            return

        if parsed.path.startswith(API_PREFIX):
            if not self._api_client_header_ok():
                return
            # No JSON API write surface exists yet (#533/#534 land the reads; a write is
            # further out still) -- this is a 404 like any other unmapped path, not a stub
            # success. Checked here, ahead of that surface existing, so its first action does
            # not have to remember to add the gate.
            self._refuse(404, "No such action", f"Nothing accepts a POST at {parsed.path}.")
            return

        if not parsed.path.startswith(SETUP_ACTION_PREFIX):
            # Not "method not allowed" -- there is no write surface at this path at all, and
            # saying so is both true and less informative to someone probing.
            self._refuse(404, "No such action", f"Nothing accepts a POST at {parsed.path}.")
            return

        body = self._read_form()
        if not tokens_match(body.get("csrf"), csrf_token(self.cfg.token)):
            # `SameSite=Strict` already stops a cross-site POST in any current browser. This is
            # the layer that does not depend on the browser being current.
            self._refuse(
                403,
                "Refused",
                "That form did not carry this session's write token. Reload the page and try "
                "again.",
            )
            return

        key = parsed.path[len(SETUP_ACTION_PREFIX) :]
        try:
            result = run_setup_action(self.cfg, key, body)
        except Exception as exc:
            self._refuse(500, "That step could not be completed", f"{type(exc).__name__}: {exc}")
            return
        if result is None:
            self._refuse(404, "No such action", f"{key!r} is not a setup step keel performs.")
            return

        # POST/redirect/GET: a browser reload must not re-submit. The actions are idempotent, so
        # a re-submission would be harmless -- but "harmless" is not a reason to leave a
        # re-submitting page in a setup flow someone is clicking nervously.
        #
        # The Location carries the step KEY and nothing else. A submitted value in a redirect URL
        # is a secret in browser history, in the Referer header of anything the page later loads,
        # and in any proxy log between here and nowhere -- which is the whole reason the form is a
        # POST in the first place.
        self._send(303, "", extra=(("Location", f"/setup?ran={quote(result.step_key)}"),))

    def _read_form(self) -> dict[str, str]:
        """The urlencoded body, bounded.

        Bounded because `rfile.read(n)` with an attacker-supplied `n` is a memory-exhaustion
        primitive, and this server has no proxy in front of it to impose a limit. A setup form
        carries an action key and a token; anything past a few kilobytes is not one."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if length <= 0 or length > _MAX_FORM_BYTES:
            return {}
        raw = self.rfile.read(length).decode("utf-8", "replace")
        return {key: values[0] for key, values in parse_qs(raw, keep_blank_values=True).items()}

    def do_GET(self) -> None:  # noqa: N802 - stdlib's naming, not ours
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
                    (
                        "Set-Cookie",
                        f"{SESSION_COOKIE}={self.cfg.token}; Path=/; HttpOnly; SameSite=Strict",
                    ),
                ),
            )
            return

        if not self._admitted():
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
            code, document = api.respond(self.cfg, parsed.path, query)
            self._send_json(code, document)
            return

        if parsed.path.startswith(staticfiles.STATIC_PREFIX):
            # Same admission as every rendered page (never weakened): a static asset is not
            # exempted from the loopback-plus-session model just because it holds no secrets
            # today. #536's client is what actually reads these, and it authenticates the same
            # way any other fetch from this origin does -- the session cookie already on the
            # request.
            self._serve_static(parsed.path)
            return

        handler = ROUTES.get(parsed.path)
        if handler is None:
            self._refuse(404, "No such page", f"Nothing is served at {parsed.path}.")
            return

        try:
            title, body, refresh = handler(self.cfg, query)
        except Exception as exc:  # a broken page must not take the server down
            self._refuse(
                500,
                "That page could not be built",
                f"{type(exc).__name__}: {exc}",
            )
            return

        self._send(
            200,
            render.page(
                title=title,
                path=parsed.path,
                body=body,
                build=self.cfg.build,
                refresh_sec=refresh,
            ),
        )


class KeelServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    #: IPv6 needs the family set before `server_bind`; `::1` is a legitimate loopback address and
    #: a `--host ::1` that silently bound IPv4 would be a lie.
    address_family = socket.AF_INET


def build_server(cfg: ServeConfig) -> KeelServer:
    handler = type("BoundKeelHandler", (KeelHandler,), {"cfg": cfg})
    family = socket.AF_INET6 if ":" in cfg.host else socket.AF_INET
    server_type = type("BoundKeelServer", (KeelServer,), {"address_family": family})
    return server_type((cfg.host, cfg.port), handler)  # type: ignore[return-value]


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
    running = ServeConfig(
        host=cfg.host,
        port=int(bound_port),
        token=cfg.token,
        db_path=cfg.db_path,
        config_path=cfg.config_path,
        build=cfg.build,
        build_info=cfg.build_info,
    )
    server.RequestHandlerClass.cfg = running  # type: ignore[attr-defined]

    if not running.host_policy.is_loopback:
        echo("")
        echo(f"  WARNING: serving on {running.host}, which is NOT loopback.")
        echo("  Anyone who can reach this address can read your positions, equity and history.")
        echo("  The session token is the only thing in the way, and it travels in cleartext.")
        echo("")
    echo(f"keel is serving a read-only view at:\n\n    {running.url()}\n")
    echo("This link contains a one-time token for this session. Press Ctrl-C to stop.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        echo("")
        echo("stopped.")
    finally:
        server.server_close()
    return 0


def _stderr(message: str) -> None:  # pragma: no cover - trivial
    print(message, file=sys.stderr)
