"""Every `doctor` fix line must name a command that can produce the state it promises.

`doctor` is what an operator runs when something is already wrong, and its `fix` field is the
one line they will act on without checking. A fix that names the wrong command does not merely
fail to help — it spends the operator's trust and their time, and on this codebase it can spend
a typed confirmation for a dangerous capability too.

That is not hypothetical (#693). `rail.kill_switch` told the operator to run `keel autonomy on`,
which cannot clear the kill switch and says so in its own docstring. Following it meant typing
`yes` to unattended order placement and remaining halted, with nothing indicating the two were
different gates.
"""

from __future__ import annotations

import re
from pathlib import Path

from keel.cli import cli
from keel.commands.doctor import rail_state_findings

_ROOT = Path(__file__).resolve().parents[2]
_DOCTOR = _ROOT / "keel/commands/doctor.py"

#: `keel <group> <sub>` as written inside a fix string. Stops at anything that is not part of a
#: command path, so `keel fetch --repair-gaps` yields `fetch` and `keel scope attest --trading`
#: yields `scope attest`.
_INVOCATION = re.compile(r'"keel ((?:[a-z][a-z-]*)(?: [a-z][a-z-]*)?)')


def _resolves(path: str) -> bool:
    """Whether `path` (e.g. `"scope attest"`) is a real command in the CLI tree."""
    command = cli
    for part in path.split():
        commands = getattr(command, "commands", None)
        if not commands or part not in commands:
            return False
        command = commands[part]
    return True


def test_every_command_named_in_a_fix_line_exists() -> None:
    """A renamed or misremembered command in a fix line is unreachable advice.

    Scanned out of the source rather than by rendering findings, because most fix lines only
    appear on the failing branch — a rendering-based scan would check the handful of states a
    test happens to construct and miss the rest.
    """
    named = sorted(set(_INVOCATION.findall(_DOCTOR.read_text(encoding="utf-8"))))
    assert named, "no `keel ...` invocations found in doctor.py -- has the fix format changed?"

    missing = [path for path in named if not _resolves(path)]
    assert not missing, (
        f"doctor names {len(missing)} command(s) that do not exist: {missing}. An operator "
        "following that advice gets `No such command`."
    )


def test_the_kill_switch_fix_names_the_command_that_clears_it() -> None:
    """**The specific failure this file was written for (#693).**

    Autonomy and the kill switch are deliberately separate controls — *who gets asked* versus
    *whether the agent runs at all* — and the separation is load-bearing. A fix line that
    conflates them teaches the operator they are one thing, which is the opposite of the design.

    Asserted against `rail_state_findings`' rendered output, not against the source, so it holds
    whatever the string is spelled like.
    """
    (finding,) = [
        f
        for f in rail_state_findings(
            kill_switch=True, streak_halt_until=0, drawdown_total=0, now_ts=0
        )
        if f.name == "rail.kill_switch"
    ]

    assert "keel resume" in finding.fix, (
        f"the kill-switch fix says {finding.fix!r}. Only `keel resume` "
        "(`trading.disengage_kill_switch`) clears it; `keel autonomy on` provably cannot, and "
        "following it costs a typed confirmation for unattended trading while staying halted"
    )
    assert "autonomy" not in finding.fix, (
        "naming `autonomy` here re-conflates the two gates the design keeps apart"
    )
