#!/usr/bin/env python
"""Rule-family significance at the fee actually paid (#475) — the driver.

PRE-REGISTERED BEFORE RUNNING (this docstring is the pre-registration; the companion record
`docs/experiments/2026-08-21-rule-family-significance.md` reports what came out).

## The question
Issue #475, verbatim in substance: *is a family's edge distinguishable from zero at the fee
actually paid?* Not "is the backtest profitable" — that is answered, honestly, no
(`docs/launch.md`: 0 of 90, 0 of 82 net positive at the 120 bp taker fee). The question here
is the statistical one underneath: given the reconstructed trades, does the observed win rate
clear the break-even the family's own payoff implies, at a one-sided 5% test, with the
sample size counted HONESTLY?

## Method (frozen before the run)
1. **Reconstruct, don't simulate anew.** Each family runs through `keel.strategy.backtest
   .backtest` over the full cached ONE_HOUR history per product — next-bar-open market fills,
   fee charged both legs, 5 bp per-leg slippage (`SLIPPAGE_FLOOR_PCT`, the flat conservative
   floor; per-product liquidity scaling is #259's axis and deliberately not mixed in here).
   - `turtle_breakout`: the shipped hourly evidence profile from the cross-verification
     (`entry_lookback 40, adx_period 14, adx_threshold 25, atr_period 20, atr_stop_mult 2,
     target_rr 6`, `granularity ONE_HOUR`; `exit_lookback` left at its default 20).
   - `rsi_meanrev`, `pullback_continuation`: constructor defaults (both ONE_HOUR-native).
   - `dca` is OUT OF SCOPE — no stop, so no win/loss framing and no R-multiple framework
     (cross-verification §7's scope gap, stated there).
2. **Two fee regimes, never a blend.** Outside = `backtest.TAKER_FEE_PCT` (120 bp per leg);
   inside = `Decimal("0")` — the rail-14 fee-free volume allowance, which the
   cross-verification §5 showed is the profitability boundary, not a budget. Same trades,
   same fills, re-priced.
3. **Significance via `keel.research.significance.significance`.** Break-even `1/(1+b)` from
   the payoff, one-sided `1 - normal_cdf(z)` at alpha 5%, and the standard error on
   `throughput.n_eff` (design effect 2.57516, #427) — pooled trades are herding trades.
4. **Every product with >= `MIN_HOURLY_BARS` cached ONE_HOUR candles** (2,000 bars ≈ 3
   months; the warm-up needs of all three families are ≤ 60 bars). No asset picking.
5. **A pooled row per (family, regime)** over all products' trades — the family-level
   answer the issue title asks, at the largest honest n, with the same n_eff correction.

## Expectation, recorded before running
The cross-verification's §5 shape: nothing distinguishable from zero OUTSIDE the allowance
(decisively negative there), and approximately break-even INSIDE it. If a family comes back
"distinguishable" inside the allowance, that is a result to inspect, not celebrate — the
multiple-comparison budget of three families × two regimes × thirty products is stated, not
hidden. This tool's job is to be able to say no.

## Provenance and safety
- READ-ONLY against the candle cache: `sqlite3.connect(f"file:{db}?mode=ro", uri=True)`.
  The deployment db is never written. The only writes are the `--out` JSONL artifact and
  stdout.
- One JSONL row per (family, product, regime) plus one pooled row per (family, regime),
  every Decimal as a string, `p_value` as a float. Re-running truncates and rewrites the
  artifact.
- `--workers` processes (default 8): `rsi_meanrev` takes ~7 minutes per product on this
  machine and there are 60 such cells; the pool is the same shape the 2026-08-12 sweep used.

    .venv/bin/python docs/experiments/2026-08-21-rule-family-significance.py
    .venv/bin/python docs/experiments/2026-08-21-rule-family-significance.py \
        --db ~/keel/keel.db --products BTC-USD,ETH-USD --workers 8
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from concurrent.futures import ProcessPoolExecutor
from decimal import Decimal

from keel.agent import build_rule_from_params
from keel.research.significance import (
    FEE_REGIMES,
    FamilySignificance,
    render_family,
    significance,
)
from keel.strategy.backtest import SLIPPAGE_FLOOR_PCT, TAKER_FEE_PCT, backtest
from keel.types import Candle, Granularity

DEFAULT_DB = "/Users/elmehdiaitbrahim/keel/keel.db"
DEFAULT_OUT = "docs/experiments/2026-08-21-rule-family-significance.jsonl"
GRANULARITY = Granularity.ONE_HOUR
MIN_HOURLY_BARS = 2000

#: JSON-plain params (the `rules.params` shape `build_rule_from_params` exists to coerce).
#: turtle: the cross-verification §4 reconstruction of the hourly evidence profile. The other
#: two families take pure constructor defaults.
FAMILIES: dict[str, dict[str, object]] = {
    "turtle_breakout": {
        "entry_lookback": 40,
        "adx_period": 14,
        "adx_threshold": 25.0,
        "atr_period": 20,
        "atr_stop_mult": "2",
        "target_rr": "6",
        "granularity": GRANULARITY.value,
    },
    "rsi_meanrev": {},
    "pullback_continuation": {},
}

#: (label, per-leg fee). Outside threads `backtest.TAKER_FEE_PCT` -- the same constant
#: `FEE_REGIMES` publishes (asserted at startup so the two cannot drift silently) -- because
#: a deployment must be able to move its rate without editing code.
REGIMES: tuple[tuple[str, Decimal], ...] = (
    ("outside_allowance_taker", TAKER_FEE_PCT),
    ("inside_allowance_fee_free", Decimal("0")),
)


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


def _one(job: tuple[str, str, str, str]) -> dict[str, object]:
    """One (db, family, product, regime) cell: backtest, significance, JSONL row.

    Runs entirely inside a worker process. The db path travels IN the job because macOS
    worker processes are spawned, not forked -- a module global set by `main()` would not
    arrive, and the worker would silently read the default db. A fresh rule is built per
    cell because a `Rule` instance may carry per-series state between `detect` calls;
    sharing one across regimes would make the second regime's fills depend on the first's
    history.
    """
    db_path, family, product_id, regime_label = job
    fee_pct = dict(REGIMES)[regime_label]
    candles = load_candles(db_path, product_id)
    rule = build_rule_from_params(family, {"product_id": product_id, **FAMILIES[family]})
    result = backtest(rule, candles, fee_pct=fee_pct, slippage_pct=SLIPPAGE_FLOOR_PCT)
    outcomes = [(t.outcome, t.pnl, t.r_multiple) for t in result.trades]
    stat = significance(family, regime_label, fee_pct, outcomes)
    return {
        "family": stat.family,
        "product": product_id,
        "fee_regime": stat.fee_regime,
        "fee_pct": str(stat.fee_pct),
        "slippage_pct": str(SLIPPAGE_FLOOR_PCT),
        "bars": len(candles),
        "n_trades": stat.n_trades,
        "wins": stat.wins,
        "open_trades": sum(1 for o, _, _ in outcomes if o == "open"),
        "win_rate": str(stat.win_rate),
        "payoff_b": str(stat.payoff_b),
        "break_even": str(stat.break_even),
        "edge": str(stat.edge),
        "n_effective": str(stat.n_effective),
        "edge_z": str(stat.edge_z),
        "p_value": stat.p_value,
        "edge_ci_low": str(stat.edge_ci_low),
        "detectable_edge": str(stat.detectable_edge),
        "verdict": stat.verdict,
        # carried for the pooled pass in the parent: the raw rows it recomputes from
        "outcomes": [
            (o, None if p is None else str(p), None if r is None else str(r))
            for o, p, r in outcomes
        ],
    }


def _stat_from_row(row: dict[str, object]) -> FamilySignificance:
    """Rebuild the `FamilySignificance` a JSONL row carries, for pooled rendering."""
    return FamilySignificance(
        family=str(row["family"]),
        fee_regime=str(row["fee_regime"]),
        fee_pct=Decimal(str(row["fee_pct"])),
        n_trades=int(row["n_trades"]),
        wins=int(row["wins"]),
        win_rate=Decimal(str(row["win_rate"])),
        payoff_b=Decimal(str(row["payoff_b"])),
        break_even=Decimal(str(row["break_even"])),
        edge=Decimal(str(row["edge"])),
        n_effective=Decimal(str(row["n_effective"])),
        edge_z=Decimal(str(row["edge_z"])),
        p_value=float(row["p_value"]),
        edge_ci_low=Decimal(str(row["edge_ci_low"])),
        detectable_edge=Decimal(str(row["detectable_edge"])),
        verdict=str(row["verdict"]),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Rule-family significance (#475), read-only")
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

    assert FEE_REGIMES["outside_allowance_taker"] == TAKER_FEE_PCT, (
        "FEE_REGIMES drifted from backtest.TAKER_FEE_PCT -- the outside-allowance label "
        "and the rate the reconstruction charges must stay the same number"
    )

    wanted = [p.strip() for p in args.products.split(",")] if args.products else None
    products = hourly_products(args.db, MIN_HOURLY_BARS)
    if wanted is not None:
        products = [(p, n) for p, n in products if p in wanted]
    print(
        f"#475 rule-family significance  db={args.db}  fee regimes: "
        + ", ".join(f"{label}={fee}" for label, fee in REGIMES)
    )
    print(
        f"slippage {SLIPPAGE_FLOOR_PCT}/leg (both regimes -- the allowance waives the "
        f"fee, not the spread)  families={list(FAMILIES)}"
    )
    print(f"products with >= {MIN_HOURLY_BARS} ONE_HOUR bars: {len(products)}")
    for product_id, bars in products:
        print(f"  {product_id:12} {bars} bars")

    jobs = [
        (args.db, family, product_id, regime)
        for family in FAMILIES
        for product_id, _ in products
        for regime, _ in REGIMES
    ]
    rows: list[dict[str, object]] = []
    t0 = time.perf_counter()
    with ProcessPoolExecutor(max_workers=args.workers) as ex, open(args.out, "w") as fh:
        for i, row in enumerate(ex.map(_one, jobs, chunksize=1), 1):
            rows.append(row)
            fh.write(json.dumps({k: v for k, v in row.items() if k != "outcomes"}) + "\n")
            fh.flush()
            if i % 10 == 0 or i == len(jobs):
                print(f"  {i}/{len(jobs)} cells  {time.perf_counter() - t0:.0f}s", flush=True)

        # The pooled row per (family, regime): every product's outcome rows together, the
        # family-level answer at the largest honest n. #359's September review pools exactly
        # this way; the design effect is applied to the POOL, never per product.
        pooled_rows: list[dict[str, object]] = []
        for family in FAMILIES:
            for regime, _ in REGIMES:
                outcomes: list[tuple[str, Decimal | None, Decimal | None]] = []
                for row in rows:
                    if row["family"] == family and row["fee_regime"] == regime:
                        outcomes += [
                            (
                                o,
                                None if p is None else Decimal(p),
                                None if r is None else Decimal(r),
                            )
                            for o, p, r in row["outcomes"]
                        ]
                stat = significance(family, regime, dict(REGIMES)[regime], outcomes)
                pooled = {
                    "family": stat.family,
                    "product": "POOLED",
                    "fee_regime": stat.fee_regime,
                    "fee_pct": str(stat.fee_pct),
                    "slippage_pct": str(SLIPPAGE_FLOOR_PCT),
                    "bars": sum(int(r["bars"]) for r in rows if r["family"] == family),
                    "n_trades": stat.n_trades,
                    "wins": stat.wins,
                    "open_trades": sum(
                        int(r["open_trades"])
                        for r in rows
                        if r["family"] == family and r["fee_regime"] == regime
                    ),
                    "win_rate": str(stat.win_rate),
                    "payoff_b": str(stat.payoff_b),
                    "break_even": str(stat.break_even),
                    "edge": str(stat.edge),
                    "n_effective": str(stat.n_effective),
                    "edge_z": str(stat.edge_z),
                    "p_value": stat.p_value,
                    "edge_ci_low": str(stat.edge_ci_low),
                    "detectable_edge": str(stat.detectable_edge),
                    "verdict": stat.verdict,
                }
                pooled_rows.append(pooled)
                fh.write(json.dumps(pooled) + "\n")
                fh.flush()

    rows.sort(key=lambda r: (str(r["family"]), str(r["fee_regime"]), str(r["product"])))
    for row in rows:
        print()
        print("\n".join(render_family(_stat_from_row(row))))

    print("\n" + "=" * 78)
    print("POOLED PER FAMILY — the issue's question, at the largest honest n (#427)")
    print("=" * 78)
    for row in pooled_rows:
        print()
        print("\n".join(render_family(_stat_from_row(row))))

    print(
        f"\nartifact: {args.out}  ({len(rows)} cells + {len(pooled_rows)} pooled rows, "
        f"{time.perf_counter() - t0:.0f}s)"
    )
    print("\nReminder: this is report-only evidence. A verdict of not_distinguishable is")
    print("the honest result, not a failure of the tool.")


if __name__ == "__main__":
    main()
