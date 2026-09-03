"""`keel posture` -- the operator surface for the cash posture no venue will affirm (#691).

Mirrors `keel scope`'s vocabulary deliberately: `attest` writes the claim, `show` reports it, the
venue resolves the same way, and re-attesting over a refutation is allowed because that is how an
operator reports "I closed the derivative portfolio". A new vocabulary for the same shape would be
a second thing to learn and a second thing to get wrong.

The two that carry it:

* `test_attesting_stamps_a_due_date` -- this record's whole difference from trade scope is that it
  EXPIRES, because nothing re-confirms it. An attestation written without a due date would never
  expire and the TTL would be decorative.
* `test_the_due_date_is_stored_not_derived` -- so that changing `ATTESTATION_TTL_SEC` later cannot
  retroactively expire (or silently extend) a claim a human made under the window in force when
  they made it.
"""

from __future__ import annotations

import pytest
from keel_core.cash_posture import (
    ATTESTATION_TTL_SEC,
    MARGIN_ENABLED,
    SPOT_CASH,
    CashPostureState,
    VenueCashPosture,
)

from keel.commands.posture import apply_posture_attest, posture_show_lines
from keel.data.db import connect, migrate
from keel.data.repository import Repository

NOW = 1_800_000_000


@pytest.fixture()
def repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    return Repository(conn)


def test_attesting_spot_cash_records_an_attested_record(repo: Repository) -> None:
    line = apply_posture_attest(repo, venue="coinbase", spot_cash=True, now_ts=NOW)
    record = repo.get_venue_cash_posture("coinbase")
    assert record.state is CashPostureState.ATTESTED
    assert record.attested_posture == SPOT_CASH
    assert record.attested_ts == NOW
    assert "coinbase" in line and "spot" in line.lower()


def test_attesting_margin_records_it_rather_than_refusing_to_write(repo: Repository) -> None:
    """An operator whose account HAS margin must be able to say so. Refusing to record it would
    leave the record absent, which reads as "nobody has attested" -- a less useful fact, and one
    that invites the same person to be asked again next week."""
    line = apply_posture_attest(repo, venue="coinbase", spot_cash=False, now_ts=NOW)
    record = repo.get_venue_cash_posture("coinbase")
    assert record.state is CashPostureState.ATTESTED
    assert record.attested_posture == MARGIN_ENABLED
    assert "margin" in line.lower()
    assert "veto" in line.lower()


def test_attesting_stamps_a_due_date(repo: Repository) -> None:
    apply_posture_attest(repo, venue="coinbase", spot_cash=True, now_ts=NOW)
    assert repo.get_venue_cash_posture("coinbase").attest_due_ts == NOW + ATTESTATION_TTL_SEC


def test_the_due_date_is_stored_not_derived(repo: Repository) -> None:
    """Stored, so shortening the TTL later cannot retroactively expire a claim a human made under
    a longer window -- or lengthening it silently revive one that had already lapsed."""
    apply_posture_attest(repo, venue="coinbase", spot_cash=True, now_ts=NOW)
    due = repo.get_venue_cash_posture("coinbase").attest_due_ts
    assert due is not None
    assert repo.get_venue_cash_posture("coinbase").is_current(due - 1)
    assert not repo.get_venue_cash_posture("coinbase").is_current(due)


def test_the_confirmation_line_says_which_way_the_rail_will_go(repo: Repository) -> None:
    cash = apply_posture_attest(repo, venue="coinbase", spot_cash=True, now_ts=NOW)
    margin = apply_posture_attest(repo, venue="alpaca", spot_cash=False, now_ts=NOW)
    assert "may now place live ENTRIES" in cash
    assert "veto" in margin.lower()


def test_re_attesting_over_a_refutation_recovers_and_reports_the_history(
    repo: Repository,
) -> None:
    """The recovery path. `refuted_ts`/`refuted_reason` are carried FORWARD, not cleared: they
    are history about this venue, and `doctor` must still be able to say the venue once
    contradicted a claim here."""
    repo.upsert_venue_cash_posture(
        VenueCashPosture(
            venue="coinbase",
            state=CashPostureState.REFUTED,
            attested_posture=SPOT_CASH,
            attested_ts=NOW - 1000,
            attest_due_ts=NOW + 1000,
            refuted_ts=NOW - 500,
            refuted_reason="INTX portfolio present",
            credential_fingerprint=None,
        )
    )
    line = apply_posture_attest(repo, venue="coinbase", spot_cash=True, now_ts=NOW)
    record = repo.get_venue_cash_posture("coinbase")
    assert record.state is CashPostureState.ATTESTED
    assert record.refuted_ts == NOW - 500
    assert record.refuted_reason == "INTX portfolio present"
    assert "refuted" in line.lower() or "contradicted" in line.lower()


def test_the_fingerprint_is_stamped_fresh_not_carried_forward(
    repo: Repository, monkeypatch
) -> None:
    """#633. The operator is attesting about the credential in place NOW; carrying an old
    fingerprint forward would bind a fresh claim to a possibly-rotated-away credential."""
    repo.upsert_venue_cash_posture(
        VenueCashPosture(
            venue="coinbase",
            state=CashPostureState.ATTESTED,
            attested_posture=SPOT_CASH,
            attested_ts=NOW - 10,
            attest_due_ts=NOW + 10,
            refuted_ts=None,
            refuted_reason=None,
            credential_fingerprint="fp-old",
        )
    )
    monkeypatch.setattr(
        "keel.commands.posture.current_credential_fingerprint", lambda _v: "fp-new"
    )
    apply_posture_attest(repo, venue="coinbase", spot_cash=True, now_ts=NOW)
    assert repo.get_venue_cash_posture("coinbase").credential_fingerprint == "fp-new"


# --- show -------------------------------------------------------------------------------------


def test_show_says_nothing_is_attested_when_nothing_is(repo: Repository) -> None:
    lines = posture_show_lines(repo, now_ts=NOW)
    assert any("no venue" in line.lower() or "never" in line.lower() for line in lines)


def test_show_reports_the_posture_and_its_expiry(repo: Repository) -> None:
    apply_posture_attest(repo, venue="coinbase", spot_cash=True, now_ts=NOW)
    text = "\n".join(posture_show_lines(repo, now_ts=NOW))
    assert "coinbase" in text
    assert "spot_cash" in text
    assert "expires" in text.lower()


def test_show_marks_an_expired_attestation_as_expired(repo: Repository) -> None:
    """The distinction an operator needs: re-attest, versus fix the account."""
    apply_posture_attest(repo, venue="coinbase", spot_cash=True, now_ts=NOW)
    text = "\n".join(posture_show_lines(repo, now_ts=NOW + ATTESTATION_TTL_SEC + 1))
    assert "EXPIRED" in text


def test_show_names_the_venue_evidence_that_refuted_a_claim(repo: Repository) -> None:
    repo.upsert_venue_cash_posture(
        VenueCashPosture(
            venue="coinbase",
            state=CashPostureState.REFUTED,
            attested_posture=SPOT_CASH,
            attested_ts=NOW - 10,
            attest_due_ts=NOW + 10,
            refuted_ts=NOW,
            refuted_reason="INTX portfolio present",
            credential_fingerprint=None,
        )
    )
    text = "\n".join(posture_show_lines(repo, now_ts=NOW))
    assert "INTX portfolio present" in text
