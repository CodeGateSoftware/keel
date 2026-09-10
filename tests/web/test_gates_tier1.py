"""The browser gate: the second kind of evidence keel accepts that a human is present (#781).

`keel/capabilities.py` has said since it was written that "#436's browser gate would be a second
`Gate` here and a second value in `Capability.gate` -- which is the point of writing the
vocabulary down before there are two". This is that second one.

**It is a gate, not a bypass.** `_require_interactive_confirmation` is untouched and the CLI path
still needs a real TTY. This admits three of the nine capability-increasing actions to the
browser behind DIFFERENT evidence of the same fact -- a human, present, who typed something only
a human would type -- and refuses everything else exactly as before.
"""

from __future__ import annotations

import pytest

from keel.web import gates


def test_only_the_three_tier_one_actions_exist() -> None:
    """**A closed table, and the closure is the safety property.**

    Six of the nine stay CLI-only: `autonomy on`, `record-flow`, the three attestations and
    `update`. They are not "not implemented yet" -- they are refused, and a table that could grow
    one by an edit nobody reviewed is the failure this shape exists to prevent."""
    assert set(gates.TIER1_ACTIONS) == {"resume", "resume-entries", "reset-hwm"}


@pytest.mark.parametrize("key", ["autonomy-on", "record-flow", "withdrawals-attest", "update"])
def test_a_tier_two_action_is_not_in_the_table(key: str) -> None:
    """Named individually rather than asserted as a set difference, so a future edit that adds
    one has to delete a test that says why it is absent."""
    assert key not in gates.TIER1_ACTIONS


def test_every_action_carries_a_phrase_that_is_not_its_own_name() -> None:
    """The phrase is the evidence. `resume` as the phrase for `resume` would be satisfied by
    anything that echoed the URL back -- which a script does for free, and which is exactly the
    population the gate exists to refuse."""
    for key, action in gates.TIER1_ACTIONS.items():
        assert action.phrase, key
        assert action.phrase != key
        assert action.phrase == action.phrase.upper(), "the phrase is typed as shown"
        assert len(action.phrase.split()) >= 2, "one word is a word a script guesses"


def test_no_two_actions_share_a_phrase() -> None:
    """A shared phrase would let an operator who meant one action authorise another -- the
    confirmation would be evidence of a human, but not of INTENT."""
    phrases = [action.phrase for action in gates.TIER1_ACTIONS.values()]
    assert len(set(phrases)) == len(phrases), phrases


@pytest.mark.parametrize("key", ["resume", "resume-entries", "reset-hwm"])
def test_the_typed_phrase_must_match_exactly(key: str) -> None:
    """**Exactly**, and this is the one place in the web layer where that word is meant
    literally. Not case-folded, not stripped of inner spacing, not prefix-matched: the operator
    is asked to reproduce a specific sentence, and anything looser turns a deliberate act into a
    plausible typo."""
    phrase = gates.TIER1_ACTIONS[key].phrase

    assert gates.phrase_matches(key, phrase) is True
    for wrong in (
        phrase.lower(),
        phrase + " ",
        " " + phrase,
        phrase[:-1],
        phrase.replace(" ", ""),
        "",
    ):
        assert gates.phrase_matches(key, wrong) is False, repr(wrong)


def test_an_unknown_action_never_matches() -> None:
    """Fails closed on the key as well as on the phrase: an action not in the table has no
    phrase, and "no phrase" must not read as "no phrase required"."""
    assert gates.phrase_matches("autonomy-on", "ARM AUTONOMY") is False
    assert gates.phrase_matches("", "") is False


def test_the_phrase_comparison_is_not_a_timing_oracle() -> None:
    """The phrase is public -- it is printed in the modal -- so this is belt-and-braces rather
    than load-bearing. It costs one function call and removes the question."""
    import inspect

    assert "compare_digest" in inspect.getsource(gates.phrase_matches)
