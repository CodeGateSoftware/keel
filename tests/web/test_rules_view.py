"""The Rules view after console PR3 -- the ledger, the track records, the DCA card and the research
record, on one page.

It absorbed the Insights view (track records and promotion-gate distances) and the Research view
(trials, evidence matrix, gauntlet, slippage). The charts, the closed-trade journal and the
discretionary-journal section did not come with it: they left the console (the services and the
CLI keep them). What is pinned HERE is the shape of the client, over its SOURCE TEXT (there is no
JavaScript runtime in this suite):

* every section is placed exactly once, in a fixed order, and every heading id is unique;
* every table pairs each header with ITS OWN cell, not merely as many cells as headers;
* the track records carry no sort control (a sorted record is a leaderboard), and neither does any
  research table;
* the research reads are SECONDARY: each one that failed is said so, and none takes the ledger
  down;
* `main.js` hands each reading to the parameter named for its endpoint.

The two views it replaced are declared nowhere, and their routes land on Pulse.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from keel.web import staticfiles
from tests.web.test_client_assets import (
    _balanced_block,
    _code_only,
    _comments_only,
    _comments_stripped,
    _function_body,
)
from tests.web.test_holdings_view import _split_top_level

_JS = Path(staticfiles.__file__).parent / "static" / "js"


def _source(name: str) -> str:
    return (_JS / name).read_text(encoding="utf-8")


def _builder(name: str) -> str:
    """One function's body, comments stripped and string literals KEPT."""
    return _function_body(_source("render.js"), name)


#: The Rules view's sections, in page order. `rulesView` calls each exactly once.
_SECTIONS = (
    "recordsSection",
    "dcaPlanCard",
    "trialsSection",
    "matrixSection",
    "gauntletSection",
    "slippageSection",
)

#: The four research sections, each handed the reading of its own endpoint.
_RESEARCH = (
    ("trialsSection", "trials"),
    ("matrixSection", "matrix"),
    ("gauntletSection", "gauntlet"),
    ("slippageSection", "slippage"),
)


def _calls_in_order(body: str, names: tuple[str, ...]) -> list[str]:
    found = re.findall(r"\b(" + "|".join(names) + r")\(", body)
    return found


def test_the_rules_view_places_every_section_once_in_page_order() -> None:
    """Ledger, params, track records, DCA card, then the research record. A section dropped from
    the view still reads its keys from inside the region, so only the call list can see it go."""
    body = _builder("rulesView")

    assert _calls_in_order(body, _SECTIONS) == list(_SECTIONS)
    assert body.count("paramsCard(") == 1


def test_each_research_section_is_handed_its_own_reading() -> None:
    body = _builder("rulesView")

    for section, reading in _RESEARCH:
        calls = re.findall(rf"\b{section}\(([^)]*)\)", body)
        assert calls == [reading], (section, calls)


def test_the_rules_view_signature_names_each_reading() -> None:
    code = _code_only(_source("render.js"))
    match = re.search(r"export function rulesView\(([^)]*)\)", code)
    assert match is not None
    params = [part.strip() for part in match.group(1).split(",")]
    assert params == [
        "data",
        "sort",
        "onSort",
        "plan",
        "planError",
        "trials",
        "gauntlet",
        "slippage",
        "matrix",
    ]


def test_every_heading_id_on_the_rules_page_is_unique() -> None:
    """Fourteen headings from three former views on one page. A repeated id breaks the in-page
    anchors, the disclosure restore (`rebuildInto` matches `summary[id]`) and `aria-labelledby` on
    the table that names it."""
    names = ("rulesView", "recordsSection", "dcaPlanCard", *(name for name, _ in _RESEARCH))
    ids: list[str] = []
    for name in names:
        ids += re.findall(r'heading\("([^"]+)"', _builder(name))

    assert len(ids) >= 11, ids
    assert len(ids) == len(set(ids)), sorted(ids)
    assert "h-research" in ids and "h-records" in ids


# -- the track records ------------------------------------------------------------------------


