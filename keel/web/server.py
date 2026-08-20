"""The loopback HTTP server behind `keel serve` -- routing, one bounded read per page, no writes.

**Why the standard library and not a web framework.** This surface is six read-only pages served
to one person on one machine. FastAPI/uvicorn would bring a dependency subtree (pydantic,
starlette, anyio, h11, ...) into a wheel that today depends on `click` and its own workspace
siblings -- and D5 has to freeze that tree into a signed, notarised app bundle, where every
dynamic import is a hook to write and every megabyte is download the user waits through. It would
also enlarge the supply-chain surface of a project whose proposition is auditability, to buy
routing for six paths and a templating engine used zero times. `http.server` is the smaller,
more honest answer here, and it is genuinely the wrong answer the moment this serves more than
one local user -- at which point the framework, not this module, is the thing to reach for.

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
import socket
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

from keel.web import render
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
    build: str = ""

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


def _open_repo(db_path: str) -> Any:
    """A plain connection -- deliberately WITHOUT `migrate`.

    Every CLI command migrates on the way in, which is right for a command: it runs once, and a
    schema behind the code is a thing to fix rather than to fail on. It is wrong here. These
    pages auto-reload every 15 seconds, so migrating per request would have a view that calls
    itself read-only take a write lock on the deployment database four times a minute -- against
    a database the agent may be mid-cycle on. `ensure_schema` does it ONCE, at bind time, before
    anything is served."""
    from keel.data.db import connect
    from keel.data.repository import Repository

    return Repository(connect(db_path))


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


def _load_config(config_path: str) -> Any:
    """`load_config` only -- deliberately NOT `_common._load_cfg`, which also calls
    `configure_logging` and `bind_venue`. Those are process-entry side effects; re-applying them
    on every page load would have the web UI quietly reconfiguring the running deployment's
    logging."""
    from keel.config import load_config

    return load_config(config_path)


def _deployment_state(cfg: ServeConfig) -> Any:
    from keel.commands.setup import inspect

    return inspect(cfg.config_path, cfg.db_path)


def page_setup(cfg: ServeConfig, query: dict[str, list[str]]) -> tuple[str, str, int | None]:
    from keel.commands import jobs
    from keel.commands.setup import ACTIONS, NOT_AUTOMATED_YET

    job = jobs.status()
    return (
        "Setup",
        render.render_setup(
            _deployment_state(cfg),
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
        if not _deployment_state(cfg).has_usable_database:
            # The full setup page, not a bare checklist: someone who lands here has nothing set
            # up, and the actions are the reason they are being shown this instead of a 500.
            return page_setup(cfg, query)
        return page(cfg, query)

    return guarded


def page_status(cfg: ServeConfig, _query: dict[str, list[str]]) -> tuple[str, str, int | None]:
    from keel.commands.status import gather_status

    repo = _open_repo(cfg.db_path)
    try:
        config = _load_config(cfg.config_path)
        report = gather_status(repo, config, now_ts=int(time.time()))
    finally:
        _close(repo)
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
    config = _load_config(cfg.config_path)
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

    repo = _open_repo(cfg.db_path)
    try:
        config = _load_config(cfg.config_path)
        now_ts = int(time.time())
        status_report = gather_status(repo, config, now_ts=now_ts)
        insights = build_insights_report(repo, config, status_report, now_ts)
        journal = build_journal_report(repo, status_report, now_ts, limit=_JOURNAL_LIMIT)
    finally:
        _close(repo)
    return "Insights", render.render_insights(insights, journal), None


def page_rules(cfg: ServeConfig, _query: dict[str, list[str]]) -> tuple[str, str, int | None]:
    repo = _open_repo(cfg.db_path)
    try:
        rows = repo.get_rules(None)
    finally:
        _close(repo)
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


#: The write surface, in full. A path here maps to one `keel.commands.setup.Action`; there is no
#: other way into this handler, and no other verb.
SETUP_ACTION_PREFIX = "/setup/"


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


def _close(repo: Any) -> None:
    conn = getattr(repo, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:  # pragma: no cover - a close that fails leaks nothing that matters
            pass


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

    def _refuse(self, code: int, heading: str, detail: str) -> None:
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

    # -- the request --
    def do_HEAD(self) -> None:  # noqa: N802 - stdlib's naming, not ours
        self.do_GET()

    def do_POST(self) -> None:  # noqa: N802 - stdlib's naming, not ours
        """The ENTIRE write surface (#437). Read `keel/web/__init__.py` before extending it.

        Four refusals before anything is performed, and then a lookup in a closed set. There is
        no dynamic dispatch here, no getattr on a user-supplied name, and no path that reaches
        keel other than `keel.commands.setup.ACTIONS` -- which contains three idempotent,
        non-destructive, `MECHANICAL` steps and cannot contain anything else without failing a
        test."""
        parsed = urlsplit(self.path)
        if not self._admitted():
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
