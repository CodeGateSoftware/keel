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

from decimal import Decimal
from pathlib import Path
from typing import Any

from click.testing import CliRunner

import keel.cli as cli_module
from keel.cli import DISCLAIMER, cli
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.security import authz

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


# -- insights (stub) ----------------------------------------------------------------------------


def test_insights_stub(tmp_path):
    runner = CliRunner()

    result = runner.invoke(cli, ["insights"])

    assert result.exit_code == 0, result.output
    assert "not yet implemented" in result.output
