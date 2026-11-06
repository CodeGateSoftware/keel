"""`keel withdrawals` -- rail 17's manual switch and a view of what the venue says (§65.4 qabd).

Rail 17 no longer asks the operator to vouch for withdrawals on a clock (#1010): it reads
Coinbase's per-wallet `allow_withdrawals` on every BUY (`executor._withdrawal_capability`). What
is left for a human is one manual brake: `--suspended` halts entries regardless of what the venue
says, and `--enabled` clears it. Neither carries an expiry.

`--enabled` RELEASES a rail-17 entry halt, so it demands a typed `yes` at a terminal via the
shared confirmation gate; `--suspended` only ever reduces capability and stays ungated. `attest`
reads and writes through the repository seam; only `show` touches the broker, to print what the
venue answers.
"""

from __future__ import annotations

import click
from keel_core.products import quote_currency_of

from keel.commands._common import (
    _build_broker,
    _load_cfg,
    _open_repo,
    _require_interactive_confirmation,
    with_disclaimer,
)
from keel.data.repository import Repository
from keel.execution import executor

#: How long `show` waits on the venue. Bounded because `show` is a viewer an operator runs by
#: hand: a hung network call must degrade to "venue unreadable", not hang the terminal.
SHOW_VENUE_TIMEOUT_SEC = 15


@click.group("withdrawals")
def withdrawals_group() -> None:
    """Rail 17's manual withdrawal switch and the venue's answer (KB §65.4 qabd/possession)."""


#: The typed gate's exact action/detail wording (O3), module-level so the console's
#: withdrawals form runs the SAME gate with the SAME words -- one home, two front-ends,
#: never a second wording that can drift from the CLI's. Pinned by test against both
#: call sites (`keel.commands.compliance_console.clis_typed_withdrawals_gate`).
WITHDRAWALS_ATTEST_ACTION = "attest withdrawals as ENABLED"
WITHDRAWALS_ATTEST_DETAIL = (
    "This RELEASES rail 17's entry halt; the agent may place orders on its next cycle."
)


@withdrawals_group.command("attest")
@click.option(
    "--enabled/--suspended",
    "enabled",
    required=True,
    help="--suspended halts new entries by hand; --enabled clears that suspension.",
)
@click.pass_context
@with_disclaimer
def withdrawals_attest(ctx: click.Context, enabled: bool) -> None:
    """Set or clear rail 17's manual suspension.

    Under §65.4 possession (`qabd`) holds only while "there is nothing to prevent the buyer from
    taking physical possession whenever he desires". Rail 17 now asks the VENUE that on every
    buy; this switch is the operator's own brake on top of it. `--suspended` halts new ENTRIES
    until cleared, whatever the venue says. Neither direction expires.

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
        _require_interactive_confirmation(WITHDRAWALS_ATTEST_ACTION, WITHDRAWALS_ATTEST_DETAIL)
    repo.set_state("withdrawals_enabled", bool(enabled))
    if enabled:
        click.echo(
            "manual suspension cleared; rail 17 reads withdrawal capability from the venue "
            "on every buy."
        )
    else:
        click.echo("manual suspension set. new ENTRIES are now halted (rail 17).")
        click.echo(
            "Exits are deliberately unaffected. Clear it with: keel withdrawals attest --enabled"
        )


#: What the venue's answer for one wallet reads as, keyed by the adapter's tri-state.
_WALLET_WORDS = {True: "allowed", False: "withdrawals disabled", None: "no wallet"}


def _suspension_line(suspended: bool | None) -> str:
    """The stored brake, tri-state: an unreadable store is `unknown`, never `off` -- the rail
    itself fails closed on it, so a display saying `off` would be green for a refused buy."""
    if suspended is None:
        return "manual suspension: unknown (store unreadable)"
    return "manual suspension: ON -- entries halted" if suspended else "manual suspension: off"


def _wallet_currencies(repo: Repository) -> list[str]:
    """The quote currency plus the base of every LIVE rule's product, in first-seen order."""
    currencies: list[str] = []
    for row in repo.get_rules(status="live"):
        product_id = (row.get("params") or {}).get("product_id")
        quote = quote_currency_of(product_id)
        if not quote:
            continue
        for currency in (quote, str(product_id).rpartition("-")[0].upper()):
            if currency and currency not in currencies:
                currencies.append(currency)
    return currencies or ["USD"]


@withdrawals_group.command("show")
@click.pass_context
def withdrawals_show(ctx: click.Context) -> None:
    """Show the manual suspension and what the venue says about each wallet rail 17 reads."""
    repo = _open_repo(ctx)
    click.echo(_suspension_line(executor._stored_suspension(repo)))
    try:
        config = _load_cfg(ctx)
        broker = _build_broker(config, timeout=SHOW_VENUE_TIMEOUT_SEC, repo=repo)
        answers = broker.withdrawals_allowed(_wallet_currencies(repo))
    except Exception:
        # A viewer, not a gate: a venue that cannot be asked is reported, never raised, so the
        # operator can still read the manual-suspension line above it.
        click.echo("venue unreadable -- rail 17 refuses entries while it cannot read the venue")
        return
    for currency, allowed in answers.items():
        click.echo(f"  {currency:<8}{_WALLET_WORDS[allowed]}")
