"""Status and Setup, broken into sections behind one tab bar (#773), and the review that
followed it (#775).

Both pages had grown past the point where an operator could see anything at a glance: Status
scrolled through four state cards and four tables, Setup through fifteen step cards. Each now
shows ONE section at a time, chosen from a tab bar, and the sections are declared as a table --
key, label, builder -- so that what the bar offers and what the page can draw cannot come apart.

**What no test in this file covers.** There is no JavaScript runtime in this suite (see
`test_client_assets.py`'s own header). Every assertion here is over the SOURCE TEXT. That
pressing a tab actually swaps the section, that focus lands back on the button, and that the bar
reads as tabs to a screen reader are properties of a real browser and are established by hand
against a running `keel serve`.

── WHY THIS MODULE WAS REWRITTEN, AND THE RULE THAT CAME OUT OF IT ────────────────────────────

The first version of this file shipped with #773 and was mechanically worthless. A post-merge
mutation run put thirteen defects into the client and **twelve of them passed the suite** --
including a `sectioned` that resolves nothing, so every tab is a silent no-op; a `stageSteps`
that renders the paper checklist under "To go live"; and one shared `data-focus` key, so every
keyboard press dumps focus onto tab one. The one mutation that failed did so by accident, caught
by a payload-key pin rather than by any test aimed at it.

The cause was uniform: the assertions were `"onSection" in body` and `"data-focus" in body` --
**a source-text scan passes on a declaration alone**. `onSection` is in the signature. Naming an
identifier proves the author typed it, never that it is wired to anything.

So every test below asserts a COUNT or a PAIRING, and each names the mutation it exists to
reject. The harness that proves it is not in the repository -- it applies each defect to a copy
of the client, checks the mutated bytes actually reached disk (a no-op mutation looks exactly
like a killed one), runs this file, and restores. Its results are recorded in #775.

**It happened again, and that is the more useful lesson.** The rewrite killed all thirteen and
was reviewed against a list generated independently of it: 24 further defects, and the fitted
suite let every one of them through. `sectionSwitch` -- the control the whole feature is -- had
no test that its buttons did ANYTHING: deleting the click listener, deleting `wrap.append`, or
showing `entry.key` instead of the label all passed the full 843-test suite. So did a
`stageSteps` whose filter is computed and then not used, a `statusView` handed `SETUP_SECTIONS`,
and dropping the `if (data.job)` guard, which throws and blanks the Setup page.

A suite that kills the defects someone already listed and nothing else has been FITTED to the
list. The count is not the property; the property is that every element a reader depends on --
the text, the handler, the append, the guard, the order -- is pinned by something. Where a test
below reads a whole construct (`_tab_loop`) rather than a scattered substring, that is why.

Three structural traps this file fell into, kept named so they are not re-dug:

  * **`_decl` bounds at the next TOP-LEVEL declaration, not at the next `\\nfunction `.** The
    first spelling sailed past `export function` and `const`, so what it called
    `statusSubscriptions`' body swallowed all of `setupView` and the whole `SETUP_SECTIONS`
    table. Every count taken over it was counting the wrong thing.
  * **A test guarded by `if "heading(" in body` is a test that can skip itself.** That guard
    made the label-to-heading check assert NOTHING for all three Setup sections, because Setup's
    heading is written one level down in `stageSteps`. A guard that decides whether to assert
    must be a guard whose falseness is itself asserted somewhere.
  * **`count(a) == count(b)` is the same trap wearing a number.** Replacing that guard with
    `count("heading(") == count(", entry.label)")` was green at `0 == 0`, so deleting the stage
    heading passed -- the identical hole, one refactor later. The count is written down as an
    EXPECTED number now (`_HEADED_SECTIONS`), because two counts always agree at zero. `_delegate`
    carried a third instance of the shape in its own `if hop else body`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from keel.web import staticfiles
from tests.web.test_client_assets import _comments_only, _view_keys

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


#: A top-level declaration of any kind. `^` with `re.M` and no leading whitespace is the whole of
#: "top-level" in this file: every nested declaration is indented.
_TOP_LEVEL = re.compile(
    r"^(?:export )?(?:async )?(?:function|const|class) ([A-Za-z_][A-Za-z0-9_]*)", re.M
)


def _decl(code: str, name: str) -> str:
    """One top-level declaration's source, bounded at the next one.

    **Bounded at the next declaration of ANY kind**, which is the correction #775 records: a
    bound written as `code.find("\\nfunction ", start)` runs straight past `export function` and
    `const`, and the region it returns then contains a neighbour's code. Measured before the fix:
    `statusSubscriptions` came back with `setupView` and `SETUP_SECTIONS` inside it, so a
    heading moved into the wrong function would have been reported as still in the right one.
    """
    found = [match for match in _TOP_LEVEL.finditer(code) if match.group(1) == name]
    # **Exactly one, because JavaScript hoists and the LAST declaration wins.** Returning the
    # first of two would have this module inspect a function the browser never runs: appending a
    # second `function stageSteps` leaves all four of its assertions green while every Setup
    # stage renders nothing (#776 review).
    assert len(found) == 1, name + " is declared " + str(len(found)) + " times at the top level"
    match = found[0]
    after = _TOP_LEVEL.search(code, match.end())
    return code[match.start() : after.start() if after else len(code)]


def _sections(code: str, name: str) -> list[tuple[str, str, str]]:
    """A `const <name> = [...]` table, as `(key, label, builder)` triples in declaration order.

    Parsed, never restated. The entries are the single place the tab bar's wording and the page's
    content are joined, so a test that re-spelled them would stop being able to notice them
    drifting apart -- which is the only failure this join can actually have.
    """
    body = _decl(code, name)
    found = re.findall(
        r'\{\s*key:\s*"([^"]+)",\s*label:\s*"([^"]+)",\s*build:\s*([A-Za-z_][A-Za-z0-9_]*)',
        body,
    )
    return [(key, label, build) for key, label, build in found]


def _tab_loop(code: str) -> str:
    """The body of `sectionSwitch`'s per-entry loop -- one tab's construction, in full.

    Read as a unit rather than as scattered substrings over the whole function, so that every
    part of a tab (its text, its type, its focus key, its current-ness, its handler and its
    append) is asserted in the one place they all have to agree."""
    body = _decl(code, "sectionSwitch")
    marker = "for (const entry of sections) {"
    assert marker in body, "the bar must build one button per section, from the table"
    return body.split(marker)[1].split("\n  }")[0]


def _delegate(code: str, builder: str) -> str:
    """The function that actually draws a section, following one `return <fn>(...)` hop.

    `setupPaper` and `setupLive` are two-line delegates onto `stageSteps`, and a scan that
    stopped at the named builder found no heading, no blurb and no step filter in either --
    which is exactly how the label check came to assert nothing for the whole Setup page.
    """
    body = _decl(code, builder)
    hops = re.findall(r"return ([A-Za-z_][A-Za-z0-9_]*)\(", body)
    # **At most one, or this helper is guessing.** The first spelling took the FIRST such return
    # anywhere in the body, so an early `return note("...")` sent it into `note` and every
    # assertion written over the result went vacuous -- the helper itself becoming the
    # "guard that decides whether to assert" this module's docstring forbids (#776 review).
    assert len(hops) <= 1, builder + " has more than one delegating return: " + repr(hops)
    return _decl(code, hops[0]) if hops else body


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


def test_the_setup_stages_are_still_the_runbook_stages() -> None:
    """The stage keys are what `step.stage` carries on the wire. A section keyed anything else
    would filter every step out and render "No steps in this stage." forever."""
    keys = [entry[0] for entry in _sections(_code("render.js"), "SETUP_SECTIONS")]

    assert keys == ["setup", "paper", "live"]


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


# -- the dispatcher ----------------------------------------------------------------------------


def test_an_unwired_switch_runs_every_builder_and_returns_before_the_bar() -> None:
    """**Kills: deleting the `if (!onSection)` block.**

    A caller that passes no `onSection` gets every section, in order, with no tab bar -- the page
    exactly as it read before it was cut up. The alternative degradation, one section and no way
    to reach the others, would be a client hiding information sitting in a payload it already
    has.

    The predecessor of this test asserted `"onSection" in body` over the VIEW's region, where the
    word appears in the signature. Deleting this entire block passed it."""
    body = _decl(_code("render.js"), "sectioned")

    assert "if (!onSection)" in body, "the unwired fallback is gone"
    fallback = body.split("if (!onSection)")[1].split("}")[0]
    assert "for (const entry of sections)" in fallback, "the fallback must run EVERY section"
    assert "entry.build(payload, entry)" in fallback, (
        "each section must be built with ITS OWN entry -- `entry.build(payload)` renders every "
        "heading as undefined and throws in stageSteps"
    )
    assert "return fragment;" in fallback, "it must return before the tab bar is appended"


def test_the_shown_section_is_resolved_from_the_request_with_a_fallback_to_the_first() -> None:
    """**Kills: `const here = sections[0];`.**

    That mutation leaves the bar rendering, `aria-current` on the first tab forever, the press
    recorded and the repaint forced -- and the page never changes section. Every tab a silent
    no-op, and the whole of the predecessor suite green.

    The fallback is the other half: a key naming no section -- one renamed while a reader had its
    tab open -- must render the first section rather than throw."""
    body = _decl(_code("render.js"), "sectioned")

    assert "entry.key === current" in body, "the shown section must be resolved from the request"
    assert "|| sections[0]" in body, "an unknown key must fall back to the first section"

    # And the RESOLVED entry is what gets built. Pinning the two lines above and not this one
    # left `here.build(payload, sections[0])` green -- every section built with section one's
    # entry, so `stageSteps` filters on "setup" under all three tabs -- and `chosen.build(...)`
    # green too, which throws on the very fallback the lines above describe (#776 review).
    assert "here.build(payload, here)" in body, "the resolved entry is what must be built"


def test_the_tab_bar_is_drawn_exactly_once_when_the_switch_is_wired() -> None:
    """**Kills: deleting the `sectionSwitch` append.**

    Without it the chosen section still renders and nothing else does -- so section one is the
    only section anyone can ever reach, and no test that merely counted `function sectionSwitch(`
    would notice, because the declaration is untouched."""
    body = _decl(_code("render.js"), "sectioned")

    assert body.count("sectionSwitch(") == 1, "the wired path draws the bar once"

    # With the RESOLVED key, and BEFORE the section. `sectionSwitch(current, ...)` leaves no tab
    # marked current on an unknown key, and a bar appended after the content puts the control
    # after the thing it controls in both DOM and tab order (#776 review).
    assert "sectionSwitch(here.key, sections, onSection, label)" in body, (
        "the bar takes the resolved key and this page's own label"
    )
    assert body.index("sectionSwitch(") < body.index("here.build("), (
        "the bar is appended before the section it chooses between"
    )


# -- the tab bar -------------------------------------------------------------------------------


def test_every_tab_carries_a_focus_key_derived_from_its_own_section() -> None:
    """**Kills: `"data-focus", "section:"` -- one key shared by every tab.**

    Pressing a tab replaces the whole view, which destroys the button that was pressed;
    `rebuildInto` finds its replacement by this key. Shared, `querySelector` returns the FIRST
    match, so every keyboard press dumps focus onto tab one -- the precise regression this
    attribute exists to prevent, and one that `"data-focus" in body` cannot see."""
    body = _decl(_code("render.js"), "sectionSwitch")

    assert "for (const entry of sections)" in body, "one button per section, from the table"
    focus = re.search(r'"data-focus",([^;]*)\);', body)
    assert focus, "the tabs need a focus key"
    assert "entry.key" in focus.group(1), (
        "the focus key must be derived from the entry, not shared: " + focus.group(1).strip()
    )


def test_every_tab_is_a_button_that_calls_back_with_its_own_section() -> None:
    """**Kills: deleting the click listener; `onSection(current)`; deleting `wrap.append`;
    `entry.label` -> `entry.key` as the tab text; deleting `type="button"`.**

    All five passed the whole 843-test suite before this test existed. Nothing anywhere asserted
    that a tab does ANYTHING: the bar rendered with the right wording, the right `aria-current`
    and the right focus keys, and pressing one was a no-op. The label assertions elsewhere are
    over the TABLE and over the BUILDERS -- never over the control that puts the label on screen.

    `type="button"` is not cosmetic: without it a `<button>` inside a form submits it.
    """
    loop = _tab_loop(_code("render.js"))

    assert 'el("button", "scopekey", entry.label)' in loop, "the tab shows the label, not the key"
    assert 'setAttribute("type", "button")' in loop, "a bare button submits its form"
    assert 'addEventListener("click", () => onSection(entry.key))' in loop, (
        "the tab must call back with ITS OWN key"
    )
    assert "wrap.append(button)" in loop, "a button nobody appends is a bar with no tabs"


def test_the_bar_announces_itself_with_the_label_its_caller_passed() -> None:
    """**Kills: `aria-label` hardcoded to `"Section"`, and dropping the argument at the call.**

    Two tab bars on one site announcing themselves identically leave a screen-reader user unable
    to tell which page's sections they landed in -- the #659 lesson this docstring cites. The
    predecessor checked only that the two CALL SITES pass distinct strings, never that
    `sectionSwitch` uses the parameter at all."""
    body = _decl(_code("render.js"), "sectionSwitch")

    assert 'setAttribute("aria-label", label' in body, "the bar must use the label it was given"


def test_only_the_tab_that_is_on_is_marked_current() -> None:
    """**Kills: setting `aria-current` unconditionally.**

    `aria-current` is both the underline and the announcement, from one attribute, so what a
    reader sees and what a reader hears cannot drift. Set on every tab, a screen-reader user is
    told all five sections are current at once -- and `"aria-current" in body` passes."""
    body = _decl(_code("render.js"), "sectionSwitch")
    lines = [line for line in body.splitlines() if "aria-current" in line]

    assert len(lines) == 1, "aria-current is set in exactly one place: " + repr(lines)
    # The CONDITION, spelled out. `"current" in line` was the first spelling and it is satisfied
    # by the attribute's own name -- `"current"` is a substring of `"aria-current"` -- so
    # `if (entry.key)` (every tab current) and `if (entry.key !== current)` (every tab EXCEPT the
    # live one) both passed. The same substring trap this whole module exists to remove, dug
    # inside the fix for it (#776 review).
    condition = re.search(r"if \(([^)]*)\)", lines[0])
    assert condition, "aria-current must be conditional: " + lines[0].strip()
    assert condition.group(1) == "entry.key === current", (
        "the condition must compare the entry's key to the resolved section, not "
        + condition.group(1)
    )


def test_one_switch_serves_both_pages_and_each_announces_itself_by_name() -> None:
    """Two tab bars on one site announcing themselves identically would leave a screen-reader
    user unable to tell which page's sections they had landed in -- `scopeSwitch` learned this at
    #659 and took a `label` argument for it. Same argument, same shape."""
    code = _code("render.js")

    assert code.count("function sectionSwitch(") == 1, "one control, declared once"
    assert code.count("function sectioned(") == 1, "one dispatcher, declared once"

    # The TABLE as well as the label. `[A-Z_]+` accepted any table, so handing `statusView` the
    # `SETUP_SECTIONS` list passed -- the Status page rendering Setup's three sections. The two
    # call lines are otherwise identical, which is what makes that the likely copy-paste when a
    # third sectioned view arrives (#776 review).
    for view, table in (("statusView", "STATUS_SECTIONS"), ("setupView", "SETUP_SECTIONS")):
        wiring = "sectioned(data, " + table + ", section, onSection, "
        assert wiring in _decl(code, view), view + " must be wired to " + table

    labels = re.findall(r'sectioned\(data, [A-Z_]+, section, onSection, "([^"]+)"\)', code)
    assert len(labels) == 2, "both views must wire the switch: " + repr(labels)
    assert len(set(labels)) == 2, "the two bars must not announce themselves alike: " + repr(labels)


