"""`keel posture` -- the operator's statement about an account no venue will describe.

Stage 2 of #666, issue #691. Stage 1 established that Coinbase exposes NO cash-versus-margin
field for spot: `margin_rate` sits in every account's response schema carrying `null`, and every
margin/borrow/leverage/liquidation field in the SDK lives in the futures or perpetuals types. The
adapter's check therefore REFUTES and never issues -- an INTX portfolio proves derivatives are
available, its absence proves nothing.

What closes that residual is a human who knows their own account saying so, on the record, with
the venue able to contradict them. This module is that record's front door, and rail 22 reads it.

**The vocabulary is `keel scope`'s, deliberately.** `attest` writes the claim, `show` reports it,
the venue resolves the same way, and re-attesting over a refutation is allowed because that is
how an operator reports "I closed the derivative portfolio". A new vocabulary for the same shape
would be a second thing to learn and a second thing to get wrong.

**The one real difference is the clock.** Trade scope carries no TTL because the venue re-confirms
it on every accepted placement. Nothing re-confirms this, ever, so the due date is the only thing
between a lapsed claim and a live entry -- `VenueSubscription`'s situation and `VenueSubscription`'s
remedy. The due date is STORED rather than derived at read time, so changing `ATTESTATION_TTL_SEC`
cannot retroactively expire a claim a human made under the window in force when they made it, nor
silently revive one that had already lapsed.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import click
from keel_core.cash_posture import (
    ATTESTATION_TTL_SEC,
    MARGIN_ENABLED,
    SPOT_CASH,
    CashPostureState,
    VenueCashPosture,
)
from keel_core.credential_identity import current_credential_fingerprint

from keel.commands._common import (
    _bound_venue_or_default,
    _open_repo,
    _require_interactive_confirmation,
    with_disclaimer,
)
from keel.data.repository import Repository


def _utc_date(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%d")


def apply_posture_attest(
    repo: Repository, *, venue: str | None, spot_cash: bool, now_ts: int
) -> str:
    """`posture attest`'s write: resolve the venue, upsert the record, return the confirmation.

    **`spot_cash=False` records `MARGIN_ENABLED` rather than refusing to write.** An operator
    whose account really does have margin must be able to say so; leaving the record absent would
    read as "nobody has attested" in every report, which is a less useful fact and invites the
    same person to be asked again next week. The record then vetoes entries, which is correct --
    on a margin-enabled account a sell can fill as a short, which is *bay' ma la yamlik*.

    **Re-attesting over a refuted record is allowed, by design** -- it is how an operator reports
    "I closed the derivative portfolio". `refuted_ts`/`refuted_reason` are carried FORWARD, not
    cleared: they are history about this venue, not a property of the claim being made now, and
    `doctor`/`posture show` must still be able to say the venue once contradicted a claim here.
    Only `state`, `attested_posture`, `attested_ts` and `attest_due_ts` are the fresh facts.

    `credential_fingerprint` (#633) is stamped with the CURRENT credential rather than carried
    forward: the operator is attesting about the account the credential in place right now
    reaches, and carrying an old fingerprint would bind a fresh claim to a possibly
    already-rotated-away credential.
    """
    resolved_venue = _bound_venue_or_default(venue)
    existing = repo.get_venue_cash_posture(resolved_venue)
    posture = SPOT_CASH if spot_cash else MARGIN_ENABLED

    repo.upsert_venue_cash_posture(
        VenueCashPosture(
            venue=resolved_venue,
            state=CashPostureState.ATTESTED,
            attested_posture=posture,
            attested_ts=now_ts,
            attest_due_ts=now_ts + ATTESTATION_TTL_SEC,
            refuted_ts=existing.refuted_ts if existing is not None else None,
            refuted_reason=existing.refuted_reason if existing is not None else None,
            credential_fingerprint=current_credential_fingerprint(resolved_venue),
        )
    )
    due = _utc_date(now_ts + ATTESTATION_TTL_SEC)
    line = f"attested {resolved_venue}: posture={posture}, expires {due}"
    if spot_cash:
        line += " -- rail 22 may now place live ENTRIES against this account"
    else:
        line += " -- rail 22 will veto live ENTRIES against this account"
    if existing is not None and existing.refuted_ts is not None:
        line += (
            " (venue evidence contradicted a claim on this venue on "
            f"{_utc_date(existing.refuted_ts)})"
        )
    return line


def refute_posture(repo: Repository, *, venue: str, reason: str, now_ts: int) -> bool:
    """Record that venue evidence CONTRADICTS `venue`'s standing cash-posture claim.

    Returns whether anything was written. **Deliberately NOT symmetric with attestation:** a
    human may issue a claim, and only the venue may withdraw one. With no record to refute this
    writes nothing and returns `False` -- creating a `REFUTED` row from nothing would invent a
    history keel never had, and "no claim" is already a veto, so there is nothing to improve by
    fabricating one.

    `refuted_ts` is set only on the FIRST contradiction of a given claim. Advancing it on every
    build would report the most recent cycle instead of the discovery, and the discovery is the
    moment the claim stopped being true as far as keel can tell. A re-attestation resets the
    cycle: the next contradiction is a new discovery about a new claim, and its timestamp moves.

    The attestation columns are preserved. `doctor` and `posture show` must be able to say WHAT
    was claimed and when, and an operator asked to re-attest deserves to be told what they said
    last time.
    """
    existing = repo.get_venue_cash_posture(venue)
    if existing is None:
        return False
    already_refuted = existing.state is CashPostureState.REFUTED and existing.refuted_ts is not None
    repo.upsert_venue_cash_posture(
        VenueCashPosture(
            venue=existing.venue,
            state=CashPostureState.REFUTED,
            attested_posture=existing.attested_posture,
            attested_ts=existing.attested_ts,
            attest_due_ts=existing.attest_due_ts,
            refuted_ts=existing.refuted_ts if already_refuted else now_ts,
            refuted_reason=existing.refuted_reason if already_refuted else reason,
            credential_fingerprint=existing.credential_fingerprint,
        )
    )
    return True


def posture_show_lines(repo: Repository, *, now_ts: int) -> list[str]:
    """`posture show`'s exact lines, as a function of the repo -- `doctor` renders the same facts.

    Reports EXPIRY as its own column, not folded into a pass/fail. "Expired" and "attested
    margin" both stop a live entry, but one calls for a re-attestation and the other for a change
    to the account, and an operator who cannot tell them apart will do the wrong one.
    """
    records = repo.list_venue_cash_postures()
    if not records:
        return [
            "no venue has an attested cash posture.",
            "  No venue exposes a cash-versus-margin field for spot, so nothing can supply this "
            "but you -- rail 22 vetoes live ENTRIES until it is attested.",
            "  Check the account, then: `keel posture attest --spot-cash`",
        ]
    lines = ["cash posture, by venue:"]
    for record in records:
        state = record.state.value.upper()
        if record.state is CashPostureState.ATTESTED and not record.is_current(now_ts):
            state = "EXPIRED"
        lines.append(
            f"  {record.venue}: {state} posture={record.attested_posture} "
            f"attested={_utc_date(record.attested_ts) if record.attested_ts else '-'} "
            f"expires={_utc_date(record.attest_due_ts) if record.attest_due_ts else '-'}"
        )
        if record.refuted_ts is not None:
            reason = f": {record.refuted_reason}" if record.refuted_reason else ""
            lines.append(
                f"    venue evidence contradicted a claim here on "
                f"{_utc_date(record.refuted_ts)}{reason}"
            )
    return lines


@click.group("posture")
def posture_group() -> None:
    """What you have established about a venue account's cash-versus-margin posture."""


@posture_group.command("attest")
@click.option("--venue", "venue", default=None, help="Venue to attest (default: the bound venue).")
@click.option(
    "--spot-cash/--margin-enabled",
    "spot_cash",
    required=True,
    help="Whether this account is cash-only spot, or can borrow/short.",
)
@click.pass_context
@with_disclaimer
def posture_attest(ctx: click.Context, venue: str | None, spot_cash: bool) -> None:
    """Record what you have established about this venue account's posture.

    GATED at a terminal when attesting `--spot-cash`, and deliberately ungated for
    `--margin-enabled`: the first RELEASES rail 22 and is the only statement in keel that can
    permit a live entry on the strength of a human's word alone, so it demands a typed `yes` from
    a person. The second only ever reduces capability, so it must work from a script -- the same
    asymmetry `keel autonomy` draws.
    """
    repo = _open_repo(ctx)
    resolved = _bound_venue_or_default(venue)
    if spot_cash:
        _require_interactive_confirmation(
            f"attest that {resolved} is a CASH-ONLY spot account",
            "No venue exposes this field for spot, so nothing can check you. Rail 22 will "
            "permit live ENTRIES on the strength of this statement alone, and a margin-enabled "
            "account can fill a sell as a SHORT. Confirm you have checked the account itself.",
        )
    click.echo(
        apply_posture_attest(
            repo, venue=venue, spot_cash=spot_cash, now_ts=int(time.time())
        )
    )


@posture_group.command("show")
@click.pass_context
@with_disclaimer
def posture_show(ctx: click.Context) -> None:
    """Print every venue's cash-posture record."""
    for line in posture_show_lines(_open_repo(ctx), now_ts=int(time.time())):
        click.echo(line)
