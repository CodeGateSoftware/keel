"""Tests for `keel.commands.account_console` -- the Account menu (issue #392 C6; PRD §3's
Account branch: pnl + versions), the console tree's LAST placeholder turned real.

Four surfaces, all pinned here:

* **The sub-menu** -- PRD §3's Account branch: `pnl` and `versions`, both READ-ONLY views
  (the one console area with no write path at all -- its ceremony row in the C6 audit
  table says exactly that).
* **pnl** -- the SAME report `keel pnl` prints (`commands.pnl.build_pnl_report` +
  `render_pnl_report`, the C1 services), rendered verbatim from the ACTIVE deployment's
  imported transactions; an empty transactions table renders its honest empty state
  naming the import path, never a confident `total realized P&L: 0`.
* **versions** -- the SAME lines `keel versions` prints, through ONE shared renderer
  extracted to `keel.commands.versions` (the C1 "one implementation, two front-ends"
  rule), disagreement styled loud; the environment scan runs ONCE per entry, never per
  poll (the venues browser's contract).
* **The live loop** -- the tree's Account entry opens this sub-menu (no more "lands in
  C6" notice anywhere in the tree), the views are banner-aware, offline and m-close.

Mirrors `tests/commands/test_data_console.py`'s fixture style.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest

from keel.commands import account_console as ac
from keel.commands.versions import render_versions_lines
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.version import BuildInfo, InstallReport

NOW_TS = 1_800_000_000

_RELEASE = BuildInfo(version="0.6.0", commit="deadbeef", dirty=False, source="release")


@pytest.fixture
def repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    return Repository(conn)


def _buy(repo: Repository, asset: str, qty: str, price: str, ts: int) -> None:
    """One imported-style BUY row -- the CSV-import shape `keel db import` writes."""
    from decimal import Decimal

    repo.upsert_transaction(
        dict(
            coinbase_id=f"tx-{asset}-{ts}",
            source="csv_import",
            type="Buy",
            asset=asset,
            ts=ts,
            qty=Decimal(qty),
            price=Decimal(price),
            subtotal=None,
            total=None,
            fees=Decimal("0.10"),
            notes=None,
            rule_id=None,
            order_id=None,
        )
    )


# -- the sub-menu (PRD §3's Account branch) --------------------------------------------------------


def test_the_account_menu_is_the_prd_account_branch() -> None:
    """PRD §3's Account line is exactly two entries: pnl and versions -- both views."""
    assert [(e.label, e.kind, e.target) for e in ac.ACCOUNT_MENU] == [
        ("pnl", "view", "pnl"),
        ("versions", "view", "versions"),
    ]


def test_the_account_menu_entries_are_reachable_by_their_displayed_ordinals() -> None:
    for entry in ac.ACCOUNT_MENU:
        assert ac.account_entry(entry.ordinal) is entry
    assert ac.account_entry(0) is None
    assert ac.account_entry(3) is None


def test_the_menu_screen_renders_every_entry_and_the_keys() -> None:
    lines = ac.build_account_menu_lines(cursor=1)
    texts = [line.text for line in lines]
    for entry in ac.ACCOUNT_MENU:
        assert any(entry.label in t for t in texts), entry.label
    # the cursor marks exactly one row (versions, index 1), like every console sub-menu
    marked = [t for t in texts if t.lstrip().startswith(">")]
    assert len(marked) == 1 and "versions" in marked[0]
    # the keys footer states the console contracts: ordinals, select, and m-close
    joined = "\n".join(texts)
    assert "Enter/Space select" in joined
    assert "1-2 jump" in joined
    assert "q/Esc/m" in joined
    assert all(len(line.text) <= 80 for line in lines)


