"""The 08-13 restatement, restated: what three engine corrections and two new rule
families do to a null measured on 3 rules and a flat 5bp fill (#259/#335, #442, #523).

**Why this exists.** `2026-08-13-restated-under-a-production-faithful-engine.md` is the
document the front page of keeltrading.com cites for "0 of 90" and "0 of 82". Three things
have happened to the engine since it was written, and one thing has happened to the rule
library, and none of them is visible in that record:

* **#334/#335 -- per-product slippage.** The 08-13 run priced every fill at
  `slippage_pct=0.0005`, the FLOOR of `slippage_for_quote_volume`. The 09-01 restatement
  measured that no asset in the universe reaches it (1.1x to 36.8x, median ~10x). Since #335
  `rules backtest`/`rules promote` price per product; the flat floor is no longer what the
  engine does.
* **#442 -- a bar that gaps through the stop exits at that bar's OPEN**, not at the stop.
  Containment-only touch checks let such a bar pass silently. This is a fill-model change in
  the conservative direction and it did not exist on 2026-08-13.
* **#523 -- the slippage cap is the corpus tail (183.8bp), not a round 50bp.** Under the old
  cap 17 of 30 cached products were flattened onto one rate; the thin half of the universe
  was being priced by the flat model the per-product model shipped to remove.
* **Two more shipped signal families** -- `cusum_event` (#341) and `triple_barrier` (#342).
  08-13 sec.6 claims "every signal rule the codebase ships has been measured". Three shipped
  then; five do now (six with `dca`, which never closes and carries no profit factor).

DECLARED BEFORE THE RUN:

* **Primary metric: the count of cells clearing `n >= 100 AND profit_factor > 1.0` at the
  taker rate under per-product pricing.** That is the exact predicate 08-13 reported as
  "0 of 90" and "0 of 82"; this run re-evaluates it on today's engine over a widened
  population. The pre-declared expectation is that it stays 0 -- every correction since is
  conservative-only -- and what is being measured is the SIZE of the drift in the numbers
  underneath a verdict that has been quoted for three weeks without re-measurement.
* **Both slippage regimes are run HERE**, in one driver, so the flat arm is a like-for-like
  bridge back to 08-13's printed numbers and the per-product arm is what the engine now
  does. No cross-document comparison is required to read the delta.
* **No sweep in arm A, no argmax.** Shipped defaults only: 5 rules x 24 assets x 3 fees
  x 2 regimes = 720 trials. `dca` is excluded and the exclusion is a statement, not an
  omission: its sleeve never closes, so profit factor is undefined for it (sec.12.6).
* **Arm B is 08-13 sec.3.2's transfer check**, unchanged config, re-run on both regimes so the
  in-sample/out-of-sample gap is recomputed on one engine rather than inherited.
* **Arm C is 08-13 sec.4's frequency axis** -- `rsi_meanrev`'s `oversold` in {25,30,35,40};
  the 20 rows come from arm A, which is the shipped default. Taker rate, both regimes.

Re-run:
    KEEL_EXPERIMENT_DB=~/keel/keel.db \
      python docs/experiments/2026-09-05-restatement-restated.py
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
JSONL_PATH = f"{OUT_DIR}/restatement_restated.jsonl"

#: The 24-asset universe of the 08-12/08-13/09-01 documents, unchanged so this run sits
#: beside the null it restates rather than beside a different population.
UNIVERSE = [
    "BTC-USD", "ETH-USD", "ADA-USD", "LINK-USD", "LTC-USD", "SOL-USD",
    "XLM-USD", "PAXG-USDT", "BCH-USD", "AAVE-USD", "DOGE-USD", "DOT-USD",
    "UNI-USD", "ZEC-USD", "ALGO-USD", "FET-USD", "CRV-USD", "ICP-USD",
    "AVAX-USD", "NEAR-USD", "XRP-USD", "PAXG-USD", "WLD-USD", "TON-USD",
]
#: 08-13 sec.3.2's six selection assets. Arm B runs the whole universe and splits on this set,
#: so the in-sample and out-of-sample halves come from one run at one cost model.
ARM_B_SELECTION = {"ZEC-USD", "FET-USD", "SOL-USD", "DOGE-USD", "ETH-USD", "BTC-USD"}

SIGNAL_RULES = [
    "turtle_breakout", "rsi_meanrev", "pullback_continuation", "cusum_event", "triple_barrier"
]
FEES = ["0", "0.006", "0.012"]
TAKER = Decimal("0.012")
OVERSOLD_LEVELS = [25.0, 30.0, 35.0, 40.0]

#: `median_daily_quote_volume` is a PER-BAR median despite its name and the slippage curve is
#: anchored on a DAILY figure -- the unit trap #342 documents. Scale before pricing.
BARS_PER_DAY = 24


def make_rule(arm, kind, asset, oversold=None):
    from keel.strategy.rules.cusum_event import CusumEvent
    from keel.strategy.rules.pullback_continuation import PullbackContinuation
    from keel.strategy.rules.rsi_meanrev import RsiMeanReversion
    from keel.strategy.rules.triple_barrier import TripleBarrier
    from keel.strategy.rules.turtle_breakout import TurtleBreakout

    if arm == "A":
        return {
            "turtle_breakout": TurtleBreakout,
            "rsi_meanrev": RsiMeanReversion,
            "pullback_continuation": PullbackContinuation,
            "cusum_event": CusumEvent,
            "triple_barrier": TripleBarrier,
        }[kind](product_id=asset)
    if arm == "B":
        # 08-13 sec.3.2's config, character for character.
        return TurtleBreakout(
            product_id=asset,
            entry_lookback=336,
            exit_lookback=80,
            atr_stop_mult=Decimal("2"),
            target_rr=Decimal("6"),
            adx_threshold=25.0,
        )
    if arm == "C":
        return RsiMeanReversion(product_id=asset, oversold=oversold)
    raise ValueError(f"unknown arm {arm}")


def run_job(job):
    arm, kind, asset, oversold = job
    from keel_core.types import Granularity

    from keel.compliance.screen import median_daily_quote_volume
    from keel.data.db import connect
    from keel.data.repository import Repository
    from keel.strategy import backtest as bt

    key = {"arm": arm, "rule": kind, "product": asset, "oversold": oversold}
    try:
        repo = Repository(connect(DB))
        candles = repo.get_candles(asset, Granularity.ONE_HOUR)
    except Exception as exc:
        return [{**key, "error": f"{type(exc).__name__}: {exc}"}]
    if not candles:
        return [{**key, "error": "no hourly candles"}]

    daily_volume = median_daily_quote_volume(candles) * BARS_PER_DAY
    per_product = bt.slippage_for_quote_volume(daily_volume)
    regimes = (("flat", bt.SLIPPAGE_FLOOR_PCT), ("per_product", per_product))
    fees = FEES if arm == "A" else ["0.012"]

    rows = []
    for regime, slippage in regimes:
        for fee in fees:
            try:
                result = bt.backtest(
                    make_rule(arm, kind, asset, oversold),
                    candles,
                    fee_pct=Decimal(fee),
                    slippage_pct=slippage,
                )
                rows.append({
                    **key,
                    "regime": regime,
                    "fee": fee,
                    "slippage_pct": str(slippage),
                    "floor_multiple": float(per_product / bt.SLIPPAGE_FLOOR_PCT),
                    "daily_quote_volume": float(daily_volume),
                    "bars": len(candles),
                    "n_trades": int(result.n_trades),
                    "win_rate": float(result.win_rate),
                    "profit_factor": float(result.profit_factor),
                    "expectancy": float(result.expectancy),
                    "max_drawdown": float(result.max_drawdown),
                })
            except Exception as exc:
                rows.append({
                    **key, "regime": regime, "fee": fee,
                    "error": f"{type(exc).__name__}: {exc}",
                })
    return rows


def build_jobs():
    jobs = []
    for kind in SIGNAL_RULES:
        for asset in UNIVERSE:
            jobs.append(("A", kind, asset, None))
    for asset in UNIVERSE:
        jobs.append(("B", "turtle_breakout", asset, None))
    for level in OVERSOLD_LEVELS:
        for asset in UNIVERSE:
            jobs.append(("C", "rsi_meanrev", asset, level))
    return jobs


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
        done.add((row["arm"], row["rule"], row["product"], row.get("oversold")))
    return done


def main():
    declared = build_jobs()
    done = done_combos()
    jobs = [j for j in declared if j not in done]
    # Scheduling only -- a backtest is independent of dispatch order. `rsi_meanrev` at a LOW
    # oversold is the slowest cell in the codebase (`backtest()` calls `detect()` only while
    # flat, so the rule that rarely fires pays full detection on ~45k bars) and it gets cheaper
    # as oversold rises. Longest-first keeps the tail from stranding a single worker.
    cost = {("A", "rsi_meanrev"): 0, ("C", "rsi_meanrev"): 1, ("A", "pullback_continuation"): 2}
    jobs.sort(key=lambda j: (cost.get((j[0], j[1]), 3), j[3] or 0))
    print(f"declared {len(declared)} combos; done {len(done)}; running {len(jobs)}", flush=True)

    started = time.time()
    with open(JSONL_PATH, "a") as sink, ProcessPoolExecutor(max_workers=14) as pool:
        futures = {pool.submit(run_job, job): job for job in jobs}
        for index, future in enumerate(as_completed(futures), start=1):
            for row in future.result():
                sink.write(json.dumps(row) + "\n")
            sink.flush()
            print(f"  {index}/{len(jobs)} {futures[future]} "
                  f"[{time.time() - started:.0f}s]", flush=True)
    print(f"done in {time.time() - started:.0f}s", flush=True)


if __name__ == "__main__":
    main()
