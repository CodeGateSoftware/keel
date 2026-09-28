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
from decimal import Decimal
from pathlib import Path

from keel.cli import cli
from keel.commands.doctor import (
    ledger_drift_findings,
    position_watch_findings,
    rail_state_findings,
    venue_drift_findings,
)

_ROOT = Path(__file__).resolve().parents[2]
_DOCTOR = _ROOT / "keel/commands/doctor.py"

#: `keel <group> <sub>` as written inside a fix string. Stops at anything that is not part of a
#: command path, so `keel fetch --repair-gaps` yields `fetch` and `keel scope attest --trading`
#: yields `scope attest`.
_INVOCATION = re.compile(r'"keel ((?:[a-z][a-z-]*)(?: [a-z][a-z-]*)?)')

#: The same shape, but for a `keel ...` invocation quoted with BACKTICKS inside a longer fix
#: string rather than one that IS the whole quoted literal (`_INVOCATION` above only ever matches
#: a string that opens with `"keel `). Most fix lines name their command this way, e.g. "... if
#: it was sold on the venue, record that with `keel positions close <id> --price P`".
_BACKTICK_INVOCATION = re.compile(r"`keel ((?:[a-z][a-z-]*)(?: [a-z][a-z-]*)?)")


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


def test_every_backtick_invocation_in_a_fix_line_exists() -> None:
    """The same check as above, but for the backtick-quoted form most fix lines actually use.

    `_INVOCATION` only matches a string that OPENS with `"keel `, which is `rail.kill_switch`'s
    shape but not most others' -- a fix line typically embeds `` `keel ...` `` inside a longer
    sentence. #899's review found two such references (`ledger.drift`, `ledger.venue_drift`)
    naming `keel positions close <id>` before that command exists, which `_INVOCATION` never saw.

    Until #798 shipped, one reference was allowlisted by its exact text because it said "once
    #798 ships". The command exists now (P4), every reference resolves, and the allowlist went:
    nothing in this scan is exempt.
    """
    lines = _DOCTOR.read_text(encoding="utf-8").splitlines()

    found = [match.group(1) for line in lines for match in _BACKTICK_INVOCATION.finditer(line)]
    assert found, "no backtick `keel ...` invocations found -- has the fix format changed?"
    assert "orders list" in found, (
        f"sanity check failed: a known invocation is missing from the scan: {found}"
    )

    missing = [path for path in found if not _resolves(path)]
    assert not missing, (
        f"doctor names {len(missing)} command(s) in backtick-quoted fix text that do not exist: "
        f"{missing}. An operator following that advice gets `No such command`."
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


def _fix_invocations(fix: str) -> list[str]:
    return _BACKTICK_INVOCATION.findall(fix)


def test_the_out_of_band_sale_findings_name_the_shipped_close_command() -> None:
    """#798 shipped `keel positions close`, so the two findings whose cause can be a sale made on
    the venue by hand name it -- rendered, not scanned, so the assertion is about the fix line an
    operator actually reads. `ledger.venue_drift` is #798's own shape; `position.unmanaged` is
    PAXG tranche 3's (#811), where closing the tranche by hand is one of the two ways out."""
    [venue] = venue_drift_findings(
        {"PAXG-USD": Decimal("0.0132")},
        {"PAXG-USD": {"total": "0", "observed_at": 1_700_000_000}},
    )
    unmanaged = next(
        f
        for f in position_watch_findings(
            [
                {
                    "id": 3,
                    "product_id": "PAXG-USD",
                    "rule_name": "turtle_breakout",
                    "qty": Decimal("0.0132"),
                    "initial_stop": None,
                }
            ],
            [],
            lambda p: True,
            set(),
        )
        if f.name == "position.unmanaged"
    )

    for finding in (venue, unmanaged):
        assert finding.status == "warn", finding.name
        invocations = _fix_invocations(finding.fix)
        assert "positions close" in invocations, (finding.name, finding.fix)
        assert all(_resolves(path) for path in invocations), (finding.name, invocations)
        assert "#798" not in finding.fix, "the command shipped; the pointer at its issue is stale"


def test_ledger_drift_does_not_send_a_booked_sale_to_the_close_command() -> None:
    """`ledger.drift` is the orders log against the ledger -- a disagreement between two keel
    tables, which an out-of-band sale (invisible to BOTH) never causes. Its two shapes are a
    filled BUY with no tranche (#799) and a SELL the orders log holds against a tranche still
    open. `keel positions close` fixes neither: it would write a SECOND SELL for the latter. So
    the fix names the reads that tell the two apart, and no longer points at #798."""
    [finding] = ledger_drift_findings(
        {"PAXG-USD": Decimal("0.0132")}, {"PAXG-USD": Decimal("0")}, {}
    )
    assert finding.status == "warn"
    assert _fix_invocations(finding.fix) == ["orders list", "positions close"]
    assert all(_resolves(path) for path in _fix_invocations(finding.fix))
    assert "#798" not in finding.fix
