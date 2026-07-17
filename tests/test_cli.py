"""Tests for keel.cli -- the CLI (P3 Task 9).

Every test drives the CLI through `click.testing.CliRunner` -- no live network, no live broker.
`FakeBroker` below duck-types `CoinbaseClient` (`get_candles`/`preview_order`/`place_order`/
`cancel_order`), modeled on `tests/test_agent.py::FakeBroker`; tests that need a broker monkeypatch
`keel.cli._build_broker` (the one seam that would otherwise construct a real, network-talking
`CoinbaseClient`) to return it instead.

Dangerous commands (`agent --bypass`, `resume`) are gated by `keel.security.authz`; read-only
commands (`db import`, `monitor`, `rules list`, `pnl`) are not and work with no `authz.json` on
disk at all.
"""

from __future__ import annotations

import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from click.testing import CliRunner

import keel.cli as cli_module
from keel.agent import RULE_REGISTRY, _build_rule
from keel.cli import DISCLAIMER, cli
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.security import authz
from keel.types import Candle, Granularity

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "transactions_dir"
PASSPHRASE = "correct-horse-battery-staple"


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

    def cancel_order(self, order_id: str) -> None:
        pass


# -- db import ------------------------------------------------------------------------------


def test_db_import_runs_importer_against_temp_db(tmp_path):
    db_path = tmp_path / "test.db"
    runner = CliRunner()

    result = runner.invoke(cli, ["--db", str(db_path), "db", "import", str(FIXTURES_DIR)])

    assert result.exit_code == 0, result.output
    assert "imported=" in result.output
    repo = _repo_at(db_path)
    assert len(repo.get_transactions()) > 0


def test_db_import_needs_no_passphrase(tmp_path):
    """Read-only command: works even though no --authz-path file exists at all."""
    db_path = tmp_path / "test.db"
    authz_path = tmp_path / "authz.json"
    runner = CliRunner()

    result = runner.invoke(
        cli,
        ["--db", str(db_path), "--authz-path", str(authz_path), "db", "import", str(FIXTURES_DIR)],
    )

    assert result.exit_code == 0, result.output
    assert not authz_path.exists()


# -- disclaimer -----------------------------------------------------------------------------


def test_disclaimer_shown_on_every_command(tmp_path):
    db_path = tmp_path / "test.db"
    runner = CliRunner()

    result = runner.invoke(cli, ["--db", str(db_path), "db", "import", str(FIXTURES_DIR)])

    assert DISCLAIMER in result.output


def test_disclaimer_shown_even_when_refused(tmp_path):
    db_path = tmp_path / "test.db"
    authz_path = tmp_path / "authz.json"
    runner = CliRunner()

    result = runner.invoke(cli, ["--db", str(db_path), "--authz-path", str(authz_path), "resume"])

    assert result.exit_code != 0
    assert DISCLAIMER in result.output


# -- agent --bypass gating --------------------------------------------------------------------


def test_agent_bypass_without_passphrase_is_refused(tmp_path, valid_config_path, monkeypatch):
    monkeypatch.setattr(cli_module, "_build_broker", lambda config: FakeBroker())
    db_path = tmp_path / "test.db"
    authz_path = tmp_path / "authz.json"
    runner = CliRunner()

    result = runner.invoke(
        cli,
        [
            "--db", str(db_path),
            "--config", str(valid_config_path),
            "--authz-path", str(authz_path),
            "agent", "--bypass",
        ],
    )

    assert result.exit_code != 0
    assert "denied" in result.output.lower()


def test_agent_bypass_with_wrong_passphrase_is_refused(tmp_path, valid_config_path, monkeypatch):
    monkeypatch.setattr(cli_module, "_build_broker", lambda config: FakeBroker())
    db_path = tmp_path / "test.db"
    authz_path = tmp_path / "authz.json"
    authz.set_passphrase(PASSPHRASE, path=str(authz_path))
    runner = CliRunner()

    result = runner.invoke(
        cli,
        [
            "--db", str(db_path),
            "--config", str(valid_config_path),
            "--authz-path", str(authz_path),
            "agent", "--bypass", "--passphrase", "wrong-passphrase",
        ],
    )

    assert result.exit_code != 0
    assert "denied" in result.output.lower()