# -- the runbook stages ------------------------------------------------------------------------


def test_a_stage_shows_its_own_steps_and_never_another_stages() -> None:
    """**Kills: `step.stage === "paper"` -- the filter hardcoded.**

    That renders the PAPER checklist under the "To go live" tab: seven steps about seeding a rule
    library, presented to an operator reading the page that gates real money. `stageSteps` had no
    test of any kind before #775; four independent mutations of it were green."""
    body = _delegate(_code("render.js"), "setupLive")
    filt = re.search(r"step\.stage === ([^)\s;]+)", body)

    assert filt, "a stage section must filter the steps to its own stage"
    assert filt.group(1) == "entry.key", (
        "the filter must come from the entry, not a literal: " + filt.group(1)
    )

    # And the loop must READ the filtered list. Pinning the filter alone left
    # `for (const step of here)` -> `of steps` green: the filter is computed, ignored, and both
    # stage tabs render all fifteen steps -- the live checklist under "To run in paper", which is
    # the exact failure the filter exists to prevent, one line lower (#776 review).
    assert "for (const step of here)" in body, "the step loop must read the FILTERED list"
    assert "stepCard(step, actions, notAutomated)" in body, (
        "the two maps are same-arity and opposite in meaning -- swapped, every not-automated "
        "note becomes an action lookup"
    )


