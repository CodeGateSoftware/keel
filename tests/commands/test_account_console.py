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
    """PRD §3's Account line is pnl and versions (both views); #415 appends the
    branch's ONE write path, the typed update entry."""
    assert [(e.label, e.kind, e.target) for e in ac.ACCOUNT_MENU] == [
        ("pnl", "view", "pnl"),
        ("versions", "view", "versions"),
        ("update", "armed", "update"),
    ]


def test_the_account_menu_entries_are_reachable_by_their_displayed_ordinals() -> None:
    for entry in ac.ACCOUNT_MENU:
        assert ac.account_entry(entry.ordinal) is entry
    assert ac.account_entry(0) is None
    assert ac.account_entry(4) is None


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
    assert "1-3 jump" in joined
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


def test_the_context_help_covers_all_four_modes() -> None:
    for mode in ("account", "account-pnl", "account-versions", "account-update"):
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


# -- the update entry (issue #415): the Account branch's ONE gated write path ----------------------
#
# The branch was read-only top to bottom through C6; the self-update slice adds its
# single mutating action -- check for a newer release, show the whole plan, and deploy
# it only behind the CLI's OWN typed gate (`keel update`'s wording, one gate, both
# front-ends). Everything below drives the service seams with fakes: no network, no
# uv, no execv.


def _fake_plan(tmp_path: Any, *, offered: bool = True) -> Any:
    import json
    from pathlib import Path

    from keel.commands import update as up

    current = "0.6.0"
    latest = "0.7.0" if offered else "0.6.0"
    (tmp_path / "keel.db").write_bytes(b"paper db")
    (tmp_path / "keel-live.db").write_bytes(b"live db")
    # the runbook's deployment layout: the running package resolves from the launch
    # folder's OWN .venv site-packages, installed from a wheel -- so the plan is OFFERED
    site = tmp_path / ".venv/lib/python3.12/site-packages"
    (site / "keel").mkdir(parents=True, exist_ok=True)
    (site / "keel" / "__init__.py").write_text("")
    dist_info = site / "keel_trader-0.6.0.dist-info"
    dist_info.mkdir(exist_ok=True)
    (dist_info / "direct_url.json").write_text(
        json.dumps({"url": "file:///Release/keel_trader-0.6.0-py3-none-any.whl"})
    )
    return up.plan_update(
        up.parse_release(
            b'{"tag_name": "v%s", "assets": [%s]}'
            % (
                latest.encode(),
                b",".join(
                    b'{"name": "%s-%s-py3-none-any.whl", "browser_download_url": "https://e/%s"}'
                    % (prefix.encode(), latest.encode(), prefix.encode())
                    for prefix in up.PRODUCTION_WHEEL_PREFIXES
                ),
            )
        ),
        build=BuildInfo(version=current, commit="deadbeef", dirty=False, source="release"),
        installed={
            "keel-trader": current,
            "keel-core": current,
            "keel-broker-api": current,
            "keel-broker-coinbase": current,
        },
        launch_dir=tmp_path,
        venv_python=tmp_path / ".venv/bin/python",
        package_file=Path(site / "keel" / "__init__.py"),
    )


def test_the_account_menu_gains_the_typed_update_entry() -> None:
    """The Account branch's third entry: update -- the ONE mutating action in the
    branch, an ARMED view whose Enter demands the CLI's own typed gate (its ceremony
    row). pnl and versions stay views."""
    assert [(e.label, e.kind, e.target) for e in ac.ACCOUNT_MENU] == [
        ("pnl", "view", "pnl"),
        ("versions", "view", "versions"),
        ("update", "armed", "update"),
    ]
    assert ac.account_entry(3) is not None
    assert ac.account_entry(4) is None


