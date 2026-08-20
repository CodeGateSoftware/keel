"""End-to-end over a real bound server (#435, D2).

These drive an actual `ThreadingHTTPServer` on an ephemeral port with a real database and a real
config, because the properties worth pinning here -- that a write verb never reaches keel, that
the token is required, that the token never appears in a log line -- are properties of the wire,
and a test against a hand-built handler object could pass while the served bytes said otherwise.
"""

from __future__ import annotations

import http.client
import threading
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlencode

import pytest

from keel.data.db import connect, migrate
from keel.web import server as web_server
from keel.web.security import SESSION_COOKIE, new_session_token
from tests.conftest import VALID_CONFIG_YAML

ROUTES = (
    "/",
    "/setup",
    "/activity",
    "/insights",
    "/rules",
    "/venues",
    "/gates",
    "/glossary",
)


@pytest.fixture
def deployment(tmp_path: Path) -> tuple[str, str]:
    db_path = tmp_path / "keel.db"
    conn = connect(str(db_path))
    migrate(conn)
    conn.close()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(VALID_CONFIG_YAML)
    return str(db_path), str(config_path)


@pytest.fixture
def running(deployment: tuple[str, str]) -> Iterator[web_server.ServeConfig]:
    db_path, config_path = deployment
    cfg = web_server.ServeConfig(
        host="127.0.0.1",
        port=0,
        token=new_session_token(),
        db_path=db_path,
        config_path=config_path,
    )
    server = web_server.build_server(cfg)
    bound = web_server.ServeConfig(
        host=cfg.host,
        port=int(server.server_address[1]),
        token=cfg.token,
        db_path=db_path,
        config_path=config_path,
    )
    server.RequestHandlerClass.cfg = bound  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield bound
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _request(
    cfg: web_server.ServeConfig,
    path: str,
    *,
    method: str = "GET",
    cookie: str | None = None,
    host: str | None = None,
    form: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], str]:
    conn = http.client.HTTPConnection(cfg.host, cfg.port, timeout=10)
    headers = {"Host": host if host is not None else f"{cfg.host}:{cfg.port}"}
    if cookie:
        headers["Cookie"] = cookie
    body = None
    if form is not None:
        body = urlencode(form)
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        headers["Content-Length"] = str(len(body))
    try:
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        body = response.read().decode("utf-8", "replace")
        return response.status, dict(response.getheaders()), body
    finally:
        conn.close()


def _session(cfg: web_server.ServeConfig) -> str:
    return f"{SESSION_COOKIE}={cfg.token}"


def _csrf(cfg: web_server.ServeConfig) -> str:
    from keel.web.security import csrf_token

    return csrf_token(cfg.token)


# -- the read-only guarantee ---------------------------------------------------------------


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
def test_no_verb_beyond_get_head_and_post_is_answered(
    running: web_server.ServeConfig, method: str
) -> None:
    """Everything outside the three implemented verbs dies in the stdlib, before any keel code
    or any authentication runs."""
    status, _headers, _body = _request(running, "/", method=method, cookie=_session(running))
    assert status == 501


def test_the_handler_declares_exactly_three_verbs() -> None:
    """Read off the class, so a `do_DELETE` added anywhere in the hierarchy fails here even if
    no test exercised it."""
    verbs = {
        name
        for klass in web_server.KeelHandler.__mro__
        for name in vars(klass)
        if name.startswith("do_")
    }
    assert verbs == {"do_GET", "do_HEAD", "do_POST"}


def test_post_is_refused_everywhere_except_the_setup_actions(
    running: web_server.ServeConfig,
) -> None:
    """The write surface is one prefix. A POST anywhere else is not "method not allowed" -- there
    is no write surface at that path at all."""
    for path in ("/", "/insights", "/gates", "/setup"):
        status, _headers, _body = _request(
            running, path, method="POST", cookie=_session(running), form={"csrf": _csrf(running)}
        )
        assert status == 404, path


# -- the guarantee that replaced "no POST at all" ---------------------------------------------


def test_the_write_surface_is_exactly_the_mechanical_steps() -> None:
    """`ACTIONS` is the whole write surface, and every member must be a step declared
    MECHANICAL. A judgement step is the operator's -- a wizard may record one but must never
    decide it -- and an off-venue step happens where keel cannot reach. This is what stops a
    button for "attest this asset" from being added as markup."""
    from keel.commands.setup import ACTIONS, STEPS, StepKind

    mechanical = {step.key for step in STEPS if step.kind is StepKind.MECHANICAL}
    declared = {action.key for action in ACTIONS}
    assert declared <= mechanical, sorted(declared - mechanical)
    assert declared, "an empty write surface would make every test below vacuous"

    for step in STEPS:
        if step.kind is not StepKind.MECHANICAL:
            assert step.key not in declared, step.key


