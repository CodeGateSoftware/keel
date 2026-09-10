"""The browser gate: the second kind of evidence keel accepts that a human is present (#781).

`keel/capabilities.py` has said since it was written that "#436's browser gate would be a second
`Gate` here and a second value in `Capability.gate` -- which is the point of writing the
vocabulary down before there are two". This is that second one.

**It is a gate, not a bypass.** `_require_interactive_confirmation` is untouched and the CLI path
still needs a real TTY. This admits two of the nine capability-increasing actions to the browser
behind DIFFERENT evidence of the same fact -- a human, present, who typed something only a human
would type -- and refuses everything else exactly as before.

── WHY EVERY TEST BELOW ASSERTS A PAIRING ────────────────────────────────────────────────────

The first version of this file passed 9 of 20 mutations (#790). The worst survivor was a
`phrase_matches` that ignored its `action_key` entirely: every test read the expected phrase back
out of the table, so the suite was self-consistent under any table content and could not see that
`RESUME TRADING` had become a master key for every action. Nothing bound a phrase to the action
it releases, nothing pinned the phrase literals, and nothing at all covered `operation` -- so
pointing two entries at one operation, or at a Tier 2 operation, shipped green.

Six of the twelve tests were also vacuous on an empty table. Each loop and count below now
asserts its own population first.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from keel.capabilities import CAPABILITIES
from keel.web import gates

#: Every gated action, by the key `TIER1_ACTIONS` would use. Derived from the inventory rather
#: than typed here, so a capability added to `keel/capabilities.py` is one this file already
#: knows about and must classify.
_ALL_ACTION_KEYS = frozenset(cap.function.replace("_", "-") for cap in CAPABILITIES)


def test_tier_one_is_exactly_the_two_halt_releases() -> None:
    """**A closed table, and the closure is the safety property.**

    `reset-hwm` was here and was dropped to Tier 2 (#790): it clears the high-water mark, zeroes
    BOTH drawdown scalars and empties `equity_history`, which is strictly more destructive than
    `record-flow` -- an action Tier 2 refuses on the grounds that it "can, with the wrong sign,
    mask a real trading drawdown". Admitting the one that erases the evidence while refusing the
    one that adjusts it was a boundary that argued against itself."""
    assert set(gates.TIER1_ACTIONS) == {"resume", "resume-entries"}


def test_every_other_gated_action_is_absent_by_name() -> None:
    """All seven, named individually rather than as a set difference, so admitting one means
    deleting the line that says why it was out. Two were missing from the first version --
    `scope-attest` and `posture-attest` -- so those two could have been added for free."""
    for key in (
        "autonomy-on",
        "record-flow",
        "reset-hwm",
        "withdrawals-attest",
        "scope-attest",
        "posture-attest",
        "update",
    ):
        assert key not in gates.TIER1_ACTIONS, key


def test_the_table_names_no_action_the_inventory_does_not_have() -> None:
    """Guards the other direction: a key here that matches no capability would be an action this
    gate believes in and `keel capabilities` has never heard of."""
    assert gates.TIER1_ACTIONS, "an empty table would make this vacuous"
    assert set(gates.TIER1_ACTIONS) <= _ALL_ACTION_KEYS, sorted(
        set(gates.TIER1_ACTIONS) - _ALL_ACTION_KEYS
    )


def test_the_table_cannot_be_widened_at_runtime() -> None:
    """**The closure is not a convention.** A plain `dict` let any importer add `autonomy-on` in
    one line, and stage 2b's dispatch reads this table at request time."""
    with pytest.raises(TypeError):
        gates.TIER1_ACTIONS["autonomy-on"] = next(iter(gates.TIER1_ACTIONS.values()))  # type: ignore[index]


def test_an_action_is_frozen_once_declared() -> None:
    """Same argument one level down: a mutable row would let the phrase or the operation be
    rewritten after import."""
    action = gates.TIER1_ACTIONS["resume"]
    with pytest.raises(Exception):
        action.phrase = "ANYTHING"  # type: ignore[misc]


# -- the phrase, and what it is bound to -------------------------------------------------------


#: The literals, pinned. Read out of the table, every assertion below is self-consistent under
#: any content -- which is how `"RESUME TRADING"` -> `"RESUME NOW"` shipped green.
_EXPECTED_PHRASES = {"resume": "RESUME TRADING", "resume-entries": "CLEAR STREAK HALT"}


def test_the_phrases_are_the_ones_the_modal_will_print() -> None:
    assert {key: action.phrase for key, action in gates.TIER1_ACTIONS.items()} == _EXPECTED_PHRASES


def test_a_phrase_releases_ONLY_its_own_action() -> None:
    """**The pairing, which nothing checked.**

    A `phrase_matches` ignoring `action_key` and testing the presented phrase against every row
    passed the entire first suite -- making any Tier 1 phrase a master key, and defeating the
    closure this module calls its safety property. Asserted as a cross product: every phrase
    against every OTHER action."""
    assert len(gates.TIER1_ACTIONS) >= 2, "a cross product needs two rows to mean anything"

    for key, action in gates.TIER1_ACTIONS.items():
        assert gates.phrase_matches(key, action.phrase) is True, key
        for other in gates.TIER1_ACTIONS:
            if other != key:
                assert gates.phrase_matches(other, action.phrase) is False, (
                    f"{action.phrase!r} released {other}, which it does not name"
                )


@pytest.mark.parametrize("key", ["resume", "resume-entries"])
def test_the_typed_phrase_must_match_exactly(key: str) -> None:
    """**Exactly**, meant literally: not case-folded, not stripped, not prefix-matched. The
    operator reproduces one specific sentence, and every loosening turns a deliberate act into a
    plausible typo."""
    phrase = gates.TIER1_ACTIONS[key].phrase

    assert gates.phrase_matches(key, phrase) is True
    for wrong in (phrase.lower(), phrase + " ", " " + phrase, phrase[:-1], phrase.replace(" ", "")):
        assert gates.phrase_matches(key, wrong) is False, repr(wrong)


def test_no_two_actions_share_a_phrase_or_a_first_word() -> None:
    """A shared phrase would prove a human but not INTENT. A shared FIRST WORD is the near miss:
    an operator pattern-matching under stress reads the first token and types from memory, so
    `RESUME TRADING` and a future `RESUME ENTRIES` would be genuinely confusable."""
    actions = list(gates.TIER1_ACTIONS.values())
    assert len(actions) >= 2, "nothing to compare"

    phrases = [action.phrase for action in actions]
    assert len(set(phrases)) == len(phrases), phrases
    firsts = [phrase.split()[0] for phrase in phrases]
    assert len(set(firsts)) == len(firsts), f"two phrases begin with the same word: {firsts}"


def test_a_phrase_is_never_derivable_from_the_key() -> None:
    """A phrase a script can build from the URL is evidence of nothing."""
    assert gates.TIER1_ACTIONS
    for key, action in gates.TIER1_ACTIONS.items():
        assert action.phrase.lower().replace(" ", "-") != key
        assert action.phrase != key
        assert len(action.phrase.split()) >= 2


def test_an_unknown_action_never_matches() -> None:
    """Fails closed on the KEY as well as the phrase: an action not in the table has no phrase,
    and "no phrase" must not read as "no phrase required"."""
    assert gates.phrase_matches("autonomy-on", "RESUME TRADING") is False
    assert gates.phrase_matches("reset-hwm", "RESET HIGH WATER MARK") is False
    assert gates.phrase_matches("", "") is False
    assert gates.phrase_matches(None, "RESUME TRADING") is False  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "presented", ["RESUME TRADINGé", "RESUME TRADING", 5, ["RESUME TRADING"], True, None, b"x"]
)
def test_an_unusable_phrase_is_refused_rather_than_raising(presented: object) -> None:
    """**The identical bug fixed in `tokens_match` one PR earlier, repeated here (#790).**

    `secrets.compare_digest` raises `TypeError` on a non-ASCII `str` and on a non-`str`. The
    phrase arrives from an operator's keyboard through a JSON body, so a smart quote or a
    non-breaking space from a phone reaches it -- and the correct verdict, refuse, was being
    delivered as a 500. The honest operator hits this before any attacker does."""
    assert gates.phrase_matches("resume", presented) is False  # type: ignore[arg-type]


