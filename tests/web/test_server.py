"""End-to-end over a real bound server (#435, D2).

These drive an actual `ThreadingHTTPServer` on an ephemeral port with a real database and a real
config, because the properties worth pinning here -- that a write verb never reaches keel, that
the token is required, that the token never appears in a log line -- are properties of the wire,
and a test against a hand-built handler object could pass while the served bytes said otherwise.

The `deployment` and `running` fixtures moved to `tests/web/conftest.py` when #536 added a second
module that drives the same server. They are unchanged -- same names, same bodies -- and pytest
discovers a conftest fixture for every module in this directory, so every test below requests
exactly what it requested before. The move happened because the alternative was importing a
fixture from one test module into another, which shadows the import with the parameter of every
test that uses it (ruff F811) and makes the fixture's home a test file rather than the place
pytest looks.
"""

from __future__ import annotations

import http.client
import json
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from keel.web import server as web_server
from keel.web.security import SESSION_COOKIE, new_session_token

#: Every endpoint that can carry deployment data, for the tests that sweep all of them.
#:
#: These were the seven server-rendered PAGES until #540. They are the JSON endpoints now, which
#: is a stronger sweep for the one test that uses it: a secret that leaked into a rendered page
#: would also have to leak into the document that page was built from, and this reads the
#: documents directly.
ROUTES = (
    "/api/status",
    "/api/setup",
    "/api/activity",
    "/api/insights",
    "/api/journal",
    "/api/rules",
    "/api/venues",
    "/api/gates",
    "/api/config",
)


def _request(
    cfg: web_server.ServeConfig,
    path: str,
    *,
    method: str = "GET",
    cookie: str | None = None,
    host: str | None = None,
    form: dict[str, str] | None = None,
    raw_body: str | None = None,
    csrf: str | None = None,
    client_header: str | None = "1",
    sec_fetch_site: str | None = None,
    origin: str | None = None,
) -> tuple[int, dict[str, str], str]:
    """`client_header` defaults to `"1"` and is only sent on `POST`.

    **Since #540 that default is load-bearing rather than harmless.** The write surface moved
    under `/api/`, where `X-Keel-Client` IS checked -- it used to be a form at `/setup/*`, which
    could not send a header and therefore could not be gated on one. A POST without it is now
    refused, and `test_a_form_style_post_is_refused_without_the_api_client_header` is the test
    that says so, in the place where its ancestor asserted the opposite.

    `form` is sent as a JSON object, because the write surface speaks JSON now; `raw_body` sends
    bytes verbatim, for the tests that are about what the parser does with a body rather than
    about what an action does with a field.

    `sec_fetch_site` and `origin` are both unset by default; pass them to reproduce what a real
    browser sends for a same-origin form submission -- see
    `test_a_browser_form_post_succeeds_without_the_api_client_header`, which is the test this
    parameter pair exists for."""
    conn = http.client.HTTPConnection(cfg.host, cfg.port, timeout=10)
    headers = {"Host": host if host is not None else f"{cfg.host}:{cfg.port}"}
    if cookie:
        headers["Cookie"] = cookie
    if method == "POST" and client_header is not None:
        headers["X-Keel-Client"] = client_header
    if sec_fetch_site is not None:
        headers["Sec-Fetch-Site"] = sec_fetch_site
    if origin is not None:
        headers["Origin"] = origin
    if csrf is not None:
        headers["X-Keel-CSRF"] = csrf
    body = None
    if raw_body is not None:
        body = raw_body
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(body))
    elif form is not None:
        body = json.dumps(form)
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(body))
    try:
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        body = response.read().decode("utf-8", "replace")
        return response.status, dict(response.getheaders()), body
    finally:
        conn.close()


def _P(rest: str) -> str:
    """A request path under the mount, composed rather than spelled -- #540 moved it to `/`."""
    from keel.web import staticfiles

    return staticfiles.STATIC_PREFIX + rest


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
    status, _headers, body = _request(running, "/", method=method, cookie=_session(running))
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
        status, _headers, body = _request(
            running, path, method="POST", cookie=_session(running), form={}, csrf=_csrf(running)
        )
        assert status == 404, path


# -- the guarantee that replaced "no POST at all" ---------------------------------------------


