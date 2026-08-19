"""Tests for `keel tui` -- the live, read-only, full-screen operator dashboard.

`keel tui` is a *view* over `keel status`'s own report: it must not re-derive Rail 11,
freshness, or autonomy logic, only style `StatusReport` into `ScreenLine`s. Mirrors
`tests/commands/test_status.py`'s fixture style (in-memory `Repository`, `_config` helper,
`NOW_TS` constant), plus the pure `build_screen`/`_freshness_style`/`render_plain`/`_paint`/
`run_once` seams that make the interactive `run_live` loop thin, untested I/O.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import click
import pytest
from click.testing import CliRunner

import keel.commands.tui as tui_mod
from keel.cli import cli
from keel.commands.activity import (
    ACTIVITY_HEADER,
    ActivityFeed,
    apply_scope,
    feed_from_lines,
    scope_start_ts,
)
from keel.commands.admission import DiscoverReport
from keel.commands.insights import (
    AccountSummary as InsightsAccountSummary,
)
from keel.commands.insights import (
    GateDistance,
    InsightsReport,
    JournalEntry,
    JournalReport,
    RuleTrackRecord,
)
from keel.commands.status import (
    AutonomyStatus,
    MarketSessionStatus,
    OpenPositionStatus,
    ProductFreshness,
    RuleSummary,
    StatusReport,
    SubscriptionStatusRow,
    WithdrawalAttestationStatus,
)
from keel.commands.tui import (
    _BALANCE_TIMEOUT_SEC,
    _CYCLE_RUN_NOTICE,
    _DISCOVER_TIMEOUT_SEC,
    _FETCH_RUN_NOTICE,
    _MONITOR_RUN_NOTICE,
    _REFRESH_MESSAGE,
    _SHORT_VERSION,
    CTRL_C_DISCLOSURE,
    AvailableBalance,
    ScreenLine,
    _activity_cursor,
    _activity_lines,
    _admission_line_style,
    _available_lines,
    _confirm_arm_autonomy,
    _follow_cursor,
    _footer_lines,
    _freshness_style,
    _guarded,
    _human_dt,
    _message_style,
    _paint,
    _refresh_balance,
    _run_notice_lines,
    _scroll_offset,
    _short_version,
    _stdio_is_interactive,
    _style_attrs,
    _visible_slice,
    build_activity_overlay,
    build_admission_screen_overlay,
    build_discover_overlay,
    build_help_screen,
    build_insights_screen,
    build_propose_overlay,
    build_screen,
    render_plain,
    run_live,
    run_once,
    toggle_autonomy,
    tui_cmd,
)
from keel.compliance import screen as screen_mod
from keel.config import (
    AutoTradeConfig,
    Caps,
    Config,
    DcaConfig,
    LoggingConfig,
    MarketDataConfig,
    MoneyMgmtConfig,
)
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.types import Candle, Granularity

NOW_TS = 1_800_000_000


@pytest.fixture
def repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    r = Repository(conn)
    r.set_state("kill_switch", False)
    return r


def _config(**overrides: Any) -> Config:
    base: dict[str, Any] = dict(
        allowlist=["BTC", "ETH"],
        target_weights={},
        risk_pct=Decimal("0.01"),
        caps=Caps(
            max_per_order_usd=Decimal("100000"),
            max_per_day_usd=Decimal("300000"),
            max_exposure_usd=Decimal("1000000"),
            max_per_asset_pct=Decimal("1"),
        ),
        market_data=MarketDataConfig(
            granularities=[Granularity.ONE_DAY, Granularity.ONE_HOUR], history_days=365
        ),
        auto_trade=AutoTradeConfig(mode="paper", interval_sec=900),
        money_mgmt=MoneyMgmtConfig(
            max_total_dd_pct=Decimal("0.20"), max_weekly_dd_pct=Decimal("0.08")
        ),
        dca=DcaConfig(budget_usd=Decimal("50"), cadence_days=7),
    )
    base.update(overrides)
    return Config(**base)


def _base_report(**overrides: Any) -> StatusReport:
    base: dict[str, Any] = dict(
        now_ts=NOW_TS,
        mode="paper",
        kill_switch_engaged=False,
        autonomy=AutonomyStatus(
            live=False,
            autonomous=False,
            autonomous_until=None,
            updated_ts=None,
            profile_readable=True,
        ),
        equity_state_mode="paper",
        high_water_mark=Decimal("10000"),
        drawdown_total_pct=Decimal("0.05"),
        drawdown_weekly_pct=Decimal("0.01"),
        max_total_dd_pct=Decimal("0.20"),
        max_weekly_dd_pct=Decimal("0.08"),
        rail11_status="ok",
        withdrawal_attestation=WithdrawalAttestationStatus(
            state="attested",
            enabled=True,
            attested_at=NOW_TS - 86400,
            expires_in_sec=6 * 86400,
            expired_for_sec=None,
        ),
        paper_cash_usdc=Decimal("955.25"),
        open_positions=[],
        rule_counts={},
        live_rules=[],
        data_freshness=[],
        subscriptions=[],
    )
    base.update(overrides)
    return StatusReport(**base)


# -- build_screen: sections present ------------------------------------------------------------


def test_build_screen_includes_mode_and_now() -> None:
    report = _base_report(mode="paper")
    lines = build_screen(report, NOW_TS)
    texts = [line.text for line in lines]
    assert any("paper" in t for t in texts)


def test_build_screen_includes_kill_switch() -> None:
    report = _base_report(kill_switch_engaged=True)
    lines = build_screen(report, NOW_TS)
    texts = [line.text.lower() for line in lines]
    assert any("kill" in t and "engaged" in t for t in texts)


def test_build_screen_includes_hwm_and_drawdown() -> None:
    report = _base_report(high_water_mark=Decimal("12345.6"))
    lines = build_screen(report, NOW_TS)
    texts = " ".join(line.text for line in lines)
    assert "12345.6" in texts
    assert "drawdown" in texts.lower()


def test_build_screen_includes_paper_cash_in_paper_mode() -> None:
    report = _base_report(mode="paper", paper_cash_usdc=Decimal("42.00"))
    lines = build_screen(report, NOW_TS)
    texts = " ".join(line.text for line in lines)
    assert "42.00" in texts


def test_build_screen_omits_paper_cash_outside_paper_mode() -> None:
    report = _base_report(mode="confirm", paper_cash_usdc=None)
    lines = build_screen(report, NOW_TS)
    texts = " ".join(line.text.lower() for line in lines)
    assert "paper_cash" not in texts


def test_build_screen_includes_each_open_position() -> None:
    pos = OpenPositionStatus(
        id=1,
        product_id="BTC-USD",
        rule_name="turtle_breakout",
        qty=Decimal("0.01"),
        entry_price=Decimal("65000"),
        opened_at=NOW_TS - 3600,
        has_bracket=True,
    )
    report = _base_report(open_positions=[pos])
    lines = build_screen(report, NOW_TS)
    texts = " ".join(line.text for line in lines)
    assert "BTC-USD" in texts
    assert "turtle_breakout" in texts
    assert "0.01" in texts
    assert "65000" in texts


def test_build_screen_includes_rule_counts_and_live_rules() -> None:
    rule = RuleSummary(
        id=7, kind="turtle_breakout", status="live", product_id="BTC-USD", params={"lookback": 20}
    )
    report = _base_report(rule_counts={"live": 1, "candidate": 2}, live_rules=[rule])
    lines = build_screen(report, NOW_TS)
    texts = " ".join(line.text for line in lines)
    assert "live=1" in texts
    assert "candidate=2" in texts
    assert "turtle_breakout" in texts


def test_build_screen_includes_each_freshness_row() -> None:
    freshness = [
        ProductFreshness("BTC-USD", "ONE_HOUR", NOW_TS - 3600, 3600),
        ProductFreshness("ETH-USD", None, None, None),
    ]
    report = _base_report(data_freshness=freshness)
    lines = build_screen(report, NOW_TS)
    texts = " ".join(line.text for line in lines)
    assert "BTC-USD" in texts
    assert "ETH-USD" in texts


def test_build_screen_includes_subscriptions() -> None:
    sub = SubscriptionStatusRow(
        venue="coinbase",
        tier_name="Preferred",
        pacing="opportunistic",
        stored_status="active",
        effective_status="active",
        effective_cap=Decimal("1000"),
    )
    report = _base_report(subscriptions=[sub])
    lines = build_screen(report, NOW_TS)
    texts = " ".join(line.text for line in lines)
    assert "coinbase" in texts
    assert "Preferred" in texts


def test_build_screen_footer_is_present_and_interval_independent() -> None:
    """The footer is now TWO lines (`_footer_lines`); `build_screen` appends both as its last two
    rows, and both must carry the original keybinding hints -- the second line does not replace
    the first, it adds the admission keys the first line had no room for."""
    report = _base_report()
    lines = build_screen(report, NOW_TS)
    footer = lines[-2:]
    assert all(line.style == "muted" for line in footer)
    joined = " ".join(line.text.lower() for line in footer)
    assert "quit" in joined
    assert "help" in joined


def test_footer_lines_contains_keybinding_hints() -> None:
    """The FIRST footer line is kept byte-for-byte as it was before the admission overlays
    existed (see `_footer_lines`'s docstring) -- every hint that used to live in the single line
    must still be found there, not merely somewhere across the two lines."""
    lines = _footer_lines()
    assert len(lines) == 2
    first = lines[0]
    assert first.style == "muted"
    text = first.text.lower()
    for hint in ("quit", "help", "refresh", "autonomy", "fetch", "insights"):
        assert hint in text


def test_footer_lines_second_line_documents_admission_keys() -> None:
    lines = _footer_lines()
    second = lines[1]
    assert second.style == "muted"
    text = second.text.lower()
    for hint in ("screen", "propose", "discover"):
        assert hint in text


# -- available-to-buy balance (v3) ---------------------------------------------------------------


def test_available_lines_none_is_empty() -> None:
    assert _available_lines(None) == []


def test_available_lines_with_amount_is_ok_style_and_informative() -> None:
    available = AvailableBalance(Decimal("10234.5"), "USDC", NOW_TS, None)
    lines = _available_lines(available)
    assert len(lines) == 1
    line = lines[0]
    assert line.style == "ok"
    assert "live account" in line.text
    assert "10,234.50" in line.text
    assert "USDC" in line.text


def test_available_lines_without_updated_ts_says_unknown_rather_than_now() -> None:
    """Same silent-"now" hazard as the autonomy line, on a FRESHNESS stamp.

    `updated_ts` is a separate field from `amount` and can be absent while the amount is
    present. A staleness marker that quietly reports the current instant is worse than one that
    admits it does not know, because an operator reads it as "just refreshed".
    """
    available = AvailableBalance(Decimal("10234.5"), "USDC", None, None)
    lines = _available_lines(available)
    assert len(lines) == 1
    line = lines[0]
    assert line.style == "ok"
    assert "10,234.50" in line.text
    assert "unknown" in line.text
    assert _human_dt(NOW_TS) not in line.text


def test_available_lines_with_error_is_warn_style() -> None:
    available = AvailableBalance(None, "USDC", NOW_TS, "no USDC balance")
    lines = _available_lines(available)
    assert len(lines) == 1
    line = lines[0]
    assert line.style == "warn"
    assert "unavailable" in line.text
    assert "no USDC balance" in line.text


def test_build_screen_includes_available_line_after_equity_before_positions() -> None:
    report = _base_report()
    available = AvailableBalance(Decimal("500.00"), "USDC", NOW_TS, None)
    lines = build_screen(report, NOW_TS, available=available)
    texts = [line.text for line in lines]
    available_idx = next(i for i, t in enumerate(texts) if "live account" in t)
    rail11_idx = next(i for i, t in enumerate(texts) if "rail11" in t.lower())
    positions_idx = next(i for i, t in enumerate(texts) if "open positions" in t.lower())
    assert rail11_idx < available_idx < positions_idx


def test_build_screen_without_available_has_no_available_line() -> None:
    """Default (no `available` kwarg) renders exactly as before -- protects `--once`."""
    report = _base_report()
    lines = build_screen(report, NOW_TS)
    texts = " ".join(line.text.lower() for line in lines)
    assert "live account" not in texts


def test_refresh_balance_returns_amount_on_success() -> None:
    config = _config()

    def open_state() -> tuple[Any, Config]:
        return object(), config

    result = _refresh_balance(open_state, lambda: NOW_TS, lambda cfg: Decimal("123.45"))

    assert result.amount == Decimal("123.45")
    assert result.quote == config.quote_currency
    assert result.updated_ts == NOW_TS
    assert result.error is None


def test_refresh_balance_none_amount_is_error_mentioning_quote_not_false_no_balance() -> None:
    """`balance_fn` returning `None` covers BOTH "no matching account" and a swallowed
    broker/auth/network error -- the message must not assert "no balance", which would wrongly
    tell an operator a deposit never landed when the real cause could be unrelated."""
    config = _config()

    def open_state() -> tuple[Any, Config]:
        return object(), config

    result = _refresh_balance(open_state, lambda: NOW_TS, lambda cfg: None)

    assert result.amount is None
    assert result.quote == config.quote_currency
    assert result.updated_ts == NOW_TS
    assert result.error is not None
    assert config.quote_currency in result.error
    assert "unreadable" in result.error
    assert "no " + config.quote_currency + " balance" != result.error


def test_refresh_balance_raising_balance_fn_is_contained() -> None:
    def open_state() -> tuple[Any, Config]:
        return object(), _config()

    def _raise(cfg: Config) -> Decimal | None:
        raise RuntimeError("boom")

    result = _refresh_balance(open_state, lambda: NOW_TS, _raise)

    assert result.amount is None
    assert result.updated_ts == NOW_TS
    assert result.error is not None
    assert "boom" in result.error


def test_refresh_balance_raising_balance_fn_truncates_long_error() -> None:
    """A stray huge (or sensitive) exception message must not be painted full-screen verbatim --
    `.error` is bounded, and the loop this feeds never crashes either way."""
    config = _config()

    def open_state() -> tuple[Any, Config]:
        return object(), config

    long_message = "x" * 5000

    def _raise(cfg: Config) -> Decimal | None:
        raise RuntimeError(long_message)

    result = _refresh_balance(open_state, lambda: NOW_TS, _raise)

    assert result.amount is None
    assert result.error is not None
    assert len(result.error) <= 120


# -- human-readable timestamps -------------------------------------------------------------------


def test_human_dt_matches_strftime_localtime() -> None:
    ts = 1_800_012_345
    assert _human_dt(ts) == time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def test_build_screen_title_shows_version_and_mode_without_datetime() -> None:
    """The header is `keel v<major>.<minor> · <mode> mode` -- version, no clock, no `now=` label.
    (Event times like a position's `opened_at` and autonomy lapse still render via `_human_dt`.)"""
    report = _base_report()
    title = build_screen(report, NOW_TS)[0]
    assert title.text == f"keel {_SHORT_VERSION} · paper mode"
    assert "now=" not in title.text
    assert _human_dt(NOW_TS) not in title.text


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("0.1.0", "v0.1.0"),
        ("0.2.5", "v0.2.5"),
        ("0.5.2", "v0.5.2"),
        # Build metadata rides on the PATCH segment (`0.5.2+79f35b9e73d5`), so a naive
        # `parts[2].isdigit()` is False and the patch would silently vanish -- which is exactly
        # the shape `keel --version` emits, i.e. the common case, not the edge case.
        ("10.34.1+abc", "v10.34.1"),
        ("0.5.2+79f35b9e73d5", "v0.5.2"),
        # No patch segment at all: show what exists rather than inventing a `.0`.
        ("2.0", "v2.0"),
        # A non-numeric patch (pre-release) degrades to major.minor -- still useful -- rather
        # than to `v?`, which would throw away the two segments we did parse.
        ("0.5.2rc1", "v0.5"),
        ("unknown", "v?"),
        ("1", "v?"),
        ("", "v?"),
    ],
)
def test_short_version(raw: str, expected: str) -> None:
    assert _short_version(raw) == expected


def test_open_position_lines_use_human_readable_opened_at() -> None:
    pos = OpenPositionStatus(
        id=1,
        product_id="BTC-USD",
        rule_name="turtle_breakout",
        qty=Decimal("0.01"),
        entry_price=Decimal("65000"),
        opened_at=NOW_TS - 3600,
        has_bracket=True,
    )
    report = _base_report(open_positions=[pos])
    lines = build_screen(report, NOW_TS)
    pos_line = next(line for line in lines if "BTC-USD" in line.text)
    assert f"opened_at={pos.opened_at}" not in pos_line.text
    assert _human_dt(pos.opened_at) in pos_line.text


def test_autonomy_lapsed_line_uses_human_readable_timestamp() -> None:
    until = NOW_TS - 60
    autonomy = AutonomyStatus(
        live=False,
        autonomous=True,
        autonomous_until=until,
        updated_ts=NOW_TS,
        profile_readable=True,
    )
    report = _base_report(autonomy=autonomy)
    lines = build_screen(report, NOW_TS)
    lapsed_line = next(line for line in lines if "LAPSED" in line.text)
    assert f"LAPSED at {until}" not in lapsed_line.text
    assert _human_dt(until) in lapsed_line.text


def test_autonomy_lapsed_line_without_a_deadline_says_unknown_rather_than_now() -> None:
    """A missing `autonomous_until` must not render as "lapsed this instant".

    `_human_dt` does not fail on `None` -- `time.localtime(None)` means "now" -- so without the
    guard this branch printed the CURRENT time as though it were the recorded lapse. The second
    assertion is the load-bearing one: absence has to read as absence, not as a fresh fact.
    """
    autonomy = AutonomyStatus(
        live=False,
        autonomous=True,
        autonomous_until=None,
        updated_ts=NOW_TS,
        profile_readable=True,
    )
    report = _base_report(autonomy=autonomy)
    lines = build_screen(report, NOW_TS)
    lapsed_line = next(line for line in lines if "LAPSED" in line.text)
    assert "unknown" in lapsed_line.text
    assert _human_dt(NOW_TS) not in lapsed_line.text


def test_autonomy_lapses_at_line_uses_human_readable_timestamp() -> None:
    until = NOW_TS + 3600
    autonomy = AutonomyStatus(
        live=True, autonomous=True, autonomous_until=until, updated_ts=NOW_TS, profile_readable=True
    )
    report = _base_report(autonomy=autonomy)
    lines = build_screen(report, NOW_TS)
    lapses_line = next(line for line in lines if "lapses at" in line.text)
    assert f"lapses at {until}" not in lapses_line.text
    assert _human_dt(until) in lapses_line.text


# -- style logic --------------------------------------------------------------------------------


def test_kill_switch_engaged_is_alert_style() -> None:
    report = _base_report(kill_switch_engaged=True)
    lines = build_screen(report, NOW_TS)
    kill_line = next(line for line in lines if "kill" in line.text.lower())
    assert kill_line.style == "alert"


def test_kill_switch_clear_is_ok_style() -> None:
    report = _base_report(kill_switch_engaged=False)
    lines = build_screen(report, NOW_TS)
    kill_line = next(line for line in lines if "kill" in line.text.lower())
    assert kill_line.style == "ok"


@pytest.mark.parametrize(
    "rail11_status,expected_style",
    [("HALTED", "alert"), ("unknown", "warn"), ("ok", "ok")],
)
def test_rail11_style_matches_status(rail11_status: str, expected_style: str) -> None:
    report = _base_report(rail11_status=rail11_status)
    lines = build_screen(report, NOW_TS)
    rail_line = next(line for line in lines if "rail11" in line.text.lower())
    assert rail_line.style == expected_style


def _attestation(state: str, **fields: Any) -> WithdrawalAttestationStatus:
    defaults: dict[str, Any] = dict(
        state=state,
        enabled=True,
        attested_at=NOW_TS - 86400,
        expires_in_sec=6 * 86400,
        expired_for_sec=None,
    )
    defaults.update(fields)
    return WithdrawalAttestationStatus(**defaults)