def test_a_stage_states_its_own_argument() -> None:
    """**Kills: deleting the blurb.**

    "Evaluates rules against real market data and places nothing" is the sentence that makes
    paper the safe default. It is `render_setup`'s own wording, kept rather than paraphrased for
    that reason, and a stage heading with no argument under it is a checklist with its reason
    removed."""
    body = _delegate(_code("render.js"), "setupPaper")

    assert "entry.blurb" in body, "a stage must state its own argument, from its entry"


def test_the_two_stages_do_not_share_a_heading_id() -> None:
    """**Kills: `heading("h-stage", ...)` -- the id no longer derived from the stage.**

    Harmless while one section renders at a time, and a duplicate `id` the moment the unwired
    path draws both stages. `heading`'s whole purpose is to be a table's accessible name, and two
    elements answering to one id makes `aria-labelledby` resolve to whichever comes first."""
    ident = re.search(r"heading\(([^,]+),", _delegate(_code("render.js"), "setupPaper"))

    assert ident, "a stage must write a heading"
    assert "entry.key" in ident.group(1), (
        "the heading id must be derived from the stage: " + ident.group(1).strip()
    )


def test_an_empty_stage_says_so_rather_than_rendering_a_bare_heading() -> None:
    """**Kills: deleting the empty-stage sentence.**

    A heading with nothing under it reads as a page that failed to load. It is also reachable:
    a deployment whose steps all belong to one stage renders the other one empty."""
    body = _delegate(_code("render.js"), "setupLive")

    assert "No steps in this stage." in body
    # Guarded on the STAGE's own list. `here.length === 0` -> `steps.length === 0` leaves the
    # sentence in the source and unreachable, rendering the bare heading this test names
    # (#776 review) -- the difference between a sentence existing and a sentence being shown.
    assert "if (here.length === 0)" in body, "the empty case is the stage's own list, not the book"


