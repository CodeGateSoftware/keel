"""CLI surface for the trials ledger (spec §4.5) -- the scratchpad recording path."""

from __future__ import annotations

from decimal import Decimal

from click.testing import CliRunner

from keel.cli import cli
from keel.research import ledger as trials_ledger


def _record(runner, path, trial_id, decision="selected"):
    return runner.invoke(
        cli,
        [
            "trials", "record",
            "--ledger", str(path),
            "--trial-id", trial_id,
            "--session", "s1",
            "--rule", "turtle_breakout",
            "--params", '{"entry": 40}',
            "--provenance", "fitted",
            "--kind", "sweep_node",
            "--decision", decision,
            "--series-missing",
        ],
    )


def test_record_then_list_and_verify(tmp_path):
    runner = CliRunner()
    path = tmp_path / "trials.jsonl"

    assert _record(runner, path, "t1").exit_code == 0
    assert _record(runner, path, "t2", decision="diagnostic_only").exit_code == 0

    listed = runner.invoke(cli, ["trials", "list", "--ledger", str(path)])
    assert listed.exit_code == 0
    assert "t1" in listed.output
    assert "M=2" in listed.output
    assert "N_decisions=1" in listed.output

    verified = runner.invoke(cli, ["trials", "verify", "--ledger", str(path)])
    assert verified.exit_code == 0
    assert "intact" in verified.output.lower()


def test_verify_exits_nonzero_on_tamper(tmp_path):
    runner = CliRunner()
    path = tmp_path / "trials.jsonl"
    _record(runner, path, "t1")
    _record(runner, path, "t2")

    lines = path.read_text().splitlines()
    lines[0] = lines[0].replace('"s1"', '"s2"')
    path.write_text("\n".join(lines) + "\n")

    verified = runner.invoke(cli, ["trials", "verify", "--ledger", str(path)])
    assert verified.exit_code != 0
    assert "row 1" in verified.output


def test_record_rejects_bad_enum(tmp_path):
    runner = CliRunner()
    path = tmp_path / "trials.jsonl"
    result = runner.invoke(
        cli,
        [
            "trials", "record", "--ledger", str(path), "--trial-id", "t1",
            "--session", "s", "--rule", "r", "--params", "{}",
            "--provenance", "vibes", "--kind", "sweep_node",
            "--decision", "selected", "--series-missing",
        ],
    )
    assert result.exit_code != 0


def test_pbo_command_reports_but_never_names_a_winner(tmp_path):
    """The command prints probabilities. It must not print a winning configuration."""
    path = tmp_path / "trials.jsonl"
    for column_index in range(6):
        drift = Decimal(column_index) / Decimal(10)
        series = [drift + (Decimal("10") if i % 2 else Decimal("-10")) for i in range(32)]
        trials_ledger.append_trial(
            path,
            trial_id=f"grid-{column_index}",
            session="grid",
            rule="turtle_breakout",
            params={"entry": 20 + column_index * 5},
            provenance="fitted",
            kind="sweep_node",
            decision="diagnostic_only",
            per_bar_pnl=series,
            timestamp=1_700_000_000,
        )

    runner = CliRunner()
    result = runner.invoke(
        cli, ["trials", "pbo", "--ledger", str(path), "--session", "grid", "--blocks", "4"]
    )
    assert result.exit_code == 0
    assert "PBO" in result.output
    assert "degradation slope" in result.output
    assert "Prob[OOS < 0]" in result.output
    assert "dominance" in result.output.lower()

    # ⛔ Strathern rail (spec §6): the output must not let a caller recover which
    # configuration won. Assert the substantive property -- no candidate parameter value and
    # no winner-naming vocabulary reaches the output -- rather than banning loose words, since
    # the legitimate N>>10 warning also contains "recommended".
    for entry in (20, 25, 30, 35, 40, 45):
        assert f"entry={entry}" not in result.output
        assert f"'entry': {entry}" not in result.output
    for word in ("best", "winner", "optimal", "chosen"):
        assert word not in result.output.lower()


def test_pbo_refuses_when_every_trial_is_series_missing(tmp_path):
    """Backfilled rows count toward M but cannot be columns (spec §4.6)."""
    path = tmp_path / "trials.jsonl"
    runner = CliRunner()
    _record(runner, path, "backfilled")

    result = runner.invoke(cli, ["trials", "pbo", "--ledger", str(path), "--blocks", "4"])
    assert result.exit_code != 0
    assert "no usable columns" in result.output


def test_deflate_reports_a_band_and_refuses_to_invent_DSR(tmp_path):
    """Anything the ledger cannot supply must read as MISSING, not as a plausible default."""
    runner = CliRunner()
    path = tmp_path / "trials.jsonl"
    for i in range(5):
        _record(runner, path, f"t{i}")

    result = runner.invoke(
        cli, ["trials", "deflate", "--ledger", str(path), "--sharpe", "0.4"]
    )
    assert result.exit_code == 0, result.output
    # A band, not a single number, because rho is not measured here.
    assert "0.00" in result.output and "0.90" in result.output
    assert "MinBTL" in result.output
    assert "DSR: NOT COMPUTED" in result.output


def test_deflate_computes_dsr_when_the_variance_is_supplied_explicitly(tmp_path):
    runner = CliRunner()
    path = tmp_path / "trials.jsonl"
    for i in range(5):
        _record(runner, path, f"t{i}")

    result = runner.invoke(
        cli,
        ["trials", "deflate", "--ledger", str(path), "--sharpe", "0.4",
         "--rho", "0.5", "--trial-sharpe-variance", "0.05"],
    )
    assert result.exit_code == 0, result.output
    assert "SR_0" in result.output
    assert "DSR" in result.output
    assert "NOT COMPUTED" not in result.output


def test_deflate_refuses_on_too_few_decision_trials(tmp_path):
    runner = CliRunner()
    path = tmp_path / "trials.jsonl"
    _record(runner, path, "only-one")
    result = runner.invoke(
        cli, ["trials", "deflate", "--ledger", str(path), "--sharpe", "0.4"]
    )
    assert result.exit_code != 0
