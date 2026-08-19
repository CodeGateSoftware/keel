"""The Research menu -- the console's evidence readers (issue #390 C4; PRD O5 and §3's tree).

Read-only, browsable overlays over the repo's evidence corpus: the `docs/experiments/` and
`docs/research/` documents, the promotion reports (`docs/superpowers/reports/` -- the same
directory `run_simulation` writes into, so a just-run simulation's report is in the list,
newest-first), and the trials ledger (`trials list`'s own rendering plus `verify`'s chain
verdict, both through the `keel.research.ledger` service, read-only).

Everything is DISPATCH and bounded reading, never behavior:

* the corpus DIRECTORIES are the engine's own paths, single-sourced -- the experiments
  directory is the trials ledger's own parent (`DEFAULT_LEDGER_PATH`), the reports
  directory is where `commands.simulate.default_report_path` writes -- never a TUI-side
  path table that could drift from the code that writes;
* each document is read through `read_document_lines`, BOUNDED at `MAX_DOC_BYTES` (the
  activity feed's own bound, for the same reason: a screen that re-reads per repaint must
  never have its cost grow with whatever a runaway writer put in the file) with a loud
  truncation note rather than a silent partial view;
* the doc view is cached per (path, mtime) -- the compliance console's scout-browser
  lesson: repaints do not re-read an unchanged file, and a changed mtime refreshes;
* the trials view renders `read_trials`/`trial_counts`/`verify_chain` -- the SAME service
  calls `keel trials list`/`verify` make, read-only, fail-calm about an absent ledger.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass
from pathlib import Path

from keel.commands.simulate import default_report_path
from keel.commands.tui import ScreenLine, _blank, _message_style
from keel.research import ledger as trials_ledger

#: The research-docs corpus (`docs/research/`).
RESEARCH_DOCS_DIR = Path("docs/research")


def corpus_path(target: str) -> Path:
    """The corpus directory for a `target` ("experiments"/"research"/"reports"),
    single-sourced from the code that writes into it and resolved at CALL time: the
    experiments corpus is the trials LEDGER's own directory (`DEFAULT_LEDGER_PATH.parent`
    -- read when asked, so a test-isolated or relocated ledger relocates the reader with
    it), the reports corpus is where `run_simulation` writes (`default_report_path`), and
    the research docs are the repo's own `docs/research/`. Never a TUI-side path table
    that could drift from the writer."""
    if target == "experiments":
        return trials_ledger.DEFAULT_LEDGER_PATH.parent
    if target == "reports":
        return default_report_path(0).parent
    return RESEARCH_DOCS_DIR

#: The most bytes of one document this module will ever read (1 MiB -- the activity feed's
#: own bound and the scout browser's, for the same reason: a reader that repaints per poll
#: must never have its cost grow with whatever a runaway writer put in the file). A
#: document past the bound reads its first `MAX_DOC_BYTES` with a loud truncation note,
#: never an unbounded read and never a silent partial view.
MAX_DOC_BYTES = 1024 * 1024

_WIDTH = 78


def _wrap(text: str, *, indent: str = "  ", width: int = _WIDTH) -> list[str]:
    """Wrap to the 80-column budget (the same rule every console screen keeps -- `_paint`
    clips at the window width, and a clipped path tail would be exactly the part that
    identifies the file). PURE."""
    return textwrap.wrap(text, width=width, initial_indent=indent, subsequent_indent=indent) or [
        indent
    ]


# -- the sub-menu model (PRD §3's Research branch) -------------------------------------------------


@dataclass(frozen=True)
class ResearchEntry:
    """One entry of the Research sub-menu. `kind` is the closed dispatch vocabulary:
    `"corpus"` lists a directory of documents, `"trials"` renders the trials-ledger view.
    `target` names the corpus for `corpus_path` (call-time resolution -- see it) or the
    trials view."""

    ordinal: int
    label: str
    description: str
    kind: str  # "corpus" | "trials"
    target: str  # "experiments" | "research" | "reports" | "trials"


#: PRD §3's Research branch in tree order: experiments, research docs, promotion reports,
#: the trials ledger (list/verify). The directories resolve through `corpus_path` -- the
#: engine's own paths, discovered, never re-declared.
RESEARCH_MENU: tuple[ResearchEntry, ...] = (
    ResearchEntry(
        ordinal=1,
        label="experiments",
        description="the docs/experiments corpus -- what was tried, and measured",
        kind="corpus",
        target="experiments",
    ),
    ResearchEntry(
        ordinal=2,
        label="research docs",
        description="the docs/research corpus -- source reviews and feasibility notes",
        kind="corpus",
        target="research",
    ),
    ResearchEntry(
        ordinal=3,
        label="promotion reports",
        description=(
            "the engine-validation reports simulate writes (a just-run report is here, "
            "newest first)"
        ),
        kind="corpus",
        target="reports",
    ),
    ResearchEntry(
        ordinal=4,
        label="trials ledger",
        description="the hash-chained experiments ledger, with its chain verdict",
        kind="trials",
        target="trials",
    ),
)


def research_entry(ordinal: int) -> ResearchEntry | None:
    """The entry selected by its displayed ordinal, or `None` -- the one-lookup rule every
    console menu keeps, so the rendered ordinals and the shortcut keys cannot drift."""
    for entry in RESEARCH_MENU:
        if entry.ordinal == ordinal:
            return entry
    return None


#: This module's screens' contextual help (O8, issue #394 C7) -- the rows the `?`
#: overlay renders, keyed by the live loop's mode names. Plain `(subject, description)`
#: pairs so the text stays HERE with the module that owns the screens;
#: `keel.commands.help_console` is the registry and renderer.
CONTEXT_HELP: dict[str, tuple[tuple[str, str], ...]] = {
    "research": (
        (
            "the corpora",
            "read-only browsers over the repo's evidence: the experiments corpus (what "
            "was tried, and measured), the research docs, and the promotion reports a "
            "simulate run writes (newest first)",
        ),
        (
            "the trials ledger",
            "the hash-chained record of every backtest trial, with the chain's own "
            "verify verdict rendered under the rows",
        ),
    ),
    "research-list": (
        (
            "the files",
            "one row per document, newest first, with its written date and size; the "
            "directories are the engine's own paths, discovered -- never a TUI-side list",
        ),
        ("Enter", "opens the document's own text, verbatim -- its words, not a summary"),
    ),
    "research-doc": (
        (
            "the document",
            "the file's own lines, wrapped to the 80-column budget and read BOUNDED "
            "(the first MiB, with a loud truncation note if the file is past it)",
        ),
    ),
    "research-trials": (
        (
            "the rows",
            "every trial the ledger records, with the two N accountings; the chain "
            "verdict under them is the SAME read-only verify `keel trials verify` runs",
        ),
    ),
}


def build_research_menu_lines(*, cursor: int = 0, message: str | None = None) -> list[ScreenLine]:
    """The Research sub-menu screen: every entry with its description wrapped to the
    80-column budget, exactly one cursor-marked row. PURE."""
    lines: list[ScreenLine] = [
        ScreenLine("keel console -- research", "heading"),
        ScreenLine("read-only browsers over the repo's evidence corpus", "muted"),
        _blank(),
    ]
    cursor = max(0, min(cursor, len(RESEARCH_MENU) - 1))
    for index, entry in enumerate(RESEARCH_MENU):
        marker = ">" if index == cursor else " "
        head = f"{marker} {entry.ordinal:>2}  {entry.label}"
        lines.append(ScreenLine(head, "heading" if index == cursor else "normal"))
        for wrapped in _wrap(f"{entry.description}.", indent="      "):
            lines.append(ScreenLine(wrapped, "muted"))
    lines.append(_blank())
    lines.append(ScreenLine("up/k down/j move · Enter/Space select · 1-4 jump", "muted"))
    lines.append(ScreenLine("q/Esc/m to the console menu", "muted"))
    if message is not None:
        lines.append(_blank())
        for part in message.splitlines():
            lines.append(ScreenLine(part, _message_style(part)))
    return lines


# -- the corpus readers (O5) ------------------------------------------------------------------


@dataclass(frozen=True)
class DocFile:
    """One corpus document with its display facts: WHEN it was written (mtime) and how big
    it is -- carried rather than re-`stat`-ed per render, the scout browser's `ScoutFile`
    convention."""

    path: Path
    mtime_ts: float
    size_bytes: int


def list_documents(directory: Path, *suffixes: str) -> tuple[DocFile, ...]:
    """Every document under `directory` (filtered by `suffixes` when given, else every
    file), NEWEST FIRST by (mtime, name) -- the scout browser's own contract: an absent or
    unreadable directory is `()` rather than an exception, a reader must render a calm
    empty state, and per-file stat failures cost that row, never the whole list."""
    try:
        if not directory.is_dir():
            return ()
    except OSError:
        return ()
    found: list[DocFile] = []
    for candidate in directory.iterdir():
        if not candidate.is_file():
            continue
        if suffixes and candidate.suffix not in suffixes:
            continue
        try:
            stat = candidate.stat()
        except OSError:
            continue
        found.append(DocFile(path=candidate, mtime_ts=stat.st_mtime, size_bytes=stat.st_size))
    found.sort(key=lambda f: (f.mtime_ts, f.path.name), reverse=True)
    return tuple(found)


def read_document_lines(path: Path, *, max_bytes: int = MAX_DOC_BYTES) -> list[str]:
    """The document's own lines, BOUNDED: at most the first `max_bytes` bytes are read,
    decoded UTF-8 (errors replaced -- a stray byte must not kill the view), with a loud
    truncation note at the head when the file is past the bound. An unreadable file is a
    calm one-line notice, never a traceback."""
    try:
        with path.open("rb") as handle:
            blob = handle.read(max_bytes + 1)
    except OSError as exc:
        return [f"(unreadable: {exc})"]
    lines: list[str] = []
    if len(blob) > max_bytes:
        try:
            total = path.stat().st_size
        except OSError:
            total = -1
        total_note = f"{total} bytes" if total >= 0 else "an unknown size"
        lines.append(
            f"(truncated: the file is {total_note}; showing the first "
            f"{max_bytes // 1024} KiB of it)"
        )
        blob = blob[:max_bytes]
    text = blob.decode("utf-8", errors="replace")
    lines.extend(text.splitlines())
    return lines


def cached_document_lines(
    path: Path, cache: dict[tuple[str, int], list[str]]
) -> list[str]:
    """`read_document_lines` for the doc view, cached per (path, mtime_ns): the view
    repaints every poll, and re-reading an UNCHANGED document each time is pure waste; a
    changed mtime (or an unstatable file -- a key that can never re-hit) refreshes. The
    cache is the caller's dict, single purpose: one document is open at a time, and the
    caller clears it when another is opened (the `cached_scout_view` lesson, verbatim)."""
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        mtime_ns = -1  # unstatable: a key that can never be re-hit, so it never caches
    key = (str(path), mtime_ns)
    if key in cache:
        return cache[key]
    lines = read_document_lines(path)
    cache.clear()  # single entry: only the open document is worth holding
    cache[key] = lines
    return lines


def build_doc_list_lines(
    title: str, files: tuple[DocFile, ...], directory: Path, *, cursor: int = 0
) -> list[ScreenLine]:
    """A corpus's file list: every document newest-first with its date and size, exactly
    one cursor row, and an empty state that NAMES the directory it read. PURE."""
    import time

    lines: list[ScreenLine] = [
        ScreenLine(f"keel console -- research / {title}", "heading"),
    ]
    for wrapped in _wrap(f"in {directory}, newest first", indent=""):
        lines.append(ScreenLine(wrapped, "muted"))
    lines.append(_blank())
    if not files:
        for wrapped in _wrap(f"no documents -- {directory} is empty or absent.", indent=""):
            lines.append(ScreenLine(wrapped, "normal"))
        lines.append(_blank())
        lines.append(ScreenLine("Press q, Esc or m to return to the Research menu.", "muted"))
        return lines
    cursor = max(0, min(cursor, len(files) - 1))
    for index, doc in enumerate(files):
        marker = ">" if index == cursor else " "
        day = time.strftime("%Y-%m-%d %H:%M", time.localtime(doc.mtime_ts))
        # Wrapped to the budget, not clipped: a long report filename's tail is exactly the
        # part that identifies the file (the same rule every console screen keeps).
        for wrapped in _wrap(
            f"{marker} {doc.path.name} · written {day} · {doc.size_bytes} bytes", indent=""
        ):
            lines.append(
                ScreenLine(wrapped, "heading" if index == cursor else "normal")
            )
    lines.append(_blank())
    lines.append(
        ScreenLine(
            "Enter opens · up/k down/j move · q/Esc/m back to the Research menu", "muted"
        )
    )
    return lines


def build_doc_lines(
    title: str, path: Path, lines_of: list[str]
) -> list[ScreenLine]:
    """The chosen document, rendered as text: the corpus's title, the file's name, then
    the document's OWN lines verbatim (its words, not a summary of them). PURE."""
    out: list[ScreenLine] = [
        ScreenLine(f"keel console -- research / {title}", "heading"),
    ]
    for wrapped in _wrap(str(path), indent=""):
        out.append(ScreenLine(wrapped, "muted"))
    out.append(_blank())
    for line in lines_of:
        if not line.strip():
            out.append(_blank())
            continue
        # Wrap long lines to the budget rather than clipping: a clipped table row or
        # sentence tail is the part an operator scrolled for.
        for wrapped in textwrap.wrap(line, width=_WIDTH) or [""]:
            out.append(ScreenLine(wrapped, "normal"))
    out.append(_blank())
    out.append(
        ScreenLine(
            "up/k down/j scroll · PgUp/PgDn/Home/End · q/Esc/m back to the list", "muted"
        )
    )
    return out


