"""The per-venue subscription record, and the policy turning it into a spend cap.

Coinbase exposes no subscription endpoint, so a subscription is *user-asserted* -- see the
broker-subscription design spec §3. This module holds what was asserted and decides what may be
spent against it.

The policy is deliberately here rather than in `execution/guards.py`: as a pure function of its
inputs it is testable with no database, no `OrderIntent`, and no rail around it, and rail 14 is
left with a single call. Every row of the spec's §7 table is a test in
`tests/test_subscription_record.py`.

Staleness is asymmetric, which is why the policy fails closed. An un-synced *upgrade*
under-permits: annoying, safe. An un-synced *downgrade or lapse* over-permits -- the engine
spends against an allowance the user does not have.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class SubscriptionStatus(str, Enum):
    """Whether the asserted subscription is still believed."""

    ACTIVE = "active"
    SUSPECT = "suspect"
    LAPSED = "lapsed"


@dataclass(frozen=True)
class BrokerSubscription:
    """One venue's subscription, as last attested by the user.

    `free_volume_usd is None` means an UNLIMITED fee-free allowance (Premium) -- there is no cap,
    which is not the same as a cap of zero.

    `tier_name` may be `'unknown'`, which `keel subscription set` produces: a raw cap the user
    asserted without naming a tier. It is visibly not an attestation.
    """

    venue: str
    tier_name: str
    free_volume_usd: Decimal | None
    pacing: str
    subscription_usd_month: Decimal
    status: SubscriptionStatus
    attested_at: int
    attest_due_ts: int

    def effective_status(self, now_ts: int) -> SubscriptionStatus:
        """The status actually in force, degrading an overdue attestation to `SUSPECT`.

        An attestation past its due date is an assertion nobody has re-confirmed, which is the
        same class of unknown as never having asserted at all. Due-at is the moment it expires,
        not one tick still-good.
        """
        if self.attest_due_ts <= now_ts:
            return SubscriptionStatus.SUSPECT
        return self.status

    def allowance_usd(
        self, now_ts: int, unsubscribed_allowance_usd: Decimal
    ) -> Decimal | None:
        """The spend cap rail 14 must enforce. `None` means unlimited (no cap at all).

        Anything other than an in-force `ACTIVE` falls back to `unsubscribed_allowance_usd` --
        including an unlimited tier, because an unlimited allowance the user may no longer hold
        is worth nothing.
        """
        if self.effective_status(now_ts) is SubscriptionStatus.ACTIVE:
            return self.free_volume_usd
        return unsubscribed_allowance_usd


__all__ = ["BrokerSubscription", "SubscriptionStatus"]
