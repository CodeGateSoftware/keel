"""Round-trip tests for the venue_trade_scopes repository methods."""

from __future__ import annotations

from keel_core.trade_scope import TRADING, TradeScopeState, VenueTradeScope

from keel.data import db
from keel.data.repository import Repository


def _repo() -> Repository:
    conn = db.connect(":memory:")
    db.migrate(conn)
    return Repository(conn)


def _record(venue: str = "coinbase", **overrides: object) -> VenueTradeScope:
    fields: dict[str, object] = {
        "venue": venue,
        "state": TradeScopeState.ATTESTED,
        "attested_scope": TRADING,
        "attested_ts": 1_800_000_000,
        "confirmed_ts": None,
        "refuted_ts": None,
        "refuted_reason": None,
        "credential_fingerprint": None,
    }
    fields.update(overrides)
    return VenueTradeScope(**fields)  # type: ignore[arg-type]


def test_missing_venue_returns_none() -> None:
    """No row means never recorded, which the caller must treat as unknown and closed."""
    assert _repo().get_venue_trade_scope("coinbase") is None


def test_upsert_then_get_round_trips_exactly() -> None:
    repo = _repo()
    record = _record()
    repo.upsert_venue_trade_scope(record)
    assert repo.get_venue_trade_scope("coinbase") == record


def test_confirmed_record_with_no_attestation_round_trips() -> None:
    """The backfilled shape: CONFIRMED, attested_scope/attested_ts both NULL."""
    repo = _repo()
    record = _record(
        state=TradeScopeState.CONFIRMED,
        attested_scope=None,
        attested_ts=None,
        confirmed_ts=1_700_000_000,
    )
    repo.upsert_venue_trade_scope(record)
    loaded = repo.get_venue_trade_scope("coinbase")
    assert loaded == record
    assert loaded is not None
    assert loaded.may_place_live_entry(None) is True


def test_refuted_ts_survives_a_reattestation_upsert() -> None:
    """Re-attesting keeps `refuted_ts` as history rather than clearing it -- the upsert must
    write whatever the caller passes, including a kept-around refuted_ts."""
    repo = _repo()
    repo.upsert_venue_trade_scope(
        _record(state=TradeScopeState.REFUTED, attested_scope=None, refuted_ts=1_750_000_000,
                refuted_reason="insufficient permissions")
    )
    reattested = _record(
        state=TradeScopeState.ATTESTED,
        attested_scope=TRADING,
        attested_ts=1_800_000_000,
        refuted_ts=1_750_000_000,
        refuted_reason="insufficient permissions",
    )
    repo.upsert_venue_trade_scope(reattested)

    loaded = repo.get_venue_trade_scope("coinbase")
    assert loaded == reattested
    assert loaded is not None
    assert loaded.refuted_ts == 1_750_000_000
    assert loaded.may_place_live_entry(None) is True


def test_upsert_replaces_in_place_keyed_on_venue() -> None:
    repo = _repo()
    repo.upsert_venue_trade_scope(_record())
    repo.upsert_venue_trade_scope(_record(state=TradeScopeState.REFUTED, attested_scope=None))

    assert len(repo.list_venue_trade_scopes()) == 1
    loaded = repo.get_venue_trade_scope("coinbase")
    assert loaded is not None
    assert loaded.state is TradeScopeState.REFUTED


def test_venues_are_independent() -> None:
    repo = _repo()
    repo.upsert_venue_trade_scope(_record(venue="coinbase"))
    repo.upsert_venue_trade_scope(_record(venue="kraken", state=TradeScopeState.UNVERIFIED,
                                           attested_scope=None))

    coinbase = repo.get_venue_trade_scope("coinbase")
    kraken = repo.get_venue_trade_scope("kraken")
    assert coinbase is not None and coinbase.state is TradeScopeState.ATTESTED
    assert kraken is not None and kraken.state is TradeScopeState.UNVERIFIED


def test_list_is_sorted_by_venue() -> None:
    repo = _repo()
    repo.upsert_venue_trade_scope(_record(venue="kraken"))
    repo.upsert_venue_trade_scope(_record(venue="coinbase"))
    assert [r.venue for r in repo.list_venue_trade_scopes()] == ["coinbase", "kraken"]


def test_state_round_trips_as_an_enum_not_a_string() -> None:
    repo = _repo()
    repo.upsert_venue_trade_scope(_record(state=TradeScopeState.REFUTED, attested_scope=None))
    loaded = repo.get_venue_trade_scope("coinbase")
    assert loaded is not None
    assert loaded.state is TradeScopeState.REFUTED


def test_credential_fingerprint_round_trips() -> None:
    repo = _repo()
    fingerprint = "a" * 32
    repo.upsert_venue_trade_scope(_record(credential_fingerprint=fingerprint))
    loaded = repo.get_venue_trade_scope("coinbase")
    assert loaded is not None
    assert loaded.credential_fingerprint == fingerprint


def test_credential_fingerprint_is_replaced_not_merged_on_reattestation() -> None:
    """`upsert_venue_trade_scope` writes exactly what it is given, including `None` -- a caller
    that re-attests with an unresolved current credential must be able to clear a stale
    fingerprint, not have the old one silently survive underneath a fresh write."""
    repo = _repo()
    repo.upsert_venue_trade_scope(_record(credential_fingerprint="a" * 32))
    repo.upsert_venue_trade_scope(_record(credential_fingerprint=None))
    loaded = repo.get_venue_trade_scope("coinbase")
    assert loaded is not None
    assert loaded.credential_fingerprint is None
