"""When each attestation expires, in one place (#793).

Live stopped placing orders on 2026-08-25 and nobody noticed for 17 days. The exposure caps were
the primary cause, but rail 17 expired on 09-08 and stacked on top -- and nothing anywhere said
so. keel fails closed on a stale attestation, which is right; it failed closed SILENTLY, which is
the hazard this module exists to remove.

**The model comes before the surfaces, because there were already four TTLs and one of them
lied.** `doctor.TTL_SEC` was `7 * 86_400` under a comment reading "Rail 17's TTL is the
executor's constant; doctor only READS it" -- it did not read it, and changing the rail's TTL
left doctor reporting the old one. A banner, a doctor state and a webhook built on that would
have been a fifth copy across two incompatible shapes.

So every test below is about the model answering for BOTH shapes -- rail 17 derives freshness
from a TTL, rail 22 stores a due date -- and about no surface ever computing one itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from keel import attestations
from keel.commands import doctor
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.execution import executor

NOW = 1_789_000_000


@pytest.fixture
def repo(tmp_path):
    conn = connect(str(tmp_path / "keel.db"))
    migrate(conn)
    return Repository(conn)


def test_a_survey_covers_every_attestation_a_rail_can_veto_on() -> None:
    """A surface renders what the survey returns, so an attestation missing here is one no banner
    can ever show. Named individually: adding a rail that vetoes on an attestation means adding
    it here, and deleting a line that says it was covered."""
    keys = {a.key for a in attestations.survey_definitions()}

    assert keys == {"withdrawal", "cash_posture"}


def test_rail_17_expiry_comes_from_the_executors_constant_and_is_not_restated() -> None:
    """**The acceptance criterion for the whole issue.**

    `doctor.TTL_SEC` was a duplicate of this number under a comment claiming it was an import.
    If the survey restates it, the same drift returns one module over -- so this MUTATES the
    rail's constant and requires the computed expiry to move with it."""
    original = executor.WITHDRAWAL_ATTESTATION_TTL_SEC
    try:
        executor.WITHDRAWAL_ATTESTATION_TTL_SEC = 3 * 86_400
        shifted = attestations.withdrawal_expiry(attested_at=NOW)
        assert shifted == NOW + 3 * 86_400, "the survey restates the TTL instead of reading it"
    finally:
        executor.WITHDRAWAL_ATTESTATION_TTL_SEC = original

    assert attestations.withdrawal_expiry(attested_at=NOW) == NOW + original


def test_an_unattested_rail_reports_missing_rather_than_expired(repo) -> None:
    """ "Nobody has ever attested" and "the attestation went stale" are different facts, and
    `_withdrawals_enabled` already keeps them apart in the veto message. A banner that called the
    first one "expired" would send an operator looking for a lapse that never happened."""
    found = {a.key: a for a in attestations.survey(repo, now_ts=NOW)}

    assert found["withdrawal"].state(NOW) == attestations.MISSING
    assert found["withdrawal"].attested_at is None
    assert found["withdrawal"].expires_at is None
    assert found["withdrawal"].seconds_left(NOW) is None


@pytest.mark.parametrize(
    ("age_days", "expected"),
    [
        (0, attestations.OK),
        (4, attestations.OK),
        (5.1, attestations.EXPIRING),  # inside the final 48h of a 7-day TTL
        (6.9, attestations.EXPIRING),
        (7.1, attestations.EXPIRED),
        (30, attestations.EXPIRED),
    ],
)
def test_rail_17_passes_through_ok_then_expiring_then_expired(repo, age_days, expected) -> None:
    """**The state that did not exist before: `EXPIRING`.**

    An attestation was `ok` until the instant it was refused, so the only warning an operator
    ever got was the halt itself. The window is 48h, which for a 7-day TTL means the last two
    days of every week."""
    repo.set_state("withdrawals_attested_at", int(NOW - age_days * 86_400))
    repo.set_state("withdrawals_enabled", True)

    found = {a.key: a for a in attestations.survey(repo, now_ts=NOW)}
    assert found["withdrawal"].state(NOW) == expected


