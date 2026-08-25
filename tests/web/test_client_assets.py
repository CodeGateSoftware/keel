"""The client shell's constraints, as tests (#536).

A browser cannot run here, so this module is deliberate about the line between what it proves and
what it cannot. It proves **properties of the shipped bytes** -- what is in the files, what is not
in them, and what the server does with them over a real socket. It proves nothing about behaviour
that requires a DOM: whether a click actually routes, whether focus actually moves, whether the
`aria-live` region is actually announced. `docs/superpowers/specs/2026-08-23-web-ui-rewrite-
design.md` does not ask for a headless browser and this issue does not add one -- that would be a
Node toolchain in a repository that is deliberately Python-only, which is the same architectural
decision the `tsc --noEmit` question raises and is not settled here.

What that leaves uncovered is listed once, honestly, so nobody reads a green suite as more than
it is:

  * that a nav click routes without a page load, and that back/forward walk the history;
  * that focus lands on `<main>` after a route change and that the focus ring is visible;
  * that a screen reader announces the engine banner when it changes;
  * that the layout holds from a narrow window to full width;
  * that the poll actually fires every 15 seconds and pauses on a hidden tab.

Each of those is checked by hand against a running `keel serve`; the procedure is in the PR body.

The rules below that constrain how `js/render.js` is WRITTEN -- no template literals, no regex
literals -- exist to keep `_code_only` small enough to be obviously correct. `render.js`'s own
module docstring records that, so the rules are not mistaken for style and removed.
"""

from __future__ import annotations

import json
import re

import pytest

from keel.web import render, staticfiles

# `_theme_palette` is IMPORTED from the contrast module rather than reimplemented, and nothing in
# it is modified: this module only calls it with a different `css` string. That direction matters
# -- a shared test helper that grows a parameter to accommodate a new caller is how #545 ended up
# with a suite that was green because every pre-existing test had quietly started sending
# something the real client could not send. Here the existing tests keep calling it with
# `render._STYLE` exactly as before, and the only new thing is a second caller.
from tests.web.test_palette_contrast import _theme_palette

_STATIC = staticfiles.STATIC_ROOT
_JS = _STATIC / "js"
_EXTERNAL = _JS / "external"
_INDEX = _STATIC / "index.html"
_CSS = _STATIC / "css" / "keel.css"

#: The client's modules, in the spec's own order (§"JS file structure"). A closed list, so a file
#: appearing under `js/` without a test author noticing fails `test_the_client_ships_exactly_the_
#: declared_modules` rather than shipping unexamined.
#:
#: `chart.js` and `live.js` arrived at #537 and `docs.js` at #539, which is what the first
#: revision of this line predicted ("a fifth module is a design decision (#537 adds `chart`,
#: `live`, `docs`, `sw`) and should arrive with the list updated, not silently").
#:
#: **`sw` arrived at #538 and is deliberately NOT in this list**, because it is not under `js/`:
#: a service worker's scope is its own directory, so `js/sw.js` would be scoped to `/static/js/`
#: and could not answer a navigation to `/static/insights`. It sits at the static root instead,
#: and `tests/web/test_pwa.py::test_the_worker_is_served_from_the_scope_it_must_control` asserts
#: that placement rather than leaving it to whoever next reads the spec's file list.
_MODULES = ("main.js", "api.js", "render.js", "chart.js", "live.js", "format.js", "docs.js")

#: The modules held to "no arithmetic, no judgement, no derived display string", and the ONLY two.
#:
#: `render.js` is the spec's own choice ("so that a reviewer can confirm the absence by reading one
#: file"). `chart.js` is added at #537 rather than exempted, and the reason is that exempting it
#: would have been the easy road and the wrong one: a chart module is exactly where "just this one
#: subtraction, it is only a pixel" gets written, and once it is written the client is performing
#: arithmetic over figures that were exact `Decimal`s a moment ago. The coordinates are computed in
#: `keel.commands.insights.build_equity_curve` instead, so this scan holds over both files and the
#: design spec's §Dependencies claim -- "the client performs no arithmetic", the sentence that
#: removes the need for a decimal library -- is a property of the whole client.
#:
#: `main.js` is NOT here and cannot be: it owns timers and route indices, which are arithmetic
#: about the interface rather than about money. `api.js` is not either -- it reads HTTP statuses.
#: The line is drawn at the modules that touch PAYLOAD VALUES, which is where it means something.
_DERIVATION_FREE = ("render.js", "chart.js")


def _source(name: str) -> str:
    return (_JS / name).read_text(encoding="utf-8")


# -- the lexer -------------------------------------------------------------------------------------