@pytest.mark.parametrize(
    "attestation,expected_style",
    [
        (_attestation("attested"), "ok"),
        (_attestation("expired", enabled=True, attested_at=NOW_TS - 19 * 86400,
                      expires_in_sec=None, expired_for_sec=12 * 86400), "warn"),
        (_attestation("unattested", enabled=None, attested_at=None,
                      expires_in_sec=None, expired_for_sec=None), "warn"),
        (_attestation("suspended", enabled=False, expires_in_sec=None), "warn"),
    ],
    ids=["attested", "expired", "unattested", "suspended"],
)
def test_rail17_line_renders_with_halt_severity(
    attestation: WithdrawalAttestationStatus, expected_style: str
) -> None:
    """The TUI reuses `render_human`'s exact rail-17 text, colored by whether entries are
    halted. `_base_report` is PAPER mode (the TUI's home turf), where rail 17 is not
    evaluated and a stale attestation halts nothing -- so every halted-state reading is a
    WARN there: a permanently-red paper dashboard is fatigue, not information (#340). The
    live-mode alert is pinned by its own test below."""
    report = _base_report(withdrawal_attestation=attestation)
    lines = build_screen(report, NOW_TS)
    rail_line = next(line for line in lines if line.text.startswith("rail 17"))
    assert rail_line.style == expected_style


def test_rail17_halted_states_are_alerts_when_the_rail_runs() -> None:
    """In a mode where rail 17 actually evaluates (live/confirm), every state but
    `attested` fails it closed -- an alert, the reading the 2026-08-14 veto deserved."""
    report = _base_report(
        mode="confirm",
        withdrawal_attestation=_attestation(
            "expired", enabled=True, attested_at=NOW_TS - 19 * 86400,
            expires_in_sec=None, expired_for_sec=12 * 86400,
        ),
    )
    lines = build_screen(report, NOW_TS)
    rail_line = next(line for line in lines if line.text.startswith("rail 17"))
    assert rail_line.style == "alert"
    assert rail_line.text == (
        "rail 17 (withdrawal capability): EXPIRED 12d ago -- entries halted; "
        "re-attest with keel withdrawals attest"
    )


def test_rail17_expired_line_names_the_halt_and_the_fix() -> None:
    report = _base_report(
        mode="confirm",
        withdrawal_attestation=_attestation(
            "expired",
            enabled=True,
            attested_at=NOW_TS - 19 * 86400,
            expires_in_sec=None,
            expired_for_sec=12 * 86400,
        ),
    )
    lines = build_screen(report, NOW_TS)
    rail_line = next(line for line in lines if line.text.startswith("rail 17"))
    assert rail_line.text == (
        "rail 17 (withdrawal capability): EXPIRED 12d ago -- entries halted; "
        "re-attest with keel withdrawals attest"
    )


# -- market session (FR-9) -----------------------------------------------------------------------
#
# The TUI reuses `render_human`'s exact session text (the `_rail17_line` discipline: two
# renderings of one state can never disagree). Unlike rail 17 there is NO paper-mode carve
# out: the session gate skips PAPER cycles too, so the same line is truthful in every mode.


def test_no_session_record_renders_no_session_line() -> None:
    """Crypto unchanged: a 24/7 venue never writes the state keys, so the dashboard does
    not grow a line that would only ever say 'open'."""
    lines = build_screen(_base_report(), NOW_TS)
    assert not any(line.text.startswith("market session") for line in lines)


@pytest.mark.parametrize(
    ("state", "expected_style"),
    [
        ("open", "ok"),
        ("closed", "muted"),
        ("clock_unavailable", "warn"),
    ],
    ids=["open", "closed", "clock-unavailable"],
)
def test_session_line_styles_expected_severity(state: str, expected_style: str) -> None:
    """A closed market is an EXPECTED state (every weekend) -- muted, never an alert, or
    the dashboard would train its operator to ignore colour. An unreadable clock is a
    degraded read worth a warn; an open market is simply ok."""
    report = _base_report(
        market_session=MarketSessionStatus(state=state, recorded_ts=NOW_TS - 60)
    )
    lines = build_screen(report, NOW_TS)
    session_line = next(line for line in lines if line.text.startswith("market session"))
    assert session_line.style == expected_style


def test_session_line_sits_directly_under_the_kill_switch() -> None:
    report = _base_report(
        market_session=MarketSessionStatus(state="closed", recorded_ts=NOW_TS - 60)
    )
    lines = build_screen(report, NOW_TS)
    kill_at = next(i for i, line in enumerate(lines) if line.text.startswith("kill_switch"))
    session_at = next(
        i for i, line in enumerate(lines) if line.text.startswith("market session")
    )
    assert session_at == kill_at + 1


def test_autonomy_live_is_alert_style() -> None:
    autonomy = AutonomyStatus(
        live=True, autonomous=True, autonomous_until=None, updated_ts=NOW_TS, profile_readable=True
    )
    report = _base_report(autonomy=autonomy)
    lines = build_screen(report, NOW_TS)
    autonomy_line = next(line for line in lines if "autonomy" in line.text.lower())
    assert autonomy_line.style == "alert"


def test_autonomy_off_is_muted_style() -> None:
    autonomy = AutonomyStatus(
        live=False, autonomous=False, autonomous_until=None, updated_ts=None, profile_readable=True
    )
    report = _base_report(autonomy=autonomy)
    lines = build_screen(report, NOW_TS)
    autonomy_line = next(line for line in lines if "autonomy" in line.text.lower())
    assert autonomy_line.style == "muted"


def test_autonomy_unreadable_profile_is_warn_style() -> None:
    autonomy = AutonomyStatus(
        live=False, autonomous=False, autonomous_until=None, updated_ts=None, profile_readable=False
    )
    report = _base_report(autonomy=autonomy)
    lines = build_screen(report, NOW_TS)
    warn_line = next(line for line in lines if "unreadable" in line.text.lower())
    assert warn_line.style == "warn"


def test_bracketless_position_has_warn_line() -> None:
    pos = OpenPositionStatus(
        id=2,
        product_id="ETH-USD",
        rule_name="dca",
        qty=Decimal("1"),
        entry_price=Decimal("3000"),
        opened_at=NOW_TS,
        has_bracket=False,
    )
    report = _base_report(open_positions=[pos])
    lines = build_screen(report, NOW_TS)
    bracket_lines = [line for line in lines if "bracket" in line.text.lower()]
    assert any(line.style == "warn" for line in bracket_lines)


# -- _freshness_style (pure, parametrised) -----------------------------------------------------


@pytest.mark.parametrize(
    "granularity,age_sec,expected",
    [
        ("ONE_HOUR", 60, "ok"),
        ("ONE_HOUR", 3600, "ok"),
        ("ONE_HOUR", 3600 * 3, "warn"),
        ("ONE_DAY", 86400, "ok"),
        ("ONE_DAY", 86400 * 3, "warn"),
        (None, 10, "warn"),
        ("ONE_HOUR", None, "warn"),
        (None, None, "warn"),
    ],
)
def test_freshness_style(granularity: str | None, age_sec: int | None, expected: str) -> None:
    assert _freshness_style(granularity, age_sec) == expected


def test_freshness_style_mutes_the_staleness_colour_under_a_closed_market() -> None:
    """`market_closed` mutes only the AGE-based warn: a behind series during a (still
    trusted) closure is the expected weekend shape. Fresh stays ok, and the no-data /
    unknown-granularity cells keep their warn -- a closed venue still serves history, so a
    cold cache is a pipeline problem, not a session artifact (the `fetch --check` rule)."""
    assert _freshness_style("ONE_HOUR", 3600 * 3, market_closed=True) == "muted"
    assert _freshness_style("ONE_HOUR", 60, market_closed=True) == "ok"
    assert _freshness_style(None, None, market_closed=True) == "warn"
    assert _freshness_style("NOT_A_GRANULARITY", 60, market_closed=True) == "warn"


def test_freshness_cells_render_muted_not_warn_while_the_market_is_closed() -> None:
    """Finding: `_freshness_style` painted warn for age > 2x period even while the
    dashboard's own session line said CLOSED -- two parts of one screen disagreeing about
    the same weekend. The session record is the source of truth: closed AND inside its
    trust window -> the behind series' cell is muted, like the session line itself."""
    report = _base_report(
        market_session=MarketSessionStatus(
            state="closed", recorded_ts=NOW_TS - 60, defused=True
        ),
        data_freshness=[ProductFreshness("BTC-USD", "ONE_HOUR", NOW_TS - 4 * 3600, 4 * 3600)],
    )
    lines = build_screen(report, NOW_TS)
    freshness_line = next(line for line in lines if line.text.startswith("  BTC-USD"))
    assert freshness_line.style == "muted"


def test_freshness_cells_still_warn_once_the_closed_record_is_stale() -> None:
    """`defused=False` (record outside its trust window) means the closure no longer
    vouches for the quiet -- the staleness colour comes back with the alert."""
    report = _base_report(
        market_session=MarketSessionStatus(
            state="closed", recorded_ts=NOW_TS - 60, defused=False
        ),
        data_freshness=[ProductFreshness("BTC-USD", "ONE_HOUR", NOW_TS - 4 * 3600, 4 * 3600)],
    )
    lines = build_screen(report, NOW_TS)
    freshness_line = next(line for line in lines if line.text.startswith("  BTC-USD"))
    assert freshness_line.style == "warn"


def test_no_data_freshness_cells_still_warn_while_the_market_is_closed() -> None:
    """The `fetch --check` rule, carried into colour: MISSING stays actionable when closed
    because a closed venue still serves history -- so 'no data' keeps the warning."""
    report = _base_report(
        market_session=MarketSessionStatus(
            state="closed", recorded_ts=NOW_TS - 60, defused=True
        ),
        data_freshness=[ProductFreshness("ETH-USD", None, None, None)],
    )
    lines = build_screen(report, NOW_TS)
    freshness_line = next(line for line in lines if line.text.startswith("  ETH-USD"))
    assert freshness_line.style == "warn"


# -- render_plain -----------------------------------------------------------------------------


def test_render_plain_matches_build_screen_text() -> None:
    report = _base_report()
    lines = build_screen(report, NOW_TS)
    plain = render_plain(report, NOW_TS)
    assert plain == [line.text for line in lines]


class _FakeCursesError(Exception):
    pass


def _fake_curses(*, has_colors: bool = True) -> SimpleNamespace:
    """A stand-in `curses` module -- distinct attribute-constant ints, `has_colors()`/
    `color_pair()`/`init_pair()` recorded on `.calls` (in call order), no real terminal
    required. Installed via `monkeypatch.setitem(sys.modules, "curses", ...)` since both
    `_style_attrs` and `run_live` do `import curses` lazily inside the function body, so the
    patched module is what they bind."""
    calls: list[str] = []
    fake = SimpleNamespace(
        A_BOLD=1 << 0,
        A_DIM=1 << 1,
        A_UNDERLINE=1 << 2,
        A_REVERSE=1 << 3,
        A_NORMAL=0,
        COLOR_RED=1,
        COLOR_YELLOW=2,
        COLOR_GREEN=3,
        KEY_UP=1001,
        KEY_DOWN=1002,
        KEY_PPAGE=1003,
        KEY_NPAGE=1004,
        KEY_HOME=1005,
        KEY_END=1006,
        KEY_ENTER=1007,
        error=_FakeCursesError,
        has_colors=lambda: has_colors,
        start_color=lambda: calls.append("start_color"),
        use_default_colors=lambda: calls.append("use_default_colors"),
        init_pair=lambda n, fg, bg: calls.append(f"init_pair:{n}"),
        color_pair=lambda n: 1 << (10 + n),
        curs_set=lambda visibility: None,
        def_prog_mode=lambda: calls.append("def_prog_mode"),
        endwin=lambda: calls.append("endwin"),
        reset_prog_mode=lambda: calls.append("reset_prog_mode"),
        calls=calls,
    )
    return fake


# -- _style_attrs (fake curses module, no real terminal) ---------------------------------------


