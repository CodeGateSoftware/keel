"""The research corpora on disk: where they are, what is in them, and bounded reads (#541).

**Why this module exists.** These four things lived in `keel/commands/research_console.py` and
were the only part of the console layer anything outside it used -- `keel/mcp/tools.py` imports
them to answer an assistant's questions about the corpora. #541 deleted the TUI and, with it, the
console layer that was reachable only from inside it; this is what had to survive, moved rather
than re-implemented so the MCP server keeps reading exactly what the console read.

Top level rather than under `keel/commands/`, for the same reason `keel/capabilities.py` and
`keel/install.py` are: it is not a command, it has no console of its own, and the directory it
came from is the one #525 is about.

**Everything here is total.** A missing directory is `()`, an unreadable file is a one-line
notice, and a file past the bound is truncated with a note saying so -- never an exception and
never a silent partial view. That contract came from a reader that repainted on a timer, where a
raised exception meant a dead screen; it is kept because the caller is now an assistant, where a
raised exception means an answer that never arrives.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

#: The research-docs corpus (`docs/research/`).
RESEARCH_DOCS_DIR = Path("docs/research")

#: The most bytes of one document this module will ever read (1 MiB).
#:
#: A reader must never have its cost grow with whatever a runaway writer put in the file. A
#: document past the bound reads its first `MAX_DOC_BYTES` with a loud truncation note, never an
#: unbounded read and never a silent partial view.
MAX_DOC_BYTES = 1024 * 1024


def corpus_path(target: str) -> Path:
    """The corpus directory for a `target` ("experiments"/"research"/"reports").

    Single-sourced from the code that WRITES into it and resolved at call time: the experiments
    corpus is the trials ledger's own directory (`DEFAULT_LEDGER_PATH.parent` -- read when asked,
    so a test-isolated or relocated ledger relocates the reader with it), the reports corpus is
    where `run_simulation` writes (`default_report_path`), and the research docs are the repo's
    own `docs/research/`. Never a path table of its own that could drift from the writer.
    """
    from keel.commands.simulate import default_report_path
    from keel.research import ledger as trials_ledger

    if target == "experiments":
        return trials_ledger.DEFAULT_LEDGER_PATH.parent
    if target == "reports":
        return default_report_path(0).parent
    return RESEARCH_DOCS_DIR


@dataclass(frozen=True)
class DocFile:
    """One corpus document with its display facts: WHEN it was written (mtime) and how big it is
    -- carried rather than re-`stat`-ed per render."""

    path: Path
    mtime_ts: float
    size_bytes: int


def list_documents(directory: Path, *suffixes: str) -> tuple[DocFile, ...]:
    """Every document under `directory` (filtered by `suffixes` when given, else every file),
    NEWEST FIRST by (mtime, name).

    An absent or unreadable directory is `()` rather than an exception -- a caller must be able
    to render a calm empty state -- and per-file stat failures cost that row, never the whole
    list."""
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
    """The document's own lines, BOUNDED: at most the first `max_bytes` bytes are read, decoded
    UTF-8 (errors replaced -- a stray byte must not kill the read), with a loud truncation note at
    the head when the file is past the bound. An unreadable file is a calm one-line notice, never
    a traceback."""
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
