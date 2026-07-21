"""`scripts/release_notes.py` -- composing release notes from PR BODIES, not links.

The release workflow used to call GitHub's `generate-notes` API, which emits
`* <title> by @author in #N` -- titles and links only. These notes inline each merged PR's
cleaned body so a reader never has to click through to know what shipped.

Only the pure composition is tested here; fetching the PRs is `gh` glue in the workflow.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.release_notes import (  # noqa: E402
    Category,
    PullRequest,
    clean_pr_body,
    compose_release_notes,
    load_categories,
)

CATS = [
    Category(title="⚠️ Breaking changes", labels=("breaking",)),
    Category(title="Features", labels=("feature", "enhancement")),
    Category(title="Fixes", labels=("bug", "fix")),
    Category(title="Other changes", labels=("*",)),
]


def _pr(number, title="A change", body="Body text.", labels=()):
    return PullRequest(number=number, title=title, body=body, labels=tuple(labels))


# -- body cleaning -------------------------------------------------------------


def test_the_claude_code_footer_is_stripped_with_everything_after_it():
    body = "Real content.\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)\n\ntrailing"
    assert clean_pr_body(body) == "Real content."


def test_html_comments_are_stripped():
    body = "Visible.\n<!-- a reviewer checklist nobody wants in the notes -->\nAlso visible."
    cleaned = clean_pr_body(body)
    assert "reviewer checklist" not in cleaned
    assert "Visible." in cleaned and "Also visible." in cleaned


def test_co_authored_by_trailers_are_stripped():
    body = "Content.\n\nCo-Authored-By: Someone <a@b.c>"
    assert "Co-Authored-By" not in clean_pr_body(body)
    assert "Content." in clean_pr_body(body)


def test_runs_of_blank_lines_collapse():
    assert clean_pr_body("A.\n\n\n\n\nB.") == "A.\n\nB."


def test_an_empty_or_whitespace_body_cleans_to_empty_string():
    assert clean_pr_body("") == ""
    assert clean_pr_body("   \n\n  ") == ""
    assert clean_pr_body(None) == ""


# -- categorisation ------------------------------------------------------------


def test_norelease_prs_are_excluded_entirely():
    out = compose_release_notes([_pr(1, title="Hidden", labels=["norelease"])], CATS)
    assert "Hidden" not in out
    assert out == ""


def test_the_first_matching_category_wins():
    """A PR labelled both `feature` and `fix` belongs to Features -- it is listed first."""
    out = compose_release_notes([_pr(7, title="Dual", labels=["fix", "feature"])], CATS)
    assert "## Features" in out
    assert "## Fixes" not in out


def test_an_unlabelled_pr_falls_into_the_catch_all():
    out = compose_release_notes([_pr(9, title="Unlabelled")], CATS)
    assert "## Other changes" in out
    assert "### Unlabelled (#9)" in out


def test_empty_categories_are_omitted():
    out = compose_release_notes([_pr(3, labels=["feature"])], CATS)
    assert "## Features" in out
    assert "## Fixes" not in out
    assert "Breaking" not in out


# -- composition ---------------------------------------------------------------


def test_the_pr_body_is_inlined_under_a_titled_heading():
    out = compose_release_notes(
        [_pr(12, title="Add the thing", body="It does X.\nAnd Y.", labels=["feature"])], CATS
    )
    assert "### Add the thing (#12)" in out
    assert "It does X." in out
    assert "And Y." in out


def test_a_pr_with_no_body_renders_a_placeholder_not_a_blank():
    out = compose_release_notes([_pr(5, title="Terse", body="", labels=["feature"])], CATS)
    assert "### Terse (#5)" in out
    assert "_(no description)_" in out


def test_prs_are_grouped_under_their_category_in_category_order():
    out = compose_release_notes(
        [
            _pr(2, title="Fixed it", labels=["bug"]),
            _pr(1, title="Built it", labels=["feature"]),
            _pr(3, title="Broke it", labels=["breaking"]),
        ],
        CATS,
    )
    assert out.index("Breaking changes") < out.index("## Features") < out.index("## Fixes")


# -- categories come from .github/release.yml ----------------------------------


def test_load_categories_reads_the_repo_release_yml():
    """`.github/release.yml` stays the single source of truth for grouping."""
    cats = load_categories(REPO_ROOT / ".github" / "release.yml")
    assert cats, "expected categories from .github/release.yml"
    assert cats[0].title == "⚠️ Breaking changes"
    assert cats[-1].labels == ("*",), "the catch-all must be last"
    titles = [c.title for c in cats]
    assert "Features" in titles and "Compliance & rails" in titles
