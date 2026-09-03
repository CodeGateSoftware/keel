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
from pathlib import Path

from keel_core.trade_scope import READ_ONLY, TRADING, TradeScopeState, VenueTradeScope

from keel.commands.doctor import (
    OK,
    AdmissibilityRow,
    Finding,
    SeriesHealth,
    admissibility_findings,
    allowance_findings,
    attestation_findings,
    backup_footprint_findings,
    balance_drift_findings,
    data_health_findings,
    doctor_exit_code,
    doctor_lines,
    gather_findings,
    orphan_bracket_findings,
    partial_fill_findings,
    rail_state_findings,
    read_backup_footprint,
    render_json,
    trade_scope_findings,
    unbooked_exit_findings,
    veto_findings,
)
from keel.config import load_config
from keel.data.db import connect, migrate
from keel.data.freshness import Freshness
from keel.data.repository import Repository
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


# -- rail 20: trade scope (#233) -----------------------------------------------------------------
#
# `trade_scope_findings` takes the record directly (like `attestation_findings` takes
# `subscription`), so it is unit-testable without a database.


def _scope(
    state: TradeScopeState,
    *,
    attested_scope: str | None = None,
    attested_ts: int | None = None,
    confirmed_ts: int | None = None,
    refuted_ts: int | None = None,
    refuted_reason: str | None = None,
    venue: str = "coinbase",
    credential_fingerprint: str | None = None,
) -> VenueTradeScope:
    return VenueTradeScope(
        venue=venue,
        state=state,
        attested_scope=attested_scope,
        attested_ts=attested_ts,
        confirmed_ts=confirmed_ts,
        refuted_ts=refuted_ts,
        refuted_reason=refuted_reason,
        credential_fingerprint=credential_fingerprint,
    )


def test_trade_scope_absent_fails_naming_the_venue_and_the_attest_command() -> None:
    findings = trade_scope_findings(None, "coinbase")
    (finding,) = [f for f in findings if f.name == "scope.trade"]
    assert finding.status == "fail"
    assert "keel scope attest --trading --venue coinbase" in finding.fix


def test_trade_scope_confirmed_is_ok_and_names_the_venue() -> None:
    record = _scope(TradeScopeState.CONFIRMED, confirmed_ts=NOW - DAY)
    findings = trade_scope_findings(record, "coinbase")
    (finding,) = [f for f in findings if f.name == "scope.trade"]
    assert finding.status == "ok"
    assert "coinbase" in finding.detail


def test_trade_scope_attested_trading_is_ok_but_flags_it_as_unconfirmed() -> None:
    record = _scope(TradeScopeState.ATTESTED, attested_scope=TRADING, attested_ts=NOW - DAY)
    findings = trade_scope_findings(record, "coinbase")
    (finding,) = [f for f in findings if f.name == "scope.trade"]
    assert finding.status == "ok"
    assert "unconfirmed" in finding.detail.lower()


def test_trade_scope_attested_read_only_fails() -> None:
    record = _scope(TradeScopeState.ATTESTED, attested_scope=READ_ONLY, attested_ts=NOW - DAY)
    findings = trade_scope_findings(record, "coinbase")
    (finding,) = [f for f in findings if f.name == "scope.trade"]
    assert finding.status == "fail"
    assert "read" in (finding.headline + finding.detail).lower()


def test_trade_scope_refuted_fails_and_names_the_venues_reason() -> None:
    record = _scope(
        TradeScopeState.REFUTED, refuted_ts=NOW - DAY, refuted_reason="insufficient permissions"
    )
    findings = trade_scope_findings(record, "coinbase")
    (finding,) = [f for f in findings if f.name == "scope.trade"]
    assert finding.status == "fail"
    assert "insufficient permissions" in finding.detail


def test_trade_scope_unverified_fails() -> None:
    record = _scope(TradeScopeState.UNVERIFIED)
    findings = trade_scope_findings(record, "coinbase")
    (finding,) = [f for f in findings if f.name == "scope.trade"]
    assert finding.status == "fail"


