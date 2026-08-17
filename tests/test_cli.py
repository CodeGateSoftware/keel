"""Tests for keel.cli -- the CLI (P3 Task 9).

Every test drives the CLI through `click.testing.CliRunner` -- no live network, no live broker.
`FakeBroker` below duck-types `CoinbaseClient` (`get_candles`/`preview_order`/`place_order`/
`cancel_order`), modeled on `tests/test_agent.py::FakeBroker`; tests that need a broker monkeypatch
`keel.cli._build_broker` (the one seam that would otherwise construct a real, network-talking
`CoinbaseClient`) to return it instead.

Halt-releasing commands (`resume`, `reset-hwm`, ...) demand a typed `yes` from a terminal;
read-only commands (`db import`, `monitor`, `rules list`, `pnl`) need no confirmation at all.
"""

from __future__ import annotations

import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from click.testing import CliRunner

import keel.cli as cli_module
from keel import agent
from keel.agent import RULE_REGISTRY, _build_rule
from keel.cli import cli
from keel.commands._common import DISCLAIMER
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.strategy.backtest import SLIPPAGE_FLOOR_PCT
from keel.types import Candle, Granularity
from tests.conftest import VALID_CONFIG_YAML

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "transactions_dir"


def _repo_at(db_path: Path) -> Repository:
    conn = connect(str(db_path))
    migrate(conn)
    return Repository(conn)


class FakeBroker:
    """No-network fake: canned (empty) candles + always-successful order responses."""

    def __init__(self) -> None:
        self.get_candles_calls: list[tuple] = []

    def get_candles(self, product_id: str, granularity: Any, start: int, end: int) -> list:
        self.get_candles_calls.append((product_id, granularity, start, end))
        return []

    def preview_order(self, product_id: str, side: Any, order_configuration: dict) -> dict:
        return {
            "order_total": Decimal("50.00"),
            "commission_total": Decimal("0"),
            "errs": [],
            "warning": [],
        }

    def place_order(self, product_id: str, side: Any, order_configuration: dict) -> dict:
        return {
            "success": True,
            "order_id": "fake-order-1",
            "product_id": product_id,
            "side": side.value if hasattr(side, "value") else side,
            "client_order_id": "fake-client-1",
            "order_configuration": order_configuration,
            "error": None,
        }

    def cancel_order(self, order_id: str) -> bool:
        return True        # a CONFIRMED cancel -- see `_cancel_at_exchange`


# -- db import ------------------------------------------------------------------------------


def test_db_import_runs_importer_against_temp_db(tmp_path):
    db_path = tmp_path / "test.db"
    runner = CliRunner()

    result = runner.invoke(cli, ["--db", str(db_path), "db", "import", str(FIXTURES_DIR)])

    assert result.exit_code == 0, result.output
    assert "imported=" in result.output
    repo = _repo_at(db_path)
    assert len(repo.get_transactions()) > 0



# -- disclaimer -----------------------------------------------------------------------------


def test_disclaimer_shown_on_every_command(tmp_path):
    db_path = tmp_path / "test.db"
    runner = CliRunner()

    result = runner.invoke(cli, ["--db", str(db_path), "db", "import", str(FIXTURES_DIR)])

    assert DISCLAIMER in result.output


def test_disclaimer_shown_even_when_refused(tmp_path):
    db_path = tmp_path / "test.db"
    runner = CliRunner()

    result = runner.invoke(cli, ["--db", str(db_path), "resume"])

    assert result.exit_code != 0
    assert DISCLAIMER in result.output


# -- agent --bypass gating --------------------------------------------------------------------





def test_agent_confirm_mode_needs_no_passphrase(tmp_path, valid_config_path, monkeypatch):
    monkeypatch.setattr(cli_module, "_build_broker", lambda config: FakeBroker())
    db_path = tmp_path / "test.db"
    runner = CliRunner()

    result = runner.invoke(
        cli,
        [
            "--db", str(db_path),
            "--config", str(valid_config_path),
                        "agent",
        ],
    )

    assert result.exit_code == 0, result.output