def test_style_attrs_calls_use_default_colors_before_init_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard: `curses.wrapper` never calls `use_default_colors()`, so an `init_pair`
    background of `-1` is illegal and raises `curses.error` -- caught, but silently dropping ALL
    colour. `_style_attrs` must call `use_default_colors()` itself, before the first
    `init_pair`."""
    fake_curses = _fake_curses()
    monkeypatch.setitem(sys.modules, "curses", fake_curses)

    attrs = _style_attrs()

    assert "use_default_colors" in fake_curses.calls
    assert fake_curses.calls.index("use_default_colors") < fake_curses.calls.index("init_pair:1")
    assert attrs["alert"] & fake_curses.color_pair(1)
    assert attrs["warn"] & fake_curses.color_pair(2)
    assert attrs["ok"] & fake_curses.color_pair(3)


# -- _paint (fake stdscr, no real terminal) -----------------------------------------------------


class _FakeStdscr:
    def __init__(self, height: int, width: int) -> None:
        self._height = height
        self._width = width
        self.calls: list[tuple[int, int, str, int]] = []
        self.refresh_calls = 0

    def getmaxyx(self) -> tuple[int, int]:
        return (self._height, self._width)

    def addstr(self, y: int, x: int, text: str, attr: int = 0) -> None:
        self.calls.append((y, x, text, attr))

    def erase(self) -> None:
        pass

    def refresh(self) -> None:
        self.refresh_calls += 1


def test_paint_does_not_raise_on_tiny_window() -> None:
    lines = [
        ScreenLine("keel · paper mode", "heading"),
        ScreenLine("kill_switch: clear", "ok"),
        ScreenLine("autonomy: off", "muted"),
        ScreenLine("open positions: none", "normal"),
        ScreenLine("q quit · read-only (no broker)", "muted"),
    ]
    stdscr = _FakeStdscr(height=3, width=10)
    _paint(stdscr, lines)  # must not raise even though window is smaller than content


def test_paint_records_addstr_per_visible_line() -> None:
    lines = [
        ScreenLine("line one", "normal"),
        ScreenLine("line two", "ok"),
    ]
    stdscr = _FakeStdscr(height=24, width=80)
    _paint(stdscr, lines)
    assert len(stdscr.calls) == 2
    ys = [call[0] for call in stdscr.calls]
    assert ys == [0, 1]


def test_paint_truncates_to_window_width() -> None:
    lines = [ScreenLine("x" * 200, "normal")]
    stdscr = _FakeStdscr(height=24, width=20)
    _paint(stdscr, lines)
    assert len(stdscr.calls) == 1
    text = stdscr.calls[0][2]
    assert len(text) <= 20


def test_paint_applies_distinct_attrs_by_style() -> None:
    lines = [
        ScreenLine("alert line", "alert"),
        ScreenLine("normal line", "normal"),
    ]
    stdscr = _FakeStdscr(height=24, width=80)
    _paint(stdscr, lines)
    attrs = [call[3] for call in stdscr.calls]
    # Not asserting exact bit values (curses colour init may be unavailable off a real terminal)
    # -- just that the two differently-styled lines don't collapse to the same attr.
    assert attrs[0] != attrs[1]


def test_the_loop_painted_run_notices_wrap_inside_the_80_column_clip() -> None:
    """[review #405] The frozen-screen notices are painted straight by the loop, not
    through a console builder -- so their fit needs its own proof. The raw bodies are
    long (the cycle's is 144 chars) and `_paint` CLIPS at the window width: painted as
    one line, the tail -- the part that says what happens to orders -- is exactly what
    a 80-column terminal loses. The notice helper wraps every body to the builders'
    78-column budget, tail included, and carries the Ctrl-C line."""
    bodies = (
        _CYCLE_RUN_NOTICE,
        _MONITOR_RUN_NOTICE,
        _FETCH_RUN_NOTICE,
        # the C4-era simulate notice rides the same helper since the C5 review
        # flagged its identical clipping (91 chars, tail lost),
        "simulating... please wait (this can take minutes; the "
        "screen is frozen exactly like the CLI)",
    )
    # the raw bodies genuinely need the wrapping this test exists to force
    assert len(_CYCLE_RUN_NOTICE) > 80
    assert len(_FETCH_RUN_NOTICE) > 80
    for body in bodies:
        lines = _run_notice_lines(body)
        for line in lines:
            assert len(line.text) <= 78, line.text
        # nothing is lost to the wrap: every word of the body renders, tail included
        rendered = " ".join(line.text for line in lines)
        for word in body.replace("(", " ").replace(")", " ").replace(";", " ").split():
            assert word in rendered, word
        # and the Ctrl-C disclosure rides every frozen notice
        assert any("Ctrl-C" in line.text for line in lines)
    assert "exits the whole console" in CTRL_C_DISCLOSURE
    assert len(CTRL_C_DISCLOSURE) > 78  # the disclosure itself needs the wrap too


# -- run_once -------------------------------------------------------------------------------


def test_run_once_captures_full_frame(repo: Repository) -> None:
    repo.set_state("drawdown_total_pct", Decimal("0.05"))
    repo.set_state("drawdown_weekly_pct", Decimal("0.01"))
    config = _config()

    echoed: list[str] = []

    def open_state() -> tuple[Repository, Any]:
        return repo, config

    run_once(open_state, lambda: NOW_TS, echoed.append)

    assert echoed  # a full frame was produced
    joined = "\n".join(echoed)
    assert "paper mode" in joined
    assert "quit" in joined.lower()
    assert "help" in joined.lower()


# -- run_live (fake curses module, no real terminal) ---------------------------------------------


class _ScriptedStdscr(_FakeStdscr):
    """Like `_FakeStdscr`, but `getch()` returns `-1` (no key) until `quit_after` polls have
    happened, then returns `q` so the loop under test terminates deterministically."""

    def __init__(self, height: int, width: int, quit_after: int) -> None:
        super().__init__(height, width)
        self._quit_after = quit_after
        self._polls = 0

    def timeout(self, ms: int) -> None:
        pass

    def getch(self) -> int:
        self._polls += 1
        return ord("q") if self._polls >= self._quit_after else -1


def test_run_live_survives_transient_read_error_and_keeps_polling(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config()
    stdscr = _ScriptedStdscr(height=24, width=80, quit_after=2)

    fake_curses = _fake_curses()
    fake_curses.wrapper = lambda fn: fn(stdscr)
    monkeypatch.setitem(sys.modules, "curses", fake_curses)

    opens: list[int] = []

    def open_state() -> tuple[Repository, Any]:
        opens.append(1)
        # `run_live` calls `open_state()` at least twice on the first normal-mode iteration: once
        # for the status report itself (call 1, the one this test targets to exercise the per-poll
        # read-error safeguard -- the balance refresh was deliberately moved AFTER the paint, so
        # it no longer blocks the first frame), and once for the slow-cadence balance refresh
        # (call 2, which swallows its own errors and never reaches this test's assertions).
        if len(opens) == 1:
            raise sqlite3.OperationalError("database is locked")
        return repo, config

    run_live(open_state, lambda: NOW_TS, interval=0.01)

    # The first poll's read error didn't kill the loop -- a second poll (the happy path) ran too.
    assert len(opens) >= 2
    painted_texts = [call[2] for call in stdscr.calls]
    assert any("status read failed" in t for t in painted_texts)
    assert any("paper" in t for t in painted_texts)


class _KeySequenceStdscr(_FakeStdscr):
    """Like `_FakeStdscr`, but `getch()` replays a scripted sequence of keycodes, one per poll,
    then returns `q` forever once exhausted -- so a test can drive the loop through an exact
    sequence of mode transitions deterministically."""

    def __init__(self, height: int, width: int, keys: list[int]) -> None:
        super().__init__(height, width)
        self._keys = list(keys)

    def timeout(self, ms: int) -> None:
        pass

    def getch(self) -> int:
        if self._keys:
            return self._keys.pop(0)
        return ord("q")


def test_run_live_i_opens_insights_overlay_and_esc_closes_it(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config()
    # poll1: normal -> 'i' opens insights. poll2: insights (offset 0). poll3: Esc closes back to
    # normal. poll4: normal -> 'q' quits (via the stdscr's post-exhaustion default).
    keys = [ord("i"), -1, 27]
    stdscr = _KeySequenceStdscr(height=24, width=80, keys=keys)

    fake_curses = _fake_curses()
    fake_curses.wrapper = lambda fn: fn(stdscr)
    monkeypatch.setitem(sys.modules, "curses", fake_curses)

    def open_state() -> tuple[Repository, Any]:
        return repo, config

    run_live(open_state, lambda: NOW_TS, interval=0.01)

    painted_texts = [call[2] for call in stdscr.calls]
    insights_idx = next(i for i, t in enumerate(painted_texts) if "keel tui -- insights" in t)
    # Proves Esc (key 27) actually closed the overlay and returned control to the dashboard --
    # not just that the loop happened to end (which `q` would also produce, even if the Esc
    # branch itself were deleted): a LATER frame, after the insights heading was painted, must
    # paint the normal-mode dashboard's own title line again.
    dashboard_after_idx = next(
        i for i, t in enumerate(painted_texts) if i > insights_idx and "paper mode" in t
    )
    assert dashboard_after_idx > insights_idx


def test_run_live_scrolling_keys_move_insights_offset(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config()
    fake_curses = _fake_curses()
    # poll1: normal -> 'i'. poll2: insights offset=0, KEY_DOWN -> offset=1. poll3: insights
    # offset=1, paint recorded. Esc closes. poll4: normal -> quits (post-exhaustion default).
    keys = [ord("i"), fake_curses.KEY_DOWN, 27]
    stdscr = _KeySequenceStdscr(height=5, width=80, keys=keys)
    fake_curses.wrapper = lambda fn: fn(stdscr)
    monkeypatch.setitem(sys.modules, "curses", fake_curses)

    def open_state() -> tuple[Repository, Any]:
        return repo, config

    run_live(open_state, lambda: NOW_TS, interval=0.01)

    # Each frame paints exactly `height` (5) addstr calls (the insights content is long enough
    # to fill the window). Frame1=normal, Frame2=insights offset=0, Frame3=insights offset=1.
    frames = [stdscr.calls[i : i + 5] for i in range(0, len(stdscr.calls), 5)]
    assert len(frames) >= 3
    frame2_top = frames[1][0][2]
    frame3_top = frames[2][0][2]
    assert "keel tui -- insights" in frame2_top  # offset 0 starts at the heading
    assert frame2_top != frame3_top  # scrolling down moved the visible window


def test_run_live_insights_survives_transient_read_error_and_keeps_polling(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The insights branch has its OWN `try`/`except` (separate from normal mode's), guarding
    `open_state`/`gather_status`/`build_insights_report`/`build_journal_report`. A transient
    failure there (e.g. `database is locked` from a concurrent `keel agent` writer) must paint an
    `insights read failed` alert line -- not crash or hang the loop -- and the loop must still be
    able to close the overlay and keep running afterwards."""
    config = _config()
    # poll1: normal -> open_state call #1 (status) + call #2 (balance refresh) both succeed;
    # 'i' opens insights. poll2: insights -> open_state call #3 raises; Esc closes back to
    # normal. poll3: normal -> open_state call #4 succeeds; 'q' quits (post-exhaustion default).
    keys = [ord("i"), 27]
    stdscr = _KeySequenceStdscr(height=24, width=80, keys=keys)

    fake_curses = _fake_curses()
    fake_curses.wrapper = lambda fn: fn(stdscr)
    monkeypatch.setitem(sys.modules, "curses", fake_curses)

    opens: list[int] = []

    def open_state() -> tuple[Repository, Any]:
        opens.append(1)
        if len(opens) == 3:
            raise RuntimeError("database is locked")
        return repo, config

    run_live(open_state, lambda: NOW_TS, interval=0.01)

    # The loop returned (no hang/crash) and made further open_state calls after the failure.
    assert len(opens) >= 4
    painted_texts = [call[2] for call in stdscr.calls]
    failed_idx = next(i for i, t in enumerate(painted_texts) if "insights read failed" in t)
    assert "database is locked" in painted_texts[failed_idx]
    # ... and the loop kept going afterwards: Esc still closed the (failed) overlay and a later
    # frame painted the normal dashboard again.
    assert any(
        i > failed_idx and "paper mode" in t for i, t in enumerate(painted_texts)
    )


# -- run_live: screen / propose overlays (offline, DB-only) ---------------------------------------


def test_run_live_s_opens_screen_overlay_and_esc_closes_it(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mirrors `test_run_live_i_opens_insights_overlay_and_esc_closes_it`: 's' opens the screen
    overlay, Esc closes it back to the dashboard."""
    config = _config()
    keys = [ord("s"), -1, 27]
    stdscr = _KeySequenceStdscr(height=24, width=80, keys=keys)

    fake_curses = _fake_curses()
    fake_curses.wrapper = lambda fn: fn(stdscr)
    monkeypatch.setitem(sys.modules, "curses", fake_curses)

    def open_state() -> tuple[Repository, Any]:
        return repo, config

    run_live(open_state, lambda: NOW_TS, interval=0.01)

    painted_texts = [call[2] for call in stdscr.calls]
    screen_idx = next(i for i, t in enumerate(painted_texts) if "keel tui -- screen" in t)
    dashboard_after_idx = next(
        i for i, t in enumerate(painted_texts) if i > screen_idx and "paper mode" in t
    )
    assert dashboard_after_idx > screen_idx


def test_run_live_p_opens_propose_overlay_and_esc_closes_it(
    repo: Repository, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Mirrors the 's' test above, for 'p'. `proposals_dir` points at a tmp_path subdirectory
    (rather than the config default, `~/keel/proposals`) so this test never reads a real
    deployment's proposals directory."""
    config = _config(proposals_dir=str(tmp_path / "proposals"))
    keys = [ord("p"), -1, 27]
    stdscr = _KeySequenceStdscr(height=24, width=80, keys=keys)

    fake_curses = _fake_curses()
    fake_curses.wrapper = lambda fn: fn(stdscr)
    monkeypatch.setitem(sys.modules, "curses", fake_curses)

    def open_state() -> tuple[Repository, Any]:
        return repo, config

    run_live(open_state, lambda: NOW_TS, interval=0.01)

    painted_texts = [call[2] for call in stdscr.calls]
    propose_idx = next(i for i, t in enumerate(painted_texts) if "keel tui -- propose" in t)
    dashboard_after_idx = next(
        i for i, t in enumerate(painted_texts) if i > propose_idx and "paper mode" in t
    )
    assert dashboard_after_idx > propose_idx


def _seed_daily_history(repo: Repository, product: str, bars: int) -> None:
    """Enough cached daily bars for `history` and `liquidity` to PASS the screen outright
    (`volume * close` = 10,000,000 per bar, well over the 1,000,000 median floor), so a REJECT
    from the gate can only be the shariah criterion the asset has no attestation for."""
    repo.upsert_candles(
        product,
        Granularity.ONE_DAY,
        [
            Candle(
                ts=i * 86400,
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100"),
                volume=Decimal("100000"),
            )
            for i in range(bars)
        ],
    )


def test_run_live_screen_overlay_paints_the_real_verdict_from_the_single_gate(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Design constraint 4 -- every candidate routes through `keel.cli._screen_product`, "so
    nothing drifts onto a laxer gate" -- was convention-only exactly where it is WIRED. Replacing
    `_screen_product` with an always-ADMIT stub in `_do_screen_report` left the entire suite
    green, because the s/p overlay tests only assert that a title paints and Esc closes; not one
    of them ever looked at a verdict.

    So: BTC is seeded with ample history and liquidity but is never attested. The only thing that
    can reject it is the shariah criterion, which only the real gate applies -- `screen_asset`
    fails CLOSED on `attestation=None`. The overlay must therefore paint `REJECT` and name
    `attestation: MISSING`. An always-ADMIT stub paints `ADMIT` with no failure lines and kills
    both assertions."""
    config = _config(allowlist=["BTC"])
    _seed_daily_history(repo, "BTC-USD", 1500)
    keys = [ord("s"), -1, 27]
    stdscr = _KeySequenceStdscr(height=40, width=120, keys=keys)

    fake_curses = _fake_curses()
    fake_curses.wrapper = lambda fn: fn(stdscr)
    monkeypatch.setitem(sys.modules, "curses", fake_curses)

    def open_state() -> tuple[Repository, Any]:
        return repo, config

    run_live(open_state, lambda: NOW_TS, interval=0.01)

    painted_texts = [call[2] for call in stdscr.calls]
    overlay_idx = next(i for i, t in enumerate(painted_texts) if "keel tui -- screen" in t)
    after = painted_texts[overlay_idx:]
    assert any(t.startswith("REJECT") and "BTC" in t for t in after)
    assert any("attestation: MISSING" in t for t in after)
    # The premise: the data criteria really did pass, so REJECT above is the shariah gate's doing
    # and not an incidental history/liquidity shortfall that any stub would also produce.
    assert not any("✗ history" in t or "✗ liquidity" in t for t in after)


def test_run_live_propose_overlay_paints_the_real_verdict_from_the_single_gate(
    repo: Repository, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """The `p` half of the same wiring gap the `s` test above closes -- `_do_propose_view` passes
    `_screen_product` into `build_propose_view` on the same convention-only basis, and swapping it
    for an always-ADMIT stub was equally invisible to the suite.

    SOL is shortlisted with ample cached history and liquidity but no attestation, so the only
    thing that can reject it is `screen_asset` failing CLOSED on `attestation=None` -- something
    only the real gate does."""
    proposals = tmp_path / "proposals"
    proposals.mkdir()
    (proposals / "shortlist.json").write_text(
        json.dumps(
            {"candidates": [{"asset": "SOL", "rationale": "r", "sources": ["https://x.invalid"]}]}
        )
    )
    config = _config(proposals_dir=str(proposals))
    _seed_daily_history(repo, "SOL-USD", 1500)
    keys = [ord("p"), -1, 27]
    stdscr = _KeySequenceStdscr(height=40, width=200, keys=keys)

    fake_curses = _fake_curses()
    fake_curses.wrapper = lambda fn: fn(stdscr)
    monkeypatch.setitem(sys.modules, "curses", fake_curses)

    def open_state() -> tuple[Repository, Any]:
        return repo, config

    run_live(open_state, lambda: NOW_TS, interval=0.01)

    painted_texts = [call[2] for call in stdscr.calls]
    overlay_idx = next(i for i, t in enumerate(painted_texts) if "keel tui -- propose" in t)
    after = painted_texts[overlay_idx:]
    assert any(t.startswith("REJECT") and "SOL" in t for t in after)
    assert any("attestation: MISSING" in t for t in after)
    assert any("keel assets attest SOL" in t for t in after)  # the next step, not just a verdict
    # The premise: the data criteria really did pass, so REJECT is the shariah gate's doing.
    assert not any("✗ history" in t or "✗ liquidity" in t for t in after)


def test_run_live_propose_overlay_reports_a_non_utf8_shortlist_calmly(
    repo: Repository, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """The TUI half of the `UnicodeDecodeError`-is-not-an-`OSError` fix. A UTF-16LE+BOM shortlist
    escaped `build_propose_view`'s fail-soft branches entirely and was caught only by the propose
    branch's broad `except Exception`, which repainted `propose read failed: 'utf-8' codec can't
    decode byte 0xff...` on every poll forever -- naming no file and offering no next step. It
    must render as the same calm, actionable `unreadable` overlay a permissions error renders
    as."""
    proposals = tmp_path / "proposals"
    proposals.mkdir()
    (proposals / "shortlist.json").write_bytes(
        json.dumps(
            {"candidates": [{"asset": "SOL", "rationale": "r", "sources": ["https://x.invalid"]}]}
        ).encode("utf-16")
    )
    config = _config(proposals_dir=str(proposals))
    keys = [ord("p"), -1, 27]
    # Wide enough that `_paint`'s clip-to-window-width does not truncate the tmp_path before the
    # filename this test is about -- the clipping is real terminal behaviour, not the bug here.
    stdscr = _KeySequenceStdscr(height=40, width=400, keys=keys)

    fake_curses = _fake_curses()
    fake_curses.wrapper = lambda fn: fn(stdscr)
    monkeypatch.setitem(sys.modules, "curses", fake_curses)

    def open_state() -> tuple[Repository, Any]:
        return repo, config

    run_live(open_state, lambda: NOW_TS, interval=0.01)

    painted_texts = [call[2] for call in stdscr.calls]
    assert any("could not read the shortlist file" in t for t in painted_texts)
    assert any("shortlist.json" in t for t in painted_texts)  # WHICH file, by name
    assert not any("propose read failed" in t for t in painted_texts)


def test_run_live_screen_survives_transient_read_error_and_keeps_polling(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The screen branch has its OWN try/except, mirroring insights' -- a transient failure (e.g.
    `database is locked`) must paint a `screen read failed` alert line, not crash or hang, and the
    loop must still be able to close the overlay and keep running afterwards."""
    config = _config()
    # poll1: normal -> open_state call #1 (status) + #2 (balance refresh) both succeed; 's' opens
    # screen. poll2: screen -> open_state call #3 (inside `_do_screen_report`) raises; Esc closes
    # back to normal. poll3: normal -> open_state call #4 succeeds; 'q' quits (default).
    keys = [ord("s"), 27]
    stdscr = _KeySequenceStdscr(height=24, width=80, keys=keys)

    fake_curses = _fake_curses()
    fake_curses.wrapper = lambda fn: fn(stdscr)
    monkeypatch.setitem(sys.modules, "curses", fake_curses)

    opens: list[int] = []

    def open_state() -> tuple[Repository, Any]:
        opens.append(1)
        if len(opens) == 3:
            raise RuntimeError("database is locked")
        return repo, config

    run_live(open_state, lambda: NOW_TS, interval=0.01)

    assert len(opens) >= 4
    painted_texts = [call[2] for call in stdscr.calls]
    failed_idx = next(i for i, t in enumerate(painted_texts) if "screen read failed" in t)
    assert "database is locked" in painted_texts[failed_idx]
    assert any(i > failed_idx and "paper mode" in t for i, t in enumerate(painted_texts))


def test_run_live_propose_survives_transient_read_error_and_keeps_polling(
    repo: Repository, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Same shape as the screen version above, for 'p'."""
    config = _config(proposals_dir=str(tmp_path / "proposals"))
    keys = [ord("p"), 27]
    stdscr = _KeySequenceStdscr(height=24, width=80, keys=keys)

    fake_curses = _fake_curses()
    fake_curses.wrapper = lambda fn: fn(stdscr)
    monkeypatch.setitem(sys.modules, "curses", fake_curses)

    opens: list[int] = []

    def open_state() -> tuple[Repository, Any]:
        opens.append(1)
        if len(opens) == 3:
            raise RuntimeError("database is locked")
        return repo, config

    run_live(open_state, lambda: NOW_TS, interval=0.01)

    assert len(opens) >= 4
    painted_texts = [call[2] for call in stdscr.calls]
    failed_idx = next(i for i, t in enumerate(painted_texts) if "propose read failed" in t)
    assert "database is locked" in painted_texts[failed_idx]
    assert any(i > failed_idx and "paper mode" in t for i, t in enumerate(painted_texts))


def test_run_live_screen_and_propose_never_construct_a_broker(
    repo: Repository, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """`screen`/`propose` are fully OFFLINE -- opening either, polling, and closing must never
    reach `_build_broker` of their own accord. `_build_broker` IS still called once during this
    run -- by `run_live`'s pre-existing, unrelated automatic "available to buy" balance refresh,
    which (with a constant `now_fn`) fires exactly once, on the very first poll, and never again.
    `len(calls) == 1` here is exactly that one call, proving screen/propose contributed zero
    calls of their own -- the DIRECT proof (screen/propose never import `_build_broker` at all)
    lives in `_do_screen_report`'s/`_do_propose_view`'s own source; this is the behavioural
    cross-check."""
    config = _config(proposals_dir=str(tmp_path / "proposals"))
    calls: list[Any] = []

    class _FakeBroker:
        def get_accounts(self) -> list[Any]:
            return []

    def _fake_build_broker(cfg: Any, timeout: int | None = None) -> _FakeBroker:
        calls.append(cfg)
        return _FakeBroker()

    monkeypatch.setattr("keel.commands._common._build_broker", _fake_build_broker)

    # poll1: normal -> 's'. poll2: screen, no key. poll3: Esc closes. poll4: normal -> 'p'.
    # poll5: propose, no key. poll6: Esc closes. poll7: normal -> 'q' (post-exhaustion default).
    keys = [ord("s"), -1, 27, ord("p"), -1, 27]
    stdscr = _KeySequenceStdscr(height=24, width=80, keys=keys)

    fake_curses = _fake_curses()
    fake_curses.wrapper = lambda fn: fn(stdscr)
    monkeypatch.setitem(sys.modules, "curses", fake_curses)

    def open_state() -> tuple[Repository, Any]:
        return repo, config

    run_live(open_state, lambda: NOW_TS, interval=0.01)

    assert len(calls) == 1


# -- run_live: discover overlay (the network-gated one) --------------------------------------------


def test_run_live_discover_opens_armed_and_never_touches_the_network_until_enter(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE most important test in this batch. Pressing 'd', polling several times, then closing
    must never call `list_products` -- the ONE network call this whole overlay can ever make is
    gated behind an explicit Enter keypress, not behind opening the overlay or an ordinary poll.
    (`_build_broker` itself is still called once by the pre-existing automatic balance refresh,
    unrelated to discover -- see `test_run_live_screen_and_propose_never_construct_a_broker`'s
    docstring for why that call doesn't confuse this assertion; `list_products` is the call that
    is unique to, and gated by, discover, and it is the one this test pins to zero.)"""
    config = _config()
    list_products_calls: list[int] = []

    class _FakeBroker:
        def get_accounts(self) -> list[Any]:
            return []

        def list_products(self) -> list[dict]:
            list_products_calls.append(1)
            return []

    def _fake_build_broker(cfg: Any, timeout: int | None = None) -> _FakeBroker:
        return _FakeBroker()

    monkeypatch.setattr("keel.commands._common._build_broker", _fake_build_broker)

    # poll1: normal -> 'd' opens discover, ARMED. poll2, poll3: no key -- repaint the armed state,
    # no fetch. poll4: Esc closes. poll5: normal -> 'q' quits (post-exhaustion default).
    keys = [ord("d"), -1, -1, 27]
    stdscr = _KeySequenceStdscr(height=24, width=80, keys=keys)

    fake_curses = _fake_curses()
    fake_curses.wrapper = lambda fn: fn(stdscr)
    monkeypatch.setitem(sys.modules, "curses", fake_curses)

    def open_state() -> tuple[Repository, Any]:
        return repo, config

    run_live(open_state, lambda: NOW_TS, interval=0.01)

    assert list_products_calls == []
    painted_texts = [call[2] for call in stdscr.calls]
    assert any("ARMED" in t for t in painted_texts)
    assert any("paper mode" in t for t in painted_texts)  # closed back to the dashboard


def test_run_live_discover_enter_calls_list_products_once_then_holds_the_result(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The counterpart to the gating test above: Enter DOES run the one network call, exactly
    once -- and further polls while the overlay stays open repaint the HELD result rather than
    re-fetching (no further `list_products` calls without another Enter)."""
    config = _config()
    list_products_calls: list[int] = []

    class _FakeBroker:
        def get_accounts(self) -> list[Any]:
            return []

        def list_products(self) -> list[dict]:
            list_products_calls.append(1)
            return [
                {
                    "product_id": "SOL-USD",
                    "quote_currency_id": "USD",
                    "status": "online",
                    "trading_disabled": False,
                    "is_disabled": False,
                    "view_only": False,
                    "quote_24h_volume": "9000000",
                    "base_name": "Solana",
                }
            ]

    def _fake_build_broker(cfg: Any, timeout: int | None = None) -> _FakeBroker:
        return _FakeBroker()

    monkeypatch.setattr("keel.commands._common._build_broker", _fake_build_broker)

    # poll1: normal -> 'd'. poll2: discover ARMED -> Enter runs the one fetch. poll3, poll4: no
    # key -- repaint the held result, no further call. poll5: Esc closes.
    keys = [ord("d"), 10, -1, -1, 27]
    stdscr = _KeySequenceStdscr(height=24, width=80, keys=keys)

    fake_curses = _fake_curses()
    fake_curses.wrapper = lambda fn: fn(stdscr)
    monkeypatch.setitem(sys.modules, "curses", fake_curses)

    def open_state() -> tuple[Repository, Any]:
        return repo, config

    run_live(open_state, lambda: NOW_TS, interval=0.01)

    assert len(list_products_calls) == 1
    painted_texts = [call[2] for call in stdscr.calls]
    assert any("SOL-USD" in t for t in painted_texts)


def test_run_live_discover_enter_raising_paints_readable_failure_and_keeps_polling(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broker/network/auth failure on Enter must paint a readable `discover failed` line, not
    crash the loop -- and Esc still closes the overlay afterwards, repainting the dashboard."""
    config = _config()

    class _FakeBroker:
        def get_accounts(self) -> list[Any]:
            return []

        def list_products(self) -> list[dict]:
            raise RuntimeError("venue unreachable")

    def _fake_build_broker(cfg: Any, timeout: int | None = None) -> _FakeBroker:
        return _FakeBroker()

    monkeypatch.setattr("keel.commands._common._build_broker", _fake_build_broker)

    keys = [ord("d"), 10, 27]
    stdscr = _KeySequenceStdscr(height=24, width=80, keys=keys)

    fake_curses = _fake_curses()
    fake_curses.wrapper = lambda fn: fn(stdscr)
    monkeypatch.setitem(sys.modules, "curses", fake_curses)

    def open_state() -> tuple[Repository, Any]:
        return repo, config

    run_live(open_state, lambda: NOW_TS, interval=0.01)

    painted_texts = [call[2] for call in stdscr.calls]
    failed_idx = next(i for i, t in enumerate(painted_texts) if "discover failed" in t)
    assert "venue unreachable" in painted_texts[failed_idx]
    assert any(i > failed_idx and "paper mode" in t for i, t in enumerate(painted_texts))


def test_run_live_discover_reopening_after_a_run_is_armed_not_stale(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reopening the overlay after a run must be ARMED again, never a silent repaint of the
    previous run's stale candidates.

    NAMED for what it actually pins, which is a DISJUNCTION, not a single line. Two independent
    clears stand between the held result and the reopened overlay -- the close branch's
    (`run_live`, discover mode, `q`/`Esc`/`d`) and the normal-mode `d` branch's self-labelled
    belt-and-braces one -- and reaching the reopened overlay necessarily runs BOTH. Deleting
    either one alone leaves this test green. It was previously called
    `..._closing_discards_the_held_result`, which claimed to pin the close branch specifically;
    nothing observable from outside `run_live` can distinguish the two, because `mode` only ever
    becomes `discover` via the normal-mode `d` branch that also clears. Keeping both clears is
    deliberate defence in depth; this test guards the property they jointly provide."""
    config = _config()

    class _FakeBroker:
        def get_accounts(self) -> list[Any]:
            return []

        def list_products(self) -> list[dict]:
            return [
                {
                    "product_id": "SOL-USD",
                    "quote_currency_id": "USD",
                    "status": "online",
                    "trading_disabled": False,
                    "is_disabled": False,
                    "view_only": False,
                    "quote_24h_volume": "9000000",
                    "base_name": "Solana",
                }
            ]

    def _fake_build_broker(cfg: Any, timeout: int | None = None) -> _FakeBroker:
        return _FakeBroker()

    monkeypatch.setattr("keel.commands._common._build_broker", _fake_build_broker)

    # poll1: normal -> 'd'. poll2: discover ARMED -> Enter fetches SOL-USD. poll3: Esc closes
    # (discards). poll4: normal -> 'd' reopens. poll5: discover -- must be ARMED again, no
    # candidates carried over. poll6: Esc closes.
    keys = [ord("d"), 10, 27, ord("d"), -1, 27]
    stdscr = _KeySequenceStdscr(height=24, width=80, keys=keys)

    fake_curses = _fake_curses()
    fake_curses.wrapper = lambda fn: fn(stdscr)
    monkeypatch.setitem(sys.modules, "curses", fake_curses)

    def open_state() -> tuple[Repository, Any]:
        return repo, config

    run_live(open_state, lambda: NOW_TS, interval=0.01)

    painted_texts = [call[2] for call in stdscr.calls]
    sol_idx = next(i for i, t in enumerate(painted_texts) if "SOL-USD" in t)
    # Every frame painted AFTER the SOL-USD result must be the armed re-explanation, not a
    # repaint of the stale candidate list.
    reopened_armed_idx = next(
        i for i, t in enumerate(painted_texts) if i > sol_idx and "ARMED" in t
    )
    assert not any(
        "SOL-USD" in t for t in painted_texts[reopened_armed_idx:]
    )


def test_run_live_discover_bounds_its_one_network_call_with_the_discover_timeout(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_DISCOVER_TIMEOUT_SEC` was correctly wired but had no test: dropping the `timeout=` kwarg
    (or reusing `_BALANCE_TIMEOUT_SEC`) left the suite green. It exists because the operator waits
    on this call with the screen frozen behind a "contacting venue" frame, so a hung connection
    must fail and say so rather than freeze the dashboard until Ctrl-C. Pinned per-call, not
    globally: the unrelated balance refresh in the same run uses its OWN, shorter bound, and this
    test would not notice the two being collapsed into one if it only checked "some timeout was
    passed"."""
    config = _config()
    timeouts: list[tuple[str, Any]] = []

    class _FakeBroker:
        def get_accounts(self) -> list[Any]:
            return []

        def list_products(self) -> list[dict]:
            return []

    def _fake_build_broker(cfg: Any, timeout: int | None = None) -> _FakeBroker:
        timeouts.append(("build", timeout))
        return _FakeBroker()

    monkeypatch.setattr("keel.commands._common._build_broker", _fake_build_broker)

    # poll1: normal -> 'd'. poll2: discover ARMED -> Enter runs the one call. poll3: Esc closes.
    keys = [ord("d"), 10, 27]
    stdscr = _KeySequenceStdscr(height=24, width=80, keys=keys)

    fake_curses = _fake_curses()
    fake_curses.wrapper = lambda fn: fn(stdscr)
    monkeypatch.setitem(sys.modules, "curses", fake_curses)

    def open_state() -> tuple[Repository, Any]:
        return repo, config

    run_live(open_state, lambda: NOW_TS, interval=0.01)

    passed = [t for _, t in timeouts]
    assert _DISCOVER_TIMEOUT_SEC in passed
    # The balance refresh (the other broker build in this run) keeps its own, distinct bound.
    assert _BALANCE_TIMEOUT_SEC in passed
    assert _DISCOVER_TIMEOUT_SEC != _BALANCE_TIMEOUT_SEC


def test_run_live_read_error_does_not_swallow_keyboard_interrupt(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`except Exception`, not `except BaseException` -- Ctrl-C during the per-poll read must
    still propagate out to `run_live`'s own `try/except KeyboardInterrupt`, which swallows it."""
    stdscr = _ScriptedStdscr(height=24, width=80, quit_after=100)

    fake_curses = _fake_curses()
    fake_curses.wrapper = lambda fn: fn(stdscr)
    monkeypatch.setitem(sys.modules, "curses", fake_curses)

    def open_state() -> tuple[Repository, Any]:
        raise KeyboardInterrupt

    run_live(open_state, lambda: NOW_TS, interval=0.01)  # must not raise


# -- build_help_screen / _visible_slice (pure) ---------------------------------------------------


def test_build_help_screen_documents_every_key_and_safety_notes() -> None:
    lines = build_help_screen()
    text = " ".join(line.text.lower() for line in lines)
    for word in ("autonomy", "fetch", "quit", "scroll", "refresh", "help"):
        assert word in text
    # v3: the admission workflow's three overlays; C3: attest as a TYPED console form.
    for word in ("screen", "propose", "discover", "attest"):
        assert word in text
    assert lines[0].style == "heading"


def test_build_help_screen_documents_discover_network_gating_and_the_typed_attest_gate() -> None:
    """The safety notes must be explicit about the two things that make `discover`
    different from `screen`/`propose`, and must state attest's CURRENT contract honestly:
    attest IS invokable from the console (the Compliance menu's form, the scout browser's
    `a` step) -- what keeps it safe is the TYPED confirmation at the end of the form (type
    the asset code back; withdrawals attest types its own CLI phrase), NOT a stale
    "CLI-only, never a keypress" claim this dashboard outgrew in C3."""
    lines = build_help_screen()
    text = " ".join(line.text.lower() for line in lines)
    assert "third deliberate network exception" in text
    assert "cli-only" not in text
    assert "never a keypress" not in text
    assert "keel assets attest" in text
    assert "type the asset code" in text


def test_help_says_which_account_is_on_screen_and_how_to_switch() -> None:
    """paper and live render in an IDENTICAL layout, so nothing on the dashboard itself says which
    account a number belongs to -- and reading a paper figure as a live one is the most expensive
    confusion this project has. The help must name the field that disambiguates, and the
    `--config`/`--db` pair that changes it, including that `--db` defaults to the PAPER database:
    an operator who omits it gets paper numbers under a live-looking command. Scoped to the
    section, like `test_help_says_the_live_balance_line_is_itself_a_venue_call`, so a word three
    sections away cannot satisfy it."""
    section: list[str] = []
    lines = build_help_screen()
    start = next(
        i for i, line in enumerate(lines) if line.text.strip() == "Which account is this?"
    )
    for line in lines[start:]:
        if not line.text.strip():
            break
        section.append(line.text.lower())
    text = " ".join(section)
    assert "separate" in text
    assert "equity_state_mode" in text
    assert "--config" in text
    assert "--db" in text
    assert "keel-live.db" in text
    # The default is the trap, not a detail: omitting --db reads keel.db and reports on paper.
    assert "defaults to keel.db" in text


#: Every operator-facing place that counts this dashboard's network touches. The count is a
#: SAFETY claim -- an operator deciding whether a keypress can reach the venue reads it and stops
#: looking -- so an undercount is a bug, not a typo, and it must be pinned wherever it is stated.
_NETWORK_COUNT_SURFACES = {
    "module docstring": tui_mod.__doc__ or "",
    "tui_cmd --help": tui_cmd.__doc__ or "",
    "help overlay": "\n".join(line.text for line in build_help_screen()),
    "ARMED discover overlay": "\n".join(
        line.text for line in build_discover_overlay(None)
    ),
}


@pytest.mark.parametrize("surface", sorted(_NETWORK_COUNT_SURFACES))
def test_no_surface_undercounts_the_dashboards_network_touches(surface: str) -> None:
    """`run_live` touches the network in exactly THREE places: the automatic ~30s live-balance
    refresh (`_refresh_balance` -> `get_accounts`), `f` fetch, and `d`+Enter. Three surfaces --
    including the operator-facing ARMED overlay, read at the moment of deciding whether to make
    a live call -- used to call discover "the SECOND deliberate network exception", silently
    forgetting the balance refresh that had been firing every 30 seconds since v3. Only
    `tui_cmd`'s own docstring had it right. An operator who trusts "second" concludes that
    closing the overlay leaves the dashboard offline; it does not."""
    text = _NETWORK_COUNT_SURFACES[surface].lower()
    assert "second deliberate network exception" not in text
    # Either phrasing states the true count -- three surfaces frame it from discover's side
    # ("the THIRD..."), `tui_cmd --help` from the dashboard's ("there are exactly three").
    assert "third deliberate network exception" in text or "exactly three" in text
    # Naming the other two is what makes the count checkable rather than a bare number, and it is
    # specifically the balance refresh that every undercount forgot.
    assert "balance" in text
    assert "fetch" in text


def test_help_says_the_live_balance_line_is_itself_a_venue_call() -> None:
    """The help's own Safety notes tell the operator that screen and propose are offline and that
    fetch/discover are the exceptions -- but the "Live balance" section only ever said the number
    is "refreshed every ~30s", never that refreshing it CONTACTS THE VENUE. That omission is what
    made "second deliberate network exception" read as plausible three sections later."""
    balance_section: list[str] = []
    lines = build_help_screen()
    start = next(i for i, line in enumerate(lines) if line.text.strip() == "Live balance")
    for line in lines[start:]:
        if not line.text.strip():
            break
        balance_section.append(line.text.lower())
    text = " ".join(balance_section)
    assert "live call" in text or "network" in text
    assert "get_accounts" in text


def test_build_help_screen_is_longer_than_a_small_terminal() -> None:
    lines = build_help_screen()
    assert len(lines) > 24


def _help_section(heading_prefix: str) -> str:
    """The lowercased body of one help section -- from the line starting `heading_prefix` up to
    the next blank -- so a glossary assertion cannot be satisfied by a word appearing three
    sections away. Mirrors `test_help_says_the_live_balance_line_is_itself_a_venue_call`."""
    lines = build_help_screen()
    start = next(i for i, line in enumerate(lines) if line.text.startswith(heading_prefix))
    body: list[str] = []
    for line in lines[start:]:
        if not line.text.strip():
            break
        body.append(line.text.lower())
    return " ".join(body)


def test_help_screen_glossary_defines_every_field_name_the_dashboard_prints() -> None:
    """`_equity_lines` and the activity overlay print keel's INTERNAL field names verbatim --
    `equity_state_mode`, `high_water_mark`, `rail11`, `paper_cash_usdc`, `sig blk ent exi err`.
    Nothing on the dashboard explains any of them, so the help must, by name."""
    text = _help_section("Glossary")
    for term in (
        "cycle",
        "signal",
        "sig / blk / ent / exi / err",
        "paper_cash_usdc",
        "equity_state_mode",
        "high_water_mark",
        "drawdown",
        "rail11",
    ):
        assert term in text, term


def test_help_screen_glossary_distinguishes_no_setup_from_a_vetoed_setup() -> None:
    """The distinction the whole glossary exists for: `sig 0` (found nothing) and `sig 1 blk 1`
    (found something, a rail stopped it) look equally idle on a dashboard of zeroes, and an
    operator who conflates them reads a correctly-declining deployment as a dead one."""
    text = _help_section("Glossary")
    assert "`sig 1 blk 1`" in text
    assert "`sig 0`" in text
    assert "rail vetoes" in text
    # A cycle that finds nothing is the normal case, not a fault -- said in those terms.
    assert "one cycle per day" in text
    assert "normal case, not a fault" in text


def test_help_screen_glossary_says_paper_cash_is_synthetic_and_paper_only() -> None:
    """`paper_cash_usdc: 11000` is the single most mistakable number on the dashboard: it reads
    like a broker balance. It is neither real nor present in live mode."""
    text = _help_section("Glossary")
    assert "not a real broker balance" in text
    assert "only in paper mode" in text
    # The two equity accounts are separate histories, not two views of one account.
    assert "separate accounts with separate histories" in text


def test_help_screen_glossary_sources_the_drawdown_ceilings_to_config() -> None:
    """The parenthesised ceilings on the `drawdown:` line are config values, not live readings --
    an operator who thinks they are measurements has no idea where to change them."""
    text = _help_section("Glossary")
    assert "come from config" in text


def test_visible_slice_clamps_too_large_offset() -> None:
    lines = [ScreenLine(str(i), "normal") for i in range(50)]
    result = _visible_slice(lines, offset=1000, height=10)
    assert result == lines[40:50]


def test_visible_slice_height_covers_all_lines() -> None:
    lines = [ScreenLine(str(i), "normal") for i in range(5)]
    result = _visible_slice(lines, offset=0, height=100)
    assert result == lines


def test_visible_slice_zero_height_returns_empty() -> None:
    lines = [ScreenLine(str(i), "normal") for i in range(5)]
    assert _visible_slice(lines, offset=0, height=0) == []


def test_visible_slice_negative_offset_clamped_to_zero() -> None:
    lines = [ScreenLine(str(i), "normal") for i in range(5)]
    result = _visible_slice(lines, offset=-10, height=2)
    assert result == lines[0:2]


def test_visible_slice_offset_past_end_returns_tail() -> None:
    lines = [ScreenLine(str(i), "normal") for i in range(5)]
    result = _visible_slice(lines, offset=4, height=2)
    assert result == lines[3:5]


def test_visible_slice_empty_lines_never_raises() -> None:
    assert _visible_slice([], offset=5, height=10) == []
    assert _visible_slice([], offset=0, height=0) == []


# -- build_insights_screen (pure, reuses keel.commands.insights renderers) ----------------------


def _insights_account(**overrides: Any) -> InsightsAccountSummary:
    base: dict[str, Any] = dict(
        mode="paper",
        equity_state_mode="paper",
        high_water_mark=Decimal("10000"),
        drawdown_total_pct=Decimal("0.05"),
        drawdown_weekly_pct=Decimal("0.01"),
        max_total_dd_pct=Decimal("0.20"),
        max_weekly_dd_pct=Decimal("0.08"),
        rail11_status="ok",
        paper_cash_usdc=Decimal("955.25"),
    )
    base.update(overrides)
    return InsightsAccountSummary(**base)


def _gate(**overrides: Any) -> GateDistance:
    base: dict[str, Any] = dict(
        rule_name="turtle_breakout",
        promotion_class="default",
        n_trades=12,
        min_trades=30,
        trades_remaining=18,
        win_rate=0.55,
        min_win_rate=0.5,
        realized_rr=Decimal("1.8"),
        min_rr=Decimal("1.5"),
        expectancy=Decimal("12.5"),
        min_expectancy=Decimal("0"),
        passing=False,
        blocking_reasons=["n_trades 12 < 30"],
    )
    base.update(overrides)
    return GateDistance(**base)


def _rule_track_record(**overrides: Any) -> RuleTrackRecord:
    base: dict[str, Any] = dict(
        rule_name="turtle_breakout",
        status="paper",
        promotion_class="default",
        n_trades=12,
        win_rate=0.55,
        avg_win=Decimal("25.00"),
        avg_loss=Decimal("-14.00"),
        realized_rr=Decimal("1.79"),
        expectancy=Decimal("12.5"),
        profit_factor=Decimal("2.1"),
        max_drawdown=Decimal("30.0"),
        significant=False,
        gate=_gate(),
    )
    base.update(overrides)
    return RuleTrackRecord(**base)


def _insights_report(**overrides: Any) -> InsightsReport:
    base: dict[str, Any] = dict(
        now_ts=NOW_TS,
        account=_insights_account(),
        rules=[_rule_track_record()],
        closed_trade_count=12,
    )
    base.update(overrides)
    return InsightsReport(**base)


def _journal_entry(**overrides: Any) -> JournalEntry:
    base: dict[str, Any] = dict(
        closed_at=NOW_TS - 60,
        opened_at=NOW_TS - 3600,
        rule_name="turtle_breakout",
        product_id="BTC-USD",
        qty=Decimal("0.01"),
        entry_fill=Decimal("64000"),
        exit_fill=Decimal("65000"),
        pnl_net=Decimal("10.00"),
        fees=Decimal("0.50"),
        r_multiple=Decimal("1.2"),
        is_dca=False,
        outcome="win",
    )
    base.update(overrides)
    return JournalEntry(**base)


def _journal_report(**overrides: Any) -> JournalReport:
    base: dict[str, Any] = dict(
        now_ts=NOW_TS,
        mode="paper",
        entries=[_journal_entry()],
        total_count=1,
        filters={"rule": None, "asset": None, "since_ts": None, "until_ts": None,
                 "limit": 5, "include_open": False},
    )
    base.update(overrides)
    return JournalReport(**base)


def test_build_insights_screen_is_nonempty_and_titled() -> None:
    report = _insights_report()
    lines = build_insights_screen(report)
    assert lines
    assert lines[0].style == "heading"
    assert "insights" in lines[0].text.lower()


def test_build_insights_screen_includes_rule_name_and_gate() -> None:
    report = _insights_report()
    lines = build_insights_screen(report)
    texts = " ".join(line.text for line in lines)
    assert "turtle_breakout" in texts
    assert "gate:" in texts


def test_build_insights_screen_includes_account_summary_line() -> None:
    report = _insights_report()
    lines = build_insights_screen(report)
    texts = " ".join(line.text for line in lines)
    assert "mode: paper" in texts
    assert "paper_cash_usdc" in texts


def test_build_insights_screen_handles_zero_trade_report_with_friendly_line() -> None:
    """An empty-DB/zero-trade report must render a friendly explanatory line, not a blank."""
    report = _insights_report(rules=[], closed_trade_count=0)
    lines = build_insights_screen(report)
    texts = [line.text for line in lines]
    assert any(t.strip() for t in texts)  # not all-blank
    joined = " ".join(texts).lower()
    assert "no rule track record yet" in joined


def test_build_insights_screen_gate_passing_is_ok_style() -> None:
    passing_gate = _gate(passing=True, blocking_reasons=[])
    report = _insights_report(rules=[_rule_track_record(gate=passing_gate)])
    lines = build_insights_screen(report)
    gate_line = next(line for line in lines if line.text.strip().startswith("gate:"))
    assert gate_line.style == "ok"
    assert "PASSING" in gate_line.text


def test_build_insights_screen_gate_blocked_is_warn_style() -> None:
    report = _insights_report(rules=[_rule_track_record(gate=_gate(passing=False))])
    lines = build_insights_screen(report)
    gate_line = next(line for line in lines if line.text.strip().startswith("gate:"))
    assert gate_line.style == "warn"
    assert "blocked" in gate_line.text


def test_build_insights_screen_without_journal_report_omits_recent_trades() -> None:
    report = _insights_report()
    lines = build_insights_screen(report)
    texts = " ".join(line.text.lower() for line in lines)
    assert "recent trades" not in texts


def test_build_insights_screen_includes_journal_tail_when_provided() -> None:
    report = _insights_report()
    journal = _journal_report()
    lines = build_insights_screen(report, journal)
    texts = " ".join(line.text for line in lines)
    assert "recent trades" in texts.lower()
    assert "BTC-USD" in texts


def test_build_insights_screen_is_read_only_pure() -> None:
    """Calling it twice on the same fixture reports must be idempotent -- it never mutates its
    inputs (frozen dataclasses would raise on mutation anyway, but this guards intent)."""
    report = _insights_report()
    journal = _journal_report()
    first = build_insights_screen(report, journal)
    second = build_insights_screen(report, journal)
    assert [line.text for line in first] == [line.text for line in second]


# -- _scroll_offset (pure, shared by help/insights/screen/propose/discover) ----------------------


def test_scroll_offset_up_and_down_move_by_one() -> None:
    fake_curses = _fake_curses()
    assert _scroll_offset(fake_curses.KEY_UP, 5, height=10, total=50, curses_mod=fake_curses) == 4
    assert _scroll_offset(ord("k"), 5, height=10, total=50, curses_mod=fake_curses) == 4
    assert _scroll_offset(fake_curses.KEY_DOWN, 5, height=10, total=50, curses_mod=fake_curses) == 6
    assert _scroll_offset(ord("j"), 5, height=10, total=50, curses_mod=fake_curses) == 6


def test_scroll_offset_page_up_and_down_move_by_almost_a_screen() -> None:
    fake_curses = _fake_curses()
    result = _scroll_offset(fake_curses.KEY_PPAGE, 20, height=10, total=50, curses_mod=fake_curses)
    assert result == 11
    result = _scroll_offset(fake_curses.KEY_NPAGE, 20, height=10, total=50, curses_mod=fake_curses)
    assert result == 29


def test_scroll_offset_home_jumps_to_top() -> None:
    fake_curses = _fake_curses()
    result = _scroll_offset(fake_curses.KEY_HOME, 20, height=10, total=50, curses_mod=fake_curses)
    assert result == 0


@pytest.mark.parametrize(
    ("height", "total", "expected"),
    [
        (10, 50, 40),  # the ordinary case: last full page
        (10, 51, 41),  # +1 line of content moves the floor by exactly 1 (catches an off-by-one)
        (10, 10, 0),   # content exactly fills the window -- nowhere to scroll
        (10, 3, 0),    # content SHORTER than the window -- End must not scroll past the top
        (1, 50, 49),   # a one-row terminal still lands on the true last line
    ],
)
def test_scroll_offset_end_jumps_to_the_last_full_page(
    height: int, total: int, expected: int
) -> None:
    """`End` sets the offset to `total` and lets the shared clamp bring it back to the last full
    page.

    Parametrized rather than asserted at a single point because one point does not pin the
    RELATIONSHIP between window and content: the interesting cases are the boundaries, where
    content exactly fills the window, is shorter than it (End must be a no-op, not a scroll into
    blank space), or is one line longer than a page (the floor must move by exactly one).

    Worth knowing before "tightening" this: `offset = total` is not the only correct
    implementation. The trailing clamp is `min(offset, max(0, total - height))`, so ANY value at
    or above `total - height` is indistinguishable from any other -- `total - 1` included. That
    is not an off-by-one waiting to be caught, it is the same function; a test asserting `total`
    specifically would be pinning an implementation detail rather than the behaviour. What these
    cases do catch is an End that lands BELOW the floor (e.g. `total // 2`, or a forgotten clamp
    letting it run past the end)."""
    fake_curses = _fake_curses()
    result = _scroll_offset(
        fake_curses.KEY_END, 0, height=height, total=total, curses_mod=fake_curses
    )
    assert result == expected


def test_scroll_offset_clamps_negative_to_zero() -> None:
    fake_curses = _fake_curses()
    assert _scroll_offset(fake_curses.KEY_UP, 0, height=10, total=50, curses_mod=fake_curses) == 0


def test_scroll_offset_clamps_past_the_last_full_page() -> None:
    fake_curses = _fake_curses()
    result = _scroll_offset(fake_curses.KEY_DOWN, 40, height=10, total=50, curses_mod=fake_curses)
    assert result == 40


def test_scroll_offset_unrecognized_key_is_a_noop() -> None:
    """A no-key poll (`getch()` returns `-1` on timeout) or any other unmapped keycode must leave
    the offset exactly where it was (still clamped) -- this is what makes it safe to route EVERY
    keypress in a scrollable overlay through this function, not just the six scroll keys."""
    fake_curses = _fake_curses()
    assert _scroll_offset(-1, 5, height=10, total=50, curses_mod=fake_curses) == 5


# -- _admission_line_style (pure) -----------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("ADMIT   BTC      bars=2000 median_daily_volume=2000000 on-allowlist attested", "ok"),
        ("REJECT  SOL      bars=0 median_daily_volume=0 not-on-allowlist UNATTESTED", "warn"),
        ("    ✗ history: only 500 bars, need 1460", "warn"),
        ("    ! sector unknown -- treated as non-yielding until attested", "warn"),
        ("INVALID  missing asset: {'rationale': 'r'}", "warn"),
        ("⚠️  These are PROPOSALS, not admissions. Nothing above has been screened.", "alert"),
        ("some other plain line", "normal"),
        ("shortlist: /home/user/keel/proposals/2026-08-01.json", "normal"),
    ],
)
def test_admission_line_style_conventions(text: str, expected: str) -> None:
    assert _admission_line_style(text) == expected


def test_admission_line_style_missing_history_line_is_muted_not_alert_or_warn() -> None:
    """`! no local history` -- and its MISSING-DATA continuation line from `missing_history_
    lines` -- must NOT read as an alarm: `keel.compliance.screen.split_failures`'s whole reason
    for existing is that "never fetched" is not a verdict about the asset (see
    `render_screen_report`'s own docstring), so painting it `"warn"`/`"alert"`, the colours a
    REAL rejection reason gets, would visually assert the opposite of what the text says."""
    missing = (
        "    ! no local history for SOL-USD -- run `keel fetch --products SOL-USD` first, "
        "then re-screen."
    )
    style = _admission_line_style(missing)
    assert style == "muted"
    assert style not in ("alert", "warn")

    continuation = (
        "      This is a MISSING-DATA verdict, not a verdict about the asset: it is not too "
        "young, we have simply never fetched candles for it."
    )
    style = _admission_line_style(continuation)
    assert style == "muted"
    assert style not in ("alert", "warn")


# -- build_admission_screen_overlay / build_propose_overlay / build_discover_overlay (pure) ------


def _fake_screen_fn(*, admitted: bool = True):
    """A minimal `ScreenFn` stub -- deliberately NOT `_screen_product` itself, since these tests
    exercise `build_admission_screen_overlay`/`build_propose_overlay` (pure styling over an
    already-built report), not the admission gate's own logic (covered by
    `tests/commands/test_admission.py`)."""

    def _screen(repo: Repository, product: str, quote: str):
        facts = screen_mod.MarketFacts(
            asset=product.split("-")[0],
            daily_bars=2000,
            median_daily_volume=Decimal("2000000"),
            quotable_in_settlement_currency=True,
            product_id=product,
            venue="coinbase",
        )
        result = screen_mod.ScreenResult(asset=facts.asset, admitted=admitted)
        return facts, result

    return _screen


def test_build_admission_screen_overlay_is_nonempty_titled_and_headed(repo: Repository) -> None:
    from keel.commands.admission import build_screen_report

    config = _config(allowlist=["BTC"])
    report = build_screen_report(repo, config, _fake_screen_fn())

    lines = build_admission_screen_overlay(report)

    assert lines
    assert lines[0].style == "heading"
    assert lines[0].text == "keel tui -- screen"


def test_build_propose_overlay_is_nonempty_titled_and_headed(repo: Repository, tmp_path) -> None:
    from keel.commands.admission import build_propose_view

    config = _config(proposals_dir=str(tmp_path / "proposals"))
    view = build_propose_view(repo, config, _fake_screen_fn(), directory=tmp_path / "proposals")

    lines = build_propose_overlay(view)

    assert lines
    assert lines[0].style == "heading"
    assert lines[0].text == "keel tui -- propose"


def test_build_discover_overlay_with_report_is_nonempty_titled_and_headed() -> None:
    candidate = screen_mod.Candidate(
        product_id="SOL-USD", asset="SOL", base_name="Solana", quote_24h_volume=Decimal("9000000")
    )
    report = DiscoverReport(
        quote="USD",
        venue_product_count=900,
        candidates=(candidate,),
        min_quote_24h_volume=Decimal("5000000"),
        excluded=screen_mod.DiscoveryExclusions(below_volume_floor=899),
    )

    lines = build_discover_overlay(report)

    assert lines
    assert lines[0].style == "heading"
    assert lines[0].text == "keel tui -- discover"
    assert any("SOL-USD" in line.text for line in lines)


def test_build_discover_overlay_none_renders_armed_explanation_and_the_run_key() -> None:
    """The state `build_discover_overlay(None)` renders is the proof that opening the discover
    overlay makes NO network call -- it must say so plainly, name what Enter will do, and name
    Enter itself, not just render a blank or "loading" screen."""
    lines = build_discover_overlay(None)

    assert lines[0].style == "heading"
    assert lines[0].text == "keel tui -- discover"
    text = " ".join(line.text for line in lines)
    assert "ARMED" in text
    assert "no network call" in text.lower()
    assert "Enter" in text


def test_build_discover_overlay_with_error_renders_readable_failure_not_a_traceback() -> None:
    lines = build_discover_overlay(None, error="could not reach coinbase.com")

    text = " ".join(line.text for line in lines)
    assert "discover failed" in text.lower()
    assert "could not reach coinbase.com" in text
    failure_line = next(line for line in lines if "discover failed" in line.text.lower())
    assert failure_line.style == "alert"


# -- toggle_autonomy / _guarded (injectable actions, no curses/network) ---------------------------


class _FakeProfile:
    def __init__(self, live: bool) -> None:
        self._live = live

    def is_autonomous(self, now_ts: int) -> bool:
        return self._live


class _FakeAutonomyRepo:
    def __init__(self, live: bool) -> None:
        self._profile = _FakeProfile(live)
        self.set_autonomous_calls: list[tuple[bool, int]] = []

    def get_profile(self) -> _FakeProfile:
        return self._profile

    def set_autonomous(self, value: bool, now_ts: int, expires_ts: int | None = None) -> None:
        self.set_autonomous_calls.append((value, now_ts))


def test_toggle_autonomy_on_to_off_is_immediate_and_ungated() -> None:
    repo = _FakeAutonomyRepo(live=True)
    confirm_calls: list[bool] = []

    result = toggle_autonomy(repo, NOW_TS, lambda: confirm_calls.append(True) or True)

    assert repo.set_autonomous_calls == [(False, NOW_TS)]
    assert not confirm_calls  # confirm_fn never consulted for de-risking
    assert "off" in result.lower()


def test_toggle_autonomy_off_to_on_confirmed() -> None:
    repo = _FakeAutonomyRepo(live=False)

    result = toggle_autonomy(repo, NOW_TS, lambda: True)

    assert repo.set_autonomous_calls == [(True, NOW_TS)]
    assert "on" in result.lower()


def test_toggle_autonomy_off_to_on_declined() -> None:
    repo = _FakeAutonomyRepo(live=False)

    result = toggle_autonomy(repo, NOW_TS, lambda: False)

    assert repo.set_autonomous_calls == []
    assert "cancelled" in result.lower()


def test_guarded_returns_fn_result_on_success() -> None:
    assert _guarded("fetch", lambda: "ok") == "ok"


def test_guarded_returns_label_failed_message_on_exception() -> None:
    def _raise() -> str:
        raise RuntimeError("boom")

    result = _guarded("fetch", _raise)
    assert result == "fetch failed: boom"


def test_guarded_does_not_catch_keyboard_interrupt() -> None:
    def _raise() -> str:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        _guarded("fetch", _raise)


# -- _message_style -------------------------------------------------------------------------------


def test_message_style_arm_on_is_alert_not_ok() -> None:
    """The dangerous ON transition must never be painted as reassuring green."""
    assert _message_style("autonomy -> ON (orders placed WITHOUT asking)") == "alert"


def test_message_style_off_is_ok() -> None:
    assert _message_style("autonomy -> OFF (every order will ask first)") == "ok"


def test_message_style_failed_is_alert() -> None:
    assert _message_style("fetch failed: boom") == "alert"


def test_message_style_cancelled_is_warn() -> None:
    assert _message_style("autonomy unchanged (arming cancelled)") == "warn"


def test_message_style_a_live_profile_switch_is_alert_not_ok() -> None:
    """The one switch that must never read as reassuring green: `profile -> LIVE` points
    the whole console at REAL MONEY, so it carries the same weight the LIVE banner line and
    arming autonomy ON get -- green there would be the toast colour saying 'all well' about
    real-account data starting to answer from every screen."""
    assert (
        _message_style("profile -> LIVE (config.live-sandbox.yaml + keel-live.db)")
        == "alert"
    )


def test_message_style_a_paper_profile_switch_stays_ok() -> None:
    """Paper switches are the calm, ungated case -- they keep the reassuring green."""
    assert (
        _message_style(
            "profile -> paper-hourly (config.paper-hourly.yaml + keel-paperhourly.db)"
        )
        == "ok"
    )


def test_message_style_fetch_complete_is_ok() -> None:
    assert _message_style("fetch complete (2 products, 5y history)") == "ok"


# -- _confirm_arm_autonomy (fake curses module, fail-closed) ---------------------------------------


def test_confirm_arm_autonomy_true_on_typed_yes_and_restores_screen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_curses = _fake_curses()
    monkeypatch.setitem(sys.modules, "curses", fake_curses)
    monkeypatch.setattr(
        "keel.commands._common._require_interactive_confirmation",
        lambda action, detail: None,
    )
    stdscr = _FakeStdscr(height=24, width=80)
    config = _config()

    assert _confirm_arm_autonomy(stdscr, config) is True
    assert stdscr.refresh_calls == 1


def test_confirm_arm_autonomy_false_on_aborted_confirmation_and_restores_screen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import click

    fake_curses = _fake_curses()
    monkeypatch.setitem(sys.modules, "curses", fake_curses)

    def _raise_aborted(action: str, detail: str) -> None:
        raise click.ClickException("aborted")

    monkeypatch.setattr(
        "keel.commands._common._require_interactive_confirmation", _raise_aborted
    )
    stdscr = _FakeStdscr(height=24, width=80)
    config = _config()

    assert _confirm_arm_autonomy(stdscr, config) is False
    assert stdscr.refresh_calls == 1


def test_confirm_arm_autonomy_fails_closed_when_endwin_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_curses = _fake_curses()

    def _raise_endwin() -> None:
        raise fake_curses.error("endwin failed")

    fake_curses.endwin = _raise_endwin
    fake_curses.def_prog_mode = lambda: None
    fake_curses.reset_prog_mode = lambda: None
    monkeypatch.setitem(sys.modules, "curses", fake_curses)
    stdscr = _FakeStdscr(height=24, width=80)
    config = _config()

    assert _confirm_arm_autonomy(stdscr, config) is False


# -- CLI ----------------------------------------------------------------------------------------


def _repo_at(db_path) -> Repository:
    conn = connect(str(db_path))
    migrate(conn)
    return Repository(conn)


def test_tui_once_command_exits_zero_and_prints_frame(tmp_path, valid_config_path) -> None:
    db_path = tmp_path / "keel.db"
    _repo_at(db_path).set_state("kill_switch", False)

    result = CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "tui", "--once"]
    )

    assert result.exit_code == 0, result.output
    assert "paper mode" in result.output
    assert "quit" in result.output.lower()
    assert "help" in result.output.lower()


def test_tui_zero_interval_without_once_is_rejected(tmp_path, valid_config_path) -> None:
    db_path = tmp_path / "keel.db"
    _repo_at(db_path).set_state("kill_switch", False)

    result = CliRunner().invoke(
        cli,
        [
            "--db",
            str(db_path),
            "--config",
            str(valid_config_path),
            "tui",
            "--interval",
            "0",
            "--once",
        ],
    )

    assert result.exit_code != 0


def test_tui_negative_interval_is_rejected(tmp_path, valid_config_path) -> None:
    db_path = tmp_path / "keel.db"
    _repo_at(db_path).set_state("kill_switch", False)

    result = CliRunner().invoke(
        cli,
        [
            "--db",
            str(db_path),
            "--config",
            str(valid_config_path),
            "tui",
            "--interval",
            "-1",
            "--once",
        ],
    )

    assert result.exit_code != 0


# -- interactive-terminal guard (no curses under CliRunner / pipes) ----------------------------


def test_stdio_is_interactive_requires_both_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """True only when BOTH stdin and stdout are TTYs -- either being a pipe means the full-screen
    loop cannot run."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    assert _stdio_is_interactive() is True

    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    assert _stdio_is_interactive() is False


def test_tui_without_once_needs_interactive_terminal(tmp_path, valid_config_path) -> None:
    """`keel tui` (live) under CliRunner -- stdin/stdout are not TTYs -- must fail with a clean,
    actionable message pointing at `--once`, NOT enter curses and dump a traceback."""
    db_path = tmp_path / "keel.db"
    _repo_at(db_path).set_state("kill_switch", False)

    result = CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "tui"]
    )

    assert result.exit_code != 0
    assert "interactive terminal" in result.output.lower()
    assert "--once" in result.output


def test_run_live_wraps_curses_error_as_clickexception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Belt-and-braces: if `curses.wrapper` itself raises `curses.error` (e.g. `cbreak()
    returned ERR` on a TTY that passes `isatty()` but can't be put into cbreak mode), `run_live`
    turns it into a `click.ClickException` with a helpful message rather than a raw traceback."""
    import click

    fake_curses = _fake_curses()

    def _raise_wrapper(_fn: Any) -> None:
        raise fake_curses.error("cbreak() returned ERR")

    fake_curses.wrapper = _raise_wrapper
    monkeypatch.setitem(sys.modules, "curses", fake_curses)

    def _must_not_open() -> Any:
        raise AssertionError("open_state must not be called -- wrapper raised first")

    with pytest.raises(click.ClickException) as excinfo:
        run_live(_must_not_open, lambda: 0, 5.0)

    assert "--once" in str(excinfo.value)


def test_run_live_r_toasts_that_it_refreshed(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`r` re-reads local state and forces the balance re-fetch, but set no message -- so the one
    keypress that always "works" was indistinguishable from a dead key. Every other action key
    (`a`, `f`) toasts; this one must too.
    """
    config = _config()
    # poll1: normal -> 'r'. poll2: the repaint that must carry the toast. poll3: 'q' (default).
    stdscr = _KeySequenceStdscr(height=24, width=80, keys=[ord("r"), -1])

    fake_curses = _fake_curses()
    fake_curses.wrapper = lambda fn: fn(stdscr)
    monkeypatch.setitem(sys.modules, "curses", fake_curses)

    def open_state() -> tuple[Repository, Any]:
        return repo, config

    run_live(open_state, lambda: NOW_TS, interval=0.01)

    painted_texts = [call[2] for call in stdscr.calls]
    assert any("refreshed" in t for t in painted_texts)


def test_the_refresh_toast_reads_as_ok_not_as_a_failure() -> None:
    """It is a routine, successful action -- it must not paint in the alert/warn colours reserved
    for a failure, a cancelled action, or arming autonomy."""
    assert _message_style(_REFRESH_MESSAGE) == "ok"


# -- activity overlay (v): the pure builder ------------------------------------------------------


def _activity_line(event: str, ts: float, cycle_id: str | None = "cyc-1", **fields: Any) -> str:
    """One JSONL record in `keel_core.telemetry.JsonFormatter`'s shape. A local copy of
    `tests/commands/test_activity.py`'s helper on purpose: the exhaustive parsing/grouping
    coverage lives over there, and these tests only need enough of a log to prove the OVERLAY
    renders and scrolls it."""
    payload: dict[str, Any] = {
        "ts": ts,
        "level": fields.pop("level", "INFO"),
        "logger": "keel.agent",
        "event": event,
        "venue": "coinbase",
    }
    if cycle_id is not None:
        payload["cycle_id"] = cycle_id
    payload.update(fields)
    return json.dumps(payload)


#: A two-cycle log: an ordinary quiet cycle, then the real 2026-08-08 PAXG shape -- a setup, a
#: guard violation, and an entry that was not placed.
_ACTIVITY_TS = 1_786_194_006.0

#: The same fixture re-anchored onto NOW_TS's OWN local calendar day, for the `run_live` tests --
#: which now build a TODAY-scoped feed, so a fixture stamped 2026-08-08 would (correctly) render
#: the "keel has not run yet today" empty state instead of the rows those tests are about. Both
#: cycles land inside the one local day, two hours apart, so they stay two rows in newest-first
#: order. Derived from `scope_start_ts` and the fixed `NOW_TS` rather than from a live clock, so
#: it is exactly as deterministic as the constant it is built from.
_ACTIVITY_TODAY_TS = (scope_start_ts("today", float(NOW_TS)) or 0.0) + 9 * 3600


def _activity_log_lines(base: float = _ACTIVITY_TS, gap: float = 86400.0) -> list[str]:
    """A quiet cycle at `base`, then a rail-vetoed one `gap` seconds later. `gap` is a parameter
    so the same shapes can be laid out across two days (the default -- what the real deployment
    does) or inside one (`_today_activity_log_lines`)."""
    later = base + gap
    return [
        _activity_line("agent.cycle_start", base, "quiet-1"),
        _activity_line("agent.mode_resolved", base + 1, "quiet-1", mode="paper"),
        _activity_line(
            "agent.signals_evaluated",
            base + 2,
            "quiet-1",
            product="BTC-USD",
            rule_count=1,
            signal_count=0,
        ),
        _activity_line("agent.cycle_start", later, "veto-1"),
        _activity_line("agent.mode_resolved", later + 1, "veto-1", mode="paper"),
        _activity_line(
            "engine.setup_detected",
            later + 2,
            "veto-1",
            rule="turtle_breakout",
            product="PAXG-USD",
            cts_score=5,
            technique="signal_candle",
            entry="4342.52",
            stop="4197.09381782563408",
            target="5215.07709304619552",
        ),
        _activity_line(
            "agent.signals_evaluated",
            later + 2,
            "veto-1",
            product="PAXG-USD",
            rule_count=1,
            signal_count=1,
        ),
        _activity_line(
            "guards.check_failed",
            later + 2,
            "veto-1",
            product="PAXG-USD",
            side="BUY",
            violation=(
                "per_asset_concentration_cap: PAXG exposure 3284.671252850915628790264696 "
                "exceeds 0.5 of max_exposure_usd (2500.0)"
            ),
        ),
        _activity_line(
            "agent.enter_evaluated",
            later + 2,
            "veto-1",
            product="PAXG-USD",
            rule="turtle_breakout",
            technique="signal_candle",
            cts_score=5,
            placed=False,
            reason="paper: vetoed by rails",
        ),
    ]


def _today_activity_log_lines() -> list[str]:
    """Both cycles inside NOW_TS's local calendar day -- what the TODAY-scoped overlay shows."""
    return _activity_log_lines(base=_ACTIVITY_TODAY_TS, gap=7200.0)


def _write_activity_log(tmp_path: Any, lines: list[str] | None = None) -> str:
    path = tmp_path / "logs" / "keel.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(_today_activity_log_lines() if lines is None else lines) + "\n")
    return str(path)


def _activity_feed() -> Any:
    return feed_from_lines(_activity_log_lines(), source="/tmp/keel.log")


def test_build_activity_overlay_is_titled_and_lists_one_row_per_cycle_newest_first() -> None:
    lines = build_activity_overlay(_activity_feed())
    texts = [line.text for line in lines]

    assert texts[0] == "keel tui -- activity"
    veto_idx = next(i for i, t in enumerate(texts) if "rail veto" in t)
    quiet_idx = next(i for i, t in enumerate(texts) if "quiet -- looked" in t)
    assert veto_idx < quiet_idx  # newest first


def test_build_activity_overlay_collapsed_does_not_list_individual_events() -> None:
    texts = [line.text for line in build_activity_overlay(_activity_feed())]

    assert not any("engine.setup_detected" in t for t in texts)


def test_build_activity_overlay_expanded_lists_the_cycles_events_in_time_order() -> None:
    feed = _activity_feed()
    texts = [
        line.text
        for line in build_activity_overlay(feed, cursor=0, expanded=frozenset({"veto-1"}))
    ]

    setup_idx = next(i for i, t in enumerate(texts) if "engine.setup_detected" in t)
    guard_idx = next(i for i, t in enumerate(texts) if "guards.check_failed" in t)
    enter_idx = next(i for i, t in enumerate(texts) if "agent.enter_evaluated" in t)
    assert setup_idx < guard_idx < enter_idx
    # ...and the fields that carry the meaning, not just the event names.
    assert any("entry=4342.52" in t for t in texts)
    assert any("per_asset_concentration_cap" in t for t in texts)
    assert any("paper: vetoed by rails" in t for t in texts)


def test_activity_overlay_styles_a_veto_cycle_differently_from_a_quiet_one() -> None:
    """The at-a-glance requirement: a rail veto must not read the same as a quiet cycle."""
    lines = build_activity_overlay(_activity_feed())
    veto = next(line for line in lines if "rail veto" in line.text)
    quiet = next(line for line in lines if "quiet -- looked" in line.text)

    assert veto.style != quiet.style
    assert quiet.style == "muted"


def test_activity_overlay_tells_the_operator_how_to_use_it() -> None:
    texts = [line.text for line in build_activity_overlay(_activity_feed())]

    footer = texts[-1]
    assert "expand" in footer
    assert "close" in footer


@pytest.mark.parametrize(
    ("status", "expected_fragment"),
    [
        ("missing", "No engine log found"),
        ("empty", "is empty"),
        ("unparseable", "could be parsed"),
        ("oversized", "No complete record"),
        ("unreadable", "could not be read"),
    ],
)
def test_activity_overlay_explains_a_broken_log_instead_of_rendering_blank(
    status: str, expected_fragment: str
) -> None:
    """A blank overlay would be indistinguishable from the dead-looking dashboard this whole
    feature exists to disprove -- so every failure mode renders words."""
    feed = ActivityFeed(status=status, source="/tmp/keel.log", detail="because reasons")

    lines = build_activity_overlay(feed)
    texts = [line.text for line in lines]

    assert texts[0] == "keel tui -- activity"
    assert any(expected_fragment in t for t in texts)
    assert any("Press v or Esc" in t for t in texts)


def test_activity_overlay_on_a_readable_log_with_no_cycles_says_so() -> None:
    feed = ActivityFeed(status="ok", source="/tmp/keel.log", cycles=())

    texts = [line.text for line in build_activity_overlay(feed)]

    assert any("No cycles in the window" in t for t in texts)


# -- activity overlay: the cursor ----------------------------------------------------------------


def test_activity_cursor_line_points_at_the_selected_row() -> None:
    feed = _activity_feed()

    for cursor in range(len(feed.cycles)):
        lines, cursor_line = _activity_lines(feed, cursor=cursor)
        assert lines[cursor_line].text.startswith(">")


def test_activity_cursor_line_survives_a_row_above_it_being_expanded() -> None:
    """The arithmetic that would drift if the cursor's screen position were computed by a second
    copy of the layout: expanding the FIRST row pushes the second one down by its event count."""
    feed = _activity_feed()

    lines, cursor_line = _activity_lines(feed, cursor=1, expanded=frozenset({"veto-1"}))

    assert lines[cursor_line].text.startswith(">")
    assert "quiet -- looked" in lines[cursor_line].text


def test_activity_cursor_line_on_an_empty_feed_is_in_range() -> None:
    lines, cursor_line = _activity_lines(ActivityFeed(status="ok", source="x", cycles=()))

    assert 0 <= cursor_line < len(lines)


@pytest.mark.parametrize(
    ("key", "start", "expected"),
    [
        ("KEY_DOWN", 0, 1),
        ("KEY_UP", 3, 2),
        ("KEY_HOME", 4, 0),
        ("KEY_END", 0, 4),
        ("KEY_NPAGE", 0, 4),  # a page is larger than this feed -- clamped to the last row
        ("KEY_PPAGE", 4, 0),
    ],
)
def test_activity_cursor_moves_by_rows_and_clamps(key: str, start: int, expected: int) -> None:
    fake_curses = _fake_curses()

    assert _activity_cursor(getattr(fake_curses, key), start, 24, 5, fake_curses) == expected


def test_activity_cursor_accepts_the_same_vi_keys_the_other_overlays_scroll_with() -> None:
    fake_curses = _fake_curses()

    assert _activity_cursor(ord("j"), 0, 24, 5, fake_curses) == 1
    assert _activity_cursor(ord("k"), 2, 24, 5, fake_curses) == 1


def test_activity_cursor_never_leaves_the_feed() -> None:
    fake_curses = _fake_curses()

    assert _activity_cursor(fake_curses.KEY_UP, 0, 24, 5, fake_curses) == 0
    assert _activity_cursor(fake_curses.KEY_DOWN, 4, 24, 5, fake_curses) == 4
    # An empty feed has no row to select at all.
    assert _activity_cursor(fake_curses.KEY_DOWN, 0, 24, 0, fake_curses) == 0


def test_activity_cursor_ignores_an_unrelated_key() -> None:
    fake_curses = _fake_curses()

    assert _activity_cursor(ord("z"), 2, 24, 5, fake_curses) == 2
    assert _activity_cursor(-1, 2, 24, 5, fake_curses) == 2  # the no-key poll timeout


def test_activity_cursor_pages_by_the_rows_the_banner_leaves_free() -> None:
    """A page leaves the title, blank and header rows in view -- and, when a console
    binding is present, the two BANNER lines the feed is prepended to as well: paging used
    to over-advance by exactly the banner, landing the selection further down than a screen
    of rows the operator actually saw."""
    fake_curses = _fake_curses()

    # Without a banner: a page on a 24-row terminal is 21 rows.
    assert _activity_cursor(fake_curses.KEY_NPAGE, 10, 24, 50, fake_curses) == 31
    assert _activity_cursor(fake_curses.KEY_PPAGE, 30, 24, 50, fake_curses) == 30 - 21
    # With the 2-line banner prepended: two fewer rows per page.
    assert (
        _activity_cursor(fake_curses.KEY_NPAGE, 10, 24, 50, fake_curses, banner_lines=2)
        == 29
    )
    assert (
        _activity_cursor(fake_curses.KEY_PPAGE, 30, 24, 50, fake_curses, banner_lines=2)
        == 30 - 19
    )
    # The floor of 1 survives the banner: a tiny terminal still advances.
    assert _activity_cursor(fake_curses.KEY_NPAGE, 0, 4, 50, fake_curses, banner_lines=2) == 1


@pytest.mark.parametrize(
    ("offset", "cursor_line", "height", "expected"),
    [
        (0, 5, 24, 0),  # already visible -- do not move the view at all
        (10, 5, 24, 5),  # above the window -- scroll up to it
        (0, 30, 24, 7),  # below the window -- scroll down the minimum
        (0, 5, 0, 0),  # a terminal mid-resize must not divide by anything
        (3, 3, 1, 3),
    ],
)
def test_follow_cursor_makes_the_smallest_change_that_reveals_the_cursor(
    offset: int, cursor_line: int, height: int, expected: int
) -> None:
    assert _follow_cursor(offset, cursor_line, height) == expected


# -- activity overlay: the live loop -------------------------------------------------------------


def test_footer_lines_advertise_the_activity_key() -> None:
    texts = [line.text for line in _footer_lines()]

    assert any("[v] activity" in t for t in texts)


def test_help_screen_documents_the_activity_overlay_and_its_keys() -> None:
    texts = [line.text for line in build_help_screen()]
    joined = " ".join(texts)

    assert any(t.strip().startswith("v ") for t in texts)
    assert "Activity overlay (v)" in joined
    assert "q / Esc / v" in joined
    assert "Enter / Space" in joined
    # The two claims the design rests on: it is offline, and the read is bounded.
    assert "BOUNDED" in joined
    assert "logging.file" in joined


def test_run_live_v_opens_activity_overlay_and_esc_closes_it(
    repo: Repository, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Mirrors the 's'/'p' tests: 'v' opens, Esc closes back to the dashboard."""
    config = _config(logging=LoggingConfig(file=_write_activity_log(tmp_path)))
    stdscr = _KeySequenceStdscr(height=24, width=200, keys=[ord("v"), -1, 27])

    fake_curses = _fake_curses()
    fake_curses.wrapper = lambda fn: fn(stdscr)
    monkeypatch.setitem(sys.modules, "curses", fake_curses)

    def open_state() -> tuple[Repository, Any]:
        return repo, config

    run_live(open_state, lambda: NOW_TS, interval=0.01)

    painted_texts = [call[2] for call in stdscr.calls]
    activity_idx = next(i for i, t in enumerate(painted_texts) if "keel tui -- activity" in t)
    dashboard_after_idx = next(
        i for i, t in enumerate(painted_texts) if i > activity_idx and "paper mode" in t
    )
    assert dashboard_after_idx > activity_idx


def test_run_live_activity_overlay_paints_the_real_cycles_from_the_configured_log(
    repo: Repository, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """The end-to-end claim: the path comes from `logging.file`, the rows come from that file,
    and a rail-vetoed cycle is legible without expanding anything."""
    config = _config(logging=LoggingConfig(file=_write_activity_log(tmp_path)))
    stdscr = _KeySequenceStdscr(height=40, width=240, keys=[ord("v"), -1])

    fake_curses = _fake_curses()
    fake_curses.wrapper = lambda fn: fn(stdscr)
    monkeypatch.setitem(sys.modules, "curses", fake_curses)

    def open_state() -> tuple[Repository, Any]:
        return repo, config

    run_live(open_state, lambda: NOW_TS, interval=0.01)

    painted_texts = [call[2] for call in stdscr.calls]
    assert any("rail veto: per_asset_concentration_cap" in t for t in painted_texts)
    assert any("quiet -- looked, nothing to do" in t for t in painted_texts)


def test_run_live_activity_enter_expands_the_selected_cycle(
    repo: Repository, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    config = _config(logging=LoggingConfig(file=_write_activity_log(tmp_path)))
    # poll1: normal -> 'v'. poll2: activity, Enter expands row 0 (the newest, vetoed cycle).
    # poll3: activity, repainted expanded. poll4: 'q' (the stdscr's post-exhaustion default).
    stdscr = _KeySequenceStdscr(height=40, width=240, keys=[ord("v"), 10, -1])

    fake_curses = _fake_curses()
    fake_curses.wrapper = lambda fn: fn(stdscr)
    monkeypatch.setitem(sys.modules, "curses", fake_curses)

    def open_state() -> tuple[Repository, Any]:
        return repo, config

    run_live(open_state, lambda: NOW_TS, interval=0.01)

    painted_texts = [call[2] for call in stdscr.calls]
    assert any("guards.check_failed" in t for t in painted_texts)
    assert any("paper: vetoed by rails" in t for t in painted_texts)


def test_run_live_activity_overlay_reports_a_missing_log_rather_than_crashing(
    repo: Repository, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    config = _config(logging=LoggingConfig(file=str(tmp_path / "nowhere" / "keel.log")))
    stdscr = _KeySequenceStdscr(height=24, width=200, keys=[ord("v"), -1])

    fake_curses = _fake_curses()
    fake_curses.wrapper = lambda fn: fn(stdscr)
    monkeypatch.setitem(sys.modules, "curses", fake_curses)

    def open_state() -> tuple[Repository, Any]:
        return repo, config

    run_live(open_state, lambda: NOW_TS, interval=0.01)

    painted_texts = [call[2] for call in stdscr.calls]
    assert any("No engine log found" in t for t in painted_texts)


def test_run_live_activity_survives_a_transient_read_error_and_keeps_polling(
    repo: Repository, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """`open_state()` itself failing (a locked DB from a concurrent `keel agent` writer) must
    become a readable overlay, not a crash -- the same contract the insights/screen/propose
    branches keep."""
    config = _config(logging=LoggingConfig(file=_write_activity_log(tmp_path)))
    stdscr = _KeySequenceStdscr(height=24, width=200, keys=[ord("v"), -1, -1])

    fake_curses = _fake_curses()
    fake_curses.wrapper = lambda fn: fn(stdscr)
    monkeypatch.setitem(sys.modules, "curses", fake_curses)

    opens: list[int] = []

    def open_state() -> tuple[Repository, Any]:
        opens.append(1)
        # Fail on the FIRST call made from inside the activity branch (calls 1 and 2 are the
        # normal-mode status read and its balance refresh).
        if len(opens) == 3:
            raise sqlite3.OperationalError("database is locked")
        return repo, config

    run_live(open_state, lambda: NOW_TS, interval=0.01)

    painted_texts = [call[2] for call in stdscr.calls]
    assert any("could not be read" in t for t in painted_texts)
    # ...and it kept polling: a later frame rendered the real feed.
    assert any("rail veto" in t for t in painted_texts)


def test_run_live_activity_overlay_never_builds_a_broker(
    repo: Repository, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """The overlay's whole admissibility argument is that reading a local file is neither a
    broker nor the network. Opening it must add ZERO broker constructions on top of the ones the
    normal-mode dashboard already makes (one slow-cadence balance refresh, here)."""
    config = _config(logging=LoggingConfig(file=_write_activity_log(tmp_path)))
    stdscr = _KeySequenceStdscr(height=24, width=200, keys=[ord("v"), -1, -1, 27])

    fake_curses = _fake_curses()
    fake_curses.wrapper = lambda fn: fn(stdscr)
    monkeypatch.setitem(sys.modules, "curses", fake_curses)

    built: list[Any] = []

    def _recording_build_broker(cfg: Any, **kwargs: Any) -> Any:
        built.append(cfg)
        raise RuntimeError("no venue in tests")

    monkeypatch.setattr("keel.commands._common._build_broker", _recording_build_broker)

    def open_state() -> tuple[Repository, Any]:
        return repo, config

    run_live(open_state, lambda: NOW_TS, interval=0.01)

    painted_texts = [call[2] for call in stdscr.calls]
    assert any("keel tui -- activity" in t for t in painted_texts)
    # `now_fn` is constant, so the ~30s balance cadence fires exactly once, on the first poll.
    # Any second construction could only have come from the activity branch.
    assert len(built) == 1


def test_run_live_activity_survives_a_log_record_whose_timestamp_cannot_be_rendered(
    repo: Repository, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """The renderer, not just the feed builder, runs on the repaint path. A `ts` no `strftime`
    can format (a bad clock, a corrupted byte) must not escape `curses.wrapper` and kill the
    dashboard -- which is why `run_live` wraps the RENDER, not only the build."""
    path = tmp_path / "keel.log"
    path.write_text(
        "\n".join(
            [
                *_today_activity_log_lines(),
                json.dumps({"ts": 1e20, "event": "agent.cycle_start", "cycle_id": "from-mars"}),
            ]
        )
        + "\n"
    )
    config = _config(logging=LoggingConfig(file=str(path)))
    stdscr = _KeySequenceStdscr(height=40, width=240, keys=[ord("v"), -1])

    fake_curses = _fake_curses()
    fake_curses.wrapper = lambda fn: fn(stdscr)
    monkeypatch.setitem(sys.modules, "curses", fake_curses)

    def open_state() -> tuple[Repository, Any]:
        return repo, config

    run_live(open_state, lambda: NOW_TS, interval=0.01)  # must not raise

    painted_texts = [call[2] for call in stdscr.calls]
    assert any("keel tui -- activity" in t for t in painted_texts)
    assert any("rail veto" in t for t in painted_texts)


# -- activity overlay: day scoping ---------------------------------------------------------------
#
# The pure boundary/empty-state logic is exercised exhaustively in
# `tests/commands/test_activity.py`. These tests are about the OVERLAY and the live loop: that the
# scope is visible on screen, that `t` widens it, and that it is reset to `today` on every open.


def _yesterday_activity_log_lines() -> list[str]:
    """One quiet cycle on the local day BEFORE NOW_TS's -- the history a `today` scope hides.

    The previous day's midnight is taken from `scope_start_ts` rather than by subtracting 86400
    from today's, so this lands on the intended civil day even across a DST transition."""
    ts = (scope_start_ts("today", float(NOW_TS) - 86400) or 0.0) + 9 * 3600
    return [
        _activity_line("agent.cycle_start", ts, "yesterday-1"),
        _activity_line("agent.mode_resolved", ts + 1, "yesterday-1", mode="paper"),
        # A gate rejection, so this cycle's COLLAPSED row names XLM-USD -- which is what makes
        # "is yesterday on screen?" answerable from the painted text alone. A quiet cycle would
        # render only "1 products" and be indistinguishable from today's.
        _activity_line(
            "engine.setup_rejected",
            ts + 2,
            "yesterday-1",
            rule="turtle_breakout",
            product="XLM-USD",
            gate="choppy_regime",
        ),
    ]


def _scoped_activity_feed(scope: str = "today") -> Any:
    """The two-cycles-today fixture plus one from yesterday, scoped as the overlay would."""
    return apply_scope(
        feed_from_lines(
            [*_yesterday_activity_log_lines(), *_today_activity_log_lines()],
            source="/tmp/keel.log",
        ),
        scope,
        now_ts=float(NOW_TS),
    )


def test_activity_overlay_header_states_the_scope_under_the_title() -> None:
    """A one-row "today" view and a one-row log look identical without this line -- and the key
    that would settle it would be invisible."""
    texts = [line.text for line in build_activity_overlay(_scoped_activity_feed())]

    assert texts[0] == "keel tui -- activity"
    assert texts[1].startswith("scope: today ")
    assert "1 older hidden" in texts[1]
    assert "press t to widen" in texts[1]


def test_activity_overlay_scoped_to_today_hides_yesterdays_row() -> None:
    texts = [line.text for line in build_activity_overlay(_scoped_activity_feed("today"))]
    joined = " ".join(texts)

    assert "rail veto: per_asset_concentration_cap" in joined  # today's vetoed cycle
    assert "XLM-USD" not in joined  # yesterday's, which only names XLM


def test_activity_overlay_widened_to_all_shows_the_earlier_day_again() -> None:
    texts = [line.text for line in build_activity_overlay(_scoped_activity_feed("all"))]

    rows = [t for t in texts if t.startswith((" ▸", ">▸"))]
    assert len(rows) == 3
    assert "all history in the window" in texts[1]


def test_activity_overlay_with_nothing_today_is_never_blank_and_names_the_last_run() -> None:
    """The morning case, on screen: the panel must answer "is keel alive" without a single row."""
    feed = apply_scope(
        feed_from_lines(_yesterday_activity_log_lines(), source="/tmp/keel.log"),
        "today",
        now_ts=float(NOW_TS),
    )

    lines = build_activity_overlay(feed)
    texts = [line.text for line in lines]
    joined = " ".join(texts)

    assert len(texts) > 3
    assert "keel has not run yet today." in texts
    assert "Last cycle:" in joined
    assert "yesterday" in joined
    assert "Press t to widen the scope" in joined
    # The column header is not painted over an empty day -- there are no columns to head.
    assert ACTIVITY_HEADER not in texts


def test_activity_cursor_line_on_an_empty_scope_stays_in_range() -> None:
    feed = apply_scope(
        feed_from_lines(_yesterday_activity_log_lines(), source="/tmp/keel.log"),
        "today",
        now_ts=float(NOW_TS),
    )

    lines, cursor_line = _activity_lines(feed)

    assert 0 <= cursor_line < len(lines)


def test_activity_overlay_footer_advertises_the_scope_key() -> None:
    footer = [line.text for line in build_activity_overlay(_scoped_activity_feed())][-1]

    assert "t scope" in footer
    assert "expand" in footer
    assert "close" in footer


def test_help_screen_documents_the_scope_and_the_t_key() -> None:
    texts = [line.text for line in build_help_screen()]
    joined = " ".join(texts)

    assert any(t.strip().startswith("t ") for t in texts)
    assert "SCOPED TO TODAY" in joined
    assert "today -> last 7 days -> all history in the window" in joined
    assert "has not run yet" in joined
    assert "reopened" in joined


def test_run_live_activity_opens_scoped_to_today(
    repo: Repository, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """The requirement, through the live loop: yesterday's cycle is in the log and is not shown."""
    config = _config(
        logging=LoggingConfig(
            file=_write_activity_log(
                tmp_path, [*_yesterday_activity_log_lines(), *_today_activity_log_lines()]
            )
        )
    )
    stdscr = _KeySequenceStdscr(height=40, width=240, keys=[ord("v"), -1])

    fake_curses = _fake_curses()
    fake_curses.wrapper = lambda fn: fn(stdscr)
    monkeypatch.setitem(sys.modules, "curses", fake_curses)

    def open_state() -> tuple[Repository, Any]:
        return repo, config

    run_live(open_state, lambda: NOW_TS, interval=0.01)

    painted = [call[2] for call in stdscr.calls]
    assert any("scope: today " in t for t in painted)
    assert any("rail veto: per_asset_concentration_cap" in t for t in painted)
    assert not any("XLM-USD" in t for t in painted)  # yesterday's, hidden
    assert any("1 older hidden" in t for t in painted)


def test_run_live_activity_t_widens_the_scope_to_reveal_the_earlier_day(
    repo: Repository, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """`t` cycles today -> 7d -> all. Two presses reach `all`, and yesterday's cycle appears."""
    config = _config(
        logging=LoggingConfig(
            file=_write_activity_log(
                tmp_path, [*_yesterday_activity_log_lines(), *_today_activity_log_lines()]
            )
        )
    )
    stdscr = _KeySequenceStdscr(
        height=40, width=240, keys=[ord("v"), ord("t"), ord("t"), -1]
    )

    fake_curses = _fake_curses()
    fake_curses.wrapper = lambda fn: fn(stdscr)
    monkeypatch.setitem(sys.modules, "curses", fake_curses)

    def open_state() -> tuple[Repository, Any]:
        return repo, config

    run_live(open_state, lambda: NOW_TS, interval=0.01)

    painted = [call[2] for call in stdscr.calls]
    assert any("scope: last 7 days from " in t for t in painted)
    assert any("all history in the window" in t for t in painted)
    assert any("XLM-USD" in t for t in painted)  # yesterday's cycle, now in scope


def test_run_live_activity_reopens_at_today_after_being_widened(
    repo: Repository, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """A widened scope answers one question once; it must never become the next open's default."""
    config = _config(
        logging=LoggingConfig(
            file=_write_activity_log(
                tmp_path, [*_yesterday_activity_log_lines(), *_today_activity_log_lines()]
            )
        )
    )
    # open, widen to 7d, widen to all, close, reopen -- then repaint.
    stdscr = _KeySequenceStdscr(
        height=40, width=240, keys=[ord("v"), ord("t"), ord("t"), 27, ord("v"), -1]
    )

    fake_curses = _fake_curses()
    fake_curses.wrapper = lambda fn: fn(stdscr)
    monkeypatch.setitem(sys.modules, "curses", fake_curses)

    def open_state() -> tuple[Repository, Any]:
        return repo, config

    run_live(open_state, lambda: NOW_TS, interval=0.01)

    painted = [call[2] for call in stdscr.calls]
    all_scope_frame = max(i for i, t in enumerate(painted) if "all history in the window" in t)
    reopened_frame = max(i for i, t in enumerate(painted) if "scope: today " in t)

    # The LAST activity frame painted is a `today` one, i.e. the reopen reset it.
    assert reopened_frame > all_scope_frame


def test_run_live_activity_paints_the_empty_state_when_today_holds_no_cycle(
    repo: Repository, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """The whole point of the empty state, through the live loop: a log with only older cycles
    renders words, not a blank panel -- and those words name when keel last ran."""
    config = _config(
        logging=LoggingConfig(file=_write_activity_log(tmp_path, _yesterday_activity_log_lines()))
    )
    stdscr = _KeySequenceStdscr(height=24, width=200, keys=[ord("v"), -1])

    fake_curses = _fake_curses()
    fake_curses.wrapper = lambda fn: fn(stdscr)
    monkeypatch.setitem(sys.modules, "curses", fake_curses)

    def open_state() -> tuple[Repository, Any]:
        return repo, config

    run_live(open_state, lambda: NOW_TS, interval=0.01)

    painted = [call[2] for call in stdscr.calls]
    assert any("keel has not run yet today." in t for t in painted)
    assert any("Last cycle:" in t for t in painted)
    assert any("Press t to widen the scope" in t for t in painted)


# -- run_live: the console shell (issue #388 C2) ---------------------------------------------------


_MINIMAL_CONSOLE_CONFIG = (
    "allowlist: [BTC]\ncaps: {max_exposure_usd: 100, max_per_asset_pct: 0.5}\n"
)
_MINIMAL_CONSOLE_CONFIG_ALT = (
    "allowlist: [ETH]\ncaps: {max_exposure_usd: 100, max_per_asset_pct: 0.5}\n"
)


def _deployment_dir(tmp_path: Any) -> Any:
    """A working directory holding every known deployment's config, the shape `discover_
    profiles` reads -- paper-forward is the pair the console starts bound to."""
    from keel.commands import console

    for profile in console.KNOWN_PROFILES:
        (tmp_path / profile.config_path).write_text(
            _MINIMAL_CONSOLE_CONFIG_ALT if profile.key == "paper-hourly" else (
                _MINIMAL_CONSOLE_CONFIG_ALT if profile.key == "live" else _MINIMAL_CONSOLE_CONFIG
            )
        )
    return tmp_path


def _console_session(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    keys: list[int],
    *,
    height: int = 30,
    start_config: str = "config.paperforward.yaml",
    start_db: str = "keel.db",
    build_broker: Any = None,
) -> tuple[_FakeStdscr, Any]:
    """Run one scripted `run_live` session with a REAL console binding over the temp
    deployment dir, and return (the stdscr with its recorded `addstr` calls, the binding).
    The balance refresh's broker construction is stubbed (it fires on the first poll by
    design) so no test touches the network; a test that needs to observe or count venue
    calls (the Compliance menu's ARMED views) passes its own `build_broker`."""
    from keel.commands import console

    monkeypatch.chdir(tmp_path)
    ctx = click.Context(click.Command("tui"), obj={})
    ctx.obj["config_path"] = start_config
    ctx.obj["db_path"] = start_db
    binding = console.ConsoleBinding(ctx, config_path=start_config, db_path=start_db)

    stdscr = _KeySequenceStdscr(height=height, width=120, keys=keys)
    fake_curses = _fake_curses()
    fake_curses.wrapper = lambda fn: fn(stdscr)
    monkeypatch.setitem(sys.modules, "curses", fake_curses)

    class _FakeBroker:
        def get_accounts(self) -> list[Any]:
            return []

    monkeypatch.setattr(
        "keel.commands._common._build_broker",
        build_broker
        if build_broker is not None
        else (lambda cfg, timeout=None: _FakeBroker()),
    )

    run_live(binding.open_state, lambda: NOW_TS, interval=0.01, console_binding=binding)
    return stdscr, binding


def _console_run(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    keys: list[int],
    *,
    start_config: str = "config.paperforward.yaml",
    start_db: str = "keel.db",
) -> tuple[list[str], Any]:
    """`_console_session` for every test that only needs the painted TEXTS (the common
    case) -- kept so those callers read at a glance."""
    stdscr, binding = _console_session(
        tmp_path, monkeypatch, keys, start_config=start_config, start_db=start_db
    )
    return [call[2] for call in stdscr.calls], binding


def test_run_live_with_a_console_binding_paints_the_banner_on_the_dashboard(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O9: the console header IS the banner -- the landing dashboard carries the active
    profile's config+db pair and the venue session line on EVERY frame, with no key pressed."""
    painted, _binding = _console_run(_deployment_dir(tmp_path), monkeypatch, [-1])

    assert any(t.startswith("console: paper-forward") for t in painted)
    assert any("config.paperforward.yaml" in t and "keel.db" in t for t in painted)
    # coinbase (the config default) is 24/7 -- the explicit always-open rendering.
    assert any("24/7" in t for t in painted)


def test_run_live_without_a_console_binding_is_the_pre_c2_dashboard(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every pre-C2 caller (and every existing test) passes no binding: the dashboard must
    render byte-identically to before -- no banner, no menu key -- so the shell is an
    addition, never a rewrite."""
    config = _config()
    stdscr = _ScriptedStdscr(height=24, width=80, quit_after=2)
    fake_curses = _fake_curses()
    fake_curses.wrapper = lambda fn: fn(stdscr)
    monkeypatch.setitem(sys.modules, "curses", fake_curses)

    class _FakeBroker:
        def get_accounts(self) -> list[Any]:
            return []

    monkeypatch.setattr(
        "keel.commands._common._build_broker", lambda cfg, timeout=None: _FakeBroker()
    )

    run_live(lambda: (repo, config), lambda: NOW_TS, interval=0.01)

    painted = [call[2] for call in stdscr.calls]
    assert painted and not any(t.startswith("console:") for t in painted)


def test_run_live_m_opens_the_menu_and_esc_returns_to_the_dashboard(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shell's navigation contract: `m` opens the console menu, every PRD §3 entry is
    on it, and Esc closes back to the landing dashboard (which stays the landing screen)."""
    # poll1: normal -> 'm'. poll2: menu, no key. poll3: Esc closes. poll4: 'q' quits.
    painted, _binding = _console_run(
        _deployment_dir(tmp_path), monkeypatch, [ord("m"), -1, 27]
    )

    menu_idx = next(i for i, t in enumerate(painted) if "keel console" in t and "menu" in t)
    menu_text = "\n".join(painted[menu_idx:])
    for label in (
        "Dashboard",
        "Profile",
        "Trading",
        "Rules",
        "Compliance",
        "Data",
        "Research",
        "Account",
        "Help",
    ):
        assert label in menu_text, label
    # Esc returned to the dashboard: a LATER frame paints the dashboard's own title again.
    assert any("paper mode" in t for t in painted[menu_idx:])


def test_run_live_a_placeholder_entry_lands_in_its_slice_notice(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Selecting a future slice's entry (8 = Account, C6 -- Trading and Data went live
    with C5, issue #391) renders the notice, not a dead click and not a feature -- the
    shell is navigation only."""
    painted, _binding = _console_run(
        _deployment_dir(tmp_path), monkeypatch, [ord("m"), ord("8"), -1, 27, 27]
    )

    assert any("lands in C6" in t for t in painted)
    assert any("navigation" in t.lower() for t in painted)


def test_run_live_profile_switch_rebinds_the_console_in_one_action(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O4, pinned end to end: one Enter on the paper-hourly row rebinds config+db together,
    and the very next dashboard frame's banner names the new pair -- the same loaders the
    CLI uses, no relaunch."""
    # 'm' menu -> '2' profile -> 'j','j' to paper-hourly (row 3 of 4) -> Enter switches ->
    # (mode returns to normal) 'q' quits.
    keys = [ord("m"), ord("2"), ord("j"), ord("j"), 10]
    painted, binding = _console_run(_deployment_dir(tmp_path), monkeypatch, keys)

    assert binding.config_path == "config.paper-hourly.yaml"
    assert binding.db_path == "keel-paperhourly.db"
    assert any("console: paper-hourly" in t for t in painted)
    assert any("config.paper-hourly.yaml" in t and "keel-paperhourly.db" in t for t in painted)
    # The switch is toasted like every other action.
    assert any("profile" in t.lower() and "paper-hourly" in t for t in painted)


def test_run_live_live_switch_declined_keeps_the_binding(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The live guard, through the loop: selecting LIVE asks (and here, the operator says
    no) -- the binding keeps the paper pair, the banner still says paper-forward, and the
    decline is toasted rather than silent."""
    from keel.commands import console as console_mod

    confirmations: list[str] = []

    def _decline(stdscr: Any, profile: Any) -> bool:
        confirmations.append(profile.key)
        return False

    monkeypatch.setattr(tui_mod, "_confirm_live_profile", _decline)
    # 'm' menu -> '2' profile -> 'j' to live (row 2 of 4) -> Enter asks, declined -> Esc
    # (to menu) -> Esc (to normal) -> 'q'.
    keys = [ord("m"), ord("2"), ord("j"), 10, 27, 27]
    painted, binding = _console_run(_deployment_dir(tmp_path), monkeypatch, keys)

    assert confirmations == ["live"]
    assert binding.config_path == "config.paperforward.yaml"
    assert binding.db_path == "keel.db"
    assert any("unchanged" in t.lower() for t in painted)
    assert any("console: paper-forward" in t for t in painted)
    # The live row's guard is stated where the operator selects it.
    assert any("LIVE" in t for t in painted)
    assert console_mod.KNOWN_PROFILES[1].requires_confirmation is True


def test_run_live_dashboard_entry_returns_to_the_landing_screen(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Menu entry 1 is the dashboard itself: selecting it closes the menu, the same as Esc
    -- the dashboard remains the console's landing screen."""
    painted, _binding = _console_run(_deployment_dir(tmp_path), monkeypatch, [ord("m"), ord("1")])

    menu_idx = next(i for i, t in enumerate(painted) if "keel console" in t and "menu" in t)
    assert any("paper mode" in t for t in painted[menu_idx:])


@pytest.mark.parametrize(
    ("open_key", "closing_line"),
    [
        (ord("h"), "Press q, Esc, h or ? now to return to the dashboard."),
        (ord("i"), "Press i or Esc to return to the dashboard."),
        (ord("s"), "Press s or Esc to return to the dashboard."),
        (ord("p"), "Press p or Esc to return to the dashboard."),
        (ord("d"), "Press d or Esc to return to the dashboard."),
    ],
)
def test_run_live_end_scrolls_to_the_overlays_true_last_line_with_a_banner(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, open_key: int, closing_line: str
) -> None:
    """The banner is part of every scrolled list, so the scroll math must count it: with a
    2-line banner prepended, `End` on any of the five scrollable overlays must reach the
    overlay's TRUE last line -- painted on the window's bottom row, not two rows short of
    a help tail the clamp was hiding forever (`_scroll_offset` was clamped against the
    banner-EXCLUDED length while `_visible_slice` sliced the combined list)."""
    fake_curses = _fake_curses()
    height = 6
    # poll1: normal -> the open key. poll2: overlay at offset 0, End pressed. poll3: the
    # End-scrolled frame. Esc closes; the post-exhaustion 'q' quits.
    stdscr, _binding = _console_session(
        _deployment_dir(tmp_path),
        monkeypatch,
        [open_key, fake_curses.KEY_END, 27],
        height=height,
    )

    bottom_row = [call for call in stdscr.calls if call[0] == height - 1]
    assert any(call[2] == closing_line for call in bottom_row), (
        "End must land the overlay's own closing line on the bottom row -- with the banner "
        "counted, not treated as free rows the clamp can spend"
    )


def test_run_live_m_in_the_profile_menu_returns_to_the_menu(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`m` closes profile mode too, the same close-key consistency menu and placeholder
    modes keep (q/Esc/m): profile mode used to close on q/Esc/p only, so the key that
    OPENED the shell could not step back one level out of it.

    Asserted on the frame IMMEDIATELY after the `m` keypress (frames delimited by their
    y=0 first row), not on "a menu frame appears later" -- the quit path also passes
    through the menu, which would make a dead `m` look bound."""
    stdscr, _binding = _console_session(
        _deployment_dir(tmp_path), monkeypatch, [ord("m"), ord("2"), -1, ord("m"), ord("q")]
    )
    texts = [call[2] for call in stdscr.calls]
    starts = [i for i, call in enumerate(stdscr.calls) if call[0] == 0]
    frames = [
        texts[start : starts[j + 1] if j + 1 < len(starts) else len(texts)]
        for j, start in enumerate(starts)
    ]

    # poll4 (frame index 3) is the profile frame whose keypress is `m`; frame 4 is what
    # that keypress did.
    assert any("keel console -- profile" in t for t in frames[3])
    assert any("keel console -- menu" in t for t in frames[4])


# -- run_live: the Compliance menu (issue #389 C3) -------------------------------------------------


def test_run_live_compliance_entry_opens_the_sub_menu_and_esc_steps_back(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Compliance entry is C3's landing: selecting it opens the Compliance sub-menu
    (every PRD §3 entry under Compliance visible), and Esc steps back to the console menu
    -- the shell is a hierarchy, never a jump."""
    painted, _binding = _console_run(
        _deployment_dir(tmp_path), monkeypatch, [ord("m"), ord("5"), -1, 27, -1, 27]
    )

    compliance_idx = next(
        i for i, t in enumerate(painted) if "compliance" in t.lower() and "menu" in t.lower()
    )
    compliance_text = "\n".join(painted[compliance_idx:])
    for label in (
        "screen", "propose", "attest", "attest-instrument", "exempt", "unexempt",
        "holdings", "discover", "Scout results", "Shariah in force", "subscription show",
        "subscription attest", "subscription set", "withdrawals attest", "purification",
    ):
        assert label in compliance_text, label
    # Esc stepped back to the console menu, not the dashboard.
    after = painted[compliance_idx:]
    assert any("keel console -- menu" in t for t in after)


def test_run_live_compliance_screen_view_is_the_services_own_report(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The screen view renders the SAME report `keel assets screen` computes
    (`build_screen_report` over `screen_product`), offline: the verdict summary line is
    painted, and no broker is ever constructed."""

    def _no_broker(cfg: Any, timeout: Any = None) -> Any:
        raise AssertionError("the screen view must never construct a broker")

    monkeypatch.setattr("keel.commands._common._build_broker", _no_broker)
    painted, _binding = _console_run(
        _deployment_dir(tmp_path),
        monkeypatch,
        [ord("m"), ord("5"), 10, -1, 27, -1, 27, ord("q")],
    )

    assert any("0/1 admitted" in t for t in painted)
    view_idx = next(i for i, t in enumerate(painted) if "compliance" in t and "screen" in t)
    assert any("REJECT" in t for t in painted[view_idx:])


def test_run_live_compliance_attest_form_dispatches_and_suspends_curses(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Selecting a form entry runs the FORM at the terminal (curses suspended for the
    prompts, restored after -- the same dance `_confirm_arm_autonomy` keeps) and shows
    the write's confirmation line on the Compliance menu. The form itself is
    `compliance_console.run_form` -- the same seam the unit tests drive -- spied here to
    prove the dispatch carries the loop's own repo/config/now."""
    from keel.commands import compliance_console as cc

    calls: list[dict[str, Any]] = []

    def _spy_form(name: str, repo: Any, config: Any, prompt_fn: Any, now_ts: int) -> str:
        calls.append({"name": name, "now_ts": now_ts})
        return "attested BTC: sector=payments backing=ayn pays_yield=False"

    monkeypatch.setattr(cc, "run_form", _spy_form)

    # m -> menu; 5 -> Compliance; j j -> cursor on 'attest' (index 2); Enter runs the
    # form; then step back out and quit.
    painted, _binding = _console_run(
        _deployment_dir(tmp_path),
        monkeypatch,
        [ord("m"), ord("5"), ord("j"), ord("j"), 10, -1, 27, -1, 27, ord("q")],
    )

    assert calls == [{"name": "attest", "now_ts": NOW_TS}]
    assert any("attested BTC: sector=payments" in t for t in painted)
    # the suspend/restore dance ran around the form (the fake curses records it)
    import sys as _sys

    fake_curses = _sys.modules["curses"]
    assert "def_prog_mode" in fake_curses.calls
    assert "reset_prog_mode" in fake_curses.calls


def test_run_live_scout_browser_lists_selects_and_screens_a_shortlist(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O6 end to end through the live loop: the Scout results browser lists the
    operator-local shortlists (path from config), Enter opens one, and the view renders
    the admission services' own verdicts for THAT file -- propose → screen through
    `build_propose_view`, no auto-attest anywhere."""
    deployment = _deployment_dir(tmp_path)
    proposals = tmp_path / "proposals"
    proposals.mkdir()
    (proposals / "2026-08-15-shortlist.json").write_text(
        '{"candidates": [{"asset": "FET", "rationale": "ai compute", '
        '"sources": ["https://example.com/fet"]}]}'
    )
    # the config the console loads names the proposals dir (config.proposals_dir)
    (deployment / "config.paperforward.yaml").write_text(
        "allowlist: [BTC]\ncaps: {max_exposure_usd: 100, max_per_asset_pct: 0.5}\n"
        f"proposals_dir: {proposals}\n"
    )

    keys = [ord("m"), ord("5")]
    keys += [ord("j")] * 8  # cursor to 'Scout results' (index 8)
    keys += [10]  # Enter -> the scout list
    keys += [-1]
    keys += [10]  # Enter -> the selected shortlist, screened
    keys += [-1, 27]  # Esc -> back to the list
    keys += [-1, 27]  # Esc -> back to Compliance
    keys += [-1, 27, ord("q")]
    painted, _binding = _console_run(deployment, monkeypatch, keys)

    list_idx = next(
        i for i, t in enumerate(painted) if t.startswith("keel console -- compliance / Scout")
    )
    assert "2026-08-15-shortlist.json" in "\n".join(painted[list_idx : list_idx + 8])
    view = "\n".join(painted[list_idx:])
    assert "REJECT" in view  # the gate ran on the unattested candidate
    assert "FET" in view
    assert any("never" in t.lower() and "attest" in t.lower() for t in painted[list_idx:])


def test_run_live_scout_attest_key_drives_the_typed_form_end_to_end(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O6's acceptance, through the LIVE loop: a real proposal file, screened by the real
    services, attested by the `a` key running the REAL typed form -- the prompt answered
    by a scripted `click.prompt` stand-in, the write landing in the deployment's own
    database, and a WRONG typed phrase on a second candidate writing nothing."""
    from keel.data.db import connect, migrate
    from keel.data.repository import Repository as Repo

    deployment = _deployment_dir(tmp_path)
    proposals = tmp_path / "proposals"
    proposals.mkdir()
    (proposals / "2026-08-15-shortlist.json").write_text(
        '{"candidates": ['
        '{"asset": "FET", "rationale": "ai compute", "sources": ["https://x.example/fet"],'
        ' "shariah_hypothesis": "compute, not lending"},'
        '{"asset": "ATOM", "rationale": "staking chain", "sources": ["https://x.example/atom"]}'
        "]}"
    )
    (deployment / "config.paperforward.yaml").write_text(
        "allowlist: [BTC]\ncaps: {max_exposure_usd: 100, max_per_asset_pct: 0.5}\n"
        f"proposals_dir: {proposals}\n"
    )

    # The form's terminal prompts, scripted: FET answered fully (typed phrase = the asset
    # code); ATOM refused at the typed gate.
    answers = iter(
        [
            "payments", "ayn", "n", "https://x.example/fet-attest", "operator", "FET",
            "payments", "native", "n", "https://x.example/atom-attest", "operator", "nope",
        ]
    )
    monkeypatch.setattr(
        click, "prompt", lambda text, **kwargs: next(answers), raising=True
    )

    keys = [ord("m"), ord("5")]
    keys += [ord("j")] * 8  # Scout results
    keys += [10]  # the list
    keys += [10]  # open the shortlist (screened by the services)
    keys += [ord("a")]  # attest candidate 0 (FET) -- the typed form runs
    keys += [-1]
    keys += [ord("j")]  # candidate 1 (ATOM)
    keys += [ord("a")]  # the typed form runs, and the phrase is wrong
    keys += [-1, 27, -1, 27, -1, 27, ord("q")]
    painted, _binding = _console_run(deployment, monkeypatch, keys)

    conn = connect(str(deployment / "keel.db"))
    migrate(conn)
    rows = Repo(conn).get_asset_attestations()
    assert [row["asset"] for row in rows] == ["FET"]  # ATOM was refused at the gate
    assert rows[0]["sector"] == "payments" and rows[0]["backing"] == "ayn"
    assert any("attested FET" in t for t in painted)
    assert any("cancelled" in t.lower() and "ATOM" in t for t in painted)


def _painted_frames(stdscr: _FakeStdscr) -> list[list[str]]:
    """The painted texts split into FRAMES (one per paint -- delimited by each frame's
    y=0 row), so a test can assert on what one keypress actually made the loop paint."""
    texts = [call[2] for call in stdscr.calls]
    starts = [i for i, call in enumerate(stdscr.calls) if call[0] == 0]
    return [
        texts[start : starts[j + 1] if j + 1 < len(starts) else len(texts)]
        for j, start in enumerate(starts)
    ]


def test_run_live_compliance_holdings_view_is_armed_until_enter(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Compliance menu's holdings view keeps the discover overlay's own gating story,
    pinned through the LIVE loop: navigating to it, opening it, and polling it ARMED make
    ZERO venue calls of its own -- the one `get_accounts` happens only on the explicit
    Enter. `get_accounts` IS called once more during this run -- by the pre-existing
    automatic first-poll balance refresh (see `test_run_live_screen_and_propose_never_
    construct_a_broker`'s docstring); with a constant `now_fn` it fires exactly once,
    before the menu is even open, so a total of 2 is exactly one refresh plus one
    holdings read -- any call from opening or polling the ARMED view would make it 3."""
    get_accounts_calls: list[int] = []

    class _CountingBroker:
        def get_accounts(self) -> list[Any]:
            get_accounts_calls.append(1)
            return [
                {"currency": "SOL", "available_balance": Decimal("3.5")},
            ]

        def list_products(self) -> list[dict]:
            return []

    # m -> menu; 5 -> Compliance; 7 -> holdings (ARMED); -1, -1 -> repaints, no call;
    # 10 -> Enter runs the ONE read; -1 -> repaint the held result, no further call;
    # 27 -> close to the menu; 27 -> back to the console menu (then the default q quits).
    stdscr, _binding = _console_session(
        _deployment_dir(tmp_path),
        monkeypatch,
        [ord("m"), ord("5"), ord("7"), -1, -1, 10, -1, 27, 27],
        build_broker=lambda cfg, timeout=None: _CountingBroker(),
    )
    frames = _painted_frames(stdscr)

    assert len(get_accounts_calls) == 2  # one balance refresh + exactly one holdings read
    armed_frames = [f for f in frames if any("ARMED" in t for t in f)]
    assert armed_frames, "the holdings view must open ARMED"
    holdings_frames = [f for f in frames if any("SOL" in t for t in f)]
    assert holdings_frames, "Enter's held result must paint"


def test_run_live_compliance_discover_view_armed_until_enter_and_enter_retries_after_a_failure(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The discover console view, through the loop: ZERO `list_products` calls while
    navigating/arming, and after a FAILED Enter the held error names the retry key (never
    a "retrying..." nothing retries on its own) -- and that key ACTUALLY retries: a second
    Enter runs the read again, so a transient venue failure costs one keypress, not a
    closed screen. The successful run renders the CLI's own discover sweep (`run_discovery`
    -> `render_discover`), so the held result paints the candidate the venue returned."""
    list_products_calls: list[int] = []

    class _FlakyBroker:
        def get_accounts(self) -> list[Any]:
            return []

        def list_products(self) -> list[dict]:
            list_products_calls.append(1)
            if len(list_products_calls) == 1:
                raise RuntimeError("venue unreachable")
            return [
                {
                    "product_id": "SOL-USD",
                    "quote_currency_id": "USD",
                    "status": "online",
                    "trading_disabled": False,
                    "is_disabled": False,
                    "view_only": False,
                    "quote_24h_volume": "9000000",
                    "base_name": "Solana",
                }
            ]

    # m -> menu; 5 -> Compliance; 8 -> discover (ARMED); -1 -> repaint, no call; 10 ->
    # Enter FAILS (error held); -1 -> repaint the error, no call; 10 -> Enter RETRIES and
    # succeeds; -1 -> repaint the held result, no further call; 27, 27 -> step back out.
    stdscr, _binding = _console_session(
        _deployment_dir(tmp_path),
        monkeypatch,
        [ord("m"), ord("5"), ord("8"), -1, 10, -1, 10, -1, 27, 27],
        build_broker=lambda cfg, timeout=None: _FlakyBroker(),
    )
    frames = _painted_frames(stdscr)

    assert len(list_products_calls) == 2  # armed: zero; each Enter: exactly one
    error_frames = [f for f in frames if any("read failed" in t for t in f)]
    assert error_frames, "the failed Enter must paint the failure"
    error_text = "\n".join("\n".join(f) for f in error_frames)
    assert "venue unreachable" in error_text
    assert "retrying" not in error_text
    assert "press Enter to retry" in error_text
    assert any(
        any("SOL-USD" in t for t in f) for f in frames
    ), "the retried run's held result must paint"


def test_run_live_the_compliance_menu_scrolls_to_keep_the_cursor_visible(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Compliance tree is 15 entries (more rows than a small terminal once every
    description fits the 80-column budget): the menu must SCROLL -- painted through
    `_visible_slice`, the cursor row kept on screen by the `_follow_cursor` rule, and
    End/PgDn reaching the true tail (banner-aware math), exactly like the other overlays.
    Before this, the tail entries were painted nowhere -- below the fold with no way up."""
    fake_curses = _fake_curses()
    # m -> menu; 5 -> Compliance (offset 0 -- purification is below the fold); j x14 ->
    # walk the cursor down to 'purification'; -1 -> paint with the cursor followed;
    # End -> the true last page; 27, 27 -> step back out (then the default q quits).
    keys = [ord("m"), ord("5"), *([ord("j")] * 14), -1, fake_curses.KEY_END, -1, 27, 27]
    stdscr, _binding = _console_session(
        _deployment_dir(tmp_path), monkeypatch, keys, height=10
    )
    frames = _painted_frames(stdscr)

    first = next(f for f in frames if any("keel console -- compliance" in t for t in f))
    assert not any("purification" in t for t in first)  # below the fold at offset 0
    followed = next(
        f
        for f in frames
        if any(t.lstrip().startswith(">") and "purification" in t for t in f)
    )
    # End reached the menu's true tail: the closing hint is on screen after it.
    assert any(
        any("to the Compliance menu" in t for t in f)
        for f in frames[frames.index(followed) :]
    )


def test_run_live_the_scout_list_scrolls_to_keep_older_shortlists_reachable(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O6's own promise, made scrollable: the Scout results browser lists EVERY shortlist
    so an OLDER run stays reachable -- on a small window that means the cursor's row must
    follow the cursor (`_follow_cursor`), not fall off the fold where the older files sit."""
    deployment = _deployment_dir(tmp_path)
    proposals = tmp_path / "proposals"
    proposals.mkdir()
    names = [f"2026-08-{day:02d}-shortlist.json" for day in range(1, 13)]
    for index, name in enumerate(names):
        path = proposals / name
        path.write_text("{}")
        stamp = 1_800_000_000 + index * 3600
        os.utime(path, (stamp, stamp))
    (deployment / "config.paperforward.yaml").write_text(
        "allowlist: [BTC]\ncaps: {max_exposure_usd: 100, max_per_asset_pct: 0.5}\n"
        f"proposals_dir: {proposals}\n"
    )

    # m -> menu; 5 -> Compliance; 9 -> Scout results (newest first, oldest below the
    # fold on a 10-line window); j x11 -> the cursor walks to the OLDEST shortlist;
    # -1 -> paint with the cursor followed; 27, 27 -> back out (then q quits).
    keys = [ord("m"), ord("5"), ord("9"), *([ord("j")] * 11), -1, 27, 27]
    stdscr, _binding = _console_session(deployment, monkeypatch, keys, height=10)
    frames = _painted_frames(stdscr)

    oldest = names[0]
    first_list = next(
        f for f in frames if any("compliance / Scout results" in t for t in f)
    )
    assert not any(oldest in t for t in first_list)  # below the fold on entry
    # ...and by the time the cursor has walked to it, its row is ON screen, marked.
    assert any(
        any(t.lstrip().startswith(">") and oldest in t for t in f) for f in frames
    )


def test_run_live_the_shariah_view_pins_the_honesty_lines_at_every_scroll_offset(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O10 through the live loop: the two standing honesty lines are a FIXED footer of
    the shariah view -- painted on the frame at offset 0 AND after End scrolls the body
    to its tail. Before this they rode the body's END, one viewport below the fold on
    any real allowlist, and an operator who never scrolled never saw them."""
    fake_curses = _fake_curses()
    # m -> menu; 5 -> Compliance; j x9 -> 'Shariah in force'; 10 -> open (offset 0);
    # -1 -> repaint; End -> the body's true last page; -1 -> repaint; 27, 27 -> back out.
    keys = [
        ord("m"), ord("5"), *([ord("j")] * 9), 10, -1, fake_curses.KEY_END, -1, 27, 27,
    ]
    stdscr, _binding = _console_session(
        _deployment_dir(tmp_path), monkeypatch, keys, height=10
    )
    frames = _painted_frames(stdscr)

    # The view stays open for exactly three paints after it opens: offset 0, the offset-0
    # repaint during which End is pressed, and the End-scrolled repaint. Every one of
    # them -- including the scrolled frame, whose title line has scrolled OFF -- must
    # carry the pinned honesty lines.
    first_idx = next(
        i for i, f in enumerate(frames) if any("compliance / Shariah in force" in t for t in f)
    )
    view_frames = frames[first_idx : first_idx + 3]
    assert len(view_frames) == 3
    for frame in view_frames:
        joined = "\n".join(frame)
        assert "not a fatwa engine" in joined
        assert "No scholarly review" in joined
    # and the last frame really IS the scrolled one: the title is gone, the footer
    # stayed -- that is the below-the-fold bug, inverted.
    assert not any("compliance / Shariah in force" in t for t in view_frames[2])


def test_run_live_m_closes_the_scout_list_and_the_scout_view(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The q/Esc/m close-key consistency every other console mode keeps: `m` -- the key
    that opened the shell -- steps back one level out of BOTH scout modes (view -> list,
    list -> the Compliance menu), so an operator is never trapped two levels deep."""
    deployment = _deployment_dir(tmp_path)
    proposals = tmp_path / "proposals"
    proposals.mkdir()
    (proposals / "2026-08-15-shortlist.json").write_text(
        '{"candidates": [{"asset": "FET", "rationale": "ai compute", '
        '"sources": ["https://example.com/fet"]}]}'
    )
    (deployment / "config.paperforward.yaml").write_text(
        "allowlist: [BTC]\ncaps: {max_exposure_usd: 100, max_per_asset_pct: 0.5}\n"
        f"proposals_dir: {proposals}\n"
    )

    # m -> menu; 5 -> Compliance; 9 -> the scout list; 10 -> the shortlist view; m ->
    # back to the LIST; m -> back to the COMPLIANCE MENU; 27, 27 -> back out; q quits.
    keys = [ord("m"), ord("5"), ord("9"), 10, ord("m"), ord("m"), 27, 27]
    stdscr, _binding = _console_session(deployment, monkeypatch, keys)
    frames = _painted_frames(stdscr)

    view_idx = next(
        i for i, f in enumerate(frames) if any("shortlist:" in t for t in f)
    )
    # `m` from the VIEW landed on the list, not a jump past it...
    assert any(
        "2026-08-15-shortlist.json" in t for t in frames[view_idx + 1]
    )
    # ...and `m` from the LIST landed on the Compliance menu.
    assert any(
        any("keel console -- compliance" in t and "Scout results" not in t for t in f)
        for f in frames[view_idx + 2 :]
    )


def test_cached_scout_view_rescreens_only_when_the_file_changes(
    repo: Repository, tmp_path: Any
) -> None:
    """The scout view repaints every poll, but the shortlist FILE does not change under a
    held screen: the parsed-and-screened view is cached per (path, mtime), so a repaint
    re-screens nothing, while a changed file (new mtime) refreshes. Without the cache
    every poll re-read, re-parsed and re-SCREENED the same bytes through the admission
    gate -- a DB read per candidate per poll, for a file that had not changed."""
    from keel.commands.tui import cached_scout_view
    from keel.compliance.screen import MarketFacts, ScreenResult

    shortlist = tmp_path / "2026-08-15-shortlist.json"
    shortlist.write_text(
        '{"candidates": [{"asset": "FET", "rationale": "ai compute", '
        '"sources": ["https://example.com/fet"]}]}'
    )
    os.utime(shortlist, (1_800_000_000, 1_800_000_000))
    config = _config(proposals_dir=str(tmp_path))
    screened: list[str] = []

    def counting_screen_fn(r: Any, product: str, quote: str) -> Any:
        screened.append(product)
        facts = MarketFacts(
            asset="FET", daily_bars=2000, median_daily_volume=Decimal("1000"),
            quotable_in_settlement_currency=True, product_id="FET-USD", venue="coinbase",
        )
        return facts, ScreenResult(asset="FET", admitted=True, failures=[], warnings=[])

    cache: dict[tuple[str, int], Any] = {}
    first = cached_scout_view(repo, config, counting_screen_fn, shortlist, cache)
    assert first.status == "ok"
    assert screened == ["FET-USD"]

    # unchanged mtime -> the cache answers; the gate does not run again
    second = cached_scout_view(repo, config, counting_screen_fn, shortlist, cache)
    assert second is first
    assert screened == ["FET-USD"]

    # a changed file (new mtime) refreshes: the gate runs again on the new bytes
    shortlist.write_text(
        '{"candidates": [{"asset": "FET", "rationale": "compute, revised", '
        '"sources": ["https://example.com/fet-v2"]}]}'
    )
    os.utime(shortlist, (1_800_000_100, 1_800_000_100))
    third = cached_scout_view(repo, config, counting_screen_fn, shortlist, cache)
    assert third.status == "ok" and third is not first
    assert screened == ["FET-USD", "FET-USD"]
