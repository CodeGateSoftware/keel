"""Regression baseline: `backtest()` output must not change during the monorepo migration.

Read-only by design. Regenerate deliberately with:

    uv run python tests/baseline/regenerate_golden.py
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
