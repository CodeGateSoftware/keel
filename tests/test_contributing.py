"""CONTRIBUTING.md: the bar, stated, so nobody has to guess it.

This repository's signature is documentation that argues: decisions carry their reasoning,
comments name what was MEASURED rather than what was assumed, and rejected alternatives stay
recorded. It is also the single biggest barrier to contributing, because nobody will guess a
standard they have never seen written down (#282). An unwritten bar does not filter for
quality -- it filters for clairvoyance: PRs arrive at ordinary quality, get heavy review, and
the contributor quietly leaves. That is the most common way a promising project loses its
first ten contributors.

These tests pin CONTRIBUTING.md to the specific things #282 asks it to state: the gates a PR
must pass, the documentation standard WITH a worked example lifted from the code, the
tests-first expectation, the commit convention, and the scope rules. The governance boundary
and licence rationale already have their own guards in `test_governance.py` and
`test_licensing.py`; this file covers the parts a first-time contributor reads before opening
a PR.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

#: The sentence from `_open_exposure_by_asset`'s docstring (`keel/execution/guards.py`) used as
#: the worked example. Quoted in CONTRIBUTING.md, asserted against the source below, so the
#: example cannot drift from the code it explains.
_WORKED_EXAMPLE_SNIPPET = "never let a row nobody can read make this figure SMALLER"


def _contributing() -> str:
    return (_ROOT / "CONTRIBUTING.md").read_text()


def _unwrapped(text: str) -> str:
    """Join markdown wrapping: drop blockquote markers, then collapse all whitespace.

    The worked example is quoted inside a `>` block, where a sentence can break as
    `...make this\\n> figure SMALLER...`; a plain whitespace-collapse leaves the `>` in the
    way of the match.
    """
    return " ".join(re.sub(r"(?m)^\s*>\s?", "", text).split())


def test_the_gates_a_pr_must_pass_are_stated():
    """Dev setup plus the three gates, as runnable commands.

    'Run the tests' is not an instruction; `uv run pytest -q` is. The commands are pinned
    verbatim because a gate nobody can paste is a gate nobody runs.
    """
    text = _contributing()
    for command in (
        "uv sync --all-extras --dev",
        "uv run ruff check",
        "uv run mypy",
        "uv run pytest -q",
    ):
        assert command in text, f"CONTRIBUTING.md must state the gate command {command!r}"


def test_the_documentation_standard_has_a_worked_example_from_the_code():
    """The standard is taught from a real comment, not described in the abstract.

    The example must be QUOTED from the codebase and must remain there: CONTRIBUTING.md
    reproduces a distinctive line from `guards.py`'s `_open_exposure_by_asset` docstring, and
    this test asserts both halves -- the quote in the doc, and the quote's continued existence
    in the source. If the source comment is ever rewritten, this fails rather than letting the
    doc teach from an example that no longer exists.
    """
    text = _contributing()
    # Wrap-normalised: the quote is markdown, and markdown wraps wherever the line ends.
    assert _WORKED_EXAMPLE_SNIPPET in _unwrapped(text), (
        "CONTRIBUTING.md's documentation-standard section must quote the worked example "
        f"(from keel/execution/guards.py): expected {_WORKED_EXAMPLE_SNIPPET!r}"
    )
    guards = (_ROOT / "keel" / "execution" / "guards.py").read_text()
    assert _WORKED_EXAMPLE_SNIPPET in _unwrapped(guards), (
        "the comment CONTRIBUTING.md quotes as its worked example no longer exists in "
        "keel/execution/guards.py -- update the doc to quote a comment that does"
    )


def test_the_standard_names_what_makes_a_comment_acceptable():
    """The reader must be told the RULE, not only shown the example.

    Three properties, so a contributor can check their own writing before anyone else has to:
    it says WHY, it names what was MEASURED, and it says what it would take to change the
    decision.
    """
    text = _contributing().lower()
    assert "why" in text
    assert "measured" in text
    assert "decision" in text


def test_tests_first_is_expected_with_evidence():
    """The TDD expectation, including the evidence that makes it checkable.

    Tests written before the fix, and shown in the PR to have failed FOR THE RIGHT REASON --
    an assertion message, not an import error -- is the house style. Stated, it is a bar
    contributors can meet; unstated, it is a review surprise.
    """
    text = _unwrapped(_contributing()).lower()
    phrases = ("tests first", "test-first", "tests before", "tests come first")
    assert any(phrase in text for phrase in phrases), (
        "CONTRIBUTING.md must state that tests are written first"
    )
    assert "fail" in text and "right reason" in text, (
        "CONTRIBUTING.md must ask for evidence the failing tests failed for the right reason"
    )


def test_the_commit_convention_is_stated():
    """Conventional Commits, because that is what the history already does.

    The point is not aesthetics: `fix(strategy):` vs `feat(engine):` is the changelog, and a
    release process that reads it (see docs/RELEASING.md) silently degrades when a commit
    arrives untyped.
    """
    assert re.search(r"conventional commits", _contributing(), re.IGNORECASE), (
        "CONTRIBUTING.md must name Conventional Commits as the commit convention"
    )


def test_scope_guidance_separates_welcome_from_needs_discussion_from_out():
    """Three tiers of scope, with the guarded kinds named.

    Rails and default classifications are the two surfaces where a casual PR does quiet,
    distributed harm, so they are the two that must be called out as discuss-first. The
    governance section above already carries the fiqh reasoning; this pins that the SCOPE
    list exists and points at them.
    """
    text = _contributing().lower()
    assert "rail" in text, "scope guidance must name rails as discuss-first territory"
    assert "classification" in text, (
        "scope guidance must name default classifications as discuss-first territory"
    )
    assert "discussion" in text or "discuss" in text, (
        "scope guidance must say these need discussion BEFORE a PR, not review during one"
    )
    assert "out of scope" in text, "scope guidance must state what is out of scope entirely"
