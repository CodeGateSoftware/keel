"""Tests for the subscription record's policy -- the fail-closed table from the design spec §7.

These are pure functions: no database, no config object, no rail. Every row of that table is a
test here, so rail 14's own tests do not have to re-derive the policy.
"""

from __future__ import annotations

import dataclasses
from decimal import Decimal

import pytest
from keel_core.subscription import BrokerSubscription, SubscriptionStatus

NOW = 1_800_000_000
UNSUBSCRIBED = Decimal("0")


def _record(
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE,
    free_volume_usd: Decimal | None = Decimal("10000"),
    attest_due_ts: int = NOW + 86_400,
) -> BrokerSubscription:
    return BrokerSubscription(
        venue="coinbase",
        tier_name="Preferred",
        free_volume_usd=free_volume_usd,
        pacing="opportunistic",
        subscription_usd_month=Decimal("29.99"),
        status=status,
        attested_at=NOW - 86_400,
        attest_due_ts=attest_due_ts,
    )


def test_active_allows_its_free_volume() -> None:
    assert _record().allowance_usd(NOW, UNSUBSCRIBED) == Decimal("10000")


def test_active_and_unlimited_returns_none() -> None:
    """Premium has no cap at all -- None, not a very large number."""
    assert _record(free_volume_usd=None).allowance_usd(NOW, UNSUBSCRIBED) is None


@pytest.mark.parametrize(
    "status", [SubscriptionStatus.SUSPECT, SubscriptionStatus.LAPSED]
)
def test_suspect_and_lapsed_fall_back_to_the_unsubscribed_allowance(
    status: SubscriptionStatus,
) -> None:
    assert _record(status=status).allowance_usd(NOW, Decimal("25")) == Decimal("25")


def test_unlimited_still_falls_back_when_suspect() -> None:
    """An unlimited allowance the user may no longer have is worth exactly nothing."""
    record = _record(status=SubscriptionStatus.SUSPECT, free_volume_usd=None)
    assert record.allowance_usd(NOW, Decimal("25")) == Decimal("25")


def test_overdue_attestation_overrides_a_stored_active_status() -> None:
    record = _record(attest_due_ts=NOW - 1)
    assert record.effective_status(NOW) is SubscriptionStatus.SUSPECT
    assert record.allowance_usd(NOW, Decimal("25")) == Decimal("25")


def test_due_exactly_now_is_already_overdue() -> None:
    """Boundary: due-at is the moment it expires, not one tick still-good.

    Matches `is_bypass_armed`'s strict `now_ts < armed_until` convention elsewhere.
    """
    assert _record(attest_due_ts=NOW).effective_status(NOW) is SubscriptionStatus.SUSPECT


def test_not_yet_due_keeps_the_stored_status() -> None:
    assert _record(attest_due_ts=NOW + 1).effective_status(NOW) is SubscriptionStatus.ACTIVE


def test_the_record_is_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        _record().status = SubscriptionStatus.LAPSED  # type: ignore[misc]
