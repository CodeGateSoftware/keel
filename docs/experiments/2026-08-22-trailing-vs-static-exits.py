#!/usr/bin/env python
"""Trailing/BE-roll exits vs the static stop, per family (#442) — the driver.

PRE-REGISTERED BEFORE RUNNING (this docstring is the pre-registration; the companion record
`docs/experiments/2026-08-22-trailing-vs-static-exits.md` reports what came out).

## The question
Issue #442, verbatim in substance: *backtest the difference before and after* wiring the
ratchet-only exit policy (an ATR-multiple trailing stop, a break-even roll) — does moving
the stop help `pullback_continuation` or `rsi_meanrev` at the fee actually paid, or is the
honest answer "no improvement / worse"?

## Method (frozen before the run)
1. **Same engine as the significance reconstruction.** Each arm runs through
   `keel.strategy.backtest.backtest` over the full cached ONE_HOUR history per product —
   next-bar-open market fills, fee charged both legs, 5 bp per-leg slippage
   (`SLIPPAGE_FLOOR_PCT`, the flat conservative floor; per-product liquidity scaling is
   #259's axis and deliberately not mixed in). The wiring under test is the #442
   `strategy.exit_policy` management the backtester now applies per bar.
2. **The 120 bp taker fee, per leg, both arms alike** (`backtest.TAKER_FEE_PCT`) — the rate
   the #475 significance run used OUTSIDE the fee-free allowance, because the question is
   whether exit management changes the family's real-cost economics, not its best case.
3. **Four arms, never blended within an arm:**
   - `static` — no knobs (every pre-#442 rule row; also the shipped default);
   - `trail_1_5` — `trail_atr_mult=1.5` (the live `executor.trail_stop_atr` primitive's own
     default multiplier, and `rsi_meanrev`'s own `atr_mult`);
   - `be_1` — `be_roll_rr=1` (roll the stop to entry at +1R);
   - `trail_1_5_be_1` — both.
4. **Families: `pullback_continuation` and `rsi_meanrev`, constructor defaults for
   everything except the exit knobs.** `turtle_breakout` is OUT OF SCOPE BY DESIGN: its real
   exit is the Donchian channel and the family deliberately carries no exit-policy knobs
   (#442 hypothesis 3) — there is no trailing arm to measure.
5. **Every product with >= `MIN_HOURLY_BARS` cached ONE_HOUR candles** (2,000 bars ≈ 3
   months; both families' warm-up needs are far below that). No asset picking.
6. **A pooled row per (family, arm)** over all products' trades — the family-level answer,
   at the largest honest n, reported beside the per-product rows.

## Expectation, recorded before running
No improvement, most likely worse, for both families: `rsi_meanrev`'s edge (where it exists
at all) is selectivity with quick full moves to a fixed-R target — a trail cuts exactly
those winners; `pullback_continuation`'s measured_1to1 target caps winners by design, so a
1.5-ATR trail mostly converts target hits into earlier, smaller exits while the BE-roll
converts some full losers into scratches. At 120 bp/leg, exit prices that move EARLIER do
not reduce friction (the round trip pays it either way). If an arm comes back better, that
is a result to inspect — one pre-registered grid point per arm is evidence to justify a
DEFAULT, not a tuning claim (the Optuna study #476 owns tuning).

## What this run does NOT decide
The wiring itself is not gated on these numbers — the capability is the point of #442 and
the constructor defaults stay OFF unless this run says otherwise; live stop management is
issue #502 regardless.

## Provenance and safety
- READ-ONLY against the candle cache: `sqlite3.connect(f"file:{db}?mode=ro", uri=True)`.
  The deployment db is never written. The only writes are the `--out` JSONL artifact and
  stdout.
- One JSONL row per (family, product, arm) plus one pooled row per (family, arm), every
  Decimal as a string. Re-running truncates and rewrites the artifact.
- `--workers` processes (default 8): `rsi_meanrev` is the slow cell (~minutes per product
  on this machine); the pool is the same shape the 2026-08-21 significance run used.

    .venv/bin/python docs/experiments/2026-08-22-trailing-vs-static-exits.py
    .venv/bin/python docs/experiments/2026-08-22-trailing-vs-static-exits.py \
        --db ~/keel/keel.db --products BTC-USD,ETH-USD --workers 8
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from decimal import Decimal
from typing import TypedDict

from keel.agent import build_rule_from_params
from keel.strategy.backtest import SLIPPAGE_FLOOR_PCT, TAKER_FEE_PCT, backtest
from keel.types import Candle, Granularity

DEFAULT_DB = "/Users/elmehdiaitbrahim/keel/keel.db"
DEFAULT_OUT = "docs/experiments/2026-08-22-trailing-vs-static-exits.jsonl"
GRANULARITY = Granularity.ONE_HOUR
MIN_HOURLY_BARS = 2000

#: The pre-registered trailing multiplier — the live primitive's own default
#: (`executor.trail_stop_atr(multiplier=Decimal("1.5"))`).
TRAIL_ATR_MULT = "1.5"
#: The pre-registered break-even threshold, in multiples of the ORIGINAL per-unit risk.
BE_ROLL_RR = "1"

#: (arm label, exit-policy params layered over the family defaults). `static` carries no
#: knobs — the shipped default and the pre-#442 behavior.
ARMS: tuple[tuple[str, dict[str, str]], ...] = (
    ("static", {}),
    ("trail_1_5", {"trail_atr_mult": TRAIL_ATR_MULT}),
    ("be_1", {"be_roll_rr": BE_ROLL_RR}),
    ("trail_1_5_be_1", {"trail_atr_mult": TRAIL_ATR_MULT, "be_roll_rr": BE_ROLL_RR}),
)

FAMILIES: tuple[str, ...] = ("pullback_continuation", "rsi_meanrev")


class JsonlRow(TypedDict):
    """One JSONL row in its plain shapes: every Decimal already stringified."""

    family: str
    arm: str
    product: str
    fee_pct: str
    slippage_pct: str
    bars: int
    n_trades: int
    wins: int
    open_trades: int
    win_rate: str
    expectancy: str
    profit_factor: str
    max_drawdown: str
    avg_r: str
    # carried for the pooled pass in the parent; stripped before the row is written
    trades_for_pool: list[tuple[str, str, str]]


@dataclass(frozen=True)
class _ArmOutcome:
    """The per-cell aggregates the driver reports — nothing here is derived anywhere but
    `strategy.stats.summarize`, so the numbers cannot disagree with the engine's own."""

    n_trades: int
    wins: int
    open_trades: int
    win_rate: str
    expectancy: str
    profit_factor: str
    max_drawdown: str
    avg_r: str
    trades_for_pool: list[tuple[str, str, str]]


