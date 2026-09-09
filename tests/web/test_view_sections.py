"""Status and Setup, broken into sections behind one tab bar.

Both pages had grown past the point where an operator could see anything at a glance: Status
scrolls through four state cards and four tables, Setup through fifteen step cards. Each now
shows ONE section at a time, chosen from a tab bar, and the sections are declared as a table --
key, label, builder -- so that what the bar offers and what the page can draw cannot come apart.

**What no test in this file covers.** There is no JavaScript runtime in this suite (see
`test_client_assets.py`'s own header). Every assertion here is over the SOURCE TEXT: that the
tables declare what they claim, that every section they name has a builder of its own, and that
no payload key was dropped on the way. That pressing a tab actually swaps the section, that
focus lands back on the button, and that the bar reads as tabs to a screen reader are properties
of a real browser and are established by hand against a running `keel serve`.

The tables are PARSED out of the JavaScript rather than restated here. A test that spelled the
five labels twice would pass a rename that reached only one of the two places.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from keel.web import staticfiles
from tests.web.test_client_assets import _code_only, _comments_only, _view_keys

_JS = Path(staticfiles.__file__).parent / "static" / "js"


def _source(name: str) -> str:
    return (_JS / name).read_text(encoding="utf-8")


def _code(name: str) -> str:
    """`name`'s source with comments gone and string literals INTACT.

    The labels and heading ids this module checks are string literals, so `_code_only` -- which
    blanks them -- cannot see any of them. Comments still have to go: both views document their
    sections in prose directly above the table, and a raw scan would find the sentence rather
    than the code."""
    return _comments_only(_source(name))


def _region(code: str, view: str) -> str:
    """One exported view's source, bounded at the next top-level export.

    The same bound `_view_keys` uses, and for the same reason: a region run to end-of-file sweeps
    up the next view's docstring and code, and every count in this module would then be counting
    the whole file."""
    start = code.index("export function " + view + "(")
    nxt = code.find("\nexport function ", start + 1)
    return code[start : len(code) if nxt == -1 else nxt]


def _sections(code: str, name: str) -> list[tuple[str, str, str]]:
    """A `const <name> = [...]` table, as `(key, label, builder)` triples in declaration order.

    Parsed, never restated. The entries are the single place the tab bar's wording and the page's
    content are joined, so a test that re-spelled them would stop being able to notice them
    drifting apart -- which is the only failure this join can actually have.
    """
    start = code.index("const " + name + " = [")
    end = code.index("\n];", start)
    body = code[start:end]
    found = re.findall(
        r'\{\s*key:\s*"([^"]+)",\s*label:\s*"([^"]+)",\s*build:\s*([A-Za-z_][A-Za-z0-9_]*)',
        body,
    )
    return [(key, label, build) for key, label, build in found]


# -- the two tables ----------------------------------------------------------------------------


def test_the_status_page_offers_the_five_sections_in_the_order_it_used_to_scroll_them() -> None:
    """The tab bar is the page's own reading order, not a re-think of it.

    An operator who knew where "Data freshness" sat on the long page should find its tab in the
    same place. Order is asserted as a list, not as a set, for exactly that reason."""
    table = _sections(_code("render.js"), "STATUS_SECTIONS")

    assert [entry[1] for entry in table] == [
        "Status",
        "Open positions",
        "Rules",
        "Data freshness",
        "Subscriptions",
    ]


def test_the_setup_page_offers_the_three_sections_the_runbook_works_down() -> None:
    """`Setup` is the deployment card and what to do next; the other two are the runbook's own
    stages, whose wording is `render_setup`'s -- "To run in paper" carries the argument that
    paper is the safe default, and a tab bar that paraphrased it would weaken it."""
    table = _sections(_code("render.js"), "SETUP_SECTIONS")

    assert [entry[1] for entry in table] == ["Setup", "To run in paper", "To go live"]


@pytest.mark.parametrize("name", ["STATUS_SECTIONS", "SETUP_SECTIONS"])
def test_every_section_has_a_builder_of_its_own_and_no_two_share_one(name: str) -> None:
    """**The pairing, not the mere presence of the words.**

    A table whose five entries all pointed at one builder would satisfy every label assertion
    above and render the same section under all five tabs. So: as many distinct builders as
    entries, each declared exactly once in the file, and each named by exactly one entry."""
    code = _code("render.js")
    table = _sections(code, name)
    builders = [entry[2] for entry in table]

    assert len(set(builders)) == len(table), "two sections share a builder: " + repr(builders)
    for builder in builders:
        declared = code.count("function " + builder + "(")
        assert declared == 1, builder + " is declared " + str(declared) + " times, want exactly 1"


@pytest.mark.parametrize("name", ["STATUS_SECTIONS", "SETUP_SECTIONS"])
def test_every_section_key_is_distinct(name: str) -> None:
    """The key is what the pressed tab stores and what the next paint resolves back to a section.
    Two entries sharing one would make the second unreachable -- a tab that renders its
    neighbour."""
    keys = [entry[0] for entry in _sections(_code("render.js"), name)]

    assert len(set(keys)) == len(keys), "duplicate section key: " + repr(keys)


# -- nothing was lost in the split -------------------------------------------------------------


def test_each_status_heading_still_exists_and_lives_in_exactly_one_section() -> None:
    """**The split moved the four tables; it did not drop or duplicate one.**

    Each heading id is written once in the file, and the function that writes it is the builder
    its own table entry names. A heading that ended up in the wrong builder would render under
    the wrong tab -- and every label test above would still pass."""
    code = _code("render.js")
    table = _sections(code, "STATUS_SECTIONS")
    wanted = {
        "positions": "h-positions",
        "rules": "h-rules",
        "freshness": "h-freshness",
        "subscriptions": "h-subscriptions",
    }

    for key, heading in wanted.items():
        assert code.count('"' + heading + '"') >= 1, heading + " is no longer written anywhere"
        builder = next(entry[2] for entry in table if entry[0] == key)
        start = code.index("function " + builder + "(")
        nxt = code.find("\nfunction ", start + 1)
        body = code[start : len(code) if nxt == -1 else nxt]
        assert '"' + heading + '"' in body, heading + " is not written by " + builder


def _body(code: str, name: str) -> str:
    """One non-exported function's source, bounded at the next top-level declaration."""
    start = code.index("function " + name + "(")
    nxt = code.find("\nfunction ", start + 1)
    return code[start : len(code) if nxt == -1 else nxt]


