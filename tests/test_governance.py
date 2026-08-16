"""The governance boundary: keel enforces rulings, it does not issue them.

Contributors to an open-source Shariah-compliance engine arrive from different madhāhib and
jurisdictions, and they will disagree about rulings -- what counts as riba exposure, whether a
staking wrapper is permissible, how a governance token is classified. In a religious context an
ungoverned disagreement is more corrosive than a technical one: left ambiguous, it fragments the
project into forks that each carry a blessing the others do not recognise.

The architecture already answers it. A Shariah classification is a human input (`keel assets
attest`, with `--source` and `--attested-by`); `screen_asset` enforces what was recorded and
never derives a ruling from price data; an absent attestation is a rejection, not a default
pass. The ruling lives in the attestation, not in the code -- so the same code serves a Hanafi
operator and a Shafi'i operator without the project adjudicating between them.

None of that is visible to someone reading the repo, and #280's judgement is that every document
written before it is stated explicitly would have to be rewritten afterwards. These tests pin
the statement where the issue asks for it: the README's first screen (where a stranger decides
what this project is), and `CONTRIBUTING.md` (where a would-be contributor decides what a PR may
change). The sentence is pinned VERBATIM in both places so the two cannot drift into saying
slightly different things -- a boundary that varies by where you read it is not a boundary.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

#: The one-sentence boundary, quoted exactly. It is the project's answer to "whose fiqh is
#: this?" and every doc that states it must state it the same way.
_BOUNDARY = "keel is not a fatwa engine. It is an enforcement engine for a ruling you supply."


def _readme() -> str:
    return (_ROOT / "README.md").read_text()


def _readme_first_screen() -> str:
    """Everything before the first `##` section: what a reader sees before scrolling."""
    return _readme().split("\n## ", 1)[0]


def _contributing() -> str:
    return (_ROOT / "CONTRIBUTING.md").read_text()


def test_the_readme_first_screen_states_the_boundary():
    """The sentence must be on the README's first screen, not three sections down.

    A stranger deciding whether keel is for them is asking exactly one governance question --
    whose ruling does this enforce? -- and an answer below the fold is an answer they never see.
    """
    assert _BOUNDARY in _readme_first_screen(), (
        "the governance boundary sentence must appear in the README's first screen (before the "
        "first `##` section): a reader should not have to scroll to learn whose fiqh keel enforces"
    )


def test_contributing_states_the_same_boundary_verbatim():
    """CONTRIBUTING.md carries the identical sentence, framed as PR scope.

    Verbatim on purpose: paraphrases drift, and a boundary stated two ways invites the question
    of which one governs.
    """
    assert _BOUNDARY in _contributing(), (
        "CONTRIBUTING.md must carry the boundary sentence verbatim; paraphrasing it leaves the "
        "two documents free to drift apart"
    )


def test_default_classification_changes_need_a_source_mechanism_changes_do_not():
    """The two kinds of PR must be told apart in CONTRIBUTING.md, with different bars.

    A PR that changes a DEFAULT classification is a ruling arriving in code's clothing: it
    silently applies someone's fiqh to every operator who upgrades, so it needs a cited source
    and discussion. A PR that changes the MECHANISM -- how an attestation is recorded, checked,
    or audited -- is ordinary engineering and needs only ordinary review. Contributors who are
    not told which is which will file the second as the first, or worse, the first as the
    second.
    """
    text = _contributing()
    assert "classification" in text and "source" in text, (
        "CONTRIBUTING.md must say that a PR changing a default classification requires a source"
    )
    assert "mechanism" in text, (
        "CONTRIBUTING.md must say that a PR changing the mechanism is ordinary engineering"
    )


def test_disagreement_has_a_route_that_is_not_upstream_adjudication():
    """A stated local route for disagreement, so the project never has to adjudicate fiqh.

    The route the architecture already provides: an operator who reads a classification
    differently attests THEIR ruling locally (`keel assets attest`, in their own database) rather
    than merging it upstream. Without that sentence, the only path a disagreeing contributor
    sees is a pull request -- and the project becomes a fiqh court with a merge button.
    """
    text = _contributing()
    assert "keel assets attest" in text, (
        "CONTRIBUTING.md must name the local route (`keel assets attest`) for a disagreeing "
        "operator"
    )
    assert "upstream" in text, (
        "CONTRIBUTING.md must say the disagreement is attested locally, not merged upstream"
    )
