"""Tests for `keel tui` -- the live, read-only, full-screen operator dashboard.

`keel tui` is a *view* over `keel status`'s own report: it must not re-derive Rail 11,
freshness, or autonomy logic, only style `StatusReport` into `ScreenLine`s. Mirrors
`tests/commands/test_status.py`'s fixture style (in-memory `Repository`, `_config` helper,
`NOW_TS` constant), plus the pure `build_screen`/`_freshness_style`/`render_plain`/`_paint`/
`run_once` seams that make the interactive `run_live` loop thin, untested I/O.
"""

from __future__ import annotations

import sqlite3
import sys
import time
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from keel.cli import cli
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
    OpenPositionStatus,
    ProductFreshness,
    RuleSummary,
    StatusReport,
    SubscriptionStatusRow,
)
from keel.commands.tui import (
    _REFRESH_MESSAGE,
    _SHORT_VERSION,
    AvailableBalance,
    ScreenLine,
    _admission_line_style,
    _available_lines,
    _confirm_arm_autonomy,
    _footer_lines,
    _freshness_style,
    _guarded,
    _human_dt,
    _message_style,
    _paint,
    _refresh_balance,
    _scroll_offset,
    _short_version,
    _stdio_is_interactive,
    _style_attrs,
    _visible_slice,
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
)
from keel.compliance import screen as screen_mod
from keel.config import (
    AutoTradeConfig,
    Caps,
    Config,
    DcaConfig,
    MarketDataConfig,
    MoneyMgmtConfig,
)
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.types import Granularity

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
        ("0.1.0", "v0.1"),
        ("0.2.5", "v0.2"),
        ("2.0", "v2.0"),
        ("10.34.1+abc", "v10.34"),
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


def test_run_live_discover_closing_discards_the_held_result(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Closing the overlay (Esc) must discard the held result -- reopening it must be armed but
    not yet run again, not silently show the previous run's stale candidates."""
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
    # v3: the admission workflow's three overlays and the CLI-only attest step.
    for word in ("screen", "propose", "discover", "attest"):
        assert word in text
    assert lines[0].style == "heading"


def test_build_help_screen_documents_discover_network_gating_and_attest_is_cli_only() -> None:
    """The safety notes must be explicit about the two things that make `discover` different
    from `screen`/`propose`, and that `attest` -- the one step that actually changes the
    allowlist -- is never reachable from here."""
    lines = build_help_screen()
    text = " ".join(line.text.lower() for line in lines)
    assert "second deliberate network exception" in text
    assert "cli-only" in text
    assert "keel assets attest" in text


def test_build_help_screen_is_longer_than_a_small_terminal() -> None:
    lines = build_help_screen()
    assert len(lines) > 24


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
        candidates=[candidate],
        min_quote_24h_volume=Decimal("5000000"),
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
