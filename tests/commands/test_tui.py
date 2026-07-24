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
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from keel.cli import cli
from keel.commands.status import (
    AutonomyStatus,
    OpenPositionStatus,
    ProductFreshness,
    RuleSummary,
    StatusReport,
    SubscriptionStatusRow,
)
from keel.commands.tui import (
    ScreenLine,
    _freshness_style,
    _paint,
    _style_attrs,
    build_screen,
    render_plain,
    run_live,
    run_once,
)
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
    report = _base_report()
    lines = build_screen(report, NOW_TS)
    footer = lines[-1]
    assert footer.style == "muted"
    assert "quit" in footer.text.lower()
    assert "read-only" in footer.text.lower()


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
        error=_FakeCursesError,
        has_colors=lambda: has_colors,
        start_color=lambda: calls.append("start_color"),
        use_default_colors=lambda: calls.append("use_default_colors"),
        init_pair=lambda n, fg, bg: calls.append(f"init_pair:{n}"),
        color_pair=lambda n: 1 << (10 + n),
        curs_set=lambda visibility: None,
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

    def getmaxyx(self) -> tuple[int, int]:
        return (self._height, self._width)

    def addstr(self, y: int, x: int, text: str, attr: int = 0) -> None:
        self.calls.append((y, x, text, attr))

    def erase(self) -> None:
        pass

    def refresh(self) -> None:
        pass


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
    assert "read-only" in joined.lower()


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
        if len(opens) == 1:
            raise sqlite3.OperationalError("database is locked")
        return repo, config

    run_live(open_state, lambda: NOW_TS, interval=0.01)

    # The first poll's read error didn't kill the loop -- a second poll (the happy path) ran too.
    assert len(opens) >= 2
    painted_texts = [call[2] for call in stdscr.calls]
    assert any("status read failed" in t for t in painted_texts)
    assert any("paper" in t for t in painted_texts)


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
    assert "read-only" in result.output.lower()


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
