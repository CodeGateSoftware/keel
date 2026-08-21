"""keel doctor (#443) — one command that answers 'is this deployment actually working'.

The load-bearing tests are the motivating case from the issue, reproduced as data: a
profile that vetoed 15 of 15 entries on `subscription_unattested` while `keel status`
looked healthy. Doctor must make that impossible to miss — and every finding must name
the command that fixes it, because the value is the next step, not the diagnosis.

The deliberately-halted distinction is a contract: an armed kill-switch is a CORRECT
state, not a fault, and must not fail the run.
"""

from __future__ import annotations

import json
from decimal import Decimal

from keel.commands.doctor import (
    Finding,
    allowance_findings,
    attestation_findings,
    doctor_exit_code,
    doctor_lines,
    rail_state_findings,
    render_json,
    veto_findings,
)

NOW = 1_784_500_000
DAY = 86_400


class FakeSubscription:
    """Just the surface attestation_findings reads."""

    def __init__(self, allowance: Decimal, due_ts: int, status_value: str) -> None:
        self._allowance, self._due_ts, self._status = allowance, due_ts, status_value

    def allowance_usd(self, now_ts: int, unsubscribed: Decimal) -> Decimal:
        return self._allowance

    @property
    def attest_due_ts(self) -> int:
        return self._due_ts

    @property
    def status(self) -> str:
        return self._status


def test_rail17_fresh_reports_days_remaining() -> None:
    findings = attestation_findings(
        subscription=None, withdrawals_attested_at=NOW - 4 * DAY, now_ts=NOW, ttl_sec=7 * DAY
    )
    (rail17,) = [f for f in findings if f.name == "attest.withdrawals"]
    assert rail17.status == "ok"
    assert "3 day" in rail17.detail
    assert rail17.fix


def test_rail17_absent_fails_and_names_the_attest_command() -> None:
    findings = attestation_findings(
        subscription=None, withdrawals_attested_at=0, now_ts=NOW, ttl_sec=7 * DAY
    )
    (rail17,) = [f for f in findings if f.name == "attest.withdrawals"]
    assert rail17.status == "fail"
    assert "keel withdrawals attest --enabled" in rail17.fix


def test_rail17_expired_fails_with_days_over() -> None:
    findings = attestation_findings(
        subscription=None, withdrawals_attested_at=NOW - 9 * DAY, now_ts=NOW, ttl_sec=7 * DAY
    )
    (rail17,) = [f for f in findings if f.name == "attest.withdrawals"]
    assert rail17.status == "fail"
    assert "expired" in rail17.detail.lower()


def test_rail14_absent_fails_naming_the_venue() -> None:
    findings = attestation_findings(
        subscription=None, withdrawals_attested_at=0, now_ts=NOW, ttl_sec=7 * DAY
    )
    (rail14,) = [f for f in findings if f.name == "attest.subscription"]
    assert rail14.status == "fail"
    assert "keel subscription attest" in rail14.fix


def test_rail14_attestation_due_soon_warns_with_days() -> None:
    record = FakeSubscription(Decimal("500"), NOW + 2 * DAY, "attested")
    findings = attestation_findings(
        subscription=record, withdrawals_attested_at=NOW, now_ts=NOW, ttl_sec=7 * DAY
    )
    (rail14,) = [f for f in findings if f.name == "attest.subscription"]
    assert rail14.status == "warn"
    assert "2 day" in rail14.detail


def test_kill_switch_is_halted_not_broken() -> None:
    findings = rail_state_findings(
        kill_switch=True, streak_halt_until=0, drawdown_total=Decimal("0"), now_ts=NOW
    )
    (kill,) = [f for f in findings if f.name == "rail.kill_switch"]
    assert kill.status == "halted"
    assert "keel autonomy on" in kill.fix


def test_streak_halts_expire_on_their_own() -> None:
    findings = rail_state_findings(
        kill_switch=False,
        streak_halt_until=NOW + 6 * 3600,
        drawdown_total=Decimal("0"),
        now_ts=NOW,
    )
    (streak,) = [f for f in findings if f.name == "rail.streak_halt"]
    assert streak.status == "halted"
    assert "6h" in streak.detail or "6 h" in streak.detail or "clears on its own" in streak.fix


def test_drawdown_over_threshold_fails() -> None:
    findings = rail_state_findings(
        kill_switch=False, streak_halt_until=0, drawdown_total=Decimal("21"), now_ts=NOW
    )
    (dd,) = [f for f in findings if f.name == "rail.drawdown"]
    assert dd.status == "fail"


def test_allowance_headroom_counts_typical_orders() -> None:
    findings = allowance_findings(
        month_to_date_spend=Decimal("200"),
        allowance=Decimal("500"),
        mean_buy_notional=Decimal("100"),
    )
    (head,) = findings
    assert head.status == "ok"
    assert "3" in head.detail  # $300 remain -> 3 typical orders


def test_unlimited_allowance_says_so() -> None:
    (head,) = allowance_findings(
        month_to_date_spend=Decimal("200"), allowance=None, mean_buy_notional=Decimal("100")
    )
    assert head.status == "ok"
    assert "unlimited" in head.detail.lower()


def test_the_motivating_case_one_reason_vetoes_everything() -> None:
    lines = [
        json.dumps(
            {
                "ts": NOW - 3600,
                "event": "executor.order_vetoed",
                "violations": [
                    "subscription_unattested: coinbase cannot spend because no subscription "
                    "has been attested"
                ],
            }
        )
        for _ in range(15)
    ]
    (finding,) = veto_findings(lines, since_ts=NOW - DAY)
    assert finding.status == "fail"
    assert "15 of 15" in finding.detail
    assert "subscription_unattested" in finding.detail
    assert "keel subscription attest" in finding.fix


def test_quiet_veto_log_is_ok() -> None:
    (finding,) = veto_findings([], since_ts=NOW - DAY)
    assert finding.status == "ok"


def test_exit_code_fails_only_on_real_faults() -> None:
    halted = Finding("rail.kill_switch", "halted", "engaged", "deliberate", "keel autonomy on")
    ok = Finding("install.versions", "ok", "aligned", "six of six", "-")
    assert doctor_exit_code([halted, ok]) == 0
    broken = Finding("attest.withdrawals", "fail", "expired", "9 days over", "attest")
    assert doctor_exit_code([halted, broken]) == 1


def test_every_finding_names_a_fix_or_says_none_needed() -> None:
    samples = attestation_findings(
        subscription=None, withdrawals_attested_at=NOW, now_ts=NOW, ttl_sec=7 * DAY
    ) + rail_state_findings(
        kill_switch=False, streak_halt_until=0, drawdown_total=Decimal("0"), now_ts=NOW
    )
    for finding in samples:
        assert finding.fix.strip()


def test_json_mode_round_trips() -> None:
    findings = [
        Finding("attest.withdrawals", "fail", "not attested", "never attested", "attest it")
    ]
    parsed = json.loads(render_json(findings))
    assert parsed[0]["name"] == "attest.withdrawals"
    assert parsed[0]["status"] == "fail"
    assert parsed[0]["fix"] == "attest it"


def test_report_lines_render_every_finding() -> None:
    findings = [
        Finding("a.b", "fail", "headline", "detail", "fix"),
        Finding("c.d", "halted", "headline2", "detail2", "fix2"),
    ]
    text = "\n".join(doctor_lines(findings))
    assert "a.b" in text and "headline" in text
    assert "c.d" in text and "headline2" in text
