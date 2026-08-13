import json
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from decimal import Decimal
from pathlib import Path

# Resolved rather than hardcoded: these were absolute literals into one laptop's home and into a
# per-session temp directory that is collected when the session ends -- so this recorded run could
# not be re-run, which is the one thing a recorded run is for. `_out/` sits beside this script
# (gitignored) so the resumable JSONL survives and the rsi-scale scripts find it as their ANCHOR.
# Override either with KEEL_EXPERIMENT_DB / KEEL_EXPERIMENT_OUT.
DB = os.environ.get("KEEL_EXPERIMENT_DB") or str(Path.home() / "keel" / "keel.db")
OUT_DIR = os.environ.get("KEEL_EXPERIMENT_OUT") or str(Path(__file__).resolve().parent / "_out")
Path(OUT_DIR).mkdir(parents=True, exist_ok=True)
JSONL_PATH = f"{OUT_DIR}/intersection_257.jsonl"
JSON_PATH = f"{OUT_DIR}/intersection_257.json"

UNIVERSE = [
    "BTC-USD", "ETH-USD", "ADA-USD", "LINK-USD", "LTC-USD", "SOL-USD",
    "XLM-USD", "PAXG-USDT", "BCH-USD", "AAVE-USD", "DOGE-USD", "DOT-USD",
    "UNI-USD", "ZEC-USD", "ALGO-USD", "FET-USD", "CRV-USD", "ICP-USD",
    "AVAX-USD", "NEAR-USD", "XRP-USD", "PAXG-USD", "WLD-USD", "TON-USD",
]

ARM_B_EXCLUDE = {"ZEC-USD", "FET-USD", "SOL-USD", "DOGE-USD", "ETH-USD", "BTC-USD"}
ARM_B_UNIVERSE = [a for a in UNIVERSE if a not in ARM_B_EXCLUDE]

FEES = ["0", "0.006", "0.012"]
SLIPPAGE = Decimal("0.0005")

RULES = ["turtle", "rsi", "pullback"]


def build_jobs():
    jobs = []
    for rule in RULES:
        for asset in UNIVERSE:
            jobs.append(("A", rule, asset))
    for asset in ARM_B_UNIVERSE:
        jobs.append(("B", "turtle", asset))
    return jobs


def make_rule(arm, rule, asset):
    from keel.strategy.rules.pullback_continuation import PullbackContinuation
    from keel.strategy.rules.rsi_meanrev import RsiMeanReversion
    from keel.strategy.rules.turtle_breakout import TurtleBreakout

    if arm == "A":
        if rule == "turtle":
            return TurtleBreakout(product_id=asset)
        elif rule == "rsi":
            return RsiMeanReversion(product_id=asset)
        elif rule == "pullback":
            return PullbackContinuation(product_id=asset)
        else:
            raise ValueError(f"unknown rule {rule}")
    elif arm == "B":
        if rule != "turtle":
            raise ValueError("arm B is turtle only")
        return TurtleBreakout(
            product_id=asset,
            entry_lookback=336,
            exit_lookback=80,
            atr_stop_mult=Decimal("2"),
            target_rr=Decimal("6"),
            adx_threshold=25.0,
        )
    else:
        raise ValueError(f"unknown arm {arm}")


def run_job(job):
    arm, rule, asset = job
    from keel_core.types import Granularity

    from keel.data.db import connect
    from keel.data.repository import Repository
    from keel.strategy import backtest as bt

    rows = []
    try:
        repo = Repository(connect(DB))
        candles = repo.get_candles(asset, Granularity.ONE_HOUR)
    except Exception as e:
        for fee in FEES:
            rows.append({
                "arm": arm, "rule": rule, "product": asset, "fee": fee,
                "error": f"{type(e).__name__}: {e}",
            })
        return rows

    for fee in FEES:
        try:
            rule_obj = make_rule(arm, rule, asset)
            result = bt.backtest(
                rule_obj, candles, fee_pct=Decimal(fee), slippage_pct=SLIPPAGE
            )
            rows.append({
                "arm": arm,
                "rule": rule,
                "product": asset,
                "fee": fee,
                "n_trades": int(result.n_trades),
                "win_rate": float(result.win_rate),
                "profit_factor": float(result.profit_factor),
                "expectancy": float(result.expectancy),
                "max_drawdown": float(result.max_drawdown),
            })
        except Exception as e:
            rows.append({
                "arm": arm, "rule": rule, "product": asset, "fee": fee,
                "error": f"{type(e).__name__}: {e}",
            })
    return rows


def done_combos():
    """Combos already in the JSONL. The first run died at 25/90 with the pool still
    holding results; the file is append-only and each row names its own combo, so the
    completed set is recoverable exactly. Resume rather than redo."""
    import os

    if not os.path.exists(JSONL_PATH):
        return set()
    done = set()
    for line in open(JSONL_PATH):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue  # torn final line from a kill mid-write
        done.add((r["arm"], r["rule"], r["product"]))
    return done


def main():
    jobs = build_jobs()
    total_declared = len(jobs)
    done = done_combos()
    jobs = [j for j in jobs if j not in done]

    # Run order is a scheduling choice ONLY -- every declared job still runs, and a backtest is
    # independent of the order it is dispatched in, so this cannot touch the result.
    # `rsi` at the shipped defaults is by far the slowest cell: `backtest()` calls `rule.detect()`
    # only while flat, so the rule that almost never fires pays full level-detection cost on
    # nearly all 44k bars. Leaving it first starves the two arms that actually answer the
    # open question (Arm B's out-of-sample transfer, and pullback's first-ever run) for an hour.
    prio = {("B", "turtle"): 0, ("A", "pullback"): 1, ("A", "rsi"): 2}
    jobs.sort(key=lambda j: prio.get((j[0], j[1]), 3))
    total = len(jobs)
    print(
        f"Declared jobs: {total_declared}; already done: {len(done)}; running: {total}",
        flush=True,
    )

    start = time.time()
    completed = 0
    all_rows = []

    with open(JSONL_PATH, "a") as jf:
        with ProcessPoolExecutor(max_workers=8) as ex:
            futures = {ex.submit(run_job, job): job for job in jobs}
            for fut in as_completed(futures):
                job = futures[fut]
                try:
                    rows = fut.result()
                except Exception as e:
                    arm, rule, asset = job
                    rows = [{
                        "arm": arm, "rule": rule, "product": asset, "fee": fee,
                        "error": f"{type(e).__name__}: {e}",
                    } for fee in FEES]

                for row in rows:
                    jf.write(json.dumps(row) + "\n")
                    jf.flush()
                    all_rows.append(row)

                completed += 1
                if completed % 10 == 0 or completed == total:
                    elapsed = time.time() - start
                    print(f"Progress: {completed}/{total} jobs, elapsed={elapsed:.1f}s", flush=True)

    # Serialise the WHOLE jsonl, not just this process's rows -- on a resume `all_rows`
    # holds only the newly-run subset and dumping it would silently drop the earlier run.
    final = [json.loads(x) for x in open(JSONL_PATH) if x.strip()]
    with open(JSON_PATH, "w") as f:
        json.dump(final, f, indent=2)

    elapsed = time.time() - start
    print(f"Done. {len(all_rows)} new rows, {len(final)} total. elapsed={elapsed:.1f}s", flush=True)


if __name__ == "__main__":
    main()