def test_trade_scope_reattested_after_refutation_warns_with_the_refusal_date() -> None:
    """THE SPECIFIC OPERATOR SURFACE THE DESIGN CALLS OUT (#233): a record that has been
    re-attested still carries `refuted_ts`, and doctor must say so -- that is the entire reason
    the record keeps it through a re-attestation instead of clearing it."""
    record = _scope(
        TradeScopeState.ATTESTED,
        attested_scope=TRADING,
        attested_ts=NOW,
        refuted_ts=1_700_000_000,  # 2023-11-14T22:13:20Z
    )
    findings = trade_scope_findings(record, "coinbase")
    names = {f.name for f in findings}
    assert "scope.trade" in names  # the OK finding is still there too
    (reattested,) = [f for f in findings if f.name != "scope.trade"]
    assert reattested.status == "warn"
    assert "2023-11-14" in reattested.detail
    assert "refut" in reattested.detail.lower()


def test_trade_scope_confirmed_record_with_a_past_refutation_also_warns() -> None:
    """The WARN fires for ANY non-REFUTED state carrying a `refuted_ts`, not just ATTESTED -- a
    CONFIRMED record (the venue accepted a later placement) can carry the same history."""
    record = _scope(TradeScopeState.CONFIRMED, confirmed_ts=NOW, refuted_ts=1_700_000_000)
    findings = trade_scope_findings(record, "coinbase")
    assert any(f.status == "warn" for f in findings)


def test_trade_scope_currently_refuted_does_not_ALSO_get_the_reattested_warn() -> None:
    """A record that is STILL refuted (never re-attested) gets the one FAIL, not a second
    finding repeating the same fact under a different name."""
    record = _scope(TradeScopeState.REFUTED, refuted_ts=NOW - DAY, refuted_reason="bad creds")
    findings = trade_scope_findings(record, "coinbase")
    assert len(findings) == 1


def test_kill_switch_is_halted_not_broken() -> None:
    findings = rail_state_findings(
        kill_switch=True, streak_halt_until=0, drawdown_total=Decimal("0"), now_ts=NOW
    )
    (kill,) = [f for f in findings if f.name == "rail.kill_switch"]
    assert kill.status == "halted"
    # `keel resume`, not `keel autonomy on` -- this assertion pinned the wrong command until
    # #693, and an operator who followed it got a still-halted agent authorised to trade
    # unattended. See tests/commands/test_doctor_fix_lines.py for the standing pin.
    assert "keel resume" in kill.fix


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
    halted = Finding("rail.kill_switch", "halted", "engaged", "deliberate", "keel resume")
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


def test_zero_atr_row_is_no_data_not_a_zero_band() -> None:
    # atr=0 is non-None but non-positive: no meaningful stop distance, so it takes the same
    # no_data WARN path as atr=None -- never a band computed off a zero ATR.
    zero = AdmissibilityRow(product_id="SOL-USD", price=Decimal("0"), atr=Decimal("0"))
    (finding,) = admissibility_findings([BTC, zero], EQUITY, RISK, Decimal("1000"))
    assert finding.status == "warn"
    assert "SOL-USD: no ATR data" in finding.detail
    assert "keel fetch" in finding.fix


def test_band_arithmetic_is_pinned_exactly() -> None:
    (finding,) = admissibility_findings([BTC], EQUITY, RISK, Decimal("300"))
    assert "250.00" in finding.detail  # band low  = 2.0x ATR stop
    assert "333.33" in finding.detail  # band high = 1.5x ATR stop


# Band-boundary contract, hand-computed the same way: equity 4800, risk_pct 0.01 -> a $48
# budget; price 100, atr 20 -> stop band 60..70:
#   mult 1.5 -> stop 70, qty 48/30 = 1.6 -> notional 160 (band high)
#   mult 2   -> stop 60, qty 48/40 = 1.2 -> notional 120 (band low)
_BAND_ROW = AdmissibilityRow(product_id="BTC-USD", price=Decimal("100"), atr=Decimal("20"))


def test_band_high_landing_on_cap_fits() -> None:
    # guards.py vetoes only when notional is STRICTLY greater than the cap, so a band whose
    # high edge lands exactly on the cap fits -- doctor must not warn where the rail passes.
    (finding,) = admissibility_findings(
        [_BAND_ROW], Decimal("4800"), Decimal("0.01"), Decimal("160")
    )
    assert finding.status == "ok"
    assert "160.00" in finding.detail


