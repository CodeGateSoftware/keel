"""Tests for `keel.commands.data_console` -- the Data menu (issue #391 C5; PRD §3's Data
branch).

Five entries, all pinned here:

* **fetch** -- an ARMED view that shows the PLAN (the products × granularities × window
  the ACTIVE profile's config resolves to, and the db it warms) before anything runs;
  Enter dispatches to `keel.commands.fetch.run_fetch` -- the SAME flow `keel fetch` runs
  -- blocking like the CLI with the progress lines the CLI would have streamed HELD and
  rendered, and the run's failure kept above nothing (an error never discards them).
* **fetch --check** -- its own ARMED entry: never opens a network connection, and the
  exit verdict (the service's own `error` message) renders.
* **repair gaps** -- an ARMED confirm (it re-requests windows from the venue), then the
  service run with the per-series outcomes rendered.
* **freshness overview** -- OFFLINE: the current assessment (`run_fetch(check=True)`,
  the same sweep the CLI prints; a broker is never constructed), rebuilt per poll.
* **db import** -- a path form through the CLI's OWN import service (`import_dir`) and
  output lines, with the CLI's own DIR_PATH validation errors surfaced verbatim.

Mirrors `tests/commands/test_trading_console.py`'s fixture style.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from typing import Any

import pytest

from keel.commands import data_console as dc
from keel.commands.fetch import FetchResult
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
from keel.types import Candle, Granularity

NOW_TS = 1_800_000_000
DAY = 86_400


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
            granularities=[Granularity.ONE_HOUR, Granularity.ONE_DAY], history_days=365
        ),
        auto_trade=AutoTradeConfig(mode="paper", interval_sec=900),
        money_mgmt=MoneyMgmtConfig(
            max_total_dd_pct=Decimal("0.20"), max_weekly_dd_pct=Decimal("0.08")
        ),
        dca=DcaConfig(budget_usd=Decimal("50"), cadence_days=7),
    )
    base.update(overrides)
    return Config(**base)


def _prompt(answers: list[str]) -> Any:
    queue = iter(answers)
    asked: list[str] = []

    def fn(text: str) -> str:
        asked.append(text)
        return next(queue)

    fn.asked = asked
    return fn


def _daily_candles(n: int, *, start: int = NOW_TS - 40 * DAY) -> list[Candle]:
    return [
        Candle(
            ts=start + i * DAY,
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100"),
            volume=Decimal("10"),
        )
        for i in range(n)
    ]


# -- the sub-menu (PRD §3's Data branch) -----------------------------------------------------------


def test_the_data_menu_is_the_prd_data_branch() -> None:
    assert [entry.label for entry in dc.DATA_MENU] == [
        "fetch",
        "fetch --check",
        "repair gaps",
        "freshness overview",
        "db import",
    ]


def test_the_menu_screen_renders_every_entry_and_the_keys() -> None:
    lines = dc.build_data_menu_lines(cursor=0)
    texts = [line.text for line in lines]
    for entry in dc.DATA_MENU:
        assert any(entry.label in t for t in texts), entry.label
    assert any("up/k down/j move" in t for t in texts)
    marked = [t for t in texts if t.lstrip().startswith(">")]
    assert len(marked) == 1 and "fetch" in marked[0]


def test_the_data_screens_fit_the_80_column_clip() -> None:
    plan = dc.fetch_plan(_config(), "keel.db", "fetch")
    screens = [
        dc.build_data_menu_lines(cursor=2),
        dc.build_fetch_armed_lines(plan),
        dc.build_fetch_armed_lines(dc.fetch_plan(_config(), "keel.db", "fetch-check")),
        dc.build_fetch_armed_lines(dc.fetch_plan(_config(), "keel.db", "repair-gaps")),
        dc.build_fetch_result_lines(
            "fetch",
            (
                "data cached in: keel.db",
                "  ok       BTC-USD     ONE_DAY   n=40      0 bars behind, 0 internal gaps",
            ),
            error=None,
            verdict=None,
        ),
        dc.build_freshness_lines(
            ("  MISSING  ETH-USD     ONE_HOUR  n=0       nothing cached",)
        ),
    ]
    for screen in screens:
        for line in screen:
            assert len(line.text) <= 80, line.text


# -- fetch: the ARMED plan and the run -------------------------------------------------------------


def test_the_fetch_plan_names_products_granularities_window_and_db() -> None:
    plan = dc.fetch_plan(_config(), "keel.db", "fetch")
    assert plan.products == ("BTC-USD", "ETH-USD")  # the allowlist, in quote currency
    assert plan.granularities == (Granularity.ONE_HOUR, Granularity.ONE_DAY)
    assert plan.years == 5  # `keel fetch --years`'s own default
    assert plan.db_path == "keel.db"
    assert plan.check is False
    assert plan.repair_gaps is False


def test_the_armed_fetch_screen_shows_the_plan_before_anything_runs() -> None:
    plan = dc.fetch_plan(_config(), "keel.db", "fetch")
    joined = "\n".join(line.text for line in dc.build_fetch_armed_lines(plan))
    assert "ARMED" in joined
    assert "BTC-USD" in joined and "ETH-USD" in joined
    assert "ONE_HOUR" in joined and "ONE_DAY" in joined
    assert "keel.db" in joined
    assert "5y" in joined or "5 y" in joined or "years" in joined.lower()


def test_the_armed_check_screen_says_it_never_touches_the_network() -> None:
    import re

    plan = dc.fetch_plan(_config(), "keel.db", "fetch-check")
    joined = "\n".join(line.text for line in dc.build_fetch_armed_lines(plan))
    assert "ARMED" in joined
    collapsed = re.sub(r"\s+", " ", joined.lower())
    assert "never opens a network connection" in collapsed


def test_the_armed_repair_screen_says_it_re_requests_windows() -> None:
    plan = dc.fetch_plan(_config(), "keel.db", "repair-gaps")
    joined = "\n".join(line.text for line in dc.build_fetch_armed_lines(plan))
    assert "ARMED" in joined
    assert "re-request" in joined.lower() or "re-fetch" in joined.lower() or "window" in joined


def test_run_console_fetch_dispatches_to_the_services_flow(
    repo: Repository,
) -> None:
    """THE dispatch: `run_fetch` itself, with the CLI's own defaults (years=5, the
    config's granularities, the default tolerance) and a LAZY broker factory -- `--check`
    and the all-current skip must never construct one, exactly as the CLI's wrapper does."""
    calls: list[dict[str, Any]] = []

    def spy_run(*args: Any, **kwargs: Any) -> FetchResult:
        calls.append(kwargs)
        return FetchResult()

    plan = dc.fetch_plan(_config(), "keel.db", "fetch")
    dc.run_console_fetch(
        repo,
        _config(),
        plan,
        now_ts=NOW_TS,
        build_client=lambda: "the-broker",
        run_fn=spy_run,
    )
    assert len(calls) == 1
    sent = calls[0]
    assert sent["products"] == ["BTC-USD", "ETH-USD"]
    assert sent["years"] == 5
    assert sent["check"] is False
    assert sent["repair_gaps"] is False
    assert sent["now_ts"] == NOW_TS
    assert sent["db_path"] == "keel.db"
    # the progress lines the service streams are collected for the held results screen
    progress: list[str] = []
    dc.run_console_fetch(
        repo,
        _config(),
        plan,
        now_ts=NOW_TS,
        build_client=lambda: "the-broker",
        run_fn=lambda *a, **k: (calls.append(k), progress.append("data cached in: keel.db"))
        and FetchResult(),
    )
    assert progress == ["data cached in: keel.db"]