def load_candles(db_path: str, product_id: str) -> list[Candle]:
    """Ascending ONE_HOUR candles for one product, read-only."""
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT ts, o, h, l, c, v FROM candles "
            "WHERE product_id = ? AND granularity = ? ORDER BY ts",
            (product_id, GRANULARITY.value),
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


def hourly_products(db_path: str, minimum_bars: int) -> list[tuple[str, int]]:
    """`(product_id, bar_count)` for every product with enough cached ONE_HOUR history."""
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT product_id, COUNT(*) FROM candles WHERE granularity = ? "
            "GROUP BY product_id HAVING COUNT(*) >= ? ORDER BY product_id",
            (GRANULARITY.value, minimum_bars),
        ).fetchall()
    finally:
        connection.close()
    return [(product_id, count) for product_id, count in rows]


def _run_arm(
    candles: list[Candle],
    family: str,
    product_id: str,
    arm_params: dict[str, str],
) -> _ArmOutcome:
    """One (family, product, arm) cell: build the rule, backtest, aggregate."""
    rule = build_rule_from_params(family, {"product_id": product_id, **arm_params})
    result = backtest(rule, candles, fee_pct=TAKER_FEE_PCT, slippage_pct=SLIPPAGE_FLOOR_PCT)
    closed = [t for t in result.trades if t.outcome != "open"]
    r_multiples = [t.r_multiple for t in closed if t.r_multiple is not None]
    avg_r = sum(r_multiples, Decimal("0")) / Decimal(len(r_multiples)) if r_multiples else None
    return _ArmOutcome(
        n_trades=result.n_trades,
        wins=sum(1 for t in closed if t.outcome == "win"),
        open_trades=sum(1 for t in result.trades if t.outcome == "open"),
        win_rate=f"{result.win_rate:.4f}",
        expectancy=str(result.expectancy),
        profit_factor=str(result.profit_factor),
        max_drawdown=str(result.max_drawdown),
        avg_r=str(avg_r) if avg_r is not None else "n/a",
        trades_for_pool=[
            (t.outcome, str(t.pnl), str(t.r_multiple) if t.r_multiple is not None else "n/a")
            for t in closed
        ],
    )