def _paired(body: str, table_id: str) -> list[tuple[str, str]]:
    """`(header label, cell expression)` for the `table("<table_id>", ...)` call in `body`."""
    found = re.search(rf'table\(\s*"{re.escape(table_id)}"', body)
    assert found is not None, table_id
    at = found.start()
    columns, after = _balanced_block(body, body.index("[", at))
    cells, _ = _balanced_block(body, body.index("[", body.index(".map(", after)))
    labels = [
        re.search(r'label:\s*"([^"]+)"', item).group(1)  # type: ignore[union-attr]
        for item in _split_top_level(columns)
    ]
    return list(zip(labels, _split_top_level(cells), strict=True))


def _table_ids(body: str) -> list[str]:
    return re.findall(r'table\(\s*"([^"]+)"', body)


def _assert_pairs(body: str, table_id: str, expected: tuple[tuple[str, str], ...]) -> None:
    pairs = _paired(body, table_id)
    assert [label for label, _ in pairs] == [label for label, _ in expected], table_id
    for (label, cell), (_, field) in zip(pairs, expected, strict=True):
        reads = re.findall(r"\brow\.(\w+)", cell)
        assert reads == [field], (table_id, label, cell, field)


_RECORD_PAIRS = (
    ("rule", "rule_name"),
    ("status", "status"),
    ("class", "promotion_class"),
    ("trades", "n_trades"),
    ("win rate", "win_rate"),
    ("avg win", "avg_win"),
    ("avg loss", "avg_loss"),
    ("realized rr", "realized_rr"),
    ("expectancy", "expectancy"),
    ("profit factor", "profit_factor"),
    ("max dd", "max_drawdown"),
    ("sample", "significant"),
)


def test_each_track_record_header_sits_over_its_own_cell() -> None:
    _assert_pairs(_builder("recordsSection"), "h-records", _RECORD_PAIRS)


def test_the_track_records_offer_no_sort_control() -> None:
    """Ruling 10: a track record ordered by expectancy is a leaderboard. `table()` draws a sort
    header only for a column with a `key` AND a sort pair; the section has neither, and the view
    hands it neither."""
    body = _builder("recordsSection")

    assert _table_ids(body) == ["h-records"]
    assert "key:" not in body
    assert "onSort" not in body and "sort:" not in body
    assert re.findall(r"\brecordsSection\(([^)]*)\)", _builder("rulesView")) == ["data"]


def test_the_gate_cards_are_drawn_on_the_presence_of_a_gate() -> None:
    """`GateDistance` is `None` for every status but `paper`, so the branch is the presence of the
    payload's object -- never a figure read and compared here."""
    body = _builder("recordsSection")

    assert body.count("gateCard(") == 1
    assert re.findall(r"Boolean\((\w+)\.gate\)", body) == ["row"]
    assert ".value" not in body


def test_every_gate_card_gets_an_id_of_its_own() -> None:
    """A track record is named by its rule KIND, and a ledger can hold several paper rules of one
    kind (the browser check found five `turtle_breakout` cards). An id built from the name alone
    repeats, and `rebuildInto` restores open disclosures by `summary[id]` -- so opening one card
    reopened all five on the next poll. The id carries the card's position as well."""
    section = _builder("recordsSection")
    card = _builder("gateCard")

    assert re.findall(r"function gateCard\((\w+), (\w+)\)", _code_only(_source("render.js"))) == [
        ("rule", "position")
    ]
    assert re.findall(r"for \(const \[(\w+), (\w+)\] of gated\.entries\(\)\)", section) == [
        ("index", "row")
    ]
    assert re.findall(r"gateCard\(([^)]*)\)", section) == ["row, index"]
    ids = re.findall(r"summary\.id = (.*);", card)
    assert ids == ['["h-gatedist", plain(gate.rule_name), String(position)].join("-")'], ids


_GATE_PAIRS = (
    ("class", "promotion_class"),
    ("trades", "n_trades"),
    ("trades needed", "min_trades"),
    ("trades remaining", "trades_remaining"),
    ("win rate", "win_rate"),
    ("win rate floor", "min_win_rate"),
    ("realized rr", "realized_rr"),
    ("rr floor", "min_rr"),
    ("expectancy", "expectancy"),
    ("expectancy floor", "min_expectancy"),
)