def test_the_fetch_results_hold_the_progress_lines_and_render_them() -> None:
    lines = dc.build_fetch_result_lines(
        "fetch",
        (
            "data cached in: keel.db",
            "",
            "fetching...",
            "",
            "after fetch:",
            "  ok       BTC-USD     ONE_DAY   n=40      0 bars behind, 0 internal gaps",
        ),
        error=None,
        verdict=None,
    )
    texts = [line.text for line in lines]
    assert "data cached in: keel.db" in texts
    # the freshness rows render VERBATIM -- their column alignment is part of what the
    # CLI prints, not decoration to re-indent
    assert (
        "  ok       BTC-USD     ONE_DAY   n=40      0 bars behind, 0 internal gaps" in texts
    )


def test_a_failed_fetch_keeps_its_progress_above_the_error() -> None:
    lines = dc.build_fetch_result_lines(
        "fetch",
        ("data cached in: keel.db", "fetching..."),
        error="connection reset",
        verdict=None,
    )
    texts = [line.text for line in lines]
    error_idx = next(i for i, t in enumerate(texts) if "fetch failed" in t)
    assert any("data cached in" in t for t in texts[:error_idx])


def test_the_check_verdict_renders_the_services_own_error() -> None:
    lines = dc.build_fetch_result_lines(
        "fetch-check",
        ("data cached in: keel.db", "  MISSING  BTC-USD     ONE_HOUR  n=0       nothing cached"),
        error=None,
        verdict="2 series missing or stale",
    )
    joined = "\n".join(line.text for line in lines)
    assert "2 series missing or stale" in joined
    # the failing verdict is loud, the passing one is calm
    passing = dc.build_fetch_result_lines(
        "fetch-check", ("data cached in: keel.db",), error=None, verdict=None
    )
    assert not any("fetch failed" in line.text for line in passing)