def test_a_delegating_builder_forwards_its_own_entry_and_not_a_table_index() -> None:
    """**Kills: `return stageSteps(payload, SETUP_SECTIONS[1]);` inside `setupLive`.**

    `setupPaper` and `setupLive` are two-line delegates onto one `stageSteps`. A delegate that
    forwards a hardcoded entry renders its neighbour's stage under its own tab -- the same defect
    as the hardcoded filter, one level up, and `setupLive`'s docstring claimed it was not
    spellable. It is spellable; this is what makes the claim true.

    Asserted as a PAIRING: the arguments of the delegating call must be the delegate's own
    parameters, in order."""
    code = _code("render.js")

    for builder in ("setupPaper", "setupLive"):
        body = _decl(code, builder)
        signature = re.search(r"function " + builder + r"\(([^)]*)\)", body)
        call = re.search(r"return [A-Za-z_][A-Za-z0-9_]*\(([^)]*)\)", body)
        assert signature and call, builder + " must delegate"
        params = [part.strip() for part in signature.group(1).split(",")]
        passed = [part.strip() for part in call.group(1).split(",")]
        assert passed == params, (
            builder + " forwards " + repr(passed) + ", not its own parameters " + repr(params)
        )


# -- nothing was lost, and nothing was duplicated ----------------------------------------------