def _code_only(source: str) -> str:
    """`source` with comments and string literals removed, so a scan sees code and only code.

    Four states and no parser, which is possible only because `render.js` contains no template
    literals and no regular-expression literals (its module docstring says so and
    `test_render_uses_neither_template_nor_regex_literals` enforces it):

      * A template literal's `${...}` re-enters CODE inside a string, so stripping one whole would
        strip code with it.
      * Telling `/` as division from `/` as the start of a regex needs the previous significant
        token -- the one genuinely hard problem in lexing JavaScript. With no regex literals,
        every `/` that is not `//` or `/*` is division, which is exactly what this scan is looking
        for.

    A string collapses to a single space rather than to nothing, so that the identifiers on either
    side of it cannot fuse into a third one that was never written.
    """
    out: list[str] = []
    i, n = 0, len(source)
    while i < n:
        ch = source[i]
        if ch in "'\"":
            quote = ch
            i += 1
            while i < n:
                if source[i] == "\\":
                    i += 2
                    continue
                if source[i] == quote:
                    i += 1
                    break
                i += 1
            out.append(" ")
            continue
        if ch == "/" and source.startswith("//", i):
            while i < n and source[i] != "\n":
                i += 1
            continue
        if ch == "/" and source.startswith("/*", i):
            end = source.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _markup_only(html: str) -> str:
    """`html` with its comments removed.

    Needed for the same reason `_code_only` strips JavaScript comments, and discovered the same
    way -- by the tests below failing on the first run. `index.html`'s comments DOCUMENT the
    properties this module asserts ("`<base href>` is impossible", "the one `aria-live` region"),
    so a raw substring search finds the prose explaining a rule and reports it as a violation of
    that rule. Both scanners exist because a file that explains itself well is a file a naive grep
    cannot check.
    """
    return re.sub(r"<!--.*?-->", " ", html, flags=re.DOTALL)


#: Every character that can only be arithmetic once comments and strings are gone. `/` is in the
#: list because `_code_only` has already consumed both comment forms, so a surviving `/` is
#: division.
_ARITHMETIC_CHARS = "+-*/%"

#: Identifiers whose presence means a number was operated on or formatted, even without an
#: operator in sight. `NumberFormat` is here because `Intl.NumberFormat` is the specific way this
#: file would come to format money -- the one thing §"The data contract" puts in Python.
_ARITHMETIC_NAMES = (
    "Number",
    "parseInt",
    "parseFloat",
    "Math",
    "BigInt",
    "toFixed",
    "toPrecision",
    "NumberFormat",
    "valueOf",
)

#: The half of a `Field` that `render.js` may never read.
#:
#: `payload.Field` is `{value, display, state}`, where `value` is documented as "machine input
#: (exact, ungrouped, `Decimal`-parseable for figures and ISO-8601 for instants)" and `display` is
#: "the human's, already formatted". Placing `display` is the job; touching `value` is the whole
#: of deriving. Banning the ATTRIBUTE catches what the operator scan cannot -- see
#: `test_render_never_judges_a_value_itself` for the mutation that got through without it.
_DERIVATION_ATTRIBUTE = ".value"


def _relational_operators(code: str) -> list[str]:
    """Every relational comparison in `code`.

    `<` is always one. `>` is one only when it is not the tail of an arrow function, which is the
    single other thing `>` can be in this file -- there are no generics in JavaScript and no JSX
    here, so `=>` is the complete exception list.
    """
    found = ["<" for ch in code if ch == "<"]
    for index, ch in enumerate(code):
        if ch == ">" and not (index and code[index - 1] == "="):
            found.append(">")
    return found


#: Everything that turns a string into markup. Absent from the WHOLE client, not just `render.js`.
_MARKUP_SINKS = (
    "innerHTML",
    "outerHTML",
    "insertAdjacentHTML",
    "document.write",
    "eval(",
    "new Function",
)


# -- the constraints ------------------------------------------------------------------------------


def test_the_client_ships_exactly_the_declared_modules() -> None:
    """The four modules the spec names, and nothing else under `js/`.

    A closed comparison rather than "each of these exists": a fifth module is a design decision
    (#537 adds `chart`, `live`, `docs`, `sw`) and should arrive with the list updated, not
    silently."""
    shipped = sorted(p.name for p in _JS.glob("*.js"))
    assert shipped == sorted(_MODULES)


def test_the_client_ships_no_third_party_code() -> None:
    """`js/external/` is empty of code, and that is the intended END STATE, not a stage.

    The one tracked file is a `README.md` explaining why the directory is empty -- git does not
    track empty directories, so without it the directory would not survive a clone and this test
    would be asserting about something that only exists on one machine.
    """
    assert _EXTERNAL.is_dir(), "js/external/ must exist -- see its README for why"
    entries = sorted(p.name for p in _EXTERNAL.iterdir() if p.is_file())
    assert entries == ["README.md"], (
        f"js/external/ holds {entries}; the spec's §Dependencies is explicit that nothing goes "
        "here, and a new entry must clear the reference's bar (see the README)"
    )


def test_no_client_asset_is_a_build_artifact() -> None:
    """Served exactly as authored: no bundle, no minified copy, and **no source map**.

    The source-map ban is the sharp one and it is the spec's §4: "the code running must be
    byte-identical to the code the user reads in devtools." A source map is what makes a build
    step feel free, so its absence is what makes the no-build rule enforceable rather than
    aspirational."""
    for path in sorted(_STATIC.rglob("*")):
        if not path.is_file():
            continue
        name = path.name
        assert not name.endswith(".map"), f"{path} is a source map"
        assert ".min." not in name, f"{path} looks like a minified build artifact"
    for name in _MODULES:
        assert "sourceMappingURL" not in _source(name), f"{name} points at a source map"


@pytest.mark.parametrize("name", _MODULES)
def test_every_module_declares_ts_check(name: str) -> None:
    """`// @ts-check` on the first line of every module.

    It is a comment. It costs nothing, it ships as authored, and it is what makes the JSDoc
    annotations below it load-bearing rather than decorative the day `tsc --noEmit` is switched
    on."""
    assert _source(name).splitlines()[0] == "// @ts-check"


