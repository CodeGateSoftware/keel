"""The scholarly review status, stated honestly: not reviewed, with the path defined (#289).

A Muslim developer evaluating keel asks one question the code cannot answer by being read:
who checked the fiqh? The honest answer today is nobody -- the basis is one operator's
sourced reading, and #289's judgement is that an ambiguous claim here is worse than a modest
one, because "an overstated claim is not a marketing problem, it is a trust-destroying one."
What shipped is the modest claim said out loud -- no scholarly review has occurred, each
operator owns their own attestations -- plus the documented path a review would take when a
reviewer engages: what would be reviewed, what a reviewer would and would not be endorsing,
how a review would be recorded, and who might be asked.

This file pins that stance so it can only harden, never soften. The no-review sentence is
pinned verbatim in the README's first screen AND in the status section of docs/fiqh-basis.md
(the house pattern from `test_governance.py`'s boundary sentence); the ratchet is pinned (the
status changes only by a dated addendum naming reviewer and scope); and the negative test
asserts that no affirmative review claim appears anywhere in the three documents a trust
decision might be made from.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

#: The document whose review-status section defines the path (#289).
_DOC = "docs/fiqh-basis.md"

#: The sentence both documents must state the status with, quoted exactly -- pinned in the
#: README's first screen and in the status section, so neither can soften while the other
#: stays blunt. "No scholarly review" plus "has occurred", in one unsoftenable clause.
_NO_REVIEW = "No scholarly review of keel's fiqh basis has occurred"

#: The section heading (#289), whose placement between "How to disagree" and "Sources index"
#: is part of its meaning.
_SECTION_HEADING = "## Scholarly review status"

#: Where the README's link must land -- the heading's GitHub anchor, pinned so the README
#: cannot cite a section that has been renamed away.
_SECTION_LINK = "docs/fiqh-basis.md#scholarly-review-status"

#: The knowledge base the basis was extracted into, named in the status section so "the
#: operator's reading" is a checkable path, not a gesture at one.
_KB_DIR = "docs/superpowers/references/trading-knowledge-base/"

#: Affirmative review claims that must appear NOWHERE in the three files a trust decision is
#: made from. Negated forms ("not reviewed", "not endorsed") are legitimate and pinned
#: positively by the other tests here; these are the positive phrasings only, hyphen and
#: space spellings alike.
_FORBIDDEN_CLAIMS = (
    "scholar-approved",
    "scholar approved",
    "reviewed and approved",
    "certified",
    "endorsed by",
)


def _unwrapped(text: str) -> str:
    """Join markdown wrapping: drop blockquote markers, then collapse all whitespace."""
    return " ".join(re.sub(r"(?m)^\s*>\s?", "", text).split())


def _read(relative: str) -> str:
    """A repo file's text; empty until it exists, so a red run FAILS rather than errors."""
    path = _ROOT / relative
    return path.read_text() if path.is_file() else ""


def _first_screen(relative: str) -> str:
    """Everything before the first `##` section: what a reader sees without scrolling."""
    return _read(relative).split("\n## ", 1)[0]


def _status_section() -> str:
    """The review-status section alone (its `###` subsections included), wrap-joined.

    Pins must land INSIDE the section: a "not reviewed" that has drifted into some other
    part of the document is a softer claim than the one #289 asks the section to make.
    """
    doc = _read(_DOC)
    if _SECTION_HEADING not in doc:
        return ""
    return _unwrapped(doc.split(_SECTION_HEADING, 1)[1].split("\n## ", 1)[0])


def _claim_normalized(text: str) -> str:
    """Collapse wrapping the way a reader does, including a hyphen split across lines.

    A claim wrapped as "scholar-\\napproved" reads as one word on the page; the scanner must
    see it the same way, or the hyphen becomes a loophole.
    """
    return re.sub(r"-\s+", "-", _unwrapped(text)).lower()


def test_the_readme_first_screen_states_the_no_review_fact():
    """#289's acceptance: the README says which of the three options keel actually offers.

    The modest option, said out loud, in the first screen -- where `test_governance.py` puts
    the boundary sentence for the same reason: a reader deciding whether to trust keel must
    not have to scroll to learn that nobody has checked the fiqh.
    """
    assert _NO_REVIEW in _unwrapped(_first_screen("README.md")), (
        "the README's first screen must state, verbatim, that "
        f"{_NO_REVIEW!r} -- the modest claim said out loud is the only claim available"
    )


def test_the_readme_no_review_statement_links_the_fiqh_basis_review_section():
    """The statement does not stand alone: it hands the reader the section that elaborates.

    Pinned two-sided -- the README must carry the link, and the link must point at a heading
    the document actually has -- so a rename of the section fails here instead of leaving a
    link into thin air.
    """
    assert _SECTION_LINK in _first_screen("README.md"), (
        f"the README's no-review statement must link {_SECTION_LINK} -- the status claim "
        "and the path that defines it travel together"
    )
    assert _SECTION_HEADING in _read(_DOC), (
        f"{_DOC} must carry the heading {_SECTION_HEADING!r} that the README's link targets"
    )


def test_the_status_section_sits_between_how_to_disagree_and_the_sources_index():
    """The section's placement is part of its meaning: after dissent, before the sources.

    A reader who has just been told how to disagree is the reader most owed the question
    "and who checked this?" -- and the sources index is the document's last word, so the
    status must be stated before the citations begin.
    """
    doc = _read(_DOC)
    assert doc.index("## How to disagree") < doc.index(_SECTION_HEADING) < doc.index(
        "## Sources index"
    ), (
        f"{_DOC} must place {_SECTION_HEADING!r} after '## How to disagree' and before "
        "'## Sources index'"
    )