def test_the_account_screens_fit_the_80_column_clip(repo: Repository) -> None:
    """`_paint` clips at the window width -- every line this module renders must fit."""
    _buy(repo, "BTC", "0.5", "60000", NOW_TS - 86_400)
    _buy(repo, "ETH", "2", "3000", NOW_TS - 43_200)
    for lines in (
        ac.build_account_menu_lines(),
        ac.build_pnl_lines(repo.get_transactions()),
        ac.build_pnl_lines([]),
        ac.build_versions_lines(
            render_versions_lines(
                _RELEASE,
                InstallReport(
                    distributions={
                        "keel-trader": "0.6.0",
                        "keel-core": "0.5.5",
                    },
                    source="release",
                ),
            )
        ),
    ):
        assert all(len(line.text) <= 80 for line in lines), lines


# -- pnl: the service's own report, verbatim, over the ACTIVE deployment ---------------------------


def test_pnl_renders_the_services_own_report_verbatim(repo: Repository) -> None:
    _buy(repo, "BTC", "0.5", "60000", NOW_TS - 86_400)
    _buy(repo, "BTC", "0.5", "62000", NOW_TS - 43_200)
    lines = ac.build_pnl_lines(repo.get_transactions())
    texts = [line.text for line in lines]
    # the report's own lines render EXACTLY as `keel pnl` prints them (the C1 parity)
    from keel.commands.pnl import build_pnl_report, render_pnl_report

    for report_line in render_pnl_report(build_pnl_report(repo.get_transactions(), None, {})):
        assert report_line.strip() in [t.strip() for t in texts], report_line
    # and the screen says what it is: the overall report, no marks supplied
    joined = "\n".join(texts)
    assert "no marks supplied" in joined


def test_pnl_renders_its_honest_empty_state_when_nothing_is_imported(repo: Repository) -> None:
    """No CSV imports, no report: an empty transactions table must NOT render a confident
    `total realized P&L: 0` -- it names the import path that would fill it instead."""
    lines = ac.build_pnl_lines(repo.get_transactions())
    joined = "\n".join(line.text for line in lines)
    assert "total realized P&L" not in joined
    assert "no imported transactions" in joined
    assert "db import" in joined


# -- versions: ONE renderer, two front-ends --------------------------------------------------------


def _install(monkeypatch: pytest.MonkeyPatch, dists: dict[str, str]) -> None:
    monkeypatch.setattr(ac, "build_info", lambda: _RELEASE)
    monkeypatch.setattr(
        ac,
        "check_install",
        lambda source=None: InstallReport(distributions=dict(dists), source="release"),
    )


def test_versions_view_renders_the_shared_renderers_exact_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The console shows the SAME lines `keel versions` prints -- through the ONE renderer
    both front-ends share (`versions.render_versions_lines`), so the deploy check cannot
    drift between terminal and console."""
    _install(monkeypatch, {"keel-trader": "0.6.0", "keel-core": "0.6.0"})
    rows = ac.versions_rows()
    lines = ac.build_versions_lines(rows)
    texts = [line.text for line in lines]
    for text, _to_stderr in render_versions_lines(
        _RELEASE, InstallReport(distributions={"keel-trader": "0.6.0", "keel-core": "0.6.0"},
                                source="release")
    ):
        assert text in texts, text


def test_versions_disagreement_is_loud_and_the_scan_runs_once_per_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A partial install renders its error lines in the alert style (the CLI exits
    non-zero; the console's loud equivalent), and ONE `versions_rows()` read is ONE
    environment scan -- the read the live loop holds, so repaints never re-scan (the
    hold itself is pinned by the run_live test below)."""
    scans: list[int] = []

    def _scan(source: str | None = None) -> InstallReport:
        scans.append(1)
        return InstallReport(
            distributions={"keel-trader": "0.6.0", "keel-core": "0.5.5"}, source="release"
        )

    monkeypatch.setattr(ac, "build_info", lambda: _RELEASE)
    monkeypatch.setattr(ac, "check_install", _scan)

    rows = ac.versions_rows()

    assert len(scans) == 1
    lines = ac.build_versions_lines(rows)
    alert_texts = [line.text for line in lines if line.style == "alert"]
    assert any("PARTIAL INSTALL" in t for t in alert_texts)


