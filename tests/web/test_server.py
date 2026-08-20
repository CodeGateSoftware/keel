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

import pytest

from keel.data.db import connect, migrate
from keel.web import server as web_server
from keel.web.security import SESSION_COOKIE, new_session_token
from tests.conftest import VALID_CONFIG_YAML

ROUTES = ("/", "/activity", "/insights", "/rules", "/venues", "/glossary")


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
) -> tuple[int, dict[str, str], str]:
    conn = http.client.HTTPConnection(cfg.host, cfg.port, timeout=10)
    headers = {"Host": host if host is not None else f"{cfg.host}:{cfg.port}"}
    if cookie:
        headers["Cookie"] = cookie
    try:
        conn.request(method, path, headers=headers)
        response = conn.getresponse()
        body = response.read().decode("utf-8", "replace")
        return response.status, dict(response.getheaders()), body
    finally:
        conn.close()


def _session(cfg: web_server.ServeConfig) -> str:
    return f"{SESSION_COOKIE}={cfg.token}"


# -- the read-only guarantee ---------------------------------------------------------------


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_no_write_verb_is_answered(running: web_server.ServeConfig, method: str) -> None:
    """The read-only property is STRUCTURAL, not a matter of routing discipline: the handler
    implements `do_GET`/`do_HEAD` and nothing else, so the stdlib refuses everything else before
    any keel code -- or any authentication -- runs. This is the test that would have to be
    deleted, not merely edited, for a write surface to appear here by accident."""
    status, _headers, _body = _request(running, "/", method=method, cookie=_session(running))
    assert status == 501


def test_the_handler_declares_only_get_and_head() -> None:
    """The same guarantee read off the class, so that a `do_POST` added anywhere in the
    hierarchy fails here even if a test forgot to exercise its verb."""
    verbs = {
        name
        for klass in web_server.KeelHandler.__mro__
        for name in vars(klass)
        if name.startswith("do_")
    }
    assert verbs == {"do_GET", "do_HEAD"}


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