def test_agent_bypass_with_correct_passphrase_proceeds(tmp_path, valid_config_path, monkeypatch):
    monkeypatch.setattr(cli_module, "_build_broker", lambda config: FakeBroker())
    db_path = tmp_path / "test.db"
    authz_path = tmp_path / "authz.json"
    authz.set_passphrase(PASSPHRASE, path=str(authz_path))
    runner = CliRunner()

    result = runner.invoke(
        cli,
        [
            "--db", str(db_path),
            "--config", str(valid_config_path),
            "--authz-path", str(authz_path),
            "agent", "--bypass", "--passphrase", PASSPHRASE,
        ],
    )

    # No `live` rules are configured and the kill-switch defaults to engaged, so `run_once`
    # fails closed immediately -- but crucially the authz gate let it get that far.
    assert result.exit_code == 0, result.output
    assert "skipped: kill_switch" in result.output


def test_agent_confirm_mode_needs_no_passphrase(tmp_path, valid_config_path, monkeypatch):
    monkeypatch.setattr(cli_module, "_build_broker", lambda config: FakeBroker())
    db_path = tmp_path / "test.db"
    authz_path = tmp_path / "authz.json"
    runner = CliRunner()

    result = runner.invoke(
        cli,
        [
            "--db", str(db_path),
            "--config", str(valid_config_path),
            "--authz-path", str(authz_path),
            "agent",
        ],
    )

    assert result.exit_code == 0, result.output
    assert not authz_path.exists()


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


# -- arm-bypass / disarm-bypass (Issue #60, bypass-arm hardening) ------------------------------


def test_arm_bypass_without_passphrase_is_refused(tmp_path, valid_config_path):
    db_path = tmp_path / "test.db"
    authz_path = tmp_path / "authz.json"
    runner = CliRunner()

    result = runner.invoke(
        cli,
        [
            "--db", str(db_path),
            "--config", str(valid_config_path),
            "--authz-path", str(authz_path),
            "arm-bypass",
        ],
    )

    assert result.exit_code != 0
    assert "denied" in result.output.lower()
    repo = _repo_at(db_path)
    assert repo.is_bypass_armed(now_ts=0) is False


def test_arm_bypass_with_wrong_passphrase_is_refused(tmp_path, valid_config_path):
    db_path = tmp_path / "test.db"
    authz_path = tmp_path / "authz.json"
    authz.set_passphrase(PASSPHRASE, path=str(authz_path))
    runner = CliRunner()

    result = runner.invoke(
        cli,
        [
            "--db", str(db_path),
            "--config", str(valid_config_path),
            "--authz-path", str(authz_path),
            "arm-bypass", "--passphrase", "wrong-passphrase",
        ],
    )

    assert result.exit_code != 0
    assert "denied" in result.output.lower()
    repo = _repo_at(db_path)
    assert repo.is_bypass_armed(now_ts=0) is False


def test_arm_bypass_with_correct_passphrase_arms(tmp_path, valid_config_path):
    db_path = tmp_path / "test.db"
    authz_path = tmp_path / "authz.json"
    authz.set_passphrase(PASSPHRASE, path=str(authz_path))
    runner = CliRunner()

    result = runner.invoke(
        cli,
        [
            "--db", str(db_path),
            "--config", str(valid_config_path),
            "--authz-path", str(authz_path),
            "arm-bypass", "--passphrase", PASSPHRASE,
        ],
    )

    assert result.exit_code == 0, result.output
    assert "armed" in result.output.lower()
    repo = _repo_at(db_path)
    # `valid_config_path`'s auto_trade.bypass_arm_ttl_sec is 3600 -- armed "now" is well inside.
    assert repo.is_bypass_armed(now_ts=int(time.time())) is True