def test_the_phrase_comparison_returns_a_constant_time_call() -> None:
    """**Not observable from behaviour, so asserted structurally -- and precisely.**

    `==` and `compare_digest` return the same values for every input; no black-box test can tell
    them apart, and one that claimed to would be theatre. The first version grepped the source
    for `compare_digest`, which a mutant defeated by replacing the call and leaving the word in a
    trailing comment.

    So this reads the AST: the value `phrase_matches` RETURNS must be a call to
    `secrets.compare_digest`. A comment cannot satisfy that, and neither can a mention elsewhere
    in the body."""
    import ast

    source = Path(gates.__file__).read_text(encoding="utf-8")
    function = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == "phrase_matches"
    )
    # SORTED BY LINE. `ast.walk` is breadth-first, so `[-1]` is not the last return in the
    # source -- an assumption that made the first version of this test pass by luck and then
    # fail the moment an import edit shifted the line numbers.
    returns = sorted(
        (node for node in ast.walk(function) if isinstance(node, ast.Return)),
        key=lambda node: node.lineno,
    )
    assert len(returns) >= 2, "the guards and the success path are all returns"

    guards = [node for node in returns[:-1]]
    assert all(isinstance(node.value, ast.Constant) for node in guards), (
        "every early return is a refusal constant: " + repr([ast.unparse(n) for n in guards])
    )

    final = returns[-1].value
    assert isinstance(final, ast.Call), ast.unparse(returns[-1])
    assert ast.unparse(final.func) == "secrets.compare_digest", ast.unparse(final)


