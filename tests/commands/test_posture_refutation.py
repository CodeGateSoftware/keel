"""Venue evidence must REFUTE a standing attestation, not coexist with it -- #691.

The acceptance criterion: "an INTX portfolio found at build refutes a standing attestation rather
than silently coexisting with it". Without this, an operator's honest-at-the-time claim outlives
the fact that contradicted it -- the record still reads ATTESTED, `doctor` still shows a green
posture, and the only thing standing between that and a live entry is an exception nobody wrote
down.

Refutation is deliberately NOT symmetric with attestation: a human may issue a claim, and only
the venue may withdraw one. `refute_posture` cannot create a record from nothing -- refuting
something never claimed would invent a history, and the correct state for "no claim, venue looks
wrong" is still "no claim", which rail 22 already vetoes.
"""

from __future__ import annotations

import pytest
from keel_core.cash_posture import SPOT_CASH, CashPostureState, VenueCashPosture

from keel.commands.posture import refute_posture
from keel.data.db import connect, migrate
from keel.data.repository import Repository

NOW = 1_800_000_000


@pytest.fixture()
def repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    return Repository(conn)


def _attested(repo: Repository) -> None:
    repo.upsert_venue_cash_posture(
        VenueCashPosture(
            venue="coinbase",
            state=CashPostureState.ATTESTED,
            attested_posture=SPOT_CASH,
            attested_ts=NOW - 100,
            attest_due_ts=NOW + 100,
            refuted_ts=None,
            refuted_reason=None,
            credential_fingerprint="fp-1",
        )
    )


def test_venue_evidence_moves_a_standing_claim_to_refuted(repo: Repository) -> None:
    _attested(repo)
    assert refute_posture(repo, venue="coinbase", reason="INTX portfolio present", now_ts=NOW)
    record = repo.get_venue_cash_posture("coinbase")
    assert record.state is CashPostureState.REFUTED
    assert record.refuted_ts == NOW
    assert record.refuted_reason == "INTX portfolio present"


def test_the_claim_that_was_refuted_is_preserved(repo: Repository) -> None:
    """`doctor` has to be able to say WHAT was claimed and when the venue contradicted it. An
    operator asked to re-attest deserves to know what they said last time."""
    _attested(repo)
    refute_posture(repo, venue="coinbase", reason="INTX portfolio present", now_ts=NOW)
    record = repo.get_venue_cash_posture("coinbase")
    assert record.attested_posture == SPOT_CASH
    assert record.attested_ts == NOW - 100
    assert record.credential_fingerprint == "fp-1"


def test_refuting_what_was_never_claimed_writes_nothing(repo: Repository) -> None:
    """Not symmetric with attestation, on purpose. A human issues a claim; only the venue
    withdraws one. Creating a REFUTED record from no record would invent a history, and "no
    claim" is already a veto -- there is nothing to improve by fabricating one."""
    assert not refute_posture(repo, venue="coinbase", reason="INTX", now_ts=NOW)
    assert repo.get_venue_cash_posture("coinbase") is None


def test_refuting_twice_keeps_the_first_refutation_time(repo: Repository) -> None:
    """The first contradiction is when the claim stopped being true as far as keel can tell.
    Advancing it on every build would report the most recent cycle rather than the discovery."""
    _attested(repo)
    refute_posture(repo, venue="coinbase", reason="INTX portfolio present", now_ts=NOW)
    refute_posture(repo, venue="coinbase", reason="INTX portfolio present", now_ts=NOW + 5000)
    assert repo.get_venue_cash_posture("coinbase").refuted_ts == NOW


def test_a_re_attestation_after_a_refutation_can_be_refuted_again(repo: Repository) -> None:
    """The full loop: attest, refuted, re-attest, refuted again. The second refutation is a NEW
    discovery about a NEW claim, so its timestamp does move."""
    _attested(repo)
    refute_posture(repo, venue="coinbase", reason="INTX", now_ts=NOW)
    repo.upsert_venue_cash_posture(
        VenueCashPosture(
            venue="coinbase",
            state=CashPostureState.ATTESTED,
            attested_posture=SPOT_CASH,
            attested_ts=NOW + 1000,
            attest_due_ts=NOW + 9000,
            refuted_ts=NOW,
            refuted_reason="INTX",
            credential_fingerprint="fp-1",
        )
    )
    refute_posture(repo, venue="coinbase", reason="INTX again", now_ts=NOW + 2000)
    record = repo.get_venue_cash_posture("coinbase")
    assert record.state is CashPostureState.REFUTED
    assert record.refuted_ts == NOW + 2000
    assert record.refuted_reason == "INTX again"


def test_only_the_named_venue_is_refuted(repo: Repository) -> None:
    _attested(repo)
    refute_posture(repo, venue="alpaca", reason="multiplier 4", now_ts=NOW)
    assert repo.get_venue_cash_posture("coinbase").state is CashPostureState.ATTESTED