@pytest.mark.parametrize("name", _DERIVATION_FREE)
def test_render_uses_neither_template_nor_regex_literals(name: str) -> None:
    """The two rules that keep `_code_only` small enough to be obviously correct.

    Asserted before `test_render_contains_no_arithmetic` relies on them, because that test's
    result is only meaningful if the lexer it uses is sound -- and the lexer is sound only under
    these two conditions."""
    code = _code_only(_source(name))
    assert "`" not in code, f"{name} must contain no template literals -- see its module note"
    # Every `/` in the residue is division (comments are already gone), which the arithmetic test
    # below rejects. Stated here as its own assertion so the REASON the lexer can be this small
    # fails loudly rather than showing up as a confusing arithmetic failure.
    assert "/" not in code, f"{name} must contain no regex literals and no division"


@pytest.mark.parametrize("name", _DERIVATION_FREE)
def test_render_contains_no_arithmetic(name: str) -> None:
    """**The acceptance criterion, mechanised.**

    The spec asks for `render` specifically so "a reviewer can confirm the absence by reading one
    file". Reading is the point; this is the gate that stops the property decaying between
    readings.

    `chart.js` joined it at #537. A chart is the one place a client is EXPECTED to do arithmetic
    -- scale a series, map it onto a box -- so a scan that stopped at `render.js` would have left
    the interesting file out. See `_DERIVATION_FREE`, and `build_equity_curve` for where the
    geometry went instead."""
    code = _code_only(_source(name))
    found = sorted({ch for ch in _ARITHMETIC_CHARS if ch in code})
    assert not found, f"{name} contains arithmetic operators: {found}"
    for word in _ARITHMETIC_NAMES:
        assert word not in code, f"{name} reaches for {word}; money is formatted in Python"


@pytest.mark.parametrize("name", _DERIVATION_FREE)
def test_render_never_judges_a_value_itself(name: str) -> None:
    """**The client places values; it never derives them -- including the judgement.**

    The arithmetic scan alone does NOT cover this, and that was found by mutation rather than by
    reasoning: appending

        function tone(v) { return v.value < 0 ? "bad" : "good"; }

    to `render.js` passed `test_render_contains_no_arithmetic` cleanly. It contains no arithmetic
    operator and no numeric identifier -- and it is exactly the bug `payload.py` spends a closed
    `state` vocabulary and #532's glyphs to make impossible. A minus sign inspected in the client
    is a second judgement, made where the rails cannot see it and where a `Decimal` has already
    become a double.

    So two more rules, and between them the mutation is caught twice over:

      * **`render.js` never reads `.value`.** `display` is what it places; `state` is what it
        styles by. `value` is machine input and has no business in a renderer.
      * **No relational comparison.** `<` and `>` (arrow functions excepted) are how a sign, a
        threshold or an ordering gets decided, and all three belong in Python.
    """
    code = _code_only(_source(name))
    assert _DERIVATION_ATTRIBUTE not in code, (
        f"{name} reads Field.value; it may place `display` and style by `state`, nothing else"
    )
    found = _relational_operators(code)
    assert not found, f"{name} compares values ({found}); judgement is payload.py's job"


def test_the_derivation_scanners_can_fail() -> None:
    """The premise for the test above, written as the mutation that motivated it.

    Both halves are asserted separately, because either one alone would have caught this
    particular mutation and a future edit may only trip one."""
    smuggled = 'function tone(v) { return v.value < 0 ? "bad" : "good"; }\n'
    code = _code_only(smuggled)
    assert _DERIVATION_ATTRIBUTE in code
    assert _relational_operators(code) == ["<"]

    # The arithmetic scan really does miss it -- which is why this test exists at all.
    assert not [ch for ch in _ARITHMETIC_CHARS if ch in code]

    # And an arrow function is not a relational comparison.
    assert _relational_operators("const f = (x) => x;\n") == []
    assert _relational_operators("if (a >= b) {}\n") == [">"]


def test_the_arithmetic_scanner_can_fail() -> None:
    """The premise, asserted before the test above is trusted.

    Two directions, and both matter. A scanner that flagged nothing would make
    `test_render_contains_no_arithmetic` pass for a trivial reason; a scanner that flagged
    everything would make it impossible to write the file at all, and the first author to hit
    that would loosen the scan rather than the code."""
    # It catches arithmetic in code.
    assert "+" in _code_only('const total = a + b;\n')
    assert "Math" in _code_only("const x = Math.round(y);\n")
    assert "/" in _code_only("const half = whole / two;\n")

    # It does NOT catch arithmetic quoted in a comment or a string -- prose about money, and the
    # `" · "` separators and `"aria-labelledby"` attribute names `render.js` is full of.
    assert "+" not in _code_only("// a + b would be arithmetic\n")
    assert "+" not in _code_only("/* a + b */\n")
    assert "-" not in _code_only('el("span", "aria-labelledby");\n')
    assert "Math" not in _code_only('const label = "Math";\n')

    # An escaped quote does not end a string early -- otherwise the code after it would be read
    # as string and a real operator could hide there.
    assert "+" not in _code_only('const s = "he said \\" a + b";\n')