def test_the_status_section_says_not_reviewed_plainly():
    """The section opens with the same unsoftenable sentence, and names the status plainly.

    Two-sided within the document: the sentence the README pins must be HERE too (one
    sentence, two places, like the governance boundary), and the plain words "not reviewed"
    must appear inside the section itself -- a status a reader must infer is a status they
    will overread.
    """
    section = _status_section()
    assert _NO_REVIEW in section, (
        f"the status section must open with the same sentence the README pins: {_NO_REVIEW!r}"
    )
    assert "not reviewed" in section.lower(), (
        f"{_DOC}'s status section must say the status plainly -- 'not reviewed', inside the "
        "section itself"
    )


def test_the_status_section_names_what_the_basis_actually_is():
    """'Not reviewed' is only honest if the section also says what the basis IS.

    The operator's reading, the sources it reads (Ayub, the OIC/AAOIFI/IIFA materials,
    Mufti Faraz Adam's papers), and the knowledge base it was extracted into -- named as a
    path, so the auditable thing is findable from the very sentence that disclaims it.
    """
    section = _status_section()
    assert "operator's reading" in section, (
        "the status section must name the basis as the operator's reading of the sources"
    )
    assert "Ayub" in section and "AAOIFI" in section and "Faraz Adam" in section, (
        "the status section must name the sources the reading reads: Ayub, the "
        "OIC/AAOIFI/IIFA materials, and Mufti Faraz Adam's papers"
    )
    assert _KB_DIR in section, (
        f"the status section must point at the knowledge base ({_KB_DIR}) the reading was "
        "extracted into"
    )


def test_the_status_section_lists_what_a_reviewer_is_not_endorsing():
    """The not-endorsement list, pinned on its load-bearing items.

    A reviewer of the mapping must not be readable as a reviewer of the strategy, and must
    not settle the premise: §71.1's non-ruling is this document's deepest caveat, and a
    review of the machinery cannot be allowed to launder it into a permission.
    """
    section = _status_section().lower()
    assert "not the trading strategy" in section, (
        "the not-endorsement list must say a reviewer is not endorsing the trading strategy "
        "or its performance"
    )
    assert "not the prudential rails" in section, (
        "the not-endorsement list must say a reviewer is not endorsing the prudential rails"
    )
    assert "§71.1" in section and "non-ruling" in section, (
        "the not-endorsement list must keep the §71.1 non-ruling standing: reviewing the "
        "machinery does not settle the premise the machinery presupposes"
    )
    assert "does not settle the premise" in section, (
        "the not-endorsement list must say so in exactly those words -- the premise is the "
        "part most likely to be overread into permission"
    )


def test_the_status_section_defines_the_addendum_ratchet():
    """The status can change by ONE mechanism only: a dated addendum naming its scope.

    This is the ratchet: not-reviewed can become reviewed-with-a-named-scope, never a vague
    approval. Pinning the mechanism's parts -- dated, naming the reviewer, the scope
    reviewed, versioned in git -- means a future edit that loosens any one of them fails
    here rather than shipping a softer claim.
    """
    section = _status_section()
    pins = (
        "dated addendum",
        "naming the reviewer",
        "the scope reviewed",
        "versioned in git",
        "reviewed-with-a-named-scope",
    )
    for pin in pins:
        assert pin in section, (
            f"the status section must pin {pin!r}: the only way the status leaves 'not "
            "reviewed' is a dated addendum naming reviewer, scope, findings, and what "
            "changed"
        )


def test_no_document_claims_a_review_has_occurred():
    """The one pin that cannot be allowed to go soft: nowhere is review CLAIMED.

    #289's warning is the law here: "an overstated claim is not a marketing problem, it is
    a trust-destroying one" -- a reader who believed a review had happened and later learns
    none existed will not believe anything else the project says. The scanner first proves
    it can fail (a hyphen-wrapped synthetic claim is caught), then asserts the affirmative
    phrasings appear nowhere in the three documents a trust decision is made from; the
    documents' many legitimate NEGATED uses ("not reviewed", "not endorsed") are pinned
    positively by the tests above.
    """
    assert "scholar-approved" in _claim_normalized("scholar-\napproved"), (
        "the claim scanner must catch a claim hyphen-wrapped across lines, or the hyphen "
        "is a loophole"
    )
    for relative in ("README.md", "CONTRIBUTING.md", _DOC):
        text = _claim_normalized(_read(relative))
        found = [claim for claim in _FORBIDDEN_CLAIMS if claim in text]
        assert not found, (
            f"{relative} claims scholarly review that has not occurred ({found}): per "
            "#289, an overstated claim here is not a marketing problem, it is a "
            "trust-destroying one -- state 'not reviewed' plainly instead"
        )


def test_the_outreach_shortlist_names_programmes_and_states_the_approach_is_unmade():
    """The shortlist is a plan on paper, and must be labelled as unexecuted.

    Naming IIUM, INCEIF, and Durham without saying nobody has been approached would imply an
    approach; the sentence doing the disclaiming is pinned, not just the names.
    """
    section = _status_section()
    for programme in ("IIUM", "INCEIF", "Durham"):
        assert programme in section, (
            f"the outreach shortlist must name {programme} among the candidates (#289)"
        )
    assert "has not been taken" in section and "as of this writing" in section, (
        "the shortlist must state plainly that approaching a reviewer is the operator's "
        "action and has not been taken as of this writing -- a shortlist that reads as "
        "outreach already made is the overstated claim in different clothes"
    )
