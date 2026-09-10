"""The browser gate: two of the nine capability-increasing actions, and what releases them.

`keel/capabilities.py` has said since it was written that "#436's browser gate would be a second
`Gate` here and a second value in `Capability.gate` -- which is the point of writing the
vocabulary down before there are two". This is the second one, and this module is its ONE
implementation, exactly as `keel.commands._common._require_interactive_confirmation` is the one
implementation of the first. A second ceremony would be a second place to get the safety model
wrong.

── WHAT THIS IS NOT ──────────────────────────────────────────────────────────────────────────

**Not a bypass, and not a seam in the TTY gate.** `_is_interactive` is untouched, has no env-var
or flag override, and the CLI path still requires a real terminal. This is a SECOND kind of
evidence for the same fact -- a human, present, who typed something a script would not. The other
seven are refused here as firmly as they are refused to cron.

**Not the whole gate either.** The typed phrase proves intent, not locality, and on its own it is
the weakest of the three checks a request must pass. `security.gated_action_permitted` decides
whether a request may attempt an action at all -- loopback peer, loopback bind, no declared
remote origin -- and `security.gates_token` proves it came from a page this session served.
Stage 2b wires all three into `server.do_POST`; **until it does, nothing in this package routes
here, and `keel/web/__init__.py`'s invariant is unchanged.** This module is a declaration.

── WHY THESE TWO ─────────────────────────────────────────────────────────────────────────────

Both release a halt an operator has reviewed, and neither is described as risk-reducing: they
increase what keel can do. What makes them Tier 1 is that the capability is bounded -- the rail
that fired can fire again on the next cycle -- and that the state they change is a flag rather
than a measurement. Neither destroys a record.

`reset-hwm` was here and was dropped to Tier 2 (#790). `reset_high_water_mark` clears the mark,
zeroes BOTH drawdown scalars and empties `equity_history` -- the rolling window the weekly peak
is computed from. That is strictly more destructive than `record-flow`, which Tier 2 refuses
because it "can, with the wrong sign, mask a real trading drawdown". Admitting the action that
ERASES the evidence while refusing the one that adjusts it was a boundary that argued against
itself, and the honest resolution was to move it rather than to reword the criterion.

`resume` is the subtler of the two, and its own entry says so: `guards.check` reads
`repo.get_state("kill_switch", default=True)`, so an unset kill-switch is ENGAGED. On a
never-resumed deployment this does not release a brake a rail applied -- it moves a fail-closed
default to permissive. It stays in Tier 1 because `keel kill` re-engages it in one gesture from
anywhere, which is the property that bounds it.

**Stopping must never be slower than starting.** `keel kill` is ungated by design -- its own
docstring says "Always allowed (safe action)" and it is absent from `CAPABILITIES`. The browser
has no halt today, so a release route that shipped before one would mean an operator could start
from a phone and not stop from it. Stage 2b ships the ungated one-click halt in the same change
as the first release route, not after it.

── WHAT THE OPERATOR IS TOLD ─────────────────────────────────────────────────────────────────

`action` and `detail` are IMPORTED from `keel.commands.trading`, never restated. That module
holds this wording because "two front-ends printing one ceremony out of two copies is exactly the
drift O3 forbids -- the CLI imports and prints these; the console imports and renders these;
neither re-words them". A browser is a third front-end and the rule does not change for it. The
first version of this module paraphrased, and the drift was immediate.
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from keel.commands.trading import (
    RESUME_ACTION,
    RESUME_DETAIL,
    RESUME_ENTRIES_ACTION,
    RESUME_ENTRIES_DETAIL,
)


@dataclass(frozen=True)
class Tier1Action:
    """One action the browser may release, and the sentence that releases it."""

    #: The OPERATION, module-qualified -- the state service in `keel.commands.trading`, never the
    #: `click.Command` of the same name. `getattr(keel.cli, "resume")()` would enter Click
    #: standalone mode, re-parse the SERVER's `sys.argv`, run the TTY gate with no terminal and
    #: `sys.exit` inside a request thread. Qualified because `resume` alone is ambiguous between
    #: the Command and its callback, and because the effect scan in `tests/web/test_server.py`
    #: reasons about exactly these names.
    operation: str
    #: The CLI's own words for what is about to happen. Imported, never restated.
    action: str
    #: The CLI's own consequence line, shown under it.
    detail: str
    #: The exact sentence the operator types. Never derivable from the key: a phrase a script can
    #: build from the URL is evidence of nothing.
    phrase: str


#: The two, and only these two (#781, #790). A read-only mapping, because the closure is the
#: safety property and a plain `dict` let any importer add `autonomy-on` in one line -- while
#: stage 2b's dispatch reads this table at request time.
TIER1_ACTIONS: Mapping[str, Tier1Action] = MappingProxyType(
    {
        "resume": Tier1Action(
            operation="keel.commands.trading.disengage_kill_switch",
            action=RESUME_ACTION,
            detail=RESUME_DETAIL,
            phrase="RESUME TRADING",
        ),
        "resume-entries": Tier1Action(
            operation="keel.commands.trading.clear_consecutive_loss_halt",
            action=RESUME_ENTRIES_ACTION,
            detail=RESUME_ENTRIES_DETAIL,
            phrase="CLEAR STREAK HALT",
        ),
    }
)


def phrase_matches(action_key: str, presented: object) -> bool:
    """Whether `presented` is EXACTLY the phrase that releases `action_key`.

    Exactly is meant literally: not case-folded, not stripped, not prefix-matched. The operator
    is asked to reproduce one specific sentence, and every loosening turns a deliberate act into
    a plausible typo. `RESUME TRADING` and `resume trading` are different answers to "prove you
    meant this", and only one is the answer that was asked for.

    **Bound to the action, which is the property that was missing.** A version testing the
    presented phrase against every row passed the whole first suite -- and made any Tier 1 phrase
    a master key for every Tier 1 action, which is the closure this module calls its safety
    property, defeated.

    Fails closed on the KEY as well: an action absent from the table has no phrase, and "no
    phrase" must never read as "no phrase required" -- which is what a lookup returning an empty
    default would quietly mean.

    **And refuses, never raises.** `secrets.compare_digest` raises `TypeError` on a non-ASCII
    `str` and on a non-`str`, and this argument comes off an operator's keyboard through a JSON
    body -- so a smart quote or a non-breaking space from a phone delivered the correct verdict
    as a 500. `tokens_match` carries the identical guard for the identical reason; this module
    repeated the bug one PR after that one was fixed (#790).

    `compare_digest` because it costs one call and removes the timing question. The phrase is
    public -- the modal prints it -- so this is belt-and-braces rather than load-bearing.
    """
    action = TIER1_ACTIONS.get(action_key) if isinstance(action_key, str) else None
    if action is None:
        return False
    if not isinstance(presented, str) or not presented or not presented.isascii():
        return False
    return secrets.compare_digest(presented, action.phrase)
