"""The per-venue trade-scope record, and the policy deciding whether a live entry may use it.

A credential that reads fine is not evidence it can trade (#233): `ROBINHOOD_API_KEY` was
well-formed, every read succeeded, and the first live order still 403'd with "You do not have
permission to perform this action." Key presence and read success are the wrong predicate. Trade
scope is a separate fact about the credential, and this module is where the engine asks it.

Unlike `subscription.py`'s venue subscription, this record has **no TTL and no due date**. A
subscription is user-asserted with no observation channel to check it against, so staleness is
the only thing standing between a lapsed plan and a live spend cap -- it must expire on a clock.
Trade scope is different: the venue itself re-confirms it on every accepted placement (`CONFIRMED`
moves the record forward) and re-attestation happens the moment an operator rotates a credential,
not on a schedule. There is no silent drift for a clock to catch, so adding one would only be
ceremony -- a due date with nothing behind it to expire.

**#633: the record outlives the credential it was collected under, unless it says which one that
was.** `state` alone answers "did we ever get permission on SOME credential for this venue" --
it says nothing about whether that is still the SAME credential a live entry would use today.
`credential_fingerprint` is the record's answer to that second question: a non-reversible
fingerprint of the credential IDENTIFIER (never the signing secret --
`keel_core.credential_identity` carries the full argument) that was in place when this evidence
was collected. `credential_evidence`/`may_place_live_entry` compare it against the CURRENT
fingerprint at read time, so a credential swapped out from under a `CONFIRMED` or
`ATTESTED`-for-`TRADING` record is caught even when nobody ran a keel command to make the swap.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

#: The two legal values of `VenueTradeScope.attested_scope`. Shared here so the CLI and rail 20
#: read the same vocabulary instead of re-typing string literals that could quietly drift apart.
TRADING = "trading"
READ_ONLY = "read_only"


class TradeScopeState(str, Enum):
    """What is known about a venue credential's ability to place a live trade."""

    UNVERIFIED = "unverified"
    ATTESTED = "attested"
    CONFIRMED = "confirmed"
    REFUTED = "refuted"


class CredentialEvidence(str, Enum):
    """What this record's `credential_fingerprint` says about the CURRENT credential, given as a
    separate question from `state` (#633). Four states, not the three "matches / does not match /
    unknown" might suggest, because two different kinds of "unknown" fail differently and
    conflating them was #624's whole lesson:

    - `MATCHES`: the record's fingerprint and the current one are both known and equal. The
      strongest case -- this evidence was collected under the credential in place right now.
    - `UNFINGERPRINTED`: the RECORD carries no fingerprint (`credential_fingerprint is None`).
      Recorded before fingerprinting existed -- the v14 backfill is the standing example, and it
      can never be given one retroactively; nothing observed which credential placed a 2026-07
      order. Treated as MATCHING (see `may_place_live_entry`), not as a mismatch: withholding
      permission here would veto the next live entry on a healthy unattended deployment, which is
      precisely the incident the v14 backfill exists to prevent.
    - `CREDENTIAL_UNREADABLE`: the record HAS a fingerprint, but the CURRENT credential could not
      be resolved (`current_fingerprint is None`) -- a locked keychain, a momentarily unreadable
      `.env`, a venue this process does not know how to fingerprint. This is a fact about the
      OBSERVER, not about the credential having changed, and it keeps the same discipline
      `venue_readiness.RECORD_UNREADABLE` already established at #624: "I could not resolve this"
      is not "this changed". Treated as MATCHING for the same reason -- a deployment that
      momentarily cannot read its own keychain is the same deployment the venue would still
      happily trade for.
    - `DIFFERENT_CREDENTIAL`: both fingerprints are known and they DIFFER. The only state that
      may withdraw permission -- a recorded fingerprint that positively disagrees with the
      credential in place now.
    """

    MATCHES = "matches"
    UNFINGERPRINTED = "unfingerprinted"
    CREDENTIAL_UNREADABLE = "credential_unreadable"
    DIFFERENT_CREDENTIAL = "different_credential"


