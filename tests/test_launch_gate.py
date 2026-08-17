"""The pre-launch gate and the announcement plan — written down, in that order (#291).

#291's premise is that a project which attracts attention before it can absorb it dies of
that attention. The gate exists so "are we ready to announce?" is a checklist with
evidence links, not a feeling; and the plan exists so the announcement, when it comes,
says the honest thing in the right rooms, in the right order. Both live in
docs/launch.md, and this file pins them so the gate can only ratchet: a box may be ticked
with evidence, but the gate itself cannot quietly disappear from the repo that needs it.

Three facts are pinned hardest. The measured result must be stated IN THE POST with its
real numbers -- 0 of 90 and 0 of 82 under production-faithful execution (the number in
#291's own text, '0 of 20', was a misremembering of an earlier 0-of-19 hourly record; the
gate doc corrects it rather than repeating it). The audience order is the issue's order --
small high-trust communities before any broad launch -- because a Show HN first is the
failure mode. And the maintainer-response commitment must be stated where contributors
read it: CONTRIBUTING.md, with honest solo-maintainer numbers, not the 24/7 responsiveness
a big project can imply.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

#: The gate document itself.
_DOC = "docs/launch.md"

#: The experiment record behind the real numbers -- the same file test_readme.py pins.
_EXPERIMENT_RECORD = "docs/experiments/2026-08-13-restated-under-a-production-faithful-engine.md"

#: The measured verdict, with the numbers that are actually in the record. An announcement
#: plan that states a softer or rounder figure is a plan to misquote the project's one
#: credibility asset.
_REAL_NUMBERS = ("0 of 90", "0 of 82")

#: The rule the whole document exists to enforce.
_NOTHING_BEFORE_THE_GATE = "nothing is announced until every box below is ticked"

#: The audience, in the order #291 gives: trust-rich small rooms before broad ones. The
#: ordering is the content; pinned as an ordered tuple and asserted by index.
_AUDIENCE_ORDER = (
    "Islamic fintech",
    "r/islamicfinance",
    "IIUM, INCEIF",
    "Hacker News",
)


def _unwrapped(text: str) -> str:
    """Join markdown wrapping: drop blockquote markers, then collapse all whitespace."""
    return " ".join(re.sub(r"(?m)^\s*>\s?", "", text).split())


def _read(relative: str) -> str:
    """A repo file's text; empty until it exists, so a red run FAILS rather than errors."""
    path = _ROOT / relative
    return path.read_text() if path.is_file() else ""


def test_the_gate_document_exists_and_states_the_rule():
    """The gate is a document, and its first law is stated in it, verbatim.

    A gate that lives in someone's head opens when energy is high and evidence is thin;
    the sentence is pinned so weakening it to 'announce when it feels ready' is a diff.
    """
    text = _unwrapped(_read(_DOC))
    assert text, f"{_DOC} must exist -- the gate is a document, not a feeling"
    assert _NOTHING_BEFORE_THE_GATE in text.lower() or _NOTHING_BEFORE_THE_GATE in text, (
        f"{_DOC} must state the rule verbatim: {_NOTHING_BEFORE_THE_GATE!r}"
    )


def test_the_gate_names_each_phase_and_the_scans():
    """Every prerequisite the issue lists, present as gate items with their evidence.

    Phase 6 (licence, discoverability, positioning), Phase 7 (contributor readiness),
    the fiqh basis and the review-path stance, the Arabic entry point, CI green on main,
    the code-quality scans actually configured, and the maintainer-response commitment
    stated in CONTRIBUTING -- the checklist is the document's spine.
    """
    text = _unwrapped(_read(_DOC))
    for pin in (
        "Phase 6",
        "Phase 7",
        "fiqh basis",
        "review",
        "Arabic",
        "CI green",
        "scans",
        "response",
    ):
        assert pin.lower() in text.lower(), (
            f"{_DOC}'s gate must name {pin!r} -- a prerequisite the gate does not list is "
            "a prerequisite that can be forgotten"
        )


