"""Is there ANY asset-rule combination that clears the trade floor and survives its own costs?

## Provenance of this docstring -- read this first

Unlike the other harnesses in this directory, the pre-registration for this run did NOT live in
this file. The two arms, the fee grid, the universe, the slippage pin and the pass/fail criteria
were fixed in the brief that dispatched the implementing agent, BEFORE any of it ran; this
docstring was written afterwards and reproduces that brief. That is weaker than a committed
docstring -- the brief is in a session transcript, not in git -- and it is recorded here rather
than quietly presented as if the file had always said so. Future runs should put the declaration
in the file.

What was NOT decided in advance: the two probes in `probe_tail()` and `probe_regime()` below.
Both were written after seeing Arm A's results, are post-hoc by construction, and are reported in
the write-up as diagnostics rather than as gates for exactly that reason.

## The question

Every negative result so far describes `turtle_breakout` at parameters someone chose. This asks a
narrower question of the whole shipped library: at the constructor defaults nobody tuned on this
corpus, is there any asset where a rule both fires enough to be admitted (`min_trades=100`) and
keeps a profit factor above 1.0 once costs are charged?

## Arm A -- a_priori, zero free parameters

All three signal rules built with `product_id=<asset>` and nothing else, so every remaining
parameter takes its shipped default. Those defaults predate this corpus and were never fitted to
it, which makes them the only genuinely unselected configuration available. `dca` is excluded: it
is scheduled accumulation, not a signal edge. 3 rules x 24 assets = 72 combinations.

## Arm B -- out-of-sample transfer

`{entry_lookback: 336, exit_lookback: 80, atr_stop_mult: 2, target_rr: 6, adx_threshold: 25}` was
the mean-across-assets winner of the 864-trial sweep, scored on ZEC/FET/SOL/DOGE/ETH/BTC. Arm B
evaluates that config on the 18 assets NOT in that six, which are therefore disjoint from
everything the selection saw. This arm selects nothing, so it spends no further multiple-testing
budget -- it only asks whether the sweep's choice generalises.

## Costs

Fees at 0 / 0.006 (maker) / 0.012 (taker), with `slippage_pct` pinned EXPLICITLY at 0.0005 rather
than inherited. The pin is the point: `backtest()` applies 5bp of slippage by default, so a "zero
fee" column is zero FEE and not zero COST, and earlier work that omitted the argument was quietly
carrying it anyway. Passing both explicitly also makes every number here independent of the
library defaults #247 changed.

## Criteria, declared before the run

    C1  n_trades >= 100                     (the promotion floor)
    C2  C1 AND profit_factor @ 0%    > 1.0  (gross edge exists at all)
    C3  C2 AND profit_factor @ 0.6%  > 1.0  (survives the cheapest rate reachable)
        and the same at 1.2%, the rate actually paid.
"""

import json
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from decimal import Decimal

DB = "/Users/elmehdiaitbrahim/keel/keel.db"
OUT_DIR = "/private/tmp/claude-501/-Users-elmehdiaitbrahim-Development-work-CodeGate-keel/28ff9a61-09d1-498b-b325-1631c0662734/scratchpad"
JSONL_PATH = f"{OUT_DIR}/intersection.jsonl"
JSON_PATH = f"{OUT_DIR}/intersection.json"

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
    from keel.strategy.rules.turtle_breakout import TurtleBreakout
    from keel.strategy.rules.rsi_meanrev import RsiMeanReversion
    from keel.strategy.rules.pullback_continuation import PullbackContinuation

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
    from keel.data.db import connect
    from keel.data.repository import Repository
    from keel.strategy import backtest as bt
    from keel_core.types import Granularity

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
    print(f"Declared jobs: {total_declared}; already done: {len(done)}; running: {total}", flush=True)

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


# ---------------------------------------------------------------------------------------------
# POST-HOC PROBES. Written AFTER seeing Arm A; diagnostics, never gates. See the module docstring.
# ---------------------------------------------------------------------------------------------

#: Complete calendar years only. 2021 starts mid-July and 2026 ends in July/August, so both are
#: partial buckets -- and including them CHANGES the streak verdict for 4 of 21 assets. Both
#: bucketings are reported for that reason; neither is authoritative.
FULL_YEARS = (2022, 2023, 2024, 2025)
ALL_YEARS = (2021, 2022, 2023, 2024, 2025, 2026)


def _closed(result):
    """Closed trades with a realised pnl.

    `summarize()` already excludes open trades from every aggregate, and an unclosed position is
    carried in `.trades` with `pnl=None` for visibility. Marking it to market at the final candle
    was considered and rejected: it injects an unrealised price into a realised-pnl metric and
    flatters trend-following, which tends to be holding a winner when a series truncates.
    """
    return [t for t in result.trades if t.outcome != "open" and t.pnl is not None]


def probe_tail(result):
    """Profit factor with the best 1 and best 3 winning trades deleted, losses all retained.

    A deliberately harsh stress, NOT an unbiased estimator: it answers "how few trades carry
    this?", not "what is the edge?". Concentration is expected of a breakout rule -- a fat right
    tail IS the strategy -- so a low ex-top3 is evidence about effective sample size, not about
    whether edge exists.
    """
    pnl = sorted((float(t.pnl) for t in _closed(result)), reverse=True)
    wins = [x for x in pnl if x > 0]
    gross_loss = -sum(x for x in pnl if x < 0)
    if not gross_loss or not wins:
        return {}
    return {
        "gross_pf": sum(wins) / gross_loss,
        "top1_share": wins[0] / sum(wins),
        "top3_share": sum(wins[:3]) / sum(wins),
        "ex_top1": sum(wins[1:]) / gross_loss,
        "ex_top3": sum(wins[3:]) / gross_loss,
    }


def probe_regime(result):
    """Gross profit factor per calendar year, and the worst run of consecutive losing years.

    Reported, never gated, and the write-up says why at length. Two independent reasons:
    (a) it is underpowered -- for an edgeless strategy yearly outcomes are near coin-flips, and
        only 8 of 16 length-4 sequences contain two consecutive losses, so a
        no-consecutive-losing-years rule passes pure noise about half the time;
    (b) ZEC under `pullback_continuation` is the counter-example that settles it -- the only
        combination in the study with no losing complete year (1.06/1.17/1.16/1.13), at a gross
        profit factor of 0.875. Stationary at losing slightly, reliably, forever.
    """
    import datetime as dt

    per = {}
    for t in _closed(result):
        ts = getattr(t, "exit_ts", None) or getattr(t, "entry_ts", None)
        if ts:
            per.setdefault(dt.datetime.utcfromtimestamp(ts).year, []).append(float(t.pnl))

    pfs = {}
    for year, trades in per.items():
        won = sum(v for v in trades if v > 0)
        lost = -sum(v for v in trades if v < 0)
        pfs[year] = (won / lost) if lost else float("inf")

    def streak(years):
        run = worst = 0
        for year in years:
            if year not in pfs:
                continue
            run = run + 1 if pfs[year] < 1.0 else 0
            worst = max(worst, run)
        return worst

    return {"pfs": pfs, "streak_all": streak(ALL_YEARS), "streak_full": streak(FULL_YEARS)}


if __name__ == "__main__":
    main()
