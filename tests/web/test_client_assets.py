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
from pathlib import Path

import pytest

from keel.web import staticfiles
from tests.web.test_palette_contrast import _load_themes


def _P(rest: str) -> str:
    """A request path under the mount, composed rather than spelled.

    Every one of these was a literal `/static/...` until #540 moved the mount to `/`. Composed
    from the constant the client and the server both read, the next move needs no edit here --
    which is the same argument `test_the_mount_prefix_is_spelled_the_same_everywhere` makes about
    the three places that legitimately DO spell it."""
    return staticfiles.STATIC_PREFIX + rest


_STATIC = staticfiles.STATIC_ROOT
_JS = _STATIC / "js"
_EXTERNAL = _JS / "external"
_INDEX = _STATIC / "index.html"
_CSS = _STATIC / "css" / "keel.css"

#: The client's modules, in the spec's own order (§"JS file structure"). A closed list, so a file
#: appearing under `js/` without a test author noticing fails `test_the_client_ships_exactly_the_
#: declared_modules` rather than shipping unexamined.
#:
#: `live.js` arrived at #537 and `docs.js` at #539, which is what the first revision of this line
#: predicted ("a fifth module is a design decision (#537 adds `chart`, `live`, `docs`, `sw`) and
#: should arrive with the list updated, not silently"). `chart.js` arrived with them and LEFT in
#: console PR3, with the Insights view's equity charts -- removed from this list in the same
#: commit, which is the same rule run the other way.
#:
#: `actions.js` is the write boundary, split out of `main.js` after the reference implementation's
#: own shape: youperiod.app keeps `main.js` to "DOM manipulation and event listener attachment"
#: and puts the storage boundary in `data-manager.js` behind a small interface. #540 gave keel a
#: write surface and left all of it -- token, submit handler, outcome memory, sentence-picking --
#: in `main.js`, which is the module that reference keeps emptiest.
#:
#: **`sw` arrived at #538 and is deliberately NOT in this list**, because it is not under `js/`:
#: a service worker's scope is its own directory, so `js/sw.js` would be scoped to `/static/js/`
#: and could not answer a navigation to a view such as `/pulse`. It sits at the static root instead,
#: and `tests/web/test_pwa.py::test_the_worker_is_served_from_the_scope_it_must_control` asserts
#: that placement rather than leaving it to whoever next reads the spec's file list.
#:
#: **`theme.js` arrived at #597 and is the one entry that is not an ES module**, on purpose. It
#: exists to restore the reader's light/dark choice BEFORE FIRST PAINT, and a `type="module"`
#: script is deferred by definition -- the flash of the wrong theme it exists to prevent would
#: happen while the module was still loading. It is a classic parser-blocking script in `<head>`
#: instead: same-origin, so `default-src 'self'` permits it, and it joins the closed list -- and
#: with it every scan this module runs over `_MODULES` -- precisely because shipping under `js/`
#: without a test author noticing is what this list exists to prevent.
_MODULES = (
    "main.js",
    "api.js",
    "actions.js",
    "render.js",
    "live.js",
    "format.js",
    "docs.js",
    "theme.js",
)

#: The modules held to "no arithmetic, no judgement, no derived display string".
#:
#: `render.js` is the spec's own choice ("so that a reviewer can confirm the absence by reading one
#: file"). `chart.js` was the second entry from #537 -- added rather than exempted, because a chart
#: module is exactly where "just this one subtraction, it is only a pixel" gets written -- until
#: console PR3 retired the charts and the module with them. A tuple of one, and kept a tuple: the
#: next module that touches payload values joins it here rather than inventing its own scan. The
#: design spec's §Dependencies claim -- "the client performs no arithmetic", the sentence that
#: removes the need for a decimal library -- is a property of the whole client.
#:
#: `main.js` is NOT here and cannot be: it owns timers and route indices, which are arithmetic
#: about the interface rather than about money. `api.js` is not either -- it reads HTTP statuses.
#: The line is drawn at the modules that touch PAYLOAD VALUES, which is where it means something.
_DERIVATION_FREE = ("render.js",)


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


def _named_imports(code: str) -> list[tuple[list[str], str]]:
    """`([names], "./module.js")` for every `import { ... } from "..."` in `code`."""
    found = []
    for names, module in re.findall(r"import\s*\{([^}]*)\}\s*from\s*\"([^\"]+)\"", code):
        found.append(([n.strip() for n in names.split(",") if n.strip()], module))
    return found


