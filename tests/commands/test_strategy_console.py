"""Tests for `keel.commands.strategy_console` -- the Rules menu, the tried-vs-used ledger,
simulate-from-console, the add form and the retry flow (issue #390 C4; PRD O11).

Four surfaces, all pinned here:

* **The sub-menu** -- PRD §3's Rules branch as the strategy console: the ledger, simulate,
  add, retry (backtest + promote, `--force` typed), enable/disable/demote, insights.
* **The tried-vs-used ledger (O11.2)** -- every rule with its lifecycle status AND the
  machine's recorded reason it sits there: the promotion gate's own failing-floor wording
  from a real backtest over the repo's cached candles, the insights gate distance for paper
  rules, the recorded stamps for live/disabled rows. A rule with no candles to backtest
  against renders "no backtest on record" -- never a TUI-authored narrative.
* **Simulate (O11.1)** -- an ARMED view that shows the target report path BEFORE any run
  (the confirm step), `run_simulation` spied (never really computed here), and the results
  rendering the service's own verdict/report verbatim.
* **The forms (O11.3/O11.4)** -- add (per-field parameter help from `describe_params`,
  landing as candidate with the service's own validation errors), retry (backtest +
  promote with an explicit confirm and a TYPED `--force` gate that refuses on a wrong
  phrase), and enable as the documented restore path.

Mirrors `tests/commands/test_compliance_console.py`'s fixture style.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from keel.commands import strategy_console as sc
from keel.commands.simulate import SimulationOutcome
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
        market_data=MarketDataConfig(granularities=[], history_days=365),
        auto_trade=AutoTradeConfig(mode="paper", interval_sec=900),
        money_mgmt=MoneyMgmtConfig(
            max_total_dd_pct=Decimal("0.20"), max_weekly_dd_pct=Decimal("0.08")
        ),
        dca=DcaConfig(budget_usd=Decimal("50"), cadence_days=7),
    )
    base.update(overrides)
    return Config(**base)


def _daily_candles(n: int, *, start: int = 1_700_000_000) -> list[Candle]:
    """Flat daily candles: enough history for a rule to evaluate, no trend to trade."""
    return [
        Candle(
            ts=start + i * 86400,
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100"),
            volume=Decimal("10"),
        )
        for i in range(n)
    ]


def _prompt(answers: list[str]) -> Any:
    queue = iter(answers)
    asked: list[str] = []

    def fn(text: str) -> str:
        asked.append(text)
        return next(queue)

    fn.asked = asked
    return fn


# -- the sub-menu (PRD §3's Rules branch) ----------------------------------------------------------


def test_the_strategy_menu_is_the_prd_rules_branch() -> None:
    assert [entry.label for entry in sc.STRATEGY_MENU] == [
        "tried-vs-used ledger",
        "simulate + results",
        "add a strategy",
        "retry a strategy",
        "enable (restore)",
        "disable",
        "demote",
        "insights",
    ]
    retry = sc.strategy_entry(4)
    assert retry is not None
    assert retry.label == "retry a strategy"
    assert "backtest" in retry.description and "promote" in retry.description


def test_the_menu_screen_renders_every_entry_and_the_keys() -> None:
    lines = sc.build_strategy_menu_lines(cursor=0)
    texts = [line.text for line in lines]
    for entry in sc.STRATEGY_MENU:
        assert any(entry.label in t for t in texts), entry.label
    assert any("up/k down/j" in t for t in texts)


# -- the tried-vs-used ledger (O11.2) ----------------------------------------------------------


def test_the_ledger_names_the_exact_failing_floor_from_a_real_backtest(repo: Repository) -> None:
    """A candidate with candles on record but far too few trades: the ledger renders the
    gate's OWN reason for that floor -- `n_trades N < min_trades 100` -- sourced from a real
    backtest judged through `can_promote`, not a TUI summary of it."""
    repo.insert_rule(
        "turtle_breakout",
        {"product_id": "BTC-USD", "entry_lookback": 40},
        status="candidate",
        now_ts=NOW_TS,
    )
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _daily_candles(60))

    ledger = sc.build_strategy_ledger(repo, _config(), NOW_TS)
    assert len(ledger) == 1
    entry = ledger[0]
    assert entry.status == "candidate"
    joined = "\n".join(entry.reason_lines)
    assert "n_trades" in joined and "min_trades" in joined
    # The overfitting axis is honestly NOT_RUN, in the machine's own words.
    assert "NOT RUN" in joined


def test_the_ledger_renders_no_backtest_on_record_for_a_never_backtested_rule(
    repo: Repository,
) -> None:
    """No cached candles for the product: no backtest can have ever run, and the ledger says
    exactly that rather than inventing a verdict."""
    repo.insert_rule(
        "turtle_breakout", {"product_id": "ETH-USD"}, status="candidate", now_ts=NOW_TS
    )
    ledger = sc.build_strategy_ledger(repo, _config(), NOW_TS)
    joined = "\n".join(ledger[0].reason_lines)
    assert "no backtest on record" in joined


def test_the_ledger_groups_every_lifecycle_status_with_its_recorded_context(
    repo: Repository,
) -> None:
    """live / paper / candidate / disabled all render, grouped in-use-first, with the
    RECORDED stamps (promoted_at / demoted_at) -- never a narrative."""
    live_id = repo.insert_rule(
        "dca", {"product_id": "BTC-USD"}, status="live", now_ts=NOW_TS
    )
    repo.update_rule_status(live_id, "live")  # stamps promoted_at
    repo.insert_rule(
        "dca", {"product_id": "ETH-USD"}, status="disabled", now_ts=NOW_TS
    )
    repo.insert_rule(
        "pullback_continuation",
        {"product_id": "BTC-USD"},
        status="paper",
        now_ts=NOW_TS,
    )
    repo.insert_rule(
        "turtle_breakout",
        {"product_id": "BTC-USD"},
        status="candidate",
        now_ts=NOW_TS,
    )

    ledger = sc.build_strategy_ledger(repo, _config(), NOW_TS)
    statuses = [entry.status for entry in ledger]
    assert statuses[0] == "live"
    assert set(statuses) == {"live", "paper", "candidate", "disabled"}

    lines = sc.build_ledger_lines(ledger, cursor=0)
    texts = [line.text for line in lines]
    joined = "\n".join(texts)
    assert "IN USE" in joined
    assert "TRIED" in joined or "CANDIDATE" in joined
    assert "DISABLED" in joined
    # The disabled row names the documented restore path (the `rules enable` docstring's own).
    assert "rules enable" in joined
    # The paper row carries the insights service's own gate-distance reading (an empty
    # track record reads as the floor it still owes, not a fabricated absence).
    assert "trades_remaining" in joined


def test_the_paper_row_carries_the_insights_gate_distance_when_one_exists(
    repo: Repository,
) -> None:
    """A paper rule with a paper track record: the ledger renders the insights service's own
    gate-distance blocking reasons (trades_remaining wording), not a recomputation."""
    import json

    repo.insert_rule(
        "pullback_continuation", {"product_id": "BTC-USD"}, status="paper", now_ts=NOW_TS
    )
    entry_id = repo.insert_order(
        dict(
            mode="paper",
            product_id="BTC-USD",
            side="BUY",
            order_type="market",
            qty=Decimal("1"),
            limit_price=Decimal("100"),
            status="filled",
            fee=Decimal("0"),
            expected_fill=Decimal("100"),
            actual_fill=Decimal("100"),
            raw_response=json.dumps(
                {
                    "role": "entry",
                    "rule_name": "pullback_continuation",
                    "entry": "100",
                    "stop": "90",
                    "target": "120",
                    "qty": "1",
                    "ts": 1_000,
                }
            ),
            confirmation="paper",
            rule_id=None,
            created_at=1_000,
            updated_at=1_000,
        )
    )
    repo.insert_order(
        dict(
            mode="paper",
            product_id="BTC-USD",
            side="SELL",
            order_type="market",
            qty=Decimal("1"),
            limit_price=Decimal("110"),
            status="filled",
            fee=Decimal("0"),
            expected_fill=Decimal("110"),
            actual_fill=Decimal("110"),
            raw_response=json.dumps(
                {
                    "role": "exit",
                    "rule_name": "pullback_continuation",
                    "entry_order_id": entry_id,
                    "entry": "100",
                    "exit": "110",
                    "qty": "1",
                    "pnl": "10",
                    "r_multiple": "1.0",
                    "mfe": "12",
                    "mae": "2",
                    "outcome": "win",
                    "entry_ts": 1_000,
                    "exit_ts": 2_000,
                }
            ),
            confirmation="paper",
            rule_id=None,
            created_at=2_000,
            updated_at=2_000,
        )
    )

    ledger = sc.build_strategy_ledger(repo, _config(), NOW_TS)
    paper = next(e for e in ledger if e.status == "paper")
    joined = "\n".join(paper.reason_lines)
    assert "trades_remaining" in joined
    assert "n_trades 1 < min_trades" in joined


def test_the_ledger_detail_renders_params_with_their_docs(repo: Repository) -> None:
    """The ledger's detail view renders every param through `describe_params` -- the O8
    per-field help, single-sourced from the class."""
    repo.insert_rule(
        "turtle_breakout",
        {"product_id": "BTC-USD", "entry_lookback": 55},
        status="candidate",
        now_ts=NOW_TS,
    )
    ledger = sc.build_strategy_ledger(repo, _config(), NOW_TS)
    lines = sc.build_ledger_detail_lines(ledger[0])
    texts = [line.text for line in lines]
    joined = "\n".join(texts)
    assert "entry_lookback" in joined
    assert "55" in joined
    # The class's own docstring renders beside the value.
    assert "Donchian-high entry" in joined


# -- simulate (O11.1): the ARMED view, the spied service, the results ------------------------------


def test_the_simulate_plan_names_the_target_report_path_before_any_run() -> None:
    plan = sc.simulate_plan(_config(), "keel.db", now_ts=NOW_TS)
    assert plan.report_path == sc.default_report_path(NOW_TS)
    lines = sc.build_simulate_armed_lines(plan)
    texts = [line.text for line in lines]
    joined = "\n".join(texts)
    assert "ARMED" in joined
    assert str(plan.report_path) in joined
    assert "Enter" in joined


def test_run_simulate_dispatches_to_run_simulation_with_the_active_profile(
    repo: Repository,
) -> None:
    """The one compute/network touch: `run_simulation` itself, handed the repo+config of the
    ACTIVE profile and the CLI's own defaults (5y, $500/month, allowlist products) -- spied
    here, never really run."""
    plan = sc.simulate_plan(_config(), "keel.db", now_ts=NOW_TS)
    calls: list[dict[str, Any]] = []

    def spy_run(*args: Any, **kwargs: Any) -> SimulationOutcome:
        calls.append({"args": args, "kwargs": kwargs})
        return SimulationOutcome(
            verdict_status="TRAIN-MORE",
            verdict_reasons=("n_trades 0 < min_trades 100",),
            report_path=plan.report_path,
            report_markdown="# report\n\nverdict: TRAIN-MORE\n",
            artifact_path=None,
        )

    outcome = sc.run_simulate(
        repo,
        _config(),
        plan,
        now_ts=NOW_TS,
        build_client=None,
        run_fn=spy_run,
    )
    assert len(calls) == 1
    assert calls[0]["args"][0] is repo
    assert calls[0]["kwargs"]["db_path"] == "keel.db"
    assert calls[0]["kwargs"]["years"] == 5
    assert calls[0]["kwargs"]["monthly_contribution"] == Decimal("500")
    assert calls[0]["kwargs"]["products"] == ["BTC-USD", "ETH-USD"]
    assert outcome.verdict_status == "TRAIN-MORE"


def test_the_simulate_results_render_the_services_verdict_and_report_verbatim(
    repo: Repository,
) -> None:
    outcome = SimulationOutcome(
        verdict_status="TRAIN-MORE",
        verdict_reasons=("n_trades 0 < min_trades 100", "edge negative vs DCA"),
        report_path=Path("docs/superpowers/reports/2026-08-17-engine-validation.md"),
        report_markdown=(
            "# Engine validation\n\nverdict: TRAIN-MORE\n\n"
            "net of fees the strategy underperformed the DCA benchmark\n"
        ),
        artifact_path=None,
    )
    lines = sc.build_simulate_result_lines(outcome, progress=("data cached in: keel.db",))
    texts = [line.text for line in lines]
    joined = "\n".join(texts)
    assert "TRAIN-MORE" in joined
    assert "net of fees the strategy underperformed the DCA benchmark" in joined
    assert "docs/superpowers/reports/2026-08-17-engine-validation.md" in joined
    # The progress the CLI would have streamed is not lost: it heads the results.
    assert "data cached in: keel.db" in joined


def test_the_simulate_verdict_footer_is_pinned_outside_the_scroll() -> None:
    outcome = SimulationOutcome(
        verdict_status="TRAIN-MORE",
        verdict_reasons=("n_trades 0 < min_trades 100",),
        report_path=Path("docs/reports/x.md"),
        report_markdown="line\n" * 200,
        artifact_path=None,
    )
    footer = sc.simulate_verdict_footer(outcome)
    joined = "\n".join(line.text for line in footer)
    assert "TRAIN-MORE" in joined
    assert "docs/reports/x.md" in joined


# -- the add form (O11.3) ----------------------------------------------------------------------


def test_the_add_form_offers_per_field_parameter_help(repo: Repository) -> None:
    """Every turtle param is prompted with its doc, type and default from
    `describe_params`; an empty answer keeps the default, and the row lands as candidate."""
    answers = ["turtle_breakout", "BTC-USD"] + [""] * len(
        sc.describe_params("turtle_breakout")
    )
    prompt = _prompt(answers)
    result = sc.run_add_form(repo, _config(), prompt, NOW_TS)
    asked = "\n".join(prompt.asked)
    assert "entry_lookback" in asked
    assert "Donchian-high entry" in asked
    assert "atr_stop_mult" in asked
    rows = repo.get_rules()
    assert len(rows) == 1
    assert rows[0]["status"] == "candidate"
    assert rows[0]["params"]["entry_lookback"] == 40  # the kind's own default
    assert "added rule" in result
    assert "status=candidate" in result


def test_the_add_form_sets_only_the_fields_answered(repo: Repository) -> None:
    params = sc.describe_params("turtle_breakout")
    answers = ["turtle_breakout", "BTC-USD"]
    for name in params:
        answers.append("70" if name == "entry_lookback" else "")
    result = sc.run_add_form(repo, _config(), _prompt(answers), NOW_TS)
    rows = repo.get_rules()
    assert rows[0]["params"]["entry_lookback"] == 70
    assert "added rule" in result


def test_the_add_form_surfaces_the_services_own_validation_errors(repo: Repository) -> None:
    """A quoted number for a float param: the SERVICE's message renders as the form's
    Error: line, and nothing is written."""
    params = sc.describe_params("rsi_meanrev")
    answers = ["rsi_meanrev", "BTC-USD"]
    for name in params:
        # `oversold` is a float param: a QUOTED value is the refusal the CLI makes.
        answers.append('"10.0"' if name == "oversold" else "")
    result = sc.run_add_form(repo, _config(), _prompt(answers), NOW_TS)
    assert result.startswith("Error:")
    assert "cannot use these params" in result
    assert repo.get_rules() == []


def test_the_add_form_cancels_on_an_empty_kind(repo: Repository) -> None:
    result = sc.run_add_form(repo, _config(), _prompt([""]), NOW_TS)
    assert "cancelled" in result
    assert repo.get_rules() == []


# -- the retry flow (O11.4) --------------------------------------------------------------------


def _retry_rule(repo: Repository) -> int:
    """A turtle candidate with daily candles on record: the backtest resolves (the kind's
    own ONE_DAY granularity), produces a short sample, and the gate's own wording is what
    the form renders."""
    rule_id = repo.insert_rule(
        "turtle_breakout",
        {"product_id": "BTC-USD", "entry_lookback": 40},
        status="candidate",
        now_ts=NOW_TS,
    )
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _daily_candles(30))
    return rule_id


def test_the_retry_form_rebacktests_and_asks_before_promoting(repo: Repository) -> None:
    """Retry = re-backtest (always) + re-attempt promote (only on an explicit y/N confirm). A
    declined confirm means no promotion attempt at all, and no typed gate either."""
    _retry_rule(repo)
    promotes: list[str] = []
    result = sc.run_retry_form(
        repo,
        _config(),
        _prompt(["1", "n", "n"]),
        NOW_TS,
        typed_force_fn=lambda *a: (promotes.append("asked"), False)[1],
    )
    assert promotes == []
    assert repo.get_rules()[0]["status"] == "candidate"
    assert "n_trades=" in result  # the backtest ran and its line rendered


def test_the_retry_promote_reports_the_machines_refusal_verbatim(repo: Repository) -> None:
    """A confirmed promote attempt without a PBO session: the gate's OWN NOT_RUN refusal is
    the form's result, and the status does not move."""
    _retry_rule(repo)
    result = sc.run_retry_form(
        repo,
        _config(),
        _prompt(["1", "y", "", "n"]),
        NOW_TS,
        typed_force_fn=lambda *a: pytest.fail("not reached: force was declined"),
    )
    assert "overfitting check" in result
    assert "NOT RUN" in result
    assert repo.get_rules()[0]["status"] == "candidate"


