#!/usr/bin/env python
"""Optuna parameter study, research-side only (#476) — the driver.

PRE-REGISTERED BEFORE RUNNING (this docstring is the pre-registration; the companion record
`docs/experiments/2026-08-22-optuna-parameter-study.md` reports what came out).

## The question
Issue #476, verbatim in substance: *could parameter tuning find candidates for the gauntlet —
never auto-tuned lives?* Not "can we find better parameters" as a rescue: the sibling
significance study (#475) found every shipped family not distinguishable from zero at the
120 bp taker fee outside the allowance and ≈break-even inside it, and the binding constraint
is the FEE, not the parameters. The question this driver answers is whether an optimizer
let loose on small, defensible search spaces finds anything that survives its own
overfitting gate — and the harness must be able to report "no candidate may be proposed".

## Method (frozen before the run)
1. **Window.** The last `WINDOW` = 17,520 cached ONE_HOUR bars per product (2 years) —
   measured before freezing: rsi_meanrev backtests cost ~56 s over 2y hourly (~14 s over
   1y; quadratic in bars), so 60 trials × the 70% train window lands the rsi cells at
   ~28 min wall each, the budget's binding cell, while turtle/pullback finish in seconds.
   Products: the liquid majors BTC-USD, ETH-USD, SOL-USD (no asset picking beyond
   liquidity, stated here). A product with fewer cached bars fails the run loudly.
2. **Studies.** `keel.research.tuning.run_study` per (family, product): TPE with
   `TPESampler(seed=476)`, 60 trials, `n_startup_trials` at its default, deterministic
   under the seed (pinned by test). The optimizer sees ONLY the chronological train split
   (`train_frac` 0.7); the held-out future is touched exactly once, by the winner, after
   `optimize` returns. Objective = train-window per-trade expectancy at the fee actually
   paid: `backtest.TAKER_FEE_PCT` (120 bp per leg) and `SLIPPAGE_FLOOR_PCT` (5 bp per leg),
   the same cost model the #475 reconstruction used.
3. **Search spaces** are the harness's pinned `SEARCH_SPACES` — 4-5 knobs per family a
   trader can name a reason for (turtle: entry/exit lookbacks, ADX gate, ATR stop multiple,
   nominal R:R; rsi: oversold/overbought, ATR stop multiple, fixed R:R, RSI length;
   pullback: the three EMA periods, entry buffer). `granularity`/`product_id` are fixed by
   this driver (ONE_HOUR for all three, matching the #475 reconstruction), never searched.
4. **The gate before any proposal.** `evaluate_gate`: held-out expectancy > 0 AND
   CSCV/PBO over the study's own per-trial per-trade P&L columns <= 0.5 (s=8; trials with
   < 10 closed trades are excluded rather than padded). The driver may print a
   "PROPOSE as candidate" line ONLY for a gate-passing cell; everything else prints
   "no candidate may be proposed" with the numbers.
5. **Ledger.** One `diagnostic_only` row per family WINNER (best train expectancy across
   the three products) via `keel.research.ledger.append_trial`, `provenance="fitted"` —
   it IS fitted; that is what the ledger is for. Diagnostics count toward M, never
   toward N (spec §4.4). Nothing is written to any rule row, live or paper.

## Expectation, recorded before running
Given #475's shape, the expectation is refusals: the optimizer will find positive TRAIN
cells (it is maximizing train expectancy — that is what optimizers do), the held-out
re-pricing at 120 bp will land at or below zero for most or all cells, PBO will sit near or
above one half, and the honest headline will be "no candidate may be proposed". A cell that
PASSES is a result to inspect, not celebrate: 3 families × 3 products is 9 chances, and the
multiple-comparison budget is stated, not hidden — and even a pass is only ever a PROPOSAL
for the unchanged promotion gauntlet.

## Provenance and safety
- READ-ONLY against the candle cache: `sqlite3.connect(f"file:{db}?mode=ro", uri=True)`.
  The deployment db is never written. The only writes are the `--out` JSONL artifact, the
  `--ledger` trials ledger (append-only, the module the spec built for fitted trials), and
  stdout.
- One JSONL row per (family, product) cell carrying every trial's params and train
  expectancy, the winner, and the gate outcome; every Decimal as a string. Re-running
  truncates and rewrites the artifact (the ledger, being append-only, is NOT truncated —
  re-running would append a second, still-valid row).
- `--workers` processes (default 12): each cell is one job; the 9 cells run concurrently
  and the rsi cells are the wall-clock budget.

    .venv/bin/python docs/experiments/2026-08-22-optuna-parameter-study.py
    .venv/bin/python docs/experiments/2026-08-22-optuna-parameter-study.py \
        --db ~/keel/keel.db --products BTC-USD --workers 3
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from concurrent.futures import ProcessPoolExecutor
from decimal import Decimal
from pathlib import Path
from typing import TypedDict

from keel.research.ledger import append_trial
from keel.research.tuning import OverfittingGate, proposal_verdict, run_study
from keel.strategy.backtest import SLIPPAGE_FLOOR_PCT, TAKER_FEE_PCT
from keel.types import Candle, Granularity

DEFAULT_DB = "/Users/elmehdiaitbrahim/keel/keel.db"
DEFAULT_OUT = "docs/experiments/2026-08-22-optuna-parameter-study.jsonl"
DEFAULT_LEDGER = "docs/experiments/trials-ledger.jsonl"
GRANULARITY = Granularity.ONE_HOUR
#: The liquid majors (issue text): no asset picking beyond liquidity, stated up front.
PRODUCTS: tuple[str, ...] = ("BTC-USD", "ETH-USD", "SOL-USD")
#: 2 years of hourly bars — the window the runtime budget measured above allows (§1).
WINDOW_BARS = 17_520
N_TRIALS = 60
SEED = 476
SESSION = "optuna-parameter-study-2026-08-22"

#: Caller-FIXED params per family (merged ahead of every searched kwarg): the clock is
#: ONE_HOUR for all three families, matching the #475 reconstruction. rsi_meanrev and
#: pullback_continuation are ONE_HOUR-native and need nothing else pinned.
FIXED_PARAMS: dict[str, dict[str, object]] = {
    "turtle_breakout": {"granularity": GRANULARITY.value},
    "rsi_meanrev": {},
    "pullback_continuation": {},
}


class TrialRow(TypedDict):
    """One completed trial as the artifact carries it (JSON-plain)."""

    number: int
    params: dict[str, object]
    train_expectancy: str
    n_trades: int


class CellRow(TypedDict):
    """One (family, product) study in its plain shapes: every Decimal already stringified."""

    family: str
    product: str
    bars: int
    fee_pct: str
    slippage_pct: str
    n_trials: int
    seed: int
    best_params: dict[str, object]
    best_train_expectancy: str
    held_out_n_trades: int
    held_out_expectancy: str
    pbo: str | None
    n_columns_used: int
    n_columns_skipped: int
    gate_passed: bool
    failures: list[str]
    verdict: str
    held_out_per_trade_pnl: list[str]
    trials: list[TrialRow]


def load_candles(db_path: str, product_id: str) -> list[Candle]:
    """Ascending ONE_HOUR candles for one product, read-only (the #475 loader's shape)."""
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


def _one(job: tuple[str, str, str, int, int]) -> CellRow:
    """One (db, family, product, window, n_trials) cell: run_study, gate, JSONL row.

    Runs entirely inside a worker process. The db path, window and trial count travel IN the
    job because macOS worker processes are spawned, not forked — a module global set by
    `main()` would not arrive. One study per cell; the study's own trials are sequential
    (TPE needs each trial's result to suggest the next), so the pool's parallelism is ACROSS
    cells.
    """
    db_path, family, product_id, window, n_trials = job
    candles = load_candles(db_path, product_id)[-window:]
    report = run_study(
        family,
        product_id,
        candles,
        n_trials=n_trials,
        seed=SEED,
        fee_pct=TAKER_FEE_PCT,
        slippage_pct=SLIPPAGE_FLOOR_PCT,
        fixed_params=FIXED_PARAMS[family],
    )
    return {
        "family": report.family,
        "product": report.product_id,
        "bars": len(candles),
        "fee_pct": str(report.fee_pct),
        "slippage_pct": str(report.slippage_pct),
        "n_trials": len(report.trials),
        "seed": report.seed,
        "best_params": report.best_params,
        "best_train_expectancy": str(report.best_train_expectancy),
        "held_out_n_trades": report.held_out_result.n_trades,
        "held_out_expectancy": str(report.held_out_result.expectancy),
        "pbo": None if report.gate.pbo is None else str(report.gate.pbo),
        "n_columns_used": report.gate.n_columns_used,
        "n_columns_skipped": report.gate.n_columns_skipped,
        "gate_passed": report.gate.passed,
        "failures": list(report.gate.failures),
        "verdict": "PROPOSE" if report.gate.passed else "REFUSED",
        # the ledger row's P&L series: the winner's HELD-OUT closed trades -- the
        # out-of-sample record of the fitted configuration, not the in-sample one it was
        # fitted on
        "held_out_per_trade_pnl": [
            str(t.pnl) for t in report.held_out_result.trades if t.outcome != "open"
        ],
        "trials": [
            {
                "number": t.number,
                "params": t.params,
                "train_expectancy": str(t.train_expectancy),
                "n_trades": t.n_trades,
            }
            for t in report.trials
        ],
    }


def _render_cell(row: CellRow) -> list[str]:
    """Report lines for one cell: the winner, the degradation, the gate, the verdict."""
    return [
        f"{row['family']} @ {row['product']} "
        f"({row['bars']} bars, {row['n_trials']} trials, seed {row['seed']}, "
        f"fee {row['fee_pct']}/leg, slippage {row['slippage_pct']}/leg):",
        f"  best params {row['best_params']}",
        f"  train expectancy {row['best_train_expectancy']} -> held-out "
        f"{row['held_out_expectancy']} over {row['held_out_n_trades']} closed trades",
        f"  pbo {row['pbo']} ({row['n_columns_used']} columns used, "
        f"{row['n_columns_skipped']} skipped as thin)",
        f"  gate {'PASSED' if row['gate_passed'] else 'REFUSED'}: {row['failures'] or '[]'}",
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Optuna parameter study (#476), read-only")
    parser.add_argument("--db", default=DEFAULT_DB, help=f"Candle cache (default: {DEFAULT_DB})")
    parser.add_argument(
        "--out", default=DEFAULT_OUT, help=f"JSONL artifact (default: {DEFAULT_OUT})"
    )
    parser.add_argument(
        "--ledger",
        default=DEFAULT_LEDGER,
        help=f"Trials ledger to append the diagnostic rows to (default: {DEFAULT_LEDGER})",
    )
    parser.add_argument(
        "--products",
        default=",".join(PRODUCTS),
        help="Comma-separated product ids (default: the liquid majors " + ",".join(PRODUCTS) + ")",
    )
    parser.add_argument(
        "--window",
        type=int,
        default=WINDOW_BARS,
        help=f"Trailing ONE_HOUR bars per study (default: {WINDOW_BARS})",
    )
    parser.add_argument(
        "--n-trials",
        type=int,
        default=N_TRIALS,
        help=f"Optuna trials per study (default: {N_TRIALS}; lower it only to smoke-test)",
    )
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args()

    products = [p.strip() for p in args.products.split(",")]
    for product_id in products:
        bars = len(load_candles(args.db, product_id))
        if bars < args.window:
            raise SystemExit(
                f"{product_id} has only {bars} ONE_HOUR bars cached -- the pre-registered "
                f"window needs {args.window}. Refusing to shrink the window silently."
            )
        print(f"{product_id:12} {bars} bars, studying the trailing {args.window}")

    jobs = [
        (args.db, family, product_id, args.window, args.n_trials)
        for family in FIXED_PARAMS
        for product_id in products
    ]
    rows: list[CellRow] = []
    t0 = time.perf_counter()
    with ProcessPoolExecutor(max_workers=args.workers) as ex, open(args.out, "w") as fh:
        for i, row in enumerate(ex.map(_one, jobs, chunksize=1), 1):
            rows.append(row)
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            print(
                f"  {i}/{len(jobs)} cells done "
                f"({row['family']} @ {row['product']}: {row['verdict']}) "
                f"{time.perf_counter() - t0:.0f}s",
                flush=True,
            )

    rows.sort(key=lambda r: (r["family"], r["product"]))
    print()
    for row in rows:
        print()

    # The per-family winner (best TRAIN expectancy -- the study's own objective) and its
    # gate outcome. Even a winning cell is only ever a PROPOSAL candidate; the refusal
    # lines below are the harness's honest primary product.
    print("=" * 78)
    print("PER FAMILY — winner across products, gate applied, ledger row appended")
    print("=" * 78)
    for family in FIXED_PARAMS:
        cells = [r for r in rows if r["family"] == family]
        winner = max(cells, key=lambda r: Decimal(r["best_train_expectancy"]))
        held_out = Decimal(winner["held_out_expectancy"])
        pbo = None if winner["pbo"] is None else Decimal(winner["pbo"])
        gate = OverfittingGate(
            train_expectancy=Decimal(winner["best_train_expectancy"]),
            held_out_expectancy=held_out,
            held_out_positive=held_out > 0,
            pbo=pbo,
            n_columns_used=winner["n_columns_used"],
            n_columns_skipped=winner["n_columns_skipped"],
            passed=winner["gate_passed"],
            failures=tuple(winner["failures"]),
        )
        print()
        print(proposal_verdict(family, winner["product"], gate))
        # The ledger row: `params` carries the fitted configuration PLUS the product (the
        # dca-ablation row's precedent -- descriptive context rides params); `summary`
        # stays int-or-Decimal because `_decode_summary` re-parses every value it finds.
        # An unavailable PBO is stated by its absence plus pbo_available=0, never by a
        # sentinel number that could be mistaken for one.
        record = append_trial(
            Path(args.ledger),
            trial_id=f"476-optuna-{family}",
            session=SESSION,
            rule=family,
            params={"product_id": winner["product"], **winner["best_params"]},
            provenance="fitted",
            kind="sweep_node",
            decision="diagnostic_only",
            per_trade_pnl=[Decimal(v) for v in winner["held_out_per_trade_pnl"]],
            series_missing=not winner["held_out_per_trade_pnl"],
            summary={
                "bars": winner["bars"],
                "n_trials": winner["n_trials"],
                "seed": winner["seed"],
                "fee_pct": Decimal(winner["fee_pct"]),
                "slippage_pct": Decimal(winner["slippage_pct"]),
                "train_expectancy": Decimal(winner["best_train_expectancy"]),
                "held_out_expectancy": held_out,
                **({"pbo": pbo, "pbo_available": 1} if pbo is not None else {"pbo_available": 0}),
                "gate_passed": int(winner["gate_passed"]),
            },
        )
        print(f"  ledger row {record.trial_id} appended ({record.decision})")

    print()
    for row in rows:
        print("\n".join(_render_cell(row)))
        print()

    print(f"artifact: {args.out}  ({len(rows)} cells, {time.perf_counter() - t0:.0f}s)")
    print("\nReminder: research-side only. Nothing here tunes a live or paper profile;")
    print("a PROPOSE line is a hypothesis for the unchanged promotion gauntlet, and the")
    print("binding constraint is the fee, not the parameters. A refusal is the honest")
    print("result, not a tool failure.")


if __name__ == "__main__":
    main()
