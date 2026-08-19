"""Tests for `keel.commands.trading_console` -- the Trading menu (issue #391 C5; PRD §3's
Trading branch, O3's typed contracts).

Six surfaces, all pinned here:

* **The sub-menu** -- PRD §3's Trading branch in tree order: agent cycle (ARMED confirm),
  monitor poll (ARMED), autonomy, record-flow [typed], reset-hwm [typed],
  resume-entries [typed], kill (one-key, per its own CLI contract), resume [typed].
* **The agent cycle** -- the ARMED view is the confirm step: which profile, what
  paper/confirm-mode semantics mean, the autonomy state, and the SESSION HONESTY line
  (a session-bound CLOSED venue says the cycle will skip with `market_closed`; the
  clock-unavailable case gets its own line) -- sourced from the RECORDED session state,
  never a TUI-side calendar. Enter runs `agent.run_once` through `run_agent_cycle` with
  the CLI's own confirm gate; the cycle's rendered result (`render_loop_result`) is held.
* **The monitor poll** -- one poll, ARMED, dispatched through `monitor_cycle`.
* **The typed forms** -- record-flow (the CLI's own validation messages and typed gate),
  reset-hwm, resume-entries, resume: every gate is the CLI's OWN
  `_require_interactive_confirmation` with the CLI's OWN wording (the constants'
  single home is `keel/commands/trading.py`, pinned here), and a declined gate means
  not a single state row is written -- spy-proven, and for resume/resume-entries
  end-to-end through the live loop like C3's withdrawals proof.
* **kill** -- the CLI's own asymmetry: ENGAGING is one key with NO ceremony (never
  typed); the console mirrors that exactly -- selecting the entry dispatches
  `engage_kill_switch` and toasts the CLI's own line.
* **autonomy** -- the CLI's own semantics: OFF->ON behind the CLI's typed arm gate
  (extracted from `autonomy_on`'s body, byte-identical wording), ON->OFF ungated.

Mirrors `tests/commands/test_strategy_console.py`'s fixture style.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from typing import Any

import pytest

from keel import agent
from keel.commands import trading_console as tc
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
            granularities=[Granularity.ONE_HOUR], history_days=365
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


class _RecordingRepo:
    """A real `Repository` wrapped so every WRITE is recorded with its exact arguments --
    the spy the "a declined gate writes nothing" proofs read. Reads fall through."""

    def __init__(self, inner: Repository) -> None:
        self._inner = inner
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def set_state(self, key: str, value: Any) -> None:
        self.calls.append(("set_state", {"key": key, "value": value}))
        self._inner.set_state(key, value)

    def set_autonomous(self, value: bool, now_ts: int, **kwargs: Any) -> None:
        self.calls.append(
            ("set_autonomous", {"value": value, "now_ts": now_ts, **kwargs})
        )
        self._inner.set_autonomous(value, now_ts, **kwargs)


def _recorded_session(
    state: str, *, recorded_ts: int = NOW_TS, interval_sec: int = 900
) -> agent.RecordedSession:
    """A FRESH recorded session in the given state -- the B1 recording the plan reads."""
    return agent.RecordedSession(
        venue="alpaca",
        state=state,
        recorded_ts=recorded_ts,
        interval_sec=interval_sec,
        next_open_ts=None,
        next_close_ts=None,
        fresh=True,
    )


# -- the sub-menu (PRD §3's Trading branch) --------------------------------------------------------


def test_the_trading_menu_is_the_prd_trading_branch() -> None:
    assert [entry.label for entry in tc.TRADING_MENU] == [
        "agent cycle (single)",
        "monitor poll (single)",
        "autonomy",
        "record-flow",
        "reset-hwm",
        "resume-entries",
        "kill",
        "resume",
    ]


def test_the_prd_marks_resume_entries_and_resume_as_typed_and_the_menu_says_so() -> None:
    typed = {entry.label for entry in tc.TRADING_MENU if entry.typed}
    # The CLI's own typed halt-releasers (tests/test_cli.py's `_HALT_COMMANDS`), plus
    # autonomy's ON direction -- the PRD tree marks resume-entries/resume "(typed)".
    assert typed == {"record-flow", "reset-hwm", "resume-entries", "resume", "autonomy"}


def test_the_menu_screen_renders_every_entry_and_the_keys() -> None:
    lines = tc.build_trading_menu_lines(cursor=0)
    texts = [line.text for line in lines]
    for entry in tc.TRADING_MENU:
        assert any(entry.label in t for t in texts), entry.label
    assert any("up/k down/j move" in t for t in texts)
    assert any("q/Esc/m to the console menu" in t for t in texts)
    # exactly one cursor-marked row
    marked = [t for t in texts if t.lstrip().startswith(">")]
    assert len(marked) == 1 and "agent cycle" in marked[0]


def test_the_trading_screens_fit_the_80_column_clip() -> None:
    """Every screen the Trading menu renders stays inside the 80-column budget `_paint`
    clips at -- wrapped by the builders, never clipped mid-fact."""
    plan = tc.CyclePlan(
        db_path="keel.db",
        profile_label="paper-forward",
        mode="paper",
        autonomous=False,
        session_line=None,
    )
    live_plan = tc.CyclePlan(
        db_path="keel-live.db",
        profile_label="LIVE",
        mode="confirm",
        autonomous=True,
        session_line=tc.session_honesty_line(True, _recorded_session("closed")),
    )
    result = agent.LoopResult(
        ts=NOW_TS,
        skipped=False,
        skip_reason=None,
        mode="paper",
        polled=12,
        products=["BTC-USD", "ETH-USD"],
        stale_products=[],
        paper_equity=Decimal("12345.67"),
        drawdown_total_pct=Decimal("0.01"),
        drawdown_weekly_pct=Decimal("0.0"),
    )
    monitor_plan = tc.MonitorPlan(
        db_path="keel.db",
        products=("BTC-USD", "ETH-USD"),
        granularities=(Granularity.ONE_HOUR,),
        interval_sec=900.0,
        session_line=None,
    )
    screens = [
        tc.build_trading_menu_lines(cursor=3),
        tc.build_cycle_armed_lines(plan),
        tc.build_cycle_armed_lines(live_plan),
        tc.build_cycle_result_lines(result),
        tc.build_monitor_armed_lines(monitor_plan),
        tc.build_monitor_result_lines(_fake_monitor_cycle()),
    ]
    for screen in screens:
        for line in screen:
            assert len(line.text) <= 80, line.text


def test_every_blocking_screen_discloses_what_ctrl_c_does() -> None:
    """[review #405] A frozen screen is exactly where an operator reaches for Ctrl-C,
    so every blocking surface -- ARMED and held-result, cycle and poll -- must state
    plainly, BEFORE the run, what it does: the whole console exits (gracefully) and
    any held results are discarded."""
    plan = tc.CyclePlan(
        db_path="keel.db",
        profile_label="paper-forward",
        mode="paper",
        autonomous=False,
        session_line=None,
    )
    monitor_plan = tc.MonitorPlan(
        db_path="keel.db",
        products=("BTC-USD",),
        granularities=(Granularity.ONE_HOUR,),
        interval_sec=900.0,
        session_line=None,
    )
    result = agent.LoopResult(
        ts=NOW_TS, skipped=True, skip_reason="market_closed", mode=None, polled=0
    )
    screens = [
        tc.build_cycle_armed_lines(plan),
        tc.build_cycle_result_lines(result),
        tc.build_monitor_armed_lines(monitor_plan),
        tc.build_monitor_result_lines(_fake_monitor_cycle()),
    ]
    for screen in screens:
        joined = "\n".join(line.text for line in screen)
        assert "Ctrl-C" in joined
        assert "exits the whole console" in joined
        assert "discards" in joined


def test_the_armed_footers_state_all_three_close_keys() -> None:
    """[review #405] `m` is bound on every ARMED screen (the loop's close set is
    q/Esc/m) -- the footer must say so, matching the result footers, or the binding is
    under-documented on the one screen that waits on it."""
    cycle_plan = tc.CyclePlan(
        db_path="keel.db",
        profile_label="paper-forward",
        mode="paper",
        autonomous=False,
        session_line=None,
    )
    monitor_plan = tc.MonitorPlan(
        db_path="keel.db",
        products=("BTC-USD",),
        granularities=(Granularity.ONE_HOUR,),
        interval_sec=900.0,
        session_line=None,
    )
    for screen in (
        tc.build_cycle_armed_lines(cycle_plan),
        tc.build_monitor_armed_lines(monitor_plan),
    ):
        joined = "\n".join(line.text for line in screen)
        assert "q/Esc/m" in joined
        assert "Press q or Esc" not in joined


def _fake_monitor_cycle() -> Any:
    from keel.commands.monitor import MonitorCycle

    return MonitorCycle(
        line=f"[{NOW_TS}] polled 3 new candle row(s) across ['BTC-USD']",
        session=None,
        session_bound=False,
        written=3,
    )


# -- the session honesty line (display of B1 semantics, no new logic) ------------------------------


def test_a_session_bound_closed_venue_says_the_cycle_will_skip_with_market_closed() -> None:
    line = tc.session_honesty_line(True, _recorded_session("closed"))
    assert line is not None
    assert "market_closed" in line
    assert "CLOSED" in line


def test_the_clock_unavailable_case_gets_its_own_line() -> None:
    line = tc.session_honesty_line(True, _recorded_session("clock_unavailable"))
    assert line is not None
    assert "market_clock_unavailable" in line
    assert "market_closed" not in line.replace("market_clock_unavailable", "")


def test_a_247_venue_and_an_open_session_have_no_honesty_line() -> None:
    assert tc.session_honesty_line(False, None) is None  # 24/7: no session gate at all
    assert tc.session_honesty_line(True, _recorded_session("open")) is None


def test_a_stale_or_absent_record_names_the_fail_closed_skip() -> None:
    stale = agent.RecordedSession(
        venue="alpaca",
        state="open",
        recorded_ts=NOW_TS - 10 * 86400,
        interval_sec=900,
        next_open_ts=None,
        next_close_ts=None,
        fresh=False,
    )
    for recorded in (None, stale):
        line = tc.session_honesty_line(True, recorded)
        assert line is not None
        assert "market_clock_unavailable" in line


# -- the agent cycle: the ARMED confirm step -------------------------------------------------------


def test_the_cycle_plan_names_the_active_profile_and_mode_semantics(
    repo: Repository,
) -> None:
    repo.set_autonomous(False, NOW_TS)
    plan = tc.cycle_plan(
        repo,
        _config(),
        "keel.db",
        NOW_TS,
        profile_label="paper-forward",
        session_bound=False,
        recorded=None,
    )
    assert plan.mode == "paper"
    assert plan.autonomous is False
    assert plan.profile_label == "paper-forward"
    assert plan.db_path == "keel.db"
    assert plan.session_line is None


def test_the_armed_cycle_screen_confirms_the_profile_and_paper_semantics() -> None:
    plan = tc.CyclePlan(
        db_path="keel.db",
        profile_label="paper-forward",
        mode="paper",
        autonomous=False,
        session_line=None,
    )
    joined = "\n".join(line.text for line in tc.build_cycle_armed_lines(plan))
    assert "ARMED" in joined
    assert "paper-forward" in joined
    assert "keel.db" in joined
    assert "paper" in joined and "SIMULATED" in joined
    assert "REAL MONEY" not in joined


def test_the_armed_cycle_screen_says_live_unmistakably() -> None:
    plan = tc.CyclePlan(
        db_path="keel-live.db",
        profile_label="LIVE",
        mode="confirm",
        autonomous=False,
        session_line=None,
    )
    lines = tc.build_cycle_armed_lines(plan)
    joined = "\n".join(line.text for line in lines)
    assert "LIVE" in joined and "keel-live.db" in joined
    assert "REAL MONEY" in joined
    # and the REAL MONEY line carries the alert style -- unmistakable, not just present
    assert any("REAL MONEY" in line.text and line.style == "alert" for line in lines)


def test_the_armed_cycle_screen_names_the_autonomy_semantics_when_armed() -> None:
    plan = tc.CyclePlan(
        db_path="keel-live.db",
        profile_label="LIVE",
        mode="confirm",
        autonomous=True,
        session_line=None,
    )
    joined = "\n".join(line.text for line in tc.build_cycle_armed_lines(plan))
    assert "autonomy" in joined.lower()
    assert "NO further prompt" in joined or "without asking" in joined


def test_the_armed_cycle_screen_carries_the_session_honesty_line(repo: Repository) -> None:
    plan = tc.CyclePlan(
        db_path="keel.db",
        profile_label="paper-equities",
        mode="paper",
        autonomous=False,
        session_line=tc.session_honesty_line(True, _recorded_session("closed")),
    )
    joined = "\n".join(line.text for line in tc.build_cycle_armed_lines(plan))
    assert "market_closed" in joined


def test_run_agent_cycle_dispatches_to_run_once_with_the_clis_own_confirm_gate(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE dispatch: `agent.run_once` itself, with the SAME `_interactive_confirm`
    function `keel agent` hands it -- the console has no order path of its own, so an
    in-console cycle IS the agent pipeline (O3/issue #391's acceptance)."""
    from keel.commands.confirm import _interactive_confirm

    calls: list[dict[str, Any]] = []

    def spy_run_once(broker, repo_, config_, **kwargs: Any) -> agent.LoopResult:
        calls.append({"broker": broker, "confirm_fn": kwargs.get("confirm_fn")})
        return agent.LoopResult(
            ts=NOW_TS, skipped=False, skip_reason=None, mode="paper", polled=0
        )

    result = tc.run_agent_cycle(
        repo,
        _config(),
        now_ts=NOW_TS,
        build_broker=lambda: "the-broker",
        run_fn=spy_run_once,
    )
    assert calls == [{"broker": "the-broker", "confirm_fn": _interactive_confirm}]
    assert result.polled == 0


def test_the_cycle_result_renders_the_services_own_lines_verbatim() -> None:
    result = agent.LoopResult(
        ts=NOW_TS,
        skipped=False,
        skip_reason=None,
        mode="paper",
        polled=7,
        products=["BTC-USD"],
        stale_products=[],
        paper_equity=Decimal("999.99"),
        drawdown_total_pct=Decimal("0.02"),
        drawdown_weekly_pct=Decimal("0.0"),
    )
    lines = tc.build_cycle_result_lines(result)
    import re

    from keel.commands.trading import render_loop_result

    # a long cycle line WRAPS to the 80-column budget rather than clipping -- so the
    # verbatim pin compares whitespace-normalized: the CONTENT is byte-identical, only
    # the fold point may differ
    collapsed = re.sub(r"\s+", " ", "\n".join(line.text for line in lines))
    for line in render_loop_result(result):
        assert re.sub(r"\s+", " ", line) in collapsed


def test_the_cycle_result_renders_a_skips_reason_verbatim() -> None:
    result = agent.LoopResult(
        ts=NOW_TS, skipped=True, skip_reason="market_closed", mode=None, polled=0
    )
    texts = [line.text.strip() for line in tc.build_cycle_result_lines(result)]
    assert f"[{NOW_TS}] skipped: market_closed" in texts


def test_the_cycle_result_renders_the_blocked_entries_lines_the_cli_prints() -> None:
    blocked = agent.BlockedEntry(
        rule_name="tb",
        product="BTC-USD",
        granularity=Granularity.FIFTEEN_MINUTE,
        expected_ts=NOW_TS,
        stored_ts=NOW_TS - 900,
        reason="stale",
    )
    result = agent.LoopResult(
        ts=NOW_TS,
        skipped=False,
        skip_reason=None,
        mode="paper",
        polled=1,
        blocked_entries=[blocked],
    )
    joined = "\n".join(line.text for line in tc.build_cycle_result_lines(result))
    assert "blocked:" in joined
    assert "needs a confirmed" in joined


# -- the monitor poll: one poll, ARMED -------------------------------------------------------------


def test_the_monitor_plan_uses_the_config_the_cli_polls() -> None:
    config = _config()
    plan = tc.monitor_plan(config, "keel.db", session_bound=False, recorded=None)
    assert plan.products == ("BTC-USD", "ETH-USD")  # _default_sim_products(config)
    assert plan.granularities == (Granularity.ONE_HOUR,)
    assert plan.interval_sec == 900.0
    assert plan.db_path == "keel.db"


def test_the_armed_monitor_screen_says_what_one_poll_does() -> None:
    plan = tc.MonitorPlan(
        db_path="keel.db",
        products=("BTC-USD",),
        granularities=(Granularity.ONE_HOUR,),
        interval_sec=900.0,
        session_line=None,
    )
    joined = "\n".join(line.text for line in tc.build_monitor_armed_lines(plan))
    assert "ARMED" in joined
    assert "BTC-USD" in joined
    assert "ONE_HOUR" in joined or "1h" in joined or "3600" in joined
    # the poll's interval -- the cadence the session record trusts -- renders too
    # (review #405: a computed-never-rendered plan field is dead display state)
    assert "interval 900" in joined


def test_run_monitor_poll_dispatches_to_monitor_cycle(repo: Repository) -> None:
    from keel.commands.monitor import MonitorCycle

    calls: list[dict[str, Any]] = []

    def spy_cycle(broker, repo_, config_, products, granularities, now_ts, interval):
        calls.append(
            {
                "broker": broker,
                "products": products,
                "granularities": granularities,
                "now_ts": now_ts,
                "interval": interval,
            }
        )
        return MonitorCycle(
            line=f"[{now_ts}] polled 1 new candle row(s) across {products}",
            session=None,
            session_bound=False,
            written=1,
        )

    config = _config()
    cycle = tc.run_monitor_poll(
        repo,
        config,
        now_ts=NOW_TS,
        build_broker=lambda: "the-broker",
        cycle_fn=spy_cycle,
    )
    assert calls == [
        {
            "broker": "the-broker",
            "products": ["BTC-USD", "ETH-USD"],
            "granularities": [Granularity.ONE_HOUR],
            "now_ts": NOW_TS,
            "interval": 900.0,
        }
    ]
    assert "polled 1" in cycle.line


def test_the_monitor_result_renders_the_cycles_line_verbatim() -> None:
    lines = tc.build_monitor_result_lines(_fake_monitor_cycle())
    texts = [line.text.strip() for line in lines]
    assert f"[{NOW_TS}] polled 3 new candle row(s) across ['BTC-USD']" in texts


# -- kill: one key, NO ceremony (the CLI's own contract) -------------------------------------------


def test_kill_dispatches_immediately_and_toasts_the_clis_own_line(
    repo: Repository,
) -> None:
    repo.set_state("kill_switch", False)
    line = tc.run_kill(repo)
    assert line == "kill-switch ENGAGED: all trading halted."
    assert repo.get_state("kill_switch") is True


def test_the_kill_line_is_pinned_to_the_services_single_home(repo: Repository) -> None:
    """The line the console toasts IS the constant the CLI prints -- one home, two
    front-ends, no drift (the C3 withdrawals-wording fix, applied from the start)."""
    from keel.commands import trading

    assert tc.run_kill(repo) == trading.KILL_ENGAGED_LINE
    assert trading.KILL_ENGAGED_LINE == "kill-switch ENGAGED: all trading halted."


# -- the typed gates: the CLI's own wording, its single home ---------------------------------------


def test_the_resume_gate_is_the_clis_own(monkeypatch: pytest.MonkeyPatch) -> None:
    """`resume`'s typed gate must be `_require_interactive_confirmation` with the CLI's
    own action/detail wording -- pinned against the SHARED constants (`trading.py`,
    their one home) and the CLI's own call site -- and it fails CLOSED."""
    import inspect

    import click as click_mod

    import keel.cli as cli_module
    import keel.commands._common as common
    from keel.commands.trading import RESUME_ACTION, RESUME_DETAIL

    asked: list[tuple[str, str]] = []

    def refusing_gate(action: str, detail: str) -> None:
        asked.append((action, detail))
        raise click_mod.ClickException("aborted (confirmation not given).")

    monkeypatch.setattr(common, "_require_interactive_confirmation", refusing_gate)
    assert tc.clis_typed_resume_gate() is False

    def accepting_gate(action: str, detail: str) -> None:
        asked.append((action, detail))

    monkeypatch.setattr(common, "_require_interactive_confirmation", accepting_gate)
    assert tc.clis_typed_resume_gate() is True

    assert asked[0] == (RESUME_ACTION, RESUME_DETAIL)
    assert asked[0][0] == "disengage the kill-switch"
    # and the CLI command itself runs the gate on those same constants
    cli_source = inspect.getsource(cli_module.resume.callback)
    assert "RESUME_ACTION" in cli_source
    assert "RESUME_DETAIL" in cli_source


def test_the_resume_entries_gate_is_the_clis_own(monkeypatch: pytest.MonkeyPatch) -> None:
    import inspect

    import click as click_mod

    import keel.cli as cli_module
    import keel.commands._common as common
    from keel.commands.trading import (
        RESUME_ENTRIES_ACTION,
        RESUME_ENTRIES_DETAIL,
    )

    asked: list[tuple[str, str]] = []

    def gate(action: str, detail: str) -> None:
        asked.append((action, detail))
        raise click_mod.ClickException("aborted (confirmation not given).")

    monkeypatch.setattr(common, "_require_interactive_confirmation", gate)
    assert tc.clis_typed_resume_entries_gate() is False
    assert asked[0] == (RESUME_ENTRIES_ACTION, RESUME_ENTRIES_DETAIL)
    assert asked[0][0] == "clear the consecutive-loss halt (rail 16)"
    cli_source = inspect.getsource(cli_module.resume_entries.callback)
    assert "RESUME_ENTRIES_ACTION" in cli_source


def test_the_reset_hwm_gate_is_the_clis_own(monkeypatch: pytest.MonkeyPatch) -> None:
    import inspect

    import click as click_mod

    import keel.cli as cli_module
    import keel.commands._common as common
    from keel.commands.trading import RESET_HWM_ACTION, RESET_HWM_DETAIL

    asked: list[tuple[str, str]] = []

    def gate(action: str, detail: str) -> None:
        asked.append((action, detail))
        raise click_mod.ClickException("aborted (confirmation not given).")

    monkeypatch.setattr(common, "_require_interactive_confirmation", gate)
    assert tc.clis_typed_reset_hwm_gate() is False
    assert asked[0] == (RESET_HWM_ACTION, RESET_HWM_DETAIL)
    assert asked[0][0] == "reset rail 11's high-water mark"
    cli_source = inspect.getsource(cli_module.reset_hwm.callback)
    assert "RESET_HWM_ACTION" in cli_source


def test_the_record_flow_gate_carries_the_amount_in_its_action_phrase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import keel.commands._common as common
    from keel.commands.trading import RECORD_FLOW_DETAIL

    asked: list[tuple[str, str]] = []

    def gate(action: str, detail: str) -> None:
        asked.append((action, detail))

    monkeypatch.setattr(common, "_require_interactive_confirmation", gate)
    assert tc.clis_typed_record_flow_gate("-250") is True
    assert asked[0][0] == "rebase rail 11's high-water mark by -250"
    assert asked[0][1] == RECORD_FLOW_DETAIL


# -- the typed forms: a declined gate writes NOTHING -----------------------------------------------


def test_resume_form_refusal_writes_nothing_and_the_success_line_is_the_clis(
    repo: Repository,
) -> None:
    spy = _RecordingRepo(repo)
    result = tc.run_resume_form(spy, gate_fn=lambda: False)
    assert spy.calls == []
    assert "not given" in result or "cancelled" in result.lower()

    result = tc.run_resume_form(spy, gate_fn=lambda: True)
    assert ("set_state", {"key": "kill_switch", "value": False}) in spy.calls
    assert result == "kill-switch disengaged: trading resumed."


def test_resume_entries_form_refusal_writes_nothing_and_success_clears_rail_16(
    repo: Repository,
) -> None:
    spy = _RecordingRepo(repo)
    repo.set_state("streak_halt_until", 2_000_000_000)
    repo.set_state("consecutive_losses", 3)

    result = tc.run_resume_entries_form(spy, gate_fn=lambda: False)
    assert spy.calls == []
    assert repo.get_state("streak_halt_until") == 2_000_000_000
    assert "not given" in result or "cancelled" in result.lower()

    result = tc.run_resume_entries_form(spy, gate_fn=lambda: True)
    assert ("set_state", {"key": "streak_halt_until", "value": 0}) in spy.calls
    assert ("set_state", {"key": "consecutive_losses", "value": 0}) in spy.calls
    assert result == "consecutive-loss breaker cleared: new entries permitted."


def test_reset_hwm_form_refusal_writes_nothing(repo: Repository) -> None:
    spy = _RecordingRepo(repo)
    repo.set_state("equity_high_water_mark", Decimal("15000"))

    result = tc.run_reset_hwm_form(spy, gate_fn=lambda: False)
    assert spy.calls == []
    assert repo.get_state("equity_high_water_mark") == Decimal("15000")

    result = tc.run_reset_hwm_form(spy, gate_fn=lambda: True)
    assert repo.get_state("equity_high_water_mark") is None
    assert repo.get_state("drawdown_total_pct") == Decimal("0")
    assert result == (
        "equity high-water mark reset: it will re-seed from the next cycle's equity."
    )


def test_record_flow_form_gates_before_validating_and_writes_nothing_on_a_refusal() -> None:
    """The CLI's own ORDER: the typed gate (naming the raw amount) comes FIRST, then the
    validation, then the write -- so a declined gate means no parse, no write, nothing."""
    spy = _RecordingRepo(repo)
    gated: list[str] = []

    def decline(amount: str) -> bool:
        gated.append(amount)
        return False

    result = tc.run_record_flow_form(spy, _prompt(["nan"]), gate_fn=decline)
    assert gated == ["nan"]
    assert spy.calls == []
    assert "not given" in result or "cancelled" in result.lower()


def test_record_flow_form_surfaces_the_clis_own_validation_errors_verbatim(
    repo: Repository,
) -> None:
    from keel.commands.trading import parse_flow_amount

    spy = _RecordingRepo(repo)
    result = tc.run_record_flow_form(
        spy, _prompt(["abc"]), gate_fn=lambda _amount: True
    )
    assert spy.calls == []
    assert "--amount must be a number, got 'abc'" in result

    result = tc.run_record_flow_form(
        spy, _prompt(["nan"]), gate_fn=lambda _amount: True
    )
    assert spy.calls == []
    assert "--amount must be a finite number, got 'nan'" in result

    with pytest.raises(ValueError, match="must be a number"):
        parse_flow_amount("abc")
    with pytest.raises(ValueError, match="must be a finite number"):
        parse_flow_amount("inf")


def test_record_flow_form_records_the_flow_and_renders_the_clis_own_lines(
    repo: Repository,
) -> None:
    repo.set_state("equity_high_water_mark", Decimal("10000"))
    result = tc.run_record_flow_form(
        repo, _prompt(["500"]), gate_fn=lambda _amount: True
    )
    assert "flow of 500 recorded" in result
    assert "High-water mark rebased to 10500" in result  # 10000 + the 500 deposit
    assert repo.get_state("equity_high_water_mark") == Decimal("10500")


def test_record_flow_on_a_repo_with_no_mark_yet_gets_the_clis_own_no_mark_line(
    repo: Repository,
) -> None:
    result = tc.run_record_flow_form(
        repo, _prompt(["500"]), gate_fn=lambda _amount: True
    )
    assert "flow of 500 recorded" in result
    assert "No high-water mark yet" in result
    assert "next cycle will seed it" in result


def test_record_flow_form_cancels_on_an_empty_amount(repo: Repository) -> None:
    spy = _RecordingRepo(repo)
    result = tc.run_record_flow_form(spy, _prompt([""]), gate_fn=lambda: True)
    assert spy.calls == []
    assert "cancelled" in result.lower()


# -- autonomy: the CLI's own semantics -------------------------------------------------------------


def test_autonomy_on_requires_the_clis_typed_gate_and_writes_nothing_on_a_refusal(
    repo: Repository,
) -> None:
    spy = _RecordingRepo(repo)
    result = tc.run_autonomy_form(spy, _config(), _prompt(["on"]), NOW_TS, arm_gate=lambda: False)
    assert spy.calls == []
    assert "not given" in result or "cancelled" in result.lower()


def test_autonomy_on_arms_with_no_expiry_and_the_clis_own_warning(
    repo: Repository,
) -> None:
    spy = _RecordingRepo(repo)
    result = tc.run_autonomy_form(spy, _config(), _prompt(["on"]), NOW_TS, arm_gate=lambda: True)
    assert ("set_autonomous", {"value": True, "now_ts": NOW_TS, "expires_ts": None}) in (
        spy.calls
    )
    assert "autonomy ON, with NO expiry" in result
    assert "--for-hours" in result


def test_autonomy_off_is_ungated_and_immediate(repo: Repository) -> None:
    repo.set_autonomous(True, NOW_TS)
    gated: list[bool] = []

    def gate() -> bool:
        gated.append(True)
        return True

    spy = _RecordingRepo(repo)
    result = tc.run_autonomy_form(spy, _config(), _prompt(["off"]), NOW_TS, arm_gate=gate)
    assert gated == []  # OFF only ever reduces capability -- no ceremony
    # the call shape is the CLI's own (`autonomy off` passes no expiry at all)
    assert ("set_autonomous", {"value": False, "now_ts": NOW_TS}) in spy.calls
    assert result == "autonomy off: every order will ask for confirmation."


def test_the_autonomy_arm_gate_is_the_clis_own_extracted_gate(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ON direction's gate is `autonomy_on`'s OWN typed gate, extracted to
    `keel.commands.autonomy` (its one home) -- pinned here against the CLI's call site,
    with the CLI's exact action wording and mode/allowlist facts."""
    import inspect

    import click as click_mod

    from keel.commands import autonomy as autonomy_mod
    from keel.commands.autonomy import autonomy_on

    asked: list[tuple[str, str]] = []

    def gate(action: str, detail: str) -> None:
        asked.append((action, detail))

    # `autonomy_on_gate` resolves its gate through the copy `autonomy.py` imported at
    # module load -- the patch lands where the call will actually resolve it
    monkeypatch.setattr(
        autonomy_mod, "_require_interactive_confirmation", gate, raising=True
    )
    assert tc.clis_autonomy_on_gate(_config()) is True
    assert asked[0][0] == "turn autonomy ON"
    assert "mode=paper" in asked[0][1]
    assert "allowlist=['BTC', 'ETH']" in asked[0][1]
    assert "until you turn it off" in asked[0][1]  # no --for-hours: the CLI default window

    # and the CLI's `autonomy on` runs the SAME extracted gate -- one home, two front-ends
    source = inspect.getsource(autonomy_on.callback)
    assert "autonomy_on_gate" in source

    def refusing(action: str, detail: str) -> None:
        raise click_mod.ClickException("aborted")

    monkeypatch.setattr(autonomy_mod, "_require_interactive_confirmation", refusing)
    assert tc.clis_autonomy_on_gate(_config()) is False  # fails CLOSED


# -- the loop wiring (fake curses): ARMED gating and the typed proofs end-to-end -------------------


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


def test_run_live_trading_menu_opens_from_the_console_menu_and_esc_steps_back(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    stdscr = _drive(repo, _config(), [ord("m"), ord("3"), -1, 27, -1, 27], monkeypatch)
    painted = [call[2] for call in stdscr.calls]
    trading_idx = next(
        i for i, t in enumerate(painted) if "keel console -- trading" in t
    )
    trading_text = "\n".join(painted[trading_idx:])
    for label in (
        "agent cycle",
        "monitor poll",
        "autonomy",
        "record-flow",
        "reset-hwm",
        "resume-entries",
        "kill",
        "resume",
    ):
        assert label in trading_text, label
    assert any("keel console -- menu" in t for t in painted[trading_idx:])


def test_run_live_the_cycle_entry_is_armed_until_enter(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE confirm gate: opening Trading -> agent cycle, polling, and closing must never
    invoke `agent.run_once` -- the cycle happens only on an explicit Enter."""
    runs: list[int] = []

    def spy_run(*args: Any, **kwargs: Any) -> agent.LoopResult:
        runs.append(1)
        return agent.LoopResult(
            ts=NOW_TS, skipped=True, skip_reason="market_closed", mode=None, polled=0
        )

    monkeypatch.setattr(tc, "run_agent_cycle", spy_run)
    # m -> menu; 3 -> Trading; 1 -> agent cycle (ARMED); poll; Esc closes; q quits.
    stdscr = _drive(
        repo, _config(), [ord("m"), ord("3"), ord("1"), -1, 27], monkeypatch
    )
    assert runs == []
    painted = [call[2] for call in stdscr.calls]
    assert any("ARMED" in t for t in painted)


def test_run_live_enter_runs_exactly_one_cycle_and_holds_the_rendered_result(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs: list[int] = []

    def spy_run(*args: Any, **kwargs: Any) -> agent.LoopResult:
        runs.append(1)
        return agent.LoopResult(
            ts=NOW_TS,
            skipped=False,
            skip_reason=None,
            mode="paper",
            polled=4,
            products=["BTC-USD"],
            paper_equity=Decimal("100.00"),
            drawdown_total_pct=Decimal("0.0"),
            drawdown_weekly_pct=Decimal("0.0"),
        )

    monkeypatch.setattr(tc, "run_agent_cycle", spy_run)
    # m; 3 -> Trading; 1 -> cycle; Enter RUNS; poll repaints the held result; Esc; q.
    stdscr = _drive(
        repo, _config(), [ord("m"), ord("3"), ord("1"), 10, -1, 27], monkeypatch
    )
    assert runs == [1]
    painted = "\n".join(call[2] for call in stdscr.calls)
    assert f"[{NOW_TS}] mode=paper" in painted


def test_run_live_a_closed_sessions_skip_renders_verbatim_through_the_loop(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Session honesty, held honestly: a cycle that skips renders the skip's logged
    reason VERBATIM -- `render_loop_result`'s own line, nothing re-worded."""
    monkeypatch.setattr(
        tc,
        "run_agent_cycle",
        lambda *a, **k: agent.LoopResult(
            ts=NOW_TS, skipped=True, skip_reason="market_closed", mode=None, polled=0
        ),
    )
    stdscr = _drive(
        repo, _config(), [ord("m"), ord("3"), ord("1"), 10, -1, 27], monkeypatch
    )
    painted = "\n".join(call[2] for call in stdscr.calls)
    assert f"[{NOW_TS}] skipped: market_closed" in painted


def test_run_live_the_monitor_poll_is_armed_until_enter(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    from keel.commands.monitor import MonitorCycle

    polls: list[int] = []

    def spy_poll(*args: Any, **kwargs: Any) -> MonitorCycle:
        polls.append(1)
        return MonitorCycle(
            line=f"[{NOW_TS}] polled 2 new candle row(s) across ['BTC-USD']",
            session=None,
            session_bound=False,
            written=2,
        )

    monkeypatch.setattr(tc, "run_monitor_poll", spy_poll)
    # m; 3 -> Trading; 2 -> monitor poll (ARMED); poll; Esc; q -- NOTHING ran.
    stdscr = _drive(
        repo, _config(), [ord("m"), ord("3"), ord("2"), -1, 27], monkeypatch
    )
    assert polls == []
    # ...and Enter runs exactly one poll, holding the cycle's line
    stdscr = _drive(
        repo, _config(), [ord("m"), ord("3"), ord("2"), 10, -1, 27], monkeypatch
    )
    assert polls == [1]
    painted = "\n".join(call[2] for call in stdscr.calls)
    assert "polled 2 new candle row(s)" in painted


def test_run_live_resume_refusal_writes_nothing_end_to_end(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C3's withdrawals proof, applied to `resume`: driving the typed form through the
    LIVE loop with the gate REFUSING must leave the kill-switch engaged -- zero state
    writes, spy-proven -- and the cancellation toasts on the Trading menu."""
    import click as click_mod

    repo.set_state("kill_switch", True)

    def refusing_gate(action: str, detail: str) -> None:
        raise click_mod.ClickException("aborted (confirmation not given).")

    monkeypatch.setattr(
        "keel.commands._common._require_interactive_confirmation", refusing_gate
    )
    # m; 3 -> Trading; 8 -> resume (the typed form runs at the terminal); poll; q.
    stdscr = _drive(
        repo, _config(), [ord("m"), ord("3"), ord("8"), -1, ord("q")], monkeypatch
    )
    assert repo.get_state("kill_switch") is True  # the halt was NOT released
    painted = "\n".join(call[2] for call in stdscr.calls)
    assert "not given" in painted or "aborted" in painted
    # the suspend/restore dance ran around the form (the fake curses records it)
    fake_curses = sys.modules["curses"]
    assert "def_prog_mode" in fake_curses.calls
    assert "reset_prog_mode" in fake_curses.calls


def test_run_live_resume_with_the_typed_yes_releases_the_halt(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo.set_state("kill_switch", True)
    monkeypatch.setattr(
        "keel.commands._common._is_interactive", lambda: True
    )
    import click

    answers = iter(["yes"])
    monkeypatch.setattr(click, "prompt", lambda text, **kw: next(answers), raising=True)
    stdscr = _drive(
        repo, _config(), [ord("m"), ord("3"), ord("8"), -1, ord("q")], monkeypatch
    )
    assert repo.get_state("kill_switch") is False
    painted = "\n".join(call[2] for call in stdscr.calls)
    assert "kill-switch disengaged: trading resumed." in painted


def test_run_live_resume_entries_refusal_writes_nothing_end_to_end(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    import click as click_mod

    repo.set_state("streak_halt_until", 2_000_000_000)
    repo.set_state("consecutive_losses", 4)

    def refusing_gate(action: str, detail: str) -> None:
        raise click_mod.ClickException("aborted (confirmation not given).")

    monkeypatch.setattr(
        "keel.commands._common._require_interactive_confirmation", refusing_gate
    )
    # m; 3 -> Trading; 6 -> resume-entries; poll; q.
    stdscr = _drive(
        repo, _config(), [ord("m"), ord("3"), ord("6"), -1, ord("q")], monkeypatch
    )
    assert repo.get_state("streak_halt_until") == 2_000_000_000
    assert repo.get_state("consecutive_losses") == 4
    painted = "\n".join(call[2] for call in stdscr.calls)
    assert "not given" in painted or "aborted" in painted


def test_run_live_kill_engages_from_the_menu_with_no_ceremony(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """kill's CLI contract is one key and NO confirmation -- selecting the entry
    dispatches immediately; the toast is the CLI's own line."""
    repo.set_state("kill_switch", False)
    # m; 3 -> Trading; 7 -> kill (engages, no prompts at all); poll; q.
    stdscr = _drive(
        repo, _config(), [ord("m"), ord("3"), ord("7"), -1, ord("q")], monkeypatch
    )
    assert repo.get_state("kill_switch") is True
    painted = "\n".join(call[2] for call in stdscr.calls)
    assert "kill-switch ENGAGED: all trading halted." in painted


def test_run_live_the_trading_menu_scrolls_banner_aware(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Banner-aware scrolling: `End` on the Trading menu reaches the LAST entry's rows
    (the banner is part of the scrolled list, so the total must count it)."""
    from tests.commands.test_tui import _fake_curses

    fake = _fake_curses()
    # m; 3 -> Trading; End; poll; q.
    stdscr = _drive(
        repo, _config(), [ord("m"), ord("3"), fake.KEY_END, -1, ord("q")], monkeypatch
    )
    painted = [call[2] for call in stdscr.calls]
    after = painted[painted.index(next(t for t in painted if "keel console -- trading" in t)) :]
    assert any("resume" in t for t in after)


def test_run_live_the_cycle_dispatch_suspends_curses_around_the_mid_cycle_confirm_gate(
    repo: Repository,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """[review #405, major] On a confirm-mode profile the cycle itself asks at the
    terminal MID-RUN: the CLI's own `_interactive_confirm` echo's the order preview and
    reads a y (confirm.py: "the rendered string is the contract"). Dispatched inside
    the curses loop that prompt garbles the screen and the y is typed BLIND under
    noecho -- so the dispatch must ride the SAME suspend/restore dance the terminal
    forms use, spy-proven BY ORDER on the fake curses' one timeline: the suspend pair
    strictly before the gate asks, the restore strictly after it answers, and the held
    result still renders cleanly through the loop."""
    from keel.commands import confirm as confirm_mod

    real_cycle = tc.run_agent_cycle

    def scripted_run(*args: Any, confirm_fn: Any = None, **kwargs: Any) -> agent.LoopResult:
        # The executor's own mid-cycle ask: the gate the CLI hands `agent.run_once`.
        assert confirm_fn is not None
        answered = confirm_fn({"order_total": "10.00"})
        assert answered is True  # the y went through, cleanly
        return agent.LoopResult(
            ts=NOW_TS, skipped=False, skip_reason=None, mode="confirm", polled=1
        )

    monkeypatch.setattr(
        tc, "run_agent_cycle", lambda *a, **k: real_cycle(*a, **k, run_fn=scripted_run)
    )

    answers = iter(["y"])

    def stdout_gate(preview: Any) -> bool:
        # The CLI gate's own shape: WRITES the preview, READS the y -- at the terminal,
        # which is only sane while curses is suspended. Recorded on the fake curses'
        # call timeline so the order vs. the dance is assertable.
        print("Rails PASSED. Order preview:")
        print(f"    order_total: {preview['order_total']}")
        sys.modules["curses"].calls.append("gate:asked")
        return next(answers).strip().lower() == "y"

    monkeypatch.setattr(confirm_mod, "_interactive_confirm", stdout_gate)

    # m; 3 -> Trading; 1 -> cycle (ARMED); Enter RUNS (the gate asks mid-run); poll;
    # Esc; q.
    stdscr = _drive(
        repo, _config(), [ord("m"), ord("3"), ord("1"), 10, -1, 27], monkeypatch
    )

    timeline: list[str] = sys.modules["curses"].calls
    gate_at = timeline.index("gate:asked")
    # the suspend pair ran BEFORE the gate asked -- and nothing sits between the
    # suspend and the ask (the screen was handed over before the first write)
    assert timeline[:gate_at][-1] == "endwin"
    assert "def_prog_mode" in timeline[:gate_at]
    # ...and the restore ran only AFTER the gate answered
    assert "reset_prog_mode" not in timeline[:gate_at]
    assert "reset_prog_mode" in timeline[gate_at:]
    # the gate's write reached a sane stdout, and the cycle's held result renders
    assert "Order preview" in capsys.readouterr().out
    painted = "\n".join(call[2] for call in stdscr.calls)
    assert f"[{NOW_TS}] mode=confirm polled=1" in painted


def test_run_live_re_entering_the_trading_menu_resets_the_cursor_to_the_top(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[review #405] A remembered cursor is a loaded one: `trading_cursor` used to
    persist across sub-menu re-entries, so leaving Trading with the row on kill and
    re-entering meant a replayed Enter engaged the halt with no ceremony. The shared
    reset point puts every console sub-menu back on its TOP row on (re)entry -- so the
    second Enter of a double-tap lands on the first entry (the ARMED cycle view), never
    on kill. The one-key contract itself is unchanged (kill still engages immediately
    when its row is chosen on purpose -- tested above)."""
    from tests.commands.test_tui import _fake_curses

    down = _fake_curses().KEY_DOWN
    # m -> menu; 3 -> Trading; j x6 -> cursor on kill (row 7); q -> back to the menu;
    # 3 -> RE-ENTER Trading (cursor must reset); poll; Enter -> the TOP entry's
    # destination; poll; Esc -> back to Trading; then out and quit.
    stdscr = _drive(
        repo,
        _config(),
        [
            ord("m"),
            ord("3"),
            *([down] * 6),
            ord("q"),
            ord("3"),
            -1,
            10,
            -1,
            27,
        ],
        monkeypatch,
    )
    painted = [call[2] for call in stdscr.calls]
    marked = [(i, t) for i, t in enumerate(painted) if t.startswith(">")]
    kill_marked = [i for i, t in marked if "kill" in t]
    cycle_marked = [i for i, t in marked if "agent cycle" in t]
    assert kill_marked  # the cursor really did reach kill before leaving
    assert cycle_marked
    # after re-entry the TOP row is the marked one again -- never kill
    assert max(cycle_marked) > max(kill_marked)
    # ...so the replayed Enter opened the cycle's ARMED view, not kill's halt
    after_reentry = painted[max(kill_marked) :]
    assert any("ARMED -- nothing has run yet." in t for t in after_reentry)
    assert not any("kill-switch ENGAGED" in t for t in after_reentry)
    assert repo.get_state("kill_switch") is False
