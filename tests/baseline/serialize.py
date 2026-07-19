"""Canonical, order-stable serialisation of a `BacktestResult` for golden-file comparison.

Decimals become strings so the comparison is exact rather than float-tolerant: a refactor that
perturbs arithmetic by one ulp is a behaviour change and must fail this test.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from keel.strategy.stats import BacktestResult
from keel.types import Candle

FIXTURES = Path(__file__).parent.parent / "fixtures"


def load_baseline_candles() -> list[Candle]:
    """Load the committed baseline candle corpus."""
    payload = json.loads((FIXTURES / "baseline_candles.json").read_text())
    return [
        Candle(
            ts=row["ts"],
            open=Decimal(row["open"]),
            high=Decimal(row["high"]),
            low=Decimal(row["low"]),
            close=Decimal(row["close"]),
            volume=Decimal(row["volume"]),
        )
        for row in payload["candles"]
    ]


def serialize_result(result: BacktestResult) -> dict[str, Any]:
    """Render `result` as JSON-safe primitives, Decimals as exact strings."""

    def dec(value: Decimal | None) -> str | None:
        return None if value is None else str(value)

    return {
        "n_trades": result.n_trades,
        "win_rate": result.win_rate,
        "avg_win": dec(result.avg_win),
        "avg_loss": dec(result.avg_loss),
        "expectancy": dec(result.expectancy),
        "profit_factor": dec(result.profit_factor),
        "max_drawdown": dec(result.max_drawdown),
        "max_losing_streak": result.max_losing_streak,
        "avg_mfe": dec(result.avg_mfe),
        "avg_mae": dec(result.avg_mae),
        "trades": [
            {
                "entry_ts": t.entry_ts,
                "exit_ts": t.exit_ts,
                "entry": dec(t.entry),
                "exit": dec(t.exit),
                "qty": dec(t.qty),
                "side": t.side.value if hasattr(t.side, "value") else str(t.side),
                "pnl": dec(t.pnl),
                "r_multiple": dec(t.r_multiple),
                "mfe": dec(t.mfe),
                "mae": dec(t.mae),
                "outcome": t.outcome,
            }
            for t in result.trades
        ],
    }


def run_baseline_backtest() -> dict[str, Any]:
    """The one canonical baseline backtest, shared by the test and the regeneration script."""
    from keel.strategy.backtest import backtest
    from keel.strategy.rules.turtle_breakout import TurtleBreakout

    rule = TurtleBreakout(product_id="BTC-USD")
    return serialize_result(backtest(rule, load_baseline_candles()))


GOLDEN = FIXTURES / "baseline_backtest.json"
