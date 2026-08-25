"""Documentation is linked, never embedded (#539) -- and the links are checked against `docs/`.

**Why this module exists at all.** A broken deep link is not an error. `…/glossary/#rial` opens
the glossary at the top of the page, looking exactly like a link to a term that happens not to
scroll, and nothing anywhere reports it. keel's `docs/` is the SOURCE and keeltrading.com is the
mirror (`engine-docs.manifest.json` pins `CodeGateSoftware/keel@main`), so this repository is the
one place where a rename and the links that depend on it can be compared at all -- and this is
the comparison.

The anchor contract is the one `docs/glossary.md` states about itself: "Each entry is a `## term`
heading, a definition, and a `Source:` line." Astro's slugger kebab-cases those headings into
ids, and `_slug` below reproduces that transformation. If the site ever changes slugger, this
module is what fails.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from keel.web import staticfiles

_REPO = Path(__file__).resolve().parents[2]
_DOCS = _REPO / "docs"
_DOCS_JS = staticfiles.STATIC_ROOT / "js" / "docs.js"
_RENDER_JS = staticfiles.STATIC_ROOT / "js" / "render.js"
_INDEX = staticfiles.STATIC_ROOT / "index.html"

#: Slug -> the document in THIS repository it is published from. Mirrors
#: `keeltrading.com/engine-docs.manifest.json`, which is the site's own pin of the same pairs.
#: Only the slugs the app links to need an entry; a link to a slug absent here fails below,
#: which is the correct outcome for a link to a document nobody has confirmed is published.
_PUBLISHED: dict[str, str] = {
    "glossary": "glossary.md",
    "fiqh-basis": "fiqh-basis.md",
    "operator-runbook": "operator-runbook.md",
    "go-live-runbook": "go-live-runbook.md",
}


def _slug(heading: str) -> str:
    """A `## heading` as the id the built site gives it.

    Lowercase, drop everything that is not a letter, digit, space or hyphen, then spaces to
    hyphens -- GitHub's slugger, which is what `rehype-slug` implements and what the built site
    was verified to emit (`id="rail"`, `id="instrument-attestation"`, `id="kill-switch"`).
    """
    text = heading.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    return re.sub(r"[\s_]+", "-", text).strip("-")


def _anchors(document: str) -> set[str]:
    """Every anchor a document offers, from its `##`-and-deeper headings."""
    source = (_DOCS / document).read_text(encoding="utf-8")
    return {_slug(match) for match in re.findall(r"^#{2,6}\s+(.+)$", source, re.MULTILINE)}


def _terms() -> dict[str, tuple[str, str]]:
    """`docs.js`'s `TERMS`, as `{label: (slug, anchor)}`.

    Parsed rather than imported, for the same reason `test_client_assets.py` parses `main.js`'s
    route table: there is no JavaScript runtime here. The parse is narrow on purpose, and
    `test_the_term_parser_actually_found_something` is what stops a rewritten table from silently
    matching nothing.
    """
    source = _DOCS_JS.read_text(encoding="utf-8")
    block = re.search(r"export const TERMS = \{(.*?)\n\};", source, re.DOTALL)
    assert block is not None, "TERMS is not in the shape this parser understands"
    found: dict[str, tuple[str, str]] = {}
    pattern = re.compile(
        r'^\s*(?:"(?P<quoted>[^"]+)"|(?P<bare>[A-Za-z_][\w$]*))\s*:\s*'
        r'\{\s*slug:\s*"(?P<slug>[^"]+)"\s*,\s*anchor:\s*"(?P<anchor>[^"]+)"\s*\}\s*,\s*$'
    )
    for line in block.group(1).splitlines():
        if not line.strip() or line.strip().startswith("//"):
            continue
        match = pattern.match(line)
        assert match is not None, f"unparsed TERMS entry: {line}"
        label = match.group("quoted") or match.group("bare")
        found[label] = (match.group("slug"), match.group("anchor"))
    return found


# -- the acceptance criterion ----------------------------------------------------------------------


def test_every_anchor_the_app_emits_exists_in_the_source_document() -> None:
    """**The acceptance criterion, and the reason a rename upstream cannot break a link quietly.**

    Each `(slug, anchor)` the client can emit is resolved to a document in `docs/` and checked
    against the headings that document actually has.
    """
    for label, (slug, anchor) in sorted(_terms().items()):
        assert slug in _PUBLISHED, (
            f"{label!r} links to slug {slug!r}, which is not a published document -- add it to "
            "_PUBLISHED here and to keeltrading.com's engine-docs.manifest.json, or link "
            "somewhere that exists"
        )
        available = _anchors(_PUBLISHED[slug])
        assert anchor in available, (
            f"{label!r} links to #{anchor} in docs/{_PUBLISHED[slug]}, which has no such heading. "
            f"A heading was probably renamed; the link would open the page at the top and report "
            f"nothing. Closest available: {sorted(a for a in available if anchor[:4] in a)}"
        )


def test_the_anchor_check_would_notice_a_renamed_heading() -> None:
    """Mutation: the assertion above compares against real headings, not against anything.

    Without this, a `_anchors` that returned everything -- or a regex that matched nothing and so
    made the set empty in a way `in` happened to tolerate -- would leave the criterion green and
    meaningless.
    """
    real = _anchors("glossary.md")
    assert "kill-switch" in real, "the heading parser found no known term"
    assert "kill-switch-renamed-upstream" not in real


def test_the_term_parser_actually_found_something() -> None:
    """Guards every assertion that iterates `TERMS` against an empty parse."""
    terms = _terms()
    assert len(terms) >= 5, terms
    assert terms["kill switch"] == ("glossary", "kill-switch")


def test_the_slugger_matches_the_ids_the_built_site_emits() -> None:
    """The transformation, pinned against ids observed in the built site rather than assumed.

    `dist/en/docs/glossary/index.html` was checked at #531 and carries `id="rail"`,
    `id="attestation"`, `id="instrument-attestation"`, `id="kill-switch"`, `id="qabd"`,
    `id="riba"`. These are those cases run backwards through `_slug`.
    """
    assert _slug("rail") == "rail"
    assert _slug("instrument attestation") == "instrument-attestation"
    assert _slug("kill switch") == "kill-switch"
    assert _slug("qabd") == "qabd"
    assert _slug("DCA benchmark") == "dca-benchmark"
    assert _slug("session-bound venue") == "session-bound-venue"


# -- the table cannot rot ----------


def test_every_linked_label_is_a_label_the_client_actually_puts_on_screen() -> None:
    """The other direction, and the one that keeps the table honest as views change.

    `kv` links a label by looking it up, so a label renamed in `render.js` does not break -- it
    just silently stops being a link, and the entry here becomes an entry for a label that no
    longer exists. This is what turns that into a failure.
    """
    source = _RENDER_JS.read_text(encoding="utf-8")
    emitted = set(re.findall(r'kv\("([^"]*)"', source))
    assert emitted, "no kv labels found -- this test would prove nothing"
    for label in sorted(_terms()):
        assert label in emitted, (
            f"{label!r} is in docs.TERMS but no view emits it as a kv label; it was probably "
            "renamed in render.js, where the link would have vanished without a word"
        )


def test_the_ambiguous_labels_are_deliberately_absent() -> None:
    """A wrong link costs more than a missing one, and these two are the wrong ones.

    `mode` reads `paper` or `live` -- one label, two definitions, and no way to pick. `evidence
    required` sits on the CAPABILITY gates (`keel.capabilities.GATES`), not the promotion gate, so
    `#promotion-gate` would be confidently and invisibly wrong. Pinned so that "the table looks
    incomplete" never becomes a reason to complete it.
    """
    terms = _terms()
    assert "mode" not in terms
    assert "evidence required" not in terms


# -- nothing is fetched, bundled or cached ----------


def test_the_app_fetches_no_documentation() -> None:
    """`docs.js` builds URLs and returns anchors. It opens no connection.

    `test_client_assets.py::test_fetch_appears_in_exactly_one_client_module` already pins that
    `fetch` lives only in `api.js`; this is the narrower statement the issue asks for -- the
    documentation module in particular never reads a document.
    """
    source = _DOCS_JS.read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith(("*", "/*", "//"))
    )
    for forbidden in ("fetch(", "XMLHttpRequest", "EventSource", "import("):
        assert forbidden not in code, f"docs.js reaches the network via {forbidden}"


def test_no_documentation_is_precached() -> None:
    """#538's worker must not hold a copy either. A cached definition that has since changed,
    presented as current, is the same failure as a cached balance in a milder register."""
    precache = (staticfiles.STATIC_ROOT / "sw.js").read_text(encoding="utf-8")
    assert "keeltrading.com" not in precache
    for name in sorted(p.name for p in _DOCS.glob("*.md")):
        assert name not in precache, f"{name} is precached; documentation is linked, not shipped"


def test_no_documentation_prose_ships_inside_the_client() -> None:
    """The definitions themselves stay in `docs/`. A copy in the client is a second source that
    drifts, and drifts silently, because nothing compares them."""
    glossary = (_DOCS / "glossary.md").read_text(encoding="utf-8")
    definitions = [
        line.strip()
        for line in glossary.splitlines()
        if len(line.strip()) > 60 and not line.startswith(("#", "Source:", "-", ">"))
    ]
    assert definitions, "no definitions found in the glossary -- this test would prove nothing"
    client = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(staticfiles.STATIC_ROOT.rglob("*"))
        if path.is_file() and path.suffix in (".js", ".html", ".css")
    )
    for definition in definitions:
        assert definition not in client, (
            f"a glossary definition is embedded in the client: {definition[:60]!r}"
        )


# -- the version, and the one URL ----------


def test_links_carry_the_running_version() -> None:
    """Version skew is made visible rather than solved: the site pins `main` while an operator
    runs a tagged release, so the build goes in the URL bar of the page they are reading."""
    source = _DOCS_JS.read_text(encoding="utf-8")
    assert 'url = url + "?v=" + encodeURIComponent(version)' in source
    main = (staticfiles.STATIC_ROOT / "js" / "main.js").read_text(encoding="utf-8")
    assert "rememberVersion(" in main, "nothing ever tells docs.js which build is running"


def test_the_first_paint_already_knows_the_version() -> None:
    """`show` is called from inside the `/api/config` callback, so no link is ever built before
    the build is known -- otherwise the first screen an operator sees carries links that do not
    say which build they are reading about, for a whole poll interval."""
    main = (staticfiles.STATIC_ROOT / "js" / "main.js").read_text(encoding="utf-8")
    config_block = main[main.index('void read("config")') :]
    assert "show(booted, false);" in config_block, (
        "the first paint no longer waits for the build; documentation links would be unversioned "
        "until the first poll"
    )
    assert main.count("show(booted, false);") == 1, "the first paint happens twice"


# `test_the_rendered_pages_version_their_documentation_link_too` stood here until #540. It existed
# because two front-ends both linked out while both existed, and the criterion was not "the client
# carries `?v=`". There is one front-end now. What that test actually caught -- `?v=` carrying the
# human-readable BUILD LINE rather than the version -- is still pinned, by
# `test_links_carry_the_running_version` below and by `server._docs_version`'s own deletion: there
# is no longer a second place where the wrong string could be passed.


def test_the_documentation_root_is_spelled_in_exactly_two_places_and_they_agree() -> None:
    """`docs.js`'s `SITE` and the shell's static `href`, and nothing else.

    There were three until #540 -- `render.py` carried a `DOCS_URL` of its own for the rendered
    nav -- and that duplication was the reason this test existed. One of them is gone with the
    module. The two that remain cannot be collapsed: `index.html`'s href is the un-versioned form,
    spelled in the markup so that view-source shows where the link goes without running a script,
    and `main.js` replaces it with the `?v=` form once `/api/config` answers."""
    js = _DOCS_JS.read_text(encoding="utf-8")
    site = re.search(r'const SITE = "([^"]+)"', js)
    assert site is not None
    assert site.group(1) in _INDEX.read_text(encoding="utf-8")


# -- the deletions ----------


def test_the_glossary_page_is_gone() -> None:
    """`/glossary` rendered a file no installed deployment has. A link replaced it at #539, and
    at #540 the entire rendering layer it belonged to went the same way."""
    from keel.web import server

    assert not hasattr(server, "ROUTES"), "the HTML route table is back"
    assert not hasattr(server, "page_glossary")
    assert "render" not in dir(server), "server.py imports a renderer again"


def test_the_web_layer_no_longer_reads_the_glossary_file() -> None:
    """`help_console.load_glossary` stays for the TUI until #541, but nothing under `keel/web/`
    calls it any more -- the whole point being that the file is not there to read.

    Read through `ast` rather than by substring, and the difference is not fastidiousness: the
    NAV comment in `render.py` explains this deletion by NAMING `load_glossary`, and a substring
    scan would fail on the prose that documents the change. An AST sees identifiers, so a
    docstring can say the word and only a call can fail the test.
    """
    import ast

    for path in sorted((_REPO / "keel" / "web").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        used = {
            node.id if isinstance(node, ast.Name) else node.attr
            for node in ast.walk(tree)
            if isinstance(node, (ast.Name, ast.Attribute))
        }
        used |= {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        assert "load_glossary" not in used, path
        assert "parse_glossary" not in used, path


@pytest.mark.parametrize("path", ["/glossary", "/static/glossary"])
def test_the_glossary_path_is_a_404_on_both_front_ends(path: str, running) -> None:  # type: ignore[no-untyped-def]
    """Over the wire, on the rendered pages and on the client's own prefix."""
    from tests.web.test_server import _request, _session

    status, _headers, _body = _request(running, path, cookie=_session(running))
    assert status == 404, path
