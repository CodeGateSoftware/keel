"""CODE_OF_CONDUCT.md: a behavioural baseline, before an international community arrives.

A project inviting international contributors around a religious topic needs a stated
baseline before it needs anything else (#284): the moment disagreement about a ruling can
read as disrespect about someone's practice, the project is one heated thread away from
losing a contributor it will not get back.

Two failure shapes these tests guard:

- **A homemade conduct policy.** The Contributor Covenant is the sane default precisely
  because it is battle-tested language with known edge cases already litigated; a rewrite
  starts from zero and reads as whatever its author already believed. The canonical section
  headings are pinned so the file stays recognisably the Covenant.
- **An unfilled template.** A `[INSERT CONTACT METHOD]` left in place gives an enforcement
  section that reaches nobody -- the same shape as an unfilled licence appendix, trusted
  and dead. The contact must be present and unfilled simultaneously impossible.

And one project-specific addition the Covenant cannot supply: a stance on religious
disagreement itself, which here is expected and legitimate while disparagement is not --
with the local-attestation route named as where technical disagreement belongs.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

#: Section headings of the canonical Contributor Covenant; their presence is what makes the
#: file the Covenant rather than a bespoke policy with the same filename.
_COVENANT_MARKERS = (
    "# Contributor Covenant Code of Conduct",
    "## Our Pledge",
    "## Our Standards",
    "## Enforcement",
    "## Attribution",
)


def _coc() -> str:
    path = _ROOT / "CODE_OF_CONDUCT.md"
    assert path.is_file(), (
        "CODE_OF_CONDUCT.md does not exist at the repo root -- the project invites "
        "international contributors around a religious topic with no behavioural baseline (#284)"
    )
    return path.read_text()


def test_the_file_is_the_contributor_covenant_not_a_homemade_policy():
    """Every canonical section heading present: battle-tested text, not a first draft."""
    text = _coc()
    missing = [marker for marker in _COVENANT_MARKERS if marker not in text]
    assert not missing, (
        f"CODE_OF_CONDUCT.md is missing the Covenant's own section headings: {missing}. "
        "The point of adopting the Covenant is not to write one from scratch"
    )


def test_no_placeholder_is_left_unfilled():
    """An unfilled template is a conduct policy that reaches nobody.

    GitHub's canonical text ships with `[INSERT CONTACT METHOD]`; leaving it in is the
    classic mistake, and it is invisible -- the section reads as configured while the
    enforcement channel is a blank.
    """
    assert "[INSERT" not in _coc(), (
        "CODE_OF_CONDUCT.md still carries an unfilled placeholder -- fill in the contact "
        "method so the enforcement section reaches a human"
    )


def test_an_enforcement_contact_that_reaches_someone_is_named():
    """A concrete, reachable contact; 'the maintainers' is not an address."""
    import re

    assert re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", _coc()), (
        "CODE_OF_CONDUCT.md must name a concrete enforcement contact (an address, not a role)"
    )


def test_religious_disagreement_has_a_stance():
    """The project-specific addition: disagreement is legitimate, disparagement is not.

    The Covenant's generic religion clause cannot carry this project's specific risk:
    contributors WILL disagree about rulings, and without a stated stance the only
    interpretations available are 'disagreement is disloyalty' or 'anything goes'. The
    boundary must be explicit -- a ruling may be argued about, a madhhab or a contributor's
    practice may not be disparaged -- and the technical route for the disagreement (a local
    attestation, not a thread) must be named so the stance routes people somewhere.
    """
    text = _coc().lower()
    assert "madhhab" in text, (
        "the code of conduct must name the line: disparaging a madhhab or school is not "
        "acceptable behaviour"
    )
    assert "expected" in text and "legitimate" in text, (
        "disagreement about a religious ruling must be stated as expected and legitimate"
    )
    assert "attestation" in text, (
        "the stance must route technical disagreement to the local-attestation route, not "
        "leave it as an argument in a thread"
    )
