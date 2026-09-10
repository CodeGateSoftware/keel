"""The browser gate: three of the nine capability-increasing actions, and what releases them.

`keel/capabilities.py` has said since it was written that "#436's browser gate would be a second
`Gate` here and a second value in `Capability.gate` -- which is the point of writing the
vocabulary down before there are two". This is the second one, and this module is its ONE
implementation, exactly as `keel.commands._common._require_interactive_confirmation` is the one
implementation of the first. A second ceremony would be a second place to get the safety model
wrong.

── WHAT THIS IS NOT ──────────────────────────────────────────────────────────────────────────

**Not a bypass, and not a seam in the TTY gate.** `_is_interactive` is untouched, has no
env-var or flag override, and the CLI path still requires a real terminal. This is a SECOND kind
of evidence for the same fact -- a human, present, who typed something a script would not. The
other six are refused here as firmly as they are refused to cron.

**Not the whole gate either.** The typed phrase is one of three checks, and on its own it is the
weakest: it proves intent, not locality. `security.gated_action_permitted` decides whether this
request may attempt an action at all (loopback peer, loopback bind, no declared remote origin),
and `security.gates_token` proves the request came from a page this session served. All three,
in `server.do_POST`, in that order.

── WHY THESE THREE, AND NOT THE OTHER SIX ────────────────────────────────────────────────────

Every one of the three RELEASES A BRAKE a rail applied, and that is deliberately not described
as "risk-reducing": they increase what keel can do. What makes them Tier 1 is that the capability
is bounded and instantly reversible -- the rail that fired can fire again, and `keel kill` undoes
all three from anywhere, with no ceremony, because stopping must never be slower than starting.

The other six grant STANDING capability or move a baseline. `autonomy on` converts every later
prompt into an automatic yes for the window it names. `record-flow` rebases the drawdown
high-water mark, which `capabilities.py` calls out as "the one direction a circuit breaker must
not fail in". The three attestations are statements about the world that nothing can check.
`update` replaces the binary. None is admitted, and the table below cannot be widened to include
one without deleting a test that says why it is absent.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass


@dataclass(frozen=True)
class Tier1Action:
    """One action the browser may release, and the sentence that releases it."""

    #: The `Capability.function` this reaches, in `keel.cli`. Named rather than imported here:
    #: the dispatch that calls it lives in `server.py`, so this module stays a declaration.
    function: str
    #: What the rail was doing, in the operator's terms -- rendered in the modal above the field.
    releases: str
    #: The exact sentence the operator types. Never the action's own key: a phrase a script can
    #: derive from the URL is evidence of nothing.
    phrase: str


#: The three, and only these three (#781). A closed table: `tests/web/test_gates_tier1.py` names
#: each Tier 2 action individually and asserts its absence, so admitting one means deleting a
#: test that states why it was out.
TIER1_ACTIONS: dict[str, Tier1Action] = {
    "resume": Tier1Action(
        function="resume",
        releases="the kill-switch halt that stopped all trading",
        phrase="RESUME TRADING",
    ),
    "resume-entries": Tier1Action(
        function="resume_entries",
        releases="rail 16's consecutive-loss halt on new entries",
        phrase="CLEAR STREAK HALT",
    ),
    "reset-hwm": Tier1Action(
        function="reset_hwm",
        releases="rail 11's drawdown veto, by re-seeding the high-water mark",
        phrase="RESET HIGH WATER MARK",
    ),
}


def phrase_matches(action_key: str, presented: str | None) -> bool:
    """Whether `presented` is EXACTLY the phrase that releases `action_key`.

    Exactly is meant literally: not case-folded, not stripped, not prefix-matched. The operator
    is asked to reproduce one specific sentence, and every loosening turns a deliberate act into
    a plausible typo. `RESUME TRADING` and `resume trading` are different answers to "prove you
    meant this", and only one of them is the answer that was asked for.

    Fails closed on the KEY as well as the phrase: an action absent from the table has no phrase,
    and "no phrase" must never read as "no phrase required" -- which is what a lookup returning
    an empty default would quietly mean.

    `compare_digest` because it costs one call and removes the timing question entirely. The
    phrase is public -- the modal prints it -- so this is belt-and-braces rather than
    load-bearing, and saying so is cheaper than leaving a reader to wonder.
    """
    action = TIER1_ACTIONS.get(action_key)
    if action is None or not presented:
        return False
    return secrets.compare_digest(presented, action.phrase)