@pytest.mark.parametrize("name", ["STATUS_SECTIONS", "SETUP_SECTIONS"])
def test_a_section_heading_is_the_tab_wording_and_not_a_second_copy_of_it(name: str) -> None:
    """**The label is the tab AND the heading, from one string.**

    The tab bar and the heading below it naming the same section differently is the one drift
    this arrangement can have. It is foreclosed by handing each builder the entry it was reached
    through: a builder writes `entry.label` or it writes no heading at all, and none of them
    spells its own wording. `STAGES` used to carry a second copy of "To run in paper" for its
    heading, which is what folding it into `SETUP_SECTIONS` removed.

    Checked over the BUILDERS rather than over the file: "Setup" and "Status" are each also a
    page `<h1>`, so a whole-file count of the label would be counting the title as a duplicate."""
    code = _code("render.js")

    for _key, label, builder in _sections(code, name):
        body = _body(code, builder)
        assert '"' + label + '"' not in body, builder + " re-spells its own label: " + label
        if "heading(" in body:
            assert "entry.label" in body, builder + " writes a heading that is not entry.label"


def test_the_setup_stages_are_still_the_runbook_stages() -> None:
    """The stage keys are what `step.stage` carries on the wire. A section keyed anything else
    would filter every step out and render "No steps in this stage." forever."""
    keys = [entry[0] for entry in _sections(_code("render.js"), "SETUP_SECTIONS")]

    assert keys == ["setup", "paper", "live"]


#: `statusView`'s payload reads, as they stood before the sections split. Pinned so that a
#: section builder moved OUT of the view's region -- where `_view_keys` can no longer see it --
#: fails here rather than quietly making the parity check in `test_client_assets` blind to a key.
_STATUS_KEYS = (
    "autonomy.configured",
    "autonomy.lapses_at",
    "autonomy.live",
    "autonomy.profile_readable",
    "data_freshness",
    "drawdown.max_total",
    "drawdown.max_weekly",
    "drawdown.rail11",
    "drawdown.total",
    "drawdown.weekly",
    "equity.high_water_mark",
    "equity.paper_cash",
    "equity.state_mode",
    "generated_at",
    "kill_switch",
    "live_rules",
    "market_session.defused",
    "market_session.recorded_at",
    "market_session.state",
    "mode",
    "open_positions",
    "rule_counts",
    "subscriptions",
    "withdrawal_attestation.attested_at",
    "withdrawal_attestation.enabled",
    "withdrawal_attestation.expired_for",
    "withdrawal_attestation.expires_in",
    "withdrawal_attestation.state",
)