def test_rail_22_reports_from_its_STORED_due_date(repo) -> None:
    """Rail 22 does not derive freshness -- `VenueCashPosture.attest_due_ts` is written when the
    attestation is made, because "a record with no due date is a claim that never expires". The
    survey reads that rather than re-deriving it from the 90-day TTL, so the two shapes come back
    the same without either being converted into the other."""
    from keel_core.cash_posture import CashPostureState, VenueCashPosture

    repo.upsert_venue_cash_posture(
        VenueCashPosture(
            venue="coinbase",
            state=CashPostureState.ATTESTED,
            attested_posture="spot_cash",
            attested_ts=NOW - 86_400,
            attest_due_ts=NOW + 3_600,  # an hour left: inside the window whatever the TTL is
            refuted_ts=None,
            refuted_reason=None,
            credential_fingerprint=None,
        )
    )

    found = {a.key: a for a in attestations.survey(repo, now_ts=NOW)}
    assert found["cash_posture"].expires_at == NOW + 3_600
    assert found["cash_posture"].state(NOW) == attestations.EXPIRING
    assert found["cash_posture"].seconds_left(NOW) == 3_600


def test_every_attestation_names_the_command_that_refreshes_it() -> None:
    """The remedy is a TTY command the browser cannot run -- rails 17 and 22 are Tier 2 and
    deliberately not admitted to it. So the surface has to TELL the operator what to type, and a
    banner that said "expired" without saying what to do would be an alarm with no action."""
    for definition in attestations.survey_definitions():
        assert definition.remedy.startswith("keel "), definition.key
        assert definition.rail in {"17", "22"}, definition.key
        assert definition.label, definition.key


def test_each_attestation_warns_on_a_window_proportional_to_its_own_life() -> None:
    """**Not one global window, which the first draft had.**

    48 hours is right for rail 17's 7-day TTL and is no notice at all on rail 22's 90-day one.
    `doctor` already knew that -- it warns rail 22 at `ATTESTATION_TTL_SEC // 6` and says the
    window is "proportional to" a fixed TTL -- so the window belongs to the attestation, and
    rail 22's is READ from the same constant rather than becoming a second opinion about it."""
    from keel_core.cash_posture import ATTESTATION_TTL_SEC

    found = {a.key: a for a in attestations.survey_definitions()}

    assert found["withdrawal"].warn_within_sec == 48 * 3_600
    assert found["cash_posture"].warn_within_sec == ATTESTATION_TTL_SEC // 6
    assert found["cash_posture"].warn_within_sec > found["withdrawal"].warn_within_sec


def test_seconds_left_never_goes_negative(repo) -> None:
    """An expired attestation reports zero, not a negative countdown a surface might render as
    "expires in -3 days"."""
    repo.set_state("withdrawals_attested_at", NOW - 30 * 86_400)
    repo.set_state("withdrawals_enabled", True)

    found = {a.key: a for a in attestations.survey(repo, now_ts=NOW)}
    assert found["withdrawal"].seconds_left(NOW) == 0


# -- the same answer on every surface -------------------------------------------------------