def test_the_write_surface_reaches_no_capability_increasing_action() -> None:
    """THE invariant, and the one the step-kind rule kept being a poor proxy for.

    `StepKind` was tried as the rule twice -- "MECHANICAL only", then "MECHANICAL or
    OPERATOR_INPUT" -- and both were the wrong axis. Two JUDGEMENT steps (attesting an asset,
    promoting a rule) live in the PAPER stage, so forbidding them bought no safety, because the
    capability registry already protects the dangerous ones. It only made a terminal-free paper
    deployment impossible, which is the entire point of the milestone.

    What must never be reachable is the eight. `withdrawals attest --enabled` is among them and
    is therefore not an action, and this asserts that by IDENTITY rather than by taste."""
    from keel.capabilities import CAPABILITIES
    from keel.commands.setup import ACTIONS, STEPS

    gated_invocations = {cap.invocation for cap in CAPABILITIES}
    by_key = {step.key: step for step in STEPS}
    for action in ACTIONS:
        step = by_key[action.key]
        for invocation in gated_invocations:
            assert invocation not in step.how, (
                f"the {action.key} action runs {invocation!r}, which is behind the TTY gate"
            )

    # The one judgement step that IS among the eight must have no action.
    assert "withdrawals_attested" in by_key
    assert "withdrawals_attested" not in {action.key for action in ACTIONS}


def test_an_off_venue_action_never_makes_its_step_done(
    running: web_server.ServeConfig,
) -> None:
    """`venue_interest_off` gained an action (#437 part 2): "on <date>, <name> stated they had
    done this at the venue" is a true statement about a STATEMENT, and keel can record it without
    claiming to have verified the venue dashboard behind it. What must still never happen -- the
    doctrine this replaces was defending exactly this -- is the step reading `done=True` as a
    result. Run the action through the real write path (CSRF, session, the lot) and check the
    step state it leaves behind, rather than asserting the old, stronger "no action at all" rule
    that #437 needed to relax."""
    from keel.commands.setup import ACTIONS, STEPS, StepKind

    off_venue = {s.key for s in STEPS if s.kind is StepKind.OFF_VENUE}
    assert off_venue, "no off-venue steps exist, so this proves nothing"
    gated = off_venue & {a.key for a in ACTIONS}
    assert gated == {"venue_interest_off"}, gated

    cookie = _session(running)
    csrf = _csrf(running)
    status, _headers, _body = _request(
        running,
        "/api/setup/venue_interest_off",
        method="POST",
        cookie=cookie,
        form={"acknowledged_by": "Elmehdi", "did_it": "yes"},
        csrf=csrf,
    )
    assert status == 200

    _status, _headers, body = _request(running, "/api/setup", cookie=cookie)
    envelope = json.loads(body)
    step = next(s for s in envelope["data"]["steps"] if s["key"] == "venue_interest_off")
    assert step["done"]["value"] == "false", step


def test_the_backing_choices_match_the_screens_own_vocabulary() -> None:
    """The form lists them as literals to keep `keel.compliance.screen` off the CLI's import
    path. That is a duplication, so it is pinned: a backing kind added to the screen and missing
    from the form would be un-attestable from the browser, silently."""
    from keel.commands.setup import _backing_choices, action_for

    field = next(f for f in action_for("assets_attested").inputs if f.name == "backing")
    assert tuple(sorted(field.choices)) == _backing_choices()


def test_promotion_offers_no_force_field() -> None:
    """`attempt_promotion`'s own docstring: force "carries no gate HERE ... the O3 contract is the
    front-end's to keep, never the service's to assume". The console keeps it with a typed
    terminal confirmation. A browser cannot keep it that way, so this front-end simply has no
    force path -- and force-promote is itself one of the eight."""
    import inspect as inspect_mod

    from keel.commands import setup as setup_mod

    action = setup_mod.action_for("rule_promoted")
    assert {f.name for f in action.inputs} == {"rule_id"}
    source = inspect_mod.getsource(setup_mod.promote_rule)
    assert "force=False" in source
    assert "force=True" not in source


def test_an_action_declares_inputs_exactly_when_its_step_needs_them() -> None:
    """So a mechanical action cannot quietly start accepting operator data, and an operator-input
    action cannot quietly stop requiring it -- either drift would move a step across the line the
    test above draws, without touching that test.

    `StepKind.OFF_VENUE` joined the set with `venue_interest_off`'s acknowledgement (#437 part
    2): recording "who, and when" is exactly as much an operator-supplied fact as a judgement's
    source, and the action declaring it without required inputs would be the thing that could
    fill the field in on the operator's behalf."""
    from keel.commands.setup import ACTIONS, STEPS, StepKind

    by_key = {step.key: step for step in STEPS}
    for action in ACTIONS:
        needs = by_key[action.key].kind in (
            StepKind.OPERATOR_INPUT,
            StepKind.JUDGEMENT,
            StepKind.OFF_VENUE,
        )
        assert action.needs_input is needs, action.key