def test_each_gate_figure_is_labelled_as_itself() -> None:
    """The gate card is a pair per figure -- what the rule has, and the floor. Swapping a figure
    with its floor keeps every count and reads as the opposite verdict."""
    body = _builder("gateCard")
    grid = body[body.index("gridCard(") :]
    block, _ = _balanced_block(grid, grid.index("["))
    found = re.findall(r'kv\("([^"]+)",\s*(?:plain\()?gate\.(\w+)\)?\)', block)

    assert found == list(_GATE_PAIRS)


# -- the research record ----------------------------------------------------------------------


_EXPLORED_PAIRS = (
    ("rule", "rule"),
    ("trials here", "trials"),
    ("cells declared", "declared_cells"),
)

_TRIAL_PAIRS = (
    ("when (UTC)", "at"),
    ("rule", "rule"),
    ("kind", "kind"),
    ("outcome", "decision"),
    ("how the parameters were chosen", "provenance"),
    ("series", "series"),
    ("parameters", "params"),
    ("summary", "summary"),
    ("row hash", "row_hash"),
)

_MATRIX_PAIRS = (
    ("when (UTC)", "at"),
    ("session", "session"),
    ("columns", "n_columns"),
    ("blocks", "n_blocks"),
    ("combinations", "n_combinations"),
    ("rows used", "rows_used"),
    ("rows dropped", "rows_dropped"),
    ("PBO", "pbo"),
    ("degradation slope", "degradation_slope"),
    ("P[OOS < 0]", "prob_loss"),
    ("1st-order dominance", "dominance_1st"),
    ("2nd-order dominance", "dominance_2nd"),
)

_GAUNTLET_PAIRS = (
    ("when (UTC)", "at"),
    ("trial", "trial_id"),
    ("rule", "rule"),
    ("session", "session"),
    ("ran", "ran"),
    ("PBO", "pbo"),
    ("train expectancy", "train_expectancy"),
    ("held out", "held_out_expectancy"),
    ("bars", "bars"),
    ("seed", "seed"),
    ("gate", "gate_passed"),
)

_SLIPPAGE_PAIRS = (
    ("product", "product_id"),
    ("assumed bp per leg", "slippage_bp"),
    ("vs floor", "floor_multiple"),
    ("median daily quote volume", "median_daily_quote_volume"),
    ("daily bars", "bars"),
    ("priced from", "fallback"),
    ("cap", "capped"),
)


@pytest.mark.parametrize(
    ("section", "table_id", "expected"),
    [
        ("trialsSection", "h-explored", _EXPLORED_PAIRS),
        ("trialsSection", "h-trials", _TRIAL_PAIRS),
        ("matrixSection", "h-matrix", _MATRIX_PAIRS),
        ("gauntletSection", "h-gauntlet", _GAUNTLET_PAIRS),
        ("slippageSection", "h-slippage", _SLIPPAGE_PAIRS),
    ],
)
def test_each_research_header_sits_over_its_own_cell(
    section: str, table_id: str, expected: tuple[tuple[str, str], ...]
) -> None:
    _assert_pairs(_builder(section), table_id, expected)


@pytest.mark.parametrize(("section", "reading"), _RESEARCH)
def test_a_research_read_that_failed_is_said_so_in_its_own_section(
    section: str, reading: str
) -> None:
    """Every research read is SECONDARY on Rules, so each section is handed `null` when its read
    failed. The branch is on that presence, it comes before any key is read, and it says so in
    words rather than drawing an empty table that reads as "nothing recorded"."""
    body = _builder(section)
    guard = f"if (!{reading})"

    assert body.count(guard) == 1, section
    first_read = re.search(rf"\b{reading}\.\w+", body)
    assert first_read is not None
    assert body.index(guard) < first_read.start(), section
    branch = body[body.index(guard) : body.index("return fragment;", body.index(guard))]
    assert "could not be read" in branch, section


