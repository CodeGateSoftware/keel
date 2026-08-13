"""Does `rsi_meanrev`'s gross edge survive being made to fire more often?

PRE-REGISTERED IN THIS FILE, BEFORE THE RUN. This docstring is the declaration -- unlike
`2026-08-12-shipped-defaults-intersection.py`, whose declaration lived in a dispatch brief and had
to be reconstructed afterwards. That was recorded as a defect; this is the correction.

## The question, and why it is not an optimisation

`2026-08-12-shipped-defaults-intersection.md` measured all three shipped rules at their
constructor defaults and found `rsi_meanrev` has the BEST gross-edge distribution of the three
(median gross PF 1.1631, 58% of assets gross-positive) while reaching the promotion floor on ZERO
of 24 assets (median n=38 against min_trades=100). It does not lose. It is not observable.

Two explanations, with opposite consequences, and no data yet separates them:

  (a) the edge is REAL and the defaults are simply over-constrained -- in which case relaxing
      them reaches n>=100 with the edge intact, and this is the only promotable rule in the
      codebase;
  (b) the edge is an ARTIFACT OF SELECTIVITY -- the rule looks good precisely because it only
      fires on the rare, easy setups, and buying trades means accepting worse ones.

## The design, and why it cannot cherry-pick

This is a MONOTONICITY TEST, not a search. The reported statistic is the SLOPE of gross profit
factor against n, computed per asset across the oversold levels, then averaged across the 24
assets. **The best cell is never reported as a result.** With 120 cells a maximum is guaranteed;
a slope is not, and a slope cannot be manufactured by trying more cells.

PRE-REGISTERED PREDICTION, recorded so the result can contradict it:

    slope < 0   -> hypothesis (b). The rule is unpromotable BY CONSTRUCTION: no parameter choice
                   escapes buying volume with quality. Report and retire the line of enquiry.
    slope >= 0  -> hypothesis (a). The edge is scalable and the defaults are the problem. This is
                   the single outcome in the whole study that points at a promotable rule, and it
                   would then need a fresh out-of-sample confirmation before any promotion.

Either outcome is publishable. Neither requires a winner, which is the point.

## Axes

VARIABLE, one only:
    oversold in {20, 25, 30, 35, 40}

`oversold` is the entire frequency mechanism and this is measured, not assumed: the 108-cell
diagnostic in `2026-08-12-fee-curve-and-rsi-meanrev-diag.py` found oversold 25->30 multiplied
trade count x2.18 and 30->35 by x3.93, against x1.186 for `support_proximity_pct` and x1.185 for
`level_min_touches`. The other two are noise on this axis.

FIXED, deliberately:
    overbought            = 80     (shipped default)
    support_proximity_pct = 0.005  (shipped default)
    everything else       = shipped defaults

`overbought` is held not because it is a weak lever but because it is the WRONG KIND of lever: it
governs the exit side, so moving it changes trade OUTCOMES and not merely trade COUNTS. If both
axes move, a fall in gross PF cannot be attributed to firing more rather than exiting differently,
and the slope -- the entire point of the run -- becomes uninterpretable.

`support_proximity_pct` is held because it is a STRUCTURAL filter (distance to a level) while
`oversold` is a MOMENTUM filter. Sweeping both confounds "does firing more degrade edge" with
"does relaxing which filter degrade edge".

## Anchor

oversold=20 is the shipped default and was already measured across all 24 assets by the
intersection run. Those rows are REUSED as the curve's left-hand anchor rather than recomputed, so
this grid runs 4 new levels x 24 assets = 96 combinations. The anchor rows are identical in every
other parameter (all defaults) and in cost treatment (same three fees, same explicit 0.0005
slippage pin), which is what makes them poolable with the new ones.

## Conditional second arm, DECLARED NOW so it cannot become a post-hoc rescue

    TRIGGER: if FEWER THAN 8 of 24 assets reach n >= 100 at oversold = 40.
    THEN:    add support_proximity_pct in {0.005, 0.02, 0.05} as a second axis.
    REPORT:  as a SEPARATE curve. Never pooled with, averaged into, or compared cell-to-cell
             against the primary arm -- it varies a different filter and answers a different
             question.

Declaring the trigger and the reporting rule before any data exists is what stops the widening
from being invented later to rescue a disappointing primary arm. It writes to its own output file
for the same reason.

## Costs

Fees 0 / 0.006 (maker) / 0.012 (taker); `slippage_pct` pinned EXPLICITLY at 0.0005. The pin makes
every figure independent of library defaults and keeps the "zero fee" column honestly labelled --
it is zero FEE, not zero COST.

## Compute

`rsi_meanrev` at LOW oversold is the slowest cell in the codebase: `backtest()` calls
`rule.detect()` only while flat, so a rule that almost never fires pays full support-level
detection on nearly all 44k bars. The grid therefore gets CHEAPER as oversold rises. Expect the
oversold=25 block to dominate wall-clock.
"""