def _one(job: tuple[str, str, str, str]) -> JsonlRow:
    """One (db, family, product, arm) cell, running entirely inside a worker process.

    The db path travels IN the job because macOS worker processes are spawned, not forked —
    the same precaution the 2026-08-21 significance driver documented. A fresh rule is
    built per cell because a `Rule` instance may carry per-series state between `detect`
    calls; sharing one across arms would make the second arm's fills depend on the first's
    history.
    """
    db_path, family, product_id, arm_label = job
    arm_params = dict(ARMS)[arm_label]
    candles = load_candles(db_path, product_id)
    outcome = _run_arm(candles, family, product_id, arm_params)
    return {
        "family": family,
        "arm": arm_label,
        "product": product_id,
        "fee_pct": str(TAKER_FEE_PCT),
        "slippage_pct": str(SLIPPAGE_FLOOR_PCT),
        "bars": len(candles),
        "n_trades": outcome.n_trades,
        "wins": outcome.wins,
        "open_trades": outcome.open_trades,
        "win_rate": outcome.win_rate,
        "expectancy": outcome.expectancy,
        "profit_factor": outcome.profit_factor,
        "max_drawdown": outcome.max_drawdown,
        "avg_r": outcome.avg_r,
        "trades_for_pool": outcome.trades_for_pool,
    }