def test_disarm_bypass_clears_the_token_no_passphrase_needed(tmp_path, valid_config_path):
    db_path = tmp_path / "test.db"
    authz_path = tmp_path / "authz.json"
    authz.set_passphrase(PASSPHRASE, path=str(authz_path))
    runner = CliRunner()
    runner.invoke(
        cli,
        [
            "--db", str(db_path),
            "--config", str(valid_config_path),
            "--authz-path", str(authz_path),
            "arm-bypass", "--passphrase", PASSPHRASE,
        ],
    )
    repo = _repo_at(db_path)
    assert repo.is_bypass_armed(now_ts=int(time.time())) is True

    result = runner.invoke(
        cli,
        [
            "--db", str(db_path),
            "--config", str(valid_config_path),
            "--authz-path", str(authz_path),
            "disarm-bypass",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "disarmed" in result.output.lower()
    repo = _repo_at(db_path)
    assert repo.is_bypass_armed(now_ts=int(time.time())) is False


def test_agent_bypass_without_arm_bypass_places_nothing_even_with_passphrase(
    tmp_path, valid_config_path, monkeypatch
):
    """The Issue #60 gap being closed: the CLI passphrase gate on `agent --bypass` alone is not
    enough -- without a separate `arm-bypass` call, `run_once` itself refuses to trade
    autonomously and the CLI surfaces that refusal."""
    monkeypatch.setattr(cli_module, "_build_broker", lambda config: FakeBroker())
    db_path = tmp_path / "test.db"
    authz_path = tmp_path / "authz.json"
    authz.set_passphrase(PASSPHRASE, path=str(authz_path))
    repo = _repo_at(db_path)
    repo.set_state("kill_switch", False)
    repo.insert_rule("dca", {"product_id": "BTC-USD"}, status="live")
    runner = CliRunner()

    result = runner.invoke(
        cli,
        [
            "--db", str(db_path),
            "--config", str(valid_config_path),
            "--authz-path", str(authz_path),
            "agent", "--bypass", "--passphrase", PASSPHRASE,
        ],
    )

    assert result.exit_code == 0, result.output
    assert "bypass" in result.output.lower()
    assert "not armed" in result.output.lower() or "refused" in result.output.lower()
    repo = _repo_at(db_path)
    assert repo.get_orders() == []


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
    authz_path = tmp_path / "authz.json"
    runner = CliRunner()
    runner.invoke(cli, ["--db", str(db_path), "kill"])

    result = runner.invoke(cli, ["--db", str(db_path), "--authz-path", str(authz_path), "resume"])

    assert result.exit_code != 0
    repo = _repo_at(db_path)
    assert repo.get_state("kill_switch", default=True) is True


def test_resume_with_wrong_passphrase_is_refused(tmp_path):
    db_path = tmp_path / "test.db"
    authz_path = tmp_path / "authz.json"
    authz.set_passphrase(PASSPHRASE, path=str(authz_path))
    runner = CliRunner()
    runner.invoke(cli, ["--db", str(db_path), "kill"])

    result = runner.invoke(
        cli,
        ["--db", str(db_path), "--authz-path", str(authz_path), "resume", "--passphrase", "nope"],
    )

    assert result.exit_code != 0
    repo = _repo_at(db_path)
    assert repo.get_state("kill_switch") is True


def test_resume_with_correct_passphrase_disengages(tmp_path):
    db_path = tmp_path / "test.db"
    authz_path = tmp_path / "authz.json"
    authz.set_passphrase(PASSPHRASE, path=str(authz_path))
    runner = CliRunner()
    runner.invoke(cli, ["--db", str(db_path), "kill"])

    result = runner.invoke(
        cli,
        [
            "--db", str(db_path),
            "--authz-path", str(authz_path),
            "resume", "--passphrase", PASSPHRASE,
        ],
    )

    assert result.exit_code == 0, result.output
    repo = _repo_at(db_path)
    assert repo.get_state("kill_switch") is False


# -- subscription (rail 14, monthly-allowance) -------------------------------------------------


def test_subscription_show_seeds_from_config_yaml_on_first_use(tmp_path, valid_config_path):
    db_path = tmp_path / "test.db"
    runner = CliRunner()

    result = runner.invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "subscription", "show"]
    )

    assert result.exit_code == 0, result.output
    assert "monthly_allowance_usd=500" in result.output
    assert "pacing=opportunistic" in result.output
    repo = _repo_at(db_path)
    assert repo.get_subscription()["monthly_allowance_usd"] == Decimal("500")


def test_subscription_set_updates_the_live_allowance_no_passphrase_needed(
    tmp_path, valid_config_path
):
    db_path = tmp_path / "test.db"
    runner = CliRunner()

    result = runner.invoke(
        cli,
        [
            "--db", str(db_path),
            "--config", str(valid_config_path),
            "subscription", "set", "--monthly-allowance", "1000", "--pacing", "even_daily",
        ],
    )

    assert result.exit_code == 0, result.output
    repo = _repo_at(db_path)
    sub = repo.get_subscription()
    assert sub["monthly_allowance_usd"] == Decimal("1000")
    assert sub["pacing"] == "even_daily"


def test_subscription_set_without_pacing_keeps_the_existing_pacing(tmp_path, valid_config_path):
    db_path = tmp_path / "test.db"
    runner = CliRunner()
    runner.invoke(
        cli,
        [
            "--db", str(db_path),
            "--config", str(valid_config_path),
            "subscription", "set", "--monthly-allowance", "1000", "--pacing", "even_daily",
        ],
    )

    result = runner.invoke(
        cli,
        [
            "--db", str(db_path),
            "--config", str(valid_config_path),
            "subscription", "set", "--monthly-allowance", "250",
        ],
    )

    assert result.exit_code == 0, result.output
    repo = _repo_at(db_path)
    sub = repo.get_subscription()
    assert sub["monthly_allowance_usd"] == Decimal("250")
    assert sub["pacing"] == "even_daily"  # unchanged