from __future__ import annotations

import itertools
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor
from decimal import Decimal
from pathlib import Path

# Resolved rather than hardcoded: these were absolute literals into one laptop's home and into a
# per-session temp directory that is collected when the session ends -- so this recorded run could
# not be re-run, which is the one thing a recorded run is for. `_out/` sits beside this script
# (gitignored), which is also where the intersection run writes the ANCHOR this reads.
# Override either with KEEL_EXPERIMENT_DB / KEEL_EXPERIMENT_OUT.
DB = os.environ.get("KEEL_EXPERIMENT_DB") or str(Path.home() / "keel" / "keel.db")
SCRATCH = os.environ.get("KEEL_EXPERIMENT_OUT") or str(Path(__file__).resolve().parent / "_out")
Path(SCRATCH).mkdir(parents=True, exist_ok=True)
ANCHOR = f"{SCRATCH}/intersection_257.jsonl"  # supplies the oversold=20 rows
OUT_PRIMARY = f"{SCRATCH}/rsi_scale_257.jsonl"
OUT_CONDITIONAL = f"{SCRATCH}/rsi_scale_257_proximity.jsonl"  # separate file, never merged

UNIVERSE = [
    "BTC-USD", "ETH-USD", "ADA-USD", "LINK-USD", "LTC-USD", "SOL-USD",
    "XLM-USD", "PAXG-USDT", "BCH-USD", "AAVE-USD", "DOGE-USD", "DOT-USD",
    "UNI-USD", "ZEC-USD", "ALGO-USD", "FET-USD", "CRV-USD", "ICP-USD",
    "AVAX-USD", "NEAR-USD", "XRP-USD", "PAXG-USD", "WLD-USD", "TON-USD",
]

FEES = ["0", "0.006", "0.012"]
SLIPPAGE = Decimal("0.0005")

ANCHOR_OVERSOLD = 20.0
NEW_LEVELS = [25.0, 30.0, 35.0, 40.0]
PROXIMITY_LEVELS = ["0.005", "0.02", "0.05"]

TRIGGER_MIN_ASSETS = 8
TRIGGER_AT_OVERSOLD = 40.0


