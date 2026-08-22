"""`keel rules lookahead` and the promotion refusal it feeds (issue #440, C1a).

The command is the operator-facing rendering of `keel.research.bias.lookahead_analysis` over
a SAVED rule -- resolved through the same seam `rules backtest` uses -- and the promotion
gate refuses a rule whose verdict is `lookahead_detected`. Pinned here:

- a clean saved rule exits 0 and prints its verdict;
- a leaky rule exits 1 with the divergences (which bar, which field, both values);
- `--recursive` maps each rule family to its own ATR (turtle/rsi) or reports honestly that
  the family configures no recursive-suspect indicator (dca);
- `rules promote` refuses a leaky rule without mutating the row, and `--force` remains the
  documented bypass, exactly as it is for the rest of the gate.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from click.testing import CliRunner

from keel import agent
from keel.cli import cli
from keel.commands.rules import RulesRefused, attempt_promotion
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.strategy.rules.base import Rule, Setup
from keel.types import Candle, Granularity

DAY = 86400
NOW_TS = 1_800_000_000


def _repo(tmp_path) -> Repository:
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    return Repository(conn)


def _daily(n: int = 120, *, start: int = 1_700_000_000) -> list[Candle]:
    """Peak-close shape: max close at bar 60, a high spike at bar 110 -- so a rule targeting
    the max high of everything it can see is fed a different target by every prefix."""
    candles = []
    for i in range(n):
        close = Decimal(100 + (i if i <= 60 else 120 - i))
        high = Decimal(500) if i == 110 else close + Decimal(1)
        candles.append(
            Candle(
                ts=start + i * DAY,
                open=close - Decimal(1),
                high=high,
                low=close - Decimal(2),
                close=close,
                volume=Decimal(10),
            )
        )
    return candles


def _flat_daily(n: int = 300, *, start: int = 1_700_000_000) -> list[Candle]:
    """Constant true range -- the converging case for the recursive ATR check."""
    return [
        Candle(
            ts=start + i * DAY,
            open=Decimal(100),
            high=Decimal(110),
            low=Decimal(100),
            close=Decimal(110),
            volume=Decimal(1),
        )
        for i in range(n)
    ]


class _LeakyRule(Rule):
    """A saved-rule stand-in whose target is the max high of the ENTIRE series it is handed
    -- the classic 'target the future swing' leak. Reconstructed via a monkeypatched
    `agent._build_rule`, the same seam `rules backtest`/`promote` rebuild rules through."""

    def __init__(self) -> None:
        self.name = "leaky"
        self.params: dict[str, Any] = {"product_id": "BTC-USD", "granularity": "ONE_DAY"}
        self.product_id = "BTC-USD"
        self.granularity = Granularity.ONE_DAY

    def detect(self, candles_by_tf: dict[Granularity, list[Candle]]) -> Setup | None:
        candles = candles_by_tf[Granularity.ONE_DAY]
        signal = max(candles, key=lambda c: c.close)
        return Setup(
            product_id="BTC-USD",
            direction="long",
            entry=signal.close,
            stop=signal.close - Decimal(10),
            target=max(c.high for c in candles),
            context={},
            ts=signal.ts,
        )

    def exit_signal(self, held: Setup, candles_by_tf: dict[Granularity, list[Candle]]) -> bool:
        return False

    def describe(self) -> dict:
        return {"name": self.name, "params": self.params}


def _invoke(tmp_path, config_path, *args):
    return CliRunner().invoke(
        cli, ["--db", str(tmp_path / "t.db"), "--config", str(config_path), *args]
    )


# -- the diagnostic command -------------------------------------------------------------------


def test_lookahead_on_a_clean_saved_rule_exits_zero(tmp_path, valid_config_path) -> None:
    repo = _repo(tmp_path)
    repo.insert_rule(
        "turtle_breakout", {"product_id": "BTC-USD"}, status="candidate", now_ts=NOW_TS
    )
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _flat_daily())

    result = _invoke(tmp_path, valid_config_path, "rules", "lookahead", "1", "--sample-step", "10")

    assert result.exit_code == 0, result.output
    assert "rule 1" in result.output
    assert "clean" in result.output


def test_lookahead_on_a_leaky_saved_rule_exits_one_with_divergences(
    tmp_path, valid_config_path, monkeypatch
) -> None:
    repo = _repo(tmp_path)
    repo.insert_rule(
        "turtle_breakout", {"product_id": "BTC-USD"}, status="candidate", now_ts=NOW_TS
    )
    candles = _daily()
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, candles)
    monkeypatch.setattr(agent, "_build_rule", lambda row: _LeakyRule())

    result = _invoke(tmp_path, valid_config_path, "rules", "lookahead", "1", "--sample-step", "5")

    assert result.exit_code == 1, result.output
    assert "lookahead_detected" in result.output
    assert f"ts={candles[60].ts}" in result.output
    assert "field=target" in result.output
    assert "161" in result.output and "500" in result.output


def test_lookahead_refuses_an_unknown_id(tmp_path, valid_config_path) -> None:
    _repo(tmp_path)
    result = _invoke(tmp_path, valid_config_path, "rules", "lookahead", "99")
    assert result.exit_code == 1
    assert "no rule with id 99" in result.output


def test_recursive_flag_runs_the_rules_own_atr(tmp_path, valid_config_path) -> None:
    repo = _repo(tmp_path)
    repo.insert_rule(
        "turtle_breakout", {"product_id": "BTC-USD"}, status="candidate", now_ts=NOW_TS
    )
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _flat_daily())

    result = _invoke(
        tmp_path, valid_config_path, "rules", "lookahead", "1", "--recursive", "--sample-step", "10"
    )

    assert result.exit_code == 0, result.output
    assert "recursive" in result.output
    assert "stable" in result.output
    assert "atr" in result.output


def test_recursive_flag_reports_honestly_when_no_suspect_indicator(
    tmp_path, valid_config_path
) -> None:
    repo = _repo(tmp_path)
    repo.insert_rule(
        "dca", {"product_id": "BTC-USD", "cadence_days": 7}, status="candidate", now_ts=NOW_TS
    )
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _flat_daily(60))

    result = _invoke(
        tmp_path,
        valid_config_path,
        "rules",
        "lookahead",
        "1",
        "--granularity",
        "ONE_DAY",
        "--recursive",
        "--sample-step",
        "5",
    )

    assert result.exit_code == 0, result.output
    assert "no recursive-suspect indicator" in result.output


# -- the promotion refusal --------------------------------------------------------------------


def test_promote_refuses_a_leaky_rule_without_mutating_the_row(
    tmp_path, valid_config_path, monkeypatch
) -> None:
    repo = _repo(tmp_path)
    repo.insert_rule(
        "turtle_breakout", {"product_id": "BTC-USD"}, status="candidate", now_ts=NOW_TS
    )
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _daily())
    monkeypatch.setattr(agent, "_build_rule", lambda row: _LeakyRule())

    result = _invoke(tmp_path, valid_config_path, "rules", "promote", "1")

    assert result.exit_code == 1, result.output
    assert "lookahead" in result.output
    assert "keel rules lookahead 1" in result.output
    # Fail-closed: nothing was written.
    assert repo.get_rules()[0]["status"] == "candidate"


def test_promote_force_remains_the_documented_bypass_for_a_leaky_rule(
    tmp_path, valid_config_path, monkeypatch
) -> None:
    repo = _repo(tmp_path)
    repo.insert_rule(
        "turtle_breakout", {"product_id": "BTC-USD"}, status="candidate", now_ts=NOW_TS
    )
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _daily())
    monkeypatch.setattr(agent, "_build_rule", lambda row: _LeakyRule())

    result = _invoke(tmp_path, valid_config_path, "rules", "promote", "1", "--force")

    assert result.exit_code == 0, result.output
    assert repo.get_rules()[0]["status"] == "paper"
    assert "FORCE-PROMOTING" in result.output


def test_attempt_promotion_service_refuses_leaky_rules(monkeypatch) -> None:
    """The service seam the console also dispatches through refuses the same way."""
    from tests.commands.test_rules_services import _config  # the existing pinned Config shape

    conn = connect(":memory:")
    migrate(conn)
    repo = Repository(conn)
    repo.insert_rule(
        "turtle_breakout", {"product_id": "BTC-USD"}, status="candidate", now_ts=NOW_TS
    )
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _daily())
    monkeypatch.setattr(agent, "_build_rule", lambda row: _LeakyRule())

    out: list[str] = []
    err: list[str] = []
    try:
        attempt_promotion(
            repo,
            _config(),
            1,
            echo=out.append,
            echo_err=err.append,
        )
        raise AssertionError("expected RulesRefused")
    except RulesRefused:
        pass
    joined = "\n".join(err)
    assert "lookahead" in joined
    assert "keel rules lookahead 1" in joined
    assert repo.get_rules()[0]["status"] == "candidate"