def test_update_check_reads_the_release_and_builds_the_plan(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The entry-time read: ONE public-API call + the plan -- the versions view's
    contract (read once, hold), never per poll."""
    from keel.commands import update as up

    plan = _fake_plan(tmp_path)  # built BEFORE the seam is patched
    release = up.parse_release(b'{"tag_name": "v0.7.0", "assets": []}')
    calls: list[str] = []
    monkeypatch.setattr(up, "latest_release", lambda fetch=None: calls.append("fetch") or release)
    monkeypatch.setattr(
        up,
        "plan_update",
        lambda rel, **kwargs: calls.append("plan") or plan,
    )
    checked = ac.update_check(launch_dir=tmp_path, venv_python=tmp_path / ".venv/bin/python")
    assert checked is plan
    assert checked.offered is True
    assert calls == ["fetch", "plan"]


def test_update_check_propagates_a_network_failure_honestly(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed check is an ERROR the view holds (with Enter as the retry), not a
    confident 'up to date' -- the same honesty `fetch --check` keeps."""
    from keel.commands import update as up

    def _offline(url: str) -> Any:
        raise up.UpdateError("could not reach the GitHub releases API")

    with pytest.raises(up.UpdateError, match="GitHub"):
        ac.update_check(
            launch_dir=tmp_path, venv_python=tmp_path / ".venv/bin/python", fetch=_offline
        )


def test_build_update_error_lines_render_honestly_with_the_retry(
    tmp_path: Any,
) -> None:
    lines = ac.build_update_error_lines("could not reach the GitHub releases API (offline)")
    joined = "\n".join(line.text for line in lines)
    assert "could not reach" in joined
    assert "Enter" in joined and "re-check" in joined.lower()
    assert all(len(line.text) <= 80 for line in lines)


def test_build_update_lines_renders_the_armed_plan(tmp_path: Any) -> None:
    plan = _fake_plan(tmp_path)
    lines = ac.build_update_lines(plan)
    texts = [line.text for line in lines]
    joined = "\n".join(texts)
    assert any("ARMED -- nothing has run yet." == t.strip() for t in texts)
    # current vs latest lead
    assert "0.6.0" in joined and "0.7.0" in joined
    # the full plan: the four wheels, the Release dir, the DB backups NAMED, the venv
    for prefix in ("keel_core", "keel_trader"):
        assert prefix in joined
    assert "download to:" in joined and "Release" in joined
    assert "keel.db" in joined and ".bak-before-0.7.0-" in joined
    assert "RUNNING venv" in joined
    # the typed gate is named as the confirm step -- Enter alone is not enough
    assert "typed" in joined.lower()
    assert "replac" in joined.lower()  # the running binary is replaced -- stated
    assert all(len(t) <= 80 for t in texts)


def test_build_update_lines_renders_refusals_and_says_nothing_runs(tmp_path: Any) -> None:
    plan = _fake_plan(tmp_path, offered=False)
    lines = ac.build_update_lines(plan)
    joined = "\n".join(line.text for line in lines)
    assert "latest" in joined.lower()
    assert any(line.style == "muted" for line in lines)
    assert all(len(line.text) <= 80 for line in lines)
    # an up-to-date plan renders calm, and offers no Enter-run
    assert "Enter" not in joined or "re-check" in joined.lower()


def test_build_update_result_lines_hold_the_progress_and_the_recovery(
    tmp_path: Any,
) -> None:
    from keel.commands import update as up

    result = up.UpdateResult(
        ok=False,
        steps=(),
        error="verify failed: PARTIAL INSTALL -- manual recovery: the runbook",
        rolled_back=True,
        backups=(),
    )
    lines = ac.build_update_result_lines(
        result, progress=("backing up keel.db", "installing the production wheels")
    )
    joined = "\n".join(line.text for line in lines)
    assert "backing up keel.db" in joined  # the streamed lines are HELD, verbatim
    assert "verify failed" in joined
    assert all(len(line.text) <= 80 for line in lines)


def test_run_update_at_terminal_gates_then_runs_then_relaunches(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The terminal runner: the CLI's OWN typed gate inside the service's confirm
    seam, the service's lines collected as progress, and -- only on a verified
    success -- the relaunch closure (execv is the caller's; here it is faked)."""
    from keel.commands import update as up

    plan = _fake_plan(tmp_path)
    gates: list[bool] = []
    gate_answers: list[bool] = []
    relaunched: list[bool] = []

    def _gate(a_plan: up.UpdatePlan) -> bool:
        gates.append(a_plan is plan)
        return True

    def _relaunch() -> None:
        relaunched.append(True)

    def _fake_run(a_plan: Any, *, echo: Any, confirm_gate: Any) -> up.UpdateResult:
        echo("the service streamed a line")
        assert confirm_gate() is True  # the gate rides the service's own confirm seam
        return up.UpdateResult(ok=True, steps=("verified",), error=None,
                                rolled_back=False, backups=())

    monkeypatch.setattr(up, "run_update", _fake_run)
    progress: list[str] = []
    result = ac.run_update_at_terminal(
        plan, progress=progress, gate_fn=_gate, relaunch_fn=_relaunch
    )
    assert result.ok is True
    assert gates == [True]
    assert relaunched == [True]
    assert progress  # the service's own streamed lines were collected
    assert any("updated to 0.7.0" in line for line in progress)

    # a refused gate writes nothing and never relaunches: the gate rides the service's
    # confirm seam (fail-safe by construction), so the SERVICE is entered but its
    # mutation seams never fire -- a spy mirroring that contract proves the flow
    gates.clear()
    relaunched.clear()
    progress.clear()

    def _refused_run(a_plan: Any, *, echo: Any, confirm_gate: Any) -> up.UpdateResult:
        gate_answers.append(confirm_gate())  # the seam the service calls first
        return up.UpdateResult(
            ok=False,
            steps=(),
            error="confirmation not given -- nothing was changed.",
            rolled_back=False,
            backups=(),
        )

    message = ac.run_update_at_terminal(
        plan,
        progress=progress,
        gate_fn=lambda _plan: False,
        relaunch_fn=_relaunch,
        run_fn=_refused_run,
    )
    assert message.ok is False
    assert gate_answers == [False]
    assert "not given" in (message.error or "").lower()
    assert relaunched == []


def test_run_update_at_terminal_a_failed_relaunch_is_rendered_not_lost(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An execv that RAISES (the relaunch closure wraps the OSError into an
    `UpdateError` naming the manual `keel tui`) must not escape the runner: the update
    itself SUCCEEDED and is verified, so the held result stays ok, the failure and the
    manual start render into the held progress, and the `on_relaunch_failure` seam
    fires so the live loop can hold the retry."""
    from keel.commands import update as up

    plan = _fake_plan(tmp_path)
    result = up.UpdateResult(ok=True, steps=("verified",), error=None,
                             rolled_back=False, backups=())

    def _fake_run(a_plan: Any, *, echo: Any, confirm_gate: Any) -> up.UpdateResult:
        echo("the service streamed a line")
        return result

    monkeypatch.setattr(up, "run_update", _fake_run)

    def _raising_relaunch() -> None:
        raise up.UpdateError(
            "relaunch failed ([Errno 13] permission denied): execv could not start the "
            "new build -- start the console by hand: `/x/.venv/bin/keel tui`"
        )

    failures: list[BaseException] = []
    progress: list[str] = []
    held = ac.run_update_at_terminal(
        plan,
        progress=progress,
        gate_fn=lambda _plan: True,
        relaunch_fn=_raising_relaunch,
        on_relaunch_failure=failures.append,
    )
    assert held.ok is True  # the update completed and verified; only the execv failed
    joined = "\n".join(progress)
    assert "RELAUNCH FAILED" in joined
    assert "keel tui" in joined  # the manual start, named
    assert len(failures) == 1


def test_build_update_result_lines_footer_distinguishes_a_pending_relaunch(
    tmp_path: Any,
) -> None:
    """The result footer says what Enter DOES: re-run the check normally, but retry
    ONLY the relaunch when one is pending -- the state that distinguishes them."""
    from keel.commands import update as up

    result = up.UpdateResult(ok=True, steps=("verified",), error=None,
                             rolled_back=False, backups=())
    normal = "\n".join(
        line.text for line in ac.build_update_result_lines(result, progress=[])
    )
    assert "Enter re-runs" in normal
    pending = "\n".join(
        line.text
        for line in ac.build_update_result_lines(result, progress=[], relaunch_pending=True)
    )
    assert "Enter retries the relaunch" in pending
    assert "re-installs nothing" in pending


def _drive_update(
    keys: list[int],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    *,
    plan: Any,
    run_result: Any = None,
    relaunch_calls: list[Any] | None = None,
) -> Any:
    """`_drive` with the update seams faked: the entry-time check returns `plan`, the
    service returns `run_result` (a refused gate by default), and `relaunch_tui` is
    recorded into `relaunch_calls` instead of execv'ing."""
    from keel.commands import update as up

    monkeypatch.setattr(ac, "update_check", lambda **_kwargs: plan)
    if run_result is None:
        run_result = up.UpdateResult(
            ok=False,
            steps=(),
            error="confirmation not given -- nothing was changed.",
            rolled_back=False,
            backups=(),
        )
    monkeypatch.setattr(
        up,
        "run_update",
        lambda plan, *, echo, confirm_gate: echo(f"gate: {confirm_gate()}") or run_result,
    )
    if relaunch_calls is None:
        relaunch_calls = []

    def _fake_relaunch(venv_python: Any, original_argv: Any) -> Any:
        def _closure() -> None:
            relaunch_calls.append((venv_python, list(original_argv)))

        return _closure

    monkeypatch.setattr(up, "relaunch_tui", _fake_relaunch)
    return _drive(_repo(), _config(), keys, monkeypatch, tmp_path)


def _repo() -> Any:
    conn = connect(":memory:")
    migrate(conn)
    return Repository(conn)


def test_run_live_update_entry_opens_the_armed_view_and_never_runs_the_service(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Opening the update view checks the release ONCE (a read-only public-API GET,
    the versions view's hold contract) and renders ARMED -- repaints re-check nothing,
    and the mutating service is never invoked without Enter + the typed gate."""
    plan = _fake_plan(tmp_path)
    checks: list[int] = []

    def _counting_check(**_kwargs: Any) -> Any:
        checks.append(1)
        return plan

    monkeypatch.setattr(ac, "update_check", _counting_check)
    from keel.commands import update as up

    def _must_not_run(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("the update service must not run on open or poll")

    monkeypatch.setattr(up, "run_update", _must_not_run)
    # m -> menu; '8' -> account; '3' -> update; two repaints; m back; q
    keys = [ord("m"), ord("8"), -1, ord("3"), -1, -1, ord("m"), -1]
    stdscr = _drive(_repo(), _config(), keys, monkeypatch, tmp_path)
    painted = [call[2] for call in stdscr.calls]
    assert any("account / update" in t for t in painted)
    assert any("ARMED" in t for t in painted)
    assert len(checks) == 1  # ONE check served the entry and every repaint


def test_run_live_update_enter_runs_the_gate_at_the_terminal_and_a_refusal_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Enter dispatches through the suspend/restore seam (curses suspended around the
    typed gate and the streamed run); with no TTY the gate fails closed, the service
    is handed a refusing confirm_gate, NOTHING moves, and the honest result is held."""
    plan = _fake_plan(tmp_path)
    from keel.commands import update as up

    gate_answers: list[bool] = []
    monkeypatch.setattr("keel.commands._common._is_interactive", lambda: False)

    def _spy_run(plan_arg: Any, *, echo: Any, confirm_gate: Any) -> Any:
        gate_answers.append(confirm_gate())
        return up.UpdateResult(
            ok=False,
            steps=("gate asked and refused",),
            error="confirmation not given -- nothing was changed.",
            rolled_back=False,
            backups=(),
        )

    monkeypatch.setattr(up, "run_update", _spy_run)
    monkeypatch.setattr(ac, "update_check", lambda **_kwargs: plan)
    relaunched: list[Any] = []

    def _no_relaunch(venv_python: Any, original_argv: Any) -> Any:
        def _closure() -> None:
            relaunched.append((venv_python, original_argv))

        return _closure

    monkeypatch.setattr(up, "relaunch_tui", _no_relaunch)
    # m -> menu; '8' -> account; '3' -> update; poll; Enter -> the terminal run; q
    keys = [ord("m"), ord("8"), -1, ord("3"), -1, ord("\r"), -1]
    stdscr = _drive(_repo(), _config(), keys, monkeypatch, tmp_path)
    painted = [call[2] for call in stdscr.calls]
    # the gate was asked THROUGH the service's confirm seam and failed closed
    assert gate_answers == [False]
    assert any("nothing was changed" in t for t in painted)
    assert relaunched == []  # a refused gate never relaunches


def test_run_live_update_success_relaunches_the_console_on_the_new_build(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """A verified success relaunches: the execv closure is built from the plan's venv
    and the ORIGINAL TUI argv, and is called once -- the console replaces itself
    rather than leaving the operator on a replaced binary."""
    plan = _fake_plan(tmp_path)
    from keel.commands import update as up

    monkeypatch.setattr("keel.commands._common._is_interactive", lambda: True)
    monkeypatch.setattr(
        "keel.commands._common._require_interactive_confirmation",
        lambda action, detail: None,
    )
    result = up.UpdateResult(ok=True, steps=("verified",), error=None,
                             rolled_back=False, backups=())
    relaunched: list[Any] = []

    def _recording_relaunch(venv_python: Any, original_argv: Any) -> Any:
        def _closure() -> None:
            relaunched.append((venv_python, list(original_argv)))

        return _closure

    monkeypatch.setattr(up, "relaunch_tui", _recording_relaunch)
    stdscr = _drive_update(
        [ord("m"), ord("8"), -1, ord("3"), -1, ord("\r"), -1],
        monkeypatch,
        tmp_path,
        plan=plan,
        run_result=result,
        relaunch_calls=relaunched,
    )
    painted = [call[2] for call in stdscr.calls]
    assert relaunched and relaunched[0][0] == tmp_path / ".venv/bin/python"
    assert relaunched[0][1] and relaunched[0][1][0]  # the original argv is carried
    assert any("updated" in t.lower() or "0.7.0" in t for t in painted)


def test_run_live_a_failed_relaunch_is_held_and_enter_retries_only_the_relaunch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """A verified update whose execv RAISES: the result is HELD (never a silent re-ARM
    of the whole update), the manual `keel tui` start is on screen, and a second Enter
    retries ONLY the relaunch -- the service is not re-entered, nothing re-installs."""
    plan = _fake_plan(tmp_path)
    from keel.commands import update as up

    result = up.UpdateResult(ok=True, steps=("verified",), error=None,
                             rolled_back=False, backups=())
    service_runs: list[int] = []

    def _fake_run(a_plan: Any, *, echo: Any, confirm_gate: Any) -> up.UpdateResult:
        service_runs.append(1)
        echo("the service streamed a line")
        return result

    monkeypatch.setattr(up, "run_update", _fake_run)
    monkeypatch.setattr(ac, "update_check", lambda **_kwargs: plan)
    relaunch_attempts: list[int] = []

    def _failing_relaunch(venv_python: Any, original_argv: Any) -> Any:
        def _closure() -> None:
            relaunch_attempts.append(1)
            raise up.UpdateError(
                "relaunch failed ([Errno 13] permission denied): execv could not start "
                "the new build -- start the console by hand: `keel tui`"
            )

        return _closure

    monkeypatch.setattr(up, "relaunch_tui", _failing_relaunch)
    # m -> menu; '8' -> account; '3' -> update; poll; Enter -> the run (relaunch
    # FAILS, result held); poll; Enter -> the relaunch RETRY (fails again, held)
    keys = [ord("m"), ord("8"), -1, ord("3"), -1, ord("\r"), -1, ord("\r"), -1]
    stdscr = _drive(_repo(), _config(), keys, monkeypatch, tmp_path)
    painted = [call[2] for call in stdscr.calls]
    assert len(service_runs) == 1  # the update ran ONCE -- Enter did not re-run it
    assert len(relaunch_attempts) == 2  # failed once, retried once by the second Enter
    assert any("RELAUNCH FAILED" in t for t in painted)
    assert any("keel tui" in t for t in painted)
    assert any("re-installs nothing" in t for t in painted)
