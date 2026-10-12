"""Pulse, the console's home view, and its one write: HALT (console PR1).

Pulse replaced Status, Activity and Gates. It is the glance page -- what keel did today, what
stopped it, what it will do next -- and the one place the browser may stop the engine. These
tests pin the SHAPE of that: where the route sits, that the three retired views are gone from
every table, and that the halt is wired the way `keel/web/gates.py` says it must be ("stopping
must never be slower than starting") without ever offering the way back.

**What no test in this file covers.** There is no JavaScript runtime in this suite (see
`test_client_assets.py`'s header). Every assertion is over the SOURCE TEXT, so each one asserts a
COUNT or a PAIRING rather than a substring -- `test_view_sections.py` records why: a scan for a
name passes on its declaration alone. That the button halts, that the native confirm appears and
that the HALTED line replaces the button on the next read are properties of a browser, checked by
hand against a running `keel serve` on a throwaway copy of a deployment.
"""

from __future__ import annotations

import json
import re

from keel.web import gates, security, staticfiles
from tests.web.test_client_assets import (
    _INDEX,
    _MODULES,
    _STATIC,
    _code_only,
    _comments_stripped,
    _function_body,
    _js_route_names,
    _source,
)
from tests.web.test_view_sections import _decl


def _pulse_region() -> str:
    """`pulseView` and everything up to the next export -- the region `_view_keys` scans too."""
    code = _comments_stripped(_source("render.js"))
    start = code.index("export function pulseView(")
    end = code.find("\nexport function ", start + 1)
    return code[start:end]


def test_pulse_is_the_first_route_in_both_tables_and_the_default() -> None:
    main = _code_only(_source("main.js"))

    assert _js_route_names()[0] == "pulse"
    assert staticfiles.CLIENT_ROUTES[0] == "pulse"
    assert _decl(main, "DEFAULT_ROUTE").strip() == "const DEFAULT_ROUTE = ROUTES[0];"


def test_the_retired_views_are_gone_from_every_table() -> None:
    retired = {"status", "activity", "gates", "orders", "positions"}
    index = _INDEX.read_text(encoding="utf-8")
    hrefs = re.findall(r'<li><a href="([^"]+)">', index)
    render = _code_only(_source("render.js"))
    main = _code_only(_source("main.js"))

    assert retired.isdisjoint(_js_route_names())
    assert retired.isdisjoint(staticfiles.CLIENT_ROUTES)
    assert hrefs, "the nav scan found no links; it has stopped working"
    assert retired.isdisjoint({href.rsplit("/", 1)[-1] for href in hrefs})
    for name in ("statusView", "activityView", "gatesView", "ordersView", "positionsView"):
        assert f"export function {name}(" not in render, name
        assert name not in main, name
    assert len(re.findall(r"route\.name === \"(?:activity|gates)\"", _comments_stripped(main))) == 0
    # The fallthrough is the home view, so an unrecognised route lands somewhere that renders.
    mount = _function_body(_source("main.js"), "mount").split("\n}\n")[0]
    assert mount.endswith("  return pulseView(data);"), "mount's fallthrough must be the home view"


def test_the_halt_control_is_one_form_with_one_button_or_one_resume_line() -> None:
    body = _function_body(_source("render.js"), "haltControl")

    assert body.count('el("form"') == 1
    assert body.count('el("button"') == 1
    assert body.count('"data-action", "halt"') == 1
    # The one `<code>` is the payload's own string, placed unread.
    assert body.count('el("code"') == 1
    codes = re.findall(r'el\("code", undefined, ([^)]*\)?)\)', body)
    assert codes == ["plain(data.halt.resume_command)"]
    # The console names `keel resume` from the payload and nowhere in its own source.
    assert "keel resume" not in _comments_stripped(_source("render.js"))


def test_the_halt_branch_is_presence_of_the_resume_command() -> None:
    body = _function_body(_source("render.js"), "haltControl")

    assert re.findall(r"\bif \(([^\n]*)\) \{", body) == ["plain(data.halt.resume_command)"]
    assert ".value" not in body


