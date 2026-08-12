#!/usr/bin/env python
"""rsi_meanrev DIAGNOSTIC sweep -- can the rule be made to fire enough to be measurable?

PRE-DECLARED BEFORE RUNNING. This is deliberately NOT an edge search.

## Why this is a different experiment from the first sweep
The first sweep (576 cells declared, 224 run before it was abandoned as mis-centred) established
that `rsi_meanrev` does not lose -- it barely trades. Median 6 trades over five years of hourly
bars; 36 of 224 cells never fired once; the maximum across every cell was 80, against a promotion
floor of 100. Every cell that showed PF > 1.0 rested on n=2 or n=3, i.e. one win and one loss.

That is not a negative edge result. It is an unmeasurable one, and the two must not be conflated.

## What the 224 cells showed about which gates bind
    oversold            15.0 -> median n=2    20.0 -> median n=25   (12x)
    require_divergence  True -> median n=1    False -> median n=20  (20x)
    overbought          80   -> median n=3    70   -> median n=6
    fixed_rr, atr_mult  no effect on n at all -- they are exit geometry, not entry gates
So the binding constraints are the ENTRY conjunction: RSI must reach `oversold`, AND price must be
within `support_proximity_pct` of a level with `level_min_touches` touches, AND optionally show a
divergence. Three rare conditions ANDed together.

## The question this sweep asks
Not "is there edge" but "is there any configuration under which this rule fires enough to be
evaluated at all". **The primary metric is n_trades. PF is recorded but is NOT the objective.**

## The honesty constraint that follows from that
Selecting configs to maximise trade count and then reading the PF of the winner is selection bias.
Whatever fires enough here must be re-tested as a fresh, pre-declared hypothesis before any edge
claim is made about it. This file produces a feasibility answer, not a performance answer, and the
write-up must say so.

## Grid, and why it is small
18 configs x 6 assets = 108 trials -- a deliberately modest draw on the multiple-testing budget,
because a feasibility question does not need a wide net. `require_divergence` is fixed False and
`oversold` starts at 25: the prior sweep already measured both alternatives into the ground, and
re-running known-dead settings would spend trials to reconfirm what is recorded above.
`atr_mult`/`fixed_rr` are pinned at defaults since they provably do not move n.

-------------------------------------------------------------------------------------------------
ADDED WHEN THIS WAS COPIED INTO THE REPO. Everything above this line is the pre-registration and
is unedited, INCLUDING ITS STALE NUMBERS, which are themselves evidence and are corrected here
rather than silently patched above.

**The "224 run" and the statistics drawn from it are the state of the first grid at the moment
this file was declared, not its final state.** The first grid's process was still resident and
went on to write 448 rows before it stopped. Re-derived from the frozen raw JSONL, the first
grid's final figures are: 448 rows / 432 distinct cells, median n **17**, max n **224** (not 80),
36 cells that never fired, and 19 distinct cells above PF 1.0 at n in {2, 3, **16**} -- so the
"n=2 or n=3" claim above is true of 17 of the 19 and false of two. None of those corrections
changes this grid's design or its justification: `oversold` and `require_divergence` really were
the binding gates, and re-running them really would have been wasted budget. They are recorded
because the write-up's argument is that the first grid was mis-read as well as mis-centred, and
this docstring is the primary exhibit for the mis-reading.

The one substantive prediction in the section above that the results below refute is the
attribution of the trade-count bind to `support_proximity_pct`: widening it 10x moves n by a
median of **1.19x**, and `level_min_touches` -- named alongside it -- moves n by a median of
**1.19x** as well. `oversold` alone moves n by **3.9x** over the same grid. See
`docs/experiments/2026-08-12-fee-curve-and-rsi-meanrev.md` section 6.

**PATHS WERE ADJUSTED.** The run-time original hard-coded the deployment's `~/keel` on
`sys.path`, its `~/keel/keel.db`, and a session scratchpad path for the output; those are now
`--db` / `--out` with repo-relative defaults, and the cache is opened `mode=ro` (the house
convention -- the run never wrote to it, so this cannot move a number). The grid, the fee, the
asset list, the metric and the per-trial body are the run's.

    .venv/bin/python docs/experiments/2026-08-12-fee-curve-and-rsi-meanrev-diag.py \
        --db ~/keel/keel.db --out rsi_diag.json
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
    # The requested widening. 0.005 is the shipped default and the suspected binding constraint.
    "support_proximity_pct": ["0.005", "0.02", "0.05"],
    # The other half of the support conjunction: how established a level must be to count.
    "level_min_touches": [2, 3],
    # 20.0 already gave median n=25; 15.0 gave 2. Start above the shipped default, not below.
    "oversold": [25.0, 30.0, 35.0],
    "overbought": [70.0],
    "require_divergence": [False],
    "atr_mult": ["1.5"],
    "fixed_rr": ["2"],
}


def _configs() -> list[dict[str, Any]]:
    keys = list(GRID)
    return [dict(zip(keys, c, strict=True)) for c in itertools.product(*GRID.values())]


def _connect(db_path: str) -> sqlite3.Connection:
    """Read-only handle to the candle cache -- structural, not a promise in a docstring."""
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _done(jsonl: str) -> set[tuple[Any, ...]]:
    if not os.path.exists(jsonl):
        return set()
    out = set()
    with open(jsonl) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            out.add((r["product"], *(r[k] for k in GRID)))
    return out


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
        }
    except Exception as exc:
        return {"product": product, **cfg, "error": f"{type(exc).__name__}: {exc}"}


def main() -> None:
    parser = argparse.ArgumentParser(description="rsi_meanrev feasibility grid -- n_trades, not PF")
    parser.add_argument("--db", default="keel.db", help="candle cache, opened read-only")
    parser.add_argument("--out", default="rsi_diag.json", help="results JSON")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    jsonl = args.out.replace(".json", ".jsonl")
    cfgs = _configs()
    jobs = [(args.db, a, c) for c in cfgs for a in ASSETS]
    done = _done(jsonl)
    jobs = [j for j in jobs if (j[1], *(j[2][k] for k in GRID)) not in done]
    print(f"trials={len(jobs)} remaining (of {len(cfgs) * len(ASSETS)}) fee={FEE}", flush=True)
    t0 = time.perf_counter()
    with ProcessPoolExecutor(max_workers=args.workers) as ex, open(jsonl, "a") as fh:
        for i, row in enumerate(ex.map(_one, jobs, chunksize=2), 1):
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            if i % 25 == 0:
                print(f"  {i}/{len(jobs)}  {time.perf_counter() - t0:.0f}s", flush=True)
    with open(jsonl) as fh:
        allrows = [json.loads(x) for x in fh if x.strip()]
    with open(args.out, "w") as fh:
        json.dump({"trials": len(allrows), "fee": str(FEE), "rows": allrows}, fh)
    print(f"done in {time.perf_counter() - t0:.0f}s -> {args.out}", flush=True)


if __name__ == "__main__":
    main()