def test_band_low_landing_on_cap_is_marginal_warn() -> None:
    # the cap sits exactly on the band's low edge: 2x ATR fits, 1.5x does not -- marginal.
    (finding,) = admissibility_findings(
        [_BAND_ROW], Decimal("4800"), Decimal("0.01"), Decimal("120")
    )
    assert finding.status == "warn"
    assert "BTC-USD" in finding.detail
    assert "straddles" in finding.detail


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


def test_stale_ok_wording_all_missing_and_singular_count() -> None:
    # every series missing leaves nothing to judge: the OK detail must say that, not claim
    # series are current; and a single judged series reads "is current", not "are current".
    cold = _series(_fresh(n_candles=0, last_ts=None, missing=True, stale=True))
    (_missing, all_missing, _gaps) = data_health_findings([cold])
    assert all_missing.status == "ok"
    assert "0 series to judge" in all_missing.detail
    assert "every series is missing" in all_missing.detail

    (_missing, single, _gaps) = data_health_findings([_series(_fresh())])
    assert single.status == "ok"
    assert "1 judged series is current" in single.detail


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


# -- gather_findings: the one seam the click command and the MCP doctor tool share (#477) --------


def _seeded_repo(db_path: Path):
    """connect + migrate + Repository -- the `_repo_at` pattern from tests/test_cli.py."""
    conn = connect(str(db_path))
    migrate(conn)
    return Repository(conn)


def test_feed_scope_ignores_series_with_no_bars(tmp_path, valid_config_path) -> None:
    """A product that was never fetched has no provenance because it has no CANDLES, not because
    it predates the provenance table (#696). `data.missing` already reports an empty series; the
    feed-scope report saying "predates feed provenance" about it is advice from the absence of
    bars, and on a fresh deployment it would say that about everything.
    """
    repo = _seeded_repo(tmp_path / "keel.db")
    config = load_config(valid_config_path)
    findings = gather_findings(repo, config, [], NOW)
    (scope,) = [f for f in findings if f.name == "data.feed_scope"]
    assert scope.status == OK, scope.detail
    for product in config.allowlist:
        assert product not in scope.detail


def test_gather_findings_covers_every_check_over_a_seeded_db(tmp_path, valid_config_path) -> None:
    repo = _seeded_repo(tmp_path / "keel.db")
    config = load_config(valid_config_path)
    findings = gather_findings(repo, config, [], NOW)
    # every check the command runs, by name: slice 1 (install, attestations, rails, allowance,
    # vetoes, partial fills) and slice 2 (data health, admissibility) -- the MCP tool inherits
    # the same set, so an assistant and an operator cannot be shown two different accounts
    assert {f.name for f in findings} == {
        "install.identity",
        "attest.subscription",
        "attest.withdrawals",
        "scope.trade",
        "attest.cash_posture",
        "rail.kill_switch",
        "rail.streak_halt",
        "rail.drawdown",
        "allowance.headroom",
        "veto.recent",
        "fill.partial",
        "balance.drift",
        "bracket.orphan",
        "backups.footprint",
        "ledger.unbooked_exit",
        "data.missing",
        "data.stale",
        "data.gaps",
        "data.feed_scope",
        "sizing.admissible",
    }
    # an unattested, empty deployment fails the run, exactly as the command does
    assert doctor_exit_code(findings) == 1


def test_gather_findings_performs_no_writes_whatsoever(tmp_path, valid_config_path) -> None:
    repo = _seeded_repo(tmp_path / "keel.db")
    conn = repo._conn  # noqa: SLF001 -- total_changes IS the read-only proof
    config = load_config(valid_config_path)
    before = conn.total_changes
    gather_findings(repo, config, [], NOW)
    assert conn.total_changes == before, "gather_findings wrote to the database"


