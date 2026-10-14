"""The browser gate: two Tier 1 releases, their phrase ceremony, and the ungated halt.

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
eight are refused here as firmly as they are refused to cron.

**Not the whole gate either.** The typed phrase proves intent, not locality, and on its own it is
the weakest of the seven checks a release must pass. `security.gated_action_permitted` decides
whether a request may attempt an action at all -- loopback peer, loopback bind, no declared
remote origin -- and `security.gates_token` proves the caller holds this session's secret. No
endpoint serves that token (#968): the console's HALT button holds `security.halt_token`, which
opens the halt and no release, so a browser session can stop keel and cannot restart it.
`server._gated_action` routes here: the console's HALT reaches `run_halt` through
`/api/gates/halt` with the halt token, and the two releases are routed too
(`/api/gates/resume`, `/api/gates/resume-entries`) but need the gates token, so no browser can
complete one. `keel resume` and `keel resume-entries` at a terminal are the release path.

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
has the same one-click halt (`HALT`, below), shipped in the same change as the first release
route so that an operator could never start from a phone and not stop from it. Today the
asymmetry runs the safe way round: the browser can stop, and only a terminal can start.

── WHAT THE OPERATOR IS TOLD ─────────────────────────────────────────────────────────────────

`action` and `detail` are IMPORTED from `keel.commands.trading`, never restated. That module
holds this wording because "two front-ends printing one ceremony out of two copies is exactly the
drift O3 forbids -- the CLI imports and prints these; the console imports and renders these;
neither re-words them". A browser is a third front-end and the rule does not change for it. The
first version of this module paraphrased, and the drift was immediate.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from keel.commands.trading import (
    KILL_ENGAGED_LINE,
    RESUME_ACTION,
    RESUME_DETAIL,
    RESUME_DISENGAGED_LINE,
    RESUME_ENTRIES_ACTION,
    RESUME_ENTRIES_CLEARED_LINE,
    RESUME_ENTRIES_DETAIL,
    clear_consecutive_loss_halt,
    disengage_kill_switch,
    engage_kill_switch,
)
from keel.data.repository import Repository


@dataclass(frozen=True)
class Tier1Action:
    """One Tier 1 release and the sentence that authorises it (routed, but token-gated: #968)."""

    #: The OPERATION ITSELF -- the state service from `keel.commands.trading`, imported and held
    #: as a reference. Never the `click.Command` of the same name: `getattr(keel.cli, "resume")()`
    #: would enter Click standalone mode, re-parse the SERVER's `sys.argv`, run the TTY gate with
    #: no terminal and `sys.exit` inside a request thread.
    #:
    #: **A reference and not a dotted string, and that is a safety property rather than taste.**
    #: The first spelling was `"keel.commands.trading.disengage_kill_switch"` -- exactly the one
    #: form no AST scan can see. A stage 2b dispatch resolving it through
    #: `getattr(import_module(...), name)` left all three of `keel/web/__init__.py`'s scans green
    #: while releasing the kill switch, and the comment here claimed the opposite as fact (#791
    #: review). Holding the function makes the import AND the call site visible, so the effect
    #: scan sees this package reaching two operations and can require they be exactly the two it
    #: is allowed to reach.
    operation: Callable[[Repository], None]
    #: The CLI's own words for what is about to happen. Imported, never restated.
    action: str
    #: The CLI's own consequence line, shown under it.
    detail: str
    #: The exact sentence the operator types. Never derivable from the key: a phrase a script can
    #: build from the URL is evidence of nothing.
    phrase: str
    #: The line the CLI prints when this succeeds. Reported back verbatim, for the same reason
    #: `action` and `detail` are imported: one ceremony, one set of words, three front-ends.
    done: str


#: The two, and only these two (#781, #790). A read-only mapping, because the closure is the
#: safety property and a plain `dict` let any importer add `autonomy-on` in one line -- while
#: `server._gated_action` reads this table at request time.
TIER1_ACTIONS: Mapping[str, Tier1Action] = MappingProxyType(
    {
        "resume": Tier1Action(
            operation=disengage_kill_switch,
            action=RESUME_ACTION,
            detail=RESUME_DETAIL,
            phrase="RESUME TRADING",
            done=RESUME_DISENGAGED_LINE,
        ),
        "resume-entries": Tier1Action(
            operation=clear_consecutive_loss_halt,
            action=RESUME_ENTRIES_ACTION,
            detail=RESUME_ENTRIES_DETAIL,
            phrase="CLEAR STREAK HALT",
            done=RESUME_ENTRIES_CLEARED_LINE,
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
    as a 500. `security.tokens_match` guards the same class of input for the same reason, but its
    guard is WEAKER -- `not presented or not presented.isascii()`, with no `isinstance` check, so
    a non-`str` still raises there. This module repeated the ASCII half of that bug one PR after
    it was fixed (#790) and carries the stronger guard now.

    `compare_digest` because it costs one call and removes the timing question. The phrase is
    public -- a string literal in `TIER1_ACTIONS` above, in source every deployment ships -- so
    this is belt-and-braces rather than load-bearing. No page prints it: the console has no
    browser resume (only the HALT on Pulse), and the release paths an operator uses are
    `keel resume` and `keel resume-entries` at a terminal, whose ceremony is the TTY gate
    (`_require_interactive_confirmation`), not this phrase.
    """
    action = TIER1_ACTIONS.get(action_key) if isinstance(action_key, str) else None
    if action is None:
        return False
    if not isinstance(presented, str) or not presented or not presented.isascii():
        return False
    return secrets.compare_digest(presented, action.phrase)


@dataclass(frozen=True)
class UngatedAction:
    """An action with no ceremony at all, because adding one would be the wrong direction."""

    #: The operation itself, as a reference -- same reason as `Tier1Action.operation`.
    operation: Callable[[Repository], None]
    #: The CLI's own line on success.
    done: str


#: The halt. **Deliberately not a `Tier1Action`, and deliberately carrying no phrase.**
#:
#: `keel kill` is ungated -- its docstring is "Always allowed (safe action)" and it is absent
#: from `CAPABILITIES` -- because a ceremony in front of the stop makes the stop slower than the
#: start. The browser inherits that exactly: one click, no typed phrase, no modal.
#:
#: It shipped in the same change as the first release route. A release route landing first would
#: have meant an operator could start trading from a phone and not stop from it, which is the
#: inversion this tier is ordered to avoid (#790). The console's HALT reaches `run_halt` through
#: `/api/gates/halt`, carrying `security.halt_token`.
#:
#: **It outlives the remote session lifetime (#983).** On `keel serve --external-host` every
#: other request stops authenticating after `security.REMOTE_SESSION_MAX_AGE_SECONDS`; a halt
#: carrying the session cookie and the halt token does not (`server._is_a_tokened_halt`). That
#: serves a console still showing a Pulse from before the lifetime ended, whose memory holds the
#: token, or any client that holds it. A RELOADED console gets the token from `/api/pulse`'s
#: expiry refusal, which carries `{halt_token, resume_command}` to a request bearing this
#: session's cookie and to nothing else (`server._expired_pulse_halt`, #985); the shell that
#: reads it is served past the lifetime to that cookie too, so a browser with no cached console
#: reaches it (`server._is_a_sessioned_shell_read`, #1006). That refusal still names
#: `keel kill`. A leaked halt token can at worst halt, which `keel resume` at a terminal
#: undoes, and it opens no release (#968).
HALT = UngatedAction(operation=engage_kill_switch, done=KILL_ENGAGED_LINE)


def run_gated_action(cfg: Any, action_key: str, presented: object) -> str | None:
    """Perform a Tier 1 action if the typed phrase releases it, else `None`.

    **`None` is the only refusal, and it is indistinguishable between causes.** An unknown key
    and a wrong phrase come back the same way, so a caller cannot use this to enumerate which
    actions exist -- the table is public in source, but that is a decision made once
    rather than leaked by every refusal here.

    **Nothing is opened before the phrase is checked.** The database connection happens after
    `phrase_matches`, so a wrong answer costs the deployment nothing -- not a write, not a read,
    not a file handle. `tests/web/test_gates_tier1.py` points `db_path` at a path that does not
    exist and asserts the refusal still returns rather than raising, which is what pins the
    ordering rather than merely describing it.

    This is the LAST of seven checks, not the only one. `server.do_POST` runs four on every write
    -- admission (`_admitted`, whose remote session lifetime binds a release always: only a halt
    carrying its own token is exempt, `_is_a_tokened_halt`, #983), `Sec-Fetch-Site`
    (`_sec_fetch_site_ok`), the `/api/` prefix and the client header (`_api_client_header_ok`) --
    and `server._gated_action` two more for a
    release: `security.gated_action_permitted` (loopback peer, loopback bind, no declared remote
    origin) and `security.gates_token` -- never the halt token. The phrase proves intent; those
    prove locality and provenance.
    """
    action = TIER1_ACTIONS.get(action_key) if isinstance(action_key, str) else None
    if action is None or not phrase_matches(action_key, presented):
        return None
    return _perform(cfg, action.operation, action.done)


def run_halt(cfg: Any) -> str:
    """Engage the kill-switch. No phrase, no key, no refusal path -- see `HALT`."""
    return _perform(cfg, HALT.operation, HALT.done)


def _perform(cfg: Any, operation: Callable[[Repository], None], done: str) -> str:
    """Open, act, close. The one place this package writes.

    A `finally`, because every reader in `keel/web/` closes in one and a writer that leaked a
    connection would hold a write lock on the deployment database until refcounting noticed.
    """
    from keel.web.api import close_repo, open_repo_to_write

    repo = open_repo_to_write(cfg.db_path)
    try:
        operation(repo)
    finally:
        close_repo(repo)
    return done