def test_the_retry_force_requires_the_typed_phrase_and_refuses_a_wrong_one(
    repo: Repository,
) -> None:
    """`--force` stays typed (O3): the gate asks for the CLI's own `Type "yes" to confirm`
    phrase; a wrong phrase means not a single status write."""
    _retry_rule(repo)
    phrases: list[str] = []
    result = sc.run_retry_form(
        repo,
        _config(),
        _prompt(["1", "n", "y"]),
        NOW_TS,
        typed_force_fn=lambda *a: (phrases.append("asked"), False)[1],
    )
    assert phrases == ["asked"]
    assert "typed confirmation" in result
    assert repo.get_rules()[0]["status"] == "candidate"


def test_the_retry_force_with_the_right_phrase_advances_the_rule(repo: Repository) -> None:
    _retry_rule(repo)
    result = sc.run_retry_form(
        repo,
        _config(),
        _prompt(["1", "n", "y"]),
        NOW_TS,
        typed_force_fn=lambda *a: True,
    )
    assert "FORCE-PROMOTING" in result
    assert repo.get_rules()[0]["status"] == "paper"


def test_clis_typed_promote_force_gate_uses_the_clis_own_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The typed gate IS `_require_interactive_confirmation` with action wording that quotes
    the CLI's own force warning -- pinned so the console and the CLI's phrase can never
    drift apart."""
    asked: list[tuple[str, str]] = []

    def fake_gate(action: str, detail: str) -> None:
        asked.append((action, detail))

    monkeypatch.setattr("keel.commands._common._require_interactive_confirmation", fake_gate)
    assert sc.clis_typed_promote_force_gate(7, "dca", "candidate", "paper") is True
    assert len(asked) == 1
    action, _detail = asked[0]
    assert "force-promote rule 7 (dca)" in action
    assert "BYPASSING" in action


def test_clis_typed_promote_force_gate_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def refusing_gate(action: str, detail: str) -> None:
        raise RuntimeError("no tty")

    monkeypatch.setattr("keel.commands._common._require_interactive_confirmation", refusing_gate)
    assert sc.clis_typed_promote_force_gate(7, "dca", "candidate", "paper") is False


# -- enable as the documented restore path ------------------------------------------------------


def test_the_enable_form_restores_a_disabled_rule_at_candidate(repo: Repository) -> None:
    rule_id = repo.insert_rule(
        "dca", {"product_id": "BTC-USD"}, status="disabled", now_ts=NOW_TS
    )
    del rule_id
    result = sc.run_enable_form(repo, _config(), _prompt(["1"]), NOW_TS)
    assert repo.get_rules()[0]["status"] == "candidate"
    assert "CANDIDATE" in result


def test_the_enable_form_refuses_a_rule_that_is_not_disabled(repo: Repository) -> None:
    repo.insert_rule("dca", {"product_id": "BTC-USD"}, status="paper", now_ts=NOW_TS)
    result = sc.run_enable_form(repo, _config(), _prompt(["1"]), NOW_TS)
    assert result.startswith("Error:")
    assert "not disabled" in result
    assert repo.get_rules()[0]["status"] == "paper"


# -- the loop wiring (fake curses): the simulate confirm gate ----------------------------------


def _fake_curses_mod(monkeypatch: pytest.MonkeyPatch, stdscr: Any) -> Any:
    from tests.commands.test_tui import _fake_curses

    fake = _fake_curses()
    fake.wrapper = lambda fn: fn(stdscr)
    monkeypatch.setitem(sys.modules, "curses", fake)
    return fake


def test_run_live_simulate_opens_armed_and_never_runs_the_service_until_enter(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE confirm gate: opening Rules -> simulate, polling, and closing must never invoke
    `run_simulation` -- the run happens only on an explicit Enter, exactly like the discover
    overlay's one network call."""
    from keel.commands import tui as tui_mod

    config = _config()
    runs: list[int] = []

    def spy_run(*args: Any, **kwargs: Any) -> SimulationOutcome:
        runs.append(1)
        return SimulationOutcome(
            verdict_status="TRAIN-MORE",
            verdict_reasons=(),
            report_path=Path("docs/superpowers/reports/x.md"),
            report_markdown="verdict: TRAIN-MORE\n",
            artifact_path=None,
        )

    monkeypatch.setattr(sc, "run_simulate", spy_run)

    from tests.commands.test_tui import _KeySequenceStdscr

    # m -> menu; 4 -> Rules; 2 -> simulate (ARMED); poll; Esc closes; q quits.
    keys = [ord("m"), ord("4"), ord("2"), -1, 27]
    stdscr = _KeySequenceStdscr(height=24, width=80, keys=keys)
    _fake_curses_mod(monkeypatch, stdscr)

    binding = _binding(repo, config)
    tui_mod.run_live(binding.open_state, lambda: NOW_TS, interval=0.01, console_binding=binding)

    assert runs == []
    painted = [call[2] for call in stdscr.calls]
    assert any("ARMED" in t for t in painted)