@pytest.mark.parametrize(("section", "reading"), _RESEARCH)
def test_a_failed_research_read_returns_before_reading_any_key(section: str, reading: str) -> None:
    """The guard block is exactly `{ <append the sentence>; return fragment; }`.

    The test above bounds its slice at the NEXT `return fragment;`, which after a deleted guard
    `return` is the function's final one -- so the sentence is still "in" the branch while the
    function carries on into `<reading>.state` on `null`, throws, and takes the whole Rules view
    (ledger, records, DCA card) down with it. Here the block is cut by its own braces: it ends in
    its own `return fragment;`, has only one, and reads no key of the missing reading."""
    body = _builder(section)
    guard = f"if (!{reading})"
    assert body.count(guard) == 1, section

    block, _ = _balanced_block(body, body.index("{", body.index(guard)))

    assert block.rstrip().rstrip("}").rstrip().endswith("return fragment;"), section
    assert block.count("return fragment;") == 1, section
    assert "could not be read" in block, section
    assert re.search(rf"\b{reading}\.\w+", block) is None, section


#: The sentence that opens the research record: it claims a record that exists and was read.
_RESEARCH_INTRO = "Every trial keel has run"

#: Every builder of the Rules view, ledger to DCA card to research record.
_RULES_BUILDERS = (
    "rulesView",
    "recordsSection",
    "samplePill",
    "gateCard",
    "paramsCard",
    "dcaPlanCard",
    "trialsSection",
    "matrixSection",
    "gauntletSection",
    "slippageSection",
)


def test_the_research_intro_is_placed_once_and_only_after_a_successful_read() -> None:
    """One copy, in the section that has the record in hand. `rulesView` once printed it too, so
    the reader saw it twice, and a failed trials read sat under a claim about a record that could
    not be read."""
    bodies = {name: _builder(name) for name in _RULES_BUILDERS}

    assert sum(body.count(_RESEARCH_INTRO) for body in bodies.values()) == 1
    assert bodies["rulesView"].count(_RESEARCH_INTRO) == 0
    trials = bodies["trialsSection"]
    assert trials.count(_RESEARCH_INTRO) == 1
    assert trials.index("return fragment;") < trials.index(_RESEARCH_INTRO)


def test_the_rules_view_offers_no_action_outside_the_ledger_sort() -> None:
    """Read-only, over EVERY Rules builder -- not only the research sections and the DCA card.

    A "promote this rule" button beside a gate card that says "passing" is the most natural
    request this page will draw, and promotion happens in the CLI. The ledger's sort headers are
    drawn inside `table()` (outside these bodies), so the builders own no button at all."""
    bodies = {name: _builder(name) for name in _RULES_BUILDERS}
    assert len(bodies) == len(_RULES_BUILDERS) and all(len(b) > 100 for b in bodies.values())
    assert sum(len(re.findall(r"\bel\(", b)) for b in bodies.values()) >= 20, "the scan is vacuous"

    for name, body in bodies.items():
        code = _code_only(body)
        tags = set(re.findall(r'\bel\("([a-z0-9]+)"', body))
        assert not tags & {"button", "form", "input", "select", "textarea", "a"}, (name, tags)
        for token in ("addEventListener", "onclick", "onsubmit", "fetch(", "perform(", "submit"):
            assert token not in code, f"{name} builds something interactive: {token}"
        assert "button" not in body.lower(), name


def test_the_trials_section_reads_through_its_own_parameter_only() -> None:
    """Moved off `researchView(data, ...)`: in the Rules region `data` is `/api/rules`, so a stray
    `data.` read here would be checked against the wrong endpoint and pass or fail by accident."""
    code = _code_only(_source("render.js"))

    assert re.findall(r"function trialsSection\((\w*)\)", code) == ["trials"]
    assert re.search(r"\bdata\.", _code_only(_builder("trialsSection"))) is None


# -- the router -------------------------------------------------------------------------------


def _rules_endpoints() -> list[str]:
    code = _source("main.js")
    entry = re.search(r'\{ name: "rules", label: "Rules", endpoints: \[([^\]]*)\] \}', code)
    assert entry is not None
    return [part.strip().strip('"') for part in entry.group(1).split(",")]


def test_the_rules_route_reads_the_ledger_first_then_the_card_then_the_research_record() -> None:
    """The ledger is the PRIMARY reading: it decides the engine banner and `data === null`."""
    assert _rules_endpoints() == [
        "rules",
        "dca-plan",
        "research/trials",
        "research/gauntlet",
        "research/slippage",
        "research/matrix",
    ]