def test_no_capability_increasing_action_is_reachable_from_the_web_layer() -> None:
    """THE safety property, and the reason this is a better guarantee than "no POST at all".

    "No POST" said the server could not write -- and was also satisfied by a server that could
    not set anything up, which is the problem #437 exists to solve. This says the server cannot
    ARM, RELEASE or SPEND anything: not one of the eleven capability-increasing actions in
    `keel/capabilities.py` is named anywhere under `keel/web/`, nor is the TTY gate they all pass
    through.

    Only possible because #453 landed the inventory; before that this test would have been a
    hand-written list going stale."""
    import ast
    import glob
    import os

    from keel.capabilities import CAPABILITIES

    forbidden_functions = {cap.function for cap in CAPABILITIES} | {
        "_require_interactive_confirmation"
    }
    forbidden_modules = {cap.module for cap in CAPABILITIES}

    web_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "keel",
        "web",
    )
    sources = sorted(glob.glob(os.path.join(web_dir, "*.py")))
    assert sources, "the scan found no web modules, which would make this vacuous"

    offences: list[str] = []
    for path in sources:
        tree = ast.parse(open(path, encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in forbidden_modules:
                for alias in node.names:
                    if alias.name in forbidden_functions:
                        offences.append(f"{os.path.basename(path)} imports {alias.name}")
            name = None
            if isinstance(node, ast.Call):
                callee = node.func
                name = (
                    callee.id
                    if isinstance(callee, ast.Name)
                    else callee.attr
                    if isinstance(callee, ast.Attribute)
                    else None
                )
            if name in forbidden_functions:
                offences.append(f"{os.path.basename(path)} calls {name}")
    assert not offences, "the web layer can reach a capability-increasing action: " + "; ".join(
        offences
    )


def test_the_scan_for_capability_increasing_actions_can_fail() -> None:
    """An AST scan that silently matched nothing would make the test above vacuously green."""
    import ast

    from keel.capabilities import CAPABILITIES

    victim = next(cap for cap in CAPABILITIES)
    tree = ast.parse(f"def sneaky():\n    {victim.function}()\n")
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert victim.function in called


# -- CSRF -------------------------------------------------------------------------------------


def test_a_write_without_the_session_cookie_is_refused(
    empty_machine: web_server.ServeConfig,
) -> None:
    """Admission is shared with GET, deliberately: a write path with a laxer check than the read
    path is exactly the shape of a bug nobody notices."""
    status, _headers, _body = _request(
        empty_machine, "/setup/config", method="POST", form={"csrf": _csrf(empty_machine)}
    )
    assert status == 403
    assert not Path(empty_machine.config_path).exists()


def test_a_write_without_the_csrf_token_is_refused(
    empty_machine: web_server.ServeConfig,
) -> None:
    """`SameSite=Strict` already stops a cross-site POST in any current browser. This is the
    layer that does not depend on the browser being current."""
    for form in ({}, {"csrf": ""}, {"csrf": "not-the-token"}):
        status, _headers, _body = _request(
            empty_machine,
            "/setup/config",
            method="POST",
            cookie=_session(empty_machine),
            form=form,
        )
        assert status == 403, form
    assert not Path(empty_machine.config_path).exists()


def test_a_write_from_a_rebound_hostname_is_refused(
    empty_machine: web_server.ServeConfig,
) -> None:
    status, _headers, _body = _request(
        empty_machine,
        "/setup/config",
        method="POST",
        cookie=_session(empty_machine),
        form={"csrf": _csrf(empty_machine)},
        host=f"evil.example:{empty_machine.port}",
    )
    assert status == 403
    assert not Path(empty_machine.config_path).exists()


def test_the_csrf_token_is_not_the_session_token(
    running: web_server.ServeConfig,
) -> None:
    """The session token is `HttpOnly` and must never be written into the page; the CSRF token
    is. They must therefore be different values, and the derivation must not be reversible."""
    from keel.web.security import csrf_token

    assert csrf_token(running.token) != running.token
    _status, _headers, body = _request(running, "/setup", cookie=_session(running))
    assert running.token not in body
    assert csrf_token(running.token) in body


# -- the actions themselves, over the wire ------------------------------------------------------


def test_a_first_run_user_can_build_a_paper_deployment_from_the_browser(
    empty_machine: web_server.ServeConfig,
) -> None:
    """#437's acceptance, as far as this PR takes it: config, database and rule library with no
    command typed. Market data and every judgement step remain outstanding, by design."""
    from keel.commands.setup import ACTIONS, inspect

    for action in ACTIONS:
        status, headers, _body = _request(
            empty_machine,
            f"/setup/{action.key}",
            method="POST",
            cookie=_session(empty_machine),
            form={"csrf": _csrf(empty_machine)},
        )
        assert status == 303, action.key
        assert headers["Location"] == f"/setup?ran={action.key}"

    state = inspect(empty_machine.config_path, empty_machine.db_path)
    done = {item.step.key: item.done for item in state.states}
    assert done["config"] is True
    assert done["database"] is True
    assert done["rules"] is True
    # Still nothing that can trade: candidates only, and every judgement step outstanding.
    assert done["rule_promoted"] is False
    assert done["assets_attested"] is False


def test_running_every_action_twice_changes_nothing_the_second_time(
    empty_machine: web_server.ServeConfig,
) -> None:
    """A setup flow is something a nervous user clicks twice, and a browser reload re-submits.
    The redirect stops the reload; idempotence stops everything else."""
    from keel.commands.setup import ACTIONS

    for _pass in range(2):
        for action in ACTIONS:
            status, _headers, _body = _request(
                empty_machine,
                f"/setup/{action.key}",
                method="POST",
                cookie=_session(empty_machine),
                form={"csrf": _csrf(empty_machine)},
            )
            assert status == 303

    config_text = Path(empty_machine.config_path).read_text()
    assert "auto_trade" in config_text
    from keel.commands.setup import inspect

    item = next(
        s
        for s in inspect(empty_machine.config_path, empty_machine.db_path).states
        if s.step.key == "rules"
    )
    assert item.done is True


def test_an_undeclared_action_key_is_a_404_not_a_lookup_that_falls_through(
    empty_machine: web_server.ServeConfig,
) -> None:
    for key in ("autonomy", "resume", "reset-hwm", "../../etc/passwd", ""):
        status, _headers, _body = _request(
            empty_machine,
            f"/setup/{key}",
            method="POST",
            cookie=_session(empty_machine),
            form={"csrf": _csrf(empty_machine)},
        )
        assert status == 404, key


def test_an_oversized_form_body_is_refused_without_being_read(
    empty_machine: web_server.ServeConfig,
) -> None:
    """`rfile.read(n)` with an attacker-supplied `n` is a memory-exhaustion primitive, and there
    is no proxy in front of this server to impose a limit."""
    status, _headers, _body = _request(
        empty_machine,
        "/setup/config",
        method="POST",
        cookie=_session(empty_machine),
        form={"csrf": _csrf(empty_machine), "padding": "x" * 32_000},
    )
    assert status == 403  # the body was discarded, so the csrf field never arrived
    assert not Path(empty_machine.config_path).exists()


# -- the token -----------------------------------------------------------------------------


def test_without_a_token_every_page_is_refused(running: web_server.ServeConfig) -> None:
    """Any other process running as this user can reach loopback. The token is what stops it."""
    for path in ROUTES:
        status, _headers, body = _request(running, path)
        assert status == 403, path
        assert "Not authorised" in body


def test_the_token_is_exchanged_for_a_strict_cookie_and_leaves_the_url(
    running: web_server.ServeConfig,
) -> None:
    """`SameSite=Strict`, not `Lax`: `Lax` attaches the cookie to top-level navigations, so a
    link on a hostile page would arrive authenticated. And the redirect drops the token from the
    URL, so it stops appearing in history, bookmarks and anything the user pastes for help."""
    status, headers, _body = _request(running, f"/?token={running.token}")
    assert status == 303
    assert headers["Location"] == "/"
    assert "token" not in headers["Location"]
    cookie = headers["Set-Cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE}={running.token}")
    assert "SameSite=Strict" in cookie
    assert "HttpOnly" in cookie


def test_a_wrong_token_is_refused(running: web_server.ServeConfig) -> None:
    status, _headers, _body = _request(running, "/?token=not-the-token")
    assert status == 403
    status, _headers, _body = _request(running, "/", cookie=f"{SESSION_COOKIE}=not-the-token")
    assert status == 403


# -- the host check ------------------------------------------------------------------------


def test_a_rebound_hostname_is_refused_even_though_it_reached_loopback(
    running: web_server.ServeConfig,
) -> None:
    """The request below really did arrive over loopback -- that is how DNS rebinding works.
    Only the `Host:` header shows that the browser thinks it is talking to someone else."""
    status, _headers, body = _request(
        running, "/", cookie=_session(running), host=f"evil.example:{running.port}"
    )
    assert status == 403
    assert "Refused" in body


def test_the_host_check_runs_before_the_token_check(running: web_server.ServeConfig) -> None:
    """Ordering matters: a rebinding attempt must not be able to probe token validity by
    watching which refusal it gets."""
    status, _headers, body = _request(
        running, f"/?token={running.token}", host=f"evil.example:{running.port}"
    )
    assert status == 403
    assert "Refused" in body


# -- the pages -----------------------------------------------------------------------------


@pytest.mark.parametrize("path", ROUTES)
def test_every_route_renders_on_an_empty_deployment(
    running: web_server.ServeConfig, path: str
) -> None:
    """A fresh install is the state a first-run user is in, and it is the state most likely to
    render as a stack trace: no trades, no log file, no market data. Every page must be a page."""
    status, headers, body = _request(running, path, cookie=_session(running))
    assert status == 200, (path, body[:400])
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert "<title>" in body
    assert "Traceback" not in body


@pytest.mark.parametrize("path", ROUTES)
def test_every_response_carries_the_security_headers(
    running: web_server.ServeConfig, path: str
) -> None:
    status, headers, _body = _request(running, path, cookie=_session(running))
    assert status == 200
    csp = headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "script-src" not in csp  # no scripts are allowed at all, so none is named
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert "no-store" in headers["Cache-Control"]


def test_the_page_contains_no_script_tag(running: web_server.ServeConfig) -> None:
    """The CSP forbids scripts; this asserts we never ship one to be forbidden. A page with no
    JavaScript is a page whose whole behaviour is readable in its source."""
    for path in ROUTES:
        _status, _headers, body = _request(running, path, cookie=_session(running))
        assert "<script" not in body.lower(), path


def test_an_unknown_path_is_a_page_not_a_stack_trace(
    running: web_server.ServeConfig,
) -> None:
    status, _headers, body = _request(running, "/nope", cookie=_session(running))
    assert status == 404
    assert "No such page" in body


def test_a_broken_page_does_not_take_the_server_down(
    running: web_server.ServeConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One page failing is a 500 on that page; the operator can still reach the others. A server
    that dies on a bad read would take the whole view away at exactly the moment it is wanted."""

    def _explode(*_args: object, **_kwargs: object) -> tuple[str, str, int | None]:
        raise RuntimeError("no rules table")

    monkeypatch.setitem(web_server.ROUTES, "/rules", _explode)
    status, _headers, body = _request(running, "/rules", cookie=_session(running))
    assert status == 500
    assert "RuntimeError" in body

    status, _headers, _body = _request(running, "/", cookie=_session(running))
    assert status == 200


def test_head_returns_the_headers_without_a_body(running: web_server.ServeConfig) -> None:
    status, headers, body = _request(running, "/", method="HEAD", cookie=_session(running))
    assert status == 200
    assert body == ""
    assert int(headers["Content-Length"]) > 0


def test_the_activity_scope_comes_from_the_query_and_hostile_input_collapses_safely(
    running: web_server.ServeConfig,
) -> None:
    """`normalise_scope` already refuses to produce an empty screen for an unrecognised scope;
    this pins that the web layer routes through it rather than filtering on raw input."""
    status, _headers, body = _request(running, "/activity?scope=7d", cookie=_session(running))
    assert status == 200
    assert "7d" in body

    status, _headers, body = _request(
        running,
        "/activity?scope=%3Cscript%3Ealert(1)%3C/script%3E",
        cookie=_session(running),
    )
    assert status == 200
    assert "<script" not in body.lower()


# -- the token must not leak -----------------------------------------------------------------


def test_the_server_never_logs_the_request_line(
    running: web_server.ServeConfig, capsys: pytest.CaptureFixture[str]
) -> None:
    """The default `BaseHTTPRequestHandler.log_message` writes the raw request line to stderr --
    and the raw request line carries `?token=...` on the very first load. A server that prints
    its own session token into the terminal has published the credential it just minted."""
    _request(running, f"/?token={running.token}")
    _request(running, "/", cookie=_session(running))
    captured = capsys.readouterr()
    assert running.token not in captured.out
    assert running.token not in captured.err


def test_the_printed_url_is_the_one_that_carries_the_token(
    running: web_server.ServeConfig,
) -> None:
    url = running.url()
    assert url.startswith(f"http://{running.host}:{running.port}/?token=")
    assert running.token in url


def test_the_nav_and_the_routing_table_agree() -> None:
    """A page with no nav entry is unreachable; a nav entry with no page is a 404 the user is
    invited to click. Neither is caught by testing either side alone."""
    from keel.web import render

    assert {href for href, _label in render.NAV} == set(web_server.ROUTES)
    assert set(ROUTES) == set(web_server.ROUTES), "this test module's list drifted from the server"


def test_the_gates_page_names_every_capability_and_claims_none_of_them(
    running: web_server.ServeConfig,
) -> None:
    """The audit surface #436 asks for. It must list every gated action -- and say plainly that
    this view cannot perform any of them, which is true because no write verb is served."""
    from keel.capabilities import CAPABILITIES
    from keel.web import render

    _status, _headers, body = _request(running, "/gates", cookie=_session(running))
    for cap in CAPABILITIES:
        # Escaped, not raw: an invocation containing an apostrophe reaches the page as `&#x27;`,
        # and asserting on the raw form would quietly stop checking those rows.
        assert render.esc(cap.invocation) in body, cap.invocation
    assert "cannot perform any of them" in body


# -- first run (#437) --------------------------------------------------------------------------


@pytest.fixture
def empty_machine(tmp_path: Path) -> Iterator[web_server.ServeConfig]:
    """A server pointed at paths where nothing exists -- the state a first-run user is in, and
    the one most likely to render as a stack trace."""
    cfg = web_server.ServeConfig(
        host="127.0.0.1",
        port=0,
        token=new_session_token(),
        db_path=str(tmp_path / "keel.db"),
        config_path=str(tmp_path / "config.yaml"),
    )
    server = web_server.build_server(cfg)
    bound = web_server.ServeConfig(
        host=cfg.host,
        port=int(server.server_address[1]),
        token=cfg.token,
        db_path=cfg.db_path,
        config_path=cfg.config_path,
    )
    server.RequestHandlerClass.cfg = bound  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield bound
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_the_landing_page_of_a_machine_with_nothing_on_it_is_the_checklist(
    empty_machine: web_server.ServeConfig,
) -> None:
    """`gather_status` reads tables, so against a database with no schema it raises. Without
    first-run detection the very first thing a new user sees is a 500 whose real cause is that
    they have not set anything up yet."""
    status, _headers, body = _request(empty_machine, "/", cookie=_session(empty_machine))
    assert status == 200
    assert "There is no deployment here yet" in body
    assert "Traceback" not in body


@pytest.mark.parametrize("path", ROUTES)
def test_no_page_is_a_stack_trace_on_a_machine_with_nothing_on_it(
    empty_machine: web_server.ServeConfig, path: str
) -> None:
    """Every route, not just the landing page. Smoke-testing an empty directory found `/activity`,
    `/insights` and `/rules` answering 500 while `/` was fine -- so a first-run user who clicked
    anything in the nav got an error page."""
    status, _headers, body = _request(empty_machine, path, cookie=_session(empty_machine))
    assert status == 200, (path, body[:300])
    assert "Traceback" not in body


def test_looking_at_a_machine_with_nothing_on_it_creates_nothing(
    empty_machine: web_server.ServeConfig,
) -> None:
    """`sqlite3.connect` CREATES the file it cannot find, so a page that opens the database
    before checking whether there is one leaves an empty `keel.db` behind -- a read-only view
    bringing a deployment into existence by being looked at. Every route, because that is how
    this was missed: the original test walked two of them."""
    for path in ROUTES:
        _request(empty_machine, path, cookie=_session(empty_machine))
    assert not Path(empty_machine.db_path).exists()
    assert not Path(empty_machine.config_path).exists()


def test_the_checklist_never_shows_an_off_venue_step_as_done(
    running: web_server.ServeConfig,
) -> None:
    """keel cannot see whether USDC Rewards is off. Rendering it as done would turn an open riba
    exposure into a false assurance -- the operator runbook says so explicitly."""
    from keel.commands.setup import STEPS, StepKind

    _status, _headers, body = _request(running, "/setup", cookie=_session(running))
    off_venue = [step for step in STEPS if step.kind is StepKind.OFF_VENUE]
    assert off_venue
    for step in off_venue:
        assert render_esc(step.title) in body
    assert "cannot verify it" in body


def render_esc(value: str) -> str:
    from keel.web import render

    return render.esc(value)
