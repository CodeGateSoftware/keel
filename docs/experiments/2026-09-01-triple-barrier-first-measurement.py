"""First measurement of `triple_barrier` (#342). Pre-declared, then run.

**This is an A/B against a control, which is what makes it worth running at all.** `cusum_event`
(measured 2026-09-01, same universe, same window) and `triple_barrier` share an entry: the same
CUSUM filter at the same threshold. What differs is the EXIT -- ATR barriers with a Donchian-free
signal exit, against friction-sized barriers with a vertical time stop. Holding the entry fixed
makes the exit the only thing that changed, so the difference is attributable.

DECLARED BEFORE THE RUN:

* **Primary metric: the DELTA in profit factor against `cusum_event` at the taker rate.** Not
  the level -- the level is already known to be a null, and asking "does it clear 1.0" invites
  reading a 0.4 as encouraging. The question is how much a better exit moves a rule whose entry
  has no gross edge, and the honest expected answer is "a little, and not enough".
* **Secondary: `n_trades`** (a vertical barrier closes positions that would otherwise run, so
  trade count should RISE) and profit factor at fee 0 / 0.006 / 0.012.
* **Arm A is ONE configuration** -- the shipped default (entry mult 2, target 4, stop 2, 24-bar
  vertical). No argmax.
* **Arm B is the vertical barrier alone**, `max_holding_bars` in {6, 12, 24, 48, 72} at the
  taker rate: the one leg no other rule has, and the only knob the source grid-searched.
  Disclosed as a sweep, 5 x 24 = 120 trials; its per-asset best is a maximum of five draws.

Trials disclosed: 24 x 3 = 72 (arm A) + 120 (arm B) = 192.

Re-run:
    KEEL_EXPERIMENT_DB=~/keel/keel.db \
      python docs/experiments/2026-09-01-triple-barrier-first-measurement.py
"""

import json
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from decimal import Decimal
from pathlib import Path

DB = os.environ.get("KEEL_EXPERIMENT_DB") or str(Path.home() / "keel" / "keel.db")
OUT_DIR = os.environ.get("KEEL_EXPERIMENT_OUT") or str(Path(__file__).resolve().parent / "_out")
Path(OUT_DIR).mkdir(parents=True, exist_ok=True)
JSONL_PATH = f"{OUT_DIR}/triple_barrier_first.jsonl"

UNIVERSE = [
    "BTC-USD", "ETH-USD", "ADA-USD", "LINK-USD", "LTC-USD", "SOL-USD",
    "XLM-USD", "PAXG-USDT", "BCH-USD", "AAVE-USD", "DOGE-USD", "DOT-USD",
    "UNI-USD", "ZEC-USD", "ALGO-USD", "FET-USD", "CRV-USD", "ICP-USD",
    "AVAX-USD", "NEAR-USD", "XRP-USD", "PAXG-USD", "WLD-USD", "TON-USD",
]

FEES = ["0", "0.006", "0.012"]
SLIPPAGE = Decimal("0.0005")
TAKER = "0.012"
HOLDINGS = ["6", "12", "24", "48", "72"]


def build_jobs():
    return [("A", asset, "24") for asset in UNIVERSE] + [
        ("B", asset, bars) for asset in UNIVERSE for bars in HOLDINGS
    ]


def run_job(job):
    arm, asset, bars = job
    from keel_core.types import Granularity

    from keel.data.db import connect
    from keel.data.repository import Repository
    from keel.strategy import backtest as bt
    from keel.strategy.rules.triple_barrier import TripleBarrier

    fees = FEES if arm == "A" else [TAKER]
    rows = []
    try:
        repo = Repository(connect(DB))
        candles = repo.get_candles(asset, Granularity.ONE_HOUR)
    except Exception as exc:
        return [{
            "arm": arm, "product": asset, "bars": bars,
            "error": f"{type(exc).__name__}: {exc}",
        }]
    if not candles:
        return [{"arm": arm, "product": asset, "bars": bars, "error": "no hourly candles"}]

    for fee in fees:
        try:
            rule = TripleBarrier(product_id=asset, max_holding_bars=int(bars))
            result = bt.backtest(rule, candles, fee_pct=Decimal(fee), slippage_pct=SLIPPAGE)
            rows.append({
                "arm": arm,
                "product": asset,
                "bars": bars,
                "fee": fee,
                "candles": len(candles),
                "n_trades": int(result.n_trades),
                "win_rate": float(result.win_rate),
                "profit_factor": float(result.profit_factor),
                "expectancy": float(result.expectancy),
                "max_drawdown": float(result.max_drawdown),
            })
        except Exception as exc:
            rows.append({
                "arm": arm, "product": asset, "bars": bars, "fee": fee,
                "error": f"{type(exc).__name__}: {exc}",
            })
    return rows


def done_combos():
    if not os.path.exists(JSONL_PATH):
        return set()
    done = set()
    for line in open(JSONL_PATH):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue  # torn final line from a kill mid-write
        done.add((row["arm"], row["product"], row["bars"]))
    return done


def main():
    jobs = [job for job in build_jobs() if job not in done_combos()]
    print(f"{len(jobs)} combos to run -> {JSONL_PATH}", flush=True)
    started = time.time()
    with open(JSONL_PATH, "a") as sink, ProcessPoolExecutor() as pool:
        futures = {pool.submit(run_job, job): job for job in jobs}
        for index, future in enumerate(as_completed(futures), start=1):
            for row in future.result():
                sink.write(json.dumps(row) + "\n")
            sink.flush()
            print(f"  {index}/{len(jobs)} {futures[future]}", flush=True)
    print(f"done in {time.time() - started:.0f}s", flush=True)


if __name__ == "__main__":
    main()
