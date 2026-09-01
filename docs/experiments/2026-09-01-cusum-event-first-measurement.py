"""First measurement of `cusum_event` (#341). Pre-declared, then run.

WHAT IS DECLARED BEFORE THE RUN, so the reader can hold this document to it:

* **Primary metric: `n_trades`.** The question this rule's mechanism actually raises is
  FEASIBILITY -- an event filter trades less by construction, and the ρ=-0.77 bind between edge
  and sample size (2026-08-12-fee-curve-and-rsi-meanrev.md) says a rule that fires rarely cannot
  be admitted no matter what its profit factor looks like. Declared primary exactly as the
  rsi_meanrev diagnostic grid declared it, and for the same reason.
* **Secondary: profit factor at fee 0 / 0.006 / 0.012.** Zero bounds from above everything an
  execution fix could ever buy; 0.006 is the maker rate this account cannot currently reach;
  0.012 is what it pays.
* **Arm A is ONE configuration -- the shipped default.** `threshold_friction_mult=2`,
  `lookback=168`, `atr_period=20`, `atr_stop_mult=2`, `target_rr=3`. One config means no argmax,
  so nothing in arm A is a maximum-of-N and the selection-bias caveat that governs the fee-curve
  document does not apply to it.
* **Arm B is the rule's own headline knob**, `threshold_friction_mult` over {1, 2, 3, 4} at the
  taker fee only. Disclosed as a SWEEP: 4 x 24 = 96 additional trials. Its per-asset best is a
  maximum of four draws and must never be quoted as an edge estimate.

`1` in arm B is the source's own setting and is exactly one round trip on this venue -- included
so the paper's configuration is measured rather than argued about.

Trials disclosed: 24 x 3 = 72 (arm A) + 96 (arm B) = 168.

Re-run:
    KEEL_EXPERIMENT_DB=~/keel/keel.db \
      python docs/experiments/2026-09-01-cusum-event-first-measurement.py
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
JSONL_PATH = f"{OUT_DIR}/cusum_first.jsonl"

#: The same 24-asset universe the restated intersection used, so this result sits directly
#: beside the null it is being compared against rather than beside a different population.
UNIVERSE = [
    "BTC-USD", "ETH-USD", "ADA-USD", "LINK-USD", "LTC-USD", "SOL-USD",
    "XLM-USD", "PAXG-USDT", "BCH-USD", "AAVE-USD", "DOGE-USD", "DOT-USD",
    "UNI-USD", "ZEC-USD", "ALGO-USD", "FET-USD", "CRV-USD", "ICP-USD",
    "AVAX-USD", "NEAR-USD", "XRP-USD", "PAXG-USD", "WLD-USD", "TON-USD",
]

FEES = ["0", "0.006", "0.012"]
SLIPPAGE = Decimal("0.0005")
TAKER = "0.012"
MULTIPLES = ["1", "2", "3", "4"]

#: The admission floors this is measured against (#337/#338).
MIN_TRADES = 100


def build_jobs():
    return [("A", asset, "2") for asset in UNIVERSE] + [
        ("B", asset, mult) for asset in UNIVERSE for mult in MULTIPLES
    ]


def run_job(job):
    arm, asset, mult = job
    from keel_core.types import Granularity

    from keel.data.db import connect
    from keel.data.repository import Repository
    from keel.strategy import backtest as bt
    from keel.strategy.rules.cusum_event import CusumEvent

    fees = FEES if arm == "A" else [TAKER]
    rows = []
    try:
        repo = Repository(connect(DB))
        candles = repo.get_candles(asset, Granularity.ONE_HOUR)
    except Exception as exc:
        return [{
            "arm": arm, "product": asset, "mult": mult,
            "error": f"{type(exc).__name__}: {exc}",
        }]
    if not candles:
        return [{"arm": arm, "product": asset, "mult": mult, "error": "no hourly candles"}]

    for fee in fees:
        try:
            rule = CusumEvent(product_id=asset, threshold_friction_mult=Decimal(mult))
            result = bt.backtest(
                rule, candles, fee_pct=Decimal(fee), slippage_pct=SLIPPAGE
            )
            rows.append({
                "arm": arm,
                "product": asset,
                "mult": mult,
                "fee": fee,
                "bars": len(candles),
                "n_trades": int(result.n_trades),
                "win_rate": float(result.win_rate),
                "profit_factor": float(result.profit_factor),
                "expectancy": float(result.expectancy),
                "max_drawdown": float(result.max_drawdown),
            })
        except Exception as exc:
            rows.append({
                "arm": arm, "product": asset, "mult": mult, "fee": fee,
                "error": f"{type(exc).__name__}: {exc}",
            })
    return rows


def done_combos():
    """Combos already written. Append-only and each row names its own combo, so a killed run
    resumes rather than redoing -- the same discipline as the restated-intersection driver."""
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
        done.add((row["arm"], row["product"], row["mult"]))
    return done


def main():
    jobs = [job for job in build_jobs() if (job[0], job[1], job[2]) not in done_combos()]
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