def test_mount_hands_each_research_reading_to_the_parameter_named_for_its_endpoint() -> None:
    """The pairing, not the count: `readings[i]` is `route.endpoints[i]`'s reading, so the index
    each local is read from must be the index of the endpoint it is named for. Swapping two
    indices keeps six readings and draws the gauntlet's rows under the slippage heading."""
    code = _comments_only(_source("main.js"))
    branch = re.search(r'if \(route\.name === "rules"\) \{(.*?)\n  \}', code, re.S)
    assert branch is not None
    locals_at = dict(re.findall(r"const (\w+) = readings\[(\d)\];", branch.group(1)))
    endpoints = _rules_endpoints()

    assert {name: endpoints[int(index)] for name, index in locals_at.items()} == {
        "planReading": "dca-plan",
        "trials": "research/trials",
        "gauntlet": "research/gauntlet",
        "slippage": "research/slippage",
        "matrix": "research/matrix",
    }
    call = re.search(r"return rulesView\(([^;]*)\);", branch.group(1))
    assert call is not None
    args = [re.sub(r"\s+", " ", arg).strip() for arg in _split_top_level("[" + call.group(1) + "]")]
    assert args == [
        "data",
        "primary.sort",
        "onSort",
        "planReading ? planReading.data : null",
        "planReading ? planReading.error : null",
        "trials ? trials.data : null",
        "gauntlet ? gauntlet.data : null",
        "slippage ? slippage.data : null",
        "matrix ? matrix.data : null",
    ]


@pytest.mark.parametrize("name", ["insights", "research"])
def test_the_absorbed_views_are_routed_nowhere_and_land_on_rules(name: str) -> None:
    """Retired, not deleted from the server's knowledge: the shell still answers the old path, and
    `routeFor` sends a bookmark to Rules -- where that content now lives (#969) -- through
    `MOVED_ROUTES`. Nothing in the client names the old view."""
    # Comments stripped, strings KEPT: `_code_only` blanks every literal, so a scan for
    # `name: "insights"` over it can never match anything and passes whatever main.js says.
    main = _comments_stripped(_source("main.js"))
    block = re.search(r"const MOVED_ROUTES = \{(.*?)\};", main, re.S)
    assert block is not None
    moved = dict(re.findall(r'(\w+): "(\w+)"', block.group(1)))
    routes = main[main.index("const ROUTES = [") : main.index("];", main.index("const ROUTES"))]

    assert moved[name] == "rules"
    assert routes.count('name: "rules"') == 1, "the scan reads string literals"
    assert name not in staticfiles.CLIENT_ROUTES
    assert name in staticfiles.RETIRED_CLIENT_ROUTES
    assert f'name: "{name}"' not in routes
    assert f'route.name === "{name}"' not in main
    assert f"export function {name}View(" not in _source("render.js")


# -- what left with the charts ---------------------------------------------------------------------


def _css_selectors() -> list[str]:
    css = (Path(staticfiles.__file__).parent / "static" / "css" / "keel.css").read_text("utf-8")
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return [sel.strip() for block in re.findall(r"([^{}]+)\{", css) for sel in block.split(",")]


def test_no_stylesheet_rule_is_left_for_the_retired_charts() -> None:
    """The `.chart*` rules, the chart-action buttons and the journal rows' `data-point-index`
    highlight styled elements only `chart.js` and the Insights view built. Left behind they are
    rules with no element, which reads to the next editor as a feature that still exists."""
    selectors = _css_selectors()

    assert len(selectors) >= 100, len(selectors)
    stale = [
        sel
        for sel in selectors
        if re.search(r"\.chart\b|\.chartactions?\b|data-point-index|svg\.(curve|series)", sel)
    ]
    assert stale == []


def test_the_client_builds_no_chart_hook() -> None:
    """The other half: no module writes the class names or attributes those rules matched."""
    for name in ("render.js", "main.js"):
        code = _source(name)
        for hook in ('"chart', "data-chart-action", "data-point-index", "figure.chart"):
            assert hook not in code, (name, hook)
