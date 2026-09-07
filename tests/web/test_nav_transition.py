"""The navigation transition: what the page may claim while a read is in flight (#754).

A browser cannot run here (see `test_client_assets`'s docstring for the standing argument), so
these are assertions over the shipped source. That makes them vulnerable in the usual way -- a
substring is satisfied by a declaration -- so every test below either slices ONE function's body
and asserts about that body alone, or counts occurrences across the file. Presence anywhere is
never the assertion.

THE BUG THESE PIN. `show()` used to set `document.title` and `aria-current="page"` and then
`await` the read. For the length of the fetch every sighted signal said "you are on Balances"
while the DOM still held Positions' rows: a stale figure under a fresh label, which is the exact
false claim `_session_banner` refuses to make elsewhere. The fix defers the claim until the DOM
that backs it has been swapped in.

WHAT THIS FILE CANNOT PROVE, stated so a green run is not read as more than it is: that the
indicator is actually visible, that the dim is perceptible, that a pending link reads as pending,
or that the reduced-motion query is honoured by any particular browser. Those are hand checks
against a running `keel serve`.
"""

from __future__ import annotations

import re

from keel.web import staticfiles

_JS = staticfiles.STATIC_ROOT / "js"
_MAIN = (_JS / "main.js").read_text()
_CSS = (staticfiles.STATIC_ROOT / "css" / "keel.css").read_text()
#: The stylesheet with its `/* ... */` prose removed. CSS has no line comments, so this is the
#: whole job -- and it is not optional: renaming the busy SELECTOR to something inert left every
#: assertion below green, because the comment ABOVE the rule still said "aria-busy". Caught by
#: mutation, which is the only thing that finds a scan satisfied by its own explanation.
_CSS_RULES = re.sub(r"/\*.*?\*/", "", _CSS, flags=re.S)


def _body(source: str, name: str) -> str:
    """The source of one top-level `function name(...)`, up to its closing brace.

    Top-level functions in this file close on a `}` in column zero, which is what bounds the
    slice. Deliberately NOT a brace counter: a counter would have to understand strings, regexes
    and comments to be right, and being subtly wrong would silently widen every assertion built
    on it. A column-zero brace is a property of the file that is checked below.
    """
    for prefix in ("\nfunction ", "\nasync function "):
        needle = prefix + name + "("
        if needle in source:
            start = source.index(needle)
            return source[start : source.index("\n}", start)]
    raise AssertionError(name + " is not a top-level function in this file")


def _code_only(source: str) -> str:
    """`source` with comments removed, so an assertion is about CODE and not about prose.

    Necessary, not tidy. The first cut of `test_show_makes_no_claim_about_arrival_before_the_read`
    failed against a correct implementation, because `show`'s new comment *explains* that it no
    longer sets `aria-current` -- and a raw substring scan cannot tell an explanation from an
    assignment. A test that a docstring can break is a test that a docstring can also satisfy.

    `//` is stripped only when not preceded by `:`, so a `https://` inside a string survives.
    `test_the_comment_stripper_keeps_code_and_drops_prose` pins both halves.
    """
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"(?<!:)//[^\n]*", "", source)


def test_the_comment_stripper_keeps_code_and_drops_prose() -> None:
    """The helper two assertions below rest on. A stripper that ate everything would make them
    pass against any input at all."""
    sample = 'a();  // mentions aria-current\nconst u = "https://x/y";\n/* aria-current */\nb();'
    stripped = _code_only(sample)
    assert "aria-current" not in stripped, "prose survived -- the assertions become prose checks"
    assert "a();" in stripped and "b();" in stripped, "code was eaten"
    assert "https://x/y" in stripped, "a URL in a string was mangled"


def test_the_slicer_finds_a_bounded_body() -> None:
    """The helper every other test rests on. If this ever returns the whole file, the assertions
    below become presence-anywhere checks and stop meaning what they say."""
    body = _body(_MAIN, "show")
    assert "function show(" in body
    assert len(body) < len(_MAIN) / 4, "the slice is not bounded -- every test here is vacuous"
    assert "async function paint(" not in body, "the slice ran past its own function"


def test_show_makes_no_claim_about_arrival_before_the_read() -> None:
    """`show` runs before the fetch. It may say a read is happening; it may not say where you are.

    Both halves matter and both were wrong: `aria-current` is what a screen reader announces as
    the current page and what CSS underlines, and `document.title` is what the tab and the
    history entry say.
    """
    body = _code_only(_body(_MAIN, "show"))
    assert "aria-current" not in body, (
        "show() sets aria-current before awaiting the read -- that is the stale-view-under-fresh-"
        "label bug this issue is about"
    )
    assert "document.title" not in body, "show() renames the tab before the tab's content exists"