def _run(job: tuple[str, float, str]) -> list[dict]:
    product, oversold, proximity = job
    from keel_core.types import Granularity

    from keel.data.db import connect
    from keel.data.repository import Repository
    from keel.strategy import backtest as bt
    from keel.strategy.rules.rsi_meanrev import RsiMeanReversion

    out: list[dict] = []
    try:
        candles = Repository(connect(DB)).get_candles(product, Granularity.ONE_HOUR)
    except Exception as exc:
        return [
            {"product": product, "oversold": oversold, "proximity": proximity, "fee": f,
             "error": f"{type(exc).__name__}: {exc}"}
            for f in FEES
        ]

    for fee in FEES:
        try:
            rule = RsiMeanReversion(
                product_id=product,
                oversold=oversold,
                support_proximity_pct=Decimal(proximity),
            )
            r = bt.backtest(rule, candles, fee_pct=Decimal(fee), slippage_pct=SLIPPAGE)
            out.append({
                "product": product, "oversold": oversold, "proximity": proximity, "fee": fee,
                "n_trades": int(r.n_trades),
                "win_rate": float(r.win_rate),
                "profit_factor": float(r.profit_factor),
                "expectancy": float(r.expectancy),
            })
        except Exception as exc:
            out.append({"product": product, "oversold": oversold, "proximity": proximity,
                        "fee": fee, "error": f"{type(exc).__name__}: {exc}"})
    return out


def _done(path: str) -> set:
    if not os.path.exists(path):
        return set()
    seen = set()
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue  # torn final line
        seen.add((r["product"], r["oversold"], r["proximity"]))
    return seen


def _execute(jobs: list, path: str, label: str) -> None:
    jobs = [j for j in jobs if (j[0], j[1], j[2]) not in _done(path)]
    print(f"[{label}] running {len(jobs)} combinations", flush=True)
    if not jobs:
        return
    t0 = time.perf_counter()
    with ProcessPoolExecutor(max_workers=8) as ex, open(path, "a") as fh:
        for i, rows in enumerate(ex.map(_run, jobs, chunksize=1), 1):
            for row in rows:
                fh.write(json.dumps(row) + "\n")
            fh.flush()
            if i % 4 == 0 or i == len(jobs):
                print(f"[{label}] {i}/{len(jobs)}  {time.perf_counter() - t0:.0f}s", flush=True)


def anchor_rows() -> list[dict]:
    """The oversold=20 rows from the intersection run, relabelled into this grid's schema."""
    rows = []
    for line in open(ANCHOR):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if r.get("rule") != "rsi" or r.get("arm") != "A" or "error" in r:
            continue
        rows.append({**r, "oversold": ANCHOR_OVERSOLD, "proximity": "0.005", "anchor": True})
    return rows


def main() -> None:
    print(f"PRIMARY ARM: oversold {NEW_LEVELS} x {len(UNIVERSE)} assets "
          f"(+ {ANCHOR_OVERSOLD} reused as anchor)", flush=True)
    jobs = [(a, o, "0.005") for o in NEW_LEVELS for a in UNIVERSE]
    _execute(jobs, OUT_PRIMARY, "primary")

    # Evaluate the PRE-DECLARED trigger. No judgement is applied here -- the condition and the
    # threshold were both fixed in the docstring above before any of this ran.
    rows = [json.loads(x) for x in open(OUT_PRIMARY) if x.strip()]
    at_max = {
        r["product"]
        for r in rows
        if "error" not in r and r["oversold"] == TRIGGER_AT_OVERSOLD
        and r["fee"] == "0" and r["n_trades"] >= 100
    }
    print(f"\nTRIGGER CHECK: {len(at_max)}/{len(UNIVERSE)} assets reach n>=100 at "
          f"oversold={TRIGGER_AT_OVERSOLD} (threshold: fewer than {TRIGGER_MIN_ASSETS} fires it)",
          flush=True)

    if len(at_max) < TRIGGER_MIN_ASSETS:
        print("TRIGGERED -> running the pre-declared conditional proximity arm, "
              "to its own file, reported as a separate curve.", flush=True)
        cond = [
            (a, o, p)
            for o, p in itertools.product(NEW_LEVELS, PROXIMITY_LEVELS)
            if p != "0.005"  # 0.005 already covered by the primary arm
            for a in UNIVERSE
        ]
        _execute(cond, OUT_CONDITIONAL, "conditional")
    else:
        print("NOT triggered -- primary arm reached the frequency floor on its own.", flush=True)

    print("\ndone", flush=True)


if __name__ == "__main__":
    main()
