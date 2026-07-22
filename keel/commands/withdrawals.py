"""`keel withdrawals` -- withdrawal-capability attestation, rail 17's input (KB §65.4 qabd).

`--enabled` RELEASES a rail-17 entry halt, so it demands a typed `yes` at a terminal via the
shared confirmation gate; `--suspended` only ever reduces capability and stays ungated. The group
reads and writes attestation state through the repository seam and never touches the broker.
"""

from __future__ import annotations

import time

import click

from keel.commands._common import _open_repo, _require_interactive_confirmation, with_disclaimer
from keel.execution import executor


@click.group("withdrawals")
def withdrawals_group() -> None:
    """Withdrawal-capability attestation -- rail 17's input (KB §65.4 qabd/possession)."""


@withdrawals_group.command("attest")
@click.option(
    "--enabled/--suspended",
    "enabled",
    required=True,
    help="Are BTC/ETH/PAXG/USDC balances withdrawable on demand right now?",
)
@click.pass_context
@with_disclaimer
def withdrawals_attest(ctx: click.Context, enabled: bool) -> None:
    """Attest the account's current withdrawal state.

    Under §65.4 possession (`qabd`) holds only while "there is nothing to prevent the buyer from
    taking physical possession whenever he desires". An asset we cannot withdraw is an asset we
    may not validly possess -- so rail 17 halts new ENTRIES when this is suspended or unknown.

    **Asymmetric, like `autonomy`.** `--suspended` only ever REDUCES capability and is ungated,
    usable from anywhere. `--enabled` RELEASES a rail-17 entry halt, so it demands a typed `yes`
    at a terminal exactly like `resume`/`resume-entries`/`record-flow`/`reset-hwm`.

    That gate used to be unnecessary for a reason that no longer holds: this command was
    justified by "the confirm gate and the bypass-arm token still sit in front of it". The
    bypass-arm token no longer exists, and with `keel autonomy on` the confirm gate is not there
    either -- so without this, a cron line could clear a rail-17 halt and the next cycle would
    place live orders with no human anywhere in the loop.
    """
    repo = _open_repo(ctx)
    if enabled:
        _require_interactive_confirmation(
            "attest withdrawals as ENABLED",
            "This RELEASES rail 17's entry halt; the agent may place orders on its next cycle.",
        )
    now_ts = int(time.time())
    repo.set_state("withdrawals_enabled", bool(enabled))
    repo.set_state("withdrawals_attested_at", now_ts)
    ttl_days = executor.WITHDRAWAL_ATTESTATION_TTL_SEC // 86400
    state = "ENABLED" if enabled else "SUSPENDED"
    click.echo(f"withdrawals attested {state}; expires in {ttl_days} days")
    if not enabled:
        click.echo("new ENTRIES are now halted (rail 17). Exits are deliberately unaffected.")


@withdrawals_group.command("show")
@click.pass_context
def withdrawals_show(ctx: click.Context) -> None:
    """Show the current attestation and whether it is still fresh."""
    repo = _open_repo(ctx)
    now_ts = int(time.time())
    resolved = executor._withdrawals_enabled(repo, now_ts)
    attested_at = int(repo.get_state("withdrawals_attested_at", default=0) or 0)

    if resolved is None and not attested_at:
        click.echo("withdrawals: UNKNOWN (never attested) -- rail 17 blocks new entries")
        return
    age_days = (now_ts - attested_at) / 86400 if attested_at else 0
    stale = resolved is None and attested_at
    label = {True: "ENABLED", False: "SUSPENDED", None: "UNKNOWN (attestation STALE)"}[resolved]
    click.echo(f"withdrawals: {label}")
    click.echo(f"attested {age_days:.1f} days ago")
    if stale or resolved is False:
        click.echo("rail 17 is blocking new entries; exits are unaffected")