def test_run_live_simulate_enter_runs_the_service_once_and_holds_the_result(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    from keel.commands import tui as tui_mod
    from tests.commands.test_tui import _KeySequenceStdscr

    config = _config()
    runs: list[int] = []

    def spy_run(*args: Any, **kwargs: Any) -> SimulationOutcome:
        runs.append(1)
        return SimulationOutcome(
            verdict_status="TRAIN-MORE",
            verdict_reasons=("n_trades 0 < min_trades 100",),
            report_path=Path("docs/superpowers/reports/x.md"),
            report_markdown="verdict: TRAIN-MORE (net of fees)\n",
            artifact_path=None,
        )

    monkeypatch.setattr(sc, "run_simulate", spy_run)

    # m -> menu; 4 -> Rules; 2 -> simulate; Enter RUNS; poll repaints the held result; Esc.
    keys = [ord("m"), ord("4"), ord("2"), 10, -1, 27]
    stdscr = _KeySequenceStdscr(height=24, width=80, keys=keys)
    _fake_curses_mod(monkeypatch, stdscr)

    binding = _binding(repo, config)
    tui_mod.run_live(binding.open_state, lambda: NOW_TS, interval=0.01, console_binding=binding)

    assert runs == [1]
    painted = [call[2] for call in stdscr.calls]
    assert any("TRAIN-MORE" in t for t in painted)


def test_run_live_the_ledger_renders_the_machines_reasons(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Loop-level: Rules -> ledger paints every rule row with the gate's own wording
    (computed on entry, held), and Esc closes back to the Rules menu."""
    from keel.commands import tui as tui_mod
    from tests.commands.test_tui import _KeySequenceStdscr

    config = _config()
    repo.insert_rule(
        "turtle_breakout",
        {"product_id": "BTC-USD", "entry_lookback": 40},
        status="candidate",
        now_ts=NOW_TS,
    )
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _daily_candles(30))

    # m -> menu; 4 -> Rules; 1 -> ledger; poll; Esc closes; q quits.
    keys = [ord("m"), ord("4"), ord("1"), -1, 27]
    stdscr = _KeySequenceStdscr(height=24, width=80, keys=keys)
    _fake_curses_mod(monkeypatch, stdscr)

    binding = _binding(repo, config)
    tui_mod.run_live(binding.open_state, lambda: NOW_TS, interval=0.01, console_binding=binding)

    painted = [call[2] for call in stdscr.calls]
    # `stdscr.calls` records one entry per PAINTED LINE; the whole visit is the join.
    joined = "\n".join(painted)
    assert "tried-vs-used ledger" in joined
    assert "min_trades" in joined  # the gate's own floor wording
    # The compute happened once and the ledger view painted with the machine's reasons.
    assert "n_trades 0 < min_trades 100" in joined


def _binding(repo: Repository, config: Config) -> Any:
    """A console binding whose open_state answers (repo, config) -- the loaders are the
    CLI's own seams, swapped here for the in-memory pair."""
    import click

    from keel.commands.console import ConsoleBinding

    ctx = click.Context(
        click.Command("tui"), obj={"config_path": "config.yaml", "db_path": "keel.db"}
    )
    binding = ConsoleBinding(ctx, config_path="config.yaml", db_path="keel.db")
    binding.open_state = lambda: (repo, config)  # type: ignore[method-assign]
    return binding