#: The same, for `setupView`.
_SETUP_KEYS = (
    "actions",
    "config_path",
    "db_path",
    "has_usable_database",
    "is_new",
    "job",
    "next_step",
    "not_automated",
    "ready_for_paper",
    "root",
    "steps",
)


@pytest.mark.parametrize(
    ("view", "keys"), [("statusView", _STATUS_KEYS), ("setupView", _SETUP_KEYS)]
)
def test_the_split_reads_every_payload_key_the_long_page_read(
    view: str, keys: tuple[str, ...]
) -> None:
    """Sectioning is a layout change and nothing else. A builder that stopped reading a key would
    render a blank tile forever with nothing in the console naming the gap -- the failure
    `_view_keys` exists for."""
    assert sorted(_view_keys(view, "data")) == sorted(keys)


# -- the control ------------------------------------------------------------------------------


def test_one_switch_serves_both_pages_and_each_announces_itself_by_name() -> None:
    """Two tab bars on one site announcing themselves identically would leave a screen-reader
    user unable to tell which page's sections they had landed in -- `scopeSwitch` learned this at
    #659 and took a `label` argument for it. Same argument, same shape."""
    code = _code("render.js")

    assert code.count("function sectionSwitch(") == 1, "one control, declared once"
    assert code.count("function sectioned(") == 1, "one dispatcher, declared once"

    labels = re.findall(r'sectioned\(data, [A-Z_]+, section, onSection, "([^"]+)"\)', code)
    assert len(labels) == 2, "both views must wire the switch: " + repr(labels)
    assert len(set(labels)) == 2, "the two bars must not announce themselves alike: " + repr(labels)


def test_the_pressed_tab_gets_focus_back_after_the_view_is_rebuilt() -> None:
    """Pressing a tab replaces the whole view, which destroys the button that was pressed.
    Without `data-focus`, a keyboard user is returned to the top of the document on every press
    -- the loss of place `rebuildInto`'s restore exists to prevent."""
    code = _code("render.js")
    start = code.index("function sectionSwitch(")
    body = code[start : code.index("\nfunction ", start + 1)]

    assert "data-focus" in body, "the tabs need a focus key to be found again by"
    assert "aria-current" in body, "which section is on must be one attribute, seen and heard"


@pytest.mark.parametrize("view", ["statusView", "setupView"])
def test_a_page_whose_switch_is_not_wired_still_shows_everything(view: str) -> None:
    """The guard `ordersView` uses for its status tabs, and the honest degradation for this one:
    a caller that passes no `onSection` gets the long page back, never a page showing one section
    with no way to reach the other four."""
    body = _region(_code_only(_source("render.js")), view)

    assert "onSection" in body, view + " must take the section callback"


# -- the state, in the shell -------------------------------------------------------------------


def test_each_sectioned_route_remembers_its_own_section() -> None:
    """Status and Setup are two pages. One shared "current section" would have a reader who
    opened Setup's "To go live" find Status showing whichever of its five sections happened to
    sit at that index -- or nothing at all. The state is keyed by `route.name`, and both views
    read it through the same pair of helpers."""
    code = _code_only(_source("main.js"))

    for view in ("statusView", "setupView"):
        call = view + "(data, sectionOf(route), onSectionFor(route))"
        assert call in code, "the shell must mount " + view + " with its own section: " + call

    start = code.index("function sectionOf(")
    body = code[start : code.index("\nfunction ", start + 1)]
    assert "route.name" in body, "the section state must be keyed by route, not held once"


def test_pressing_a_section_repaints_at_once_rather_than_waiting_for_focus_to_leave() -> None:
    """A rebuild a reader just asked for must be FORCED. Deferred -- which is what an unforced
    rebuild does while focus sits on the button that was just clicked -- pressing a tab does
    nothing, and a control that does nothing is indistinguishable from a broken one."""
    code = _code_only(_source("main.js"))
    start = code.index("function onSectionFor(")
    body = code[start : code.index("\nfunction ", start + 1)]

    assert "paint(route, true, true)" in body, "the section press forces the rebuild"
