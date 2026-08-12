"""Regression baseline: `backtest()` output must not change during the monorepo migration.

Read-only by design. Regenerate deliberately with:

    uv run python tests/baseline/regenerate_golden.py

**Regenerated once, for #247** -- the fee correction (maker 0.006 -> taker 0.012, the rate that
matches this engine's market-style fill model). That is a deliberate strategy-output change, the
one case the regeneration script exists for, so the golden was rebuilt rather than pinned to the
superseded rate. What moved, on the committed BTC daily corpus:

    profit_factor   1.6143 -> 1.2694
    expectancy      1368.48 -> 692.08
    max_drawdown    12177.59 -> 14220.67
    n_trades  13    win_rate 0.4615    (both UNCHANGED)

`n_trades` and `win_rate` holding still is the check that this was a costing change and not an
accidental change to fill logic: fees are charged on a filled trade, they never decide whether a
level was touched. The old numbers are not restated anywhere -- they were real outputs of the
code as it stood, and `docs/experiments/` keeps them as printed.
"""

from __future__ import annotations

import json

from tests.baseline.serialize import GOLDEN, load_baseline_candles, run_baseline_backtest


def test_turtle_breakout_backtest_matches_baseline() -> None:
    assert run_baseline_backtest() == json.loads(GOLDEN.read_text())


def test_baseline_corpus_is_non_trivial() -> None:
    """Guard against an empty fixture silently making the golden test vacuous."""
    assert len(load_baseline_candles()) > 1000


def test_baseline_records_trades() -> None:
    """A zero-trade baseline would pass forever while proving nothing."""
    assert json.loads(GOLDEN.read_text())["n_trades"] > 0
