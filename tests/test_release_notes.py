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


# -- heading levels: PR bodies must nest UNDER their entry, not beside it ------


def test_body_headings_are_demoted_below_the_pr_heading():
    """A PR body full of `##` headings would otherwise render as siblings of the
    category headings and destroy the document outline. Found on real data."""
    from scripts.release_notes import demote_headings

    out = demote_headings("## Top\n\ntext\n\n### Nested\n")
    assert "#### Top" in out
    assert "##### Nested" in out


def test_demotion_preserves_relative_heading_depth():
    from scripts.release_notes import demote_headings

    out = demote_headings("# A\n## B\n### C\n")
    assert "#### A" in out and "##### B" in out and "###### C" in out


def test_demotion_never_exceeds_h6():
    from scripts.release_notes import demote_headings

    assert "####### " not in demote_headings("###### Deep\n")


def test_hashes_inside_code_fences_are_not_headings():
    from scripts.release_notes import demote_headings

    out = demote_headings("```bash\n# not a heading\n```\n\n## real heading\n")
    assert "# not a heading" in out
    assert "#### real heading" in out


def test_a_body_with_no_headings_is_untouched():
    from scripts.release_notes import demote_headings

    assert demote_headings("just prose\n") == "just prose\n"


def test_composed_notes_demote_real_body_headings():
    out = compose_release_notes(
        [_pr(4, title="T", body="## Section\n\nprose", labels=["feature"])], CATS
    )
    assert "## Features" in out
    assert "### T (#4)" in out
    assert "#### Section" in out


# -- size: GitHub rejects a release body over 125k characters ------------------


def test_a_long_body_is_truncated_with_a_pointer_to_the_pr():
    from scripts.release_notes import truncate_body

    out = truncate_body("x" * 5000, 1000, 42)
    assert len(out) < 1200
    assert "#42" in out and "truncated" in out


def test_truncation_closes_an_orphaned_code_fence():
    """Cutting mid-fence would break every following block in the release page."""
    from scripts.release_notes import truncate_body

    out = truncate_body("intro\n\n```python\n" + "y = 1\n" * 500, 200, 7)
    assert out.count("```") % 2 == 0, "left an unclosed code fence"


def test_a_short_body_is_never_truncated():
    from scripts.release_notes import truncate_body

    assert truncate_body("short", 1000, 1) == "short"


def test_total_output_respects_a_total_limit():
    """The real first release composed 168k chars against a 125k platform limit."""
    prs = [_pr(n, title=f"PR {n}", body="z" * 9000, labels=["feature"]) for n in range(60)]
    out = compose_release_notes(prs, CATS, total_limit=40_000)
    assert len(out) <= 40_000, f"composed {len(out)} chars, over the limit"
    assert "PR 59" in out, "every PR must still be listed, even if its body is trimmed"