# -- the trials reader (list + verify, read-only) ---------------------------------------------


def build_trials_lines(
    ledger_path: Path | None = None,
) -> list[ScreenLine]:
    """The trials-ledger view: `trials list`'s own rendering (one row per trial, the two N
    accountings) plus `verify`'s chain verdict -- the SAME service reads the CLI commands
    make, read-only. The path defaults to the ledger's OWN constant, read at CALL time (so
    a test-isolated or relocated ledger relocates this reader with it). An absent ledger
    is a calm empty state that names the path."""
    path = trials_ledger.DEFAULT_LEDGER_PATH if ledger_path is None else ledger_path
    lines: list[ScreenLine] = [
        ScreenLine("keel console -- research / trials ledger", "heading"),
    ]
    for wrapped in _wrap(f"ledger: {path}", indent=""):
        lines.append(ScreenLine(wrapped, "muted"))
    lines.append(_blank())
    try:
        trials = trials_ledger.read_trials(path)
    except OSError:
        for wrapped in _wrap(
            f"no trials on record -- {path} does not exist yet (a trial is "
            "recorded by `keel trials record` and by every `keel simulate` run).",
            indent="",
        ):
            lines.append(ScreenLine(wrapped, "normal"))
        lines.append(_blank())
        lines.append(ScreenLine("Press q, Esc or m to return to the Research menu.", "muted"))
        return lines
    if not trials:
        for wrapped in _wrap(
            f"the ledger at {path} holds no trials yet.", indent=""
        ):
            lines.append(ScreenLine(wrapped, "normal"))
    for index, record in enumerate(trials, start=1):
        flag = " [series_missing]" if record.series_missing else ""
        lines.append(
            ScreenLine(
                f"{index:>4}  {record.trial_id:<34} {record.rule:<18} "
                f"{record.provenance:<9} {record.kind:<16} {record.decision}{flag}",
                "normal",
            )
        )
    m, n_decisions = trials_ledger.trial_counts(trials)
    lines.append(_blank())
    lines.append(ScreenLine(f"M={m}  N_decisions={n_decisions}", "normal"))
    # The chain verdict, through the SAME read-only verify the CLI runs.
    errors = trials_ledger.verify_chain(path)
    if not errors:
        lines.append(ScreenLine("chain intact", "ok"))
    else:
        lines.append(ScreenLine(f"CHAIN BROKEN -- {len(errors)} error(s):", "alert"))
        for error in errors:
            for wrapped in _wrap(error, indent="      "):
                lines.append(ScreenLine(wrapped, "alert"))
    lines.append(_blank())
    lines.append(
        ScreenLine(
            "up/k down/j scroll · PgUp/PgDn/Home/End · q/Esc/m back to the Research menu",
            "muted",
        )
    )
    return lines
