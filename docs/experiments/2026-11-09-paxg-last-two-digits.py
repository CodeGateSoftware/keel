#!/usr/bin/env python
"""Do the last two digits of PAXG's price at a 4-hour window's open predict that window's direction?

**Pre-registered.** This docstring and the code below were committed and pushed before the
first run. Nothing here was chosen after seeing a result.

## The claim under test
An outside observer says the last two digits of PAXG's price on the "starting candle" predict
whether PAXG rises or falls over the next four hours. The claim came without a rule: which
digits map to "up" was not stated. So this tests the claim in its weakest form, the form any
specific rule would need to be true: **the two digits carry information about the sign of the
next 4-hour move.** If no mapping from digits to direction works out of sample, no particular
rule does either.

## Data (fixed before the run)
- **Primary, Coinbase `PAXG-USD`** (the venue keel trades), `ONE_HOUR` candles from
  `~/keel/keel.db`, every bar up to `END_TS` (2026-11-09 00:00 UTC). About 18 months.
- **Replication, Binance `PAXG-USDT`**, `ONE_HOUR`, the same cache, up to its last bar
  (2026-07-18). About five years. Binance quoted PAXG in whole dollars for much of 2022-2023, so
  the cents are 00 for that stretch. That arm therefore uses the dollar digits only.

## Windows
Non-overlapping 4-hour windows aligned to UTC (00, 04, 08, 12, 16, 20). A window counts only
if all four hourly bars are present. Start price = the open of the first hour; end price = the
close of the fourth. Outcome = up if end > start, down if end < start. Flat windows are dropped.

## Two meanings of "last two digits", three arms
- `usd_dollars`: Coinbase, `floor(start) mod 100` (4673.49 -> 73).
- `usd_cents`: Coinbase, the cents (4673.49 -> 49).
- `usdt_dollars`: Binance, `floor(start) mod 100`. This is the replication arm.

## Tests, per arm
1. **In-sample dependence.** The chi-square statistic of the 100 x 2 table (digit x up/down),
   with its p-value taken from 2,000 permutations of the outcomes (seed 1109), not from the
   asymptotic distribution, because 100 cells leave some of them thin.
2. **Out of sample (the decisive test).** Split chronologically: the first 60% of windows
   train, the last 40% test. On train, each digit is mapped to its majority direction (a tied
   or unseen digit takes the train set's overall majority). Score the hit rate on test. The
   null: shuffle the train outcomes among train windows, refit, rescore, 2,000 times (seed
   1109). That keeps the base rate, so a trending market cannot pass for skill.
   p = (1 + #null >= observed) / 2001.
3. **Economics, reported and not tested.** keel's spot account can't short, so the only
   tradeable call is "up". On the test set, the gross and net 4-hour return of buying at the
   start price whenever the map says "up". Net is after a 0.9% taker fee on each leg
   (`keel-advanced-trade-is-not-fee-free`). Also reported: the median absolute 4-hour move on the
   full sample, set against the 1.8% round trip.

## Decision rule (fixed)
Three arms means a Bonferroni alpha of 0.05 / 3 = 0.0167.
- An arm **shows dependence** only if both permutation p-values are below 0.0167.
- The claim is **supported** only if a Coinbase arm shows dependence, and, for a dollar-digit
  effect, `usdt_dollars` shows it too. Otherwise it is **not supported**.
- Even a supported claim is **tradeable** only if the test-set mean net return per "up" call
  is above zero.

## Ledger
Each arm is appended to the trials ledger (`--ledger`) as `kind="ablation"`,
`provenance="a_priori"`, `decision="diagnostic_only"`. `per_trade_pnl` holds the arm's
test-set net returns per "up" call, as fractions of notional, not dollars.

## Safety
Read-only. The cache is opened `mode=ro`. The only writes are `--out`, `--ledger` and stdout.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import statistics
from decimal import Decimal
from pathlib import Path
from typing import Any

from keel.research.ledger import append_trial

SESSION = "paxg-last-two-digits-2026-11-09"
END_TS = 1794182400  # 2026-11-09 00:00 UTC
WINDOW_S = 4 * 3600
HOUR_S = 3600
TRAIN_FRACTION = Decimal("0.6")
N_PERM = 2000
SEED = 1109
ALPHA = Decimal("0.05") / 3
TAKER = Decimal("0.009")

ARMS = (
    ("usd_dollars", "PAXG-USD", "dollars"),
    ("usd_cents", "PAXG-USD", "cents"),
    ("usdt_dollars", "PAXG-USDT", "dollars"),
)


def _hourly(db: str, product: str) -> dict[int, tuple[Decimal, Decimal]]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT ts, o, c FROM candles WHERE product_id = ? AND granularity = 'ONE_HOUR' "
        "AND ts < ? ORDER BY ts",
        (product, END_TS),
    ).fetchall()
    conn.close()
    return {int(ts): (Decimal(o), Decimal(c)) for ts, o, c in rows}


def windows(hourly: dict[int, tuple[Decimal, Decimal]]) -> list[tuple[int, Decimal, Decimal]]:
    """`(start_ts, start_price, end_price)` per complete, non-flat, UTC-aligned 4-hour window."""
    out = []
    for ts in sorted(hourly):
        if ts % WINDOW_S:
            continue
        hours = [ts + k * HOUR_S for k in range(4)]
        if not all(h in hourly for h in hours):
            continue
        start, end = hourly[hours[0]][0], hourly[hours[-1]][1]
        if end != start:
            out.append((ts, start, end))
    return out


def digit(price: Decimal, kind: str) -> int:
    if kind == "dollars":
        return int(price) % 100
    return int(price * 100) % 100


def chi_square(digits: list[int], ups: list[bool]) -> float:
    n = len(ups)
    p_up = sum(ups) / n
    count = [0] * 100
    up = [0] * 100
    for d, u in zip(digits, ups, strict=True):
        count[d] += 1
        up[d] += u
    stat = 0.0
    for d in range(100):
        if count[d] == 0:
            continue
        e_up = count[d] * p_up
        e_dn = count[d] - e_up
        stat += (up[d] - e_up) ** 2 / e_up + ((count[d] - up[d]) - e_dn) ** 2 / e_dn
    return stat


def fit_map(digits: list[int], ups: list[bool]) -> list[bool]:
    overall = sum(ups) * 2 > len(ups)
    count = [0] * 100
    up = [0] * 100
    for d, u in zip(digits, ups, strict=True):
        count[d] += 1
        up[d] += u
    return [overall if up[d] * 2 == count[d] else up[d] * 2 > count[d] for d in range(100)]


def hit_rate(mapping: list[bool], digits: list[int], ups: list[bool]) -> float:
    return sum(mapping[d] == u for d, u in zip(digits, ups, strict=True)) / len(ups)


def perm_p(observed: float, null: list[float]) -> Decimal:
    return Decimal(1 + sum(x >= observed for x in null)) / Decimal(len(null) + 1)


def run_arm(arm: str, wins: list[tuple[int, Decimal, Decimal]], kind: str) -> dict[str, Any]:
    rng = random.Random(SEED)
    digits = [digit(s, kind) for _, s, _ in wins]
    ups = [e > s for _, s, e in wins]
    rets = [(e - s) / s for _, s, e in wins]
    n = len(wins)

    chi = chi_square(digits, ups)
    shuffled = ups[:]
    chi_null = []
    for _ in range(N_PERM):
        rng.shuffle(shuffled)
        chi_null.append(chi_square(digits, shuffled))
    p_chi = perm_p(chi, chi_null)

    cut = int(n * TRAIN_FRACTION)
    tr_d, tr_u, te_d, te_u = digits[:cut], ups[:cut], digits[cut:], ups[cut:]
    mapping = fit_map(tr_d, tr_u)
    hit = hit_rate(mapping, te_d, te_u)
    tr_shuf = tr_u[:]
    hit_null = []
    for _ in range(N_PERM):
        rng.shuffle(tr_shuf)
        hit_null.append(hit_rate(fit_map(tr_d, tr_shuf), te_d, te_u))
    p_oos = perm_p(hit, hit_null)

    long_gross = [r for d, r in zip(te_d, rets[cut:], strict=True) if mapping[d]]
    long_net = [(1 + r) * (1 - TAKER) * (1 - TAKER) - 1 for r in long_gross]
    shows = p_chi < ALPHA and p_oos < ALPHA
    q = Decimal("0.000001")
    return {
        "arm": arm,
        "n_windows": n,
        "first_ts": wins[0][0],
        "last_ts": wins[-1][0],
        "up_rate": Decimal(sum(ups)) / n,
        "chi_square": Decimal(str(chi)).quantize(q),
        "chi_null_median": Decimal(str(statistics.median(chi_null))).quantize(q),
        "p_chi": p_chi.quantize(q),
        "n_train": cut,
        "n_test": n - cut,
        "test_up_rate": Decimal(sum(te_u)) / len(te_u),
        "oos_hit_rate": Decimal(str(hit)).quantize(q),
        "oos_null_median": Decimal(str(statistics.median(hit_null))).quantize(q),
        "oos_null_p95": Decimal(str(sorted(hit_null)[int(0.95 * N_PERM)])).quantize(q),
        "p_oos": p_oos.quantize(q),
        "n_up_calls": len(long_gross),
        "mean_gross_up_call": (sum(long_gross) / len(long_gross)).quantize(q)
        if long_gross
        else None,
        "mean_net_up_call": (sum(long_net) / len(long_net)).quantize(q) if long_net else None,
        "median_abs_4h_move": Decimal(statistics.median(abs(r) for r in rets)).quantize(q),
        "shows_dependence": shows,
        "_series": [r.quantize(q) for r in long_net],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", required=True, help="candle cache (opened read-only)")
    parser.add_argument("--out", required=True)
    parser.add_argument("--ledger", help="trials ledger to append to (omit to skip)")
    args = parser.parse_args()

    results = {}
    cache: dict[str, list[tuple[int, Decimal, Decimal]]] = {}
    for arm, product, kind in ARMS:
        if product not in cache:
            cache[product] = windows(_hourly(args.db, product))
        results[arm] = run_arm(arm, cache[product], kind)

    usd_d = results["usd_dollars"]["shows_dependence"]
    usd_c = results["usd_cents"]["shows_dependence"]
    usdt_d = results["usdt_dollars"]["shows_dependence"]
    supported = (usd_d and usdt_d) or usd_c
    verdict = "supported" if supported else "not supported"

    with Path(args.out).open("w", encoding="utf-8") as fh:
        for arm, row in results.items():
            public = {k: v for k, v in row.items() if k != "_series"}
            fh.write(json.dumps({"row": "arm", **public}, default=str) + "\n")
            print(json.dumps(public, default=str, indent=1))
            if args.ledger:
                append_trial(
                    args.ledger,
                    trial_id=f"{SESSION}-{arm}",
                    session=SESSION,
                    rule="paxg_last_two_digits",
                    params={
                        "arm": arm,
                        "end_ts": END_TS,
                        "train_fraction": str(TRAIN_FRACTION),
                        "n_perm": N_PERM,
                        "seed": SEED,
                        "taker_pct": str(TAKER),
                        "units": "per_trade_pnl is net return per 'up' call as a fraction, not USD",
                        "verdict": verdict,
                    },
                    provenance="a_priori",
                    kind="ablation",
                    decision="diagnostic_only",
                    per_trade_pnl=row["_series"],
                    series_missing=not row["_series"],
                    summary={
                        k: v for k, v in public.items() if k not in ("arm",) and v is not None
                    },
                )
        fh.write(json.dumps({"row": "verdict", "verdict": verdict}) + "\n")
    print(f"verdict: {verdict}")


if __name__ == "__main__":
    main()
