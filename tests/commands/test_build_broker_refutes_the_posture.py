"""Venue evidence found at broker-build must refute the standing attestation -- #691.

Without this the acceptance criterion is unmet and `refute_posture` has no caller: an operator's
honest-at-the-time claim outlives the fact that contradicted it, the record still reads ATTESTED,
`doctor` still shows it green, and the only thing between that and a live entry is an exception
nobody wrote down.

`repo` is OPTIONAL on `_build_broker` on purpose. Most call sites are read-only inspection
(`keel balances`, `keel brokers list`) with no repository in hand, and threading one through every
one of them to record a fact none of them will act on would be churn. The paths that matter are
the ones that go on to trade.

**The refusal still propagates.** Recording it is in addition to failing closed, never instead --
`test_the_refusal_still_raises` pins that, because a refutation that swallowed the exception would
turn a hard stop into a database row.
"""

from __future__ import annotations

import pytest
from keel_core.cash_posture import SPOT_CASH, CashPostureState, VenueCashPosture

from keel.commands._common import record_cash_posture_refutation
from keel.data.db import connect, migrate
from keel.data.repository import Repository

NOW = 1_800_000_000


@pytest.fixture()
def repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    repo = Repository(conn)
    repo.upsert_venue_cash_posture(
        VenueCashPosture(
            venue="coinbase",
            state=CashPostureState.ATTESTED,
            attested_posture=SPOT_CASH,
            attested_ts=NOW,
            attest_due_ts=NOW + 1000,
            refuted_ts=None,
            refuted_reason=None,
            credential_fingerprint=None,
        )
    )
    return repo


class _Refusing:
    """A broker whose posture check refuses, as `CoinbaseAdapter` does on an INTX portfolio."""

    def verify_cash_account(self) -> None:
        raise RuntimeError("this account holds an INTX (perpetuals) portfolio")


class _Clean:
    def verify_cash_account(self) -> None:
        return None


def test_a_refusal_marks_the_standing_attestation_refuted(repo: Repository) -> None:
    with pytest.raises(RuntimeError):
        record_cash_posture_refutation(
            _Refusing(), repo=repo, venue="coinbase", now_ts=NOW + 10
        )
    record = repo.get_venue_cash_posture("coinbase")
    assert record.state is CashPostureState.REFUTED
    assert "INTX" in record.refuted_reason


def test_the_refusal_still_raises(repo: Repository) -> None:
    """Recording is IN ADDITION to failing closed. Swallowing the exception would turn a hard
    stop into a database row -- the account would still be wrong and the build would proceed."""
    with pytest.raises(RuntimeError, match="INTX"):
        record_cash_posture_refutation(_Refusing(), repo=repo, venue="coinbase", now_ts=NOW)


def test_a_clean_check_leaves_the_attestation_alone(repo: Repository) -> None:
    """REFUTE-ONLY: a check that finds no contradiction is not evidence of a cash posture, so it
    must not touch the record -- and certainly must not promote it."""
    record_cash_posture_refutation(_Clean(), repo=repo, venue="coinbase", now_ts=NOW)
    assert repo.get_venue_cash_posture("coinbase").state is CashPostureState.ATTESTED


def test_no_repo_still_runs_the_check_and_still_raises() -> None:
    """`repo=None` is the read-only call sites. The posture check must still run and still
    refuse -- only the RECORDING is optional."""
    with pytest.raises(RuntimeError, match="INTX"):
        record_cash_posture_refutation(_Refusing(), repo=None, venue="coinbase", now_ts=NOW)


def test_no_repo_and_a_clean_check_is_a_no_op() -> None:
    record_cash_posture_refutation(_Clean(), repo=None, venue="coinbase", now_ts=NOW)


def test_nothing_attested_records_nothing_and_still_raises(repo: Repository) -> None:
    """`refute_posture` refuses to invent a record, and this seam must not work around that."""
    repo._conn.execute("DELETE FROM venue_cash_postures")  # noqa: SLF001
    repo._conn.commit()  # noqa: SLF001
    with pytest.raises(RuntimeError):
        record_cash_posture_refutation(_Refusing(), repo=repo, venue="coinbase", now_ts=NOW)
    assert repo.get_venue_cash_posture("coinbase") is None
