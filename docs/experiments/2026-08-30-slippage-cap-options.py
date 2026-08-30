#!/usr/bin/env python
"""The three options #523 names, measured on one cohort by one method — the driver.

PRE-REGISTERED BEFORE RUNNING (this docstring is the pre-registration; the companion record
`docs/experiments/2026-08-30-slippage-cap-options.md` reports what came out).

## The question
Issue #523: `slippage_for_quote_volume` is `floor x sqrt(anchor / median_daily_quote_volume)`
clamped to [5bp, 50bp]. The cap binds at $5M/day median while the admission floor is $1M/day,
so every asset in that 5x band is simultaneously admissible and charged the identical capped
rate — the sqrt model degenerates to a flat fee across the realistic candidate universe. The
issue offers three remedies and says plainly that none is obviously right. This run measures
all three on the same cohort by the same method so they can be compared, and DECIDES NOTHING.

## Method (frozen before the run)
1. **The shipped engine, unmodified.** `keel.strategy.backtest.backtest` over the full cached
   ONE_HOUR history per product — next-bar-open market fills (#257), fee charged on both legs,
   slippage worsening both legs. Nothing in `keel/` is edited; each arm is expressed purely as
   the `slippage_by_product` resolver the engine already accepts.
2. **`turtle_breakout`, constructor defaults, every product with >= MIN_HOURLY_BARS cached
   ONE_HOUR candles.** No asset picking. The family is the one #523's own shadow run used.
3. **The 120bp taker fee on every arm** (`backtest.TAKER_FEE_PCT`) — verified against three real
   live fills on 2026-08-30 (BTC $0.592885/$50.00, BTC $0.589805/$50.00, PAXG $0.731621/$61.70;
   aggregate effective 1.1839%). A research claim that 0.012 overstates costs by 2x is FALSE
   and is not used here.
4. **Seven arms, differing ONLY in the per-leg slippage rate:**
   - `gross`      — fee 0, slippage 0. The invariance control: identical across every other arm
                    is the proof that the arms differ in cost alone and not in fills.
   - `shipped`    — `slippage_for_quote_volume(median)`, i.e. clamped to [5bp, 50bp]. Today.
   - `tail_cap`   — the same curve clamped at the CORPUS-TAIL value the current cap's own
                    docstring reasons about ("TON ... would demand ~184bp unclamped").
                    `TAIL_CAP_PCT` is that number recomputed from the cache, not copied.
   - `uncapped`   — the same curve with the thin-end clamp removed entirely (the floor stays).
   - `part_50` / `part_100` / `part_50k` — option 2, a SIZE-AWARE model (see below) at the clip
                    sizes this deployment actually uses and at the $50,000 contrast #523 names.
5. **Option 3 (raise the admission floor to $5M/day) changes no rate and is therefore not an
   arm.** Every product it would still admit is already above the cap threshold by construction,
   so its `shipped` number IS its uncapped number. It is measured as an ELIGIBILITY delta: which
   products leave, and what the remaining shortlist looks like. That asymmetry is the finding,
   not an omission.
6. **Read only rows at n >= SAMPLE_FLOOR** (`promotion.PromotionConfig.min_trades`, 100).
   Rows below it are printed and marked, never read.

## The participation model, stated as an assumption
    slip = SLIPPAGE_FLOOR_PCT + Y * sigma_daily * sqrt(clip_notional / median_daily_quote_volume)
- The second term is the standard square-root impact law (Almgren et al.), `Y = 1.0`, the middle
  of the usual 0.5-1.5 range. This is the law the CURRENT docstring invokes ("cost scales with
  the SQUARE ROOT of the inverse volume ratio") — but the law's argument is PARTICIPATION RATE
  (size / volume), and the shipped model drops the size, which is exactly #523's option-2 point.
- `sigma_daily` is close-to-close stdev of daily log returns over the product's full cached
  ONE_DAY history. Close-to-close and not Yang-Zhang deliberately: `analysis/indicators.py
  ::yang_zhang_volatility` carries a MEASURED-AND-NOT-ADOPTED warning for crypto (efficiency
  1.01x/0.81x/0.45x on BTC/ETH/PAXG, biased upward), and close-to-close is the near-unbiased one.
- The first term is a SPREAD PROXY and is the weakest part of this run. **keel stores no book
  snapshots and no realised spreads**, so the half-spread cannot be measured from anything in
  the cache. `SLIPPAGE_FLOOR_PCT` (5bp) stands in for it, which is the rate the corpus's most
  liquid product pays and is therefore certainly too small for a thin book. Every participation
  arm is consequently a LOWER BOUND on cost, i.e. it errs in the FLATTERING direction — the
  direction this project refuses. Read those three arms as "what is left once impact is priced
  honestly", not as a proposed cost model.
- `clip_notional`: $50 and $100 are this deployment's real clips — `config.live-sandbox.yaml`
  sets `max_per_order_usd: 100`, and the three filled live orders were $50.00, $50.00, $61.70.
  $50,000 is the contrast #523 itself names.

## Expectation, recorded before running
Arms 1 (`tail_cap`, `uncapped`) move net PF DOWN by the 1.6%-24% #523 measured and change no
verdict, because every verdict is already far below 1.0. `tail_cap` and `uncapped` are expected
to be IDENTICAL on this corpus, since nothing cached is thinner than TON. The participation arms
at $50/$100 are expected to move net PF UP — impact at a $50 clip in a $1M/day book is a fraction
of a basis point — which would make option 2 the only one of the three that flatters results.
If that happens it is a result to state loudly, not to bury.

## What this run does NOT decide
Nothing. The cap, the floor and the model are unchanged by this script and by its record. Live
PAXG's status under a $5M floor is the operator's call and is reported, not recommended into.

## Provenance and safety
- READ-ONLY against the candle cache: `sqlite3.connect(f"file:{db}?mode=ro", uri=True)`.
- The only writes are the `--out` JSONL artifact and stdout.
- Every Decimal is written as a string. Re-running truncates and rewrites the artifact.

    PYTHONPATH=. .venv/bin/python docs/experiments/2026-08-30-slippage-cap-options.py
    PYTHONPATH=. .venv/bin/python docs/experiments/2026-08-30-slippage-cap-options.py \
        --db ~/keel/keel.db --products CRO-USD,BTC-USD --workers 8
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
import time
from concurrent.futures import ProcessPoolExecutor
from decimal import Decimal
from typing import TypedDict

from keel.agent import build_rule_from_params
from keel.compliance.screen import ScreenPolicy, median_daily_quote_volume
from keel.strategy.backtest import (
    SLIPPAGE_CAP_PCT,
    SLIPPAGE_FLOOR_PCT,
    SLIPPAGE_REFERENCE_QUOTE_VOLUME,
    TAKER_FEE_PCT,
    backtest,
    slippage_for_quote_volume,
)
from keel.strategy.promotion import PromotionConfig
from keel.types import Candle, Granularity

DEFAULT_DB = "/Users/elmehdiaitbrahim/keel/keel.db"
DEFAULT_OUT = "docs/experiments/2026-08-30-slippage-cap-options.jsonl"
GRANULARITY = Granularity.ONE_HOUR
MIN_HOURLY_BARS = 2000
FAMILY = "turtle_breakout"

#: The sample floor rows are READ at, straight from the promotion gate rather than restated.
SAMPLE_FLOOR = PromotionConfig().min_trades
#: The admission floor in force today, straight from the screen rather than restated.
ADMISSION_FLOOR = ScreenPolicy().min_median_daily_volume
#: The floor #523's option 3 proposes.
PROPOSED_ADMISSION_FLOOR = Decimal("5000000")

#: Square-root-law coefficient. 1.0, the middle of the usual 0.5-1.5 practitioner range.
IMPACT_Y = Decimal("1.0")
#: Clip notionals the participation arms price, in USD. $50/$100 are this deployment's real
#: sizes (`max_per_order_usd: 100`; filled live orders $50.00/$50.00/$61.70); $50,000 is #523's
#: own contrast.
CLIPS: tuple[tuple[str, Decimal], ...] = (
    ("part_50", Decimal("50")),
    ("part_100", Decimal("100")),
    ("part_50k", Decimal("50000")),
)

ARMS: tuple[str, ...] = ("gross", "shipped", "tail_cap", "uncapped", *(name for name, _ in CLIPS))


class JsonlRow(TypedDict):
    arm: str
    product: str
    bars: int
    median_daily_quote_volume: str
    sigma_daily: str
    fee_pct: str
    slippage_pct: str
    unclamped_pct: str
    capped: bool
    admissible_1m: bool
    admissible_5m: bool
    n_trades: int
    wins: int
    win_rate: str
    expectancy: str
    profit_factor: str
    max_drawdown: str


def load_candles(db_path: str, product_id: str, granularity: Granularity) -> list[Candle]:
    """Ascending candles for one product at one granularity, read-only."""
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT ts, o, h, l, c, v FROM candles "
            "WHERE product_id = ? AND granularity = ? ORDER BY ts",
            (product_id, granularity.value),
        ).fetchall()
    finally:
        connection.close()
    return [
        Candle(
            ts=ts,
            open=Decimal(o),
            high=Decimal(h),
            low=Decimal(low),
            close=Decimal(c),
            volume=Decimal(v),
        )
        for ts, o, h, low, c, v in rows
    ]


def hourly_products(db_path: str, minimum_bars: int) -> list[str]:
    """Every product with enough cached ONE_HOUR history. No asset picking."""
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT product_id, COUNT(*) FROM candles WHERE granularity = ? "
            "GROUP BY product_id HAVING COUNT(*) >= ? ORDER BY product_id",
            (GRANULARITY.value, minimum_bars),
        ).fetchall()
    finally:
        connection.close()
    return [product_id for product_id, _ in rows]


def daily_sigma(candles: list[Candle]) -> Decimal:
    """Close-to-close stdev of daily log returns over the full cached daily history.

    Close-to-close and not Yang-Zhang on purpose: `analysis/indicators.yang_zhang_volatility`
    documents its own MEASURED rejection for crypto (no efficiency gain, biased upward).
    """
    returns = [
        math.log(float(later.close) / float(earlier.close))
        for earlier, later in zip(candles, candles[1:])
        if earlier.close > 0 and later.close > 0
    ]
    if len(returns) < 2:
        return Decimal(0)
    return Decimal(str(statistics.stdev(returns)))


def unclamped_rate(median: Decimal) -> Decimal:
    """The sqrt curve with NO clamp at either end -- what the model demands before the bounds."""
    if median <= 0:
        return SLIPPAGE_CAP_PCT
    return SLIPPAGE_FLOOR_PCT * (SLIPPAGE_REFERENCE_QUOTE_VOLUME / median).sqrt()


def participation_rate(median: Decimal, sigma: Decimal, clip: Decimal) -> Decimal:
    """`SLIPPAGE_FLOOR_PCT + Y * sigma_daily * sqrt(clip / median)`.

    The first term is an UNMEASURED spread proxy (keel stores no book data), so this is a
    LOWER bound on cost. See the module docstring.
    """
    if median <= 0:
        return SLIPPAGE_CAP_PCT
    return SLIPPAGE_FLOOR_PCT + IMPACT_Y * sigma * (clip / median).sqrt()


def arm_rate(arm: str, median: Decimal, sigma: Decimal) -> tuple[Decimal, Decimal]:
    """`(fee_pct, slippage_pct)` for one arm on one product."""
    if arm == "gross":
        return Decimal(0), Decimal(0)
    if arm == "shipped":
        return TAKER_FEE_PCT, slippage_for_quote_volume(median)
    unclamped = unclamped_rate(median)
    if arm == "tail_cap":
        return TAKER_FEE_PCT, min(max(unclamped, SLIPPAGE_FLOOR_PCT), tail_cap_pct())
    if arm == "uncapped":
        return TAKER_FEE_PCT, max(unclamped, SLIPPAGE_FLOOR_PCT)
    clip = dict(CLIPS)[arm]
    return TAKER_FEE_PCT, participation_rate(median, sigma, clip)


#: Filled once per process by `_set_tail_cap`; the corpus-tail rate is a MEASUREMENT of the
#: thinnest cached product, not a constant copied out of the docstring it verifies.
_TAIL_CAP: Decimal | None = None


def _set_tail_cap(value: str) -> None:
    global _TAIL_CAP
    _TAIL_CAP = Decimal(value)


def tail_cap_pct() -> Decimal:
    assert _TAIL_CAP is not None, "tail cap not measured"
    return _TAIL_CAP


def measure_liquidity(db_path: str, products: list[str]) -> dict[str, tuple[Decimal, Decimal]]:
    """`{product: (median_daily_quote_volume, sigma_daily)}` from the cached ONE_DAY bars."""
    out: dict[str, tuple[Decimal, Decimal]] = {}
    for product_id in products:
        daily = load_candles(db_path, product_id, Granularity.ONE_DAY)
        out[product_id] = (median_daily_quote_volume(daily), daily_sigma(daily))
    return out


def _one(job: tuple[str, str, str, str, str, str]) -> JsonlRow:
    """One (product, arm) cell, entirely inside a worker process.

    The db path and the measured statistics travel IN the job: macOS spawns workers rather than
    forking, so module-level state does not survive (the same precaution the 2026-08-22 driver
    documents). A fresh rule is built per cell because a `Rule` may carry per-series state.
    """
    db_path, product_id, arm, median_s, sigma_s, tail_cap_s = job
    _set_tail_cap(tail_cap_s)
    median, sigma = Decimal(median_s), Decimal(sigma_s)
    fee, slippage = arm_rate(arm, median, sigma)
    candles = load_candles(db_path, product_id, GRANULARITY)
    rule = build_rule_from_params(FAMILY, {"product_id": product_id})
    result = backtest(
        rule,
        candles,
        fee_pct=fee,
        slippage_by_product=lambda _product, rate=slippage: rate,
    )
    closed = [t for t in result.trades if t.outcome != "open"]
    return {
        "arm": arm,
        "product": product_id,
        "bars": len(candles),
        "median_daily_quote_volume": str(median),
        "sigma_daily": str(sigma),
        "fee_pct": str(fee),
        "slippage_pct": str(slippage),
        "unclamped_pct": str(unclamped_rate(median)),
        "capped": slippage_for_quote_volume(median) == SLIPPAGE_CAP_PCT,
        "admissible_1m": median >= ADMISSION_FLOOR,
        "admissible_5m": median >= PROPOSED_ADMISSION_FLOOR,
        "n_trades": result.n_trades,
        "wins": sum(1 for t in closed if t.outcome == "win"),
        "win_rate": f"{result.win_rate:.4f}",
        "expectancy": str(result.expectancy),
        "profit_factor": str(result.profit_factor),
        "max_drawdown": str(result.max_drawdown),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--products", default="")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    products = (
        [p.strip() for p in args.products.split(",") if p.strip()]
        if args.products
        else hourly_products(args.db, MIN_HOURLY_BARS)
    )
    liquidity = measure_liquidity(args.db, products)

    # The corpus tail: the thinnest cached product's UNCLAMPED demand. This is the number the
    # shipped cap's docstring reasons about ("TON ... would demand ~184bp"); measuring it here
    # rather than hard-coding it is what makes `tail_cap` a verification and not a restatement.
    thinnest, (thin_median, _) = min(liquidity.items(), key=lambda kv: kv[1][0])
    tail_cap = unclamped_rate(thin_median)
    _set_tail_cap(str(tail_cap))

    print(f"corpus tail: {thinnest} median ${thin_median:,.0f} -> {tail_cap * 10000:.1f}bp")
    print(
        f"shipped cap: {SLIPPAGE_CAP_PCT * 10000:.1f}bp   floor: {SLIPPAGE_FLOOR_PCT * 10000:.1f}bp"
    )
    print(f"admission floor: ${ADMISSION_FLOOR:,.0f}   proposed: ${PROPOSED_ADMISSION_FLOOR:,.0f}")
    print(f"sample floor: n >= {SAMPLE_FLOOR}   fee: {TAKER_FEE_PCT * 100:.1f}%")
    print(f"{len(products)} products x {len(ARMS)} arms = {len(products) * len(ARMS)} cells\n")

    jobs = [
        (args.db, p, arm, str(liquidity[p][0]), str(liquidity[p][1]), str(tail_cap))
        for p in products
        for arm in ARMS
    ]
    started = time.time()
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        rows = list(pool.map(_one, jobs))
    print(f"{len(rows)} cells in {time.time() - started:.0f}s\n")

    with open(args.out, "w") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")

    by_key = {(r["product"], r["arm"]): r for r in rows}
    header = (
        f"{'product':11} {'n':>4} {'median':>13} {'sig%':>5} "
        + " ".join(f"{a:>18}" for a in ARMS)
        + "  flags"
    )
    print(header)
    print("-" * len(header))
    for product in sorted(products, key=lambda p: liquidity[p][0]):
        shipped = by_key[(product, "shipped")]
        n = shipped["n_trades"]
        cells = []
        for arm in ARMS:
            row = by_key[(product, arm)]
            cells.append(
                f"{Decimal(row['slippage_pct']) * 10000:6.2f}bp "
                f"{Decimal(row['profit_factor']):9.4f}"
            )
        flags = []
        if shipped["capped"]:
            flags.append("CAPPED")
        if not shipped["admissible_1m"]:
            flags.append("inadmissible@1M")
        elif not shipped["admissible_5m"]:
            flags.append("DROPPED@5M")
        if n < SAMPLE_FLOOR:
            flags.append(f"n<{SAMPLE_FLOOR}")
        print(
            f"{product:11} {n:4d} {liquidity[product][0]:13,.0f} "
            f"{liquidity[product][1] * 100:5.2f} " + " ".join(cells) + "  " + " ".join(flags)
        )

    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