@dataclass(frozen=True)
class VenueTradeScope:
    """One venue's trade-scope record: what the operator attested, and what the venue itself has
    since proven or refused.

    `attested_scope` is the operator's claim about what the credential can do --
    `TRADING`/`"trading"` or `READ_ONLY`/`"read_only"` -- and is `None` when nobody has attested
    (state is `UNVERIFIED`, or the record came from a backfill that inferred `CONFIRMED` straight
    from order history with no attestation in the loop at all).

    `refuted_reason` is free text from the venue's own refusal, kept only for the operator surface
    (`doctor`, `keel scope show`) to explain why a venue once lost trust -- it is never read
    by policy.

    `credential_fingerprint` (#633) is a non-reversible fingerprint
    (`keel_core.credential_identity.current_credential_fingerprint`) of the credential IDENTIFIER
    this evidence was collected under, or `None`. Deliberately has NO DEFAULT: every writer of
    this record must decide what to put here, because a default would let a future writer silently
    forget and quietly degrade detection back to the venue-only key this record used to have.
    """

    venue: str
    state: TradeScopeState
    attested_scope: str | None
    attested_ts: int | None
    confirmed_ts: int | None
    refuted_ts: int | None
    refuted_reason: str | None
    credential_fingerprint: str | None

    def credential_evidence(self, current_fingerprint: str | None) -> CredentialEvidence:
        """Compare this record's fingerprint against `current_fingerprint` -- the CURRENT
        credential's fingerprint, resolved by the caller
        (`keel_core.credential_identity.current_credential_fingerprint`). See `CredentialEvidence`
        for what each of the four results means and why there are four, not three.
        """
        if self.credential_fingerprint is None:
            return CredentialEvidence.UNFINGERPRINTED
        if current_fingerprint is None:
            return CredentialEvidence.CREDENTIAL_UNREADABLE
        if self.credential_fingerprint == current_fingerprint:
            return CredentialEvidence.MATCHES
        return CredentialEvidence.DIFFERENT_CREDENTIAL

    def may_place_live_entry(self, current_fingerprint: str | None) -> bool:
        """Whether a live ENTRY may be placed on this venue's credential.

        `current_fingerprint` is REQUIRED (no default) so mypy names every call site rather than
        letting one silently opt out of the #633 check by omitting the argument. Pass `None`
        explicitly when the caller does not (yet) resolve a real one -- PR1 of #633 does this for
        every DISPLAY call site (`venue_readiness`, `doctor`, `scope show`, `setup`'s inspection),
        deliberately: wiring the display to distinguish "different credential" from "never
        attested" before it CAN make that distinction correctly would reproduce #624, so it waits
        for PR2. `None` here always means "current credential unknown" and therefore never
        withdraws permission (see below) -- it is the safe default to pass, not merely the lazy
        one.

        First checks `credential_evidence`: `DIFFERENT_CREDENTIAL` withdraws permission
        UNCONDITIONALLY, regardless of what `state` says -- a `CONFIRMED` record whose credential
        has since changed is not evidence the venue would accept a placement on the NEW one.
        `UNFINGERPRINTED` and `CREDENTIAL_UNREADABLE` do NOT withdraw permission; they fall
        through to the state machine below exactly as if the fingerprint check did not exist,
        which is the fail-safe direction for both: an old backfilled record was never going to
        have a fingerprint, and an observer that cannot read its own keychain right now is not
        evidence the credential changed underneath it.

        Falls through to `state`, driven by `state` alone and not by `refuted_ts` -- unchanged
        from before #633. `refuted_ts` is history, never a veto: re-attesting over a refuted
        record is how an operator reports "I rotated the credential", and that re-attestation
        moves `state` back to `ATTESTED` -- but the record KEEPS `refuted_ts` so `doctor` can
        still say "you re-attested a venue that refuted a credential on <date>". If the predicate
        instead vetoed on `refuted_ts is not None`, the one path that is supposed to *recover*
        trade scope (re-attest after rotating) would never actually unblock it, since the old
        refusal timestamp would sit there forever overriding the new attestation. That failure
        mode -- a fixed credential permanently unable to trade because of a fact about the
        credential it replaced -- is exactly what driving this off `state` avoids.

        - `CONFIRMED`: the venue itself proved it by accepting a placement. True.
        - `ATTESTED` with `attested_scope == TRADING`: the operator's claim, not yet confirmed by
          the venue, but nothing has refused it. True.
        - `ATTESTED` with `attested_scope == READ_ONLY` (or anything else): the operator
          attested a credential that cannot place live orders. False.
        - `REFUTED`: the venue has refused a placement on this credential. False.
        - `UNVERIFIED`: nobody has attested anything. False.

        Fails closed on any state not listed above.
        """
        if self.credential_evidence(current_fingerprint) is CredentialEvidence.DIFFERENT_CREDENTIAL:
            return False
        if self.state is TradeScopeState.CONFIRMED:
            return True
        if self.state is TradeScopeState.ATTESTED:
            return self.attested_scope == TRADING
        return False


__all__ = [
    "READ_ONLY",
    "TRADING",
    "CredentialEvidence",
    "TradeScopeState",
    "VenueTradeScope",
]