def test_no_client_module_can_write_markup() -> None:
    """No `innerHTML`, anywhere in the client.

    `render.py::esc` exists because "rule names, product ids and adapter error strings all
    originate outside this process; none of them is trusted markup" -- and every one of those
    strings reaches this client too. `textContent` cannot interpret markup, so with no markup sink
    on the page there is no escaping to get right and no injection sink to audit. This asserts the
    sink is absent rather than that the escaping is correct, which is the stronger property."""
    for name in _MODULES:
        source = _source(name)
        for sink in _MARKUP_SINKS:
            # Checked against the code, not the raw text: `render.js`'s own docstring NAMES these
            # sinks while explaining that it does not use them, and a raw substring search would
            # fail on the documentation of the property it is checking.
            assert sink not in _code_only(source), f"{name} can write markup via {sink}"


def test_fetch_appears_in_exactly_one_client_module() -> None:
    """`api` is the single `fetch` wrapper.

    The guarantee the design spec sells hardest -- "the interface is provably incapable of sending
    positions, equity or trade history anywhere but the local process" -- is enforced by
    `connect-src 'self'` and AUDITED by a reader. A reader can audit one file."""
    callers = sorted(name for name in _MODULES if "fetch(" in _code_only(_source(name)))
    assert callers == ["api.js"]


def test_no_client_module_opens_a_second_kind_of_connection() -> None:
    """`XMLHttpRequest`, `WebSocket`, `navigator.sendBeacon` -- none of them, anywhere. And
    `EventSource` in exactly one place.

    `connect-src 'self'` covers all four in the browser, so this is not the security boundary; it
    is the pin that keeps `api.js`'s "the only place `fetch` appears" from being technically true
    and substantively false.

    **`EventSource` arrived at #537 and is scoped to `live.js`, which is how it lands
    deliberately** -- the previous revision of this docstring said so in advance, and the shape of
    the exemption is the shape it predicted: one named file, not a relaxed rule. It matters
    because `live.js` is the second way bytes enter this client, and the whole audit story is that
    a reader can find every one of them by opening two files. That story survives two transports;
    it would not survive an unbounded number."""
    for name in _MODULES:
        code = _code_only(_source(name))
        for transport in ("XMLHttpRequest", "WebSocket", "sendBeacon"):
            assert transport not in code, f"{name} opens a {transport}"
        if name != "live.js":
            assert "EventSource" not in code, (
                f"{name} opens an EventSource; live.js is the only module that may"
            )

    # And the exemption is not vacuous: `live.js` really does open one.
    assert "EventSource" in _code_only(_source("live.js"))


def test_the_event_stream_carries_no_figures() -> None:
    """The client's data still enters through ONE `fetch`, and the stream is not a second door.

    `keel/web/events.py` puts one key in a tick's `data` -- a revision marker -- and this asserts
    the client half of the same bargain: `live.js` reads `revision` and nothing else off a tick,
    so a payload key added to the stream later cannot quietly start being rendered from a
    transport that `api.js`'s "the only place `fetch` appears" docstring does not cover.

    Checked on the SOURCE rather than at run time because a browser cannot run here -- so it is a
    statement about what this module was written to read, which is the property a reviewer would
    otherwise have to take on trust."""
    code = _code_only(_source("live.js"))
    reads = sorted(set(re.findall(r"\breading\.data\.([a-z_]+)", code)))
    assert reads == ["revision"], f"live.js reads {reads} off a tick; the stream carries figures"


# -- the two sources of truth that must not drift --------------------------------------------------


def test_the_client_palette_is_byte_identical_to_the_rendered_one() -> None:
    """`css/keel.css`'s palette equals `render.py::_STYLE`'s, token for token, in both themes.

    The client's stylesheet is a COPY, and this is what makes the copy safe: every WCAG ratio in
    `tests/web/test_palette_contrast.py` is measured against `render.py`, so it guards this file
    only for as long as the two agree. Equality here is what makes that transitive.

    The duplication has an end date. At #540 `render.py` goes, this test goes with it, and
    `test_palette_contrast._load_themes` re-points at this file."""
    client = _CSS.read_text(encoding="utf-8")
    for dark in (False, True):
        theirs = _theme_palette(render._STYLE, dark=dark)
        ours = _theme_palette(client, dark=dark)
        assert ours == theirs, (
            f"the {'dark' if dark else 'light'} palettes have diverged; the contrast gate in "
            "test_palette_contrast.py only measures render.py's"
        )


def test_the_palette_comparison_would_notice_a_divergence() -> None:
    """The premise: the comparison above is only worth running if it can fail.

    Asserted because both sides are parsed by the same function from files that were written to
    match -- exactly the shape of test that passes because it is comparing something to itself."""
    client = _CSS.read_text(encoding="utf-8")
    tampered = client.replace("--good: #1f5f4f", "--good: #1f5f50", 1)
    assert tampered != client, "the light --good declaration is no longer spelled as expected"
    assert _theme_palette(tampered, dark=False) != _theme_palette(render._STYLE, dark=False)


