"""Regression baseline: `backtest()` output must not change during the monorepo migration.

Read-only by design. Regenerate deliberately with:

    uv run python tests/baseline/regenerate_golden.py

**Regenerated twice**, both times for a deliberate strategy-output change -- the one case the
regeneration script exists for.

**#247** -- the fee correction (maker 0.006 -> taker 0.012, the rate that matches this engine's
market-style fill model):

    profit_factor   1.6143 -> 1.2694
    expectancy      1368.48 -> 692.08
    max_drawdown    12177.59 -> 14220.67
    n_trades  13    win_rate 0.4615    (both UNCHANGED)

`n_trades` and `win_rate` holding still was the check that this was a costing change and not an
accidental change to fill logic: fees are charged on a filled trade, they never decide whether a
level was touched.

**#257** -- entries now fill at the next bar's OPEN rather than seeking the setup's quoted entry
level, mirroring the market orders `execution/executor.py` actually places:

    profit_factor   1.269371 -> 1.269287
    expectancy      692.0773 -> 691.9480
    max_drawdown    14220.67 -> 14222.05
    n_trades  13    win_rate 0.4615    (both UNCHANGED)

That #247 reasoning does NOT carry over here: this *is* a fill-logic change, so `n_trades` was
free to move and simply didn't. It held because `turtle_breakout` sets `entry = current.close`,
and on a 24/7 crypto series the next bar's open sits a hair from the prior close -- so on this
corpus the two models pick nearly the same price and decline nearly the same trades. The change
is materially larger for a rule whose entry is offset from the close (`pullback_continuation`
uses `signal_candle.high + buffer_ticks`), which this daily BTC baseline does not exercise. Read
the tiny deltas above as "this corpus is insensitive to the fill model", not as "the fill model
barely matters".

Old numbers are not restated anywhere -- they were real outputs of the code as it stood, and
`docs/experiments/` keeps them as printed.
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
