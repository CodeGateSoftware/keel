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
from keel.commands import trading
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

    # And no BACKING STORE is reachable. A proxy over a module-level `_TIER1_RAW` passes the
    # assertion above while `gates._TIER1_RAW["autonomy-on"] = ...` is the one-liner the proxy
    # exists to prevent -- and stage 2b's dispatch reads the proxy over it (#791 review).
    mutable = [
        name
        for name, value in vars(gates).items()
        if isinstance(value, dict)
        and value
        and all(isinstance(v, gates.Tier1Action) for v in value.values())
    ]
    assert not mutable, f"a mutable copy of the table is reachable as gates.{mutable}"


def test_every_field_of_every_row_is_frozen() -> None:
    """Same argument one level down: a mutable row lets the phrase OR the operation be rewritten
    after import.

    Every row and every field, because a `__setattr__` guarding only `phrase` passed when this
    checked one field of one row -- leaving `operation`, `action` and `detail` rewritable. And
    `FrozenInstanceError` by name, because `pytest.raises(Exception)` cannot tell the intended
    refusal from a typo in the test (#791 review)."""
    import dataclasses

    assert gates.TIER1_ACTIONS
    for key, action in gates.TIER1_ACTIONS.items():
        for field in dataclasses.fields(action):
            before = getattr(action, field.name)
            with pytest.raises(dataclasses.FrozenInstanceError):
                setattr(action, field.name, "ANYTHING")
            assert getattr(action, field.name) is before, f"{key}.{field.name}"


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

    # And phrases that are not near misses at all. Every negative above is a MUTATION of the
    # right answer, so `if presented == "OPEN SESAME": return True` passed all 24 tests -- a
    # hardcoded master phrase, invisible to a suite that only ever tries typos (#791 review).
    for unrelated in ("OPEN SESAME", "yes", "y", "ARM AUTONOMY", "true", "1", key, "*"):
        assert gates.phrase_matches(key, unrelated) is False, repr(unrelated)


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

    # The KEY refuses and never raises, on the inputs the phrase is tested with. Dropping the
    # `isinstance` guard passed the whole suite, and a list key then raised `TypeError:
    # unhashable` -- the same 500-instead-of-refusal bug, on the argument nothing covered.
    for bad in (5, ["resume"], {"resume": 1}, True, b"resume"):
        assert gates.phrase_matches(bad, "RESUME TRADING") is False, repr(bad)  # type: ignore[arg-type]

    # And it is not normalised: looked up as given, or not at all.
    for near in ("RESUME", " resume ", "resume\n", "Resume"):
        assert gates.phrase_matches(near, "RESUME TRADING") is False, repr(near)


