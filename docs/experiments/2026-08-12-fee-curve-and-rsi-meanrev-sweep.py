#!/usr/bin/env python
"""rsi_meanrev parameter sweep -- the first test of a rule other than turtle_breakout.

PRE-DECLARED BEFORE RUNNING (this docstring is the pre-registration; `provenance: a_priori`).

## Why this rule, and why now
Every negative result to date (2026-08-11 hourly finding, 2026-08-11 param sweep) describes ONE
rule: `turtle_breakout`, a momentum/breakout system. `rsi_meanrev` has been implemented and
registered in `RULE_REGISTRY` the whole time and has never been backtested -- the database holds
19 turtle rules, 2 dca rules, and zero of this one. So "trend-following fails on crypto spot at
these fees" is currently a claim about one Donchian breakout, not about the venue.

Mean reversion is the natural adversarial test: it profits from exactly the conditions that hurt
a breakout (chop, failed moves, reversion to a level). If it ALSO comes back negative, the claim
generalises and the conclusion is about fees/venue rather than about signal choice. If it comes
back positive, most of the current roadmap dissolves.

## Stated expectation, recorded before the result
The operator's hypothesis is that mean reversion OUTPERFORMS the breakout. Recording it here so
the result can contradict it: turtle's best mean PF across assets was 0.634 (best qualifying,
n>=100: 0.584). Anything at or below that is a replication of the negative finding, not a new one.

## Design choices carried over, deliberately
1.  **fee_pct = 0.012 (taker).** The simulator fills market-style at next-bar open. The shipped
    default of 0.006 is what made ZEC look like a winner at 1.042 before correction. Every number
    here is priced at the rate the config says applies.
2.  **Headline metric is MEAN PF ACROSS ASSETS**, not the best cell. With ~500 trials, the best
    cell is close to guaranteed to be noise; a config that only works on one asset is what a
    regime artifact looks like (see ZEC in the param-sweep doc).
3.  **Same six assets** as the turtle sweep -- ZEC, FET, SOL, DOGE, ETH, BTC -- so the two results
    are directly comparable rather than measured on different universes.
4.  **Trial count reported honestly** for the multiple-testing budget.

## Grid rationale
`oversold`/`overbought` are the primary signal axis. `atr_mult` and `fixed_rr` set the risk
geometry. `support_proximity_pct` gates how near a support level price must be -- the rule's
distinguishing feature over a bare RSI trigger -- so it is swept rather than assumed.
`require_divergence` is the strongest available filter and is tested both ways.

-------------------------------------------------------------------------------------------------
ADDED WHEN THIS WAS COPIED INTO THE REPO. Everything above this line is the pre-registration and
is unedited. Everything below it is provenance, and it is here because a script in
`docs/experiments/` is a claim that its numbers can be reproduced.

**THIS GRID WAS ABANDONED MID-RUN AND ITS RESULTS WERE NOT USED FOR ANY EDGE CLAIM.** It is
committed because it was paid for -- 448 trials against the multiple-testing budget -- and
because the reason it was abandoned is a finding in its own right. See
`docs/experiments/2026-08-12-fee-curve-and-rsi-meanrev.md` sections 5 and 6. Read that before
re-running anything here: this grid is a recorded dead end, and re-running it as declared would
spend a further 144 cells to reconfirm it.

What the abandoned run actually produced, re-derived from the raw JSONL rather than from any
running summary: 576 cells declared, **448 rows written**, of which **432 are distinct cells**
and 16 are re-runs of cells the resume path had already computed (all 16 returned identical
results). `oversold` varies slowest under `itertools.product`, so the three blocks that ran are
`oversold` 15/20/25 complete, and the entire `oversold=30` block -- 144 cells, the most likely
of the four to fire -- never ran at all.

**PATHS WERE ADJUSTED.** The run-time original hard-coded the deployment's `~/keel` on
`sys.path`, its `~/keel/keel.db`, and a session scratchpad path for the output; those are now
`--db` / `--out` with repo-relative defaults, and the cache is opened `mode=ro` (the house
convention -- the run never wrote to it, so this cannot move a number). The grid, the fee, the
asset list, the metric, the resume logic and the per-trial body are the run's.

    .venv/bin/python docs/experiments/2026-08-12-fee-curve-and-rsi-meanrev-sweep.py \
        --db ~/keel/keel.db --out rsi_results.json
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import sqlite3
import time
from concurrent.futures import ProcessPoolExecutor
from decimal import Decimal
from typing import Any

from keel_core.types import Granularity

from keel.data.repository import Repository
from keel.strategy import backtest as bt
from keel.strategy.rules.rsi_meanrev import RsiMeanReversion

FEE = Decimal("0.012")
ASSETS = ["ZEC-USD", "FET-USD", "SOL-USD", "DOGE-USD", "ETH-USD", "BTC-USD"]

GRID = {
    "oversold": [15.0, 20.0, 25.0, 30.0],
    "overbought": [70.0, 80.0],
    "atr_mult": ["1.2", "2.5"],
    "fixed_rr": ["1.5", "2", "3"],
    # Held at the shipped default. Sweeping it too would take the grid to 1,728 trials -- double
    # the turtle sweep's draw on the multiple-testing budget for a rule that has not yet shown
    # any reason to deserve it. If the primary axes show life, widen then, and say so.
    "support_proximity_pct": ["0.005"],
    "require_divergence": [False, True],
}


def _configs() -> list[dict[str, Any]]:
    keys = list(GRID)
    return [dict(zip(keys, c, strict=True)) for c in itertools.product(*GRID.values())]


def _connect(db_path: str) -> sqlite3.Connection:
    """Read-only handle to the candle cache -- structural, not a promise in a docstring."""
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _one(job: tuple[str, str, dict[str, Any]]) -> dict[str, Any]:
    db_path, product, cfg = job
    repo = Repository(_connect(db_path))
    candles = repo.get_candles(product, Granularity.ONE_HOUR)
    params = dict(cfg)
    for dec in ("atr_mult", "fixed_rr", "support_proximity_pct"):
        params[dec] = Decimal(params[dec])
    try:
        rule = RsiMeanReversion(product_id=product, **params)
        r = bt.backtest(rule, candles, fee_pct=FEE)
        return {
            "product": product,
            **cfg,
            "n_trades": r.n_trades,
            "win_rate": float(r.win_rate),
            "profit_factor": float(r.profit_factor),
            "expectancy": float(r.expectancy),
            "max_drawdown": float(r.max_drawdown),
        }
    except Exception as exc:
        return {"product": product, **cfg, "error": f"{type(exc).__name__}: {exc}"}


def _done_keys(jsonl: str) -> set[tuple[Any, ...]]:
    """Cells already computed, read back from the incremental JSONL.

    This sweep has been interrupted twice by the harness reclaiming its background process. Rather
    than restart 576 trials each time, resume: the JSONL is append-only and every row identifies
    its own cell, so the completed set is recoverable exactly.
    """
    if not os.path.exists(jsonl):
        return set()
    done = set()
    with open(jsonl) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue  # a torn final line from a kill mid-write
            done.add((r["product"], *(r[k] for k in GRID)))
    return done


def main() -> None:
    parser = argparse.ArgumentParser(description="rsi_meanrev sweep (ABANDONED -- see docstring)")
    parser.add_argument("--db", default="keel.db", help="candle cache, opened read-only")
    parser.add_argument("--out", default="rsi_results.json", help="results JSON")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    jsonl = args.out.replace(".json", ".jsonl")
    cfgs = _configs()
    jobs = [(args.db, a, c) for c in cfgs for a in ASSETS]
    done = _done_keys(jsonl)
    jobs = [j for j in jobs if (j[1], *(j[2][k] for k in GRID)) not in done]
    print(f"resuming: {len(done)} cells already done, {len(jobs)} remaining", flush=True)
    print(f"configs={len(cfgs)} assets={len(ASSETS)} trials={len(jobs)} fee={FEE}", flush=True)
    t0 = time.perf_counter()
    with ProcessPoolExecutor(max_workers=args.workers) as ex, open(jsonl, "a") as fh:
        for i, row in enumerate(ex.map(_one, jobs, chunksize=4), 1):
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            if i % 100 == 0:
                print(f"  {i}/{len(jobs)}  {time.perf_counter() - t0:.0f}s", flush=True)
    with open(jsonl) as fh:
        allrows = [json.loads(x) for x in fh if x.strip()]
    with open(args.out, "w") as fh:
        json.dump({"trials": len(allrows), "fee": str(FEE), "rows": allrows}, fh)
    print(f"done in {time.perf_counter() - t0:.0f}s -> {args.out}", flush=True)


if __name__ == "__main__":
    main()