def test_versions_reports_a_checkout_with_nothing_installed_calmly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = BuildInfo(version="0.6.0", commit="deadbeef", dirty=False, source="checkout")
    monkeypatch.setattr(ac, "build_info", lambda: checkout)
    monkeypatch.setattr(
        ac,
        "check_install",
        lambda source=None: InstallReport(distributions={}, source="checkout"),
    )
    lines = ac.build_versions_lines(ac.versions_rows())
    joined = "\n".join(line.text for line in lines)
    assert "nothing to compare" in joined


def test_a_non_reproducible_build_warns_on_the_console_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dirty = BuildInfo(version="0.6.0", commit="deadbeef", dirty=True, source="checkout")
    monkeypatch.setattr(ac, "build_info", lambda: dirty)
    monkeypatch.setattr(
        ac,
        "check_install",
        lambda source=None: InstallReport(distributions={"keel-trader": "0.6.0"},
                                          source="checkout"),
    )
    lines = ac.build_versions_lines(ac.versions_rows())
    assert any("NOT reproducible" in line.text for line in lines)


def test_the_context_help_covers_all_three_modes() -> None:
    for mode in ("account", "account-pnl", "account-versions"):
        assert ac.CONTEXT_HELP[mode], mode
        for subject, description in ac.CONTEXT_HELP[mode]:
            assert subject.strip() and description.strip(), mode


# -- the live loop: the tree's last placeholder is real --------------------------------------------


def _drive(
    repo: Repository,
    config: Any,
    keys: list[int],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    *,
    height: int = 30,
    width: int = 100,
) -> Any:
    """Run `run_live` under the fake curses module with a REAL console binding over the
    temp deployment dir, replaying `keys`; returns the stdscr (its `calls` hold every
    painted line). The balance refresh's broker construction is stubbed (it fires on the
    first poll by design) so no test touches the network."""
    import click

    from keel.commands import console as console_mod
    from keel.commands.tui import run_live

    monkeypatch.chdir(tmp_path)
    ctx = click.Context(click.Command("tui"), obj={})
    ctx.obj["config_path"] = "config.paperforward.yaml"
    ctx.obj["db_path"] = "keel.db"
    binding = console_mod.ConsoleBinding(
        ctx, config_path="config.paperforward.yaml", db_path="keel.db"
    )

    stdscr = _KeySequenceStdscr(height=height, width=width, keys=keys)
    fake_curses = _fake_curses()
    fake_curses.wrapper = lambda fn: fn(stdscr)
    monkeypatch.setitem(sys.modules, "curses", fake_curses)

    class _FakeBroker:
        def get_accounts(self) -> list[Any]:
            return []

    monkeypatch.setattr(
        "keel.commands._common._build_broker",
        lambda cfg, timeout=None: _FakeBroker(),
    )

    def open_state() -> tuple[Repository, Any]:
        return repo, config

    run_live(open_state, lambda: NOW_TS, interval=0.01, console_binding=binding)
    return stdscr


class _FakeCursesError(Exception):
    pass


def _fake_curses() -> Any:
    from types import SimpleNamespace

    calls: list[str] = []
    return SimpleNamespace(
        A_BOLD=1,
        A_DIM=2,
        A_UNDERLINE=4,
        A_REVERSE=8,
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
        has_colors=lambda: False,
        start_color=lambda: calls.append("start_color"),
        use_default_colors=lambda: calls.append("use_default_colors"),
        init_pair=lambda n, fg, bg: calls.append(f"init_pair:{n}"),
        color_pair=lambda n: 1 << (10 + n),
        curs_set=lambda visibility: None,
        def_prog_mode=lambda: calls.append("def_prog_mode"),
        endwin=lambda: calls.append("endwin"),
        reset_prog_mode=lambda: calls.append("reset_prog_mode"),
        wrapper=None,
        calls=calls,
    )


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

    def timeout(self, ms: int) -> None:
        pass


class _KeySequenceStdscr(_FakeStdscr):
    def __init__(self, height: int, width: int, keys: list[int]) -> None:
        super().__init__(height, width)
        self._keys = list(keys)

    def getch(self) -> int:
        if self._keys:
            return self._keys.pop(0)
        return ord("q")