def test_each_status_heading_is_written_once_by_the_builder_its_entry_names() -> None:
    """**Kills: a second `heading("h-positions", ...)` in another section.**

    Two elements carrying one id make `table()`'s `aria-labelledby` resolve to whichever comes
    first, so a table is announced under another section's heading. The predecessor asserted
    `count(...) >= 1`, which proves "not dropped" and can never prove "not duplicated" -- while
    its own docstring claimed both."""
    code = _code("render.js")
    table = _sections(code, "STATUS_SECTIONS")
    wanted = {
        "positions": "h-positions",
        "rules": "h-rules",
        "freshness": "h-freshness",
        "subscriptions": "h-subscriptions",
    }

    for key, heading in wanted.items():
        written = code.count('heading("' + heading + '"')
        assert written == 1, heading + " is written by " + str(written) + " headings, want 1"
        builder = next(entry[2] for entry in table if entry[0] == key)
        body = _decl(code, builder)
        assert 'heading("' + heading + '"' in body, heading + " is not written by " + builder
        # TWICE in the builder: once as the heading's id, once as the `table()` id that becomes
        # its `aria-labelledby`. Pinning the heading alone left the id in the `table(...)` call
        # free to drift, which silently unnames the table for a screen reader while the heading
        # above it still looks right (#776 review).
        assert body.count('"' + heading + '"') == 2, (
            heading + " must name both the heading and the table it labels"
        )


