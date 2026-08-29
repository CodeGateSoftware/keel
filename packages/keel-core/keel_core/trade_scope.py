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
    """

    venue: str
    state: TradeScopeState
    attested_scope: str | None
    attested_ts: int | None
    confirmed_ts: int | None
    refuted_ts: int | None
    refuted_reason: str | None

    def may_place_live_entry(self) -> bool:
        """Whether a live ENTRY may be placed on this venue's credential.

        Driven by `state` alone, not by `refuted_ts`. `refuted_ts` is history, never a veto:
        re-attesting over a refuted record is how an operator reports "I rotated the credential",
        and that re-attestation moves `state` back to `ATTESTED` -- but the record KEEPS
        `refuted_ts` so `doctor` can still say "you re-attested a venue that refuted a credential
        on <date>". If the predicate instead vetoed on `refuted_ts is not None`, the one path that
        is supposed to *recover* trade scope (re-attest after rotating) would never actually
        unblock it, since the old refusal timestamp would sit there forever overriding the new
        attestation. That failure mode -- a fixed credential permanently unable to trade because
        of a fact about the credential it replaced -- is exactly what driving this off `state`
        avoids.

        - `CONFIRMED`: the venue itself proved it by accepting a placement. True.
        - `ATTESTED` with `attested_scope == TRADING`: the operator's claim, not yet confirmed by
          the venue, but nothing has refused it. True.
        - `ATTESTED` with `attested_scope == READ_ONLY` (or anything else): the operator
          attested a credential that cannot place live orders. False.
        - `REFUTED`: the venue has refused a placement on this credential. False.
        - `UNVERIFIED`: nobody has attested anything. False.

        Fails closed on any state not listed above.
        """
        if self.state is TradeScopeState.CONFIRMED:
            return True
        if self.state is TradeScopeState.ATTESTED:
            return self.attested_scope == TRADING
        return False


__all__ = [
    "READ_ONLY",
    "TRADING",
    "TradeScopeState",
    "VenueTradeScope",
]