def test_agent_loop_bounded_by_max_cycles(tmp_path, valid_config_path, monkeypatch):
    monkeypatch.setattr(cli_module, "_build_broker", lambda config: FakeBroker())
    db_path = tmp_path / "test.db"
    runner = CliRunner()

    result = runner.invoke(
        cli,
        [
            "--db", str(db_path),
            "--config", str(valid_config_path),
            "agent", "--loop", "--max-cycles", "3", "--interval", "0",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.output.count("skipped: kill_switch") == 3


def test_agent_prints_paper_equity_and_drawdown_line(tmp_path, write_config, monkeypatch):
    """Task 9: `_print_loop_result` surfaces the synthetic paper equity + Rail 11's drawdown
    scalars -- the observability for a paper-forward, not just a side effect buried in state."""
    from tests.conftest import VALID_CONFIG_YAML

    monkeypatch.setattr(cli_module, "_build_broker", lambda config: FakeBroker())
    config_path = write_config(VALID_CONFIG_YAML + "\npaper:\n  starting_equity_usd: 10000\n")
    db_path = tmp_path / "test.db"
    _repo_at(db_path).set_state("kill_switch", False)
    runner = CliRunner()

    result = runner.invoke(
        cli,
        [
            "--db", str(db_path),
            "--config", str(config_path),
            "agent",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "paper equity $10000" in result.output
    assert "drawdown 0 total / 0 weekly" in result.output


# -- agent -- blocked entries (Finding 1, HIGH: duplicate real-money orders) -------------------


def _seed_blocked_dca_scenario(db_path: Path, now_ts: int) -> None:
    """A `dca` rule whose gating ONE_DAY bar is wildly stale, with a fresh ONE_HOUR bar so
    `market_feed.is_fresh`'s STALE-FEED skip (checked against the FINEST configured
    granularity) doesn't pre-empt the entry gate before it's ever reached. `cadence_days=1`
    makes every stored bar a cadence hit regardless of ts, matching
    `tests/test_agent.py::test_run_once_blocks_a_dca_entry_on_a_stale_daily_bar`, whose
    reasoning this reuses one layer up, through the real CLI entrypoint.
    """
    product = "BTC-USD"
    repo = _repo_at(db_path)
    repo.set_state("kill_switch", False)

    def _c(ts: int, price: str = "100") -> Candle:
        p = Decimal(price)
        return Candle(ts=ts, open=p, high=p, low=p, close=p, volume=Decimal("1"))

    repo.upsert_candles(product, Granularity.ONE_DAY, [_c(0)])
    repo.upsert_candles(product, Granularity.ONE_HOUR, [_c(now_ts - 60)])
    repo.insert_rule("dca", {"product_id": product, "cadence_days": 1}, status="paper")


def test_agent_exits_data_not_ready_when_an_entry_is_blocked(tmp_path, write_config, monkeypatch):
    """CLI surface for Finding 1 (HIGH). A single-cycle `keel agent` must not exit `0` when
    `run_once` withheld an entry on an unconfirmed bar -- a green exit code is exactly what lets
    a cron/LaunchAgent wrapper stamp the day as done and never retry, turning a transient
    publication lag into a silently-skipped trading day forever instead of the intended
    `<= 60 minutes of delay` (see `agent.DATA_NOT_READY_EXIT`'s docstring).
    """
    from tests.conftest import VALID_CONFIG_YAML

    monkeypatch.setattr(cli_module, "_build_broker", lambda config: FakeBroker())
    # Drop FIFTEEN_MINUTE -- the live config's finest granularity -- so the market-data-wide
    # STALE-FEED skip (`market_feed.is_fresh`, checked against the finest configured
    # granularity) doesn't need its own fixture and can't mask the entry gate this test is about.
    config_path = write_config(VALID_CONFIG_YAML.replace("    - FIFTEEN_MINUTE\n", ""))
    db_path = tmp_path / "test.db"
    now_ts = 5 * 86_400 + 2 * 3_600
    _seed_blocked_dca_scenario(db_path, now_ts)
    monkeypatch.setattr(cli_module.time, "time", lambda: now_ts)
    runner = CliRunner()

    result = runner.invoke(cli, ["--db", str(db_path), "--config", str(config_path), "agent"])

    assert result.exit_code == agent.DATA_NOT_READY_EXIT, result.output
    assert "signals=0" in result.output
    assert "blocked=1" in result.output


def test_agent_exits_zero_and_reports_blocked_zero_when_nothing_is_blocked(
    tmp_path, valid_config_path, monkeypatch
):
    """The counterweight to the test above: a normal cycle with nothing blocked exits `0`, and
    the printed line carries `blocked=0` alongside `signals=` -- `_print_loop_result` must keep
    emitting the `signals=[0-9]+` token the live runner greps out of its output (see the runner's
    own `keel-live-run.sh`), and `blocked=0` proves the new token is additive, not a replacement.
    """
    monkeypatch.setattr(cli_module, "_build_broker", lambda config: FakeBroker())
    db_path = tmp_path / "test.db"
    _repo_at(db_path).set_state("kill_switch", False)
    runner = CliRunner()

    result = runner.invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "agent"]
    )

    assert result.exit_code == 0, result.output
    assert "signals=0" in result.output
    assert "blocked=0" in result.output


def test_agent_loop_does_not_exit_the_process_when_a_cycle_is_blocked(
    tmp_path, write_config, monkeypatch
):
    """`--loop` must NEVER terminate the process on a blocked cycle -- a long-running loop is
    supposed to skip the cycle and retry next interval, exactly like it does for any other
    per-cycle condition (kill-switch, stale feed); dying on a usually-transient publication lag
    would take the whole scheduled loop down over what a single-cycle runner recovers from in
    one retry. Reuses the same blocked scenario as the single-cycle exit-code test above, but
    through `--loop --max-cycles 1`.
    """
    from tests.conftest import VALID_CONFIG_YAML

    monkeypatch.setattr(cli_module, "_build_broker", lambda config: FakeBroker())
    config_path = write_config(VALID_CONFIG_YAML.replace("    - FIFTEEN_MINUTE\n", ""))
    db_path = tmp_path / "test.db"
    now_ts = 5 * 86_400 + 2 * 3_600
    _seed_blocked_dca_scenario(db_path, now_ts)
    monkeypatch.setattr(cli_module.time, "time", lambda: now_ts)
    runner = CliRunner()

    result = runner.invoke(
        cli,
        [
            "--db", str(db_path),
            "--config", str(config_path),
            "agent", "--loop", "--max-cycles", "1", "--interval", "0",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "blocked=1" in result.output






# -- kill / resume ----------------------------------------------------------------------------


def test_kill_engages_kill_switch_no_passphrase_needed(tmp_path):
    db_path = tmp_path / "test.db"
    runner = CliRunner()

    result = runner.invoke(cli, ["--db", str(db_path), "kill"])

    assert result.exit_code == 0, result.output
    repo = _repo_at(db_path)
    assert repo.get_state("kill_switch") is True


def test_resume_without_passphrase_is_refused(tmp_path):
    db_path = tmp_path / "test.db"
    runner = CliRunner()
    runner.invoke(cli, ["--db", str(db_path), "kill"])

    result = runner.invoke(cli, ["--db", str(db_path), "resume"])

    assert result.exit_code != 0
    repo = _repo_at(db_path)
    assert repo.get_state("kill_switch", default=True) is True



def test_resume_disengages_when_confirmed(tmp_path, monkeypatch):
    _at_a_terminal(monkeypatch)
    db_path = tmp_path / "test.db"
    runner = CliRunner()
    runner.invoke(cli, ["--db", str(db_path), "kill"])

    result = runner.invoke(
        cli,
        [
            "--db", str(db_path),
                        "resume",
        ],
        input="yes\n",
    )

    assert result.exit_code == 0, result.output
    repo = _repo_at(db_path)
    assert repo.get_state("kill_switch") is False


# -- monitor ----------------------------------------------------------------------------------


def test_monitor_single_poll_needs_no_passphrase(tmp_path, valid_config_path, monkeypatch):
    monkeypatch.setattr(cli_module, "_build_broker", lambda config: FakeBroker())
    db_path = tmp_path / "test.db"
    runner = CliRunner()

    result = runner.invoke(
        cli,
        [
            "--db", str(db_path),
            "--config", str(valid_config_path),
                        "monitor",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.output.count("polled") == 1


def test_monitor_loop_bounded_by_max_cycles(tmp_path, valid_config_path, monkeypatch):
    monkeypatch.setattr(cli_module, "_build_broker", lambda config: FakeBroker())
    db_path = tmp_path / "test.db"
    runner = CliRunner()

    result = runner.invoke(
        cli,
        [
            "--db", str(db_path),
            "--config", str(valid_config_path),
            "monitor", "--loop", "--max-cycles", "2", "--interval", "0",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.output.count("polled") == 2


# -- rules ------------------------------------------------------------------------------------


def test_rules_list_empty(tmp_path):
    db_path = tmp_path / "test.db"
    runner = CliRunner()

    result = runner.invoke(cli, ["--db", str(db_path), "rules", "list"])

    assert result.exit_code == 0
    assert "no rules found" in result.output


def test_rules_list_shows_rule_needs_no_passphrase(tmp_path):
    db_path = tmp_path / "test.db"
    repo = _repo_at(db_path)
    repo.insert_rule("pullback_continuation", {"product_id": "BTC-USD"}, status="candidate")
    runner = CliRunner()

    result = runner.invoke(cli, ["--db", str(db_path), "rules", "list"])

    assert result.exit_code == 0
    assert "pullback_continuation" in result.output
    assert "candidate" in result.output


def test_rules_backtest_with_no_candles_reports_zero_trades(tmp_path):
    db_path = tmp_path / "test.db"
    repo = _repo_at(db_path)
    rule_id = repo.insert_rule("pullback_continuation", {"product_id": "BTC-USD"})
    runner = CliRunner()

    result = runner.invoke(cli, ["--db", str(db_path), "rules", "backtest", str(rule_id)])

    assert result.exit_code == 0, result.output
    assert "n_trades=0" in result.output


def test_rules_backtest_states_the_fee_rate_it_priced_fills_at(tmp_path, valid_config_path):
    """The output line must name the fee it used.

    This is the half of #247 that matters most and the half that keeps mattering: a printed
    `profit_factor` with no fee beside it is unfalsifiable by its reader. Every number in
    `docs/experiments/` predating this line was maker-priced and said nothing about it, which
    is precisely how a 2x cost error survived in a shipped gate. The rate travels WITH the
    result from here on.
    """
    db_path = tmp_path / "test.db"
    repo = _repo_at(db_path)
    rule_id = repo.insert_rule("pullback_continuation", {"product_id": "BTC-USD"})
    runner = CliRunner()

    result = runner.invoke(
        cli,
        ["--db", str(db_path), "--config", str(valid_config_path), "rules", "backtest",
         str(rule_id)],
    )

    assert result.exit_code == 0, result.output
    assert "fee_pct=1.2000%" in result.output
    assert "taker" in result.output


def test_rules_backtest_prices_fills_at_the_configs_taker_rate(tmp_path, write_config):
    """The rate comes from `fees.taker_pct`, not from a constant that merely happens to agree.

    A deployment that edits `fees.taker_pct` (a different Coinbase volume tier, a different
    venue) must see the backtest follow it. Asserted with a rate no default anywhere in the
    tree uses, so passing this cannot be an accident of matching numbers.
    """
    config_path = write_config(
        VALID_CONFIG_YAML + "\nfees:\n  taker_pct: 0.03\n  maker_pct: 0.01\n"
    )
    db_path = tmp_path / "test.db"
    repo = _repo_at(db_path)
    rule_id = repo.insert_rule("pullback_continuation", {"product_id": "BTC-USD"})

    result = CliRunner().invoke(
        cli,
        ["--db", str(db_path), "--config", str(config_path), "rules", "backtest", str(rule_id)],
    )

    assert result.exit_code == 0, result.output
    assert "fee_pct=3.0000%" in result.output


def test_rules_backtest_unknown_rule_id_errors(tmp_path):
    db_path = tmp_path / "test.db"
    _repo_at(db_path)
    runner = CliRunner()

    result = runner.invoke(cli, ["--db", str(db_path), "rules", "backtest", "999"])

    assert result.exit_code != 0


def test_rules_promote_stays_when_floor_not_cleared(tmp_path, valid_config_path):
    db_path = tmp_path / "test.db"
    repo = _repo_at(db_path)
    rule_id = repo.insert_rule("pullback_continuation", {"product_id": "BTC-USD"})
    runner = CliRunner()

    result = runner.invoke(
        cli,
        [
            "--db", str(db_path),
            "--config", str(valid_config_path),
            "rules", "promote", str(rule_id),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "status -> candidate" in result.output


# -- rules promote --force (funded paper-forward: un-gated lifecycle step) ------------------


def test_rules_promote_force_advances_candidate_to_paper_without_a_passing_backtest(tmp_path):
    """A low-frequency trend-follower's backtest can never clear the `min_trades=100` floor,
    yet the whole point of a paper-forward is to accrue the out-of-sample trades the backtest
    can't -- `--force` advances the lifecycle step directly, no backtest/gate involved. (0
    candles here stands in for "backtest that could never reach the floor".)"""
    db_path = tmp_path / "test.db"
    repo = _repo_at(db_path)
    rule_id = repo.insert_rule("pullback_continuation", {"product_id": "BTC-USD"})
    runner = CliRunner()

    result = runner.invoke(
        cli, ["--db", str(db_path), "rules", "promote", str(rule_id), "--force"]
    )

    assert result.exit_code == 0, result.output
    assert "status -> paper" in result.output
    row = {r["id"]: r for r in repo.get_rules()}[rule_id]
    assert row["status"] == "paper"


def test_rules_promote_force_advances_paper_to_live(tmp_path):
    db_path = tmp_path / "test.db"
    repo = _repo_at(db_path)
    rule_id = repo.insert_rule("dca", {"product_id": "BTC-USD"}, status="paper")
    runner = CliRunner()

    result = runner.invoke(
        cli, ["--db", str(db_path), "rules", "promote", str(rule_id), "--force"]
    )

    assert result.exit_code == 0, result.output
    assert "status -> live" in result.output
    row = {r["id"]: r for r in repo.get_rules()}[rule_id]
    assert row["status"] == "live"


def test_rules_promote_force_on_live_rule_is_a_noop(tmp_path):
    db_path = tmp_path / "test.db"
    repo = _repo_at(db_path)
    rule_id = repo.insert_rule("dca", {"product_id": "BTC-USD"}, status="live")
    runner = CliRunner()

    result = runner.invoke(
        cli, ["--db", str(db_path), "rules", "promote", str(rule_id), "--force"]
    )

    assert result.exit_code == 0, result.output
    assert "nothing to promote" in result.output.lower()
    row = {r["id"]: r for r in repo.get_rules()}[rule_id]
    assert row["status"] == "live"


def test_rules_promote_force_on_disabled_rule_is_a_noop(tmp_path):
    db_path = tmp_path / "test.db"
    repo = _repo_at(db_path)
    rule_id = repo.insert_rule("dca", {"product_id": "BTC-USD"}, status="disabled")
    runner = CliRunner()

    result = runner.invoke(
        cli, ["--db", str(db_path), "rules", "promote", str(rule_id), "--force"]
    )

    assert result.exit_code == 0, result.output
    assert "nothing to promote" in result.output.lower()
    row = {r["id"]: r for r in repo.get_rules()}[rule_id]
    assert row["status"] == "disabled"


def test_rules_promote_force_prints_a_loud_bypass_warning(tmp_path):
    db_path = tmp_path / "test.db"
    repo = _repo_at(db_path)
    rule_id = repo.insert_rule("pullback_continuation", {"product_id": "BTC-USD"})
    runner = CliRunner()

    result = runner.invoke(
        cli, ["--db", str(db_path), "rules", "promote", str(rule_id), "--force"]
    )

    assert result.exit_code == 0, result.output
    assert "bypass" in result.output.lower()


def test_rules_promote_without_force_still_gates_on_the_backtest(tmp_path, valid_config_path):
    """Unchanged non-force behavior: no candles -> the backtest can't clear the floor -> the
    rule stays `candidate`, exactly as `test_rules_promote_stays_when_floor_not_cleared` covers."""
    db_path = tmp_path / "test.db"
    repo = _repo_at(db_path)
    rule_id = repo.insert_rule("pullback_continuation", {"product_id": "BTC-USD"})
    runner = CliRunner()

    result = runner.invoke(
        cli,
        [
            "--db", str(db_path),
            "--config", str(valid_config_path),
            "rules", "promote", str(rule_id),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "status -> candidate" in result.output
    row = {r["id"]: r for r in repo.get_rules()}[rule_id]
    assert row["status"] == "candidate"


def test_rules_promote_reports_that_the_overfitting_check_did_not_run(
    tmp_path, valid_config_path
):
    """Without `--pbo-session` the G4 check cannot run, and the output SAYS SO.

    The visible half of #247. Before it, `rules promote` printed only `status -> X` and an
    operator had no way to tell that the overfitting gate had never been consulted -- the gate
    was dormant and the output was indistinguishable from one where it had passed.
    """
    db_path = tmp_path / "test.db"
    repo = _repo_at(db_path)
    rule_id = repo.insert_rule("pullback_continuation", {"product_id": "BTC-USD"})

    result = CliRunner().invoke(
        cli,
        ["--db", str(db_path), "--config", str(valid_config_path),
         "rules", "promote", str(rule_id)],
    )

    assert result.exit_code == 0, result.output
    assert "overfitting check = not_run" in result.output
    assert "NOT RUN" in result.output
    assert "status -> candidate" in result.output


def test_rules_promote_errors_rather_than_downgrading_an_unusable_pbo_session(
    tmp_path, valid_config_path
):
    """Asking for the check and not getting one is an ERROR, not a quiet "not run".

    The two states must stay distinguishable: not asking is an operator choice, whereas asking
    against an empty or `series_missing` ledger means the evidence the operator believes exists
    does not. Collapsing the second into the first would hide a broken ledger behind a routine
    message -- the same failure mode as the dormant gate itself.
    """
    db_path = tmp_path / "test.db"
    repo = _repo_at(db_path)
    rule_id = repo.insert_rule("pullback_continuation", {"product_id": "BTC-USD"})

    result = CliRunner().invoke(
        cli,
        ["--db", str(db_path), "--config", str(valid_config_path),
         "rules", "promote", str(rule_id), "--pbo-session", "no-such-session"],
    )

    assert result.exit_code != 0
    assert "no usable trial columns" in result.output


# -- rules promote: cross-product pooling of min_trades (#338) ------------------
#
# The gate's unit of evaluation, not its floors: the sample-size axis may be cleared
# by the same parameters' pooled PAPER evidence on other products, discounted by a
# diversity floor. The command must show BOTH readings -- the per-rule number and the
# pooled census -- so the operator approving the promotion sees which path carried it.


def _pbo_pass():
    """A clean CSCV result, so these tests exercise the SAMPLE-SIZE axis without the
    overfitting axis also blocking (its wiring is covered by its own tests above)."""
    from keel.research.cscv import PBOResult

    return PBOResult(
        pbo=Decimal("0.01"),
        n_combinations=20,
        n_columns=12,
        n_blocks=16,
        rows_used=800,
        rows_dropped=0,
        logits=[],
        is_performance=[],
        oos_performance=[],
        degradation_slope=Decimal("-0.2"),
    )


def test_rules_promote_reports_both_readings_and_promotes_via_the_pooled_path(
    tmp_path, valid_config_path, monkeypatch
):
    """A rule with 16 of its own trades and 7 same-parameter paper siblings promotes,
    and the output names BOTH readings: per-rule n, pooled n, and the diversity census.

    The backtest and PBO seams are faked (each has its own owning tests): this test is
    about what the COMMAND counts, decides, and prints. BTC-USD's reading fails the
    per-rule floors outright (16 trades, 25% win rate, negative expectancy); the seven
    siblings' pooled reading clears everything -- pooled n 128 across 8 products, 8
    products each >= 10 trades, pooled win rate 74/128 -- so the promotion carries on
    the pooled path and says so.
    """
    from keel.commands import rules as rules_cmd
    from keel.strategy.backtest import BacktestResult

    db_path = tmp_path / "test.db"
    repo = _repo_at(db_path)
    rule_id = repo.insert_rule("pullback_continuation", {"product_id": "BTC-USD"})
    for product in ("ETH-USD", "SOL-USD", "ADA-USD", "XLM-USD", "PAXG-USD", "LTC-USD", "DOGE-USD"):
        repo.insert_rule("pullback_continuation", {"product_id": product}, status="paper")

    def fake_backtest(rule, candles, **kwargs):
        return BacktestResult(
            trades=[],
            n_trades=16,
            win_rate=0.25 if rule.product_id == "BTC-USD" else 0.625,
            avg_win=Decimal("30"),
            avg_loss=Decimal("-10"),
            expectancy=Decimal("-2") if rule.product_id == "BTC-USD" else Decimal("14"),
            profit_factor=Decimal("2"),
            max_drawdown=Decimal("50"),
            max_losing_streak=4,
            avg_mfe=Decimal("20"),
            avg_mae=Decimal("8"),
        )

    monkeypatch.setattr(rules_cmd.backtest_mod, "backtest", fake_backtest)
    monkeypatch.setattr(rules_cmd, "_load_pbo", lambda ctx, session, blocks: _pbo_pass())

    result = CliRunner().invoke(
        cli,
        ["--db", str(db_path), "--config", str(valid_config_path),
         "rules", "promote", str(rule_id)],
    )

    assert result.exit_code == 0, result.output
    assert "per-rule n_trades=16" in result.output
    assert "pooled n_trades=128 across 8 products" in result.output
    assert "pooled census" in result.output
    assert "8 products contribute" in result.output
    assert "BTC-USD=16" in result.output and "DOGE-USD=16" in result.output
    assert "status -> paper" in result.output
    row = {r["id"]: r for r in repo.get_rules()}[rule_id]
    assert row["status"] == "paper"


def test_rules_promote_names_the_diversity_failure_when_the_pool_is_too_narrow(
    tmp_path, valid_config_path, monkeypatch
):
    """4 products of 30 trades each: the pooled total clears 100, the diversity floor
    (5 products) does not, and the failure reason says which path and which axis."""
    from keel.commands import rules as rules_cmd
    from keel.strategy.backtest import BacktestResult

    db_path = tmp_path / "test.db"
    repo = _repo_at(db_path)
    rule_id = repo.insert_rule("pullback_continuation", {"product_id": "BTC-USD"})
    for product in ("ETH-USD", "SOL-USD", "ADA-USD"):
        repo.insert_rule("pullback_continuation", {"product_id": product}, status="paper")

    def fake_backtest(rule, candles, **kwargs):
        return BacktestResult(
            trades=[],
            n_trades=30,
            win_rate=0.6,
            avg_win=Decimal("30"),
            avg_loss=Decimal("-10"),
            expectancy=Decimal("14"),
            profit_factor=Decimal("2"),
            max_drawdown=Decimal("50"),
            max_losing_streak=4,
            avg_mfe=Decimal("20"),
            avg_mae=Decimal("8"),
        )

    monkeypatch.setattr(rules_cmd.backtest_mod, "backtest", fake_backtest)
    monkeypatch.setattr(rules_cmd, "_load_pbo", lambda ctx, session, blocks: _pbo_pass())

    result = CliRunner().invoke(
        cli,
        ["--db", str(db_path), "--config", str(valid_config_path),
         "rules", "promote", str(rule_id)],
    )

    assert result.exit_code == 0, result.output
    assert "pooled n_trades=120 across 4 products" in result.output
    assert "pooled diversity 4 products < required 5" in result.output
    assert "status -> candidate" in result.output


def test_rules_promote_with_no_siblings_prints_no_pooled_reading(
    tmp_path, valid_config_path
):
    """Default behavior for a single-product promotion is unchanged: no siblings in the
    table means no pooled reading in the output -- the per-rule decision, alone, exactly
    as before #338."""
    db_path = tmp_path / "test.db"
    repo = _repo_at(db_path)
    rule_id = repo.insert_rule("pullback_continuation", {"product_id": "BTC-USD"})

    result = CliRunner().invoke(
        cli,
        ["--db", str(db_path), "--config", str(valid_config_path),
         "rules", "promote", str(rule_id)],
    )

    assert result.exit_code == 0, result.output
    assert "pooled" not in result.output
    assert "status -> candidate" in result.output


def test_rules_demote_steps_back_one_stage(tmp_path):
    db_path = tmp_path / "test.db"
    repo = _repo_at(db_path)
    rule_id = repo.insert_rule("dca", {"product_id": "BTC-USD"}, status="live")
    runner = CliRunner()

    result = runner.invoke(cli, ["--db", str(db_path), "rules", "demote", str(rule_id)])

    assert result.exit_code == 0, result.output
    assert "status -> paper" in result.output
    row = {r["id"]: r for r in repo.get_rules()}[rule_id]
    assert row["status"] == "paper"


def test_rules_disable_sets_terminal_status(tmp_path):
    db_path = tmp_path / "test.db"
    repo = _repo_at(db_path)
    rule_id = repo.insert_rule("dca", {"product_id": "BTC-USD"})
    runner = CliRunner()

    result = runner.invoke(cli, ["--db", str(db_path), "rules", "disable", str(rule_id)])

    assert result.exit_code == 0, result.output
    row = {r["id"]: r for r in repo.get_rules()}[rule_id]
    assert row["status"] == "disabled"


def test_rules_enable_restores_a_disabled_rule_to_candidate(tmp_path):
    """`enable` is the inverse of `disable`'s WRITE but not of its effect: nothing about the
    prior status was recorded, so the rule comes back at `candidate` -- the bottom of the
    ladder -- and the output must print the path onward (promote, with --force named for the
    paper-forwards that can never reach the min_trades floor)."""
    db_path = tmp_path / "test.db"
    repo = _repo_at(db_path)
    rule_id = repo.insert_rule("dca", {"product_id": "BTC-USD"}, status="disabled")
    runner = CliRunner()

    result = runner.invoke(cli, ["--db", str(db_path), "rules", "enable", str(rule_id)])

    assert result.exit_code == 0, result.output
    assert "status -> candidate" in result.output
    assert "rules promote" in result.output
    assert "--force" in result.output
    row = {r["id"]: r for r in repo.get_rules()}[rule_id]
    assert row["status"] == "candidate"


def test_rules_enable_on_a_non_disabled_rule_is_an_error(tmp_path):
    db_path = tmp_path / "test.db"
    repo = _repo_at(db_path)
    rule_id = repo.insert_rule("dca", {"product_id": "BTC-USD"}, status="live")
    runner = CliRunner()

    result = runner.invoke(cli, ["--db", str(db_path), "rules", "enable", str(rule_id)])

    assert result.exit_code != 0
    assert "Error" in result.output
    assert "not disabled" in result.output
    # Refused, not partially applied: the rule keeps the status it had.
    row = {r["id"]: r for r in repo.get_rules()}[rule_id]
    assert row["status"] == "live"


def test_rules_disable_then_enable_round_trip_lands_at_candidate(tmp_path):
    """Documents the round-trip's one-way ratchet: a rule disabled from `live` re-enables at
    `candidate`, NOT at `live` -- `disable` records no prior status to restore, so re-entry to
    the trading set is a promotion decision, not an undo. What #339's re-enable of the
    disabled paper DCA twins depends on."""
    db_path = tmp_path / "test.db"
    repo = _repo_at(db_path)
    rule_id = repo.insert_rule("dca", {"product_id": "BTC-USD"}, status="live")
    runner = CliRunner()

    disabled = runner.invoke(cli, ["--db", str(db_path), "rules", "disable", str(rule_id)])
    assert disabled.exit_code == 0, disabled.output
    enabled = runner.invoke(cli, ["--db", str(db_path), "rules", "enable", str(rule_id)])
    assert enabled.exit_code == 0, enabled.output

    row = {r["id"]: r for r in repo.get_rules()}[rule_id]
    assert row["status"] == "candidate"


# -- rules seed (Issue #81 -- the `rules` table starts empty, seed candidates from the built-in
# `RULE_REGISTRY` so the engine has something to trade) --------------------------------------


def test_rules_seed_populates_products_times_kinds(tmp_path, valid_config_path):
    db_path = tmp_path / "test.db"
    repo = _repo_at(db_path)
    runner = CliRunner()

    result = runner.invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "rules", "seed"]
    )

    assert result.exit_code == 0, result.output
    rows = repo.get_rules("candidate")
    # valid_config_path's allowlist is BTC/ETH/PAXG (3 products) x all of RULE_REGISTRY (N kinds).
    assert len(rows) == 3 * len(RULE_REGISTRY)
    assert f"seeded={3 * len(RULE_REGISTRY)} skipped=0" in result.output


def test_rules_seed_is_idempotent(tmp_path, valid_config_path):
    db_path = tmp_path / "test.db"
    repo = _repo_at(db_path)
    runner = CliRunner()
    args = ["--db", str(db_path), "--config", str(valid_config_path), "rules", "seed"]

    first = runner.invoke(cli, args)
    assert first.exit_code == 0, first.output

    second = runner.invoke(cli, args)

    assert second.exit_code == 0, second.output
    assert f"seeded=0 skipped={3 * len(RULE_REGISTRY)}" in second.output
    assert len(repo.get_rules()) == 3 * len(RULE_REGISTRY)


def test_rules_seed_force_reseeds_even_when_present(tmp_path, valid_config_path):
    db_path = tmp_path / "test.db"
    repo = _repo_at(db_path)
    runner = CliRunner()
    args = ["--db", str(db_path), "--config", str(valid_config_path), "rules", "seed"]

    first = runner.invoke(cli, args)
    assert first.exit_code == 0, first.output

    second = runner.invoke(cli, [*args, "--force"])

    assert second.exit_code == 0, second.output
    assert f"seeded={3 * len(RULE_REGISTRY)} skipped=0" in second.output
    assert len(repo.get_rules()) == 2 * 3 * len(RULE_REGISTRY)


def test_rules_seed_respects_products_and_kinds_options(tmp_path, valid_config_path):
    # `--config` is passed even though this is not a config test: `rules seed` loads config
    # unconditionally (it needs `settlement_currencies` to validate `--products`), so without it
    # the default `config.yaml` resolves against the CURRENT WORKING DIRECTORY and this passes
    # only because pytest happens to run from the repo root. See tests/test_init_and_seed.py.
    db_path = tmp_path / "test.db"
    repo = _repo_at(db_path)
    runner = CliRunner()

    result = runner.invoke(
        cli,
        [
            "--db", str(db_path),
            "--config", str(valid_config_path),
            "rules", "seed",
            "--products", "BTC-USD",
            "--kinds", "dca",
        ],
    )

    assert result.exit_code == 0, result.output
    rows = repo.get_rules()
    assert len(rows) == 1
    assert rows[0]["kind"] == "dca"
    assert rows[0]["params"]["product_id"] == "BTC-USD"


def test_rules_seed_unknown_kind_errors(tmp_path):
    db_path = tmp_path / "test.db"
    runner = CliRunner()

    result = runner.invoke(
        cli,
        [
            "--db", str(db_path),
            "rules", "seed",
            "--products", "BTC-USD",
            "--kinds", "not_a_real_kind",
        ],
    )

    assert result.exit_code != 0


def test_rules_seed_rows_round_trip_through_build_rule(tmp_path, valid_config_path):
    db_path = tmp_path / "test.db"
    repo = _repo_at(db_path)
    runner = CliRunner()

    result = runner.invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "rules", "seed"]
    )

    assert result.exit_code == 0, result.output
    rows = repo.get_rules()
    assert rows
    for row in rows:
        rule = _build_rule(row)
        assert rule.product_id == row["params"]["product_id"]


def test_rules_seed_needs_no_passphrase(tmp_path, valid_config_path):
    db_path = tmp_path / "test.db"
    runner = CliRunner()

    result = runner.invoke(
        cli,
        [
            "--db", str(db_path),
            "--config", str(valid_config_path),
            "rules", "seed",
            "--products", "BTC-USD",
            "--kinds", "dca",
        ],
    )

    assert result.exit_code == 0, result.output


# -- pnl --------------------------------------------------------------------------------------


def test_pnl_asset_report_needs_no_passphrase(tmp_path):
    db_path = tmp_path / "test.db"
    repo = _repo_at(db_path)
    repo.upsert_transaction(
        dict(
            coinbase_id="tx-1",
            source="csv_import",
            type="Buy",
            asset="BTC",
            ts=1700000000,
            qty=Decimal("1"),
            price=Decimal("100"),
            subtotal=None,
            total=None,
            fees=Decimal("0"),
            notes=None,
            rule_id=None,
            order_id=None,
        )
    )
    runner = CliRunner()

    result = runner.invoke(cli, ["--db", str(db_path), "pnl", "--asset", "BTC"])

    assert result.exit_code == 0, result.output
    assert "realized=0" in result.output
    assert "open_qty=1" in result.output
    assert "avg_cost=100" in result.output


def test_pnl_with_mark_reports_unrealized(tmp_path):
    db_path = tmp_path / "test.db"
    repo = _repo_at(db_path)
    repo.upsert_transaction(
        dict(
            coinbase_id="tx-1",
            source="csv_import",
            type="Buy",
            asset="BTC",
            ts=1700000000,
            qty=Decimal("1"),
            price=Decimal("100"),
            subtotal=None,
            total=None,
            fees=Decimal("0"),
            notes=None,
            rule_id=None,
            order_id=None,
        )
    )
    runner = CliRunner()

    result = runner.invoke(
        cli, ["--db", str(db_path), "pnl", "--asset", "BTC", "--mark", "BTC=150"]
    )

    assert result.exit_code == 0, result.output
    assert "unrealized=50" in result.output


def test_pnl_overall_report(tmp_path):
    db_path = tmp_path / "test.db"
    runner = CliRunner()

    result = runner.invoke(cli, ["--db", str(db_path), "pnl"])

    assert result.exit_code == 0, result.output
    assert "total realized P&L:" in result.output


# -- simulate -------------------------------------------------------------------------------


def _seed_candles_for_allowlist(repo: Repository, now_ts: int) -> None:
    """A tiny, deterministic candle set for the default `config.yaml` allowlist
    (BTC-USD/ETH-USD/PAXG-USD) -- just enough for `simulate --no-fetch` to run end to end
    without ever touching the network."""
    hour = now_ts - (now_ts % 3600)
    for product in ("BTC-USD", "ETH-USD", "PAXG-USD"):
        hourly = [
            Candle(
                ts=hour - i * 3600,
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100"),
                volume=Decimal("1"),
            )
            for i in range(72, -1, -1)
        ]
        daily = [
            Candle(
                ts=hour - i * 86400,
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100"),
                volume=Decimal("1"),
            )
            for i in range(5, -1, -1)
        ]
        repo.upsert_candles(product, Granularity.ONE_HOUR, hourly)
        repo.upsert_candles(product, Granularity.ONE_DAY, daily)


def test_simulate_no_fetch_produces_report_and_never_touches_network(tmp_path, monkeypatch):
    db_path = tmp_path / "sim.db"
    out_path = tmp_path / "report.md"
    repo = _repo_at(db_path)
    _seed_candles_for_allowlist(repo, int(time.time()))

    def _boom(config):  # pragma: no cover -- only invoked if the test fails its own contract
        raise AssertionError("_build_broker must never be called under --no-fetch")

    monkeypatch.setattr(cli_module, "_build_broker", _boom)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--db",
            str(db_path),
            "simulate",
            "--no-fetch",
            "--years",
            "1",
            "--out",
            str(out_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert out_path.exists()
    report_text = out_path.read_text()
    assert report_text.lower().count("verdict") >= 1
    assert "verdict:" in result.output.lower()
    assert "cached" in result.output.lower()
    assert "coverage" in result.output.lower()


def _seed_liquidity_stratified_candles(repo: Repository, now_ts: int) -> None:
    """The #259 fixture: three allowlist products at deliberately different liquidity, so the
    simulate path's per-product slippage has something to scale from.

    BTC's daily bars carry volume*close == 500,000,000 == the mapping's anchor (-> the 5bp
    floor); PAXG's carry 100 (-> the 50bp cap); ETH has NO daily bars cached at all (-> the
    flat fallback, flagged). Hourly bars exist for all three so the account pass runs.
    """
    hour = now_ts - (now_ts % 3600)

    def _series(volume: str, bars: int, step: int) -> list[Candle]:
        return [
            Candle(
                ts=hour - i * step,
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100"),
                volume=Decimal(volume),
            )
            for i in range(bars - 1, -1, -1)
        ]

    for product in ("BTC-USD", "ETH-USD", "PAXG-USD"):
        repo.upsert_candles(product, Granularity.ONE_HOUR, _series("1", 73, 3600))
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _series("5000000", 6, 86400))
    repo.upsert_candles("PAXG-USD", Granularity.ONE_DAY, _series("1", 6, 86400))
    # ETH-USD: hourly only -- deliberately no daily bars.


def test_simulate_reports_per_product_slippage_beside_the_results(tmp_path, monkeypatch):
    """#259: the simulate path prints -- and writes into the report -- the per-product slippage
    its edge-table numbers were actually priced at, with fallback products flagged.

    A profit factor printed without its assumed slippage has the same problem a profit factor
    printed without its fee rate had (#247): the reader cannot check the number.
    """
    # The flat rate simulate's dollar sections use is ALIASED to the engine's floor, not a
    # repeated literal: the report asserts those sections cost "the flat SLIPPAGE_FLOOR_PCT
    # per leg", and that claim must be structurally true -- a retuned floor with a stray
    # 0.0005 literal here would make the report's own cost statement silently false.
    assert cli_module._SIM_SLIPPAGE_PCT == SLIPPAGE_FLOOR_PCT
    db_path = tmp_path / "sim.db"
    out_path = tmp_path / "report.md"
    repo = _repo_at(db_path)
    _seed_liquidity_stratified_candles(repo, int(time.time()))
    monkeypatch.setattr(cli_module, "_build_broker", lambda config: None)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--db",
            str(db_path),
            "simulate",
            "--no-fetch",
            "--years",
            "1",
            "--out",
            str(out_path),
        ],
    )

    assert result.exit_code == 0, result.output
    # The terminal states what was assumed per product, from the cached candles alone.
    assert "slippage" in result.output.lower()
    assert "BTC-USD" in result.output and "5.0bp" in result.output
    assert "PAXG-USD" in result.output and "50.0bp" in result.output and "capped" in result.output
    assert "ETH-USD" in result.output and "fallback" in result.output
    assert "no liquidity statistic" in result.output

    # And the report file carries the same table beside the fee line.
    report_text = out_path.read_text()
    assert "1.2000%" in report_text
    assert "BTC-USD" in report_text and "5.0bp" in report_text
    assert "PAXG-USD" in report_text and "50.0bp" in report_text
    assert "ETH-USD" in report_text and "no liquidity statistic" in report_text
    assert "assumption, not a measurement" in report_text


def test_simulate_no_fetch_does_not_refetch_and_reuses_cached_db(tmp_path, monkeypatch):
    """A second `--no-fetch` run over the same persistent DB reuses the cached candles with no
    network -- `_build_broker` must never be constructed either time."""
    db_path = tmp_path / "sim.db"
    repo = _repo_at(db_path)
    _seed_candles_for_allowlist(repo, int(time.time()))

    monkeypatch.setattr(
        cli_module,
        "_build_broker",
        lambda config: (_ for _ in ()).throw(AssertionError("no network under --no-fetch")),
    )

    runner = CliRunner()
    for i in range(2):
        result = runner.invoke(
            cli,
            [
                "--db",
                str(db_path),
                "simulate",
                "--no-fetch",
                "--years",
                "1",
                "--out",
                str(tmp_path / f"report{i}.md"),
            ],
        )
        assert result.exit_code == 0, result.output


def test_simulate_artifact_flag_writes_html_next_to_markdown(tmp_path, monkeypatch):
    db_path = tmp_path / "sim.db"
    out_path = tmp_path / "report.md"
    repo = _repo_at(db_path)
    _seed_candles_for_allowlist(repo, int(time.time()))
    monkeypatch.setattr(cli_module, "_build_broker", lambda config: None)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--db",
            str(db_path),
            "simulate",
            "--no-fetch",
            "--years",
            "1",
            "--out",
            str(out_path),
            "--artifact",
        ],
    )

    assert result.exit_code == 0, result.output
    html_path = out_path.with_suffix(".html")
    assert out_path.exists()
    assert html_path.exists()
    html_text = html_path.read_text()
    assert "<svg" in html_text
    assert "http://" not in html_text
    assert "https://" not in html_text
    assert str(html_path) in result.output


def test_simulate_no_fetch_skip_within_cap_still_produces_over_cap_tier_table(
    tmp_path, monkeypatch
):
    """Issue #86: `--skip-within-cap` skips the extra throttled sim runs but still computes and
    renders the over-cap tier/fee overlay (cheap -- reuses the natural run already being done)."""
    db_path = tmp_path / "sim.db"
    out_path = tmp_path / "report.md"
    repo = _repo_at(db_path)
    _seed_candles_for_allowlist(repo, int(time.time()))

    def _boom(config):  # pragma: no cover -- only invoked if the test fails its own contract
        raise AssertionError("_build_broker must never be called under --no-fetch")

    monkeypatch.setattr(cli_module, "_build_broker", _boom)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--db",
            str(db_path),
            "simulate",
            "--no-fetch",
            "--skip-within-cap",
            "--years",
            "1",
            "--out",
            str(out_path),
        ],
    )

    assert result.exit_code == 0, result.output
    report_text = out_path.read_text()
    assert "Subscription tier & fee analysis" in report_text
    assert "Basic" in report_text
    assert "Preferred" in report_text
    assert "Premium" in report_text
    assert "Over cap" in report_text


def test_simulate_no_fetch_default_runs_full_tier_matrix(tmp_path, monkeypatch):
    """Without `--skip-within-cap` (the default), the tier/fee matrix includes within-cap rows
    too, from the extra throttled sim runs."""
    db_path = tmp_path / "sim.db"
    out_path = tmp_path / "report.md"
    repo = _repo_at(db_path)
    _seed_candles_for_allowlist(repo, int(time.time()))
    monkeypatch.setattr(
        cli_module,
        "_build_broker",
        lambda config: (_ for _ in ()).throw(AssertionError("no network under --no-fetch")),
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--db",
            str(db_path),
            "simulate",
            "--no-fetch",
            "--years",
            "1",
            "--out",
            str(out_path),
        ],
    )

    assert result.exit_code == 0, result.output
    report_text = out_path.read_text()
    assert "Subscription tier & fee analysis" in report_text
    assert "Within cap" in report_text
    assert "Over cap" in report_text


# -- insights (read-only promotion-gate + journal reporting; see tests/commands/test_insights.py)
# ----------------------------------------------------------------------------------------------


def test_insights_is_a_group_with_summary_and_journal_subcommands(tmp_path):
    runner = CliRunner()

    result = runner.invoke(cli, ["insights", "--help"])

    assert result.exit_code == 0, result.output
    assert "summary" in result.output
    assert "journal" in result.output


def test_loading_config_binds_the_venue_for_telemetry(tmp_path: Path) -> None:
    """Spec 10.2's `venue` field arrives from ONE binding at the CLI entry point.

    It is deliberately not passed into the ~26 `log_event` call sites: the engine is
    single-venue today, so threading a constant through every payload would mean revisiting
    all of them again the moment it isn't. This asserts the single binding actually happens on
    the real command path -- without it, every event silently loses `venue` and no unit test
    of `telemetry` would notice.
    """
    from keel_core import telemetry

    from keel.execution.guards import DEFAULT_VENUE

    assert telemetry.current_venue() is None

    result = CliRunner().invoke(
        cli, ["--db", str(tmp_path / "keel.db"), "subscription", "show"]
    )

    assert result.exit_code == 0, result.output
    assert telemetry.current_venue() == DEFAULT_VENUE


# -- operator overrides for the two circuit breakers ------------------------------------------


def _repo_at(db_path):
    conn = connect(str(db_path))
    migrate(conn)
    return Repository(conn)


def test_resume_entries_clears_an_armed_streak_halt(tmp_path, monkeypatch):
    """Rail 16's violation message tells the operator to run `keel resume-entries`. Until now
    that command did not exist, and a test merely asserted the message MENTIONED it -- pinning a
    promise nothing implemented.

    It is the only escape hatch: rail 16 reads `streak_halt_until` and never the threshold, so
    setting `max_consecutive_losses: 0` does NOT release an armed halt. Without this, an
    operator who mis-set the cooloff waits it out or edits sqlite by hand.
    """
    _at_a_terminal(monkeypatch)
    db_path = tmp_path / "test.db"
    repo = _repo_at(db_path)
    repo.set_state("streak_halt_until", 2_000_000_000)
    runner = CliRunner()

    result = runner.invoke(
        cli,
        ["--db", str(db_path),          "resume-entries"],
        input="yes\n",
    )

    assert result.exit_code == 0, result.output
    assert _repo_at(db_path).get_state("streak_halt_until") == 0



def test_reset_hwm_clears_the_equity_high_water_mark(tmp_path, monkeypatch):
    """Rail 11's high-water mark is MONOTONIC, so any bad equity write is permanent: a deposit
    ratchets it up and a later withdrawal then reads as a drawdown that never recovers. Without
    this command the only remedy is hand-editing sqlite."""
    _at_a_terminal(monkeypatch)
    db_path = tmp_path / "test.db"
    repo = _repo_at(db_path)
    repo.set_state("equity_high_water_mark", Decimal("15000"))
    repo.set_state("drawdown_total_pct", Decimal("0.33"))
    runner = CliRunner()

    result = runner.invoke(
        cli,
        ["--db", str(db_path),          "reset-hwm"],
        input="yes\n",
    )

    assert result.exit_code == 0, result.output
    after = _repo_at(db_path)
    assert after.get_state("equity_high_water_mark") is None
    assert after.get_state("drawdown_total_pct") == Decimal("0")


def test_record_flow_rebases_the_high_water_mark(tmp_path, monkeypatch):
    """A deposit is not profit and a withdrawal is not a loss, but equity is cash + positions so
    both move it. Declaring the flow keeps rail 11's drawdown measuring TRADING performance."""
    _at_a_terminal(monkeypatch)
    db_path = tmp_path / "test.db"
    repo = _repo_at(db_path)
    repo.set_state("equity_high_water_mark", Decimal("10000"))
    runner = CliRunner()

    result = runner.invoke(
        cli,
        ["--db", str(db_path),          "record-flow", "--amount", "5000"],
        input="yes\n",
    )

    assert result.exit_code == 0, result.output
    assert _repo_at(db_path).get_state("equity_high_water_mark") == Decimal("15000")


def test_record_flow_accepts_a_negative_amount_for_a_withdrawal(tmp_path, monkeypatch):
    _at_a_terminal(monkeypatch)
    db_path = tmp_path / "test.db"
    repo = _repo_at(db_path)
    repo.set_state("equity_high_water_mark", Decimal("15000"))
    runner = CliRunner()

    result = runner.invoke(
        cli,
        ["--db", str(db_path),          "record-flow", "--amount", "-5000"],
        input="yes\n",
    )

    assert result.exit_code == 0, result.output
    assert _repo_at(db_path).get_state("equity_high_water_mark") == Decimal("10000")



def test_record_flow_rejects_a_non_finite_amount(tmp_path, monkeypatch):
    """`Decimal("nan")` parses without raising. Written into the high-water mark it poisons it
    permanently: every later `equity > hwm` is False, so the HWM can never re-seed."""
    _at_a_terminal(monkeypatch)
    db_path = tmp_path / "test.db"
    repo = _repo_at(db_path)
    repo.set_state("equity_high_water_mark", Decimal("10000"))
    runner = CliRunner()

    result = runner.invoke(
        cli,
        ["--db", str(db_path),          "record-flow", "--amount", "nan"],
        input="yes\n",
    )

    assert result.exit_code != 0
    assert _repo_at(db_path).get_state("equity_high_water_mark") == Decimal("10000")


# -- keel migrate (schema-only, idempotent) ------------------------------------


def test_migrate_brings_a_fresh_db_up_to_head(tmp_path):
    """A fresh file has no schema at all: migrate must create it and report 0 -> HEAD."""
    from keel.data.db import SCHEMA_VERSION

    db = tmp_path / "fresh.db"
    result = CliRunner().invoke(cli, ["--db", str(db), "migrate"])
    assert result.exit_code == 0, result.output
    assert f"0 -> {SCHEMA_VERSION}" in result.output
    conn = connect(str(db))
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    assert int(row["version"]) == SCHEMA_VERSION


def test_migrate_is_idempotent(tmp_path):
    """Running it twice must be safe -- it is the command CI/an operator re-runs."""
    db = tmp_path / "twice.db"
    CliRunner().invoke(cli, ["--db", str(db), "migrate"])
    again = CliRunner().invoke(cli, ["--db", str(db), "migrate"])
    assert again.exit_code == 0, again.output
    assert "nothing to do" in again.output


def test_migrate_advances_a_downgraded_db(tmp_path):
    """The real job: an existing DB stamped below HEAD is stepped up to HEAD."""
    from keel.data.db import SCHEMA_VERSION

    db = tmp_path / "old.db"
    conn = connect(str(db))
    migrate(conn)
    conn.execute("UPDATE schema_version SET version = 1")
    conn.commit()

    result = CliRunner().invoke(cli, ["--db", str(db), "migrate"])
    assert result.exit_code == 0, result.output
    assert f"1 -> {SCHEMA_VERSION}" in result.output
    check = connect(str(db))
    assert int(check.execute("SELECT version FROM schema_version").fetchone()["version"]) == (
        SCHEMA_VERSION
    )


def test_migrate_honours_an_explicit_db_option(tmp_path):
    """--db targets a database directly, so it can point at wherever the DB lives."""
    from keel.data.db import SCHEMA_VERSION

    target = tmp_path / "explicit.db"
    result = CliRunner().invoke(cli, ["migrate", "--db", str(target)])
    assert result.exit_code == 0, result.output
    assert str(target) in result.output
    conn = connect(str(target))
    assert int(conn.execute("SELECT version FROM schema_version").fetchone()["version"]) == (
        SCHEMA_VERSION
    )


# -- halt-releasing commands: interactive confirmation, no passphrase ----------
#
# These four RE-PERMIT trading after a safety halt. They keep a human gate even when autonomous
# mode is on -- "trade without asking me" and "un-stick your own drawdown breaker" are different
# powers, and a breaker that can reset itself is not a breaker.
#
# There is deliberately NO env-var/flag override for the TTY check: any such seam would be
# settable from cron and would defeat the fail-closed. Tests patch the predicate instead.

_HALT_COMMANDS = (
    ["resume"],
    ["resume-entries"],
    ["record-flow", "--amount", "500"],
    ["reset-hwm"],
)


def _at_a_terminal(monkeypatch, yes: bool = True) -> None:
    # The TTY predicate lives in keel.commands._common; _require_interactive_confirmation
    # calls it there, so patch it at its definition (see that module's docstring).
    monkeypatch.setattr("keel.commands._common._is_interactive", lambda: yes)


def test_halt_commands_proceed_on_a_typed_yes(tmp_path, monkeypatch):
    _at_a_terminal(monkeypatch)
    for args in _HALT_COMMANDS:
        db = tmp_path / f"{args[0]}-yes.db"
        _repo_at(db)
        result = CliRunner().invoke(cli, ["--db", str(db), *args], input="yes\n")
        assert result.exit_code == 0, f"{args}: {result.output}"


def test_halt_commands_abort_on_anything_other_than_yes(tmp_path, monkeypatch):
    """A bare 'y' is not enough -- these are rarer and heavier than an order confirmation."""
    _at_a_terminal(monkeypatch)
    for args in _HALT_COMMANDS:
        db = tmp_path / f"{args[0]}-no.db"
        _repo_at(db)
        result = CliRunner().invoke(cli, ["--db", str(db), *args], input="y\n")
        assert result.exit_code != 0, f"{args} should have aborted: {result.output}"
        assert "aborted" in result.output.lower()


def test_halt_commands_fail_closed_without_a_tty(tmp_path, monkeypatch):
    """A cron job or piped script must never be able to release a safety halt."""
    _at_a_terminal(monkeypatch, yes=False)
    for args in _HALT_COMMANDS:
        db = tmp_path / f"{args[0]}-notty.db"
        _repo_at(db)
        result = CliRunner().invoke(cli, ["--db", str(db), *args], input="yes\n")
        assert result.exit_code != 0, f"{args} should have refused off-TTY: {result.output}"
        assert "terminal" in result.output.lower()


def test_resume_actually_disengages_the_kill_switch_when_confirmed(tmp_path, monkeypatch):
    _at_a_terminal(monkeypatch)
    db = tmp_path / "resume.db"
    repo = _repo_at(db)
    repo.set_state("kill_switch", True)
    result = CliRunner().invoke(cli, ["--db", str(db), "resume"], input="yes\n")
    assert result.exit_code == 0, result.output
    assert _repo_at(db).get_state("kill_switch") is False


# -- keel autonomy ------------------------------------------------------------


def test_autonomy_is_off_by_default(tmp_path):
    db = tmp_path / "a.db"
    _repo_at(db)
    result = CliRunner().invoke(cli, ["--db", str(db), "autonomy", "show"])
    assert result.exit_code == 0, result.output
    assert "off" in result.output


def test_autonomy_on_requires_a_typed_yes_and_persists(tmp_path, monkeypatch, valid_config_path):
    _at_a_terminal(monkeypatch)
    db = tmp_path / "a.db"
    _repo_at(db)
    result = CliRunner().invoke(
        cli, ["--db", str(db), "--config", str(valid_config_path), "autonomy", "on"], input="yes\n"
    )
    assert result.exit_code == 0, result.output
    assert _repo_at(db).get_profile().autonomous is True


def test_autonomy_on_aborts_on_a_bare_y(tmp_path, monkeypatch, valid_config_path):
    _at_a_terminal(monkeypatch)
    db = tmp_path / "a.db"
    _repo_at(db)
    result = CliRunner().invoke(
        cli, ["--db", str(db), "--config", str(valid_config_path), "autonomy", "on"], input="y\n"
    )
    assert result.exit_code != 0
    assert _repo_at(db).get_profile().autonomous is False


def test_autonomy_on_refuses_without_a_terminal(tmp_path, monkeypatch, valid_config_path):
    """Arming unattended trading must not be scriptable."""
    _at_a_terminal(monkeypatch, yes=False)
    db = tmp_path / "a.db"
    _repo_at(db)
    result = CliRunner().invoke(
        cli, ["--db", str(db), "--config", str(valid_config_path), "autonomy", "on"], input="yes\n"
    )
    assert result.exit_code != 0
    assert "terminal" in result.output.lower()
    assert _repo_at(db).get_profile().autonomous is False


def test_autonomy_off_works_without_a_terminal(tmp_path, monkeypatch):
    """De-risking is never obstructed: this must work from cron, a pipe, anywhere."""
    _at_a_terminal(monkeypatch, yes=False)
    db = tmp_path / "a.db"
    repo = _repo_at(db)
    repo.set_autonomous(True, now_ts=1)

    result = CliRunner().invoke(cli, ["--db", str(db), "autonomy", "off"])

    assert result.exit_code == 0, result.output
    assert _repo_at(db).get_profile().autonomous is False


def test_autonomy_ON_does_not_let_halt_commands_skip_confirmation(tmp_path, monkeypatch):
    """THE invariant. 'Trade without asking me' and 'un-stick your own drawdown breaker' are
    different powers; a breaker that can reset itself is not a breaker."""
    _at_a_terminal(monkeypatch, yes=False)  # no terminal available
    for args in _HALT_COMMANDS:
        db = tmp_path / f"auto-{args[0]}.db"
        repo = _repo_at(db)
        repo.set_autonomous(True, now_ts=1)  # fully autonomous...

        result = CliRunner().invoke(cli, ["--db", str(db), *args], input="yes\n")

        assert result.exit_code != 0, f"{args} was released without a human: {result.output}"
        assert "terminal" in result.output.lower()


def test_withdrawals_attest_ENABLED_needs_a_terminal(tmp_path, monkeypatch):
    """Rail 17 halts ENTRIES when withdrawals are suspended/stale; `--enabled` releases that
    halt, so it is a halt-releasing command like the other four. Its old justification was that
    the confirm gate and the bypass-arm token sat in front of it -- with autonomy on, neither
    does, so a cron line could re-permit live entries with no human anywhere."""
    _at_a_terminal(monkeypatch, yes=False)
    db = tmp_path / "w.db"
    repo = _repo_at(db)
    repo.set_autonomous(True, now_ts=1)

    result = CliRunner().invoke(
        cli, ["--db", str(db), "withdrawals", "attest", "--enabled"], input="yes\n"
    )

    assert result.exit_code != 0, result.output
    assert "terminal" in result.output.lower()
    assert _repo_at(db).get_state("withdrawals_enabled") is None


def test_withdrawals_attest_SUSPENDED_is_ungated_and_needs_no_terminal(tmp_path, monkeypatch):
    """De-risking is never obstructed: suspending only ever halts entries."""
    _at_a_terminal(monkeypatch, yes=False)
    db = tmp_path / "w2.db"
    _repo_at(db)

    result = CliRunner().invoke(cli, ["--db", str(db), "withdrawals", "attest", "--suspended"])

    assert result.exit_code == 0, result.output
    assert _repo_at(db).get_state("withdrawals_enabled") is False


def test_withdrawals_attest_ENABLED_proceeds_on_a_typed_yes(tmp_path, monkeypatch):
    _at_a_terminal(monkeypatch)
    db = tmp_path / "w3.db"
    _repo_at(db)

    result = CliRunner().invoke(
        cli, ["--db", str(db), "withdrawals", "attest", "--enabled"], input="yes\n"
    )

    assert result.exit_code == 0, result.output
    assert _repo_at(db).get_state("withdrawals_enabled") is True


def test_autonomy_on_for_hours_sets_an_expiry_that_lapses(tmp_path, monkeypatch, valid_config_path):
    """A forgotten `autonomy on` should not be able to grant unattended trading forever."""
    _at_a_terminal(monkeypatch)
    db = tmp_path / "exp.db"
    _repo_at(db)
    result = CliRunner().invoke(
        cli,
        ["--db", str(db), "--config", str(valid_config_path), "autonomy", "on", "--for-hours", "1"],
        input="yes\n",
    )
    assert result.exit_code == 0, result.output
    profile = _repo_at(db).get_profile()
    assert profile.autonomous_until is not None
    assert profile.is_autonomous(profile.autonomous_until - 1) is True
    assert profile.is_autonomous(profile.autonomous_until) is False


def test_autonomy_on_without_for_hours_warns_that_it_never_lapses(
    tmp_path, monkeypatch, valid_config_path
):
    _at_a_terminal(monkeypatch)
    db = tmp_path / "noexp.db"
    _repo_at(db)
    result = CliRunner().invoke(
        cli, ["--db", str(db), "--config", str(valid_config_path), "autonomy", "on"], input="yes\n"
    )
    assert result.exit_code == 0, result.output
    assert "NO expiry" in result.output
    assert _repo_at(db).get_profile().autonomous_until is None


def test_autonomy_on_rejects_a_nonsensical_for_hours(tmp_path, monkeypatch, valid_config_path):
    """0/negative would write an already-lapsed row while claiming 'autonomy ON until ...';
    inf/nan/huge would overflow int() AFTER the operator had already typed yes."""
    _at_a_terminal(monkeypatch)
    for bad in ("0", "-5", "inf", "nan", "1e18"):
        db = tmp_path / f"bad-{bad}.db"
        _repo_at(db)
        result = CliRunner().invoke(
            cli,
            ["--db", str(db), "--config", str(valid_config_path),
             "autonomy", "on", "--for-hours", bad],
            input="yes\n",
        )
        assert result.exit_code != 0, f"--for-hours {bad} should be rejected: {result.output}"
        assert _repo_at(db).get_profile().autonomous is False


