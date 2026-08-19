"""The Help menu -- the O8 help & glossary system (issue #394 C7; PRD O8).

The PRD §3 Help branch made real: the glossary, the per-screen/per-action catalog, the
rule-parameter help, and the keys/safety notes -- plus the `?` contextual overlay that
renders the CURRENT screen's contribution wherever it is pressed. Three single-source
rules hold the whole system together:

* **The glossary is ONE hand-written file** -- `docs/glossary.md`. This module renders it
  (bounded read at the research corpus's own byte bound, cached per mtime exactly like
  the research doc view), the docs link to it, and no other surface defines console
  terms. The fiqh terms' definitions are ANCHORED to `docs/fiqh-basis.md` (verbatim
  passages with their section citations; where that document is silent the entry says so,
  the way C3's shariah screen handles gharar) -- pinned by test, and pinned EQUAL to the
  shariah screen's own vocabulary so the two surfaces cannot drift. An installed
  deployment has no docs/ checkout: an absent glossary renders a calm empty state that
  names the path, never a traceback.
* **Rule parameters are never duplicated.** `build_params_help_lines` renders
  `keel.commands.rules.describe_params` -- the rule classes' own docstrings, defaults,
  types and choices, by introspection. The glossary's `granularity` entry points here
  too; no table in this module restates a parameter.
* **Contextual help text lives with the module that owns the screen.** Each console
  module (and the TUI itself, for the pre-console overlays) declares a `CONTEXT_HELP`
  mapping of its mode names to plain `(subject, description)` pairs -- one line per
  entry plus a short plain-English description. THIS module is only the registry (a
  closed mode -> owner mapping, `CONSOLE_MODES`/`contextual_help`) and the renderer;
  pressing `?` in a mode renders that mode's contribution, and the Help menu's "screens
  & actions" entry consolidates every mode's rows into one auditable catalog -- the C7
  consolidation the PRD promised (the per-screen strings landed with C2-C5 where they
  existed; this slice makes them systematic).
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from keel.commands.tui import ScreenLine, _blank

#: The glossary file: THE home for console term definitions (see the module docstring).
#: Relative, like the CLI's `--config`/`--db` defaults -- resolved against the working
#: directory of the running console, which for a checkout deployment is the repo root.
GLOSSARY_PATH = Path("docs/glossary.md")

#: The most bytes of the glossary this module will ever read -- the research corpus's own
#: bound (`research_console.MAX_DOC_BYTES`), for the same reason: a screen that repaints
#: per poll must never have its cost grow with whatever a runaway writer put in the file.
MAX_GLOSSARY_BYTES = 1024 * 1024

_WIDTH = 78


def _wrap(text: str, *, indent: str = "  ", width: int = _WIDTH) -> list[str]:
    """Wrap to the 80-column budget `_paint` clips at -- the rule every console screen
    keeps. PURE."""
    return textwrap.wrap(text, width=width, initial_indent=indent, subsequent_indent=indent) or [
        indent
    ]


# -- the glossary -------------------------------------------------------------------------------


@dataclass(frozen=True)
class GlossaryTerm:
    """One glossary entry, as parsed from `docs/glossary.md`. `fiqh=True` means the
    entry anchors to `docs/fiqh-basis.md`: `citation` is one of that document's EXACT
    section headings and `definition` is a verbatim passage of it -- except where
    `stated=False`, where fiqh-basis does not state the term and the definition SAYS so
    (never a help-authored fiqh summary). Non-fiqh entries carry keel's own vocabulary
    with a `source` naming where the concept lives in the code/docs."""

    term: str
    definition: str
    source: str
    citation: str | None = None
    fiqh: bool = False
    stated: bool = True


_SOURCE_LINE = "Source:"


def parse_glossary(text: str) -> list[GlossaryTerm]:
    """Parse the glossary file's shape: `## term` headings, each followed by the
    definition's lines and a final `Source:` line. PURE -- the parser is the file
    format's only description; the fiqh anchoring derives from the source line itself
    (a `docs/fiqh-basis.md` source anchors; a quoted section heading is the citation;
    "not stated" marks the gharar case)."""
    terms: list[GlossaryTerm] = []
    current: dict[str, Any] | None = None
    for raw_line in text.splitlines():
        if raw_line.startswith("## ") and not raw_line.startswith("### "):
            if current is not None:
                terms.append(_finish_term(current))
            current = {"term": raw_line[3:].strip(), "definition_lines": [], "source": ""}
            continue
        if current is None:
            continue  # the file's preamble (title, the honesty rules) -- not a term
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped.startswith(_SOURCE_LINE):
            current["source"] = stripped[len(_SOURCE_LINE) :].strip()
            continue
        current["definition_lines"].append(stripped)
    if current is not None:
        terms.append(_finish_term(current))
    return terms


def _finish_term(current: dict[str, Any]) -> GlossaryTerm:
    """Assemble one parsed entry: the definition is its lines joined with spaces (the
    file hard-wraps, the term's definition is one passage), the fiqh anchoring derived
    from the source line (see `parse_glossary`)."""
    source: str = current["source"]
    definition = " ".join(current["definition_lines"])
    # The ANCHOR form is `Source: docs/fiqh-basis.md -- "..."` -- the document LEADS the
    # line. A keel-vocabulary entry may POINT AT fiqh-basis (the rail entry names the
    # rails table) without leading with it: mentioning the doc is not quoting it, and
    # only a quote is an anchor.
    fiqh = source.startswith("docs/fiqh-basis.md")
    quoted = re.search(r'"([^"]+)"', source)
    citation = quoted.group(1) if quoted is not None else None
    stated = "not stated" not in source.lower()
    return GlossaryTerm(
        term=current["term"],
        definition=definition,
        source=source,
        citation=citation,
        fiqh=fiqh,
        stated=stated,
    )


def load_glossary(path: Path | None = None) -> list[GlossaryTerm]:
    """The glossary's terms, read BOUNDED through the research corpus's own reader
    (`read_document_lines`: at most `MAX_GLOSSARY_BYTES`, UTF-8 errors replaced, a calm
    one-line notice for an unreadable file -- an installed deployment has no docs/
    checkout, and the help screen renders that notice as its empty state)."""
    from keel.commands.research_console import read_document_lines

    target = GLOSSARY_PATH if path is None else path
    return parse_glossary("\n".join(read_document_lines(target)))


def cached_glossary(
    path: Path | None, cache: dict[tuple[str, int], list[GlossaryTerm]]
) -> list[GlossaryTerm]:
    """`load_glossary` cached per (path, mtime_ns) -- the research doc view's contract,
    applied to the glossary: repaints do not re-read an unchanged file, a changed mtime
    refreshes, and the cache is the caller's single-entry dict."""
    from keel.commands.research_console import read_document_lines

    target = GLOSSARY_PATH if path is None else path
    try:
        mtime_ns = target.stat().st_mtime_ns
    except OSError:
        mtime_ns = -1  # unstatable: a key that can never re-hit, so it never caches
    key = (str(target), mtime_ns)
    if key in cache:
        return cache[key]
    terms = parse_glossary("\n".join(read_document_lines(target)))
    cache.clear()  # single entry: only the open glossary is worth holding
    cache[key] = terms
    return terms


def build_glossary_lines(
    terms: list[GlossaryTerm], *, path: Path | None = None
) -> list[ScreenLine]:
    """The glossary view: every term with its definition wrapped to the 80-column budget
    and its source named -- fiqh entries cite their `docs/fiqh-basis.md` section, and an
    entry the document does not state says so in its own row. PURE; an empty `terms`
    (absent file) renders the calm empty state that names the path."""
    shown_path = GLOSSARY_PATH if path is None else path
    lines: list[ScreenLine] = [
        ScreenLine("keel console -- help / glossary", "heading"),
    ]
    for wrapped in _wrap(f"the single source: {shown_path}", indent=""):
        lines.append(ScreenLine(wrapped, "muted"))
    lines.append(_blank())
    if not terms:
        for wrapped in _wrap(
            f"no glossary at {shown_path} -- a deployment installed from a wheel has no "
            "docs/ checkout; the repo's docs/glossary.md is where the terms are defined.",
            indent="",
        ):
            lines.append(ScreenLine(wrapped, "normal"))
        lines.append(_blank())
        lines.append(
            ScreenLine(
                "up/k down/j scroll · PgUp/PgDn/Home/End · q/Esc/m back to the Help menu",
                "muted",
            )
        )
        return lines
    for term in terms:
        lines.append(ScreenLine(term.term, "heading"))
        for wrapped in _wrap(term.definition):
            lines.append(ScreenLine(wrapped, "normal"))
        anchor = f"-- {term.citation}" if term.citation is not None else ""
        for wrapped in _wrap(f"Source: {term.source} {anchor}".rstrip()):
            lines.append(ScreenLine(wrapped, "muted"))
    lines.append(_blank())
    lines.append(
        ScreenLine(
            "up/k down/j scroll · PgUp/PgDn/Home/End · q/Esc/m back to the Help menu",
            "muted",
        )
    )
    return lines


# -- rule-parameter help: describe_params is the source -----------------------------------------


def build_params_kinds_lines(*, cursor: int = 0) -> list[ScreenLine]:
    """The parameter-help entry point: every rule kind in `RULE_REGISTRY`, one
    cursor-marked row -- selecting one renders `describe_params` for it. PURE."""
    from keel import agent

    kinds = sorted(agent.RULE_REGISTRY)
    lines: list[ScreenLine] = [
        ScreenLine("keel console -- help / rule parameters", "heading"),
    ]
    for wrapped in _wrap(
        "every kind's parameters render from the rule classes themselves "
        "(rules.describe_params, by introspection) -- never a duplicated table",
        indent="",
    ):
        lines.append(ScreenLine(wrapped, "muted"))
    lines.append(_blank())
    cursor = max(0, min(cursor, max(0, len(kinds) - 1)))
    for index, kind in enumerate(kinds):
        marker = ">" if index == cursor else " "
        lines.append(
            ScreenLine(f"{marker} {kind}", "heading" if index == cursor else "normal")
        )
    lines.append(_blank())
    lines.append(
        ScreenLine(
            "up/k down/j move · Enter opens · q/Esc/m back to the Help menu", "muted"
        )
    )
    return lines


def build_params_help_lines(kind: str) -> list[ScreenLine]:
    """One rule kind's parameter help, DELEGATED whole to `rules.describe_params` -- the
    O8 single source: the doc strings, defaults, types, choices and quoting rules are
    the rule classes' own, rendered here; this module owns no parameter text. An unknown
    kind is `describe_params`' own refusal, rendered calmly. PURE aside from that one
    call."""
    from keel.commands.rules import describe_params
    from keel.commands.strategy_console import _default_display

    lines: list[ScreenLine] = [
        ScreenLine(f"keel console -- help / rule parameters: {kind}", "heading"),
    ]
    for wrapped in _wrap(
        "rendered from rules.describe_params -- the class's own docstrings, defaults "
        "and types, introspected. The help never restates them.",
        indent="",
    ):
        lines.append(ScreenLine(wrapped, "muted"))
    lines.append(_blank())
    try:
        params = describe_params(kind)
    except ValueError as exc:
        for wrapped in _wrap(str(exc), indent=""):
            lines.append(ScreenLine(wrapped, "warn"))
        lines.append(_blank())
        lines.append(
            ScreenLine("q/Esc/m back to the kinds list", "muted")
        )
        return lines
    for name, help_ in params.items():
        head = f"  {name} ({help_.type_name}"
        if help_.choices is not None:
            head += f", choices {'/'.join(help_.choices)}"
        head += ")"
        lines.append(ScreenLine(head, "normal"))
        for wrapped in _wrap(
            f"{help_.doc} [default: {_default_display(help_)}]", indent="      "
        ):
            lines.append(ScreenLine(wrapped, "muted"))
    lines.append(_blank())
    lines.append(
        ScreenLine(
            "up/k down/j scroll · PgUp/PgDn/Home/End · q/Esc/m back to the kinds list",
            "muted",
        )
    )
    return lines


# -- the contextual help registry (O8) ---------------------------------------------------------


@dataclass(frozen=True)
class HelpEntry:
    """One contextual-help row: the thing on the screen (`subject` -- an entry, a key, a
    state) and its one-line plain-English description. The TEXT lives in the owning
    module's `CONTEXT_HELP` (plain `(subject, description)` pairs, so no console module
    needs to import this one); this dataclass is the registry's rendered shape."""

    subject: str
    description: str


#: Every console mode, in owner order -- the closed registry. A mode here without a
#: contribution, or a `run_live` mode not here, is a test failure (the registry and the
#: live loop are pinned to each other), which is what makes `?` total.
CONSOLE_MODES: tuple[str, ...] = (
    # the shell (keel.commands.console)
    "menu",
    "profile",
    "venues",
    "placeholder",
    # the compliance menu (keel.commands.compliance_console)
    "compliance",
    "compliance-view",
    "scout-list",
    "scout-view",
    # the strategy console (keel.commands.strategy_console)
    "strategy",
    "strategy-ledger",
    "strategy-rule",
    "strategy-simulate",
    # the research readers (keel.commands.research_console)
    "research",
    "research-list",
    "research-doc",
    "research-trials",
    # the trading menu (keel.commands.trading_console)
    "trading",
    "trading-cycle",
    "trading-monitor",
    # the data menu (keel.commands.data_console)
    "data",
    "data-fetch",
    "data-freshness",
    # the dashboard and its overlays, the help surfaces (keel.commands.tui)
    "normal",
    "help",
    "help-menu",
    "help-glossary",
    "help-params",
    "help-params-kind",
    "help-screens",
    "context-help",
    "insights",
    "screen",
    "propose",
    "discover",
    "activity",
)

#: mode -> the `keel.commands` module NAME that owns the screen (and therefore its
#: `CONTEXT_HELP` rows). Lazy import at read time -- the console modules import this
#: module's import targets' shared base (`tui`) at load, so the registry resolves
#: ownership only when asked (the established cycle-dodge, see `tui.run_live`).
_MODE_OWNERS: dict[str, str] = {
    "menu": "console",
    "profile": "console",
    "venues": "console",
    "placeholder": "console",
    "compliance": "compliance_console",
    "compliance-view": "compliance_console",
    "scout-list": "compliance_console",
    "scout-view": "compliance_console",
    "strategy": "strategy_console",
    "strategy-ledger": "strategy_console",
    "strategy-rule": "strategy_console",
    "strategy-simulate": "strategy_console",
    "research": "research_console",
    "research-list": "research_console",
    "research-doc": "research_console",
    "research-trials": "research_console",
    "trading": "trading_console",
    "trading-cycle": "trading_console",
    "trading-monitor": "trading_console",
    "data": "data_console",
    "data-fetch": "data_console",
    "data-freshness": "data_console",
}
# Every other mode (the dashboard's own overlays and the help surfaces) is owned by the
# TUI module itself.


def _owning_module(mode: str) -> Any:
    """The module that owns `mode`'s screen -- `keel.commands.tui` for the dashboard's
    own overlays and the help surfaces, the named console module for the rest."""
    import keel.commands.tui as tui_module

    name = _MODE_OWNERS.get(mode)
    if name is None:
        return tui_module
    import importlib

    return importlib.import_module(f"keel.commands.{name}")


def contextual_help(mode: str) -> list[HelpEntry]:
    """`mode`'s contextual help -- the "what am I looking at" / "what will this do" rows
    the `?` overlay renders, from the module that OWNS the screen (its `CONTEXT_HELP`).
    An unregistered mode answers `[]`, which the registry pins never happens for a real
    console mode."""
    pairs = _owning_module(mode).CONTEXT_HELP.get(mode, ())
    return [HelpEntry(subject, description) for subject, description in pairs]


def build_context_help_lines(mode: str) -> list[ScreenLine]:
    """The `?` overlay for `mode`: its title, the mode's contribution (one subject per
    row, its description wrapped), and the close note. PURE over the registry."""
    lines: list[ScreenLine] = [
        ScreenLine(f"keel console -- help: {mode}", "heading"),
        ScreenLine("what am I looking at (press q, Esc or ? to return)", "muted"),
        _blank(),
    ]
    for entry in contextual_help(mode):
        lines.append(ScreenLine(entry.subject, "normal"))
        for wrapped in _wrap(entry.description, indent="      "):
            lines.append(ScreenLine(wrapped, "muted"))
    lines.append(_blank())
    lines.append(
        ScreenLine(
            "the Help menu (m -> Help) holds the glossary and every screen's rows", "muted"
        )
    )
    return lines


def build_screens_catalog_lines() -> list[ScreenLine]:
    """The CONSOLIDATED catalog: every console mode's contribution in registry order --
    the C7 audit the PRD's phasing asked for (per-screen strings landed with C2-C5
    where they existed; this view makes the whole set browsable in one place). PURE
    over the registry."""
    lines: list[ScreenLine] = [
        ScreenLine("keel console -- help / screens & actions", "heading"),
    ]
    for wrapped in _wrap(
        "every console screen's own help rows, one block per screen -- the same text "
        "the ? overlay renders in place",
        indent="",
    ):
        lines.append(ScreenLine(wrapped, "muted"))
    lines.append(_blank())
    for mode in CONSOLE_MODES:
        lines.append(ScreenLine(mode, "heading"))
        for entry in contextual_help(mode):
            lines.append(ScreenLine(f"  {entry.subject}", "normal"))
            for wrapped in _wrap(entry.description, indent="      "):
                lines.append(ScreenLine(wrapped, "muted"))
    lines.append(_blank())
    lines.append(
        ScreenLine(
            "up/k down/j scroll · PgUp/PgDn/Home/End · q/Esc/m back to the Help menu",
            "muted",
        )
    )
    return lines


# -- the Help menu (PRD §3's Help branch) -------------------------------------------------------


@dataclass(frozen=True)
class HelpMenuEntry:
    """One entry of the Help sub-menu. `kind` is the closed dispatch vocabulary:
    `"view"` opens a scrolled help surface (`target` names it)."""

    ordinal: int
    label: str
    description: str
    kind: str  # "view"
    target: str  # "glossary" | "screens" | "params" | "keys"


#: PRD §3's Help branch: the glossary, the per-screen/per-action catalog, the parameter
#: help, and the keys/safety notes (the pre-C7 help screen, kept whole).
HELP_MENU: tuple[HelpMenuEntry, ...] = (
    HelpMenuEntry(
        ordinal=1,
        label="glossary",
        description=(
            "every console term defined once, the fiqh terms anchored to docs/fiqh-basis.md"
        ),
        kind="view",
        target="glossary",
    ),
    HelpMenuEntry(
        ordinal=2,
        label="screens & actions",
        description="every console screen's what-am-I-looking-at rows, consolidated",
        kind="view",
        target="screens",
    ),
    HelpMenuEntry(
        ordinal=3,
        label="rule parameters",
        description="every rule kind's params, rendered from the classes via describe_params",
        kind="view",
        target="params",
    ),
    HelpMenuEntry(
        ordinal=4,
        label="keys & safety",
        description="the keybindings, the network touches, and the typed-action safety notes",
        kind="view",
        target="keys",
    ),
)


def help_entry(ordinal: int) -> HelpMenuEntry | None:
    """The entry selected by its displayed ordinal (1-4), or `None` -- the one-lookup
    rule every console menu keeps."""
    for entry in HELP_MENU:
        if entry.ordinal == ordinal:
            return entry
    return None


def build_help_menu_lines(*, cursor: int = 0) -> list[ScreenLine]:
    """The Help sub-menu screen: every entry with its description wrapped to the
    80-column budget, exactly one cursor-marked row. PURE."""
    lines: list[ScreenLine] = [
        ScreenLine("keel console -- help", "heading"),
        ScreenLine("? opens the current screen's help wherever you are", "muted"),
        _blank(),
    ]
    cursor = max(0, min(cursor, len(HELP_MENU) - 1))
    for index, entry in enumerate(HELP_MENU):
        marker = ">" if index == cursor else " "
        lines.append(
            ScreenLine(
                f"{marker} {entry.ordinal}  {entry.label}",
                "heading" if index == cursor else "normal",
            )
        )
        for wrapped in _wrap(f"{entry.description}.", indent="      "):
            lines.append(ScreenLine(wrapped, "muted"))
    lines.append(_blank())
    lines.append(
        ScreenLine(
            "up/k down/j move · Enter/Space select · 1-4 jump · q/Esc/m to the menu",
            "muted",
        )
    )
    return lines
