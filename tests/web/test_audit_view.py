"""The Audit & Setup view (console PR4) -- the tamper-evident timeline, the setup checklist and
venue readiness, on one page -- and the four-view nav it completes.

It absorbed three views: Timeline (now opening on keel's own record, `kind=keel`), Setup (the
steps, the three stage tabs, the job panel and the CSRF write token) and Venues (readiness only;
the declarations stay with `keel brokers list`). Balances and Plans left the console. What is
pinned HERE is the shape of the client, over its SOURCE TEXT (there is no JavaScript runtime in
this suite):

* the nav is exactly Pulse, Holdings, Rules, Audit & Setup, then the Docs link;
* the Audit route reads `/api/setup` FIRST, so the stopped view's "Set keel up on this machine"
  lands somewhere that answers on a machine with nothing on it;
* each section is placed once, in order, and each reading reaches the parameter named for it;
* every table pairs each header with ITS OWN cell;
* a secondary read that failed says so, and returns before reading a key;
* the retired views are declared nowhere, and the three whose content moved here route here.
"""

from __future__ import annotations

import re
from pathlib import Path

from keel.web import staticfiles
from tests.web.test_client_assets import (
    _balanced_block,
    _code_only,
    _comments_only,
    _function_body,
)
from tests.web.test_holdings_view import _split_top_level
from tests.web.test_rules_view import _assert_pairs, _table_ids

_STATIC = Path(staticfiles.__file__).parent / "static"
_JS = _STATIC / "js"
_INDEX = _STATIC / "index.html"


def _source(name: str) -> str:
    return (_JS / name).read_text(encoding="utf-8")


def _builder(name: str) -> str:
    """One function's body, comments stripped and string literals KEPT."""
    return _function_body(_source("render.js"), name)


def _route_entries() -> list[tuple[str, str, list[str]]]:
    """`(name, label, endpoints)` for every entry of `main.js`'s `ROUTES`, in order."""
    code = _comments_only(_source("main.js"))
    table = code[code.index("const ROUTES = [") : code.index("];", code.index("const ROUTES"))]
    return [
        (name, label, [part.strip().strip('"') for part in endpoints.split(",")])
        for name, label, endpoints in re.findall(
            r'\{ name: "([a-z]+)", label: "([^"]+)", endpoints: \[([^\]]*)\] \}', table
        )
    ]


def _audit_branch() -> str:
    code = _comments_only(_source("main.js"))
    branch = re.search(r'if \(route\.name === "audit"\) \{(.*?)\n  \}', code, re.S)
    assert branch is not None, 'main.js has no `route.name === "audit"` branch'
    return branch.group(1)


# -- the nav ----------------------------------------------------------------------------------


def test_the_nav_is_exactly_the_four_views_and_docs() -> None:
    """The milestone's Done-means: Pulse, Holdings, Rules, Audit & Setup, plus the Docs link --
    and the three tables that spell it (main.js, staticfiles, index.html) agree, label by label."""
    html = _INDEX.read_text(encoding="utf-8")
    nav = html[html.index('<nav aria-label="Views">') : html.index("</nav>")]
    nav = re.sub(r"<!--.*?-->", "", nav, flags=re.S)
    items = re.findall(r"<li>(.*?)</li>", nav, re.S)

    views = [re.fullmatch(r'<a href="([^"]+)">([^<]+)</a>', item) for item in items[:-1]]
    assert all(views), items
    assert [(m.group(1), m.group(2)) for m in views if m] == [
        ("/pulse", "Pulse"),
        ("/holdings", "Holdings"),
        ("/rules", "Rules"),
        ("/audit", "Audit &amp; Setup"),
    ]
    assert len(items) == 5
    assert 'id="docs-link"' in items[-1]
    assert [(name, label) for name, label, _ in _route_entries()] == [
        ("pulse", "Pulse"),
        ("holdings", "Holdings"),
        ("rules", "Rules"),
        ("audit", "Audit & Setup"),
    ]
    assert staticfiles.CLIENT_ROUTES == ("pulse", "holdings", "rules", "audit")


def test_the_audit_route_reads_setup_first_then_the_timeline_then_the_venues() -> None:
    """`/api/setup` is the PRIMARY reading (planner ruling 14): it is `needs_database=False`, so a
    machine with nothing on it still gets the checklist -- where a timeline primary would answer
    `data: null` and paint "keel isn't running" over the page that fixes it."""
    entries = {name: endpoints for name, _label, endpoints in _route_entries()}

    assert entries["audit"] == ["setup", "timeline", "venues"]


def test_the_stopped_view_lands_on_audit() -> None:
    code = _code_only(_source("main.js"))

    assert code.count("const SETUP_ROUTE = ") == 1
    assert (
        'const SETUP_ROUTE = ROUTES.find((route) => route.name === "audit") ?? DEFAULT_ROUTE;'
        in _comments_only(_source("main.js"))
    )
    assert code.count("stoppedView(primary, pathFor(SETUP_ROUTE))") == 1


