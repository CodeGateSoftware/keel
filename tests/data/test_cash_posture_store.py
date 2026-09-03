"""Storage for the per-venue cash-posture record -- issue #691.

Mirrors `venue_trade_scopes` deliberately: one row per venue, `None` from the read meaning
"never recorded" rather than an error, and `credential_fingerprint` written exactly as given
including `None`. A second storage idiom for a record that behaves the same way would be a
second thing to get wrong.

The one that matters is `test_a_refutation_preserves_the_attestation_history`: the record has to
keep `attested_ts` and the posture claimed when the venue refutes it, or `doctor` cannot say WHAT
was claimed and when -- and an operator asked to re-attest deserves to be told what they said
last time.
"""

from __future__ import annotations

import pytest
from keel_core.cash_posture import SPOT_CASH, CashPostureState, VenueCashPosture

from keel.data.db import SCHEMA_VERSION, connect, migrate
from keel.data.repository import Repository

NOW = 1_800_000_000


@pytest.fixture()
def repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    return Repository(conn)


def _record(
    venue: str = "coinbase",
    state: CashPostureState = CashPostureState.ATTESTED,
    posture: str | None = SPOT_CASH,
    fingerprint: str | None = "fp-1",
    refuted_ts: int | None = None,
) -> VenueCashPosture:
    return VenueCashPosture(
        venue=venue,
        state=state,
        attested_posture=posture,
        attested_ts=NOW,
        attest_due_ts=NOW + 90 * 86400,
        refuted_ts=refuted_ts,
        refuted_reason="INTX portfolio present" if refuted_ts else None,
        credential_fingerprint=fingerprint,
    )


def test_the_schema_carries_the_table() -> None:
    conn = connect(":memory:")
    migrate(conn)
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "venue_cash_postures" in names


def test_an_existing_database_gains_the_table_on_migration() -> None:
    conn = connect(":memory:")
    migrate(conn)
    conn.execute(f"UPDATE schema_version SET version = {SCHEMA_VERSION - 1}")
    conn.execute("DROP TABLE venue_cash_postures")
    conn.commit()
    migrate(conn)
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "venue_cash_postures" in names


def test_an_unrecorded_venue_reads_as_none(repo: Repository) -> None:
    """`None` is meaningful: nobody has attested, so callers treat it as unknown and therefore
    closed. Same convention as `get_venue_trade_scope`."""
    assert repo.get_venue_cash_posture("coinbase") is None


def test_a_record_round_trips(repo: Repository) -> None:
    repo.upsert_venue_cash_posture(_record())
    got = repo.get_venue_cash_posture("coinbase")
    assert got == _record()


def test_the_record_is_keyed_on_venue_alone(repo: Repository) -> None:
    repo.upsert_venue_cash_posture(_record(venue="coinbase"))
    repo.upsert_venue_cash_posture(_record(venue="alpaca", posture=None,
                                           state=CashPostureState.UNVERIFIED))
    assert repo.get_venue_cash_posture("coinbase").attested_posture == SPOT_CASH
    assert repo.get_venue_cash_posture("alpaca").attested_posture is None


def test_re_attesting_replaces_in_place(repo: Repository) -> None:
    repo.upsert_venue_cash_posture(_record())
    repo.upsert_venue_cash_posture(_record(fingerprint="fp-2"))
    assert len(repo.list_venue_cash_postures()) == 1
    assert repo.get_venue_cash_posture("coinbase").credential_fingerprint == "fp-2"


def test_a_none_fingerprint_is_written_not_ignored(repo: Repository) -> None:
    """`None` means "recorded without fingerprinting", which is a value, not "leave what was
    there". A writer that could not clear it would let a stale fingerprint outlive its record."""
    repo.upsert_venue_cash_posture(_record(fingerprint="fp-1"))
    repo.upsert_venue_cash_posture(_record(fingerprint=None))
    assert repo.get_venue_cash_posture("coinbase").credential_fingerprint is None


def test_a_refutation_preserves_the_attestation_history(repo: Repository) -> None:
    """`doctor` has to be able to say what was claimed, when, and when the venue contradicted
    it. An operator asked to re-attest deserves to know what they said last time."""
    repo.upsert_venue_cash_posture(_record())
    repo.upsert_venue_cash_posture(
        _record(state=CashPostureState.REFUTED, refuted_ts=NOW + 100)
    )
    got = repo.get_venue_cash_posture("coinbase")
    assert got.state is CashPostureState.REFUTED
    assert got.attested_ts == NOW
    assert got.attested_posture == SPOT_CASH
    assert got.refuted_ts == NOW + 100
    assert got.refuted_reason == "INTX portfolio present"


def test_listing_is_ordered_by_venue(repo: Repository) -> None:
    for venue in ("robinhood", "alpaca", "coinbase"):
        repo.upsert_venue_cash_posture(_record(venue=venue))
    assert [r.venue for r in repo.list_venue_cash_postures()] == [
        "alpaca",
        "coinbase",
        "robinhood",
    ]


def test_the_state_is_read_back_as_an_enum_not_a_string(repo: Repository) -> None:
    repo.upsert_venue_cash_posture(_record())
    assert repo.get_venue_cash_posture("coinbase").state is CashPostureState.ATTESTED
