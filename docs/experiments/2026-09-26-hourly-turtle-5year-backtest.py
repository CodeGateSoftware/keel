#!/usr/bin/env python
"""The hourly `turtle_breakout`, five years, per trade (#823): the driver.

**NOT pre-registered.** This driver re-derives, trade by trade, a run already made and already
reported: `keel simulate --years 5 --no-fetch` over the 19 hourly rules on 2026-09-26
(result on #823). It exists so the companion record,
`docs/experiments/2026-09-26-hourly-turtle-5year-backtest.md`, can publish the complete
dataset behind those numbers and the fee-to-risk arithmetic they imply. Nothing in the method
below was chosen after seeing a result, except one arm: the **0% fee arm**, added because the
first write-up extrapolated to zero fee instead of measuring it, and
`2026-08-12-fee-curve-and-rsi-meanrev.md` had already measured something else there.

## Method: the same code path as `keel simulate`'s edge pass, nothing new
- **Rules:** every `turtle_breakout` row whose `params.granularity` is `ONE_HOUR`, whatever its
  status, from the `--db` cache. The 2026-09-26 run used the 19 rows of `keel-paperhourly.db`.
  Built with `agent._build_rule`, exactly as `simulate` does.
- **Candles:** `commands.simulate.load_sim_candles` over `[now_ts - years * 365 d, now_ts]`.
  `--now-ts` defaults to the original run's `1790455996`, so the window is the one reported.
- **Slippage:** per product, from `commands.simulate.slippage_assumptions` (#259). This is the
  resolver the edge table priced with, 5 to 101 bp per leg on this universe.
- **Fills and fees:** `sim.report.edge_table`: next-bar-open market fills, the fee charged on
  both legs, R from the achieved fill against the original stop (#820).
- **Fee arms:** 1.20% taker (`config.paper-hourly.yaml`'s `fees.taker_pct`, the rate the
  deployment's forward trades actually recorded, at least 1.136% per leg), 0.60%
  (sensitivity), and 0% (measured, not extrapolated).
- **One trade set.** A fee changes a trade's P&L, never its entry, stop, exit bar or fill. The
  driver ASSERTS that all arms produce the identical set of trades and fails if they do not.
  That is why each trade can carry one R per arm.
- **Daily reference (optional, `--daily-db`):** the daily `turtle_breakout` rows of a second
  cache, run at the headline fee, for one purpose only: to compare how wide a daily stop is
  (risk as a fraction of price) with an hourly one.

## Output (`--out`, JSONL, every Decimal as a string; re-running truncates)
- one `meta` row: window, arms, rule params, slippage per product;
- one `trade` row per closed hourly trade, identical across arms;
- one `summary` row per (arm, product) and per (arm, `__pooled__`), from
  `stats.summarize` in R;
- one `daily_risk` summary row when `--daily-db` is given.

## Safety
READ-ONLY. Both caches are opened `mode=ro`. The only writes are `--out` and stdout.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from decimal import Decimal
from typing import Any

from keel import agent
from keel.commands.fetch import DAYS_PER_YEAR
from keel.commands.simulate import SIM_SLIPPAGE_PCT, load_sim_candles, slippage_assumptions
from keel.data.repository import Repository
from keel.sim import report as report_mod
from keel.strategy.rules.base import Rule, Trade

ORIGINAL_NOW_TS = 1790455996
FEE_ARMS = (Decimal("0.012"), Decimal("0.006"), Decimal("0"))
HEADLINE_FEE = FEE_ARMS[0]


def _repo(db_path: str) -> Repository:
    """Read-only, with the `sqlite3.Row` factory `keel.data.db.connect` sets (the repository reads
    rows by column name). Not `connect()` itself: that converts the file to WAL, a write."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return Repository(conn)


def _turtle_rules(repo: Repository, granularity: str) -> list[Rule]:
    return [
        agent._build_rule(row)
        for row in repo.get_rules()
        if row["kind"] == "turtle_breakout"
        and (row["params"] or {}).get("granularity", "ONE_DAY") == granularity
    ]


def _edge(
    db_path: str, granularity: str, now_ts: int, years: int, fees: tuple[Decimal, ...]
) -> tuple[list[Rule], dict[str, Decimal], dict[Decimal, dict[str, list[Trade]]]]:
    """`(rules, slippage by product, {fee: {rule_key: closed trades}})` over the window."""
    repo = _repo(db_path)
    rules = _turtle_rules(repo, granularity)
    products = sorted({rule.product_id for rule in rules})
    start_ts = now_ts - years * DAYS_PER_YEAR * 86400
    candles, _prices = load_sim_candles(repo, products, start_ts, now_ts)
    rows, resolve = slippage_assumptions(candles, products, products, SIM_SLIPPAGE_PCT)
    slippage = {row.product_id: row.slippage_pct for row in rows}
    by_fee: dict[Decimal, dict[str, list[Trade]]] = {}
    for fee in fees:
        table = report_mod.edge_table(
            rules, candles, fee_pct=fee, slippage_pct=SIM_SLIPPAGE_PCT, slippage_by_product=resolve
        )
        by_fee[fee] = {
            key: [t for t in result.trades if t.exit_ts is not None]
            for key, result in table.items()
            if key != report_mod.POOLED_KEY
        }
    return rules, slippage, by_fee