def test_the_views_whose_content_moved_here_route_here() -> None:
    """A bookmark, a runbook line or an operator's habit naming `/setup`, `/timeline` or
    `/venues` lands on Audit & Setup, where that content now lives -- not on Pulse, where it does
    not. Balances and Plans moved nowhere, so they keep landing on the home view."""
    code = _comments_only(_source("main.js"))
    block = re.search(r"const MOVED_ROUTES = \{(.*?)\};", code, re.S)
    assert block is not None, "main.js names no moved routes"
    moved = dict(re.findall(r'(\w+): "(\w+)"', block.group(1)))

    assert moved == {"setup": "audit", "timeline": "audit", "venues": "audit"}
    assert set(moved) <= set(staticfiles.RETIRED_CLIENT_ROUTES), "the server must serve the shell"
    assert set(moved.values()) <= set(staticfiles.CLIENT_ROUTES)
    # The lookup and its use, whole: a lookup whose result is discarded (`? asked : asked`) or a
    # find over the raw name both leave `/setup` landing on Pulse.
    route_for = re.sub(r"\s+", " ", _function_body(_source("main.js"), "routeFor"))
    lookup = (
        "const name = Object.prototype.hasOwnProperty.call(MOVED_ROUTES, asked) "
        "? MOVED_ROUTES[asked] : asked;"
    )
    assert route_for.count(lookup) == 1, "routeFor does not resolve a moved name"
    assert (
        route_for.count("return ROUTES.find((route) => route.name === name) ?? DEFAULT_ROUTE;") == 1
    )


def test_the_timeline_defaults_to_keel_only() -> None:
    """Seeded once at load, in the top-level block that seeds the DCA card's address inputs -- the
    console opens on keel's own record, `all` is one chip away, and the CLI and the API default
    (every kind) are untouched."""
    code = _comments_only(_source("main.js"))
    seed = 'paramsFor("timeline").kind = "keel";'
    blocks = re.findall(r"^\{\n(.*?)^\}\n", code, re.M | re.S)
    boot = [block for block in blocks if 'paramsFor("dca-plan")' in block]

    assert code.count(seed) == 1
    assert len(boot) == 1 and seed in boot[0]


# -- the mount ----------------------------------------------------------------------------------


def test_the_write_token_is_remembered_on_the_audit_mount_and_nowhere_else() -> None:
    """Setup's CSRF token travels only on `/api/setup`; the branch that mounts it keeps it."""
    mount = _function_body(_source("main.js"), "mount")

    assert len(re.findall(r"\bremember\(", mount)) == 1
    assert _audit_branch().count("remember(data);") == 1


def test_mount_hands_each_reading_to_the_parameter_named_for_it() -> None:
    """The pairing, not the count: `readings[i]` is `route.endpoints[i]`'s reading."""
    branch = _audit_branch()
    endpoints = {name: e for name, _label, e in _route_entries()}["audit"]
    locals_at = dict(re.findall(r"const (\w+) = readings\[(\d)\];", branch))

    assert {name: endpoints[int(index)] for name, index in locals_at.items()} == {
        "timeline": "timeline",
        "venues": "venues",
    }
    call, _after = _balanced_block(branch, branch.index("(", branch.index("return auditView(")))
    args = [re.sub(r"\s+", " ", arg).strip() for arg in _split_top_level("[" + call[1:-1] + "]")]
    assert args[:3] == ["data", "sectionOf(route)", "onSectionFor(route)"]
    assert args[3:5] == ["timeline ? timeline.data : null", "timeline ? timeline.error : null"]
    assert args[5] == "timeline ? timeline.sort : null"
    assert args[6] == 'sorter(route, "timeline")'
    assert args[7].startswith("(kind) => {") and 'paramsFor("timeline").kind = kind;' in args[7]
    assert args[8:] == ["venues ? venues.data : null"]


def test_the_audit_view_signature_names_each_reading() -> None:
    code = _code_only(_source("render.js"))
    match = re.search(r"export function auditView\(([^)]*)\)", code)
    assert match is not None
    assert [part.strip() for part in match.group(1).split(",") if part.strip()] == [
        "setup",
        "section",
        "onSection",
        "timeline",
        "timelineError",
        "timelineSort",
        "onTimelineSort",
        "onKind",
        "venues",
    ]


# -- the page -----------------------------------------------------------------------------------


def test_audit_places_its_three_sections_once_in_page_order() -> None:
    body = _builder("auditView")
    calls = re.findall(r"\b(timelineSection|setupSection|readinessSection)\(([^)]*)\)", body)

    assert calls == [
        ("timelineSection", "timeline, timelineError, timelineSort, onTimelineSort, onKind"),
        ("setupSection", "setup, section, onSection"),
        ("readinessSection", "venues"),
    ]
    assert re.findall(r'el\("h1", undefined, "([^"]+)"\)', body) == ["Audit & Setup"]


def test_no_section_writes_a_second_page_heading() -> None:
    for name in ("timelineSection", "setupSection", "readinessSection"):
        assert '"h1"' not in _builder(name), name