def _config() -> Any:
    from decimal import Decimal

    from keel.config import (
        AutoTradeConfig,
        Caps,
        Config,
        DcaConfig,
        MarketDataConfig,
        MoneyMgmtConfig,
    )
    from keel.types import Granularity

    return Config(
        allowlist=["BTC"],
        target_weights={},
        risk_pct=Decimal("0.01"),
        caps=Caps(
            max_per_order_usd=Decimal("100000"),
            max_per_day_usd=Decimal("300000"),
            max_exposure_usd=Decimal("1000000"),
            max_per_asset_pct=Decimal("1"),
        ),
        market_data=MarketDataConfig(granularities=[Granularity.ONE_HOUR], history_days=365),
        auto_trade=AutoTradeConfig(mode="paper", interval_sec=900),
        money_mgmt=MoneyMgmtConfig(
            max_total_dd_pct=Decimal("0.20"), max_weekly_dd_pct=Decimal("0.08")
        ),
        dca=DcaConfig(budget_usd=Decimal("50"), cadence_days=7),
    )


def test_run_live_the_account_entry_opens_the_sub_menu_and_the_views_close_on_m(
    repo: Repository, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """8 (or the cursor) opens the Account sub-menu -- no placeholder notice anywhere;
    Enter opens pnl; m steps back one level to the sub-menu, then to the menu."""
    _buy(repo, "BTC", "0.5", "60000", NOW_TS - 86_400)
    # m -> menu; '8' -> account; poll; Enter -> account-pnl; poll; 'm' -> account; 'q'
    keys = [ord("m"), ord("8"), -1, ord("\r"), -1, ord("m"), -1]
    stdscr = _drive(repo, _config(), keys, monkeypatch, tmp_path)
    painted = [call[2] for call in stdscr.calls]
    account_idx = next(i for i, t in enumerate(painted) if "keel console -- account" in t)
    assert not any("lands in C6" in t for t in painted)
    pnl_idx = next(i for i, t in enumerate(painted) if "account / pnl" in t)
    assert pnl_idx > account_idx
    # m from the pnl view returns to the ACCOUNT menu (the shell is a hierarchy)
    assert any("keel console -- account" in t for i, t in enumerate(painted) if i > pnl_idx)


def test_run_live_the_pnl_view_renders_the_empty_state_and_the_banner(
    repo: Repository, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    keys = [ord("m"), ord("8"), -1, ord("\r"), -1]
    stdscr = _drive(repo, _config(), keys, monkeypatch, tmp_path)
    painted = [call[2] for call in stdscr.calls]
    assert any("no imported transactions" in t for t in painted)
    # the banner rides every console screen, this one included
    assert any(t.startswith("console:") for t in painted)


def test_run_live_the_versions_entry_holds_its_rows_across_repaints(
    repo: Repository, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    scans: list[int] = []
    monkeypatch.setattr(ac, "build_info", lambda: _RELEASE)
    monkeypatch.setattr(
        ac,
        "check_install",
        lambda source=None: (
            scans.append(1),
            InstallReport(distributions={"keel-trader": "0.6.0"}, source="release"),
        )[1],
    )
    # m -> menu; '8' -> account; poll; down; Enter -> versions; two repaints; q
    keys = [ord("m"), ord("8"), -1, ord("j"), ord("\r"), -1, -1]
    stdscr = _drive(repo, _config(), keys, monkeypatch, tmp_path)
    painted = [call[2] for call in stdscr.calls]
    assert any("keel console -- account / versions" in t for t in painted)
    # ONE scan served the entry and every repaint after it (the venues contract)
    assert len(scans) == 1


def test_the_console_tree_has_no_placeholders_left() -> None:
    """C6 landed Account: every PRD §3 entry is a live destination now -- the placeholder
    MECHANISM stays for future slices, but no current entry points at one."""
    from keel.commands import console as console_mod

    assert all(entry.lands_in is None for entry in console_mod.CONSOLE_MENU)
    assert console_mod.menu_entry(8).action == "account"
