"""`keel scope` -- per-venue trade-scope attestation, rail 20's input (#233).

A credential that reads fine is not evidence it can TRADE: `ROBINHOOD_API_KEY` was well-formed,
every read succeeded, and the first live order still 403'd with "You do not have permission to
perform this action." `packages/keel-core/keel_core/trade_scope.py` is where that separate fact
lives (`VenueTradeScope`/`TradeScopeState`); this module is the only way an operator writes it.

**The asymmetry, like `withdrawals` and `autonomy`.** `--trading` RELEASES a rail-20 veto on live
ENTRIES for this venue's credential, so it demands a typed `yes` at a terminal exactly like
`withdrawals attest --enabled`. `--read-only` only ever REDUCES capability and stays ungated, so
it keeps working from cron or a script with no human anywhere in the loop.

**Follows `subscription.py`'s service-layer split** (issue #389 C3): the write
(`apply_scope_attest`) and the line-rendering (`scope_show_lines`) are plain functions taking
`(repo, ...)` and returning a string/list of strings, with the click command bodies thin. PR2
renders `show`'s lines from `doctor`, so a second implementation of either would let the CLI and
`doctor` silently disagree about what rail 20 will do next.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import click
from keel_core.credential_identity import current_credential_fingerprint
from keel_core.trade_scope import READ_ONLY, TRADING, TradeScopeState, VenueTradeScope

from keel.commands._common import (
    _bound_venue_or_default,
    _load_cfg,
    _open_repo,
    _require_interactive_confirmation,
    with_disclaimer,
)
from keel.data.repository import Repository


@click.group("scope")
def scope_group() -> None:
    """View or attest a venue's trade scope (rail 20's input: can this credential place a live
    ENTRY?).

    A credential that reads fine is not evidence it can trade -- rail 20 (PR2) is the guard that
    reads the record this group writes. Until a venue is attested, it fails closed: keel ships
    unable to place a live entry on a venue nobody has attested, deliberately. An omitted
    `--venue` means this deployment's bound venue (its `broker:` selection; coinbase when
    unbound) -- the same key rail 20 will gate on, so the default writes the record that will
    actually be read.
    """


#: The typed gate's exact action/detail wording, module-level so any future second front-end
#: (PR2's `doctor`, or a later console form) runs the SAME gate with the SAME words rather than a
#: second wording that could drift from this one. Checked against the source by
#: `tests/test_capabilities.py` via the `(module, function)` the gate call site sits in.
SCOPE_ATTEST_ACTION = "attest venue trade scope as TRADING"
SCOPE_ATTEST_DETAIL = (
    "This RELEASES rail 20's veto on live ENTRIES using this venue's credential. Exits are "
    "deliberately unaffected -- rail 20 is entries-only."
)


def _utc_date(ts: int) -> str:
    """An epoch second as a plain UTC date, for operator-facing refusal history.

    A raw epoch is unreadable at the moment it matters most -- an operator deciding whether a
    past refusal is old news or this morning's incident. The design's wording is "you re-attested
    a venue that refuted a credential on <date>", and a date is what that sentence needs.
    """
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d")


def apply_scope_attest(
    repo: Repository, *, venue: str | None, trading: bool, now_ts: int
) -> str:
    """`scope attest`'s write: resolve the venue, upsert the record, return the confirmation
    line.

    **Re-attesting over a refuted record is allowed, by design** -- it is how an operator
    reports "I rotated the credential" after the venue itself refused the old one. The record's
    `refuted_ts`/`refuted_reason` are carried FORWARD unchanged rather than cleared: they are
    history (what a PREVIOUS credential on this venue did), not a property of the credential
    being attested now, and `doctor`/`scope show` must still be able to say "you re-attested a
    venue that refused a credential on <date>" after the operator has moved on. `confirmed_ts` is
    carried forward the same way -- it too describes what has happened on this venue, not what
    this attestation newly claims. Only `state`, `attested_scope` and `attested_ts` are the fresh
    facts this command asserts.

    `credential_fingerprint` (#633) is the one field that does NOT follow "only state/
    attested_scope/attested_ts are fresh": it is stamped with the CURRENT credential's
    fingerprint (`current_credential_fingerprint`), including `None` when nothing resolves,
    rather than carried forward from `existing`. The operator is attesting about the credential
    IN PLACE right now, not about whichever credential happened to be current the last time this
    venue was written -- carrying the old fingerprint forward would bind this fresh attestation
    to a possibly-already-rotated-away credential and defeat the whole point of #633's read-time
    comparison.
    """
    resolved_venue = _bound_venue_or_default(venue)
    existing = repo.get_venue_trade_scope(resolved_venue)
    scope = TRADING if trading else READ_ONLY

    repo.upsert_venue_trade_scope(
        VenueTradeScope(
            venue=resolved_venue,
            state=TradeScopeState.ATTESTED,
            attested_scope=scope,
            attested_ts=now_ts,
            confirmed_ts=existing.confirmed_ts if existing is not None else None,
            refuted_ts=existing.refuted_ts if existing is not None else None,
            refuted_reason=existing.refuted_reason if existing is not None else None,
            credential_fingerprint=current_credential_fingerprint(resolved_venue),
        )
    )
    label = "TRADING" if trading else "READ_ONLY"
    line = f"attested {resolved_venue}: scope={label}"
    if trading:
        line += " -- rail 20 may now place live ENTRIES on this credential"
    else:
        line += " -- rail 20 will veto live ENTRIES on this credential"
    if existing is not None and existing.refuted_ts is not None:
        line += (
            " (a prior credential on this venue was refuted on "
            f"{_utc_date(existing.refuted_ts)})"
        )
    return line


def scope_show_lines(repo: Repository) -> list[str]:
    """`scope show`'s exact lines, as a function of the repo it reads -- PR2's `doctor` renders
    the same report this command echoes.

    Unlike `subscription`'s record, trade scope carries no TTL/due-date (see
    `keel_core.trade_scope`'s module docstring for why), so there is no staleness column here.
    """
    records = repo.list_venue_trade_scopes()

    if not records:
        # The advice names the BOUND venue -- the one rail 20 will actually gate on for this
        # deployment -- so the operator's copy-paste writes the record that will be read.
        venue = _bound_venue_or_default(None)
        return [
            "no trade scope attested for any venue -- rail 20 fails closed against every live "
            "entry until attested (keel ships unable to place a live entry, deliberately). "
            f"Run `keel scope attest --trading --venue {venue}` once you have verified the "
            "credential can trade (or `--read-only` to record that it cannot)."
        ]

    lines: list[str] = []
    for record in records:
        scope = record.attested_scope if record.attested_scope is not None else "none"
        # `current_fingerprint=None` here is deliberate for PR1 of #633, not an oversight: this
        # display does not yet resolve the real current fingerprint, so it cannot distinguish
        # "different credential" from "never attested" -- wiring that distinction in before the
        # display can make it correctly would reproduce #624's collapse. PR2 wires the real value
        # and the distinguishing text; `None` here never withdraws permission in the meantime.
        lines.append(
            f"{record.venue}: state={record.state.value} attested_scope={scope} "
            f"live_entry_permitted={record.may_place_live_entry(None)}"
        )
        if record.refuted_ts is not None:
            # Surfaced even when the record has since been re-attested (state is no longer
            # REFUTED): a past refusal on this venue is exactly what an operator re-checking
            # trust needs to see, and `apply_scope_attest` deliberately never clears it.
            reason = f": {record.refuted_reason}" if record.refuted_reason else ""
            lines.append(f"  refuted on {_utc_date(record.refuted_ts)}{reason}")
    return lines


@scope_group.command("attest")
@click.option(
    "--venue",
    default=None,
    help="Venue to attest (default: this config's bound venue -- its `broker:` selection; "
    "coinbase when unbound).",
)
@click.option(
    "--trading/--read-only",
    "trading",
    required=True,
    help="Can keel place live ENTRY orders on this venue's credential right now?",
)
@click.pass_context
@with_disclaimer
def scope_attest(ctx: click.Context, venue: str | None, trading: bool) -> None:
    """Attest whether this venue's credential can place live ENTRY orders (rail 20's input).

    **Asymmetric, like `withdrawals attest`.** `--trading` RELEASES rail 20's veto, so it demands
    a typed `yes` at a terminal: releasing this is what lets an unattended cycle place a real,
    money-spending order on this venue for the first time, and a cron line must never be able to
    do that unattended. `--read-only` only ever REDUCES capability -- it records that this
    credential cannot (or should not) place live orders -- and stays ungated, usable from
    anywhere, the same way `withdrawals attest --suspended` does.

    Says nothing about EXITS: rail 20 (PR2) is entries-only, so this attestation's effect is
    scoped to new entries exactly like rail 17's withdrawal attestation is scoped to new
    entries.

    Re-attesting is how an operator reports a rotated credential, including over a record the
    venue itself previously REFUSED -- see `apply_scope_attest` for why the refusal history
    survives that re-attestation rather than being cleared.
    """
    repo = _open_repo(ctx)
    # Binds this process's venue (telemetry.bind_venue) so an omitted --venue below resolves to
    # the deployment this config actually trades, not a frozen default.
    _load_cfg(ctx)

    if trading:
        _require_interactive_confirmation(SCOPE_ATTEST_ACTION, SCOPE_ATTEST_DETAIL)

    line = apply_scope_attest(repo, venue=venue, trading=trading, now_ts=int(time.time()))
    click.echo(line)


@scope_group.command("show")
@click.pass_context
@with_disclaimer
def scope_show(ctx: click.Context) -> None:
    """Show every venue's recorded trade scope, and whether rail 20 currently permits a live
    entry there."""
    repo = _open_repo(ctx)
    _load_cfg(ctx)
    for line in scope_show_lines(repo):
        click.echo(line)