def test_gather_findings_reads_the_veto_lines_it_is_handed(tmp_path, valid_config_path) -> None:
    repo = _seeded_repo(tmp_path / "keel.db")
    config = load_config(valid_config_path)
    noisy = [
        json.dumps(
            {
                "ts": NOW - 3600,
                "event": "executor.order_vetoed",
                "violations": ["subscription_unattested: nothing attested"],
            }
        )
        for _ in range(3)
    ]
    findings = gather_findings(repo, config, noisy, NOW)
    (veto,) = [f for f in findings if f.name == "veto.recent"]
    assert veto.status == "fail"
    assert "3 of 3" in veto.detail


# -- partial fills (#446) ------------------------------------------------------------------------


def _live_order(
    *,
    product_id: str = "BTC-USD",
    status: str = "filled",
    qty: Decimal = Decimal("0.01"),
    filled_quantity: Decimal | None = None,
) -> dict:
    """An `orders` row dict as `Repository._order_row_to_dict` hands it to doctor."""
    return {
        "id": 7,
        "mode": "live",
        "product_id": product_id,
        "side": "BUY",
        "order_type": "market",
        "qty": qty,
        "limit_price": None,
        "status": status,
        "filled_quantity": filled_quantity,
        "actual_fill": Decimal("50000"),
    }


def test_no_partial_fills_is_ok() -> None:
    (finding,) = partial_fill_findings(
        [_live_order(filled_quantity=Decimal("0.01")), _live_order(filled_quantity=None)]
    )
    assert finding.name == "fill.partial"
    assert finding.status == "ok"


def test_a_partial_fill_warns_naming_the_order_and_both_sizes() -> None:
    (finding,) = partial_fill_findings(
        [_live_order(status="partially_filled", filled_quantity=Decimal("0.004"))]
    )
    assert finding.name == "fill.partial"
    assert finding.status == "warn"
    assert "BTC-USD" in finding.detail
    assert "0.004" in finding.detail and "0.01" in finding.detail


def test_the_partial_fill_finding_names_the_manual_remedy() -> None:
    """Doctor's contract: every finding names the next step. The automated bracket resize is
    #502's (no bracket kind on the port), so the fix must be the MANUAL one."""
    (finding,) = partial_fill_findings(
        [_live_order(status="partially_filled", filled_quantity=Decimal("0.004"))]
    )
    assert finding.fix.strip()
    assert "#502" in finding.fix


def test_paper_mode_rows_do_not_warn() -> None:
    """Paper-mode fills must never reach a live-money diagnostic -- the same boundary
    `held_products` and the exposure rails draw."""
    row = _live_order(status="partially_filled", filled_quantity=Decimal("0.004"))
    row["mode"] = "paper"
    (finding,) = partial_fill_findings([row])
    assert finding.status == "ok"


# -- unbooked exits (#639): the ledger invariant doctor was silent about ------------------------


def _tranche(**over):
    row = {
        "id": 7,
        "product_id": "BTC-USD",
        "rule_name": "turtle_breakout",
        "opened_at": NOW - 2 * DAY,
        "qty": Decimal("0.01"),
        "realized_qty": Decimal("0"),
    }
    row.update(over)
    return row


def _sell(**over):
    row = {
        "id": 11,
        "mode": "paper",
        "product_id": "BTC-USD",
        "side": "SELL",
        "status": "filled",
        "created_at": NOW - DAY,
    }
    row.update(over)
    return row


def test_a_filled_exit_behind_an_open_tranche_warns() -> None:
    """#639 exactly as the deployment held it: the exit filled, the tranche never closed, and
    `trade_outcomes` stayed empty -- found by asking about promotion timing, because no
    diagnostic said a word."""
    (finding,) = unbooked_exit_findings([_tranche()], [_sell()])
    assert finding.name == "ledger.unbooked_exit"
    assert finding.status == "warn"
    assert "BTC-USD" in finding.detail and "7" in finding.detail
    assert "#338" in finding.detail, "the finding must name what the missing rows starve"
    assert finding.fix.strip() and finding.fix != "-"


def test_a_tranche_with_no_exit_behind_it_is_ok() -> None:
    """A position that is simply still held is the normal case and must stay silent."""
    (finding,) = unbooked_exit_findings([_tranche()], [_sell(side="BUY")])
    assert finding.status == "ok"