def test_the_python_and_javascript_route_tables_agree() -> None:
    """`staticfiles.CLIENT_ROUTES` equals `main.js`'s `ROUTES`, in order.

    Two tables, because one is Python and one is JavaScript and neither can import the other. The
    consequence of drift is specific: a view added to `main.js` alone routes fine on a click and
    404s on a reload, which is the sort of bug that reaches a user rather than CI."""
    names = _js_route_names()
    assert names == list(staticfiles.CLIENT_ROUTES)


def test_the_route_table_parser_actually_found_something() -> None:
    """The premise for the test above: an empty parse compared to an empty tuple is green.

    `CLIENT_ROUTES` has seven entries today, and the assertion is on a floor rather than on the
    exact number so that #537 adding a view does not have to edit this test to keep it honest."""
    names = _js_route_names()
    assert len(names) >= 7, f"parsed {names} out of main.js -- the parser has stopped working"
    assert "status" in names


def _js_route_names() -> list[str]:
    """The `name:` of every entry in `main.js`'s `ROUTES` array, in source order."""
    source = _source("main.js")
    start = source.index("const ROUTES = [")
    end = source.index("];", start)
    return re.findall(r'\{\s*name:\s*"([a-z]+)"', source[start:end])


def test_the_mount_prefix_is_spelled_the_same_everywhere() -> None:
    """`/static/` is written in three files and must agree in all three.

    It becomes `/` at #540, when the shell moves off the static prefix and onto the root the
    rendered pages vacate. This test is what makes that a mechanical edit: change one, and the
    other two fail by name."""
    prefix = staticfiles.STATIC_PREFIX
    assert f'const BASE = "{prefix}";' in _source("main.js")

    html = _INDEX.read_text(encoding="utf-8")
    hrefs = re.findall(r'<li><a href="([^"]+)">', html)
    assert hrefs == [prefix + name for name in staticfiles.CLIENT_ROUTES]

    for asset in ("css/keel.css", "js/main.js"):
        loaded = f'href="{prefix}{asset}"' in html or f'src="{prefix}{asset}"' in html
        assert loaded, f"index.html does not load {asset} from {prefix}"


# -- the markup ------------------------------------------------------------------------------------


def test_the_shell_carries_its_accessibility_affordances() -> None:
    """The parts of the accessibility baseline that live in the shipped markup.

    Everything here is a property of the bytes. Whether a screen reader ANNOUNCES the live region,
    and whether the focus ring is visible, are not -- see this module's docstring."""
    html = _markup_only(_INDEX.read_text(encoding="utf-8"))

    assert '<html lang="en">' in html, "a page with no language is a page a reader mispronounces"
    assert 'name="viewport"' in html, "no viewport meta means no responsive layout on any device"

    # The one live region, and both attributes on it. `aria-atomic` is what makes a reader speak
    # the whole banner rather than the single word that changed.
    engine = re.search(r"<div id=\"engine\"[^>]*>", html)
    assert engine is not None
    assert 'role="status"' in engine.group(0)
    assert 'aria-live="polite"' in engine.group(0)
    assert 'aria-atomic="true"' in engine.group(0)

    # And ONLY one: a live region around the data would re-announce every table twice a minute.
    assert html.count("aria-live") == 1, "exactly one live region -- see index.html's note"

    assert '<a class="skip" href="#view">' in html, "keyboard users need a way past the nav"
    assert '<main id="view" tabindex="-1">' in html, (
        "main must be focusable-by-script so a route change can move focus to it"
    )
    assert '<nav aria-label="Views">' in html, "a nav landmark needs a name to be worth landing on"
    assert "<noscript>" in html, "a shell with no fallback message is a blank page"


def test_the_shell_ships_no_inline_script_or_style() -> None:
    """`_STATIC_CSP` is `default-src 'self'` with no `'unsafe-inline'`.

    So an inline block here is not a style violation caught in review -- it is markup the browser
    silently refuses at run time, which is the worst place to find out. `<base href>` is impossible
    for the same reason (`base-uri 'none'`), which is why every href in the shell is absolute."""
    html = _markup_only(_INDEX.read_text(encoding="utf-8")).lower()
    assert "<style" not in html
    assert "<base" not in html
    # A `<script>` with a `src` is the only permitted form.
    for tag in re.findall(r"<script[^>]*>", html):
        assert "src=" in tag, f"inline script in index.html: {tag}"
        assert 'type="module"' in tag, f"the client is ES modules: {tag}"


