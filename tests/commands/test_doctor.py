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
    AdmissibilityRow,
    Finding,
    SeriesHealth,
    admissibility_findings,
    allowance_findings,
    attestation_findings,
    data_health_findings,
    doctor_exit_code,
    doctor_lines,
    rail_state_findings,
    render_json,
    veto_findings,
)
from keel.data.freshness import Freshness
from keel.types import Granularity

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


# -- admissibility at current ATR (#443 slice 2) ------------------------------------------------
#
# Hand-verifiable numbers throughout: equity 5000, risk_pct 0.01 -> a $50 risk budget.
#   BTC row (price 100, atr 10): stop band 85..80 -> qty 50/15..50/20 -> notional 333.33..250.00
#   ETH row (price 100, atr 4):  stop band 94..92 -> qty 50/6..50/8   -> notional 833.33..625.00

EQUITY = Decimal("5000")
RISK = Decimal("0.01")
BTC = AdmissibilityRow(product_id="BTC-USD", price=Decimal("100"), atr=Decimal("10"))
ETH = AdmissibilityRow(product_id="ETH-USD", price=Decimal("100"), atr=Decimal("4"))


def test_all_products_fit_under_cap_is_ok_with_count() -> None:
    (finding,) = admissibility_findings([BTC, ETH], EQUITY, RISK, Decimal("1000"))
    assert finding.name == "sizing.admissible"
    assert finding.status == "ok"
    assert "2/2" in finding.headline
    # the convention is part of the contract: the detail always states what was assumed
    assert "caps.max_exposure_usd" in finding.detail
    assert "ATR(14)" in finding.detail


def test_one_product_cannot_fit_warns_naming_it() -> None:
    (finding,) = admissibility_findings([BTC, ETH], EQUITY, RISK, Decimal("500"))
    assert finding.status == "warn"
    assert "ETH-USD" in finding.detail
    assert "BTC-USD" not in finding.detail


def test_every_product_cannot_fit_fails_with_risk_fix() -> None:
    (finding,) = admissibility_findings([BTC, ETH], EQUITY, RISK, Decimal("200"))
    assert finding.status == "fail"
    assert "risk_pct" in finding.fix
    assert "caps.max_per_order_usd" in finding.fix


def test_marginal_product_warns_naming_it() -> None:
    # cap 300 sits between the band edges 250.00 and 333.33: fits at 2x ATR, not at 1.5x
    (finding,) = admissibility_findings([BTC], EQUITY, RISK, Decimal("300"))
    assert finding.status == "warn"
    assert "BTC-USD" in finding.detail


def test_no_atr_row_warns_toward_fetch() -> None:
    no_data = AdmissibilityRow(product_id="SOL-USD", price=Decimal("0"), atr=None)
    (finding,) = admissibility_findings([BTC, no_data], EQUITY, RISK, Decimal("1000"))
    assert finding.status == "warn"
    assert "SOL-USD" in finding.detail
    assert "keel fetch" in finding.fix


def test_all_rows_without_atr_warn_no_atr_data() -> None:
    no_data = AdmissibilityRow(product_id="SOL-USD", price=Decimal("0"), atr=None)
    (finding,) = admissibility_findings([no_data], EQUITY, RISK, Decimal("1000"))
    assert finding.status == "warn"
    assert "no ATR data" in finding.headline
    assert finding.fix == "keel fetch"


def test_band_arithmetic_is_pinned_exactly() -> None:
    (finding,) = admissibility_findings([BTC], EQUITY, RISK, Decimal("300"))
    assert "250.00" in finding.detail  # band low  = 2.0x ATR stop
    assert "333.33" in finding.detail  # band high = 1.5x ATR stop


# -- per-product data health (#443 slice 2) ------------------------------------------------------