def test_a_partially_exited_tranche_does_not_warn() -> None:
    """A deliberate scale-out (`executor.scale_out`) and #446's short market exit BOTH leave a
    tranche legitimately open behind a filled SELL, and both record the leg on the tranche.
    Flagging them would fire on correct behaviour every time a position was de-risked."""
    (finding,) = unbooked_exit_findings(
        [_tranche(realized_qty=Decimal("0.004"))], [_sell()]
    )
    assert finding.status == "ok"


def test_a_sale_that_predates_the_tranche_does_not_warn() -> None:
    """The ledger is FIFO: a sale that closed an EARLIER tranche says nothing about one opened
    after it, so a long-running product would otherwise warn forever on its own history."""
    (finding,) = unbooked_exit_findings(
        [_tranche(opened_at=NOW)], [_sell(created_at=NOW - DAY)]
    )
    assert finding.status == "ok"


def test_an_unfilled_exit_does_not_warn() -> None:
    """A resting or cancelled SELL has sold nothing -- the tranche is correctly still open."""
    (finding,) = unbooked_exit_findings([_tranche()], [_sell(status="pending")])
    assert finding.status == "ok"


def test_a_sale_in_another_product_does_not_warn() -> None:
    (finding,) = unbooked_exit_findings([_tranche()], [_sell(product_id="ETH-USD")])
    assert finding.status == "ok"


def test_an_unbooked_LIVE_exit_warns_too() -> None:
    """Modes are pooled deliberately: `agent._open_tranche` writes the ledger for paper and live
    alike, and an unbooked LIVE exit is strictly worse than the paper one that found this."""
    (finding,) = unbooked_exit_findings([_tranche()], [_sell(mode="live")])
    assert finding.status == "warn"


# -- ledger/venue balance drift (#667) -----------------------------------------------------------


def test_balance_drift_findings_is_ok_when_nothing_drifted() -> None:
    """The clean state is REPORTED, not omitted. An absent row reads as a check that never ran."""
    (finding,) = balance_drift_findings({})

    assert finding.name == "balance.drift"
    assert finding.status == "ok"


def test_balance_drift_findings_warns_and_names_both_numbers() -> None:
    """An operator cannot reconcile a divergence they are only told the size of.

    WARN rather than FAIL because every cause is legitimate -- a fee taken in the base leg, a
    short fill, an operator's own transfer. None is a fault in the deployment; all of them leave
    the ledger's idea of the position wrong until a human decides which it was.
    """
    (finding,) = balance_drift_findings(
        {
            "BTC-USD": {
                "ordered": "1.0",
                "held": "0.9985",
                "drift": "0.0015",
                "observed_at": NOW,
            }
        }
    )

    assert finding.status == "warn"
    assert "1.0" in finding.detail and "0.9985" in finding.detail
    assert "clamped" in finding.detail, (
        "the detail must say the SELL was already reduced -- otherwise this reads as an order "
        "that oversold, which is the outcome the clamp exists to prevent"
    )


def test_balance_drift_findings_ignores_a_malformed_record() -> None:
    """State is written by code that can change; a bad row must not crash `doctor`.

    `doctor` is what an operator runs when something is already wrong. It is the one command
    that must not fail on the state it exists to describe.
    """
    (finding,) = balance_drift_findings({"BTC-USD": "not a record"})

    assert finding.status == "ok"


def test_gather_findings_surfaces_a_recorded_drift(tmp_path, valid_config_path) -> None:
    """The wiring, not just the function. A finding nothing calls reports nothing.

    Written the way the executor writes it -- `balance_drift:<product_id>` -- so a rename on
    either side fails here rather than silently retiring the check.
    """
    from keel.execution.executor import BALANCE_DRIFT_PREFIX

    repo = _seeded_repo(tmp_path / "keel.db")
    repo.set_state(
        f"{BALANCE_DRIFT_PREFIX}BTC-USD",
        {"ordered": "1.0", "held": "0.9985", "drift": "0.0015", "observed_at": NOW},
    )
    config = load_config(valid_config_path)

    findings = gather_findings(repo, config, [], NOW)

    (drift,) = [f for f in findings if f.name == "balance.drift"]
    assert drift.status == "warn"
    assert "BTC-USD" in drift.detail


