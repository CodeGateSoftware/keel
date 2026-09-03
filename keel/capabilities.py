"""Every action that increases what keel can do without asking again, and the gate covering it.

**Why this file exists.** Until now the answer to "what can this program do that needs a human?"
was a grep for `_require_interactive_confirmation`. That is a fine way to find call sites and a
poor way to audit a safety model: it tells you where the gate is called, not what is being
released, not from which front-end, and not whether something dangerous was added *without* a
gate. #436 asks for the inventory instead -- "both gates enumerable in one place, so an auditor
can see every capability-increasing action and which gate covers it".

**What it is not.** It is not the gate. `_require_interactive_confirmation` in
`keel/commands/_common.py` is still the only thing that stops anything, and nothing here is
consulted at runtime before an action proceeds. A registry that were load-bearing would be a
second place to get the safety model wrong; this one is a declaration checked against reality by
`tests/test_capabilities.py`, which fails in BOTH directions -- an undeclared gate call site fails,
and a declared entry whose gate has disappeared fails.

**One entry per call site, and since #541 there is one call site per action.** `autonomy on` used
to appear twice -- the CLI and the TUI each gated it in their own front-end -- and four of the
eleven rows were a TUI or console form of a CLI action, carrying `mirrors` so the duplication read
as deliberate. #541 deleted the TUI and the console layer that was reachable only from inside it,
and those four rows went with their call sites. `mirrors` stays on the dataclass: it describes a
shape this registry must be able to express the moment a second front-end gates anything again.

**What that leaves is worth stating plainly: every capability increase in this build is a CLI
command run by a person at a terminal.** The browser can perform none of them. That is not a
property of what the client draws -- "a client that hides a button is not a gate" -- it is a
property of the server, which implements no verb that would reach one.

**And one bypass is deliberately absent, as it always was.** `keel rules promote --force` skips
the backtest and every gate check, and it is not here, because this registry is an inventory of
GATE CALL SITES and that flag has none: `keel/commands/rules.py` records the reasoning -- "the
CLI's `--force` is a flag the operator already typed at a terminal". The console's retry flow DID
run a typed gate before calling with `force=True`, which is why a force-promote row existed at
all; it described the console's ceremony, not the CLI's flag, and it went with the console.

**The gate vocabulary is a tuple of one today.** `TTY` is the whole model: a human at a terminal,
evidenced by `sys.stdin.isatty()`, with no env-var or flag seam because any such seam would be
settable from cron. #436's browser gate would be a second `Gate` here and a second value in
`Capability.gate` -- which is the point of writing the vocabulary down before there are two.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Gate:
    """One kind of evidence that a human is present and intends this."""

    #: The identifier used by `Capability.gate`.
    name: str
    #: What the caller must produce.
    evidence: str
    #: What it refuses, stated as the thing it is actually built to refuse.
    fails_closed_against: str
    #: Where the gate itself lives -- the ONE implementation, never a copy.
    implementation: str


TTY = Gate(
    name="tty",
    evidence="a typed `yes` from a human at an interactive terminal",
    fails_closed_against=(
        "cron jobs, pipes, scripts and any other stdin that is not a terminal -- deliberately "
        "with no env-var or flag override, because such a seam would itself be settable from cron"
    ),
    implementation="keel.commands._common._require_interactive_confirmation",
)

#: Every gate kind keel knows about. #436 adds the browser gate here, not beside it.
GATES: tuple[Gate, ...] = (TTY,)


@dataclass(frozen=True)
class Capability:
    """One place a capability-increasing action is released, and what covers it."""

    #: Dotted module of the call site.
    module: str
    #: Enclosing function of the call site. `(module, function)` is the identity the pin matches.
    function: str
    #: The front-end an operator reaches this through: `cli`, `console` or `tui`.
    surface: str
    #: What the operator invokes.
    invocation: str
    #: What keel can do afterwards that it could not do before. NOT a copy of the prompt wording:
    #: the prompt says what is about to happen, this says what capability is gained, and an
    #: auditor reading the inventory needs the second.
    increases: str
    #: Which `Gate.name` covers it.
    gate: str = TTY.name
    #: For a console/TUI row, the `(module, function)` of the CLI row it mirrors -- the same
    #: action reached from a second front-end, gated by the same one implementation.
    mirrors: tuple[str, str] | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.module, self.function)


#: THE INVENTORY. Ordered by surface, then by how much each one releases.
#:
#: Every row is checked against the source by `tests/test_capabilities.py`: the set of
#: `(module, function)` here must equal the set of `_require_interactive_confirmation` call sites
#: exactly. Adding a gated action without declaring it fails; declaring one that has quietly lost
#: its gate fails.
CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        module="keel.commands.autonomy",
        function="autonomy_on_gate",
        surface="cli",
        invocation="keel autonomy on",
        increases=(
            "orders are placed with no further prompt. This is the single largest capability "
            "increase in the program: it converts every later confirm-mode prompt into an "
            "automatic yes, for the window the flag names"
        ),
    ),
    Capability(
        module="keel.cli",
        function="resume",
        surface="cli",
        invocation="keel resume",
        increases="trading resumes after the kill-switch halted it",
    ),
    Capability(
        module="keel.cli",
        function="resume_entries",
        surface="cli",
        invocation="keel resume-entries",
        increases=(
            "new entries resume after rail 16's consecutive-loss halt. Exits, sells and DCA were "
            "never affected, so this releases entries and nothing else"
        ),
    ),
    Capability(
        module="keel.cli",
        function="reset_hwm",
        surface="cli",
        invocation="keel reset-hwm",
        increases=(
            "rail 11's drawdown breaker is re-seeded against current equity, so a drawdown that "
            "was vetoing trading stops vetoing"
        ),
    ),
    Capability(
        module="keel.cli",
        function="record_flow",
        surface="cli",
        invocation="keel record-flow --amount N",
        increases=(
            "the high-water mark is rebased for a deposit or withdrawal. Gated because the same "
            "operation that stops a deposit reading as a gain can, with the wrong sign, mask a "
            "real trading drawdown -- the one direction a circuit breaker must not fail in"
        ),
    ),
    Capability(
        module="keel.commands.withdrawals",
        function="withdrawals_attest",
        surface="cli",
        invocation="keel withdrawals attest --enabled",
        increases=(
            "rail 17 stops vetoing: the operator attests that withdrawals are possible at the "
            "venue, which is what makes holding a balance there permissible rather than assumed"
        ),
    ),
    Capability(
        module="keel.commands.scope",
        function="scope_attest",
        surface="cli",
        invocation="keel scope attest --trading",
        increases=(
            "rail 20 may place a live ENTRY order against this venue's credential -- it was "
            "refused that until the credential was attested to trade"
        ),
    ),
    Capability(
        module="keel.commands.posture",
        function="posture_attest",
        surface="cli",
        invocation="keel posture attest --spot-cash",
        increases=(
            "rail 22 may place a live ENTRY order against this venue's account -- it was refused "
            "that until a human attested the account is cash-only spot. No venue exposes this "
            "field for spot (#666), so this statement is the ONLY thing that can release the "
            "rail, and nothing can check it"
        ),
    ),
    Capability(
        module="keel.commands.update",
        function="typed_update_gate",
        surface="cli",
        invocation="keel update",
        increases=(
            "the running binary is replaced. Not a trading capability, but it changes every "
            "other one at once, which is why it is gated identically"
        ),
    ),
)



def gate_named(name: str) -> Gate:
    for gate in GATES:
        if gate.name == name:
            return gate
    raise KeyError(name)


def render_lines() -> list[str]:
    """The inventory as text -- ONE renderer, so the CLI and the browser view cannot drift into
    two accounts of the same safety model."""
    lines: list[str] = []
    for gate in GATES:
        covered = [cap for cap in CAPABILITIES if cap.gate == gate.name]
        lines.append(f"gate: {gate.name} -- {gate.evidence}")
        lines.append(f"  fails closed against: {gate.fails_closed_against}")
        lines.append(f"  implementation: {gate.implementation}")
        lines.append(f"  covers {len(covered)} action(s):")
        for cap in covered:
            mirror = f"  (mirrors {cap.mirrors[1]})" if cap.mirrors else ""
            lines.append(f"    [{cap.surface}] {cap.invocation}{mirror}")
            lines.append(f"        {cap.module}.{cap.function}")
            lines.append(f"        grants: {cap.increases}")
        lines.append("")
    return lines
