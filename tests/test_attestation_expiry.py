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

Since #1010 rail 17 is no longer in this model: it reads the venue per buy and has nothing to
expire, so the model covers rail 22 alone and the tests below pin that rail 17 stays out.
"""

from __future__ import annotations

from typing import Any

import pytest

from keel import attestations
from keel.data.db import connect, migrate
from keel.data.repository import Repository

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

    assert keys == {"cash_posture"}


def test_survey_has_no_withdrawal_attestation(repo) -> None:
    """#1010: rail 17 reads the venue per buy, so there is no attestation to expire -- not in
    the definitions, and not in a survey of a database that still carries the old keys."""
    repo.set_state("withdrawals_attested_at", NOW - 30 * 86_400)
    repo.set_state("withdrawals_enabled", True)

    assert "withdrawal" not in {a.key for a in attestations.survey_definitions()}
    assert "withdrawal" not in {a.key for a in attestations.survey(repo, NOW)}
    assert not hasattr(attestations, "withdrawal_expiry")


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
        assert definition.rail == "22", definition.key
        assert definition.label, definition.key


def test_each_attestation_warns_on_a_window_proportional_to_its_own_life() -> None:
    """Rail 22's window is READ from the rail's own 90-day constant rather than becoming a second
    opinion about it: 15 days of a 90-day claim."""
    from keel_core.cash_posture import ATTESTATION_TTL_SEC

    found = {a.key: a for a in attestations.survey_definitions()}

    assert found["cash_posture"].warn_within_sec == ATTESTATION_TTL_SEC // 6


# -- the verdict, not just the clock -----------------------------------------------------------


def test_the_verdict_does_not_outrank_the_clock() -> None:
    """An expired "no" reports as EXPIRED, not REFUSED: what an operator must do about it is
    re-attest either way, and `expired` is the word that says the reading is stale."""
    definition = attestations.definition("cash_posture")
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


# -- the review of #794 ------------------------------------------------------------------------
#
# Every test below was written against a defect the first version of this module actually had.
# The pattern in all of them is the same: the model answered the CLOCK question correctly and
# then disagreed with the rail about whether the attestation was any good.


def _posture(**overrides: Any) -> Any:
    from keel_core.cash_posture import CashPostureState, VenueCashPosture

    fields: dict[str, Any] = {
        "venue": "coinbase",
        "state": CashPostureState.ATTESTED,
        "attested_posture": "SPOT_CASH",
        "attested_ts": NOW - 86_400,
        "attest_due_ts": NOW + 60 * 86_400,
        "refuted_ts": None,
        "refuted_reason": None,
        "credential_fingerprint": "fp-1",
    }
    return VenueCashPosture(**{**fields, **overrides})


class _PostureRepo:
    """Just the read `_posture_dates` makes."""

    def __init__(self, record: Any, *, venue: str = "coinbase") -> None:
        self._record, self._venue = record, venue
        self.asked_for: list[str] = []

    def get_state(self, key: str, default: Any = None) -> Any:
        return default

    def get_venue_cash_posture(self, venue: str) -> Any:
        self.asked_for.append(venue)
        return self._record if venue == self._venue else None


def test_a_refuted_posture_is_not_a_healthy_one() -> None:
    """**The worst of the #794 findings, because it made things worse than before.**

    `refute_posture` PRESERVES `attested_ts` and `attest_due_ts` (`posture.py:28-29`), so a
    refuted record still carries a due date months out. The model read the dates alone, called it
    `ok`, and the banner stayed blank while rail 22 vetoed every live entry. Worse: `ok:<due_ts>`
    is a window that does not move, so the `attestation.expiring` alert doctor still FAILs on was
    delivered once and then suppressed for the rest of the due date -- up to 90 days. Before
    #793 it fired every cycle.
    """
    from keel_core.cash_posture import CashPostureState

    repo = _PostureRepo(_posture(state=CashPostureState.REFUTED, refuted_ts=NOW - 3_600))
    (posture,) = attestations.survey(repo, NOW)
    assert posture.state(NOW) == attestations.REFUSED


def test_a_margin_enabled_posture_is_not_a_healthy_one() -> None:
    """Rail 22's other veto. Doctor's own words: "a sell can fill as a short".

    The CONSTANT, not the literal `"MARGIN_ENABLED"` -- `cash_posture.MARGIN_ENABLED` is
    `"margin_enabled"`, and a test that spells it in the wrong case asserts nothing: the
    comparison is false for the same reason the defect made it false."""
    from keel_core.cash_posture import MARGIN_ENABLED

    repo = _PostureRepo(_posture(attested_posture=MARGIN_ENABLED))
    (posture,) = attestations.survey(repo, NOW)
    assert posture.state(NOW) == attestations.REFUSED


def test_a_posture_attested_with_no_due_date_is_not_reported_as_never_attested() -> None:
    """`MISSING` names a rail nobody has attested. This record WAS attested; what it lacks is an
    expiry, which `VenueCashPosture` refuses to invent and rail 22 refuses to treat as "never
    expires". Doctor words it correctly at `doctor.py:516` and the model did not."""
    repo = _PostureRepo(_posture(attest_due_ts=None))
    (posture,) = attestations.survey(repo, NOW)
    assert posture.state(NOW) == attestations.REFUSED
    assert posture.attested_at == NOW - 86_400, "the date it WAS attested must survive"


def test_a_rail_nobody_has_attested_still_reports_missing() -> None:
    """The negative control for the three above: `MISSING` must not become unreachable."""
    repo = _PostureRepo(None)
    (posture,) = attestations.survey(repo, NOW)
    assert posture.state(NOW) == attestations.MISSING


def test_survey_covers_every_definition_rather_than_the_first_two(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`survey` indexed `_DEFINITIONS[0]` and `[1]`, which two definitions cannot distinguish
    from iterating them -- so the test has to ADD a third.

    Append a rail and it reaches `survey_definitions()` but never `survey()`, so it can never
    reach the banner, doctor or the ledger and nothing fails. REORDER the tuple and withdrawal
    dates get attached to the posture definition. Both are silent."""
    third = attestations.Attestation(
        key="third_rail", rail="99", label="A rail added later", remedy="keel attest --something"
    )
    monkeypatch.setattr(attestations, "_DEFINITIONS", (*attestations.survey_definitions(), third))
    surveyed = attestations.survey(_PostureRepo(None), NOW)

    assert [a.key for a in surveyed] == ["cash_posture", "third_rail"]
    assert surveyed[1].state(NOW) == attestations.MISSING, "a rail with no reader is not ok"


def test_the_survey_never_raises_even_when_every_read_fails() -> None:
    """The promise `survey`'s docstring makes, which nothing tested. It matters most at
    `notify_after_cycle`, whose one broad `except` would swallow the whole cycle's
    notifications -- `rail.armed` and `setup.unplaced` included -- for a fault in here."""

    class _Broken:
        def get_state(self, *_args: Any, **_kwargs: Any) -> Any:
            raise OSError("database is locked")

        def get_venue_cash_posture(self, *_args: Any, **_kwargs: Any) -> Any:
            raise OSError("database is locked")

    surveyed = attestations.survey(_Broken(), NOW)
    assert [a.state(NOW) for a in surveyed] == [attestations.MISSING]


def test_asking_for_an_attestation_that_does_not_exist_is_an_error_not_a_blank() -> None:
    """`definition` returning a placeholder would put a rail on the banner that no rail owns."""
    with pytest.raises(KeyError):
        attestations.definition("no_such_rail")