# -- orphaned protective orders (#668) -----------------------------------------------------------


def test_orphan_bracket_findings_is_ok_when_none_were_swept() -> None:
    (finding,) = orphan_bracket_findings({})

    assert finding.name == "bracket.orphan"
    assert finding.status == "ok"


def test_orphan_bracket_findings_warns_and_says_the_tranche_is_still_open() -> None:
    """The cancel resolved the order. The ledger row it stood over is deliberately untouched.

    An operator reading only "cancelled" would assume the position was closed out; it was not,
    and the detail has to say so or the finding is misleading in the direction that matters.
    """
    (finding,) = orphan_bracket_findings(
        {"BTC-USD": {"order_id": 7, "held": "0", "cancelled_at": NOW}}
    )

    assert finding.status == "warn"
    assert "still open in the ledger" in finding.detail


def test_orphan_bracket_findings_ignores_a_malformed_record() -> None:
    (finding,) = orphan_bracket_findings({"BTC-USD": "not a record"})

    assert finding.status == "ok"


def test_gather_findings_surfaces_a_swept_orphan(tmp_path, valid_config_path) -> None:
    """The wiring, keyed on the prefix the sweep actually writes."""
    from keel.execution.reconcile import ORPHAN_BRACKET_PREFIX

    repo = _seeded_repo(tmp_path / "keel.db")
    repo.set_state(
        f"{ORPHAN_BRACKET_PREFIX}BTC-USD", {"order_id": 7, "held": "0", "cancelled_at": NOW}
    )
    config = load_config(valid_config_path)

    findings = gather_findings(repo, config, [], NOW)

    (orphan,) = [f for f in findings if f.name == "bracket.orphan"]
    assert orphan.status == "warn"
    assert "BTC-USD" in orphan.detail


# -- update backups: counted, never deleted (#681) ------------------------------------------------


def _bak(launch: Path, name: str, size: int = 1024) -> None:
    (launch / name).write_bytes(b"x" * size)


def test_a_launch_folder_with_no_backups_says_so_calmly(tmp_path) -> None:
    """A fresh deployment must not be told it has a problem it does not have."""
    (finding,) = backup_footprint_findings(read_backup_footprint(tmp_path))

    assert finding.name == "backups.footprint"
    assert finding.status == "ok"


def test_a_handful_of_recent_backups_is_the_design_working(tmp_path) -> None:
    """`keel update` is SUPPOSED to leave these. Warning about three would train the finding
    to be ignored by the time it matters."""
    for version in ("0.13.0", "0.13.1", "0.13.2"):
        _bak(tmp_path, f"keel.db.bak-before-{version}-20260901-120000")

    (finding,) = backup_footprint_findings(read_backup_footprint(tmp_path))

    assert finding.status == "ok"


def test_backups_beyond_the_keep_count_are_surfaced_per_database(tmp_path) -> None:
    """The COUNT is the operator-actionable number. "23 copies of keel.db, oldest 0.4.0" says
    what to do; a byte total says only that something is large."""
    for version in ("0.4.0", "0.9.1", "0.12.2", "0.13.1", "0.13.2"):
        _bak(tmp_path, f"keel.db.bak-before-{version}-20260901-120000")
    _bak(tmp_path, "keel-live.db.bak-before-0.13.2-20260901-120000")

    (finding,) = backup_footprint_findings(read_backup_footprint(tmp_path))

    assert finding.status == "warn"
    assert "keel.db: 5" in finding.detail
    assert "0.4.0" in finding.detail, "the oldest version is what says how far back this goes"
    assert "keel-live.db" not in finding.detail, "one backup is not an accumulation"


def test_the_fix_never_tells_keel_to_delete_anything(tmp_path) -> None:
    """**The load-bearing test of this finding.** These files are the data-recovery path, and
    the release you need is the one before the release that broke. A fix line that offered to
    prune them would be the updater deleting its own rollback with extra steps."""
    for version in ("0.4.0", "0.9.1", "0.12.2", "0.13.2"):
        _bak(tmp_path, f"keel.db.bak-before-{version}-20260901-120000")

    (finding,) = backup_footprint_findings(read_backup_footprint(tmp_path))

    assert "BY HAND" in finding.fix
    assert "keel will not delete" in finding.fix
    for forbidden in ("keel backups prune", "--prune", "rm -rf"):
        assert forbidden not in finding.fix