def test_every_heading_id_on_the_audit_page_is_unique() -> None:
    names = ("auditView", "timelineSection", "setupSection", "readinessSection")
    ids: list[str] = []
    for name in names:
        ids += re.findall(r'heading\("([^"]+)"', _builder(name))

    assert {"h-audit-timeline", "h-timeline", "h-setup", "h-readiness"} <= set(ids), ids
    assert len(ids) == len(set(ids)), sorted(ids)


_TIMELINE_PAIRS = (
    ("when (UTC)", "at"),
    ("kind", "kind"),
    ("how we know", "provenance"),
    ("source", "source"),
    ("reference", "reference"),
    ("product", "product_id"),
    ("amount", "amount"),
    ("what", "summary"),
    ("row hash", "row_hash"),
    ("tamper-evidence", "chain_status"),
)

_READINESS_PAIRS = (
    ("venue", "venue"),
    ("state", "state"),
    ("explanation", "explanation"),
    ("fix", "next_step"),
)


def test_each_timeline_header_sits_over_its_own_cell() -> None:
    _assert_pairs(_builder("timelineSection"), "h-timeline", _TIMELINE_PAIRS)


def test_each_readiness_header_sits_over_its_own_cell() -> None:
    _assert_pairs(_builder("readinessSection"), "h-readiness", _READINESS_PAIRS)


def test_the_readiness_table_is_the_only_venue_table() -> None:
    """Planner ruling 16: `/api/venues` is kept whole, but the page renders readiness only -- the
    capability declarations live in `keel brokers list`."""
    assert _table_ids(_builder("readinessSection")) == ["h-readiness"]
    render = _code_only(_source("render.js"))
    for gone in ("venueCard",):
        assert not re.search(rf"\b{gone}\b", render), gone
    raw = _comments_only(_source("render.js"))
    assert '"h-declared"' not in raw and '"h-venues"' not in raw


def test_the_job_panel_stays_with_the_setup_section() -> None:
    body = _builder("setupSection")
    assert body.count("if (data.job) fragment.append(jobPanel(data.job));") == 1
    assert "jobPanel(" not in _builder("auditView")


def test_the_kind_chips_are_drawn_and_the_export_carries_the_page_kind() -> None:
    body = _builder("timelineSection")

    assert body.count("kindSwitch(plain(data.kind), data.kinds || [], onKind)") == 1
    assert body.count('target.searchParams.set("kind", plain(data.kind));') == 1
    assert body.count('target.searchParams.set("scope", plain(data.scope));') == 1


# -- a secondary read that failed --------------------------------------------------------------


def _guard(name: str, param: str) -> str:
    body = _builder(name)
    guard = re.search(rf"if \({param} === null\) \{{", body)
    assert guard is not None, f"{name} has no `if ({param} === null)` guard"
    start = body.index("{", guard.start())
    depth = 0
    for index in range(start, len(body)):
        depth += {"{": 1, "}": -1}.get(body[index], 0)
        if depth == 0:
            return body[start : index + 1]
    raise AssertionError(name)


def test_a_failed_timeline_read_says_so_and_returns_before_reading_a_key() -> None:
    guard = _guard("timelineSection", "data")

    assert guard.count("return fragment;") == 1
    assert "could not be read" in guard
    assert "no database" in guard
    assert re.search(r"\bdata\.", guard) is None


def test_a_failed_readiness_read_says_so_and_returns_before_reading_a_key() -> None:
    guard = _guard("readinessSection", "data")

    assert guard.count("return fragment;") == 1
    assert "could not be read" in guard
    assert re.search(r"\bdata\.", guard) is None


# -- what left ----------------------------------------------------------------------------------


def test_the_retired_views_and_their_helpers_are_declared_nowhere() -> None:
    gone = (
        "setupView",
        "timelineView",
        "balancesView",
        "venuesView",
        "venueCard",
        "plansView",
        "claimList",
        "citation",
    )
    for source in ("render.js", "main.js"):
        code = _code_only(_source(source))
        for name in gone:
            assert not re.search(rf"\b{name}\b", code), (source, name)


def test_the_retired_routes_are_in_no_live_table() -> None:
    main = _comments_only(_source("main.js"))
    for name in ("setup", "timeline", "balances", "venues", "plans"):
        assert name not in staticfiles.CLIENT_ROUTES
        assert name in staticfiles.RETIRED_CLIENT_ROUTES
        assert f'route.name === "{name}"' not in main


def test_no_stylesheet_rule_is_left_for_the_retired_markup() -> None:
    """`.venue` (the declaration disclosure), `.declared` (its lists) and `.claims` (the Plans
    quotes) were drawn only by the views this PR removed."""
    css = (_STATIC / "css" / "keel.css").read_text(encoding="utf-8")
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    selectors = [s.strip() for block in re.findall(r"([^{}]+)\{", css) for s in block.split(",")]
    for gone in (".venue", ".declared", ".claims"):
        assert not any(re.search(rf"{re.escape(gone)}\b", s) for s in selectors), gone
