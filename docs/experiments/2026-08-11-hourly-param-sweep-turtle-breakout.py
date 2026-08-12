#!/usr/bin/env python
"""Hourly parameter sweep for turtle_breakout, built to resist fooling us.

The question: the 2026-08-11 hourly finding showed daily-tuned turtle loses on all 19 assets at
the realistic 1.2% taker fee. The obvious objection is that `entry_lookback=40` means 40 HOURS on
hourly bars, not 40 days -- the params were never tuned for this granularity. This sweep tests
whether ANY hourly-appropriate parameter set produces a positive edge.

Three design choices exist to stop this manufacturing a false positive:

1.  **Fee is 1.2% (taker), not the backtest default 0.006.** The simulator fills market-style at
    next-bar open, which is taker behaviour. Sweeping at the maker rate would flatter every cell
    and is how ZEC's apparent 1.042 arose in the first place.
2.  **The headline metric is MEAN profit factor ACROSS assets, not the best single cell.** With
    864 trials, the best cell is a near-certain artifact: at p<0.05 roughly 43 cells look good by
    chance. A parameter set that only works on one asset has told us nothing. One that works on
    most is a finding.
3.  **Every trial is counted and reported**, so the multiple-testing burden is visible rather
    than buried. The count goes in the trials ledger.

**The grid below was declared before the run and is unchanged since** -- it is the pre-registration
for `docs/experiments/2026-08-11-hourly-param-sweep-turtle-breakout.md`, not a post-hoc
description of which cells were kept. Every cell it names was run, and all 864 results are
reported.

PATHS WERE ADJUSTED WHEN THIS WAS COPIED INTO THE REPO. The run-time original hard-coded the
deployment's `~/keel` on `sys.path`, its `~/keel/keel.db`, and a session scratchpad path for the
output; those are now `--db` / `--out` with repo-relative defaults, and the cache is opened
`mode=ro` (the house convention -- the run never wrote to it, so this cannot move a number). The
grid, the fee, the asset list, the metric and the per-trial body are byte-for-byte the run's.

    .venv/bin/python docs/experiments/2026-08-11-hourly-param-sweep-turtle-breakout.py
    .venv/bin/python docs/experiments/2026-08-11-hourly-param-sweep-turtle-breakout.py \
        --db ~/keel/keel.db --out sweep_results.json

Runtime is ~9 minutes on 8 workers.
"""

from __future__ import annotations

import argparse
import itertools
import json
import sqlite3
import time
from concurrent.futures import ProcessPoolExecutor
from decimal import Decimal
from typing import Any

from keel_core.types import Granularity

from keel.data.repository import Repository
from keel.strategy import backtest as bt
from keel.strategy.rules.turtle_breakout import TurtleBreakout

FEE = Decimal("0.012")  # taker -- see docstring

#: Six assets spanning the observed hourly PF range (ZEC best 0.736 .. BTC worst 0.148), so a
#: parameter set cannot look good merely by being tested on the easy end of the field.
ASSETS = ["ZEC-USD", "FET-USD", "SOL-USD", "DOGE-USD", "ETH-USD", "BTC-USD"]

#: Hourly-appropriate lookbacks. 40 is the daily-tuned baseline (40h ~ 1.7 days); 168 = one week,
#: 336 = two weeks, which is where a daily-40 breakout actually lives in wall-clock terms.
GRID = {
    "entry_lookback": [40, 80, 120, 168, 240, 336],
    "exit_lookback": [20, 40, 80],
    "atr_stop_mult": ["2", "3"],
    "target_rr": ["6", "3"],
    "adx_threshold": [25.0, 20.0],
}


def _configs() -> list[dict]:
    keys = list(GRID)
    return [dict(zip(keys, combo, strict=True)) for combo in itertools.product(*GRID.values())]


def _connect(db_path: str) -> sqlite3.Connection:
    """Read-only handle to the candle cache.

    An experiment must not be able to write to the data it reads, and `mode=ro` is the cheapest
    way to make that structural rather than a promise in a docstring.
    """
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _one(job: tuple[str, str, dict]) -> dict[str, Any]:
    db_path, product, cfg = job
    repo = Repository(_connect(db_path))
    candles = repo.get_candles(product, Granularity.ONE_HOUR)
    params = dict(cfg)
    params["atr_stop_mult"] = Decimal(params["atr_stop_mult"])
    params["target_rr"] = Decimal(params["target_rr"])
    rule = TurtleBreakout(product_id=product, **params)
    try:
        r = bt.backtest(rule, candles, fee_pct=FEE)
        return {
            "product": product,
            **cfg,
            "n_trades": r.n_trades,
            "win_rate": float(r.win_rate),
            "profit_factor": float(r.profit_factor),
            "expectancy": float(r.expectancy),
            "max_drawdown": float(r.max_drawdown),
            "max_losing_streak": r.max_losing_streak,
        }
    except Exception as exc:  # a config the rule refuses is a result, not a crash
        return {"product": product, **cfg, "error": f"{type(exc).__name__}: {exc}"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="keel.db", help="candle cache, opened read-only")
    parser.add_argument("--out", default="sweep_results.json", help="results JSON")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    cfgs = _configs()
    jobs = [(args.db, a, c) for c in cfgs for a in ASSETS]
    print(f"configs={len(cfgs)} assets={len(ASSETS)} trials={len(jobs)} fee={FEE}", flush=True)
    t0 = time.perf_counter()
    rows: list[dict[str, Any]] = []
    # Write each row as it lands. The first run of this script died at 700/864 and lost everything
    # because results were only serialised at the end -- a 9-minute job should never be all-or-
    # nothing.
    jsonl = args.out.replace(".json", ".jsonl")
    with ProcessPoolExecutor(max_workers=args.workers) as ex, open(jsonl, "w") as fh:
        for i, row in enumerate(ex.map(_one, jobs, chunksize=4), 1):
            rows.append(row)
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            if i % 100 == 0:
                print(f"  {i}/{len(jobs)}  {time.perf_counter() - t0:.0f}s", flush=True)
    with open(args.out, "w") as fh:
        json.dump({"trials": len(jobs), "fee": str(FEE), "rows": rows}, fh)
    print(f"done in {time.perf_counter() - t0:.0f}s -> {args.out}", flush=True)


if __name__ == "__main__":
    main()