def test_no_capability_increasing_action_is_reachable_from_the_web_layer() -> None:
    """THE safety property, and the reason this is a better guarantee than "no POST at all".

    "No POST" said the server could not write -- and was also satisfied by a server that could
    not set anything up, which is the problem #437 exists to solve. This says the server cannot
    ARM, RELEASE or SPEND anything: not one of the eight capability-increasing actions in
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


def test_the_disjointness_comments_state_the_actual_capability_count() -> None:
    """`test_no_capability_increasing_action_is_reachable_from_the_web_layer` above proves the
    disjointness; it says nothing about whether the prose a reviewer actually reads -- "not one of
    the N capability-increasing actions ... is reachable" -- names the right N. That number has
    drifted twice already (eleven to seven when the TUI was deleted, seven to eight at #233), and
    a stale one is worse than no comment: it is the sentence a reviewer checks this very test
    against.

    So this reads the count back out of the prose itself, in every `keel/web/` source that makes
    the claim, and compares it to `len(CAPABILITIES)` -- never a literal written here, which would
    only move the drift into this file instead of catching it."""
    import glob
    import os
    import re

    from keel.capabilities import CAPABILITIES

    number_words = {
        "zero": 0,
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
        "eleven": 11,
        "twelve": 12,
        "thirteen": 13,
        "fourteen": 14,
        "fifteen": 15,
    }
    #: Matches the prose across a line wrap too: `security.py` and `server.py:180` both break the
    #: line between "eleven" and "capability-increasing", with a comment marker (`#:` or ` *`) and
    #: leading whitespace in between.
    pattern = re.compile(r"(\w+)\s+capability-[\s#:*]*increasing actions", re.IGNORECASE)

    web_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "keel",
        "web",
    )
    sources = sorted(glob.glob(os.path.join(web_dir, "**", "*.py"), recursive=True)) + sorted(
        glob.glob(os.path.join(web_dir, "static", "js", "*.js"))
    )
    assert sources, "the scan found no sources, which would make this vacuous"

    stated: list[tuple[str, str]] = []
    for path in sources:
        text = open(path, encoding="utf-8").read()
        rel = os.path.relpath(path, web_dir)
        for word in pattern.findall(text):
            stated.append((rel, word.lower()))

    #: Five files state the claim today (`server.py` twice) -- if the scan found none, the
    #: assertions below would pass on an empty list and prove nothing.
    assert stated, "no source under keel/web states the capability count in prose"

    for rel, word in stated:
        assert word in number_words, f"{rel} states an unrecognised count word: {word!r}"
        assert number_words[word] == len(CAPABILITIES), (
            f"{rel} says {word!r} ({number_words[word]}) capability-increasing actions, but "
            f"keel/capabilities.py's CAPABILITIES has {len(CAPABILITIES)} rows -- prose is stale"
        )


# -- CSRF -------------------------------------------------------------------------------------


def test_a_write_without_the_session_cookie_is_refused(
    empty_machine: web_server.ServeConfig,
) -> None:
    """Admission is shared with GET, deliberately: a write path with a laxer check than the read
    path is exactly the shape of a bug nobody notices."""
    status, _headers, body = _request(
        empty_machine, "/api/setup/config", method="POST", form={}, csrf=_csrf(empty_machine)
    )
    assert status == 403
    assert not Path(empty_machine.config_path).exists()


def test_a_write_without_the_csrf_token_is_refused(
    empty_machine: web_server.ServeConfig,
) -> None:
    """`SameSite=Strict` already stops a cross-site POST in any current browser. This is the
    layer that does not depend on the browser being current."""
    for form in ({}, {"csrf": ""}, {"csrf": "not-the-token"}):
        status, _headers, body = _request(
            empty_machine,
            "/api/setup/config",
            method="POST",
            cookie=_session(empty_machine),
            form=form,
        )
        assert status == 403, form
    assert not Path(empty_machine.config_path).exists()


def test_a_write_from_a_rebound_hostname_is_refused(
    empty_machine: web_server.ServeConfig,
) -> None:
    status, _headers, body = _request(
        empty_machine,
        "/api/setup/config",
        method="POST",
        cookie=_session(empty_machine),
        form={}, csrf=_csrf(empty_machine),
        host=f"evil.example:{empty_machine.port}",
    )
    assert status == 403
    assert not Path(empty_machine.config_path).exists()


def test_the_csrf_token_is_not_the_session_token(
    running: web_server.ServeConfig,
) -> None:
    """The session token is `HttpOnly` and must never leave the cookie; the CSRF token is handed
    to the client. They must therefore be different values, and the derivation must not be
    reversible.

    Read off `/api/setup` since #540, which is where the token is delivered now that there is no
    rendered form to carry it. `payload.setup_payload`'s docstring records why it is in a body at
    all -- that was a reversal of an earlier decision, and the reasoning is written down there."""
    from keel.web.security import csrf_token

    assert csrf_token(running.token) != running.token
    _status, _headers, body = _request(running, "/api/setup", cookie=_session(running))
    assert running.token not in body, "the SESSION token reached a response body"
    assert csrf_token(running.token) in body


# -- Sec-Fetch-Site (#535), checked on every POST ----------------------------------------------
#
# A real browser form POST DOES carry Sec-Fetch-Site: it is Fetch Metadata, set by the browser
# itself on every request the page initiates -- forms included -- and page JavaScript can neither
# set nor override it. That mattered most while the write surface WAS a script-free form; since
# #540 it is one layer of three on the same request, alongside a custom header (see the
# section after the next one), which that UI has no way to attach at all.


def test_a_write_missing_sec_fetch_site_is_accepted(
    empty_machine: web_server.ServeConfig,
) -> None:
    """Older browsers never send `Sec-Fetch-Site` at all; its absence must not be a refusal, or
    every one of them would be locked out of the one write surface that exists."""
    status, _headers, body = _request(
        empty_machine,
        "/api/setup/config",
        method="POST",
        cookie=_session(empty_machine),
        form={}, csrf=_csrf(empty_machine),
        sec_fetch_site=None,
    )
    assert status == 200


def test_a_write_with_sec_fetch_site_same_origin_is_accepted(
    empty_machine: web_server.ServeConfig,
) -> None:
    status, _headers, body = _request(
        empty_machine,
        "/api/setup/config",
        method="POST",
        cookie=_session(empty_machine),
        form={}, csrf=_csrf(empty_machine),
        sec_fetch_site="same-origin",
    )
    assert status == 200


@pytest.mark.parametrize("value", ["cross-site", "same-site", "none"])
def test_a_write_with_a_wrong_sec_fetch_site_is_refused(
    empty_machine: web_server.ServeConfig, value: str
) -> None:
    """Page JavaScript cannot forge this header -- the browser sets it. A value present and
    wrong is therefore real evidence of a cross-origin request, unlike a missing one."""
    status, _headers, body = _request(
        empty_machine,
        "/api/setup/config",
        method="POST",
        cookie=_session(empty_machine),
        form={}, csrf=_csrf(empty_machine),
        sec_fetch_site=value,
    )
    assert status == 403, value
    assert not Path(empty_machine.config_path).exists()


# -- the browser form the shipped UI actually submits --------------------------------------------


def test_a_form_style_post_is_refused_without_the_api_client_header(
    empty_machine: web_server.ServeConfig,
) -> None:
    """**This test asserted the OPPOSITE until #540, and the inversion is the point.**

    Its ancestor was named `..._succeeds_without_the_api_client_header` and it existed to catch a
    real regression: an earlier version of #535 checked `X-Keel-Client` over every POST, which
    made a plain browser form submission a 403. The shipped UI's entire write surface WAS such a
    form -- `<form method="post" action="/setup/{key}">`, on pages that shipped no `script-src`
    at all -- so every setup action would have been unreachable, with no terminal to fall back to
    on the desktop bundle. Scoping the header check to `/api/*` was the fix.

    #540 deleted those forms. The write surface moved under `/api/`, every write now comes from a
    `fetch()` client that sends the header, and the check the old shape could not survive is now
    the one closing the gap `SameSite` and the HMAC token both assume shut: a plain form POST is
    never preflighted, so a custom header is what a hostile origin cannot produce.

    This sends exactly what a browser sends for a form submission -- cookie, CSRF token, `Origin`,
    and `Sec-Fetch-Site: same-origin` -- and deliberately not `X-Keel-Client`. It must be refused,
    and nothing must be written.
    """
    status, _headers, body = _request(
        empty_machine,
        "/api/setup/config",
        method="POST",
        cookie=_session(empty_machine),
        form={},
        csrf=_csrf(empty_machine),
        client_header=None,
        sec_fetch_site="same-origin",
        origin=f"http://{empty_machine.host}:{empty_machine.port}",
    )
    assert status == 403
    assert not Path(empty_machine.config_path).exists()


# -- the client header (#535's third CSRF layer), over the whole write surface ------------------
#
# A custom request header is only obtainable from a `fetch()` client -- an HTML
# `<form method=post>` cannot set one, which the section above pins directly. That used to make
# this check inapplicable to the write surface, because the write surface WAS such a form; since
# #540 every write comes from a `fetch()` client under `/api/`, and the check covers all of it.
# `SameSite=Strict` and the HMAC CSRF token both assume a hostile cross-origin write is
# PREFLIGHTED -- true for `fetch()`, never
# true for a form -- so a custom header closes that gap specifically where a `fetch()` client
# exists to carry it. A hostile origin's script could still try to add the header itself, but
# doing so makes the request cross-origin-with-a-custom-header, which is exactly the shape CORS
# preflights, and this server answers no `Access-Control-Allow-*` to any origin (there is no CORS
# configuration at all -- see server.py's module docstring), so a hostile preflight cannot
# succeed either.


def test_an_api_write_without_the_client_header_is_refused(
    empty_machine: web_server.ServeConfig,
) -> None:
    status, _headers, body = _request(
        empty_machine,
        "/api/whatever",
        method="POST",
        cookie=_session(empty_machine),
        client_header=None,
    )
    assert status == 403


@pytest.mark.parametrize("value", ["0", "true", "yes", "2", ""])
def test_an_api_write_with_a_wrong_client_header_value_is_refused(
    empty_machine: web_server.ServeConfig, value: str
) -> None:
    status, _headers, body = _request(
        empty_machine,
        "/api/whatever",
        method="POST",
        cookie=_session(empty_machine),
        client_header=value,
    )
    assert status == 403, value


def test_an_api_write_with_the_client_header_reaches_the_404_not_the_403(
    empty_machine: web_server.ServeConfig,
) -> None:
    """No `/api/*` write surface exists yet (#533/#534); this pins that a CORRECT header clears
    the client-header gate and lands on the same "no such action" 404 every unmapped path gets --
    proving the check is a gate on a real path, not a black hole that also swallows valid
    requests once one exists."""
    status, _headers, body = _request(
        empty_machine,
        "/api/whatever",
        method="POST",
        cookie=_session(empty_machine),
        client_header="1",
    )
    assert status == 404
    assert "No such action" in body


# -- the actions themselves, over the wire ------------------------------------------------------


def test_a_first_run_user_can_build_a_paper_deployment_from_the_browser(
    empty_machine: web_server.ServeConfig,
) -> None:
    """#437's acceptance, as far as this PR takes it: config, database and rule library with no
    command typed. Market data and every judgement step remain outstanding, by design."""
    from keel.commands.setup import ACTIONS, inspect

    for action in ACTIONS:
        status, headers, body = _request(
            empty_machine,
            f"/api/setup/{action.key}",
            method="POST",
            cookie=_session(empty_machine),
            form={}, csrf=_csrf(empty_machine),
        )
        assert status == 200, action.key
        assert json.loads(body)["data"]["step_key"] == action.key

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
            status, _headers, body = _request(
                empty_machine,
                f"/api/setup/{action.key}",
                method="POST",
                cookie=_session(empty_machine),
                form={}, csrf=_csrf(empty_machine),
            )
            assert status == 200

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
        status, _headers, body = _request(
            empty_machine,
            f"/api/setup/{key}",
            method="POST",
            cookie=_session(empty_machine),
            form={}, csrf=_csrf(empty_machine),
        )
        assert status == 404, key


def test_an_oversized_form_body_is_refused_without_being_read(
    empty_machine: web_server.ServeConfig,
) -> None:
    """`rfile.read(n)` with an attacker-supplied `n` is a memory-exhaustion primitive, and there
    is no proxy in front of this server to impose a limit.

    **The refusal is a 400 since #540, and it used to be a 403.** The difference is where the
    write token lives: it was a field IN the body, so an oversized body meant the token never
    arrived and the CSRF layer refused first. It is a header now, so the token is present and
    valid and the body itself is what is rejected -- which is a more accurate answer to what went
    wrong. What has not changed is the part that matters: the body is refused on its declared
    `Content-Length`, before a single byte of it is read, and nothing is written."""
    status, _headers, body = _request(
        empty_machine,
        "/api/setup/config",
        method="POST",
        cookie=_session(empty_machine),
        csrf=_csrf(empty_machine), form={"padding": "x" * 32_000},
    )
    assert status == 400
    assert not Path(empty_machine.config_path).exists()


def test_a_refused_write_does_not_poison_the_next_request_on_the_same_connection(
    empty_machine: web_server.ServeConfig,
) -> None:
    """**The bug this test exists for was invisible to every other test in this file.**

    `protocol_version` is HTTP/1.1, so browsers reuse connections -- and every helper here opens a
    fresh one per request, which is precisely why none of them could see this. A POST refused
    before its body was read left those bytes in the socket; the stdlib then parsed them as the
    next request line and answered 501. The refusal was correct and the request AFTER it failed,
    with a status unrelated to either.

    It became possible at #540: the CSRF token moved from a form field into a header, so the
    refusal now happens in front of the body read rather than behind it. Found by driving a real
    browser through a sequence of writes; fixed in `server._drain_request_body`.

    This drives two requests down ONE connection, which is the only way to observe it.
    """
    conn = http.client.HTTPConnection(empty_machine.host, empty_machine.port, timeout=10)
    try:
        body = json.dumps({"padding": "x" * 200})
        headers = {
            "Host": f"{empty_machine.host}:{empty_machine.port}",
            "Cookie": _session(empty_machine),
            "X-Keel-Client": "1",
            "X-Keel-CSRF": "not-the-token",
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
        }
        conn.request("POST", "/api/setup/config", body=body, headers=headers)
        first = conn.getresponse()
        first.read()
        assert first.status == 403

        # The SAME connection. Before the fix this answered 501, having parsed the previous
        # request's body as a request line.
        conn.request(
            "GET",
            "/api/config",
            headers={
                "Host": f"{empty_machine.host}:{empty_machine.port}",
                "Cookie": _session(empty_machine),
            },
        )
        second = conn.getresponse()
        payload = second.read().decode("utf-8", "replace")
        assert second.status == 200, payload[:200]
        assert json.loads(payload)["data"] is not None
    finally:
        conn.close()

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
    status, headers, body = _request(running, f"/?token={running.token}")
    # Still a 303, and the ONE redirect this server still sends. The write surface stopped
    # redirecting at #540 -- there is no page left to redirect to -- but the token exchange is a
    # navigation, and getting the credential out of the address bar is the whole point of it.
    assert status == 303
    assert headers["Location"] == "/"
    assert "token" not in headers["Location"]
    cookie = headers["Set-Cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE}={running.token}")
    assert "SameSite=Strict" in cookie
    assert "HttpOnly" in cookie


def test_a_wrong_token_is_refused(running: web_server.ServeConfig) -> None:
    status, _headers, body = _request(running, "/?token=not-the-token")
    assert status == 403
    status, _headers, body = _request(running, "/", cookie=f"{SESSION_COOKIE}=not-the-token")
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


def test_an_unknown_path_is_a_page_not_a_stack_trace(
    running: web_server.ServeConfig,
) -> None:
    status, _headers, body = _request(running, "/nope", cookie=_session(running))
    assert status == 404
    assert "No such page" in body


def test_head_returns_the_headers_without_a_body(running: web_server.ServeConfig) -> None:
    status, headers, body = _request(running, "/", method="HEAD", cookie=_session(running))
    assert status == 200
    assert body == ""
    assert int(headers["Content-Length"]) > 0


# -- static assets (#535) -------------------------------------------------------------------
#
# `keel/web/static/index.html` is the one placeholder asset the package ships (#536's real
# client does not exist yet). These drive it over the real wire -- a real bound server, a real
# `Path` on disk -- for the same reason `tests/web/test_staticfiles.py`'s module docstring
# gives: the properties worth pinning are properties of the served bytes, not of a resolver
# called directly.


def test_a_shipped_static_asset_is_served(running: web_server.ServeConfig) -> None:
    status, headers, body = _request(running, _P("index.html"), cookie=_session(running))
    assert status == 200
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert "keel" in body.lower()


def test_static_assets_are_behind_the_same_admission_as_every_other_page(
    running: web_server.ServeConfig,
) -> None:
    """Never weakened: a static file is not exempted from the loopback-plus-session model just
    because it holds no secrets today. The same guard that protects `/rules` protects this."""
    status, _headers, body = _request(running, _P("index.html"))  # no cookie
    assert status == 403


def test_a_missing_static_asset_is_a_404(running: web_server.ServeConfig) -> None:
    status, _headers, body = _request(
        running, _P("does-not-exist.html"), cookie=_session(running)
    )
    assert status == 404


@pytest.mark.parametrize(
    "path",
    [
        _P("../keel/web/security.py"),
        _P("../../pyproject.toml"),
        _P("%2e%2e/%2e%2e/pyproject.toml"),
        _P("/etc/passwd"),
        _P("..%2f..%2fpyproject.toml"),
    ],
)
def test_directory_traversal_through_the_wire_is_refused(
    running: web_server.ServeConfig, path: str
) -> None:
    """The unit-level payloads live in `tests/web/test_staticfiles.py`; these are the same shape
    of attack sent as an actual HTTP request line, over a real socket, to prove nothing between
    the wire and the resolver -- URL parsing, `http.server`'s own request handling -- reopens
    what the resolver closes."""
    status, _headers, body = _request(running, path, cookie=_session(running))
    assert status == 404, (path, body[:200])


def test_a_static_html_response_carries_the_new_header_set(
    running: web_server.ServeConfig,
) -> None:
    """The full set from #535, on `text/html`. `form-action`, `base-uri` and `frame-ancestors`
    do NOT inherit from `default-src` under CSP3 -- each defaults to "anywhere" unless named --
    so all three are asserted individually, not just `default-src`/`connect-src`."""
    status, headers, body = _request(running, _P("index.html"), cookie=_session(running))
    assert status == 200
    csp = headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "connect-src 'self'" in csp
    assert "form-action 'self'" in csp
    assert "base-uri 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert "no-store" in headers["Cache-Control"]
    assert "Strict-Transport-Security" not in headers


def test_a_static_svg_response_also_carries_the_csp(
    running: web_server.ServeConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """SVG is active content: a same-origin `.svg` opened directly (not `<img>`-embedded, which
    does not execute it) runs any inline `<script>` it contains in keel's own origin. Without CSP
    on this content type too, that script runs with no policy at all."""
    from keel.web import staticfiles

    (tmp_path / "icon.svg").write_text("<svg></svg>")
    monkeypatch.setattr(staticfiles, "STATIC_ROOT", tmp_path)

    status, headers, body = _request(running, _P("icon.svg"), cookie=_session(running))
    assert status == 200
    assert headers["Content-Type"] == "image/svg+xml"
    csp = headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp


def test_a_non_html_non_svg_static_asset_carries_no_csp(
    running: web_server.ServeConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Per the design spec: CSP is invalid and discouraged on anything but `text/html` (and, per
    #535's review, `image/svg+xml` -- see the test above). A CSS or JS asset still gets
    `nosniff`, `X-Frame-Options` and `Referrer-Policy` -- those are meaningful on any content
    type -- but no `Content-Security-Policy` header at all."""
    from keel.web import staticfiles

    (tmp_path / "style.css").write_text("body { color: black; }")
    monkeypatch.setattr(staticfiles, "STATIC_ROOT", tmp_path)

    status, headers, body = _request(running, _P("style.css"), cookie=_session(running))
    assert status == 200
    assert headers["Content-Type"] == "text/css; charset=utf-8"
    assert "Content-Security-Policy" not in headers
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert "no-store" in headers["Cache-Control"]
    assert "Strict-Transport-Security" not in headers


def test_an_unrecognised_static_extension_is_refused(
    running: web_server.ServeConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A file with no entry in the Content-Type table is refused rather than guessed at -- see
    `staticfiles.content_type_for`'s docstring."""
    from keel.web import staticfiles

    (tmp_path / "payload.exe").write_bytes(b"MZ")
    monkeypatch.setattr(staticfiles, "STATIC_ROOT", tmp_path)

    status, _headers, body = _request(
        running, _P("payload.exe"), cookie=_session(running)
    )
    assert status == 404


def test_a_static_file_removed_between_resolve_and_read_is_a_500_not_a_hang(
    running: web_server.ServeConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`resolve_static_asset` proves the file existed at check time; a `read_bytes()` failure
    afterwards (deleted, permission change) must land as a clean page, matching the guarantee
    `test_a_broken_page_does_not_take_the_server_down` already pins for rendered routes."""
    from keel.web import staticfiles

    target = tmp_path / "flaky.html"
    target.write_text("<html></html>")
    monkeypatch.setattr(staticfiles, "STATIC_ROOT", tmp_path)

    real_resolve = staticfiles.resolve_static_asset

    def _resolve_then_delete(root: Path, url_path: str) -> Path | None:
        resolved = real_resolve(root, url_path)
        if resolved is not None:
            resolved.unlink()
        return resolved

    monkeypatch.setattr(staticfiles, "resolve_static_asset", _resolve_then_delete)

    status, _headers, body = _request(running, _P("flaky.html"), cookie=_session(running))
    assert status == 500
    assert "OSError" in body or "FileNotFoundError" in body


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


def render_esc(value: str) -> str:
    from keel.web import render

    return render.esc(value)


# -- the credential form, over the wire --------------------------------------------------------


def test_a_submitted_secret_never_appears_in_a_response_or_a_redirect(
    empty_machine: web_server.ServeConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A submitted secret must not come back out of any surface that can be read.

    **There is no redirect to check any more** -- #540 deleted the POST/redirect/GET cycle along
    with the page it redirected to -- and that removes one of the three leaks this test was
    written for: a secret in a redirect URL is a secret in browser history, in the `Referer` of
    anything the page later loads, and in any proxy log in between. What remains is the leak that
    was always the harder one to notice: the value coming back in a document. This drives the
    real wire and then re-reads every endpoint."""
    from keel.commands import setup as setup_mod

    stored: dict[str, str] = {}
    monkeypatch.setattr(
        "keel_core.secrets.store_secret", lambda name, value: stored.__setitem__(name, value)
    )
    monkeypatch.setattr("keel_core.secrets.keychain_available", lambda: True)
    monkeypatch.setattr("keel_core.secrets._from_keychain", lambda name: stored.get(name))

    secret = "cdp-secret-that-must-never-be-echoed"
    status, headers, body = _request(
        empty_machine,
        "/api/setup/credentials",
        method="POST",
        cookie=_session(empty_machine),
        csrf=_csrf(empty_machine),
            form={
            "CDP_API_KEY": "cdp-key-value",
            "CDP_API_SECRET": secret,
        },
    )
    assert status == 200
    assert json.loads(body)["data"]["step_key"] == "credentials"
    assert "Location" not in headers, "the write surface redirects again; a secret can ride in one"
    assert secret not in body
    assert stored["CDP_API_SECRET"] == secret

    for path in ROUTES:
        _status, _headers, document = _request(empty_machine, path, cookie=_session(empty_machine))
        assert secret not in document, path
        assert "cdp-key-value" not in document, path

    assert setup_mod.MARKET_DATA_SECRETS == ("CDP_API_KEY", "CDP_API_SECRET")


def test_a_blank_field_records_nothing(
    empty_machine: web_server.ServeConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An action that could fill in a field the operator left blank is one that could record
    something they never supplied."""
    stored: dict[str, str] = {}
    monkeypatch.setattr(
        "keel_core.secrets.store_secret", lambda name, value: stored.__setitem__(name, value)
    )
    status, _headers, body = _request(
        empty_machine,
        "/api/setup/credentials",
        method="POST",
        cookie=_session(empty_machine),
        csrf=_csrf(empty_machine), form={"CDP_API_KEY": "k", "CDP_API_SECRET": "   "},
    )
    assert status == 200
    assert stored == {}


def test_a_field_the_action_did_not_declare_is_dropped(
    empty_machine: web_server.ServeConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The form is attacker-shaped input the moment anyone can craft a POST, so an action should
    never receive a key it has no name for."""
    seen: dict[str, str] = {}

    def _capture(_config: object, _db: object, values: dict[str, str]) -> object:
        seen.update(values)
        from keel.commands.setup import ActionResult

        return ActionResult("credentials", False, "captured")

    from keel.commands import setup as setup_mod

    monkeypatch.setattr(
        setup_mod,
        "ACTIONS",
        tuple(
            a if a.key != "credentials" else type(a)(a.key, a.title, a.detail, _capture, a.inputs)
            for a in setup_mod.ACTIONS
        ),
    )
    _request(
        empty_machine,
        "/api/setup/credentials",
        method="POST",
        cookie=_session(empty_machine),
        csrf=_csrf(empty_machine),
            form={
            "CDP_API_KEY": "k",
            "CDP_API_SECRET": "s",
            "SOMETHING_ELSE": "should not arrive",
        },
    )
    assert set(seen) == {"CDP_API_KEY", "CDP_API_SECRET"}


# -- the background job, on the page -----------------------------------------------------------


def test_starting_market_data_returns_immediately(
    empty_machine: web_server.ServeConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The property the whole module exists for: the POST must not wait for the fetch."""
    from keel.commands import jobs
    from keel.commands import setup as setup_mod

    jobs.reset()
    gate = threading.Event()
    started = threading.Event()

    def _slow_action(_config, _db, _values):
        jobs.start("market_data", lambda echo: (started.set(), gate.wait(5)))
        return setup_mod.ActionResult("market_data", True, "started")

    monkeypatch.setattr(
        setup_mod,
        "ACTIONS",
        tuple(
            a
            if a.key != "market_data"
            else type(a)(a.key, a.title, a.detail, _slow_action, a.inputs)
            for a in setup_mod.ACTIONS
        ),
    )
    try:
        status, headers, body = _request(
            empty_machine,
            "/api/setup/market_data",
            method="POST",
            cookie=_session(empty_machine),
            form={}, csrf=_csrf(empty_machine),
        )
        assert status == 200
        assert json.loads(body)["data"]["step_key"] == "market_data"
        assert started.wait(5), "the job never started"
        assert jobs.is_running(), "the request returned before the job finished, as intended"
    finally:
        gate.set()
        jobs.wait(5)
        jobs.reset()