# -- what each action reaches, and what it tells the operator ----------------------------------


#: The OPERATION each Tier 1 action performs -- the state service, never the Click command.
#: Pinned as literals because this is the field stage 2b dispatches on.
_EXPECTED_OPERATIONS = {
    "resume": "keel.commands.trading.disengage_kill_switch",
    "resume-entries": "keel.commands.trading.clear_consecutive_loss_halt",
}


def test_each_action_names_the_operation_and_not_the_click_command() -> None:
    """**`keel.cli.resume` is a `click.Command`, and calling it would be a disaster (#790).**

    `getattr(keel.cli, "resume")()` enters Click standalone mode, re-parses the SERVER process's
    `sys.argv`, calls `_require_interactive_confirmation` with no TTY, and `sys.exit`s inside a
    request thread. The field names the state service in `keel/commands/trading.py`, which that
    module's docstring designates as the one home for these mutations.

    Module-qualified, because `resume` alone is ambiguous between the Command and its callback --
    and because `tests/web/test_server.py`'s effect scan reasons about exactly these names."""
    assert {key: action.operation for key, action in gates.TIER1_ACTIONS.items()} == (
        _EXPECTED_OPERATIONS
    )


def test_no_two_actions_reach_the_same_operation() -> None:
    """A dispatch collision would release the kill-switch when the operator asked to clear a
    streak halt -- the ceremony correct, the effect wrong."""
    operations = [action.operation for action in gates.TIER1_ACTIONS.values()]
    assert len(operations) >= 2
    assert len(set(operations)) == len(operations), operations


def test_every_operation_exists_and_is_callable() -> None:
    """A name that resolves to nothing would be a table that passes every test here and fails at
    the first request."""
    import importlib

    assert gates.TIER1_ACTIONS
    for action in gates.TIER1_ACTIONS.values():
        module_name, _, function = action.operation.rpartition(".")
        module = importlib.import_module(module_name)
        assert callable(getattr(module, function, None)), action.operation


def test_the_operator_sees_the_cli_s_own_words_and_not_a_paraphrase() -> None:
    """**`keel/commands/trading.py` holds this wording so that "neither re-words them".**

    The first version paraphrased, and the drift was immediate: for `reset-hwm` the CLI printed
    "Any real, unrecovered drawdown stops being visible to the rail" while the browser was to
    show "re-seeding the high-water mark" -- the destructive consequence gone. Two front-ends
    printing one ceremony out of two copies is the drift that module forbids."""
    from keel.commands import trading

    assert gates.TIER1_ACTIONS
    for action in gates.TIER1_ACTIONS.values():
        assert action.action in vars(trading).values(), action.action
        assert action.detail in vars(trading).values(), action.detail