def test_doctor_reads_rail_17s_TTL_from_the_executor_rather_than_restating_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The point of the whole module, applied to the surface that had the duplicate.

    `doctor.TTL_SEC` was `7 * 86_400` under a comment reading "doctor only READS it". Shorten
    the executor's TTL to 3 days and an attestation made 4 days ago is expired; if doctor is
    still holding its own copy of the number it reports 3 days remaining and says `ok`."""
    from keel.execution import executor

    source = Path(executor.__file__).read_text(encoding="utf-8")
    monkeypatch.setattr(executor, "WITHDRAWAL_ATTESTATION_TTL_SEC", 3 * 86_400)
    assert "WITHDRAWAL_ATTESTATION_TTL_SEC = 7 * 24 * 3600" in source, "the constant moved"

    (rail17,) = [
        f
        for f in doctor.attestation_findings(
            subscription=None, withdrawals_attested_at=NOW - 4 * 86_400, now_ts=NOW
        )
        if f.name == "attest.withdrawals"
    ]
    assert rail17.status == doctor.FAIL
    # The DAYS too, not just the verdict. `ttl_sec` feeds every number in doctor's sentence, and
    # a local `7 * 86_400` there leaves the state right (it comes from the model) while the
    # sentence reads "expired 0 day(s) ago" on a 3-day TTL. Measured: the mutant survived an
    # assertion that only checked the status and the word "expired".
    assert "expired 1 day(s) ago" in rail17.detail


@pytest.mark.parametrize(
    ("hours_left", "expected"),
    [(72, attestations.OK), (60, attestations.OK), (47, attestations.EXPIRING)],
)
def test_doctor_starts_warning_on_rail_17_exactly_when_the_banner_does(
    hours_left: int, expected: str
) -> None:
    """The divergence this feature exists to prevent, in the one place it already existed.

    Doctor warned at `_days(remaining) <= 2`, which truncation makes anything under 72 hours;
    the banner's window is 48. Between those two an operator saw a doctor WARNING and a cockpit
    that called the same attestation healthy. One window, read from the attestation."""
    attested_at = NOW + hours_left * 3_600 - attestations.withdrawal_expiry(attested_at=0)
    (rail17,) = [
        f
        for f in doctor.attestation_findings(
            subscription=None, withdrawals_attested_at=attested_at, now_ts=NOW
        )
        if f.name == "attest.withdrawals"
    ]
    model = attestations.Attestation(
        key="withdrawal",
        rail="17",
        label="",
        remedy="",
        attested_at=attested_at,
        expires_at=attestations.withdrawal_expiry(attested_at=attested_at),
    ).state(NOW)

    assert model == expected
    assert rail17.status == (doctor.WARN if expected == attestations.EXPIRING else doctor.OK)


# -- the verdict, not just the clock -----------------------------------------------------------


def test_a_suspended_attestation_is_not_a_healthy_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """Found while wiring doctor, and it is the same lie one column over.

    Rail 17 reads TWO keys -- `withdrawals_attested_at` and `withdrawals_enabled` -- and vetoes
    on `enabled is False` with its own sentence ("withdrawals are suspended/restricted for this
    account"). `attestation_findings` was handed the timestamp alone, so an operator who ran
    `keel withdrawals attest --disabled` because their broker had frozen withdrawals got
    `ok · withdrawal capability attested · 6 day(s) remain` from `keel doctor` while every BUY
    was refused. `keel status` never had this bug: it resolves through the executor's own
    `_withdrawals_enabled`."""
    conn = connect(":memory:")
    migrate(conn)
    repo = Repository(conn)
    repo.set_state("withdrawals_attested_at", str(NOW - 86_400))
    repo.set_state("withdrawals_enabled", False)

    (withdrawal, _posture) = attestations.survey(repo, NOW)
    assert withdrawal.state(NOW) == attestations.REFUSED

    (rail17,) = [
        f
        for f in doctor.attestation_findings(
            subscription=None,
            withdrawals_attested_at=NOW - 86_400,
            withdrawals_enabled=False,
            now_ts=NOW,
        )
        if f.name == "attest.withdrawals"
    ]
    assert rail17.status == doctor.FAIL
    assert "suspend" in rail17.headline.lower()


def test_the_verdict_does_not_outrank_the_clock() -> None:
    """An expired "no" reports as EXPIRED, not REFUSED: what an operator must do about it is
    re-attest either way, and `expired` is the word that says the reading is stale."""
    definition = attestations.definition("withdrawal")
    stale = attestations.Attestation(
        key=definition.key,
        rail=definition.rail,
        label=definition.label,
        remedy=definition.remedy,
        attested_at=NOW - 9 * 86_400,
        expires_at=NOW - 2 * 86_400,
        satisfied=False,
    )
    assert stale.state(NOW) == attestations.EXPIRED
