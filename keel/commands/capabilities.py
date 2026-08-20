"""`keel capabilities` -- print every action that needs a human, and what covers it.

The inventory itself is `keel/capabilities.py`; this is only the rendering and the `--json`
shape. It exists because an inventory an auditor has to read the source to see is not much of an
audit surface -- and because "which of these can a scheduled job reach?" is a question an operator
asks about their own deployment, not about the repository.

No config, no database, no network: it answers from the code it was built from, so it cannot fail
for environmental reasons and it describes the binary actually running.
"""

from __future__ import annotations

import json
from dataclasses import asdict

import click

from keel.capabilities import CAPABILITIES, GATES, render_lines


@click.command("capabilities")
@click.option("--json", "as_json", is_flag=True, default=False, help="Emit machine-readable JSON.")
def capabilities_cmd(as_json: bool) -> None:
    """Every capability-increasing action in this build, and the gate covering it.

    A capability-increasing action is one that leaves keel able to do something afterwards that
    it could not do before without asking again -- arming autonomy, releasing a halt, re-seeding
    the drawdown breaker, attesting withdrawals, replacing the binary.

    Each is gated on a human at an interactive terminal. That gate has no environment-variable or
    flag override, deliberately: any such seam would be settable from cron, and would dissolve
    the fail-closed behaviour of every rail built on it. So a scheduled job cannot reach any
    action listed here, and that is the property this command exists to make checkable.
    """
    if as_json:
        click.echo(
            json.dumps(
                {
                    "gates": [asdict(gate) for gate in GATES],
                    "capabilities": [asdict(cap) for cap in CAPABILITIES],
                },
                indent=2,
            )
        )
        return
    for line in render_lines():
        click.echo(line)
