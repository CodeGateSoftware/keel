"""Round-trip tests for the broker_subscriptions repository methods."""

from __future__ import annotations

from decimal import Decimal

from keel_core.subscription import BrokerSubscription, SubscriptionStatus

from keel.data import db
from keel.data.repository import Repository


def _repo() -> Repository:
    conn = db.connect(":memory:")
    db.migrate(conn)
    return Repository(conn)


def _record(venue: str = "coinbase", **overrides: object) -> BrokerSubscription:
    fields: dict[str, object] = {
        "venue": venue,
        "tier_name": "Preferred",
        "free_volume_usd": Decimal("10000"),
        "pacing": "opportunistic",
        "subscription_usd_month": Decimal("29.99"),
        "status": SubscriptionStatus.ACTIVE,
        "attested_at": 1_800_000_000,
        "attest_due_ts": 1_800_000_000 + 31_536_000,
    }
    fields.update(overrides)
    return BrokerSubscription(**fields)  # type: ignore[arg-type]


def test_missing_venue_returns_none() -> None:
    """No row means never attested, which the caller must treat as unknown and closed."""
    assert _repo().get_broker_subscription("coinbase") is None


def test_upsert_then_get_round_trips_exactly() -> None:
    repo = _repo()
    record = _record()
    repo.upsert_broker_subscription(record)
    assert repo.get_broker_subscription("coinbase") == record


def test_decimals_survive_the_round_trip_without_drift() -> None:
    repo = _repo()
    repo.upsert_broker_subscription(_record(free_volume_usd=Decimal("10000.123456789")))
    loaded = repo.get_broker_subscription("coinbase")
    assert loaded is not None
    assert loaded.free_volume_usd == Decimal("10000.123456789")


def test_unlimited_round_trips_as_none_not_zero() -> None:
    repo = _repo()
    repo.upsert_broker_subscription(_record(tier_name="Premium", free_volume_usd=None))
    loaded = repo.get_broker_subscription("coinbase")
    assert loaded is not None
    assert loaded.free_volume_usd is None


def test_upsert_replaces_in_place_keyed_on_venue() -> None:
    repo = _repo()
    repo.upsert_broker_subscription(_record())
    repo.upsert_broker_subscription(_record(tier_name="Basic", free_volume_usd=Decimal("500")))

    assert len(repo.list_broker_subscriptions()) == 1
    loaded = repo.get_broker_subscription("coinbase")
    assert loaded is not None
    assert loaded.tier_name == "Basic"


def test_venues_are_independent() -> None:
    repo = _repo()
    repo.upsert_broker_subscription(_record(venue="coinbase"))
    repo.upsert_broker_subscription(_record(venue="kraken", tier_name="none"))

    coinbase = repo.get_broker_subscription("coinbase")
    kraken = repo.get_broker_subscription("kraken")
    assert coinbase is not None and coinbase.tier_name == "Preferred"
    assert kraken is not None and kraken.tier_name == "none"


def test_list_is_sorted_by_venue() -> None:
    repo = _repo()
    repo.upsert_broker_subscription(_record(venue="kraken"))
    repo.upsert_broker_subscription(_record(venue="coinbase"))
    assert [r.venue for r in repo.list_broker_subscriptions()] == ["coinbase", "kraken"]


def test_status_round_trips_as_an_enum_not_a_string() -> None:
    repo = _repo()
    repo.upsert_broker_subscription(_record(status=SubscriptionStatus.SUSPECT))
    loaded = repo.get_broker_subscription("coinbase")
    assert loaded is not None
    assert loaded.status is SubscriptionStatus.SUSPECT