def test_the_announcement_plan_states_the_real_numbers():
    """The post states the measured result itself, with the numbers the record shows.

    '0 of 20' (the figure in #291's text) is a misremembering; the record's verdict is
    0 of 90 and 0 of 82. The plan is pinned to the real figures and to the record link,
    because 'being the one who says it first' only works if what is said first is true.
    """
    text = _unwrapped(_read(_DOC))
    for number in _REAL_NUMBERS:
        assert number in text, (
            f"{_DOC} must state the measured result as {number!r} -- the record's real "
            "verdict, not a rounder figure that flatters it"
        )
    # launch.md lives in docs/, so its live link target is experiments/... relative to
    # itself; the pin checks the link that actually resolves on GitHub, and the file
    # existence check below keeps that link honest against renames.
    assert "experiments/2026-08-13-restated-under-a-production-faithful-engine.md" in text, (
        f"{_DOC} must link the experiment record beside the numbers -- the link target as "
        "it resolves from docs/launch.md"
    )
    assert (_ROOT / _EXPERIMENT_RECORD).is_file(), (
        "the experiment record the gate cites no longer exists -- update the link"
    )


def test_the_audience_order_is_small_rooms_before_broad_launch():
    """The issue's order, pinned: practitioner communities first, Hacker News last.

    One credible post in the right place outperforms a Show HN -- and a Show HN first is
    the failure mode the gate exists to prevent. The order is asserted by position, so
    reordering the list is a deliberate diff, not an edit.
    """
    text = _read(_DOC)
    positions = [text.lower().find(audience.lower()) for audience in _AUDIENCE_ORDER]
    assert all(p >= 0 for p in positions), (
        f"{_DOC}'s audience section must name all of {_AUDIENCE_ORDER} -- the rooms the "
        "announcement is actually for"
    )
    assert positions == sorted(positions), (
        f"{_DOC} must order the audience {_AUDIENCE_ORDER} -- trust-rich small rooms "
        f"before any broad launch; found positions {positions}"
    )


def test_the_plan_states_the_non_goal():
    """Stars are not the goal; the handful of builders is.

    #291's explicit non-goal is pinned so the plan cannot drift into launch-theatre: the
    point of announcing is to find the people who want an auditable Shariah screening
    engine and will help build one.
    """
    text = _unwrapped(_read(_DOC)).lower()
    assert "stars" in text, (
        f"{_DOC} must state the non-goal -- announcing is not for stars"
    )


def test_the_announcement_draft_leads_with_the_engine_and_the_honest_result():
    """A ready-to-adapt draft exists, and it says the two things the plan demands.

    The draft must lead with the compliance engine (not 'a trading bot') and carry the
    measured result in the post body itself -- plus the boundary and the no-review
    stance, the two facts any trust decision will be made on.
    """
    draft = _unwrapped(_read(_DOC))
    assert "draft" in _read(_DOC).lower(), f"{_DOC} must contain the announcement draft"
    for pin in (
        "compliance",
        "0 of 90",
        "not a fatwa engine",
        "No scholarly review",
    ):
        assert pin.lower() in draft.lower(), (
            f"the announcement draft in {_DOC} must say {pin!r} -- the post is where the "
            "honest claims live, not only the repo"
        )


def test_contributing_states_the_solo_maintainer_response_commitment():
    """The gate item with a human cost, stated where contributors read it.

    CONTRIBUTING.md must carry an honest-for-one-person commitment: triage and response
    numbers a solo maintainer can keep, pointing security reports at SECURITY.md's SLA.
    An unstated commitment defaults to the reader's most hopeful assumption, which is the
    one thing a solo maintainer cannot meet.
    """
    contributing = _unwrapped(_read("CONTRIBUTING.md"))
    assert "solo maintainer" in contributing.lower(), (
        "CONTRIBUTING.md must name the solo-maintainer reality its response times come from"
    )
    assert "SECURITY.md" in contributing, (
        "CONTRIBUTING.md's response commitment must route security reports to SECURITY.md's "
        "SLA rather than restating it (two SLAs drift apart)"
    )


def test_the_readme_maps_the_launch_document():
    """The gate is discoverable from the README's documentation map."""
    assert "docs/launch.md" in _read("README.md"), (
        "README.md's documentation map must link docs/launch.md -- the gate is findable, "
        "not filed"
    )