def test_pulse_view_offers_no_resume_and_names_no_phrase() -> None:
    phrases = [action.phrase for action in gates.TIER1_ACTIONS.values()]
    assert phrases, "the gate table is empty; the scan below would pass by vacuum"
    for name in _MODULES:
        raw = _source(name)
        stripped = _comments_stripped(raw)
        assert "gates/resume" not in stripped, name
        for key in gates.TIER1_ACTIONS:
            assert "gates/" + key not in stripped, (name, key)
        for phrase in phrases:
            assert phrase not in raw, (name, phrase)


def test_main_confirms_once_before_the_halt_and_never_elsewhere() -> None:
    main = _comments_stripped(_source("main.js"))
    code = _code_only(_source("main.js"))

    assert code.count("confirm(") == 1
    assert len(re.findall(r"\bhalt\(", code)) == 1

    start = main.index('contentNode.addEventListener("submit"')
    listener = main[start : main.index("\n});", start)]
    branch = listener.index('key === "halt"')
    confirm = listener.index("window.confirm(HALT_CONFIRM)")
    called = listener.index("halt()")
    generic = listener.index("perform(key, values)")
    assert branch < confirm < called < generic
    # The halt branch ends in a return, so the generic setup action never runs for it.
    assert "return;" in listener[called:generic]

    declared = re.findall(r'^const HALT_CONFIRM = "([^"\n]*)";$', main, re.M)
    assert declared == ["Halt keel? Resuming requires `keel resume` at a terminal."]


def test_the_halt_token_lives_only_in_the_write_boundary() -> None:
    """**#968, at the client.** The halt token is held in `actions.js`, read off `data.halt_token`
    there and nowhere else, and sent by `api.js` alone. The release header (`GATES_HEADER`) is
    named by no client module at all: the browser never holds the release token, so no code here
    has anything to put in it. Counts per module, not a substring of one file, so a second
    sender or a second holder fails rather than hiding behind the first."""
    actions = _code_only(_source("actions.js"))
    declared = re.findall(r'^let haltToken = "";$', _comments_stripped(_source("actions.js")), re.M)
    assert len(declared) == 1
    assert "headers" not in actions.lower()
    transport = _code_only(_source("api.js"))
    assert not re.search(r"\b(let|const) haltToken\b", transport)

    halt_senders = {
        name: _comments_stripped(_source(name)).count(f'"{security.HALT_HEADER}"')
        for name in _MODULES
    }
    assert {name: n for name, n in halt_senders.items() if n} == {"api.js": 1}
    gate_senders = {
        name: _comments_stripped(_source(name)).count(security.GATES_HEADER)
        for name in (*_MODULES, "../sw.js")
    }
    assert sum(gate_senders.values()) == 0, gate_senders
    holders = {name: _code_only(_source(name)).count("data.halt_token") for name in _MODULES}
    assert {name: n for name, n in holders.items() if n} == {"actions.js": 2}
    assert not any(re.search(r"\bdata\.gate\b", _code_only(_source(n))) for n in _MODULES)


def test_api_js_posts_to_exactly_two_paths() -> None:
    paths = re.findall(r'API_PREFIX \+ "([^"]+)"', _comments_stripped(_source("api.js")))

    # `read` builds its URL from a variable; the two literals are the two writes.
    assert sorted(paths) == ["gates/halt", "setup/"]
    assert len(paths) == 2


def _async_function(source: str, name: str) -> str:
    """One top-level `async function` of `api.js`, from its declaration to its closing brace at
    column 0. `_function_body`'s end markers do not know `async`, so they would run on past it."""
    code = _comments_stripped(source)
    for marker in (f"export async function {name}(", f"async function {name}("):
        if marker in code:
            start = code.index(marker)
            break
    else:
        raise AssertionError(f"api.js declares no async function {name}")
    end = code.index("\n}\n", start)
    return code[start:end]