#: The sections that carry a heading of their own. The other two -- `status` and `setup` -- are
#: the page each tab bar sits on, so their `<h1>` already says it and a second heading would
#: repeat the title. Written down because the count test below needs an EXPECTED number: without
#: one it can only compare two counts, and two counts agree at zero.
_HEADED_SECTIONS = frozenset({"positions", "rules", "freshness", "subscriptions", "paper", "live"})


@pytest.mark.parametrize("name", ["STATUS_SECTIONS", "SETUP_SECTIONS"])
def test_a_section_heading_is_the_tab_wording_and_never_a_second_copy_of_it(name: str) -> None:
    """**Kills: `heading("h-stage-".concat(entry.key), "Steps")` -- a heading spelling its own.**

    The tab bar and the heading below it naming the same section differently is the one drift
    this arrangement can have, and it is foreclosed by handing each builder the entry it was
    reached through.

    Asserted as a COUNT rather than behind `if "heading(" in body`. That guard is what made this
    check assert nothing at all for every Setup section -- Setup's heading lives one hop down in
    `stageSteps` -- so the section of the page whose duplicate wording motivated the whole table
    was the section nothing was checking. `_delegate` follows the hop."""
    code = _code("render.js")

    for key, label, builder in _sections(code, name):
        body = _delegate(code, builder)
        assert '"' + label + '"' not in body, builder + " re-spells its own label: " + label
        # An EXPECTED count, not a comparison of two counts. `count("heading(") ==
        # count(", entry.label)")` is satisfied at 0 == 0, so deleting `stageSteps`' heading
        # passed -- all three Setup tabs rendering a blurb and a step list under no heading at
        # all. That is the self-skipping shape this module's docstring says was "kept named so it
        # is not re-dug", re-dug in a new form (#776 review).
        wanted = 1 if key in _HEADED_SECTIONS else 0
        assert body.count("heading(") == wanted, (
            builder + " writes " + str(body.count("heading(")) + " headings, want " + str(wanted)
        )
        assert body.count(", entry.label)") == wanted, (
            builder + " writes a heading whose text is not entry.label"
        )


def test_the_setup_builders_name_their_payload_so_the_parity_pin_can_see_it() -> None:
    """**Kills: renaming a Setup builder's payload parameter away from `data`.**

    `_view_keys("setupView", "data")` scans this view's whole region for `data.<key>` and checks
    each against `/api/setup`. Every builder in that region therefore has to spell its payload
    `data` -- a `payload.<key>` read there is invisible to the pin, so a key the endpoint does
    not send would render blank forever with nothing failing.

    Proved as a controlled pair rather than reasoned: `payload.made_up_key` inserted into
    `setupPaper` escaped the suite; the identical `data.made_up_key` in `setupSummary` was caught
    (#776 review). `sectioned` and `sectionSwitch` take `payload` for the opposite reason -- they
    sit in `activityView`'s region, where a `data.` read would be checked against the wrong
    endpoint. The rule is regional, which is why it is written down rather than left to taste."""
    code = _code("render.js")

    for builder in ("setupSummary", "setupPaper", "setupLive", "stageSteps"):
        signature = re.search(r"function " + builder + r"\(([^)]*)\)", code)
        assert signature, builder + " is not declared"
        first = signature.group(1).split(",")[0].strip()
        assert first == "data", (
            builder + " names its payload " + repr(first) + ", which _view_keys cannot see"
        )