def test_the_check_verdict_footer_is_pinned_outside_the_scroll() -> None:
    """The check verdict is the load-bearing fact of a check run: pinned under the body
    (`compliance_console.pinned_frame` reserves it), so no scroll offset hides it."""
    failing = dc.check_verdict_footer("2 series missing or stale")
    assert any("2 series missing or stale" in line.text for line in failing)
    assert any(line.style == "alert" for line in failing)
    passing = dc.check_verdict_footer(None)
    assert passing == []  # a passing run's verdict already rides the service's own lines


# -- the freshness overview: offline, the current assessment ---------------------------------------


def test_the_freshness_overview_renders_the_current_assessment_offline(
    repo: Repository,
) -> None:
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _daily_candles(40))
    lines = dc.build_freshness_lines(dc.freshness_lines(repo, _config(), "keel.db", NOW_TS))
    joined = "\n".join(line.text for line in lines)
    assert "BTC-USD" in joined
    assert "ONE_DAY" in joined
    # the empty series render too -- the sweep judges every (product, granularity)
    assert "ETH-USD" in joined
    assert "MISSING" in joined or "STALE" in joined


def test_the_freshness_read_never_constructs_a_broker(repo: Repository) -> None:
    def _no_broker() -> Any:
        raise AssertionError("the freshness overview must never construct a broker")

    lines = dc.freshness_lines(repo, _config(), "keel.db", NOW_TS, build_client=_no_broker)
    assert lines  # the sweep ran and answered without the factory ever being called


# -- db import: the path form ----------------------------------------------------------------------


def _csv_dir(tmp_path: Any) -> Any:
    directory = tmp_path / "transactions"
    directory.mkdir()
    (directory / "coinbase.csv").write_text(
        "ID,Timestamp,Transaction Type,Asset,Quantity Transacted,Price Currency,"
        "Price at Transaction,Subtotal,Total (inclusive of fees and/or Spread),"
        "Fees and/or Spread,Notes\n"
        "TX1,2023-01-01 10:00:00 UTC,Buy,BTC,0.5,USD,20000,10000,10020,20,\n"
    )
    return directory


def test_db_import_runs_the_services_import_and_renders_the_clis_output(
    repo: Repository, tmp_path: Any
) -> None:
    result = dc.run_db_import_form(repo, _prompt([str(_csv_dir(tmp_path))]))
    assert result.startswith("imported=1 skipped=0")
    assert len(repo.get_transactions()) == 1


def test_db_import_surfaces_the_warnings_the_service_records(
    repo: Repository, tmp_path: Any
) -> None:
    directory = tmp_path / "exports"
    directory.mkdir()
    (directory / "unknown.csv").write_text("some,unrelated,header\n1,2,3\n")
    result = dc.run_db_import_form(repo, _prompt([str(directory)]))
    assert "imported=0" in result
    assert "warning:" in result
    assert "could not detect a known CSV header shape" in result