def test_the_client_loads_nothing_from_a_third_party_origin() -> None:
    """No CDN, no web font, no remote anything -- in the markup and in the stylesheet.

    `Content-Security-Policy: default-src 'self'` already makes this unenforceable-by-accident in
    a browser. This asserts we never ship the attempt, so the header is never the only thing
    standing between the page and an external request."""
    html = _markup_only(_INDEX.read_text(encoding="utf-8"))

    # **`<a href>` is navigation, not a load, and #539 makes that distinction load-bearing.** The
    # documentation link opens keeltrading.com in a new tab; nothing is fetched into this page,
    # nothing is bundled and nothing is cached, so `default-src 'self'` is untouched by it (the
    # spec: "Outbound links are navigation, not connections"). Every OTHER way a URL can appear in
    # this markup is a subresource, and those stay same-origin: `src` on a script or an image,
    # and `href` on a `<link>` -- the stylesheet, the manifest and the icons.
    #
    # The check is narrowed rather than dropped. An earlier spelling of this test matched `href`
    # anywhere, which would now pass only by listing the docs URL as an exception -- and an
    # exception list is what turns a rule into a habit of adding to a list.
    loads = re.findall(r'<(?:script|img|iframe|source|embed)\b[^>]*\bsrc="([^"]*)"', html)
    loads += re.findall(r'<link\b[^>]*\bhref="([^"]*)"', html)
    remote = [url for url in loads if url.startswith(("http://", "https://", "//"))]
    assert remote == [], f"index.html loads from another origin: {remote}"

    # And what IS allowed outbound is exactly one link, to the documentation, opened safely.
    anchors = re.findall(r"<a\b[^>]*>", html)
    outbound = [tag for tag in anchors if re.search(r'href="(?:https?:)?//', tag)]
    assert len(outbound) == 1, f"expected one outbound link, found {len(outbound)}: {outbound}"
    assert 'href="https://keeltrading.com/en/docs/"' in outbound[0], outbound[0]
    # `noopener` is the one that matters: a tab opened without it holds a `window.opener` handle
    # back to a trading console on a token-bearing origin.
    assert 'rel="noopener noreferrer"' in outbound[0], outbound[0]

    css = _CSS.read_text(encoding="utf-8")
    assert "@import" not in css, "a stylesheet that imports another can import a remote one"
    # `url()` covers fonts, background images and cursors in one check. There are none today, and
    # a local one added later should still come through this test rather than around it: an
    # `@font-face` is the single likeliest way a third-party asset ever enters this page.
    assert "url(" not in css, "no external asset references -- see js/external/README.md"


def test_the_stylesheet_is_responsive_and_theme_aware() -> None:
    """Responsive from the start rather than retrofitted, which is what the issue asks for.

    Asserted as "the mechanisms are present", not as "the layout is correct" -- the latter needs a
    viewport. `prefers-color-scheme` was already there; the two width breakpoints and the
    scroll-containment rule are what this issue adds."""
    css = _CSS.read_text(encoding="utf-8")
    assert "prefers-color-scheme: dark" in css
    widths = re.findall(r"@media \(max-width: ([\d.]+)rem\)", css)
    assert len(widths) >= 2, f"only {widths} -- the layout was not built responsive"
    assert "overflow-x: auto" in css, "a wide table must scroll inside itself, not scroll the page"
    # The other half of that promise, and the half a scrolling table does not deliver: a file
    # path or a dotted call site is an unbreakable run that sets the MINIMUM width of the
    # paragraph holding it, and a paragraph is not inside a scroller. Found by driving a real
    # browser at 360px -- `/activity` overflowed the document by 194px on its log path and
    # `/gates` by 123px on a `<code>`, with every table on both pages scrolling correctly.
    # `anywhere` specifically: it is the only value that counts towards `min-content`, so it is
    # the only one that stops the long word forcing its ancestors wide.
    assert "overflow-wrap: anywhere" in css, (
        "an unbreakable path in a paragraph widens the document; see the note beside this rule"
    )
    assert "grid-template-columns: repeat(auto-fit" in css
    assert ":focus-visible" in css, "visible focus is an acceptance criterion"


# -- over the wire ---------------------------------------------------------------------------------
#
# `deployment` and `running` come from `tests/web/conftest.py`, so the shell is served through the
# same admission, the same host check and the same header code path as every other response on
# this server -- not through a second, more permissive one stood up for the client's convenience.
#
# Those two fixtures were `test_server.py`'s and MOVED to the conftest unchanged when this module
# arrived; that module's docstring records it, and every one of its own tests still requests them
# by the same names with the same bodies behind them. `_request` and `_session` are imported
# rather than moved, because they are plain helper functions and importing one does not shadow a
# test parameter the way importing a fixture does.

from tests.web.test_server import _request, _session  # noqa: E402


def test_the_shell_is_served_at_the_static_prefix(running) -> None:  # type: ignore[no-untyped-def]
    status, headers, body = _request(running, "/static/", cookie=_session(running))
    assert status == 200
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert '<main id="view"' in body


@pytest.mark.parametrize("name", staticfiles.CLIENT_ROUTES)
def test_every_client_route_serves_the_shell_on_a_reload(name: str, running) -> None:  # type: ignore[no-untyped-def]
    """**Deep links and reloads.**

    This is the whole reason the server knows about client routes at all: `main.js` routes with
    `pushState`, so `/static/insights` appears in the address bar, and without this a reload or a
    bookmark asks for a file that does not exist. Parametrised over `CLIENT_ROUTES` itself so a
    view added there is covered without a test edit."""
    status, headers, body = _request(running, f"/static/{name}", cookie=_session(running))
    assert status == 200, name
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert '<script type="module"' in body


def test_a_path_that_is_not_a_client_route_is_still_a_404(running) -> None:  # type: ignore[no-untyped-def]
    """The list is closed, and this is what that buys.

    A wildcard fallback would answer this 200 with HTML. That is not a cosmetic difference: the
    same wildcard turns a missing `.js` into a `text/html` response the browser refuses to execute
    under `nosniff`, and reports as a MIME-type error naming the module rather than as "that file
    is not there"."""
    status, _headers, _body = _request(running, "/static/glossary", cookie=_session(running))
    assert status == 404