def test_arrival_is_claimed_only_after_the_dom_that_backs_it() -> None:
    """`commitNavigation` is the one place that says "you are here", and `paint` calls it after
    `rebuildInto` -- never before."""
    assert "function commitNavigation(" in _MAIN
    commit = _body(_MAIN, "commitNavigation")
    assert "aria-current" in commit
    assert "document.title" in commit

    paint = _body(_MAIN, "paint")
    assert "commitNavigation(" in paint, "paint never commits the navigation it completed"
    assert paint.index("rebuildInto(") < paint.index("commitNavigation("), (
        "paint commits the navigation before swapping the DOM -- the same ordering bug, moved"
    )


def test_a_click_is_acknowledged_without_claiming_arrival() -> None:
    """Deferring `aria-current` removes every immediate signal from a click, which invites a
    second click on a slow read. A pending marker restores the feedback WITHOUT the claim, so it
    must be a different attribute from the one that means "you are here"."""
    body = _code_only(_body(_MAIN, "show"))
    assert "markPending(" in body, "a click on a slow route gives no feedback at all"
    assert "aria-current" not in body

    # And the marker it sets is a DIFFERENT attribute from the one meaning "you are here".
    # Asserting the call alone would pass over a `markPending` that set `aria-current`, which is
    # the bug wearing a new name.
    marker = _code_only(_body(_MAIN, "markPending"))
    assert "PENDING_ATTR" in marker
    assert "aria-current" not in marker
    assert _code_only(_MAIN).count('PENDING_ATTR = "data-pending"') == 1


def test_the_pending_marker_is_always_cleared_where_arrival_is_claimed() -> None:
    """A `data-pending` left behind outlives the read and marks a link forever."""
    commit = _body(_MAIN, "commitNavigation")
    assert "removeAttribute" in commit
    assert "PENDING_ATTR" in commit or "data-pending" in commit


def test_only_navigation_raises_the_busy_flag() -> None:
    """The visual indicator hangs off `aria-busy`, so a poll or an SSE repaint raising it would
    flash the page four times a minute on a route nobody navigated.

    Counted, not merely located: exactly ONE assignment of `"true"` in the client, and it is in
    `show`. `index.html` ships the initial `aria-busy="true"` for the first paint, which is a
    navigation in every sense that matters here.
    """
    raised = re.findall(r'aria-busy",\s*"true"', _MAIN)
    assert len(raised) == 1, f"aria-busy is raised in {len(raised)} places; exactly one is nav"
    assert 'aria-busy", "true"' in _body(_MAIN, "show").replace("\n", " ").replace("  ", " ")


def test_the_busy_flag_is_lowered_on_every_path_that_raised_it() -> None:
    """`paint` returns early twice -- a superseded route and a banner-only poll. Neither raised
    the flag, so neither needs to lower it; the rebuild path does and must."""
    paint = _body(_MAIN, "paint")
    assert 'aria-busy", "false"' in paint.replace("\n", " ").replace("  ", " ")


def test_the_stylesheet_gives_the_busy_state_a_visible_form() -> None:
    """The accessible half shipped correct and the visual half did not exist: `aria-busy` was
    toggled properly and nothing in the CSS referred to it."""
    assert '#content[aria-busy="true"]' in _CSS_RULES, (
        "nothing styles the busy state -- a sighted user sees no change. Asserted against the "
        'SELECTOR in comment-stripped CSS: `"aria-busy" in _CSS` passes on the prose alone.'
    )
    assert "opacity" in _CSS_RULES


def test_the_pending_link_is_styled_distinctly_from_the_current_one() -> None:
    """Otherwise the acknowledgement and the arrival look the same, which is the bug again."""
    assert "header a[data-pending]" in _CSS_RULES
    assert 'header a[aria-current="page"]' in _CSS_RULES, "the state it must differ from is gone"


def test_the_transition_is_disabled_under_reduced_motion() -> None:
    """Anything that animates has to answer this, and a media query naming it is the only way to
    say so in a stylesheet."""
    assert "@media (prefers-reduced-motion: reduce)" in _CSS_RULES
    assert "@keyframes keel-busy" in _CSS_RULES, "the busy bar has no animation to reduce"