def test_db_import_validation_errors_surface_verbatim(
    repo: Repository, tmp_path: Any
) -> None:
    """A bad path is refused with the CLI's OWN message -- pinned byte-for-byte against
    what `keel db import <bad-path>` actually prints, so the two front-ends can never
    grow two wordings for one refusal."""
    from click.testing import CliRunner

    import keel.cli as cli_module

    spy = _RecordingRepo(repo)
    bad = str(tmp_path / "no-such-dir")
    result = dc.run_db_import_form(spy, _prompt([bad]))
    assert spy.calls == []  # the import service was never handed a nonexistent dir
    assert "does not exist" in result

    cli_out = CliRunner().invoke(cli_module.cli, ["db", "import", bad]).output
    cli_error_line = next(line for line in cli_out.splitlines() if line.startswith("Error:"))
    # the console's refusal IS the CLI's refusal, byte for byte
    assert result == cli_error_line


def test_db_import_cancels_on_an_empty_path(repo: Repository) -> None:
    spy = _RecordingRepo(repo)
    result = dc.run_db_import_form(spy, _prompt([""]))
    assert spy.calls == []
    assert "cancelled" in result.lower()


class _RecordingRepo:
    """A real `Repository` wrapped so every WRITE is recorded -- proves a refused import
    never reaches the service. Reads fall through."""

    def __init__(self, inner: Repository) -> None:
        self._inner = inner
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def upsert_transaction(self, tx: dict[str, Any]) -> None:
        self.calls.append(("upsert_transaction", {"tx": tx}))
        self._inner.upsert_transaction(tx)


# -- the loop wiring (fake curses): ARMED gating ---------------------------------------------------


def _fake_curses_mod(monkeypatch: pytest.MonkeyPatch, stdscr: Any) -> Any:
    from tests.commands.test_tui import _fake_curses

    fake = _fake_curses()
    fake.wrapper = lambda fn: fn(stdscr)
    monkeypatch.setitem(sys.modules, "curses", fake)
    return fake


def _binding(repo: Repository, config: Config) -> Any:
    import click

    from keel.commands.console import ConsoleBinding

    ctx = click.Context(
        click.Command("tui"), obj={"config_path": "config.yaml", "db_path": "keel.db"}
    )
    binding = ConsoleBinding(ctx, config_path="config.yaml", db_path="keel.db")
    binding.open_state = lambda: (repo, config)  # type: ignore[method-assign]
    return binding