def test_subscription_set_rejects_a_negative_allowance(tmp_path, valid_config_path):
    db_path = tmp_path / "test.db"
    runner = CliRunner()

    result = runner.invoke(
        cli,
        [
            "--db", str(db_path),
            "--config", str(valid_config_path),
            "subscription", "set", "--monthly-allowance", "-50",
        ],
    )

    assert result.exit_code != 0
    # rejected before it ever overwrites the (config.yaml-seeded) live value.
    repo = _repo_at(db_path)
    assert repo.get_subscription()["monthly_allowance_usd"] == Decimal("500")


def test_subscription_set_rejects_a_non_numeric_allowance(tmp_path, valid_config_path):
    db_path = tmp_path / "test.db"
    runner = CliRunner()

    result = runner.invoke(
        cli,
        [
            "--db", str(db_path),
            "--config", str(valid_config_path),
            "subscription", "set", "--monthly-allowance", "not-a-number",
        ],
    )

    assert result.exit_code != 0
    repo = _repo_at(db_path)
    assert repo.get_subscription()["monthly_allowance_usd"] == Decimal("500")


def test_agent_seeds_the_subscription_from_config_on_first_run(
    tmp_path, valid_config_path, monkeypatch
):
    monkeypatch.setattr(cli_module, "_build_broker", lambda config: FakeBroker())
    db_path = tmp_path / "test.db"
    runner = CliRunner()

    result = runner.invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "agent"]
    )

    assert result.exit_code == 0, result.output
    repo = _repo_at(db_path)
    assert repo.get_subscription()["monthly_allowance_usd"] == Decimal("500")


# -- monitor ----------------------------------------------------------------------------------


def test_monitor_single_poll_needs_no_passphrase(tmp_path, valid_config_path, monkeypatch):
    monkeypatch.setattr(cli_module, "_build_broker", lambda config: FakeBroker())
    db_path = tmp_path / "test.db"
    authz_path = tmp_path / "authz.json"
    runner = CliRunner()

    result = runner.invoke(
        cli,
        [
            "--db", str(db_path),
            "--config", str(valid_config_path),
            "--authz-path", str(authz_path),
            "monitor",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.output.count("polled") == 1
    assert not authz_path.exists()


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
    # valid_config_path's allowlist is BTC/ETH/PAXG (3 products) x all of RULE_REGISTRY (3 kinds).
    assert len(rows) == 3 * len(RULE_REGISTRY)
    assert "seeded=9 skipped=0" in result.output


def test_rules_seed_is_idempotent(tmp_path, valid_config_path):
    db_path = tmp_path / "test.db"
    repo = _repo_at(db_path)
    runner = CliRunner()
    args = ["--db", str(db_path), "--config", str(valid_config_path), "rules", "seed"]

    first = runner.invoke(cli, args)
    assert first.exit_code == 0, first.output

    second = runner.invoke(cli, args)

    assert second.exit_code == 0, second.output
    assert "seeded=0 skipped=9" in second.output
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
    assert "seeded=9 skipped=0" in second.output
    assert len(repo.get_rules()) == 2 * 3 * len(RULE_REGISTRY)


def test_rules_seed_respects_products_and_kinds_options(tmp_path):
    db_path = tmp_path / "test.db"
    repo = _repo_at(db_path)
    runner = CliRunner()

    result = runner.invoke(
        cli,
        [
            "--db", str(db_path),
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


def test_rules_seed_needs_no_passphrase(tmp_path):
    db_path = tmp_path / "test.db"
    authz_path = tmp_path / "authz.json"
    runner = CliRunner()

    result = runner.invoke(
        cli,
        [
            "--db", str(db_path),
            "--authz-path", str(authz_path),
            "rules", "seed",
            "--products", "BTC-USD",
            "--kinds", "dca",
        ],
    )

    assert result.exit_code == 0, result.output
    assert not authz_path.exists()


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


# -- insights (stub) ----------------------------------------------------------------------------


def test_insights_stub(tmp_path):
    runner = CliRunner()

    result = runner.invoke(cli, ["insights"])

    assert result.exit_code == 0, result.output
    assert "not yet implemented" in result.output