def _fresh(**overrides: object) -> Freshness:
    """A current, gap-free hourly series unless the test says otherwise."""
    base = dict(
        product="BTC-USD",
        granularity=Granularity.ONE_HOUR,
        n_candles=8000,
        last_ts=NOW - 3600,
        bars_behind=0,
        gaps=0,
        missing=False,
        stale=False,
        market_closed=False,
    )
    base.update(overrides)
    return Freshness(**base)  # type: ignore[arg-type]


def _series(fresh: Freshness, unexplained_gaps: int = 0) -> SeriesHealth:
    return SeriesHealth(
        product=fresh.product,
        granularity=fresh.granularity.value,
        freshness=fresh,
        unexplained_gaps=unexplained_gaps,
    )


def test_all_fresh_no_gaps_is_three_ok_findings() -> None:
    findings = data_health_findings([_series(_fresh()), _series(_fresh(product="ETH-USD"))])
    assert len(findings) == 3
    assert all(f.status == "ok" for f in findings)
    assert {f.name for f in findings} == {"data.missing", "data.stale", "data.gaps"}


def test_missing_series_fails_naming_product_and_granularity() -> None:
    cold = _series(_fresh(product="SOL-USD", n_candles=0, last_ts=None, missing=True, stale=True))
    (missing, _stale, _gaps) = data_health_findings([_series(_fresh()), cold])
    assert missing.status == "fail"
    assert "SOL-USD ONE_HOUR" in missing.detail
    assert missing.fix == "keel fetch"


def test_some_stale_market_open_warns_with_bars_behind() -> None:
    stale = _series(_fresh(product="ETH-USD", bars_behind=5, stale=True))
    (_missing, stale_f, _gaps) = data_health_findings([_series(_fresh()), stale])
    assert stale_f.status == "warn"
    assert "ETH-USD ONE_HOUR" in stale_f.detail
    assert "5" in stale_f.detail
    assert stale_f.fix == "keel fetch"


def test_every_judged_series_stale_fails_feed_dead() -> None:
    rows = [
        _series(_fresh(product="BTC-USD", bars_behind=9, stale=True)),
        _series(_fresh(product="ETH-USD", bars_behind=7, stale=True)),
    ]
    (_missing, stale_f, _gaps) = data_health_findings(rows)
    assert stale_f.status == "fail"
    assert "feed looks dead" in stale_f.headline


def test_stale_under_closed_market_is_defused_ok() -> None:
    closed = _series(_fresh(bars_behind=40, stale=True, market_closed=True))
    (_missing, stale_f, _gaps) = data_health_findings([_series(_fresh()), closed])
    assert stale_f.status == "ok"
    assert "defused" in stale_f.detail
    assert "FR-9" in stale_f.detail


def test_unexplained_gaps_warn_with_repair_fix() -> None:
    gappy = _series(_fresh(product="ETH-USD", gaps=3), unexplained_gaps=3)
    (_missing, _stale, gaps_f) = data_health_findings([_series(_fresh()), gappy])
    assert gaps_f.status == "warn"
    assert "ETH-USD" in gaps_f.detail
    assert "3" in gaps_f.detail
    assert gaps_f.fix == "keel fetch --repair-gaps"


def test_empty_series_list_says_none_configured() -> None:
    (_missing, stale_f, _gaps) = data_health_findings([])
    assert stale_f.status == "ok"
    assert "no series configured" in stale_f.headline


def test_exit_code_fails_on_data_missing() -> None:
    cold = _series(_fresh(n_candles=0, last_ts=None, missing=True, stale=True))
    findings = data_health_findings([cold])
    assert doctor_exit_code(findings) == 1


def test_render_json_includes_new_finding_names() -> None:
    findings = admissibility_findings([BTC], EQUITY, RISK, Decimal("1000")) + data_health_findings(
        [_series(_fresh())]
    )
    parsed = json.loads(render_json(findings))
    names = {row["name"] for row in parsed}
    assert "sizing.admissible" in names
    assert {"data.missing", "data.stale", "data.gaps"} <= names