def test_every_named_import_is_an_export_of_a_module_that_ships() -> None:
    """A browser links every ES module before it runs any of them, so ONE import naming a module
    that is not shipped -- or a name that module does not export -- blanks the whole console with
    a `SyntaxError` nobody in CI sees (`node --check` parses one file at a time). Console PR3
    deleted `chart.js`; an import of it left in `main.js` or `render.js` would have been exactly
    that, with every Python gate green."""
    exports = {
        name: set(
            re.findall(
                r"^export (?:async )?(?:function|const|let)\s+(\w+)",
                _code_only(_source(name)),
                re.M,
            )
        )
        for name in _MODULES
    }
    seen = 0
    for name in _MODULES:
        for names, module in _named_imports(_comments_only(_source(name))):
            target = module.removeprefix("./")
            assert target in _MODULES, f"{name} imports {module}, which does not ship"
            missing = set(names) - exports[target]
            assert not missing, f"{name} imports {sorted(missing)} from {module}; not exported"
            seen += len(names)
    assert seen >= 30, f"the import scan saw only {seen} names; it has stopped working"


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

    Parametrised over `_DERIVATION_FREE` rather than written for one file, so a module that joins
    that tuple is scanned without a second test."""
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
    assert "+" in _code_only("const total = a + b;\n")
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


# -- a lexer that keeps string literals ----------------------------------------------------------


def _comments_only(source: str) -> str:
    """`source` with `//` and `/* */` comments removed, but STRING LITERALS LEFT INTACT.

    The same four-state walk `_code_only` runs, with the one difference this test needs: a string
    is copied through rather than blanked. `_code_only` exists to make the arithmetic scan
    impossible to fool with an operator quoted in a string or a comment, and blanking strings is
    right for that job -- but it means a literal such as `"button"` cannot be found in its output
    at all, which is exactly what the structural pins built on this look for. Comments still have
    to go: a docstring that NAMES the thing a function refuses to build would otherwise be found
    instead of the code it describes -- the same trap `_markup_only` exists for on `index.html`.
    (Written for #602's chart pins, which left with the charts in console PR3.)
    """
    out: list[str] = []
    i, n = 0, len(source)
    while i < n:
        ch = source[i]
        if ch in "'\"":
            quote = ch
            start = i
            i += 1
            while i < n:
                if source[i] == "\\":
                    i += 2
                    continue
                if source[i] == quote:
                    i += 1
                    break
                i += 1
            out.append(source[start:i])
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


def test_no_client_module_can_write_markup() -> None:
    """No `innerHTML`, anywhere in the client.

    **This is where `tests/web/test_render.py` went at #540.** That module existed for one
    security property -- "nothing reaching the page is trusted markup" -- and tested it by feeding
    `<script>alert("x")</script>` through `render_rules` and `render_venues` and asserting the
    output was escaped. Both functions are deleted, and so is the escaping they did.

    The property did not go with them; it got stronger, and this is the assertion that carries it.
    `render.py::esc` had to be CORRECT on every string it touched, and a call site that forgot to
    invoke it was a hole no test of `esc` would find. `textContent` cannot interpret markup at
    all, so with no markup sink anywhere on the page there is nothing to escape and no call site
    that can forget. Asserting the SINK IS ABSENT is a stronger claim than asserting the escaping
    is right, and it is checkable over the whole client rather than function by function.

    Rule names, product ids, log highlights and adapter error strings all still originate outside
    this process -- an operator names a rule, the engine writes a highlight, and a third-party
    package chooses what its exception says. Every one of them reaches this client. None of them
    can reach a parser."""
    for name in _MODULES:
        source = _source(name)
        for sink in _MARKUP_SINKS:
            # Checked against the code, not the raw text: `render.js`'s own docstring NAMES these
            # sinks while explaining that it does not use them, and a raw substring search would
            # fail on the documentation of the property it is checking.
            assert sink not in _code_only(source), f"{name} can write markup via {sink}"


def test_the_write_token_lives_only_in_the_write_boundary() -> None:
    """**The module split this file's `_MODULES` note describes, asserted rather than intended.**

    The design spec's reference implementation keeps `main.js` to "DOM manipulation and event
    listener attachment" and puts the storage boundary in a module of its own behind a small
    interface. #540 gave keel a write surface and left every part of it in `main.js`: the session
    write token, the submit handler, the memory of what each action reported, and the choice of
    which server field to show. `actions.js` owns all of that now.

    The token is the sharpest part to pin, and the rule is about where it is HELD rather than
    where the word appears. `api.js` names it because it is the parameter it puts in a header --
    that module is the only one allowed to open a connection, so it is the only one that can send
    it -- but it takes it as an argument and keeps nothing. `actions.js` is the one module with a
    variable holding it between calls.

    Stated that way rather than "the word appears once", because a credential that acquires a
    second home is a credential the next thing added there will read, and that is what this
    catches. A parameter passed straight into a header is not a home.
    """
    # Asserted as BEHAVIOUR, not as prose. `actions.js` names `X-Keel-CSRF` in a comment listing
    # what it hides from its callers, and a raw-source search would read that explanation as a
    # violation of the thing it explains -- the same trap `_markup_only` exists for on
    # `index.html`. What actually matters is that the boundary builds no headers at all: the
    # transport is `api.js`'s job and nothing here should be assembling a request.
    boundary = _code_only(_source("actions.js"))
    assert "headers" not in boundary.lower(), (
        "the write boundary assembles a request; that belongs to api.js, which sends it"
    )
    assert "let token" in boundary, "the write boundary holds no token"

    assert "X-Keel-CSRF" in _source("api.js"), "nothing sends the token"
    transport = _code_only(_source("api.js"))
    assert "let csrf" not in transport and "const csrf" not in transport, (
        "api.js stores the write token; it takes one as an argument and keeps nothing"
    )

    for name in _MODULES:
        if name in ("actions.js", "api.js"):
            continue
        assert "csrf" not in _code_only(_source(name)).lower(), (
            f"{name} names the write token; it is held by actions.js and sent by api.js"
        )


def test_the_write_boundary_opens_no_connection_and_builds_no_dom() -> None:
    """It sits between the two modules that do, and touches neither's job.

    `api.js` is still the only module that calls `fetch` (pinned below), and the DOM is
    `render.js`'s and the listener's. A boundary module that reached into either would be a third
    place to look for behaviour that already has two homes."""
    code = _code_only(_source("actions.js"))
    for forbidden in ("fetch(", "document.", "querySelector", "createElement", "innerHTML"):
        assert forbidden not in code, f"actions.js reaches past its boundary via {forbidden}"


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


# `test_the_client_palette_is_byte_identical_to_the_rendered_one` and its mutation check stood
# here, and both were deleted at #540 exactly as their own docstring said they would be: "the
# duplication has an end date. At #540 `render.py` goes, this test goes with it, and
# `test_palette_contrast._load_themes` re-points at this file." It does. There is one stylesheet
# now, the contrast gate measures it directly, and there is nothing left to hold in agreement.
#
# #593 puts a DIFFERENT pin in the same spot: the palette's upstream is no longer inside this
# repository at all.


def test_the_palette_wears_keeltrading_coms_identity() -> None:
    """**The literals that make the app look like keeltrading.com (#593), pinned.**

    The palette's upstream is `../keeltrading.com/src/styles/global.css` -- a SIBLING repository
    this CI cannot read, so a test cannot compare the copy against its source the way the old
    render.py pin could. What it can do is refuse to let the values that CARRY the identity
    drift: `--accent` is the teal every button and equity curve on the site wears, `--bg` is the
    warm paper both products sit on, and `--link` is the teal the site colours its anchors with
    -- which is NOT the dark accent (#6fd0bb beside #57c5af), so an app that styled its links
    with `--accent` showed two link teals the moment somebody opened it beside the site in a
    dark tab. Both themes are pinned, not just the light one: the stylesheet's header claims
    byte-identity for every token with a site counterpart, and half a claim is not a claim.
    Change any of these here without changing the site and the app and its public face are two
    products again -- which is the exact regression #593 exists to close.
    """
    light, dark = _load_themes()
    assert light["accent"] == "#0c5d52", (
        "light --accent is not keeltrading.com's teal (global.css --accent); the identity has "
        "drifted from the site"
    )
    assert light["bg"] == "#f8f7f3", (
        "light --bg is not keeltrading.com's paper (global.css --bg); the identity has drifted "
        "from the site"
    )
    assert dark["accent"] == "#57c5af", (
        "dark --accent is not keeltrading.com's dark teal (global.css dark --accent); the "
        "identity has drifted from the site"
    )
    assert dark["bg"] == "#0f171d", (
        "dark --bg is not keeltrading.com's dark slate (global.css dark --bg); the identity "
        "has drifted from the site"
    )
    assert (light["link"], dark["link"]) == ("#0c5d52", "#6fd0bb"), (
        "--link is not keeltrading.com's link teal (global.css --link, both themes); dark links "
        "would render in a teal the site never shows"
    )


def test_the_python_and_javascript_route_tables_agree() -> None:
    """`staticfiles.CLIENT_ROUTES` equals `main.js`'s `ROUTES`, in order.

    Two tables, because one is Python and one is JavaScript and neither can import the other. The
    consequence of drift is specific: a view added to `main.js` alone routes fine on a click and
    404s on a reload, which is the sort of bug that reaches a user rather than CI."""
    names = _js_route_names()
    assert names == list(staticfiles.CLIENT_ROUTES)


def test_the_route_table_parser_actually_found_something() -> None:
    """The premise for the test above: an empty parse compared to an empty tuple is green.

    The assertion is a FLOOR rather than an exact number, so adding a view does not have to edit
    this test to keep it honest -- which is also why the docstring no longer names a count. It
    said "seven entries today" while `CLIENT_ROUTES` held thirteen, which is the failure mode of
    writing a number into prose beside a test that deliberately does not assert one."""
    names = _js_route_names()
    assert len(names) >= 7, f"parsed {names} out of main.js -- the parser has stopped working"
    assert "pulse" in names


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

    for asset in ("css/keel.css", "js/main.js", "js/theme.js"):
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

    # The TARGET moved in #704 (`#view` -> `#session`, the wrapper that opens above the mode
    # banner) because skipping the nav must not also skip the statement about whether real money
    # is involved. What this line pins is that the affordance EXISTS; which element it lands on,
    # and that the element can take focus, are pinned by
    # `test_skipping_the_nav_does_not_skip_the_mode_banner` and its sibling below.
    assert '<a class="skip" href="#' in html, "keyboard users need a way past the nav"
    assert '<main id="view" tabindex="-1">' in html, (
        "main must be focusable-by-script so a route change can move focus to it"
    )
    assert '<nav aria-label="Views">' in html, "a nav landmark needs a name to be worth landing on"
    assert "<noscript>" in html, "a shell with no fallback message is a blank page"


# -- the header is the brand (#597) ----------------------------------------------------------------


def test_the_header_brand_is_the_mark_and_it_goes_home() -> None:
    """**The brand block: the site's mark and the wordmark, in ONE anchor to `/`.**

    Three properties, each answering one symptom the issue reports:

      * the MARK is inline SVG, not an `<img>` -- inline strokes can reference the palette's
        tokens, which is what keeps one geometry readable against the paper and against the
        slate (#593's favicon cannot do that; it is a fixed raster of the light theme);
      * the mark and the wordmark are inside ONE `<a href="/">`: since #540 the app's home is
        `/` (`main.js::BASE`), and a brand where only the text is clickable is a brand half of
        which looks broken;
      * `aria-label="keel home"` because the anchor's accessible name should say where it goes,
        not spell the wordmark twice -- a reader hears "keel home, link".

    The anchor is focusable by being an anchor, and the global `:focus-visible` ring is what a
    keyboard reader sees on it -- asserted on the stylesheet by
    `test_the_stylesheet_is_responsive_and_theme_aware`, which pins that the rule exists."""
    html = _markup_only(_INDEX.read_text(encoding="utf-8"))

    # One brand anchor, and it goes to the mount, not to /status: the home of this app is `/`.
    assert html.count('<a class="brand"') == 1, "the brand block must be exactly one anchor"
    assert 'href="/" aria-label="keel home"' in html, (
        "the brand anchor must go to / and name itself for a reader"
    )

    # The anchor holds BOTH halves of the brand, and only those: the mark, then the wordmark.
    # `aria-hidden` on the svg because the anchor's name is the aria-label above -- a reader
    # would otherwise hear the svg's own label and the wordmark as three names for one link.
    start = html.index('<a class="brand"')
    end = html.index("</a>", start)
    brand = html[start:end]
    assert "<svg" in brand and 'aria-hidden="true"' in brand, "the mark is missing from the brand"
    assert "<span>keel</span>" in brand, "the wordmark is missing from the brand"
    assert brand.index("<svg") < brand.index("<span>keel</span>"), "the mark leads the wordmark"


def test_the_inline_mark_is_the_one_the_icons_carry() -> None:
    """The header's mark is the SAME mark as `icons/keel.svg` (#594 transcribed it from the
    site), not a second drawing of a boat.

    Pinned on the geometry rather than on bytes: the header copy deliberately DIFFERS from the
    file in its colours (token references instead of the favicon's fixed light-theme literals,
    which is the entire reason it is inline -- see the test above). What must not differ is the
    shape: the mast, the two sail strokes and the hull path, and the `1 1` viewBox they were
    transcribed against. A hand-redrawn mark here would be a third identity for one product,
    which is the drift #593 closed."""
    html = _markup_only(_INDEX.read_text(encoding="utf-8"))
    keel_svg = (staticfiles.STATIC_ROOT / "icons" / "keel.svg").read_text(encoding="utf-8")

    start = html.index('<a class="brand"')
    end = html.index("</a>", start)
    inline = html[start:end]

    assert 'viewBox="0 0 1 1"' in inline, "the inline mark must keep the transcribed viewBox"
    for path in re.findall(r'<path d="([^"]+)"/>', keel_svg):
        assert path in inline, f"the header mark is missing the icon's geometry: {path}"
    # corner radius and stroke weight are geometry too: a softer square or a heavier mast
    # would be a third drawing of one boat.
    for attr in re.findall(r'(?:rx|stroke-width)="([^"]+)"', keel_svg):
        assert attr in inline, f"the header mark is missing the icon's {attr} shape"


def test_the_header_carries_a_theme_toggle_and_a_mode_badge() -> None:
    """The other two header surfaces #597 adds, pinned as structure the way the shell's other
    landmarks are above.

    The toggle is a `<button>` (keyboard-operable and announced for what it is), carries the
    site's own accessible name for the same control, and holds BOTH icons inline -- no icon
    font, no external asset, and the stylesheet decides which one shows so the visible icon
    matches the painted theme even before `main.js` has hydrated the click.

    The badge is in the MARKUP and empty, not built by script: it is a landmark of the page a
    reader can find, and `main.js` fills it from the `/api/config` read the shell already makes
    at boot -- which is why the badge is present on every view rather than only where a status
    report happens to load. It has no `role` and no live region: it changes once, at boot, and
    an `aria-live` region is for things that change underneath a reader (see `index.html`'s note
    on the one region the page does carry)."""
    html = _markup_only(_INDEX.read_text(encoding="utf-8"))

    assert '<button type="button" id="theme-toggle"' in html, "the theme toggle is missing"
    assert 'aria-label="Toggle light and dark theme"' in html, (
        "the toggle's name must say what it does -- this is the site's own wording for it"
    )
    assert 'class="icon-sun"' in html and 'class="icon-moon"' in html, (
        "both toggle icons ship inline; the stylesheet decides which shows"
    )
    for icon in re.findall(r'<svg class="icon-(?:sun|moon)"[^>]*>', html):
        assert "currentColor" in icon and 'aria-hidden="true"' in icon, (
            f"an icon that is not token-coloured or is self-announcing: {icon}"
        )

    # A `<summary>` since #755, not a `<span>`: the badge is the summary of the deployment card's
    # native disclosure, which is what gives a touch device a path to the `--db`/`--config` pair
    # that `modeBadge` also writes into a hover-only `title`. The id and both classes are
    # unchanged, and they are what `modeBadge` and `header .mode:empty` actually key on.
    assert '<summary id="mode-badge" class="pill mode"' in html, "the mode badge is missing"
    # Empty in the markup, like #build: filled from the boot config read, hidden until it is.
    assert html.count('id="mode-badge"') == 1


def test_the_theme_choice_is_spelled_where_it_is_stored() -> None:
    """Two files share the light/dark choice and neither imports the other.

    `js/theme.js` READS it before first paint; `main.js` WRITES it when the toggle is pressed.
    A spelling drift between the two is not an error anywhere -- it is a choice that silently
    stops surviving the reload it was stored for, which is the exact acceptance criterion this
    pins ("persists across reloads"). keeltrading.com's `global.css` spells the same key for the
    same reason; the app keeps the same literal so the two products describe one mechanism."""
    boot = _source("theme.js")
    main = _source("main.js")

    assert '"keel-theme"' in boot, "the pre-paint restore does not read the stored choice"
    assert '"keel-theme"' in main, "the toggle does not store its choice where boot restores it"


# -- config parity (#597) --------------------------------------------------------------------------


#: The `render.js` functions that read `/api/config`'s payload, and the parameter each reads it
#: through. `buildLine` has read this payload since #538 with NO parity check of its own -- its
#: keys were covered only by a note in `_view_keys` saying where the scan was bounded. #597 adds
#: a second consumer (`modeBadge`, for the header badge), and with it the check that covers both.
_CONFIG_READERS: tuple[tuple[str, str], ...] = (
    ("buildLine", "data"),
    ("modeBadge", "config"),
    # #704's two, registered WITH them rather than after a review found them missing. The rule
    # this table states is "a new consumer arrives with the check that covers it", and the first
    # cut of #704 added two consumers and left this tuple alone -- so renaming `config.profile`
    # on the client would have passed the whole suite while the chip's profile half rendered
    # empty forever, which is precisely the failure named below.
    ("sessionChip", "config"),
    ("paperBanner", "config"),
    # #755's card, registered in the same commit that adds it, for the reason above: it reads
    # four config keys and a rename on either side would leave it rendering em-dashes forever.
    ("deploymentCard", "config"),
)


@pytest.mark.parametrize(("view", "root"), _CONFIG_READERS)
def test_the_config_payload_carries_every_key_the_header_reads(
    view: str,
    root: str,
    running,  # type: ignore[no-untyped-def]
) -> None:
    """**The key-parity scanner, for `/api/config`.**

    The same contract `test_every_ported_view_reads_only_keys_its_endpoint_sends` holds for the
    seven views: a browser cannot run here, so the failure this prevents is a header reaching
    for a key the payload does not emit -- a badge that renders nothing, forever, with nothing
    in the console naming the gap. The badge hydrates from the ONE endpoint every view reads at
    boot, so this is the parity check that travels with it to every screen."""
    status, _headers, body = _request(running, "/api/config", cookie=_session(running))
    assert status == 200
    document = json.loads(body)
    data = document["data"]

    for path in _view_keys(view, root):
        cursor = data
        for part in path.split("."):
            assert isinstance(cursor, dict), f"{path} -- {part} is not an object in /api/config"
            assert part in cursor, f"{view} reads {root}.{path}; /api/config does not send it"
            cursor = cursor[part]


def test_the_config_key_scan_found_the_keys_it_claims_to_check() -> None:
    """The premise, as ever: a scan that found nothing passes any payload check vacuously."""
    badge = _view_keys("modeBadge", "config")
    assert "mode" in badge, f"the badge's keys did not parse: {badge}"
    assert "db_path" in badge and "config_path" in badge, (
        f"the badge's tooltip names the deployment; its keys did not parse: {badge}"
    )
    assert "build" in _view_keys("buildLine", "data"), "the footer's keys did not parse"


def test_the_shell_ships_no_inline_script_or_style() -> None:
    """`_STATIC_CSP` is `default-src 'self'` with no `'unsafe-inline'`.

    So an inline block here is not a style violation caught in review -- it is markup the browser
    silently refuses at run time, which is the worst place to find out. `<base href>` is impossible
    for the same reason (`base-uri 'none'`), which is why every href in the shell is absolute.

    #597 adds the ONE exception this file has ever needed, and it is not an inline block: a
    same-origin CLASSIC script in `<head>` that restores the stored light/dark choice before
    first paint. A module cannot do that job (deferred by definition), and an inline one is
    refused by the policy -- so the exception is narrow in both directions at once: exactly one
    non-module tag, exactly one file, and it must load BEFORE the stylesheet so the theme
    attribute is set before a single rule is applied to a single element.

    See `js/theme.js`'s own module note, which carries the full argument."""
    html = _markup_only(_INDEX.read_text(encoding="utf-8")).lower()
    assert "<style" not in html
    assert "<base" not in html
    # A `<script>` with a `src` is the only permitted form.
    tags = re.findall(r"<script[^>]*>", html)
    assert tags, "the shell loads no script at all"
    for tag in tags:
        assert "src=" in tag, f"inline script in index.html: {tag}"
    # Everything is an ES module except the one pre-paint classic script...
    classic = [tag for tag in tags if 'type="module"' not in tag]
    assert classic == ['<script src="/js/theme.js">'], (
        f"the one permitted non-module script is the theme restore: {classic}"
    )
    # ...and that one runs before the stylesheet is applied, which is the no-flash property.
    assert html.index('<script src="/js/theme.js"') < html.index('<link rel="stylesheet"'), (
        "the theme restore must load before the stylesheet, or the page paints before it runs"
    )


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
    status, headers, body = _request(running, _P(""), cookie=_session(running))
    assert status == 200
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert '<main id="view"' in body


@pytest.mark.parametrize("name", staticfiles.CLIENT_ROUTES)
def test_every_client_route_serves_the_shell_on_a_reload(name: str, running) -> None:  # type: ignore[no-untyped-def]
    """**Deep links and reloads.**

    This is the whole reason the server knows about client routes at all: `main.js` routes with
    `pushState`, so `/static/rules` appears in the address bar, and without this a reload or a
    bookmark asks for a file that does not exist. Parametrised over `CLIENT_ROUTES` itself so a
    view added there is covered without a test edit."""
    status, headers, body = _request(running, _P(name), cookie=_session(running))
    assert status == 200, name
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert '<script type="module"' in body


def test_a_path_that_is_not_a_client_route_is_still_a_404(running) -> None:  # type: ignore[no-untyped-def]
    """The list is closed, and this is what that buys.

    A wildcard fallback would answer this 200 with HTML. That is not a cosmetic difference: the
    same wildcard turns a missing `.js` into a `text/html` response the browser refuses to execute
    under `nosniff`, and reports as a MIME-type error naming the module rather than as "that file
    is not there"."""
    status, _headers, _body = _request(running, _P("glossary"), cookie=_session(running))
    assert status == 404


def test_the_retired_client_routes_are_a_closed_tuple_of_exactly_these_names() -> None:
    """Console PR1 (#951) retired three views, PR2 two more and PR3 (#953) two more -- Insights and
    Research, folded into Rules. PR4 adds its own."""
    assert staticfiles.RETIRED_CLIENT_ROUTES == (
        "status",
        "activity",
        "gates",
        "orders",
        "positions",
        "insights",
        "research",
    )
    assert set(staticfiles.RETIRED_CLIENT_ROUTES).isdisjoint(staticfiles.CLIENT_ROUTES)


@pytest.mark.parametrize(
    "name", ["status", "activity", "gates", "orders", "positions", "insights", "research"]
)
def test_a_retired_route_serves_the_shell_so_the_client_can_land_on_the_home_view(
    name: str,
    running,  # type: ignore[no-untyped-def]
) -> None:
    """**Review F1.** `main.js`'s `routeFor` sends an unknown name to the home view, but only if
    the shell is delivered. Without this, a bookmark, a cached `start_url` or a `?token=` redirect
    to a retired path is a plain-text 404 whenever no service worker answers the navigation."""
    status, headers, body = _request(running, _P(name), cookie=_session(running))
    assert status == 200, name
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert '<script type="module"' in body


def test_a_retired_route_is_still_behind_the_same_admission(running) -> None:  # type: ignore[no-untyped-def]
    status, _headers, _body = _request(running, _P("status"))  # no cookie
    assert status == 403


def test_a_missing_asset_is_a_404_and_never_the_shell(running) -> None:  # type: ignore[no-untyped-def]
    """The failure mode the closed list exists to prevent, asserted directly."""
    status, _headers, body = _request(running, _P("js/nope.js"), cookie=_session(running))
    assert status == 404
    # `<main>` is the WRONG marker here and this test failed on it first: `server._refuse` renders
    # its own error page through `render.page`, which has a `<main>` of its own. The shell is
    # identified by the thing only the shell has -- the module script tag.
    assert "<script" not in body


def test_a_real_file_wins_over_a_client_route(running) -> None:  # type: ignore[no-untyped-def]
    """`index.html` is reachable by its own name, not shadowed by the fallback."""
    status, headers, body = _request(running, _P("index.html"), cookie=_session(running))
    assert status == 200
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert "<noscript>" in body


def test_the_client_route_fallback_is_behind_the_same_admission(running) -> None:  # type: ignore[no-untyped-def]
    """Never weakened. A path that resolves to the shell is not exempt from the loopback-plus-
    session model for having been synthesised rather than found on disk."""
    status, _headers, _body = _request(running, _P("pulse"))  # no cookie
    assert status == 403


@pytest.mark.parametrize(
    ("path", "content_type"),
    [
        (_P("js/main.js"), "text/javascript; charset=utf-8"),
        (_P("js/api.js"), "text/javascript; charset=utf-8"),
        (_P("js/render.js"), "text/javascript; charset=utf-8"),
        (_P("js/live.js"), "text/javascript; charset=utf-8"),
        (_P("js/format.js"), "text/javascript; charset=utf-8"),
        (_P("js/theme.js"), "text/javascript; charset=utf-8"),
        (_P("css/keel.css"), "text/css; charset=utf-8"),
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
    status, headers, _body = _request(running, _P("pulse"), cookie=_session(running))
    assert status == 200
    csp = headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "connect-src 'self'" in csp
    assert "base-uri 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "'unsafe-inline'" not in csp
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert "Strict-Transport-Security" not in headers


def _view_keys(view: str, root: str) -> list[str]:
    """Every `<root>.<...>` path a view function reads, from the source.

    Generalised from the status view's own key scan at #537, when six more views needed the same
    check -- and the generalisation is a REQUIRED second argument, never a default. The views do
    not all name their payload `data`: `rulesView` reads six endpoints, through `data`, `plan`,
    `trials`, `gauntlet`, `slippage` and `matrix` (console PR3; `insightsView` took `insights` and
    `journal` before it). A helper defaulting to `"data"` would have scanned nothing for those
    roots, found no keys, and passed. Every existing caller passes `("pulseView", "data")` and gets
    exactly what it got before.

    Row-level keys (`row.qty`, `row.product_id`) are out of scope: they live inside collections
    that are empty on a fresh deployment, so there is nothing to check them against here. They are
    covered by `tests/web/test_payload.py`, which builds populated reports.
    """
    # Scanned over the CODE, not the raw file. `_code_only` strips comments, and the views
    # document the keys they read -- `activityView`'s docstring names `data.scope` while
    # explaining where the current scope comes from. Since a region runs up to the next `export
    # function` LINE, the next view's docstring falls inside the previous view's region, and
    # scanning the raw text reported `setupView` reading a key of `/api/activity`. Found by this
    # test failing on its first run, which is the same way the status view's scan learned it had to
    # be bounded at all. It is the same lesson `_markup_only` records for `index.html`: a file
    # that explains itself well is a file a naive grep cannot check.
    source = _code_only(_source("render.js"))
    start = source.index(f"export function {view}(")
    # BOUNDED at the next top-level export, not run to end-of-file. Unbounded, this swept up
    # `data.build` -- a key of `/api/config`, not of `/api/status` -- and reported a payload gap
    # that does not exist. Found by this test failing on its first run.
    #
    # The bound is the next export OR the end of the file, whichever comes first: #597's config
    # parity check reads `buildLine`, which is the last export in the file, and a helper that
    # raised on "no next export" would have made the last function in the file the one function
    # no payload check could ever cover. Mid-file scans are bounded exactly as they were.
    next_export = source.find("\nexport function ", start + 1)
    end = len(source) if next_export == -1 else next_export
    # The negative lookahead drops METHOD calls: `data.data_freshness.map(` must contribute
    # `data_freshness`, not `data_freshness.map`. Backtracking does that -- the two-segment
    # alternative fails the lookahead on `(` and the one-segment one succeeds on `.`.
    # `[a-zA-Z_]`, not `[a-z_]`. Payload keys are snake_case by convention, so the lowercase
    # class looked sufficient -- and it silently made the scan blind to exactly the rename it
    # exists to catch. `config.profileName` matched as `config.profile`, a key the endpoint DOES
    # send, so renaming a client read to camelCase passed this check while the element rendered
    # empty forever. Found by mutating #704's `sessionChip`; the class now covers any identifier
    # JavaScript allows, and a key the payload does not send fails whatever its casing.
    found = re.findall(rf"\b{root}\.([a-zA-Z_]+(?:\.[a-zA-Z_0-9]+)?)\b(?!\s*\()", source[start:end])
    # Neither `.length` nor `.display` is a payload key: the first is a list length in JavaScript,
    # the second is half of a `Field` whose presence is already checked one level up. Two names
    # rather than a general rule, because a THIRD non-payload member appearing here should be
    # noticed, not absorbed.
    return sorted({path for path in found if not path.endswith((".length", ".display"))})


#: Each ported view, the parameter it reads a payload through, and the endpoint that fills it.
#:
#: `rulesView` appears once per endpoint it reads (console PR3): the ledger and track records, then
#: the four research reads, each through a parameter named for it so `_view_keys` keeps their keys
#: apart in the one region. All of them name `/rules` as their `html_route` -- which is the server
#: saying they are one page. The DCA card is its own exported function, so it is its own entry.
_VIEW_ENDPOINTS: tuple[tuple[str, str, str], ...] = (
    ("setupView", "data", "/api/setup"),
    # Pulse (console PR1): the home view, and the one that replaced Status, Activity and Gates.
    ("pulseView", "data", "/api/pulse"),
    # #659/#701/#702's views were added to the client without being added here, so nothing
    # checked that they read keys their endpoint actually sends. Demonstrated on #702: renaming
    # `data.hwm` to `data.high_water_mark` in `balancesView` left the whole web suite green, and
    # the tile would have rendered blank forever with nothing in the console naming the gap.
    ("holdingsView", "data", "/api/holdings"),
    # Holdings reads a SECOND endpoint for its recent-orders section, through a parameter named
    # `orders` -- the root that keeps the two payloads' keys apart in `_view_keys`' one region.
    ("holdingsView", "orders", "/api/orders"),
    ("balancesView", "data", "/api/balances"),
    ("timelineView", "data", "/api/timeline"),
    ("rulesView", "data", "/api/rules"),
    ("rulesView", "trials", "/api/research/trials"),
    ("rulesView", "gauntlet", "/api/research/gauntlet"),
    ("rulesView", "slippage", "/api/research/slippage"),
    ("rulesView", "matrix", "/api/research/matrix"),
    # The DCA proposal card on /rules. Read bare, the endpoint answers its awaiting shape, which
    # carries every key the ready shape does (`payload.dca_plan_awaiting_payload`).
    ("dcaPlanCard", "plan", "/api/dca-plan"),
    ("venuesView", "data", "/api/venues"),
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
    checks are the keys most likely to be got wrong -- payload fields ADDED with the view that
    reads them (`status_note` in #537, `records` in console PR3), which is exactly the shape of key
    a client can start reading before the server sends it."""
    for view, root, _endpoint in _VIEW_ENDPOINTS:
        assert _view_keys(view, root), f"{view} reads nothing off {root} -- the parser has broken"

    assert "halt.resume_command" in _view_keys("pulseView", "data")
    assert "activity.status_note" in _view_keys("pulseView", "data")
    assert "records" in _view_keys("rulesView", "data")
    assert "closed_trade_count" in _view_keys("rulesView", "data")
    assert "chain_errors" in _view_keys("rulesView", "trials")
    assert "steps" in _view_keys("setupView", "data")


def test_the_pulse_key_scan_found_the_keys_it_claims_to_check() -> None:
    """The premise. A regex that matched nothing would make the parity test pass on any payload.

    (The status view's version of this test moved here with its keys: Pulse reads the same
    sections through the same helpers.)"""
    keys = _view_keys("pulseView", "data")
    assert "equity.high_water_mark" in keys
    assert "autonomy.live" in keys
    assert "withdrawal_attestation.state" in keys
    assert len(keys) >= 15, keys

    # The two filters, asserted rather than assumed -- both were added because this test failed
    # on a real key that was not a key. A method name or `.length` reaching the payload check
    # reports a gap in `/api/pulse` that does not exist, which is a false failure pointing at
    # the wrong file entirely.
    assert "data_freshness" in keys and "data_freshness.map" not in keys
    assert not any(key.endswith(".length") for key in keys), keys

    # And the scan is bounded: `data.build` belongs to `/api/config` and is read by `buildLine`,
    # which lives BELOW `pulseView` in the same file.
    assert "build" not in keys, keys


# -- every table declares as many cells as it declares headers ---------------------------------


def _balanced_block(text: str, start: int) -> tuple[str, int]:
    """The bracketed block beginning at `start` (which must be a `[`), and the index after it."""
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1], index + 1
    raise AssertionError("unbalanced block")


def _top_level_items(block: str) -> int:
    """How many elements a `[...]` literal declares, counting commas at depth 1 only."""
    inner = block[1:-1]
    depth = 0
    items = 0
    seen_content = False
    for char in inner:
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            items += 1
            seen_content = False
            continue
        if not char.isspace():
            seen_content = True
    return items + (1 if seen_content else 0)


def _table_calls(code: str) -> list[tuple[int, int]]:
    """`(headers, cells)` for every `table(` call in `render.js` that inlines both."""
    pairs: list[tuple[int, int]] = []
    cursor = 0
    while True:
        found = code.find("table(", cursor)
        if found == -1:
            return pairs
        cursor = found + 6
        # The columns array is the first `[` after the id argument.
        columns_at = code.find("[", cursor)
        if columns_at == -1:
            continue
        columns_block, after = _balanced_block(code, columns_at)
        if "label:" not in columns_block:
            continue  # not a table() call -- some other identifier ending in `table(`
        headers = columns_block.count("label:")
        # The cells come from a `.map(` whose arrow body is an array literal.
        map_at = code.find(".map(", after)
        if map_at == -1 or map_at > after + 400:
            continue  # rows passed as a variable; nothing inline to compare
        cells_at = code.find("[", map_at)
        if cells_at == -1:
            continue
        cells_block, _ = _balanced_block(code, cells_at)
        pairs.append((headers, _top_level_items(cells_block)))


def test_every_table_emits_one_cell_per_declared_header() -> None:
    """`table()` pairs `columns[index]` with `row.entries()` BY POSITION (render.js:400), so a
    column added to the header list without a matching value silently shifts every later cell
    one place left -- and the last column renders no `<td>` at all.

    That is not a cosmetic failure. On the Orders table it put the placement timestamp under
    "fee" and a quantity under "rule", so every money column named the wrong figure while each
    value stayed individually true. Nothing else in this suite compares the two lists: the
    per-view tests assert that a key is *declared*, which a header alone satisfies."""
    pairs = _table_calls(_code_only(_source("render.js")))

    assert len(pairs) >= 6, f"the table scan found only {len(pairs)} tables; it has stopped working"
    mismatched = [(headers, cells) for headers, cells in pairs if headers != cells]
    assert mismatched == [], f"(headers, cells) mismatches: {mismatched}"


#: Globals the browser provides, which no module declares and every module may call.
#:
#: Deliberately short. A long list is a list that absorbs a typo -- the whole value of the scan
#: below is that an undefined name is loud, so anything added here should be a real browser API
#: someone can point at.
_BROWSER_GLOBALS = frozenset(
    {
        "Array",
        "Boolean",
        "Date",
        "FormData",
        "JSON",
        "Map",
        "Math",
        "Number",
        "Object",
        "Set",
        "String",
        "URL",
        "console",
        "document",
        "window",
    }
)

#: Keywords that are followed by a parenthesis and are not calls.
_NOT_CALLS = frozenset(
    {
        "await",
        "catch",
        "delete",
        "for",
        "function",
        "if",
        "instanceof",
        "new",
        "return",
        "super",
        "switch",
        "typeof",
        "void",
        "while",
    }
)


def _scannable_functions(code: str) -> list[tuple[str, str]]:
    """Every function body the call scan walks, as `(parameter list, body)`.

    Both shapes this codebase writes: `function name(params) { ... }` and the module-level
    `const name = (params) => { ... }`. The parameter list is taken by BALANCED PARENTHESES
    rather than `([^)]*)`, because a default value (`function f(a = el())`) closes the character
    class early -- and the failure mode of that was not a false positive but a silent DROP: the
    function stopped being scanned at all, quietly, which is the worst way for a guard to fail.
    """
    out: list[tuple[str, str]] = []
    for match in re.finditer(r"\bfunction\s+[A-Za-z_$][\w$]*\s*\(", code):
        params, after = _balanced_block(code, match.end() - 1)
        brace = code.find("{", after)
        if brace == -1:
            continue
        out.append((params, _balanced_block(code, brace)[0]))
    for match in re.finditer(r"\b(?:const|let|var)\s+[A-Za-z_$][\w$]*\s*=\s*\(", code):
        params, after = _balanced_block(code, match.end() - 1)
        arrow = code[after : after + 4]
        if "=>" not in arrow:
            continue
        brace = code.find("{", after)
        if brace == -1:
            continue
        out.append((params, _balanced_block(code, brace)[0]))
    return out


def _undefined_calls(name: str) -> list[str]:
    """Bare-identifier calls in `name` that are not in scope where they are made.

    Scoped PER FUNCTION, which is the part that took two attempts. A first version collected
    every function's parameters into one module-wide set, so `sorting` -- a parameter of `table`
    and of `headerCell` -- counted as defined inside a view function, and that is exactly the bug
    this scan exists to catch. Parameters are in scope in their own function only.

    KNOWN BLIND SPOTS, stated rather than implied. Object-literal and class methods are not
    walked (this codebase writes neither in these two modules). Arrow PARAMETERS are admitted
    function-wide rather than per-arrow, so a callback argument sharing a name with a missing
    function would mask it -- real JavaScript scoping needs a parser, and a regex that pretended
    to do it would be worse than one whose limits are written down. What the scan does cover is
    the mistake that actually shipped: calling a name that exists in the file as a typedef, a
    parameter of some other function, or nothing at all.
    """
    code = _code_only(_source(name))

    module_level = set(re.findall(r"\bfunction\s+([A-Za-z_$][\w$]*)", code))
    module_level |= set(re.findall(r"^(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=", code, re.M))
    for block in re.findall(r"import\s*\{([^}]*)\}", code):
        for imported in block.split(","):
            imported = imported.strip().split(" as ")[-1].strip()
            if imported:
                module_level.add(imported)

    undefined: set[str] = set()
    for params, body in _scannable_functions(code):
        in_scope = set(module_level)
        for param in params[1:-1].split(","):
            param = param.split("=")[0].strip()
            if param:
                in_scope.add(param)
        in_scope |= set(re.findall(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)", body))
        in_scope |= set(re.findall(r"\(([A-Za-z_$][\w$]*)\)\s*=>", body))
        in_scope |= set(re.findall(r"\b([A-Za-z_$][\w$]*)\s*=>", body))
        called = {m.group(1) for m in re.finditer(r"(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(", body)}
        undefined |= called - in_scope - _BROWSER_GLOBALS - _NOT_CALLS
    return sorted(undefined)


@pytest.mark.parametrize("name", _DERIVATION_FREE)
def test_every_call_resolves_to_something_the_module_has(name: str) -> None:
    """No JavaScript runs in this suite, so a call to a function that does not exist ships.

    It is not hypothetical. #701's positions table was written calling `sorting(sort, onSort)` --
    `sorting` is a TYPEDEF and a parameter name in this file, never a function -- and every gate
    stayed green: mypy does not read JavaScript, ruff does not either, and the view tests assert
    over source text rather than executing it. The page would have thrown `ReferenceError` on
    first render.

    Scoped to the derivation-free modules rather than the whole client: they are the ones
    that touch almost no browser API, so the short `_BROWSER_GLOBALS` list stays honest. Widening
    it to `main.js` would mean listing `fetch`, `setTimeout`, `URL` and friends, and a list long
    enough to cover those is long enough to hide a typo.
    """
    scanned = _scannable_functions(_code_only(_source(name)))
    assert len(scanned) >= 5, (
        f"the call scan found only {len(scanned)} functions in {name}; it has stopped working"
    )
    assert _undefined_calls(name) == []


@pytest.mark.parametrize(
    "snippet",
    [
        "function planted() { return nope(); }",
        # The default value that used to close `([^)]*)` early and silently drop the function.
        "function planted(a = el()) { return nope(); }",
        # A module-level arrow, which the first version never walked at all.
        "const planted = (a) => { return nope(); };",
    ],
)
def test_the_call_scanner_sees_every_shape_this_codebase_writes(snippet: str) -> None:
    """The premise, per shape. A scan that silently skipped one of these would report a clean
    file while the missing call sat inside it -- which is how the guard fails without saying so.
    """
    code = _code_only(snippet)
    found: set[str] = set()
    for params, body in _scannable_functions(code):
        called = {m.group(1) for m in re.finditer(r"(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(", body)}
        found |= called
    assert "nope" in found, f"the scanner did not walk: {snippet}"


def test_the_call_scanner_can_fail() -> None:
    """The premise. A scanner whose regex missed every call would pass any file."""
    code = "function a() { return b(1); }"
    called = {m.group(1) for m in re.finditer(r"(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(", code)}

    assert "b" in called, "the call scanner does not find calls"
    assert "a" in called or "a" in set(re.findall(r"\bfunction\s+([A-Za-z_$][\w$]*)", code))


# -- row-level keys, which the scan above declares out of scope ------------------------------------
#
# `_view_keys` reads only `<root>.<key>` paths and says so, with a reason: row keys "live inside
# collections that are empty on a fresh deployment, so there is nothing to check them against
# here. They are covered by `tests/web/test_payload.py`, which builds populated reports."
#
# **The first half of that is true and the second half is not.** `test_payload.py` verifies that
# the payload EMITS a key. Nothing verified that `render.js` READS the same one. So the two sides
# could be renamed apart independently, and were: #725 renamed `priced_from` to `fallback` in the
# payload and in the view, and reverting only the view's half left the entire 630-test web suite
# green while that cell rendered `?` forever. That is the `data.hwm` failure of #702 exactly, one
# level down, in the gap the note above declared and then mis-named the cover for.
#
# The fix for "collections are empty on a fresh deployment" is to stop having a fresh deployment:
# `_ROW_ENDPOINTS` seeds each collection and REQUIRES a row, the same discipline
# `test_api.py::test_every_declared_sort_column_is_a_column_the_rows_actually_have` uses ("a
# collection with no rows proves nothing here, which is why the seeding is not optional").


def _row_reads(view: str) -> dict[tuple[str, str], set[str]]:
    """Every `<param>.<key>` a view reads inside a `.map()` over a root collection.

    Returns `{(root, collection): {keys}}`. Handles the three receiver shapes `render.js` uses:
    `(root.coll || []).map(`, `root.coll.map(`, and a local `const name = root.coll || [];`
    followed by `name.map(` -- plus one further hop for `holdingsSection`, which narrows a bound
    collection with `.filter()` before mapping it.
    """
    source = _code_only(_source("render.js"))
    start = source.index(f"export function {view}(")
    rest = source[start + 1 :]
    end = rest.find("\nexport function ")
    body = rest if end == -1 else rest[:end]

    # Local aliases: `const assets = data.assets || [];` and `const held = rows.filter(...)`.
    aliases: dict[str, tuple[str, str]] = {}
    for name, root, collection in re.findall(
        r"const\s+(\w+)\s*=\s*(\w+)\.(\w+)\s*(?:\|\|\s*\[\])?\s*;", body
    ):
        aliases[name] = (root, collection)
    for name, source_name in re.findall(r"const\s+(\w+)\s*=\s*(\w+)\.filter\(", body):
        if source_name in aliases:
            aliases[name] = aliases[source_name]

    found: dict[tuple[str, str], set[str]] = {}
    for match in re.finditer(r"(?:\((\w+)\.(\w+)\s*\|\|\s*\[\]\)|(\w+)\.(\w+)|(\w+))\.map\(", body):
        direct_root, direct_coll, plain_root, plain_coll, alias = match.groups()
        if direct_root:
            target = (direct_root, direct_coll)
        elif plain_root:
            target = (plain_root, plain_coll)
        elif alias in aliases:
            target = aliases[alias]
        else:
            continue

        call, _after = _balanced_block(body, body.index("(", match.end() - 1))
        param = re.search(r"\(\s*(\w+)\s*\)\s*=>", call)
        if not param:
            continue
        keys = set(re.findall(rf"\b{re.escape(param.group(1))}\.(\w+)", call))
        found.setdefault(target, set()).update(keys)
    return found


#: `/api/dca-plan` with the inputs the card takes from the page address. Bare, it answers the
#: awaiting state, whose collections are empty by design.
_DCA_PLAN_ASKED = "/api/dca-plan?budget=500&buffer=0.1"

#: Which endpoint each mapped collection comes from, and how to give it a row.
#:
#: Hand-written like `_VIEW_ENDPOINTS` beside it, and for the same reason: a table derived from
#: the client would agree with the client by construction, which is the disagreement this test
#: exists to find.
_ROW_ENDPOINTS: tuple[tuple[str, str, str, str, str], ...] = (
    # Holdings' tranche table, seeded with a tranche AND the entry order it joins to, so the
    # placement and fill cells a row carries are the matched shape and not the "unknown" one.
    ("holdingsView", "data", "rows", "/api/holdings", "holdings"),
    ("holdingsView", "orders", "rows", "/api/orders", "orders"),
    ("balancesView", "data", "assets", "/api/balances", "positions"),
    ("timelineView", "data", "rows", "/api/timeline", "orders"),
    ("rulesView", "data", "rules", "/api/rules", "rules"),
    # The track records (console PR3). The `rules` seeder writes a `candidate` rule, which is in
    # the promotion pipeline `build_insights_report` scopes to, so the collection has a row.
    ("rulesView", "data", "records", "/api/rules", "rules"),
    # Pulse's two row tables a seeded deployment can fill: the open tranche, and a LIVE dca rule's
    # next cadence day. Registered WITH the code that reads them.
    ("pulseView", "data", "open_positions", "/api/pulse", "positions"),
    ("pulseView", "data", "next_dca", "/api/pulse", "dca_live"),
    ("rulesView", "slippage", "rows", "/api/research/slippage", "none"),
    ("rulesView", "gauntlet", "rows", "/api/research/gauntlet", "gauntlet"),
    # #708 view 2. Registered WITH the code that reads it -- the rule `_ROW_ENDPOINTS` states.
    ("rulesView", "matrix", "rows", "/api/research/matrix", "matrix"),
    # The DCA card's three collections. The endpoint is asked WITH a budget, and the `dca_plan`
    # seeder admits BTC, rejects PAXG and gives ETH an existing DCA rule -- so each collection has
    # a row, and each row the card maps is one the served payload actually built.
    ("dcaPlanCard", "plan", "buys", _DCA_PLAN_ASKED, "dca_plan"),
    ("dcaPlanCard", "plan", "excluded", _DCA_PLAN_ASKED, "dca_plan"),
    ("dcaPlanCard", "plan", "existing", _DCA_PLAN_ASKED, "dca_plan"),
)

#: Mapped collections this test does NOT cover, each with the reason. Named rather than omitted:
#: an uncovered collection that nobody wrote down is indistinguishable from one nobody noticed,
#: and `test_every_mapped_collection_is_either_checked_or_named` fails on anything in neither set.
_ROW_ENDPOINTS_UNCOVERED: dict[tuple[str, str, str], str] = {
    ("pulseView", "data", "live_rules"): "needs a promoted rule; no seeder in this module yet",
    ("pulseView", "data", "data_freshness"): "needs cached candles per configured product",
    ("pulseView", "data", "subscriptions"): "needs a venue subscription record",
    ("pulseView", "activity", "cycles"): (
        "needs a cycle log file beside the deployment; the service is pinned in "
        "tests/commands/test_pulse.py and the reader in test_api.py"
    ),
    ("setupView", "data", "not_automated"): "the checklist is config-derived, not row-seeded",
    ("setupView", "data", "actions"): "the checklist is config-derived, not row-seeded",
    ("rulesView", "trials", "exploration"): "needs a trials ledger; #708 view 1 seeds one",
    ("rulesView", "trials", "rows"): "needs a trials ledger; #708 view 1's own tests seed one",
    ("venuesView", "data", "venues"): "config-derived; no rows to seed",
    ("venuesView", "data", "readiness"): "config-derived; no rows to seed",
    # Nested one level down: the root is a ROW variable, not a payload root, so the check above
    # cannot address them by `(root, collection)` at all. Naming them is what stops the scan from
    # looking complete while quietly skipping every nested collection in the client.
    ("pulseView", "cycle", "events"): "nested under a cycle row; root is a loop variable",
}


def _seed_ledger(tmp_path: Path) -> None:
    """A trials ledger holding one recorded gauntlet run, where the API will look for it.

    `tests/conftest.py::_isolate_trials_ledger` is autouse and already points
    `research.ledger.DEFAULT_LEDGER_PATH` at an absolute path under `tmp_path`, so writing there
    is exactly what `api._ledger_path` will resolve -- no monkeypatching, and the resolution
    under test is the real one.
    """
    from decimal import Decimal

    from keel.research import ledger as trials_ledger

    trials_ledger.append_trial(
        trials_ledger.DEFAULT_LEDGER_PATH,
        trial_id="row-key-scan",
        session="row-key-scan-session",
        rule="turtle_breakout",
        params={"entry_lookback": 20},
        provenance="a_priori",
        kind="sweep_node",
        decision="rejected",
        summary={
            "pbo": "0.7",
            "pbo_available": 1,
            "gate_passed": 0,
            "train_expectancy": "-1.33",
            "held_out_expectancy": "-1.82",
            "seed": 476,
            "bars": 17520,
        },
        per_trade_pnl=[Decimal("1.5"), Decimal("-2.0")],
        timestamp=1_700_000_000,
    )


def _seed_for(kind: str, db_path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.web.test_api import _seed_holding, _seed_orders, _seed_positions, _seed_rules

    if kind == "holdings":
        # The tranche and its BUY share one clock, the way both rows are written in production.
        _seed_holding(db_path, opened_at=1_700_000_000, order_created_at=1_700_000_000)
    elif kind == "dca_plan":
        # The admission screen, patched at its module (the reader resolves it per call), plus
        # one existing `dca` rule written through the repository -- the card's three
        # collections then each hold a row.
        from keel.commands import assets
        from keel.data.db import connect, migrate
        from keel.data.repository import Repository
        from tests.commands.test_dca_plan import _insert_dca, _screen

        monkeypatch.setattr(assets, "screen_product", _screen("PAXG"))
        conn = connect(db_path)
        migrate(conn)
        _insert_dca(Repository(conn), "ETH-USD", "candidate")
        conn.close()
    elif kind == "dca_live":
        # A LIVE dca rule, written through the repository: Pulse lists only live ones, so a
        # candidate would leave `next_dca` empty and the row-key check would fail by vacuum.
        from keel.data.db import connect, migrate
        from keel.data.repository import Repository
        from tests.commands.test_dca_plan import _insert_dca

        conn = connect(db_path)
        migrate(conn)
        _insert_dca(Repository(conn), "ETH-USD", "live")
        conn.close()
    elif kind == "gauntlet":
        _seed_ledger(Path(db_path).parent)
    elif kind == "positions":
        _seed_positions(db_path, (("BTC-USD", "0.01", "50000"),))
    elif kind == "orders":
        _seed_orders(db_path, (("BTC-USD", "buy", "50000"),))
    elif kind == "rules":
        _seed_rules(db_path, ("breakout",))
    elif kind == "matrix":
        # A recorded CSCV run, appended through the real ledger writer -- the page has no way to
        # produce one and must not: it READS what `trials pbo` recorded.
        from decimal import Decimal

        from keel.research import ledger as _ledger

        _ledger.append_trial(
            _ledger.DEFAULT_LEDGER_PATH,
            trial_id="cscv-s1-s16",
            session="s1",
            rule="(cscv over the recorded columns)",
            provenance="a_priori",
            kind="cscv",
            decision="diagnostic_only",
            series_missing=True,
            summary={
                "pbo": Decimal("0.88"),
                "degradation_slope": Decimal("-0.4"),
                "degradation_intercept": Decimal("0.05"),
                "prob_loss": Decimal("0.7"),
                "dominance_1st": False,
                "dominance_2nd": True,
                "n_columns": 12,
                "n_blocks": 16,
                "n_combinations": 12870,
                "rows_used": 1819,
                "rows_dropped": 9,
                "columns_refused": 0,
            },
        )


@pytest.mark.parametrize(("view", "root", "collection", "endpoint", "seed"), _ROW_ENDPOINTS)
def test_every_row_key_a_view_reads_is_a_key_its_endpoint_sends(
    view: str,
    root: str,
    collection: str,
    endpoint: str,
    seed: str,
    running,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The check `_view_keys` declares out of scope, done one level down.

    Reverting `row.fallback` to `row.priced_from` in `slippageSection` -- the rename #725 made on
    both sides -- passes the whole web suite without this test and fails here.
    """
    _seed_for(seed, running.db_path, monkeypatch)

    status, _headers, body = _request(running, endpoint, cookie=_session(running))
    assert status == 200, endpoint
    document = json.loads(body)
    assert document["engine"]["value"] == "running", document
    # A DOTTED PATH, because a collection is not always a top-level key (#705's discretionary
    # journal rode `/api/journal` at `data.notes.entries` until console PR3). Every collection
    # registered today is top-level; the walk keeps a nested one checkable without a route of its
    # own for the sake of this test.
    rows = document["data"]
    for part in collection.split("."):
        assert isinstance(rows, dict), (
            f"{endpoint}: {part} is not an object on the way to {collection}"
        )
        assert part in rows, f"{endpoint} does not send {collection}"
        rows = rows[part]

    # A collection with no rows proves nothing, so an empty one is the failure rather than a pass.
    assert rows, f"{endpoint} sent no {collection} to check {view}'s row reads against"
    # The SCAN is keyed by the last segment -- a view mapping `a.b.c` is seen by `_row_reads` as
    # the receiver `b` and the collection `c`.
    reads = _row_reads(view).get((root, collection.rsplit(".", 1)[-1]), set())
    assert reads, f"the scan found no row keys in {view} -- it would pass against any payload"

    for row in rows:
        missing = reads - set(row)
        assert not missing, f"{view} reads {sorted(missing)}; {endpoint} does not send them"


def test_the_holdings_seed_reaches_the_reader_with_its_entry_order(
    running,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The row-key check above is vacuous if the seeded BUY never joined its tranche: the row would
    still exist, with every fill cell "unknown". So the join is asserted on the served payload."""
    _seed_for("holdings", running.db_path, monkeypatch)

    status, _headers, body = _request(running, "/api/holdings", cookie=_session(running))
    assert status == 200
    (row,) = json.loads(body)["data"]["rows"]
    assert row["entry_order_id"] != "", "the seeded entry order did not reach the holdings reader"
    assert row["placement"]["state"] != "unknown"
    assert row["actual_fill"]["display"] != "unknown"


def test_the_dca_card_places_every_key_an_existing_rule_row_sends(
    running,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reverse of the row-key scan, for the existing-rules list: a key the served row sends
    and the card never reads fails here. Review of #850: `dip_bonus` -- the CLI's
    `(dip_bonus_pct X)` marker -- was on neither side."""
    _seed_for("dca_plan", running.db_path, monkeypatch)
    status, _headers, body = _request(running, _DCA_PLAN_ASKED, cookie=_session(running))
    assert status == 200
    rows = json.loads(body)["data"]["existing"]
    assert len(rows) == 1, rows  # the seeder's ETH rule reached the served payload
    sent = set(rows[0])
    assert "dip_bonus" in sent and len(sent) >= 6, sorted(sent)
    reads = _row_reads("dcaPlanCard").get(("plan", "existing"), set())
    assert reads, "the scan found no existing-row reads -- it would pass against any payload"
    assert sent == reads, f"sent but never placed: {sorted(sent - reads)}"


def test_every_mapped_collection_is_either_checked_or_named() -> None:
    """No mapped collection may be silently uncovered.

    Without this, the table above is a list of what somebody happened to get round to, and a view
    added later inherits the exact hole #725 fell into. With it, a new `.map()` over a payload
    collection fails the build until it is either checked or written down with a reason.
    """
    # The last segment of the path, because that is what `_row_reads` sees: a view mapping `a.b.c`
    # is reported as the receiver `b` and the collection `c`, while the table above carries the
    # payload path for the walk.
    checked = {
        (view, root, collection.rsplit(".", 1)[-1])
        for view, root, collection, _e, _s in _ROW_ENDPOINTS
    }
    views = {view for view, _root, _endpoint in _VIEW_ENDPOINTS}

    mapped = {(view, root, collection) for view in views for (root, collection) in _row_reads(view)}

    unaccounted = mapped - checked - set(_ROW_ENDPOINTS_UNCOVERED)
    assert not unaccounted, f"mapped collections neither checked nor named: {sorted(unaccounted)}"

    # And the other direction: an exemption for a collection nothing maps any more is a note that
    # has outlived its subject, and would quietly excuse a future collection of the same name.
    stale = set(_ROW_ENDPOINTS_UNCOVERED) - mapped
    assert not stale, f"exemptions for collections nothing maps: {sorted(stale)}"


# -- the session chip and the mode banner (#704) -------------------------------------------------
#
# The refusal this issue is built around is a NEGATIVE: no way to go live sits on either surface.
# A negative is exactly what rots silently, so it is pinned structurally -- against the parsed
# function bodies and the parsed markup, not against a substring that a rename would slip past.


def _comments_stripped(source: str) -> str:
    """`source` with comments removed and STRING LITERALS KEPT.

    Deliberately not `_code_only`, and the difference is the whole test below. `_code_only` also
    collapses every string, which is right for the scans looking for arithmetic and wrong here:
    the thing being forbidden is `el("button", ...)`, and `button` is a string literal. Scanning
    the stripped form for it finds nothing, always -- a test that passes against the very mutation
    it exists to catch. (Confirmed: adding a "Go live" button to `paperBanner` survived the first
    version of this test.)

    Comments still go, because a docstring is not what a function does -- `paperBanner`'s explains
    at length what it refuses to build, using the exact words this scan looks for.
    """
    out: list[str] = []
    i, n = 0, len(source)
    while i < n:
        if source.startswith("//", i):
            while i < n and source[i] != "\n":
                i += 1
            continue
        if source.startswith("/*", i):
            end = source.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        # Strings are stepped THROUGH rather than skipped, so a `//` inside one is not mistaken
        # for a comment and does not eat the rest of the line.
        if source[i] in "'\"":
            quote = source[i]
            out.append(source[i])
            i += 1
            while i < n:
                if source[i] == "\\":
                    out.append(source[i : i + 2])
                    i += 2
                    continue
                out.append(source[i])
                if source[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        out.append(source[i])
        i += 1
    return "".join(out)


def _function_body(source: str, name: str) -> str:
    """One top-level function's body, comments stripped and string literals kept.

    Exported OR module-private: `trialsSection` is private, and a helper that only knew how to
    find exports raised `ValueError` on one (#705's `notesSection`, retired in console PR3) --
    which at least fails loudly, unlike the shape where a scan silently finds nothing. Bounded at
    the next top-level function of either kind, so the next function's body cannot be read as
    this one's.
    """
    code = _comments_stripped(source)
    for prefix in ("export function ", "function "):
        marker = prefix + name + "("
        if marker in code:
            start = code.index(marker)
            break
    else:
        raise AssertionError(f"render.js declares no top-level function {name}")
    rest = code[start + 1 :]
    ends = [at for at in (rest.find("\nexport function "), rest.find("\nfunction ")) if at != -1]
    return rest if not ends else rest[: min(ends)]


def test_the_clickable_scan_can_actually_see_a_string_literal() -> None:
    """The scan's own smoke test, because the first version of it could not.

    A source-text check that strips the very tokens it searches for passes against every input,
    including the mutation it was written to catch. This asserts the lexer keeps what the test
    below depends on before that test is allowed to mean anything.
    """
    kept = _comments_stripped('const go = el("button", undefined, "Go live"); // el("form")')
    assert '"button"' in kept
    assert "form" not in kept, "comments must still be stripped"


#: What a display-only renderer may not contain. ONE list, shared by every such test, because two
#: lists drift: #704's blocked `button` and `href` UNQUOTED and #706's blocked `"button"` quoted
#: and dropped `href` -- so `el('button', 'cta', 'Buy Pro')` in single quotes, and a `setAttribute`
#: of `href` on a span, both passed the newer test while failing the older one.
#:
#: Unquoted, so a quoting style cannot evade it. `element` and `anchor` are absent deliberately:
#: `el(...)` is this file's own constructor and every renderer calls it.
_INTERACTIVE_TOKENS: tuple[str, ...] = (
    "button",
    "addEventListener",
    "onclick",
    '"a"',
    "'a'",
    "href",
    "form",
    "input",
)


def test_neither_the_chip_nor_the_banner_builds_anything_clickable() -> None:
    """Display and navigation only, by construction rather than by intent.

    A retail console makes the chip a dropdown and hangs an "Open Live Account" button off the
    paper banner. Switching profile or mode in keel is a config-file edit plus a typed terminal
    ceremony, and going live is a runbook -- this console may explain both and may not offer
    either. So neither function may create an interactive node or bind a handler.
    """
    source = _source("render.js")
    # #755's `deploymentCard` joins them rather than getting an exemption: it is the surface where
    # a reader looks for the control, which makes it the likeliest place for one to be added.
    for name in ("sessionChip", "paperBanner", "deploymentCard"):
        body = _function_body(source, name)
        for forbidden in _INTERACTIVE_TOKENS:
            assert forbidden not in body, f"{name} builds something interactive: {forbidden}"


def test_the_banner_sentence_is_placed_and_never_written_here() -> None:
    """Rule 2. Choosing between two sentences on the basis of what a config says is a judgement,
    and judgements are made in Python -- so the words must arrive on the payload. A renderer that
    composed the sentence could drift from the one the tests assert verbatim."""
    body = _function_body(_source("render.js"), "paperBanner")
    assert "config.banner" in body
    assert "PAPER" not in body, "the banner's words belong to payload._session_banner"


def test_the_chip_and_the_banner_live_outside_the_view_so_they_survive_navigation() -> None:
    """ "Present on all views" is a property of WHERE they are, not of every view remembering to
    draw them. `#view` is replaced wholesale on each navigation; these sit outside it."""
    html = _INDEX.read_text(encoding="utf-8")
    view_at = html.index('id="view"')
    for element in ('id="session-chip"', 'id="mode-banner"'):
        assert element in html, f"{element} is missing from the shell"
        assert html.index(element) < view_at, f"{element} is inside #view and would be repainted"


def test_the_banner_element_ships_empty_and_carries_no_children() -> None:
    """Empty in the markup, like `#build` and `#mode-badge`: an unreadable config costs the
    banner rather than showing a guessed one. And nothing is smuggled in as static markup -- the
    node's whole content comes from the payload or it has none."""
    html = _markup_only(_INDEX.read_text(encoding="utf-8"))
    assert '<p id="mode-banner" class="modebanner"></p>' in html


def test_every_id_main_js_demands_exists_in_the_shell() -> None:
    """`must()` throws on a missing id, which is the right behaviour and a terrible way to find
    out: the two files ship together, so a missing id is a bug in the commit that made it."""
    import re

    html = _INDEX.read_text(encoding="utf-8")
    # The RAW source, not `_code_only`: the ids being looked for ARE string literals, which is
    # exactly what that lexer strips. Over-matching an id mentioned in a comment would fail this
    # test rather than pass it silently, so the raw scan errs in the safe direction.
    demanded = set(re.findall(r'must\("([a-z0-9-]+)"\)', _source("main.js")))
    assert demanded, "the scan found no must() calls -- it would pass against any shell"
    for element_id in sorted(demanded):
        assert f'id="{element_id}"' in html, f"main.js demands #{element_id}; index.html has none"


def test_the_shell_actually_calls_the_chip_and_the_banner() -> None:
    """A renderer nothing invokes is a renderer that ships doing nothing.

    Every other pin on these two functions asks what they WOULD draw. None asked whether anything
    draws them -- so deleting both call sites in `main.js` left the whole suite green while the
    header lost its chip and the banner never appeared. The parity scan above cannot catch that
    either: an uncalled function still reads the keys it reads.
    """
    source = _comments_stripped(_source("main.js"))
    for name in ("sessionChip", "paperBanner"):
        assert name + "(" in source, f"main.js never calls {name}"


def test_the_chip_and_the_banner_hydrate_from_the_boot_read() -> None:
    """From the SAME `/api/config` read the badge and the docs links use, which is what puts them
    on every view rather than only where a status report happens to load. A second read would be a
    second round trip on the boot path and a second answer that could describe another instant."""
    source = _comments_stripped(_source("main.js"))
    boot = source[source.index('read("config")') :]
    for name in ("modeBadge", "sessionChip", "paperBanner"):
        assert name + "(" in boot, f"{name} is not hydrated from the boot config read"


def test_skipping_the_nav_does_not_skip_the_mode_banner() -> None:
    """Skip-to-content exists to skip the NAV. The banner is not nav, and it is the one persistent
    statement about whether real money is involved -- so the link's target opens above it rather
    than past it. It targeted `#view`, which sits below the banner."""
    html = _INDEX.read_text(encoding="utf-8")
    target = re.search(r'<a class="skip" href="#([a-z0-9-]+)"', html)
    assert target is not None, "the shell has no skip link"
    anchor = f'id="{target.group(1)}"'
    assert anchor in html, f"the skip link points at {target.group(1)}, which the shell lacks"
    assert html.index(anchor) < html.index('id="mode-banner"'), (
        "skip-to-content lands below the mode banner"
    )


def test_the_skip_target_can_actually_take_focus() -> None:
    """A `div` is not focusable without it, so the link would move the viewport and leave the
    keyboard where it was. `#view` carries one for the same reason."""
    html = _INDEX.read_text(encoding="utf-8")
    target = re.search(r'<a class="skip" href="#([a-z0-9-]+)"', html)
    assert target is not None
    element = re.search(rf"<[a-z]+ id=\"{target.group(1)}\"[^>]*>", html)
    assert element is not None
    assert 'tabindex="-1"' in element.group(0)


def test_the_chip_separators_the_comments_describe_actually_ship() -> None:
    """Three comments and an issue describe `profile · mode · equity state`. The first cut shipped
    no separator anywhere and the chip read "keel paper not recorded" -- and the CSS `:empty` rule
    reasoned at length about "a lone separator beside a name that never arrived", a separator that
    did not exist.

    Generated on the parts, so a hidden part takes its own separator with it.
    """
    css = (_STATIC / "css" / "keel.css").read_text(encoding="utf-8")
    for selector in ("#session-profile:not(:empty)::after", "#session-equity:not(:empty)::before"):
        assert selector in css, f"no separator rule for {selector}"


# -- the Plans page's refusals (#706) -------------------------------------------------------------
#
# The acceptance criterion is "zero interactive purchase affordances", which is a NEGATIVE -- the
# thing that rots silently. So it is asserted structurally against the parsed function bodies,
# with string literals KEPT (the earlier lesson: a scan that strips them cannot see `el("a")`).


#: Text that would turn a published price into something a reader could act on.
#:
#: A SECOND BELT, and it is worth saying which belt is first: every string in the tier table has to
#: appear verbatim in `docs/evolution-plan.md` (`tests/commands/test_plans.py`), so an injected
#: call to action fails traceability before it reaches this list. That is the defence that does not
#: depend on anyone having thought of the wording.
#:
#: This list exists for the strings traceability does not cover, and it carries the schemes the
#: project ACTUALLY chose: ADR 0004 and Phase F both name self-hosted BTCPay, so `bitcoin:` and
#: `lightning:` URIs are the single most likely real affordance here -- and the first cut of this
#: list, built from generic e-commerce words, could not see either of them.
_PURCHASE_AFFORDANCES: tuple[str, ...] = (
    "http://",
    "https://",
    "mailto:",
    "bitcoin:",
    "lightning:",
    "btcpay",
    "lnbc",
    "checkout",
    "subscribe",
    "upgrade",
    "purchase",
    "pay via",
    "pay with",
    "get started",
    "buy ",
    "order at",
    "contact sales",
)


def test_the_plans_view_builds_nothing_a_reader_can_click() -> None:
    """No button, no anchor, no form, no handler.

    A retail Plans page is a funnel by construction: every row exists to move a reader one row
    down. The inversion is not a matter of gentler wording -- there must be nothing on the page
    that could take an action, so that the page cannot become a funnel by a later edit that only
    changes copy.
    """
    source = _source("render.js")
    for name in ("plansView", "claimList", "citation"):
        body = _function_body(source, name)
        for forbidden in _INTERACTIVE_TOKENS:
            assert forbidden not in body, f"{name} builds something interactive: {forbidden}"


def test_the_plans_payload_sends_no_destination_to_build_one_from() -> None:
    """The refusal is structural rather than editorial: `render.js` cannot make a link out of a
    payload that never sends a URL. Asserted over the SERIALISED payload, so a key added anywhere
    beneath it is caught too."""
    from keel.commands.plans import gather_plans
    from keel.web import payload as payload_mod

    body = json.dumps(payload_mod.plans_payload(gather_plans())).lower()
    for affordance in _PURCHASE_AFFORDANCES:
        assert affordance not in body, f"the plans payload carries {affordance}"


def test_the_plans_view_cites_a_path_and_never_links_to_one() -> None:
    """`keel serve` is a loopback SQLite reader with no document server behind it, so a link to a
    document would 404 -- and a path a reader can open in their own checkout is the honest form.
    It is also what keeps this page free of anchors entirely."""
    body = _function_body(_source("render.js"), "citation")
    assert '"code"' in body
    assert "href" not in body


def test_the_plans_view_shows_every_section_the_payload_sends() -> None:
    """A page that quoted the constitution and dropped the refusal list would be a page choosing
    which of the project's commitments a reader sees -- on the page whose subject is exactly
    that."""
    from keel.commands.plans import gather_plans
    from keel.web import payload as payload_mod

    # DERIVED from the payload, not a list of literals: a section added to the wire and never
    # rendered would have passed a hardcoded list forever.
    sections = set(payload_mod.plans_payload(gather_plans()))
    assert sections, "the scan found no sections -- it would pass against any view"

    body = _function_body(_source("render.js"), "plansView")
    for key in sorted(sections):
        assert "data." + key in body, f"/api/plans sends {key}; plansView never renders it"


def test_the_plans_table_offers_no_sort_control() -> None:
    """A tier table ordered by price is a shopping comparison. These four rows are not four
    choices, and `table()` draws a sort control only when handed a `sort`/`onSort` pair."""
    body = _function_body(_source("render.js"), "plansView")
    assert "onSort" not in body
    assert "sort:" not in body


def _duplicate_block_declarations(code: str) -> list[str]:
    """Every `const`/`let`/`class` name declared twice in ONE block scope of `code`.

    That is a `SyntaxError` for the whole module, and a module that does not parse runs nothing:
    #810 declared `const note` twice in `refusedView`, and every console built from it loaded a
    blank page. The repo has no JavaScript toolchain (see this module's docstring), so the parse
    this check stands in for never happens in CI.

    Braces delimit scopes, which is enough because `_code_only` has already removed the strings
    and comments that could hold a stray one. A declaration inside parentheses -- `for (const x
    of ...)` -- belongs to the loop, not the enclosing block, so it is skipped; destructuring
    names nothing matchable after the keyword and is skipped the same way.
    """
    scopes: list[set[str]] = [set()]
    parens = 0
    found: list[str] = []
    for match in re.finditer(r"[{}()]|\b(?:const|let|class)\s+([A-Za-z_$][\w$]*)", code):
        token = match.group(0)
        if token == "{":
            scopes.append(set())
        elif token == "}":
            if len(scopes) != 1:
                scopes.pop()
        elif token == "(":
            parens += 1
        elif token == ")":
            parens = max(parens - 1, 0)
        elif parens == 0:
            name = match.group(1)
            if name in scopes[-1]:
                found.append(name)
            scopes[-1].add(name)
    return found


@pytest.mark.parametrize("name", sorted(p.name for p in _JS.glob("*.js")))
def test_no_module_declares_a_name_twice_in_one_scope(name: str) -> None:
    assert not _duplicate_block_declarations(_code_only(_source(name))), (
        f"{name} redeclares {_duplicate_block_declarations(_code_only(_source(name)))} -- a "
        "SyntaxError that stops the whole module, and the page, from running"
    )


def test_the_redeclaration_scan_can_fail() -> None:
    """The #810 shape is caught; the same name in sibling blocks and loop heads is not."""
    assert _duplicate_block_declarations(
        "function f() {\n  const note = a;\n  if (x) {\n    const note = b;\n  }\n"
        "  const note = c;\n}\n"
    ) == ["note"]
    assert not _duplicate_block_declarations(
        "function f() {\n  for (const c of a) {\n  }\n  for (const c of b) {\n  }\n}\n"
        "function g() {\n  const note = a;\n}\n"
    )


# -- the DCA proposal card on /rules (read-only) --------------------------------------------------
#
# The card's refusal is a NEGATIVE -- no control that could apply the plan -- so, like the chip,
# the banner and the plans view above, it is pinned against the parsed function bodies: tags on a
# closed list, one `code` element paired with `plan.command`, and every key the endpoint sends
# paired with a read of it.

#: Every function that builds part of the card. The scans below read their bodies together.
_DCA_CARD_FUNCTIONS = ("dcaPlanCard", "dcaPlanFigure", "dcaPlanRows", "dcaPlanList")

#: The only element tags the card may build. `code` carries the CLI command; nothing on the list
#: can take an action.
_DCA_CARD_TAGS = {"section", "h2", "h3", "p", "code", "ul", "li", "span", "div"}


def _dca_card_bodies() -> str:
    source = _source("render.js")
    return "\n".join(_function_body(source, name) for name in _DCA_CARD_FUNCTIONS)


def test_the_dca_card_builds_only_non_interactive_elements() -> None:
    """Structure, not substrings: every `el("<tag>"` the card makes is on a closed list, the list
    holds nothing a reader can activate, and its one table is drawn without a sort pair."""
    body = _dca_card_bodies()
    tags = re.findall(r'\bel\("([a-z0-9]+)"', body)
    assert len(tags) >= 8, f"the scan found {len(tags)} elements -- it would pass any card"
    assert set(tags) <= _DCA_CARD_TAGS, sorted(set(tags) - _DCA_CARD_TAGS)
    assert len(re.findall(r"\btable\(", body)) == 1, "the schedule is the card's one table"
    assert "onSort" not in body and "sort:" not in body
    for forbidden in _INTERACTIVE_TOKENS:
        assert forbidden not in body, f"the DCA card builds something interactive: {forbidden}"


def test_the_dca_card_scan_would_see_a_button() -> None:
    """The premise of the scan above: a button added to the card's source is found by it."""
    mutated = _source("render.js").replace(
        "export function dcaPlanCard(plan, error) {",
        'export function dcaPlanCard(plan, error) {\n  el("button", undefined, "Apply");',
        1,
    )
    assert mutated != _source("render.js"), "the mutation did not apply"
    tags = set(re.findall(r'\bel\("([a-z0-9]+)"', _function_body(mutated, "dcaPlanCard")))
    assert "button" in tags and not tags <= _DCA_CARD_TAGS


def test_the_dca_card_places_the_command_in_exactly_one_code_element() -> None:
    """The pairing: one `code` element in the whole card, and it is filled from `plan.command`."""
    body = _dca_card_bodies()
    assert len(re.findall(r'\bel\("code"', body)) == 1
    paired = re.findall(r'\bel\("code", "[a-z-]+", plain\(plan\.command\)\)', body)
    assert len(paired) == 1, "the one code element is not the one filled from plan.command"


def test_the_dca_card_renders_every_section_the_payload_sends(running) -> None:  # type: ignore[no-untyped-def]
    """Derived from the served payload, not a list of literals: a key added to the wire and never
    placed fails here -- the summary's figures one level down, including the worst-month check."""
    status, _headers, body = _request(running, "/api/dca-plan", cookie=_session(running))
    assert status == 200
    data = json.loads(body)["data"]
    keys = set(data)
    summary = set(data["summary"])
    assert "cap_check" in keys and len(keys) >= 10, sorted(keys)
    assert len(summary) >= 6, sorted(summary)
    reads = set(_view_keys("dcaPlanCard", "plan"))
    for key in sorted(keys - {"summary"}):
        assert any(read == key or read.startswith(key + ".") for read in reads), (
            f"/api/dca-plan sends {key}; the card never places it"
        )
    for key in sorted(summary):
        assert "summary." + key in reads, f"/api/dca-plan sends summary.{key}; never placed"


def test_the_dca_card_labels_the_worst_month_check_as_what_the_cap_was_checked_against() -> None:
    """#847: the figure beside the cap is the one the blocker compared, and the card says so in
    the CLI's own words. Paired: exactly one figure reads `plan.cap_check`, under that label."""
    figures = re.findall(r'dcaPlanFigure\("([^"]+)", (plan\.[a-z_.]+)\)', _dca_card_bodies())
    assert len(figures) >= 8, figures
    checks = [label for label, read in figures if read == "plan.cap_check"]
    assert checks == ["checked against the cap"], figures


def test_the_dca_card_places_the_screen_age_as_a_note_exactly_once() -> None:
    """#856: the staleness note is built and filled by ONE statement pair -- a `<p class="note">`
    immediately appended to the card, filled from `field(plan.screen_age)` (a judged `Field`, not
    a bare string: Rule 3). Paired, not just present twice somewhere in the card's source."""
    body = _dca_card_bodies()
    assert body.count("field(plan.screen_age)") == 1
    pair = re.search(
        r'const (\w+) = el\("p", "note"\);\s*'
        r"\1\.append\(field\(plan\.screen_age\)\);\s*"
        r"card\.append\(\1\)",
        body,
    )
    assert pair is not None, body


def test_the_dca_card_states_a_failed_read_with_the_servers_reason() -> None:
    """A 400 (a bad `?budget=`, a config refusal) leaves the rules table standing and says why:
    the card is handed the reading's `error` and places its `detail`, the service's own words."""
    body = _function_body(_source("render.js"), "dcaPlanCard")
    assert body.count("plain(error.detail)") == 1
    assert "if (!plan)" in body


def test_the_rules_view_places_the_dca_card_exactly_once() -> None:
    source = _source("render.js")
    rules = _function_body(source, "rulesView")
    assert rules.count("dcaPlanCard(") == 1
    assert "dcaPlanCard(plan || null, planError || null)" in rules
    # And nowhere else: one card, on one page.
    assert _code_only(source).count("dcaPlanCard(") == 2  # the declaration and the one call


def test_the_rules_view_reads_the_dca_plan_as_its_second_endpoint() -> None:
    """The ROUTES row, which `_js_route_names` parses and
    `test_the_python_and_javascript_route_tables_agree` pairs with the server's table."""
    source = _source("main.js")
    start = source.index("const ROUTES = [")
    table_src = source[start : source.index("];", start)]
    rows = [line.strip() for line in table_src.splitlines() if 'name: "rules"' in line]
    assert len(rows) == 1, rows
    assert rows[0].startswith('{ name: "rules", label: "Rules", endpoints: ["rules", "dca-plan", ')


def test_mount_hands_the_rules_view_the_dca_reading_data_and_error() -> None:
    """The secondary reading's `data` AND `error` both reach the view -- `data` alone would turn
    a 400 into a bare "could not be read" with the reason thrown away. (The research readings
    that follow it are pinned, pairing by pairing, in `test_rules_view.py`.)"""
    code = _code_only(_source("main.js"))
    calls = re.findall(r"rulesView\(([^;]*)\);", code)
    assert len(calls) == 1, calls
    args = re.sub(r"\s+", " ", calls[0]).strip().rstrip(",").strip()
    assert args.startswith(
        "data, primary.sort, onSort, planReading ? planReading.data : null, "
        "planReading ? planReading.error : null, "
    ), args
    assert code.count("const planReading = readings[1];") == 1


def test_the_page_address_seeds_only_the_dca_plan_query() -> None:
    """R16: the card's inputs come from the page's own address, copied once into the
    `dca-plan` endpoint's query bag only -- the names are a closed pair, and nothing else in the
    client reads `location.search`."""
    code = _comments_stripped(_source("main.js"))
    assert code.count("window.location.search") == 1
    names = re.findall(r"for \(const name of \[([^\]]*)\]\)", code)
    assert names == ['"budget", "buffer"'], names
    assert code.count('paramsFor("dca-plan")') == 1