def test_a_missing_asset_is_a_404_and_never_the_shell(running) -> None:  # type: ignore[no-untyped-def]
    """The failure mode the closed list exists to prevent, asserted directly."""
    status, _headers, body = _request(running, "/static/js/nope.js", cookie=_session(running))
    assert status == 404
    # `<main>` is the WRONG marker here and this test failed on it first: `server._refuse` renders
    # its own error page through `render.page`, which has a `<main>` of its own. The shell is
    # identified by the thing only the shell has -- the module script tag.
    assert "<script" not in body


def test_a_real_file_wins_over_a_client_route(running) -> None:  # type: ignore[no-untyped-def]
    """`index.html` is reachable by its own name, not shadowed by the fallback."""
    status, headers, body = _request(running, "/static/index.html", cookie=_session(running))
    assert status == 200
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert "<noscript>" in body


def test_the_client_route_fallback_is_behind_the_same_admission(running) -> None:  # type: ignore[no-untyped-def]
    """Never weakened. A path that resolves to the shell is not exempt from the loopback-plus-
    session model for having been synthesised rather than found on disk."""
    status, _headers, _body = _request(running, "/static/status")  # no cookie
    assert status == 403


@pytest.mark.parametrize(
    ("path", "content_type"),
    [
        ("/static/js/main.js", "text/javascript; charset=utf-8"),
        ("/static/js/api.js", "text/javascript; charset=utf-8"),
        ("/static/js/render.js", "text/javascript; charset=utf-8"),
        ("/static/js/chart.js", "text/javascript; charset=utf-8"),
        ("/static/js/live.js", "text/javascript; charset=utf-8"),
        ("/static/js/format.js", "text/javascript; charset=utf-8"),
        ("/static/css/keel.css", "text/css; charset=utf-8"),
    ],
)
def test_every_shipped_asset_is_served_with_the_right_type(
    path: str,
    content_type: str,
    running,  # type: ignore[no-untyped-def]
) -> None:
    """A `.js` served as `text/plain` is a module the browser refuses to RUN under `nosniff`, not
    one it sniffs its way around -- `staticfiles._CONTENT_TYPES` exists for this and this asserts
    it end to end for every file the client actually loads."""
    status, headers, body = _request(running, path, cookie=_session(running))
    assert status == 200, path
    assert headers["Content-Type"] == content_type
    assert body, path


def test_the_shell_is_served_with_the_static_header_set(running) -> None:  # type: ignore[no-untyped-def]
    """The shell is the one response on this server that must permit scripts, and `_STATIC_CSP` is
    the tightest policy that does. Asserted here on the SHELL specifically, because
    `test_server.py` asserts it on the placeholder asset that used to be the only HTML under
    `/static/`."""
    status, headers, _body = _request(running, "/static/status", cookie=_session(running))
    assert status == 200
    csp = headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "connect-src 'self'" in csp
    assert "base-uri 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "'unsafe-inline'" not in csp
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert "Strict-Transport-Security" not in headers


def test_the_status_payload_carries_every_field_the_status_view_places(running) -> None:  # type: ignore[no-untyped-def]
    """**Parity, checked against the payload rather than against the screen.**

    `js/render.js::statusView` reads a fixed set of keys. A browser cannot run here, so the risk
    this test removes is the one a browserless suite is otherwise blind to: `render.js` reaching
    for a key `payload.py` does not emit, rendering `?` forever, exactly as `render.py`'s five
    `getattr(..., default)` calls render blanks today (see `statusView`'s own docstring). The key
    list is parsed out of the JavaScript rather than restated here, so it cannot drift.
    """
    status, _headers, body = _request(running, "/api/status", cookie=_session(running))
    assert status == 200
    document = json.loads(body)
    # A migrated but empty deployment: `engine` is running, so `data` is a real payload.
    assert document["engine"]["value"] == "running", document
    data = document["data"]

    for path in _status_view_keys():
        cursor = data
        for part in path.split("."):
            assert isinstance(cursor, dict), f"{path} -- {part} is not an object in the payload"
            assert part in cursor, f"statusView reads data.{path}; /api/status does not send it"
            cursor = cursor[part]


def _view_keys(view: str, root: str) -> list[str]:
    """Every `<root>.<...>` path a view function reads, from the source.

    Generalised from `_status_view_keys` at #537, when six more views needed the same check --
    and the generalisation is a REQUIRED second argument, never a default. The views do not all
    name their payload `data`: `insightsView` takes `insights` and `journal`, because it reads two
    endpoints. A helper defaulting to `"data"` would have scanned nothing for that view, found no
    keys, and passed -- over the view with the most keys in it. Every existing caller passes
    `("statusView", "data")` and gets exactly what it got before.

    Row-level keys (`row.qty`, `row.product_id`) are out of scope: they live inside collections
    that are empty on a fresh deployment, so there is nothing to check them against here. They are
    covered by `tests/web/test_payload.py`, which builds populated reports.
    """
    # Scanned over the CODE, not the raw file. `_code_only` strips comments, and the views
    # document the keys they read -- `activityView`'s docstring names `data.scope` while
    # explaining where the current scope comes from. Since a region runs up to the next `export
    # function` LINE, the next view's docstring falls inside the previous view's region, and
    # scanning the raw text reported `setupView` reading a key of `/api/activity`. Found by this
    # test failing on its first run, which is the same way `_status_view_keys` learned it had to
    # be bounded at all. It is the same lesson `_markup_only` records for `index.html`: a file
    # that explains itself well is a file a naive grep cannot check.
    source = _code_only(_source("render.js"))
    start = source.index(f"export function {view}(")
    # BOUNDED at the next top-level export, not run to end-of-file. Unbounded, this swept up
    # `buildLine`'s `data.build` -- a key of `/api/config`, not of `/api/status` -- and reported a
    # payload gap that does not exist. Found by this test failing on its first run.
    end = source.index("\nexport function ", start + 1)
    # The negative lookahead drops METHOD calls: `data.data_freshness.map(` must contribute
    # `data_freshness`, not `data_freshness.map`. Backtracking does that -- the two-segment
    # alternative fails the lookahead on `(` and the one-segment one succeeds on `.`.
    found = re.findall(rf"\b{root}\.([a-z_]+(?:\.[a-z_0-9]+)?)\b(?!\s*\()", source[start:end])
    # Neither `.length` nor `.display` is a payload key: the first is a list length in JavaScript,
    # the second is half of a `Field` whose presence is already checked one level up. Two names
    # rather than a general rule, because a THIRD non-payload member appearing here should be
    # noticed, not absorbed.
    return sorted({path for path in found if not path.endswith((".length", ".display"))})