def test_the_job_panel_is_page_level_and_not_inside_any_section() -> None:
    """**Kills: `jobPanel` rendered from a section builder.**

    `market_data` is a `Stage.PAPER` step, so "Fetch market data" is pressed from the "To run in
    paper" tab. Rendered inside the `Setup` section, the job's state line, elapsed time, streamed
    transcript and -- the one that matters -- `job.error` are all invisible to the person who
    started it. `jobPanel`'s own docstring keeps a failure on screen because "the whole point of
    running something in the background is that nobody was watching when it broke"; putting the
    panel on a tab the operator is not on defeats that structurally.

    It is page-level state, like the `<h1>`, so it renders above the switch."""
    code = _code("render.js")

    view = _decl(code, "setupView")
    # The GUARD and the ARGUMENT, not merely the name. `jobPanel` opens `plain(job.key)`, so
    # dropping `if (data.job)` throws on every deployment with no job running and renders the
    # Setup page blank; `jobPanel(data)` renders every field undefined. Both passed the whole
    # 843-test suite, on the one line this change moved (#776 review).
    assert "if (data.job) fragment.append(jobPanel(data.job));" in view, (
        "the job panel is guarded and takes data.job"
    )
    assert view.index("jobPanel(") < view.index("sectioned("), (
        "the panel is page-level: it renders ABOVE the switch, not after it"
    )
    for _key, _label, builder in _sections(code, "SETUP_SECTIONS"):
        assert "jobPanel(" not in _delegate(code, builder), (
            builder + " renders the job panel, which hides it from the tab that starts the job"
        )


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


# -- the state, in the shell -------------------------------------------------------------------


def test_each_sectioned_route_remembers_its_own_section() -> None:
    """Status and Setup are two pages. One shared "current section" would have a reader who
    opened Setup's "To go live" find Status showing whichever of its five sections happened to
    sit at that key -- or nothing at all. Both views read it through the same pair of helpers."""
    code = _code("main.js")

    for view in ("statusView", "setupView"):
        call = view + "(data, sectionOf(route), onSectionFor(route))"
        assert call in code, "the shell must mount " + view + " with its own section: " + call

    # The whole expression. `route.name` alone left a fallback to another route's bucket green
    # -- Setup showing whatever section Status was on (#776 review).
    assert 'return sections.get(route.name) || "";' in _decl(code, "sectionOf"), (
        "a route with no section falls back to its own table's first, never another route's"
    )


def test_a_section_press_writes_into_its_own_routes_bucket() -> None:
    """**Kills: `sections.set("status", key)`.**

    Read per route but WRITTEN to a constant, Setup's presses land in Status's bucket: Setup can
    then never leave its first section, and Status silently jumps to a key it has no section for.
    The predecessor checked `route.name` inside `sectionOf` only, so the write was unpinned --
    and the read side alone cannot tell you the pair is consistent."""
    body = _decl(_code("main.js"), "onSectionFor")

    assert "sections.set(route.name, key)" in body, "the press is recorded against its own route"


def test_pressing_a_section_repaints_at_once_rather_than_waiting_for_focus_to_leave() -> None:
    """A rebuild a reader just asked for must be FORCED. Deferred -- which is what an unforced
    rebuild does while focus sits on the button that was just clicked -- pressing a tab does
    nothing, and a control that does nothing is indistinguishable from a broken one."""
    body = _decl(_code("main.js"), "onSectionFor")

    assert "paint(route, true, true)" in body, "the section press forces the rebuild"
    # AFTER recording it. Repainting first re-reads and re-renders the section the reader is
    # leaving, and the press only takes effect on the NEXT paint (#776 review).
    assert body.index("sections.set(") < body.index("paint("), (
        "the section is recorded before the repaint that reads it"
    )