def _identity(trade: Trade) -> tuple[Any, ...]:
    return (trade.entry_ts, trade.exit_ts, trade.entry, trade.exit, trade.initial_risk)


def _summary(arm: str, key: str, trades: list[Trade]) -> dict[str, Any]:
    from keel.strategy.stats import summarize

    s = summarize(sorted(trades, key=lambda t: t.exit_ts or 0))
    return {
        "row": "summary",
        "arm": arm,
        "key": key,
        "n": s.n_trades,
        "win_rate": str(round(Decimal(s.win_rate), 4)),
        "expectancy_r": str(s.expectancy_r),
        "avg_win_r": str(s.avg_win_r),
        "avg_loss_r": str(s.avg_loss_r),
        "profit_factor_r": str(s.profit_factor_r),
        "max_losing_streak": s.max_losing_streak,
    }


def _fmt(value: Decimal | None, places: int = 6) -> str | None:
    return None if value is None else str(round(value, places))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", required=True, help="candle cache holding the hourly rules")
    parser.add_argument("--daily-db", help="optional cache holding the daily turtle rules")
    parser.add_argument("--now-ts", type=int, default=ORIGINAL_NOW_TS)
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    rules, slippage, by_fee = _edge(args.db, "ONE_HOUR", args.now_ts, args.years, FEE_ARMS)
    arms = {fee: f"fee_{int(fee * 10000):04d}bp" for fee in FEE_ARMS}

    headline = by_fee[HEADLINE_FEE]
    for fee, per_key in by_fee.items():
        for key, trades in headline.items():
            if [_identity(t) for t in per_key.get(key, [])] != [_identity(t) for t in trades]:
                raise SystemExit(f"{key}: the trade set differs between fee arms -- not joinable")

    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "row": "meta",
                    "now_ts": args.now_ts,
                    "start_ts": args.now_ts - args.years * DAYS_PER_YEAR * 86400,
                    "arms": list(arms.values()),
                    "slippage_pct_per_leg": {p: str(v) for p, v in sorted(slippage.items())},
                    "rules": {
                        r.product_id: {k: str(v) for k, v in r.params.items()} for r in rules
                    },
                }
            )
            + "\n"
        )
        n = 0
        for key in sorted(headline):
            product = key.split(":", 1)[1]
            for i, trade in enumerate(headline[key]):
                risk = trade.initial_risk
                row = {
                    "row": "trade",
                    "product": product,
                    "entry_ts": trade.entry_ts,
                    "exit_ts": trade.exit_ts,
                    "entry_fill": _fmt(trade.entry, 8),
                    "exit_fill": _fmt(trade.exit, 8),
                    "risk_pct": _fmt(risk / trade.entry if risk else None),
                    "outcome": trade.outcome,
                    "mfe_r": _fmt(trade.mfe / risk if risk else None, 4),
                    "mae_r": _fmt(trade.mae / risk if risk else None, 4),
                }
                for fee, arm in arms.items():
                    row[f"r_{arm}"] = _fmt(by_fee[fee][key][i].r_multiple, 4)
                fh.write(json.dumps(row) + "\n")
                n += 1
        for fee, arm in arms.items():
            pooled: list[Trade] = []
            for key in sorted(by_fee[fee]):
                trades = by_fee[fee][key]
                pooled.extend(trades)
                fh.write(json.dumps(_summary(arm, key, trades)) + "\n")
            fh.write(json.dumps(_summary(arm, report_mod.POOLED_KEY, pooled)) + "\n")
        if args.daily_db:
            _d_rules, _d_slip, d_by_fee = _edge(
                args.daily_db, "ONE_DAY", args.now_ts, args.years, (HEADLINE_FEE,)
            )
            d_risk = [
                t.initial_risk / t.entry
                for trades in d_by_fee[HEADLINE_FEE].values()
                for t in trades
                if t.initial_risk
            ]
            fh.write(
                json.dumps(
                    {
                        "row": "daily_risk",
                        "n": len(d_risk),
                        "median_risk_pct": str(round(statistics.median(d_risk), 6)),
                    }
                )
                + "\n"
            )
    print(f"{n} hourly trades written to {args.out}")


if __name__ == "__main__":
    main()