def test_the_halt_sends_no_body_because_its_route_reads_none() -> None:
    """Found by the PR1 browser check, not by reading: the halt POST carried `{}` and
    `server._gated_action` never reads a body for the halt, so on a kept-alive connection the two
    unread bytes were parsed as the start of the NEXT request line -- the repaint's
    `GET /api/pulse` arrived as `{}GET` and was refused 501. A write whose route reads no body
    must send none; the setup actions, whose route does read one, still send theirs."""
    api = _source("api.js")
    halt = _async_function(api, "runHalt")
    assert halt.count("post(") == 1
    # The halt token rides `security.HALT_HEADER` -- the server's own constant, read here rather
    # than restated, so a header renamed on one side only fails this pin.
    sent = re.findall(r'return post\(url, \{ "([^"]+)": haltToken \}\);', halt)
    assert sent == [security.HALT_HEADER], halt

    run_action = _async_function(api, "runAction")
    assert re.search(r'return post\(url, \{ "X-Keel-CSRF": csrf \}, values\);', run_action)

    post = _async_function(api, "post")
    guard = post.index("if (body !== undefined) {")
    block = post[guard : post.index("\n  }", guard)]
    # The body AND its content type exist only inside the guard, each exactly once.
    assert post.count("JSON.stringify(body)") == 1 and "JSON.stringify(body)" in block
    assert post.count('"Content-Type"') == 1 and '"Content-Type"' in block


def test_the_pulse_view_places_every_section_once() -> None:
    """Every section builder is called exactly once, and the set is DERIVED from the source.

    A hand-listed tuple drops a section silently (`pulseRules` was missing from one, so deleting
    its call passed the suite): the expected names are every `pulse*` declaration but `pulseView`
    itself, plus `haltControl`, and what `pulseView` calls must equal that set.
    """
    code = _comments_stripped(_source("render.js"))
    body = _function_body(_source("render.js"), "pulseView")
    declared = set(re.findall(r"^function (pulse[A-Za-z]+)\(", code, re.M)) | {"haltControl"}
    declared.discard("pulseView")
    called = re.findall(r"fragment\.append\((\w+)\(data\)\)", body)

    assert len(declared) == 10, sorted(declared)
    assert sorted(called) == sorted(declared)


def test_the_empty_cycles_card_is_the_services_sentence_on_its_presence() -> None:
    """R2-F1's client half: the card shows on the PRESENCE of `data.no_cycles_line`, and its
    sentence is the parameter. The engine claim is judged in Python, per basis; if the client
    words it or branches on the emptiness of a list, an unread log reads as "keel has not run"."""
    source = _source("render.js")
    cycles = _function_body(source, "pulseCycles")
    today = _function_body(source, "noCyclesToday")

    assert re.findall(r"\bif \(([^\n]*)\) fragment\.append\(noCyclesToday", cycles) == [
        'noCycles !== ""'
    ]
    assert re.findall(r"const noCycles = ([^;]*);", cycles) == ["plain(data.no_cycles_line)"]
    assert len(re.findall(r"noCyclesToday\(", cycles)) == 1
    assert re.findall(r'el\("strong", undefined, ([^)]*)\)', today) == ["line"]
    code = _comments_stripped(source).lower()
    assert "has not run" not in code
    assert "no cycles today" not in code


def test_pulse_tables_pair_headers_and_cells() -> None:
    """The generic pairing scan (`test_every_table_emits_one_cell_per_declared_header`) covers
    the pulse tables once they are found in its input; this pins that they are found."""
    from tests.web.test_client_assets import _table_calls

    region = _code_only(_pulse_region())
    pairs = _table_calls(region)

    assert len(pairs) >= 4, f"the pulse region holds {len(pairs)} tables"
    assert [pair for pair in pairs if pair[0] != pair[1]] == []


def test_the_manifest_starts_at_pulse() -> None:
    manifest = json.loads((_STATIC / "manifest.webmanifest").read_text(encoding="utf-8"))

    assert manifest["start_url"] == "/pulse"
    assert manifest["id"] == "/"


# -- HALT survives a Pulse that cannot be built (#958) ---------------------------------------------


