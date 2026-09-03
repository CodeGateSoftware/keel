"""`doctor` must warn BEFORE a cash-posture attestation expires -- #691, review finding.

The rail vetoes on an expired attestation, which is correct and also silent: the live profile runs
unattended daily, so on day 90 entries stop and nothing has said why. Rail 17 already treats an
expiring attestation as WARN-worthy before it bites, and this TTL is thirteen times longer, which
makes the cliff MORE surprising rather than less -- nobody remembers what they attested a quarter
ago.

The warning window is proportional to the TTL rather than copied from rail 17: two days' notice on
a 7-day window is ~29% of it; two days on a 90-day window is 2%, and a daily profile that misses
those two cycles for any reason gets no notice at all.

`test_the_warning_reaches_the_notification_path` is the one that matters -- a `doctor` finding
nobody is told about is only marginally better than the veto.
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

from keel import notifications
from keel.commands.doctor import FAIL, OK, WARN, cash_posture_findings

NOW = 1_800_000_000


def _record(
    *,
    state: CashPostureState = CashPostureState.ATTESTED,
    posture: str | None = SPOT_CASH,
    due_ts: int | None = NOW + ATTESTATION_TTL_SEC,
    refuted_ts: int | None = None,
) -> VenueCashPosture:
    return VenueCashPosture(
        venue="coinbase",
        state=state,
        attested_posture=posture,
        attested_ts=NOW,
        attest_due_ts=due_ts,
        refuted_ts=refuted_ts,
        refuted_reason="INTX portfolio present" if refuted_ts else None,
        credential_fingerprint=None,
    )


def test_no_record_fails(m: None = None) -> None:
    """Rail 22 vetoes every entry in this state, so it is a FAIL, not a warning."""
    (finding,) = cash_posture_findings(None, venue="coinbase", now_ts=NOW)
    assert finding.status == FAIL
    assert "keel posture attest" in finding.fix
    assert "coinbase" in finding.detail


def test_a_fresh_attestation_is_ok() -> None:
    (finding,) = cash_posture_findings(_record(), venue="coinbase", now_ts=NOW)
    assert finding.status == OK
    assert "spot_cash" in finding.detail


def test_an_expired_attestation_fails_and_says_how_long_ago() -> None:
    (finding,) = cash_posture_findings(
        _record(due_ts=NOW - 3 * 86400), venue="coinbase", now_ts=NOW
    )
    assert finding.status == FAIL
    assert "3 day" in finding.detail


def test_an_attestation_nearing_its_due_date_warns() -> None:
    """The whole point. A daily profile gets many cycles' notice, not two."""
    (finding,) = cash_posture_findings(
        _record(due_ts=NOW + 5 * 86400), venue="coinbase", now_ts=NOW
    )
    assert finding.status == WARN
    assert "5 day" in finding.detail


def test_the_warning_window_is_proportional_to_the_ttl_not_two_days() -> None:
    """Two days on a 90-day window is 2% of it, and a daily profile that misses those cycles
    gets no notice at all. Rail 17's two days is ~29% of ITS window; the same generosity here is
    two weeks."""
    warn_at = ATTESTATION_TTL_SEC // 6  # 15 days on 90
    (early,) = cash_posture_findings(
        _record(due_ts=NOW + warn_at + 86400), venue="coinbase", now_ts=NOW
    )
    (late,) = cash_posture_findings(
        _record(due_ts=NOW + warn_at - 86400), venue="coinbase", now_ts=NOW
    )
    assert early.status == OK
    assert late.status == WARN


def test_a_margin_attestation_fails_and_does_not_ask_for_a_re_attestation() -> None:
    """The remedy is a change to the ACCOUNT. Telling an operator to re-attest would send them
    to type the same true answer again."""
    (finding,) = cash_posture_findings(
        _record(posture=MARGIN_ENABLED), venue="coinbase", now_ts=NOW
    )
    assert finding.status == FAIL
    assert "margin" in finding.detail.lower()


def test_a_refuted_posture_fails_and_names_the_venue_evidence() -> None:
    (finding,) = cash_posture_findings(
        _record(state=CashPostureState.REFUTED, refuted_ts=NOW - 10),
        venue="coinbase",
        now_ts=NOW,
    )
    assert finding.status == FAIL
    assert "INTX portfolio present" in finding.detail


@pytest.mark.parametrize("due_offset", [-86400, 5 * 86400])
def test_the_warning_reaches_the_notification_path(due_offset: int) -> None:
    """A doctor finding nobody is told about is only marginally better than the veto. Both the
    WARN and the FAIL have to produce an `attestation.expiring` event."""
    findings = cash_posture_findings(
        _record(due_ts=NOW + due_offset), venue="coinbase", now_ts=NOW
    )
    assert "attest.cash_posture" in notifications._ATTESTATION_FINDINGS  # noqa: SLF001
    events = notifications.events_from_state(
        attestation_findings=findings,
        rail_findings=[],
        month_to_date_spend=None,
        allowance=None,
        unplaced_setups=[],
        stale_products=[],
        held_products=[],
    )
    assert [e for e in events if e.key == "attestation.expiring"], findings