def _drive(
    repo: Repository,
    config: Config,
    keys: list[int],
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> Any:
    from keel.commands import tui as tui_mod
    from tests.commands.test_tui import _KeySequenceStdscr

    stdscr = _KeySequenceStdscr(height=30, width=120, keys=keys)
    if monkeypatch is not None:
        _fake_curses_mod(monkeypatch, stdscr)
    binding = _binding(repo, config)
    tui_mod.run_live(binding.open_state, lambda: NOW_TS, interval=0.01, console_binding=binding)
    return stdscr


def test_run_live_data_menu_opens_from_the_console_menu_and_esc_steps_back(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    stdscr = _drive(repo, _config(), [ord("m"), ord("6"), -1, 27, -1, 27], monkeypatch)
    painted = [call[2] for call in stdscr.calls]
    data_idx = next(i for i, t in enumerate(painted) if "keel console -- data" in t)
    data_text = "\n".join(painted[data_idx:])
    for label in ("fetch", "fetch --check", "repair gaps", "freshness overview", "db import"):
        assert label in data_text, label
    assert any("keel console -- menu" in t for t in painted[data_idx:])


def test_run_live_fetch_is_armed_until_enter_and_enter_runs_exactly_one(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE ARMED gate: opening Data -> fetch, polling, and closing fire NOTHING; Enter
    fires EXACTLY ONE service run; the progress lines it streamed are held and painted."""
    runs: list[dict[str, Any]] = []

    def spy_run(*args: Any, **kwargs: Any) -> FetchResult:
        runs.append(kwargs)
        sink = kwargs["progress"].append  # what the dispatch wires as the echo stream
        sink("data cached in: keel.db")
        sink("fetching...")
        sink("after fetch:")
        return FetchResult()

    monkeypatch.setattr(dc, "run_console_fetch", spy_run)

    # m; 6 -> Data; 1 -> fetch (ARMED); poll; Esc; q -- nothing ran.
    stdscr = _drive(
        repo, _config(), [ord("m"), ord("6"), ord("1"), -1, 27], monkeypatch
    )
    assert runs == []
    assert any("ARMED" in call[2] for call in stdscr.calls)

    # ...and Enter runs exactly one fetch, holding the progress lines.
    stdscr = _drive(
        repo, _config(), [ord("m"), ord("6"), ord("1"), 10, -1, 27], monkeypatch
    )
    assert len(runs) == 1
    painted = "\n".join(call[2] for call in stdscr.calls)
    assert "data cached in: keel.db" in painted
    assert "after fetch:" in painted


def test_run_live_check_entry_runs_the_check_and_renders_the_verdict(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    def spy_run(*args: Any, **kwargs: Any) -> FetchResult:
        sink = kwargs["progress"].append  # what the dispatch wires as the echo stream
        sink("data cached in: keel.db")
        assert args[2].check is True  # the plan the loop built for THIS entry
        return FetchResult(error="2 series missing or stale")

    monkeypatch.setattr(dc, "run_console_fetch", spy_run)
    # m; 6 -> Data; 2 -> fetch --check; Enter RUNS; poll; Esc; q.
    stdscr = _drive(
        repo, _config(), [ord("m"), ord("6"), ord("2"), 10, -1, 27], monkeypatch
    )
    painted = "\n".join(call[2] for call in stdscr.calls)
    assert "2 series missing or stale" in painted


def test_run_live_repair_gaps_confirms_then_renders_per_series_outcomes(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    def spy_run(*args: Any, **kwargs: Any) -> FetchResult:
        assert args[2].repair_gaps is True  # the plan the loop built for THIS entry
        sink = kwargs["progress"].append  # what the dispatch wires as the echo stream
        sink("repairing interior gaps...")
        sink(
            "  BTC-USD     ONE_HOUR  windows=2 probed=2 skipped=0 recovered=40 "
            "absent_at_source=0"
        )
        return FetchResult()

    monkeypatch.setattr(dc, "run_console_fetch", spy_run)
    # m; 6 -> Data; 3 -> repair gaps (ARMED); Enter RUNS; poll; Esc; q.
    stdscr = _drive(
        repo, _config(), [ord("m"), ord("6"), ord("3"), 10, -1, 27], monkeypatch
    )
    painted = "\n".join(call[2] for call in stdscr.calls)
    assert "repairing interior gaps..." in painted
    assert "recovered=40" in painted


def test_run_live_the_freshness_overview_is_offline_and_per_poll(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The freshness view reads the CURRENT assessment each poll and never constructs a
    broker -- the offline views' contract, proven end-to-end through the loop."""
    monkeypatch.setattr(
        "keel.commands._common._build_broker",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("the freshness view must never construct a broker")
        ),
    )
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _daily_candles(40))
    # m; 6 -> Data; 4 -> freshness overview; poll; Esc; q.
    stdscr = _drive(
        repo, _config(), [ord("m"), ord("6"), ord("4"), -1, 27], monkeypatch
    )
    painted = "\n".join(call[2] for call in stdscr.calls)
    assert "BTC-USD" in painted
    assert "ONE_DAY" in painted


def test_run_live_db_import_form_runs_at_the_terminal(
    repo: Repository, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = _csv_dir(tmp_path)
    import click

    answers = iter([str(directory)])
    monkeypatch.setattr(
        click, "prompt", lambda text, **kw: next(answers), raising=True
    )
    # m; 6 -> Data; 5 -> db import (the form runs at the terminal); poll; q.
    stdscr = _drive(
        repo, _config(), [ord("m"), ord("6"), ord("5"), -1, ord("q")], monkeypatch
    )
    painted = "\n".join(call[2] for call in stdscr.calls)
    assert "imported=1 skipped=0" in painted
    assert len(repo.get_transactions()) == 1
    fake_curses = sys.modules["curses"]
    assert "def_prog_mode" in fake_curses.calls
    assert "reset_prog_mode" in fake_curses.calls


def test_run_live_the_data_menu_scrolls_banner_aware(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.commands.test_tui import _fake_curses

    fake = _fake_curses()
    # m; 6 -> Data; End; poll; q.
    stdscr = _drive(
        repo, _config(), [ord("m"), ord("6"), fake.KEY_END, -1, ord("q")], monkeypatch
    )
    painted = [call[2] for call in stdscr.calls]
    data_idx = next(i for i, t in enumerate(painted) if "keel console -- data" in t)
    assert any("db import" in t for t in painted[data_idx:])
