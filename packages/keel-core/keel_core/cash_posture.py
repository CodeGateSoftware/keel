"""The per-venue cash-posture record: what a human attested about an account no venue will describe.

**The fact this module exists around.** Coinbase exposes NO cash-versus-margin field for spot.
A read of the live account on 2026-09-02 (#666, Stage 1) found `margin_rate` present in the
response schema and `null` in the value -- so its presence signals nothing, and a check keyed on
presence would refuse every deployment -- and portfolios `DEFAULT`/`CONSUMER` with no INTX. Every
margin, borrow, leverage and liquidation field in the SDK lives in `futures_types`,
`perpetuals_types`, or the derivative order fields.

So the venue check **refutes and never issues**: an INTX portfolio proves derivatives are
available on the account, and its absence proves nothing at all. That residual is not an
engineering problem to solve with another read. It is the same shape as rail 17's `qabd` (§65.4)
and #233's trade scope: silence is not evidence of possession, and silence is not evidence of a
cash account. What closes it is a human who knows their own account saying so, on the record,
with the venue able to contradict them.

TWO THINGS DIFFER FROM `VenueTradeScope`, AND BOTH FOLLOW FROM THAT.

**There is no `CONFIRMED` state.** Trade scope earns one because the venue re-proves it on every
accepted placement, moving the record forward by itself. Nothing -- no placement, no read, no
field -- can ever prove a spot account is cash-only. A `CONFIRMED` value would therefore be a
state nothing is entitled to write, and an unreachable state is one a later reader eventually
writes anyway. Its absence is the design, and a test pins it.

**There IS a TTL**, where trade scope has none. Trade scope needs no clock precisely because the
venue re-confirms it continuously, so there is no silent drift for a clock to catch. This record
has NO observation channel, which is exactly `VenueSubscription`'s situation -- and there a due
date is the only thing standing between a lapsed claim and a live spend. Same argument, same
remedy, same boundary semantics (due-at is expired, not one tick still-good).

WHAT THIS RECORD MAY AND MAY NOT GATE. A missing, expired, refuted or margin-attested posture
vetoes new ENTRIES and nothing else. Exits, stop rolls and cancels are unaffected, because a rail
that blocked an exit over a fact about the account would strand a position that wanted out -- the
rule rails 11/16/17/20 already follow.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from keel_core.trade_scope import CredentialEvidence

#: The two postures an operator may attest. `SPOT_CASH` is the only one that permits a live
#: entry; `MARGIN_ENABLED` is an HONEST answer that refuses one, and recording it is better than
#: leaving the record absent -- absent reads as "nobody has attested" in every report, which is a
#: different and less useful fact than "the operator says this account can borrow".
SPOT_CASH = "spot_cash"
MARGIN_ENABLED = "margin_enabled"

#: How long an attestation stands: 90 days.
#:
#: NOT rail 17's 7 days. That window guards a withdrawal freeze, which a venue can impose
#: overnight without telling anyone, so it has to be re-checked at roughly the cadence a freeze
#: could appear. An account does not silently acquire margin -- enabling it is a deliberate act by
#: the account holder -- so a weekly re-attestation would be ceremony, and ceremony that fires
#: often enough gets automated, which is how an attestation stops meaning anything.
#:
#: NOT "never", either: a posture attested about an account eighteen months ago is not evidence
#: about that account today, and the operator may not even be the same person. 90 days is roughly
#: quarterly -- often enough that a real change surfaces within a quarter, rare enough that the
#: prompt is still read rather than dismissed.
ATTESTATION_TTL_SEC = 90 * 86400


class CashPostureState(str, Enum):
    """What is known about a venue account's cash-versus-margin posture.

    THREE states, and the missing fourth is the point -- see the module docstring. There is no
    `CONFIRMED`, because no venue read can affirm this and a state nothing may write is a trap.
    """

    UNVERIFIED = "unverified"
    ATTESTED = "attested"
    REFUTED = "refuted"


@dataclass(frozen=True)
class VenueCashPosture:
    """One venue's cash-posture record: what the operator claimed, and what the venue has refuted.

    `attested_posture` is `SPOT_CASH`, `MARGIN_ENABLED`, or `None` when nobody has attested.

    `attest_due_ts` deliberately has no default and is checked for `None`: a record with no due
    date is a claim that never expires, which this record does not permit, and inventing one at
    read time would let a writer forget to set one and have the reader quietly cover for it.

    `refuted_reason` is free text from the venue evidence that refuted the claim -- "INTX
    portfolio present" is the one Stage 1 can produce. It is for the operator surface only and is
    never read by policy.

    `credential_fingerprint` (#633) is a non-reversible fingerprint of the credential IDENTIFIER
    the claim was made under, or `None`. No default, for the same reason as `VenueTradeScope`: a
    default lets a future writer silently forget and degrade detection back to a venue-only key.
    """

    venue: str
    state: CashPostureState
    attested_posture: str | None
    attested_ts: int | None
    attest_due_ts: int | None
    refuted_ts: int | None
    refuted_reason: str | None
    credential_fingerprint: str | None

    def credential_evidence(self, current_fingerprint: str | None) -> CredentialEvidence:
        """What this record's fingerprint says about the CURRENT credential.

        Imported from `trade_scope` rather than redefined: the four states and the reasoning for
        why there are four rather than three are general to "evidence collected under some
        credential", and a second copy would drift from the first.
        """
        if self.credential_fingerprint is None:
            return CredentialEvidence.UNFINGERPRINTED
        if current_fingerprint is None:
            return CredentialEvidence.CREDENTIAL_UNREADABLE
        if self.credential_fingerprint == current_fingerprint:
            return CredentialEvidence.MATCHES
        return CredentialEvidence.DIFFERENT_CREDENTIAL

    def is_current(self, now_ts: int) -> bool:
        """Whether the attestation is still inside its window -- the CLOCK question, alone.

        Asked separately from `may_place_live_entry` so a report can say "expired" rather than
        only "refused": those call for different actions from an operator, one a re-attestation
        and the other a change to the account.
        """
        return self.attest_due_ts is not None and self.attest_due_ts > now_ts

    def may_place_live_entry(self, now_ts: int, current_fingerprint: str | None) -> bool:
        """Whether a live ENTRY may be placed against this venue's account.

        `current_fingerprint` is REQUIRED (no default) so mypy names every call site rather than
        letting one silently opt out of the #633 check. `None` means "current credential unknown"
        and never withdraws permission -- the safe value to pass, not merely the lazy one.

        `DIFFERENT_CREDENTIAL` withdraws permission unconditionally: a posture attested under one
        credential is not a claim about the account another credential reaches. `UNFINGERPRINTED`
        and `CREDENTIAL_UNREADABLE` fall through untouched, both fail-safe for the reasons
        `VenueTradeScope` sets out.

        Then, driven by `state` and the clock -- never by `refuted_ts`, which is history. An
        operator who closes an INTX portfolio and re-attests must be able to trade again while
        `doctor` can still report the old refusal; vetoing on `refuted_ts is not None` would mean
        the one path that RECOVERS never unblocks.

        - `ATTESTED`, posture `SPOT_CASH`, inside its window: True.
        - `ATTESTED` but expired: False. An unre-confirmed claim is the same class of unknown as
          no claim.
        - `ATTESTED` with any other posture (`MARGIN_ENABLED` included): False.
        - `REFUTED`: False. Venue evidence outranks the claim.
        - `UNVERIFIED`: False.

        Fails closed on anything not listed.
        """
        if self.credential_evidence(current_fingerprint) is CredentialEvidence.DIFFERENT_CREDENTIAL:
            return False
        if self.state is not CashPostureState.ATTESTED:
            return False
        return self.attested_posture == SPOT_CASH and self.is_current(now_ts)


__all__ = [
    "ATTESTATION_TTL_SEC",
    "MARGIN_ENABLED",
    "SPOT_CASH",
    "CashPostureState",
    "VenueCashPosture",
]
