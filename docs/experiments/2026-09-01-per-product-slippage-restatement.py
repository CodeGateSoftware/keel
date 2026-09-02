"""What every measured null looks like when execution is priced per product (#335, #259).

**The finding this exists to quantify.** Every experiment document in this repository prices
fills at `slippage_pct=0.0005` -- the FLOOR of `slippage_for_quote_volume`, reached only at the
model's $500M/day anchor. Measured over the 24-asset universe's own cached hourly candles, NOT
ONE ASSET REACHES IT: the range is 1.1x the floor (BTC, 5.5bp) to 36.8x (TON, the 183.8bp cap),
with a median near 10x. #335 named "the STX/CRO-class 1.15-1.30x floor entries" as the live
example of a thin-asset candidate; the live example is the entire universe.

The direction is conservative -- real costs are HIGHER, so real profit factors are LOWER, and
per-product pricing can only make a null more negative, never rescue one. That is why #259's
deferral was safe. It is not why it should continue.

DECLARED BEFORE THE RUN:

* **Primary metric: the DELTA in profit factor, flat vs per-product, at the taker rate.** Both
  arms are run HERE rather than compared against numbers in other documents, so the A/B is
  internally consistent and no cross-document drift can enter it.
* **Every shipped signal rule plus both #341/#342 rules**, at their shipped defaults. No sweep,
  no argmax, no free parameters: 5 rules x 24 assets x 2 slippage regimes = 240 trials.
* **The pre-declared expectation is that nothing changes verdict** -- the intersection is
  already empty, and a strictly higher cost cannot fill it. What is being measured is the SIZE
  of the understatement, and whether any cell that looked positive under flat pricing survives.

Re-run:
    KEEL_EXPERIMENT_DB=~/keel/keel.db \
      python docs/experiments/2026-09-01-per-product-slippage-restatement.py
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
JSONL_PATH = f"{OUT_DIR}/per_product_slippage.jsonl"

UNIVERSE = [
    "BTC-USD", "ETH-USD", "ADA-USD", "LINK-USD", "LTC-USD", "SOL-USD",
    "XLM-USD", "PAXG-USDT", "BCH-USD", "AAVE-USD", "DOGE-USD", "DOT-USD",
    "UNI-USD", "ZEC-USD", "ALGO-USD", "FET-USD", "CRV-USD", "ICP-USD",
    "AVAX-USD", "NEAR-USD", "XRP-USD", "PAXG-USD", "WLD-USD", "TON-USD",
]
RULES = ["turtle_breakout", "rsi_meanrev", "pullback_continuation", "cusum_event", "triple_barrier"]
TAKER = Decimal("0.012")
#: Bars per day at ONE_HOUR -- `median_daily_quote_volume` is a PER-BAR median despite its name,
#: and the slippage model is anchored on a DAILY figure. See `triple_barrier`'s own note.
BARS_PER_DAY = 24


def make_rule(kind, asset):
    from keel.strategy.rules.cusum_event import CusumEvent
    from keel.strategy.rules.pullback_continuation import PullbackContinuation
    from keel.strategy.rules.rsi_meanrev import RsiMeanReversion
    from keel.strategy.rules.triple_barrier import TripleBarrier
    from keel.strategy.rules.turtle_breakout import TurtleBreakout

    return {
        "turtle_breakout": TurtleBreakout,
        "rsi_meanrev": RsiMeanReversion,
        "pullback_continuation": PullbackContinuation,
        "cusum_event": CusumEvent,
        "triple_barrier": TripleBarrier,
    }[kind](product_id=asset)


def run_job(job):
    kind, asset = job
    from keel_core.types import Granularity

    from keel.compliance.screen import median_daily_quote_volume
    from keel.data.db import connect
    from keel.data.repository import Repository
    from keel.strategy import backtest as bt

    try:
        repo = Repository(connect(DB))
        candles = repo.get_candles(asset, Granularity.ONE_HOUR)
    except Exception as exc:
        return [{"rule": kind, "product": asset, "error": f"{type(exc).__name__}: {exc}"}]
    if not candles:
        return [{"rule": kind, "product": asset, "error": "no hourly candles"}]

    daily_volume = median_daily_quote_volume(candles) * BARS_PER_DAY
    per_product = bt.slippage_for_quote_volume(daily_volume)
    rows = []
    for regime, slippage in (("flat", bt.SLIPPAGE_FLOOR_PCT), ("per_product", per_product)):
        try:
            result = bt.backtest(
                make_rule(kind, asset), candles, fee_pct=TAKER, slippage_pct=slippage
            )
            rows.append({
                "rule": kind,
                "product": asset,
                "regime": regime,
                "slippage_pct": str(slippage),
                "floor_multiple": float(per_product / bt.SLIPPAGE_FLOOR_PCT),
                "daily_quote_volume": float(daily_volume),
                "n_trades": int(result.n_trades),
                "profit_factor": float(result.profit_factor),
                "win_rate": float(result.win_rate),
                "expectancy": float(result.expectancy),
            })
        except Exception as exc:
            rows.append({
                "rule": kind, "product": asset, "regime": regime,
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
        done.add((row["rule"], row["product"]))
    return done


def main():
    jobs = [
        (kind, asset)
        for kind in RULES
        for asset in UNIVERSE
        if (kind, asset) not in done_combos()
    ]
    print(f"{len(jobs)} combos ({len(jobs) * 2} trials) -> {JSONL_PATH}", flush=True)
    started = time.time()
    with open(JSONL_PATH, "a") as sink, ProcessPoolExecutor() as pool:
        futures = {pool.submit(run_job, job): job for job in jobs}
        for index, future in enumerate(as_completed(futures), start=1):
            for row in future.result():
                sink.write(json.dumps(row) + "\n")
            sink.flush()
            if index % 20 == 0 or index == len(jobs):
                print(f"  {index}/{len(jobs)}", flush=True)
    print(f"done in {time.time() - started:.0f}s", flush=True)


if __name__ == "__main__":
    main()
