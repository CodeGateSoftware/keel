"""Which workflows may skip a documentation-only change, and which may never (#644).

Three workflows read no documentation, so a docs-only change produces no signal from them
and the run is pure cost. `ci.yml` is the opposite: keel's documentation is UNDER TEST, and
`ci.yml` is what runs those tests. Two of them exist because docs drifted and nobody noticed
-- `test_python_floor.py` (five places said Python 3.11 while the wheels required 3.14,
#595/#607) and `test_doc_links.py`'s `SHA256SUMS-*` pin (the docs promised one file, the
workflow shipped three, #605/#618). A docs-only pull request is the ONLY kind those pins are
for, so filtering docs out of `ci.yml` would disable them on exactly the changes they catch.

That asymmetry is a decision, not an accident, and a comment cannot enforce it. This module
is the enforcement: the filter is required where it belongs and forbidden where it does not.
"""

from __future__ import annotations

from pathlib import Path

from tests._workflow_yaml import strict_load

_WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"

#: Reads no documentation; a docs-only change must not start it.
FILTERED = ("code-quality.yml", "security.yml", "migrate.yml")

#: Runs the doc pins. Must start on a docs-only change, forever.
UNFILTERED = "ci.yml"

_EXPECTED = {"docs/**", "**/*.md", "LICENSE"}


def _triggers(name: str) -> dict:
    """The `on:` block. PyYAML resolves the bare key `on` to the boolean True (YAML 1.1),
    which is why this is not simply `doc["on"]` -- a detail that silently returns None."""
    doc = strict_load((_WORKFLOWS / name).read_text(encoding="utf-8"), source=name)
    return doc[True] if True in doc else doc["on"]


def test_the_docs_filtered_workflows_skip_documentation_on_every_event_they_answer() -> None:
    """Every `push`/`pull_request` trigger these declare carries the filter -- not just the
    first one. `security.yml` answers BOTH, so a docs-only pull request would otherwise cost
    two of its runs where the others cost one; it was missed on the first pass of #644's sweep
    for exactly that reason."""
    for name in FILTERED:
        on = _triggers(name)
        answered = [e for e in ("push", "pull_request") if e in on]
        assert answered, f"{name} answers neither push nor pull_request -- has it been rewritten?"
        for event in answered:
            block = on[event] or {}
            ignored = set(block.get("paths-ignore") or ())
            assert ignored == _EXPECTED, (
                f"{name}'s `{event}` trigger ignores {sorted(ignored)}, expected "
                f"{sorted(_EXPECTED)} -- this workflow reads no documentation and a docs-only "
                "change must not start it (#644)"
            )


def test_ci_never_skips_a_documentation_change() -> None:
    """**The pin that matters.** `ci.yml` runs the tests that READ the docs, so a filter here
    would disable them on the only pull requests they are for. If a future change needs CI to
    skip something, it needs a different mechanism and a written argument -- not this one."""
    on = _triggers(UNFILTERED)
    for event in ("push", "pull_request"):
        block = on.get(event) or {}
        assert not block.get("paths-ignore"), (
            f"ci.yml's `{event}` trigger has grown a paths-ignore. keel's documentation is "
            "under test -- tests/test_python_floor.py reads README.md and "
            "docs/desktop-install.md, tests/test_doc_links.py pins the SHA256SUMS filenames "
            "against release.yml. Filtering docs here disables those pins on precisely the "
            "docs-only pull requests they exist to catch (#644, #607, #618)."
        )
        assert not block.get("paths"), (
            f"ci.yml's `{event}` trigger has grown a paths allowlist, which skips everything "
            "NOT listed -- the same defect as paths-ignore, inverted (#644)."
        )