def _pool_row(family: str, arm_label: str, rows: list[JsonlRow]) -> JsonlRow:
    """The pooled (family, arm) row: every product's closed trades together."""
    outcomes = [t for row in rows for t in row["trades_for_pool"]]
    n = len(outcomes)
    wins = sum(1 for o, _, _ in outcomes if o == "win")
    pnls = [Decimal(p) for _, p, _ in outcomes if p != "n/a"]
    gross_win = sum((p for p in pnls if p > 0), Decimal("0"))
    gross_loss = sum((p for p in pnls if p < 0), Decimal("0"))
    r_multiples = [Decimal(r) for _, _, r in outcomes if r != "n/a"]
    win_rate = Decimal(wins) / Decimal(n) if n else None
    expectancy = sum(pnls, Decimal("0")) / Decimal(n) if n else None
    profit_factor = (
        gross_win / -gross_loss if gross_loss < 0 else (None if gross_win == 0 else Decimal("Inf"))
    )
    avg_r = sum(r_multiples, Decimal("0")) / Decimal(len(r_multiples)) if r_multiples else None
    return {
        "family": family,
        "arm": arm_label,
        "product": "POOLED",
        "fee_pct": str(TAKER_FEE_PCT),
        "slippage_pct": str(SLIPPAGE_FLOOR_PCT),
        "bars": sum(row["bars"] for row in rows),
        "n_trades": n,
        "wins": wins,
        "open_trades": sum(row["open_trades"] for row in rows),
        "win_rate": f"{win_rate:.4f}" if win_rate is not None else "n/a",
        "expectancy": str(expectancy) if expectancy is not None else "n/a",
        "profit_factor": str(profit_factor) if profit_factor is not None else "n/a",
        "max_drawdown": "n/a",  # a pooled drawdown needs an equity path, not per-cell scalars
        "avg_r": str(avg_r) if avg_r is not None else "n/a",
        "trades_for_pool": [],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Trailing vs static exits (#442), read-only")
    parser.add_argument("--db", default=DEFAULT_DB, help=f"Candle cache (default: {DEFAULT_DB})")
    parser.add_argument(
        "--out", default=DEFAULT_OUT, help=f"JSONL artifact (default: {DEFAULT_OUT})"
    )
    parser.add_argument(
        "--products",
        default=None,
        help="Comma-separated product ids to restrict to (default: every product with "
        f">= {MIN_HOURLY_BARS} ONE_HOUR bars)",
    )
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    wanted = [p.strip() for p in args.products.split(",")] if args.products else None
    products = hourly_products(args.db, MIN_HOURLY_BARS)
    if wanted is not None:
        products = [(p, n) for p, n in products if p in wanted]
    print(
        f"#442 trailing vs static exits  db={args.db}  fee={TAKER_FEE_PCT}/leg  "
        f"slippage={SLIPPAGE_FLOOR_PCT}/leg"
    )
    print(f"arms={[label for label, _ in ARMS]}  families={list(FAMILIES)}")
    print(f"products with >= {MIN_HOURLY_BARS} ONE_HOUR bars: {len(products)}")
    for product_id, bars in products:
        print(f"  {product_id:12} {bars} bars")

    jobs = [
        (args.db, family, product_id, arm_label)
        for family in FAMILIES
        for product_id, _ in products
        for arm_label, _ in ARMS
    ]
    rows: list[JsonlRow] = []
    t0 = time.perf_counter()
    with ProcessPoolExecutor(max_workers=args.workers) as ex, open(args.out, "w") as fh:
        for i, row in enumerate(ex.map(_one, jobs, chunksize=1), 1):
            rows.append(row)
            fh.write(json.dumps({k: v for k, v in row.items() if k != "trades_for_pool"}) + "\n")
            fh.flush()
            if i % 20 == 0 or i == len(jobs):
                print(f"  {i}/{len(jobs)} cells  {time.perf_counter() - t0:.0f}s", flush=True)

        pooled: list[JsonlRow] = []
        for family in FAMILIES:
            for arm_label, _ in ARMS:
                # Filter by arm TOO: `rows` carries each product once per arm, so a
                # family-only filter would count every product's trades 4x.
                family_arm_rows = [
                    row for row in rows if row["family"] == family and row["arm"] == arm_label
                ]
                pooled_row = _pool_row(family, arm_label, family_arm_rows)
                pooled.append(pooled_row)
                fh.write(json.dumps(pooled_row) + "\n")
                fh.flush()

    rows.sort(key=lambda r: (r["family"], r["arm"], r["product"]))

    def _fmt(label: str, value: str) -> str:
        return f"  {label:<16} {value}"

    print("\n" + "=" * 78)
    print("POOLED PER (FAMILY, ARM) — the pre-registered question, at the largest honest n")
    print("=" * 78)
    for row in pooled:
        print()
        print(f"{row['family']} / {row['arm']}  (n={row['n_trades']}, open={row['open_trades']})")
        for line in (
            _fmt("win_rate", row["win_rate"]),
            _fmt("expectancy", row["expectancy"]),
            _fmt("profit_factor", row["profit_factor"]),
            _fmt("avg_r", row["avg_r"]),
        ):
            print(line)

    print(
        f"\nartifact: {args.out}  ({len(rows)} cells + {len(pooled)} pooled rows, "
        f"{time.perf_counter() - t0:.0f}s)"
    )
    print("\nReminder: this is report-only evidence. An arm that comes back worse is the")
    print("honest result, and the constructor defaults stay OFF unless this run says otherwise.")


if __name__ == "__main__":
    main()