def _status_view_keys() -> list[str]:
    """`statusView`'s keys. Kept under its own name because three tests call it that."""
    return _view_keys("statusView", "data")


#: Each ported view, the parameter it reads a payload through, and the endpoint that fills it.
#:
#: `insightsView` appears twice because it reads two endpoints. `api.py` splits them so that "one
#: sortable collection per endpoint keeps `?sort=` unambiguous", and both name `/insights` as
#: their `html_route` -- which is the server saying they are one page.
_VIEW_ENDPOINTS: tuple[tuple[str, str, str], ...] = (
    ("setupView", "data", "/api/setup"),
    ("activityView", "data", "/api/activity"),
    ("insightsView", "insights", "/api/insights"),
    ("insightsView", "journal", "/api/journal"),
    ("rulesView", "data", "/api/rules"),
    ("venuesView", "data", "/api/venues"),
    ("gatesView", "data", "/api/gates"),
)


@pytest.mark.parametrize(("view", "root", "endpoint"), _VIEW_ENDPOINTS)
def test_every_ported_view_reads_only_keys_its_endpoint_sends(
    view: str,
    root: str,
    endpoint: str,
    running,  # type: ignore[no-untyped-def]
) -> None:
    """**The status parity check, for the six views #537 ports.**

    A browser cannot run here, so the risk this removes is the one a browserless suite is
    otherwise blind to: a view reaching for a key the payload does not emit, rendering `?`
    forever -- which is what `render.py`'s `getattr(..., default)` calls do on the rendered pages
    today (#548, plus the two more this issue found). Checking in the direction the API actually
    answers is the only check available without a DOM, and it is a real one: it is precisely what
    would have caught #548 had it existed when `render.py` was written.

    The key list is parsed out of the JavaScript rather than restated here, so it cannot drift.
    """
    status, _headers, body = _request(running, endpoint, cookie=_session(running))
    assert status == 200, endpoint
    document = json.loads(body)
    # A migrated but empty deployment: `engine` is running, so `data` is a real payload.
    assert document["engine"]["value"] == "running", document
    data = document["data"]

    for path in _view_keys(view, root):
        cursor = data
        for part in path.split("."):
            assert isinstance(cursor, dict), f"{path} -- {part} is not an object in {endpoint}"
            assert part in cursor, f"{view} reads {root}.{path}; {endpoint} does not send it"
            cursor = cursor[part]


def test_the_view_key_scan_reads_every_ported_view() -> None:
    """The premise: a scan that found nothing would make the test above pass on any payload.

    Non-empty per view, so a parse that silently returned `[]` fails here by name. The three spot
    checks are the keys most likely to be got wrong -- two of them are payload fields this issue
    ADDED (`status_note`, `curve`), which is exactly the shape of key a client can start reading
    before the server sends it."""
    for view, root, _endpoint in _VIEW_ENDPOINTS:
        assert _view_keys(view, root), f"{view} reads nothing off {root} -- the parser has broken"

    assert "status_note" in _view_keys("activityView", "data")
    assert "curve" in _view_keys("insightsView", "journal")
    assert "steps" in _view_keys("setupView", "data")


def test_the_status_key_scan_found_the_keys_it_claims_to_check() -> None:
    """The premise. A regex that matched nothing would make the test above pass on any payload."""
    keys = _status_view_keys()
    assert "equity.high_water_mark" in keys
    assert "autonomy.live" in keys
    assert "withdrawal_attestation.state" in keys
    assert len(keys) >= 15, keys

    # The two filters, asserted rather than assumed -- both were added because this test failed
    # on a real key that was not a key. A method name or `.length` reaching the payload check
    # reports a gap in `/api/status` that does not exist, which is a false failure pointing at
    # the wrong file entirely.
    assert "data_freshness" in keys and "data_freshness.map" not in keys
    assert not any(key.endswith(".length") for key in keys), keys

    # And the scan is bounded: `data.build` belongs to `/api/config` and is read by `buildLine`,
    # which lives BELOW `statusView` in the same file.
    assert "build" not in keys, keys