def test_every_reading_carries_the_halt_key_and_only_a_failure_reads_it() -> None:
    """A `Reading` is the client's whole picture of one answer, so the failure document's `halt`
    must survive `readingFrom` -- a key dropped there is `undefined`, which the page treats as
    "no halt to offer", and a broken Pulse would hide the stop again. Every Reading literal in
    every module carries the key (paired with `attestations`, which every literal has), and the
    only one that READS it off a document is `readingFrom`'s failure arm."""

    def keys(name: str, key: str) -> int:
        # A Reading key sits on a line of its own; `{ halt: reading.halt }` inline is an argument.
        return len(re.findall(rf"^\s+{key}: ", _comments_stripped(_source(name)), re.M))

    literals = {name: keys(name, "attestations") for name in _MODULES}
    halts = {name: keys(name, "halt") for name in _MODULES}
    assert sum(literals.values()) >= 7, "the scan found no Reading literals; it has stopped working"
    assert halts == literals

    api = _comments_stripped(_source("api.js"))
    reading_from = _function_body(_source("api.js"), "readingFrom")
    failure_arm = reading_from[reading_from.index("if (!ok) {") : reading_from.index("\n  }\n")]
    assert api.count("document_.halt") == 1
    assert failure_arm.count("halt: document_.halt ?? null") == 1


def _paint() -> str:
    code = _comments_stripped(_source("main.js"))
    start = code.index("async function paint(")
    return code[start : code.index("\n}\n", start)]


def test_a_failed_pulse_paints_the_halt_before_the_stopped_view() -> None:
    """In `paint`'s `data: null` branch, a Pulse failure that carries `halt` gets its own view --
    after the 403 branch (a refusal carries `halt: null` and needs the reconnect field), before
    the stopped view (which offers no stop). The halt token is taken off that failure exactly as
    `mount` takes it off a built report: through `rememberHaltToken`, the one holder."""
    paint = _paint()
    start = paint.index("if (primary.data === null) {")
    null_branch = paint[start : paint.index("} else {\n      rebuildInto(mount(", start)]

    refused = null_branch.index("primary.error.status === REFUSED_STATUS")
    failed = null_branch.index('} else if (route.name === "pulse" && primary.halt) {')
    stopped = null_branch.index("stoppedView(primary")
    assert refused < failed < stopped
    arm = null_branch[failed:stopped]
    assert arm.count("rememberHaltToken(primary.halt);") == 1
    assert arm.count("rebuildInto(pulseFailedView(primary), Boolean(force));") == 1
    assert arm.index("rememberHaltToken(") < arm.index("pulseFailedView(")

    main = _code_only(_source("main.js"))
    assert len(re.findall(r"\brememberHaltToken\(", main)) == 2, "mount + the failure arm, no third"


def test_the_failed_pulse_view_is_the_halt_control_and_the_error_only() -> None:
    """`pulseFailedView` reuses `haltControl` -- the SAME form, button and HALTED line, so the
    stop on a broken page is the stop on a working one -- with the failure's own `halt` in the
    shape `haltControl` reads. HALT first, then why the rest is missing; none of the report's
    sections, which have nothing to render. No extra confirm and no phrase: stopping is never
    slower than starting, and the one confirm lives in `main.js`."""
    body = _function_body(_source("render.js"), "pulseFailedView")

    assert body.count("haltControl(") == 1
    assert re.findall(r"haltControl\(([^)]*)\)", body) == ["{ halt: reading.halt }"]
    assert body.count('el("h1"') == 1
    assert body.index("haltControl(") < body.index("reading.error")
    inside = body[body.index("{") :]
    assert not re.search(r"\bpulse[A-Z]\w*\(", inside), "no report section on a failed report"
    assert "confirm" not in body
    render = _code_only(_source("render.js"))
    assert len(re.findall(r"\bhaltControl\(", render)) == 3, "declaration + pulseView + failed"
    assert "export function pulseFailedView(" in _comments_stripped(_source("render.js"))
