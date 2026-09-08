"""Retired surfaces may be REMEMBERED in the docs, never OFFERED (#541).

`keel tui` was the console until #541 deleted it, along with roughly 10,000 lines of curses.
Months later the docs still sent operators to it: the README listed it as one of the ways to read
keel, `docs/glossary.md` claimed "the TUI's Help menu renders this file directly" -- a rendering
path `render_glossary` lost at #540, on top of the command lost at #541 -- and the runbook pointed
at the Account menu's update entry "(see 'The TUI console' for the ceremony)", a section that no
longer exists.

None of that was catchable: every statement was prose, and prose has no compiler.

THE RULE, and it deliberately permits the history. A decision record that erased its own subject
would be worse than a stale one -- `keel update` really did have two front-ends, and the runbook
explaining why it now has one is doing its job. So a retired surface may be named, provided the
line naming it also carries the issue that retired it. Mention it, date it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


#: Docs a reader acts on TODAY. Deliberately excludes `docs/superpowers/specs/` (dated design
#: artifacts), `docs/decisions/` (records of what was true when decided) and `docs/experiments/`
#: (dated measurements) -- all three are supposed to describe the past and would fail this rule
#: for doing their job.
def _current_facing() -> list[Path]:
    docs = [REPO_ROOT / "README.md"]
    docs.extend(sorted((REPO_ROOT / "docs").glob("*.md")))
    return [p for p in docs if p.is_file()]


#: Surfaces that were deleted, and the issue that deleted each. A paragraph may name one only if
#: it names the issue too.
#:
#: **Entries must be DISTINCTIVE tokens.** The match is `\b<surface>\b` case-insensitively over
#: whole documents, which is safe for `tui` and would be useless for something like `api` -- it
#: would fire on every page, and the next author would loosen the rule to escape the noise rather
#: than fix a document. If a retired surface has a common name, match its command
#: (`keel <name>`) instead of the bare word.
RETIRED: tuple[tuple[str, str], ...] = (("tui", "#541"),)


@pytest.mark.parametrize(("surface", "issue"), RETIRED)
def test_a_retired_surface_is_never_named_without_its_retirement(surface: str, issue: str) -> None:
    """Checked per PARAGRAPH, not per line.

    The first cut asked that the LINE naming the surface also cite the issue, and it failed on a
    sentence whose wrap put "`keel tui`, a command that no longer exists" two lines below its own
    "Until #541". Prose wraps at 100 characters here, so a line boundary carries no meaning and a
    rule keyed to one is a rule about the reflow -- the same trap that made a runbook locator break
    when its introducing sentence was reworded earlier in this series. A paragraph is the unit a
    reader actually takes the claim from.
    """
    offenders = []
    for doc in _current_facing():
        text = doc.read_text()
        offset = 1
        for paragraph in text.split("\n\n"):
            if re.search(r"\b" + surface + r"\b", paragraph, flags=re.I) and issue not in paragraph:
                first = paragraph.strip().splitlines()[0][:90]
                offenders.append(f"{doc.relative_to(REPO_ROOT)}:{offset}: {first}")
            offset += paragraph.count("\n") + 2
    assert not offenders, (
        f"a current-facing doc names the retired `keel {surface}` without citing {issue}, which "
        "reads as an offer rather than a memory:\n  " + "\n  ".join(offenders)
    )


def test_the_scan_covers_the_docs_it_claims_to() -> None:
    """A glob that matched nothing would make the test above pass over any wording at all."""
    docs = _current_facing()
    names = {p.name for p in docs}
    assert {"README.md", "glossary.md", "mcp-server.md", "operator-runbook.md"} <= names, names
    assert len(docs) >= 8, f"only {len(docs)} current-facing docs found"