def test_nothing_in_keel_deletes_an_update_backup() -> None:
    """The pin that outlives this finding.

    `update.py` states that the `.bak-before-*` files are never removed, and every recovery
    procedure in the runbook rests on it. A future change that added a prune would be a change
    to the rollback guarantee, and it should have to delete this test to make it.
    """
    import keel

    root = Path(keel.__file__).resolve().parent
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "bak-before" not in source:
            continue
        for line in source.splitlines():
            if "bak-before" not in line:
                continue
            if any(verb in line for verb in ("unlink", "rmtree", "os.remove", "shutil.move")):
                offenders.append(f"{path.name}: {line.strip()}")
    assert not offenders, (
        f"something now deletes an update backup: {offenders}. These are the data-recovery "
        "path; the release you need is the one before the release that broke."
    )


def test_an_unreadable_launch_folder_does_not_break_doctor(tmp_path) -> None:
    """`doctor` is what an operator runs when something is already wrong. A diagnostic that
    dies on the state it exists to describe is worse than no diagnostic."""
    missing = tmp_path / "not-a-directory"

    footprint = read_backup_footprint(missing)

    assert footprint.total_files == 0
    assert backup_footprint_findings(footprint)[0].status == "ok"


def test_a_hand_named_backup_never_claims_to_be_the_oldest_release(tmp_path) -> None:
    """`keel.db.bak-before-recordflow-...` exists in the live deployment and is not a version.

    Sorting it as one would report the oldest release as "recordflow", which is both wrong and
    unactionable -- the operator cannot decide whether to keep a release they cannot name.
    """
    _bak(tmp_path, "keel.db.bak-before-recordflow-20260820T075747")
    for version in ("0.9.1", "0.12.2", "0.13.1", "0.13.2"):
        _bak(tmp_path, f"keel.db.bak-before-{version}-20260901-120000")

    footprint = read_backup_footprint(tmp_path)

    assert footprint.oldest_version == "0.9.1"


def test_gather_findings_reads_the_real_launch_folder(tmp_path, valid_config_path, monkeypatch):
    """The wiring, not just the finding.

    A mutation replacing the launch-folder read with an empty footprint passed every test above,
    because the seeded deployment has no backups and both paths then report `ok`. The finding
    has to be shown reading somewhere real.

    Resolved through `update._launch_dir`, the same seam `keel update` uses, so doctor counts
    the folder the updater actually writes to rather than the process's cwd.
    """
    from keel.commands import update as update_mod

    launch = tmp_path / "launch"
    launch.mkdir()
    for version in ("0.4.0", "0.9.1", "0.12.2", "0.13.2"):
        (launch / f"keel.db.bak-before-{version}-20260901-120000").write_bytes(b"x" * 4096)
    monkeypatch.setattr(update_mod, "_launch_dir", lambda: launch)

    repo = _seeded_repo(tmp_path / "keel.db")
    findings = gather_findings(repo, load_config(valid_config_path), [], NOW)

    (footprint,) = [f for f in findings if f.name == "backups.footprint"]
    assert footprint.status == "warn", (
        "doctor reported no backups for a folder holding four -- it is not reading the launch "
        "folder the updater writes to"
    )
    assert "keel.db: 4" in footprint.detail


def test_a_folder_keel_cannot_read_measures_as_empty(tmp_path) -> None:
    """`Path.glob` returns nothing for a missing directory and for one with mode 000 alike, so
    this is a statement about behaviour rather than about a handler -- see the note in
    `read_backup_footprint` about the guard that was removed for being unreachable."""
    import os

    unreadable = tmp_path / "sealed"
    unreadable.mkdir()
    (unreadable / "keel.db.bak-before-0.1.0-1").write_bytes(b"x")
    os.chmod(unreadable, 0o000)
    try:
        footprint = read_backup_footprint(unreadable)
    finally:
        os.chmod(unreadable, 0o755)

    assert footprint.total_files == 0
    assert read_backup_footprint(tmp_path / "missing").total_files == 0