@pytest.mark.parametrize(
    "presented",
    [
        "RESUME TRADING\u00e9",
        "RESUME\u00a0TRADING",  # a non-breaking space -- SPELLED; typed it is invisible
        b"RESUME TRADING",  # the correct bytes: `b"x"` alone is refused for its VALUE
        5,
        ["RESUME TRADING"],
        True,
        None,
        b"x",
    ],
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

    # The ARGUMENTS, because the spelling alone is theatre. `compare_digest('y' if presented ==
    # action.phrase else 'n', 'y')` satisfies everything above while the real comparison is a
    # plain `==` over a one-byte constant (#791 review).
    assert [ast.unparse(arg) for arg in final.args] == ["presented", "action.phrase"], ast.unparse(
        final
    )

    # And the NAME is the stdlib module, not a shim. `secrets = SimpleNamespace(compare_digest=
    # lambda a, b: a == b)` also satisfies the spelling.
    import secrets as stdlib_secrets

    assert gates.secrets is stdlib_secrets


def test_the_ceremony_wording_is_imported_and_never_restated() -> None:
    """**The rule this module states three times and enforced nowhere (#791 review).**

    `keel/commands/trading.py` holds the wording so that "the CLI imports and prints these; the
    console imports and renders these; neither re-words them". Re-declaring the four constants as
    literals in `gates.py` passed the whole suite -- the value comparisons all still held, on the
    day the copy was made, which is exactly when a copy is indistinguishable from the original.

    Asserted over the AST, so the mechanism is pinned and not just today's values."""
    import ast

    tree = ast.parse(Path(gates.__file__).read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "keel.commands.trading"
        for alias in node.names
    }

    assert {
        "RESUME_ACTION",
        "RESUME_DETAIL",
        "RESUME_ENTRIES_ACTION",
        "RESUME_ENTRIES_DETAIL",
    } <= imported, sorted(imported)


# -- what each action reaches, and what it tells the operator ----------------------------------


#: The OPERATION each Tier 1 action performs -- the state service, never the Click command.
#: Pinned as literals because this is the field stage 2b dispatches on.
_EXPECTED_OPERATIONS = {
    "resume": trading.disengage_kill_switch,
    "resume-entries": trading.clear_consecutive_loss_halt,
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
    # IDENTITY: the row holds the very function the state service exports, so a look-alike
    # defined elsewhere cannot satisfy it.
    for key, expected in _EXPECTED_OPERATIONS.items():
        assert gates.TIER1_ACTIONS[key].operation is expected, key


def test_no_two_actions_reach_the_same_operation() -> None:
    """A dispatch collision would release the kill-switch when the operator asked to clear a
    streak halt -- the ceremony correct, the effect wrong."""
    operations = [action.operation for action in gates.TIER1_ACTIONS.values()]
    assert len(operations) >= 2
    assert len(set(operations)) == len(operations), operations


def test_every_operation_is_a_function_of_the_state_service() -> None:
    """Not a `click.Command`, not a class, and defined in `keel.commands.trading` -- the module
    its own docstring designates as the one home for these mutations."""
    import inspect

    assert gates.TIER1_ACTIONS
    for key, action in gates.TIER1_ACTIONS.items():
        assert inspect.isfunction(action.operation), f"{key}: {action.operation!r}"
        assert action.operation.__module__ == "keel.commands.trading", key


def test_the_operator_sees_the_cli_s_own_words_and_not_a_paraphrase() -> None:
    """**`keel/commands/trading.py` holds this wording so that "neither re-words them".**

    The first version paraphrased, and the drift was immediate: for `reset-hwm` the CLI printed
    "Any real, unrecovered drawdown stops being visible to the rail" while the browser was to
    show "re-seeding the high-water mark" -- the destructive consequence gone. Two front-ends
    printing one ceremony out of two copies is the drift that module forbids."""
    expected = {
        "resume": (trading.RESUME_ACTION, trading.RESUME_DETAIL),
        "resume-entries": (trading.RESUME_ENTRIES_ACTION, trading.RESUME_ENTRIES_DETAIL),
    }

    assert set(gates.TIER1_ACTIONS) == set(expected), "a row here has no expected wording"
    for key, action in gates.TIER1_ACTIONS.items():
        # IDENTITY, per row. `in vars(trading).values()` accepted any module attribute, so
        # `action=trading.__name__` passed -- the modal would read "You are about to
        # keel.commands.trading" -- and swapping the copy between rows passed too, which shows
        # the operator rail 16's ceremony while disengaging the kill switch (#791 review).
        assert action.action is expected[key][0], key
        assert action.detail is expected[key][1], key


# -- the dispatch, and the halt beside it (stage 2b, #781) ---------------------------------------


def test_the_halt_is_declared_ungated_and_carries_no_phrase() -> None:
    """**Stopping must never be slower than starting.**

    `keel kill` is ungated by design -- its docstring is "Always allowed (safe action)" and it is
    absent from `CAPABILITIES`, because a ceremony in front of the stop makes the stop slower
    than the start. The browser's halt inherits that: one click, no phrase, no confirmation.

    It ships in the SAME change as the first release route. A release route landing first would
    mean an operator could start from a phone and not stop from it, which is the inversion this
    whole tier is ordered to avoid."""
    from keel.commands import trading

    assert gates.HALT.operation is trading.engage_kill_switch
    assert not hasattr(gates.HALT, "phrase"), "a phrase in front of the stop is the wrong shape"
    assert gates.HALT.done is trading.KILL_ENGAGED_LINE


def test_the_halt_is_not_in_the_tier_one_table() -> None:
    """It is not a gated action and must not be reachable through the gated dispatch: a caller
    naming `halt` there would be asking for a phrase check on something that has none."""
    assert "halt" not in gates.TIER1_ACTIONS
    assert gates.run_gated_action(_cfg_stub(), "halt", "") is None


def _cfg_stub() -> object:
    """A config whose `db_path` no dispatch should ever reach -- every test below that uses it
    expects a refusal BEFORE the database is opened."""

    class _Cfg:
        db_path = "/nonexistent/keel.db"

    return _Cfg()


@pytest.mark.parametrize("key", ["resume", "resume-entries"])
def test_a_gated_action_refuses_a_wrong_phrase_without_opening_the_database(key: str) -> None:
    """**The refusal comes first, and that ordering is the test.**

    `db_path` points at nothing. If the dispatch opened the repository before checking the
    phrase, this would raise rather than return `None` -- so this pins that a wrong phrase costs
    the deployment nothing at all, not even a connection."""
    assert gates.run_gated_action(_cfg_stub(), key, "NOPE") is None
    assert gates.run_gated_action(_cfg_stub(), key, "") is None
    assert gates.run_gated_action(_cfg_stub(), key, None) is None


def test_an_unknown_action_is_refused_before_anything_else() -> None:
    """Same ordering, on the key. `autonomy-on` is a real capability and not a Tier 1 one; it
    must not reach a database either."""
    for key in ("autonomy-on", "reset-hwm", "", "../resume", "RESUME"):
        assert gates.run_gated_action(_cfg_stub(), key, "RESUME TRADING") is None, key


def test_a_correct_phrase_performs_the_operation_and_reports_the_cli_s_line(tmp_path) -> None:
    """The whole path, against a real database: the phrase matches, the operation runs, and what
    comes back is the line the CLI prints for it -- not a sentence this layer invented."""
    from keel.commands import trading
    from keel.data.db import connect, migrate
    from keel.data.repository import Repository

    db_path = tmp_path / "keel.db"
    conn = connect(str(db_path))
    migrate(conn)
    Repository(conn).set_state("kill_switch", True)
    conn.close()

    class _Cfg:
        pass

    cfg = _Cfg()
    cfg.db_path = str(db_path)  # type: ignore[attr-defined]

    assert gates.run_gated_action(cfg, "resume", "RESUME TRADING") == trading.RESUME_DISENGAGED_LINE

    conn = connect(str(db_path))
    try:
        assert Repository(conn).get_state("kill_switch", default=True) is False
    finally:
        conn.close()


def test_the_halt_engages_the_kill_switch(tmp_path) -> None:
    """And the halt, which takes no phrase: one call, and the rail's fail-closed default is back
    in force."""
    from keel.commands import trading
    from keel.data.db import connect, migrate
    from keel.data.repository import Repository

    db_path = tmp_path / "keel.db"
    conn = connect(str(db_path))
    migrate(conn)
    Repository(conn).set_state("kill_switch", False)
    conn.close()

    class _Cfg:
        pass

    cfg = _Cfg()
    cfg.db_path = str(db_path)  # type: ignore[attr-defined]

    assert gates.run_halt(cfg) == trading.KILL_ENGAGED_LINE

    conn = connect(str(db_path))
    try:
        assert Repository(conn).get_state("kill_switch", default=True) is True
    finally:
        conn.close()
