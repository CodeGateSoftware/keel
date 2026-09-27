#!/usr/bin/env python
"""Does a 200-day SMA trend filter improve the daily `turtle_breakout`? (#830): the driver.

PRE-REGISTERED BEFORE RUNNING. This docstring is the pre-registration, committed before any
arm was run. The companion record, `docs/experiments/2026-09-27-turtle-sma200-filter.md`,
reports what came out.

## The question
The daily Turtle already gates on ADX(14) > 25, a choppy-regime filter and a higher-timeframe
bias. Its 5-year baseline on v0.18.0 (#830's issue body) is 220 pooled trades, 25.0% wins,
+0.166 R per trade: about 3 win-rate points above break-even, against a detectable edge of
about 13 points. So it is not distinguishable from zero. The hypothesis: entries against a
falling long-term trend are disproportionately false breakouts, and gating on the 200-day SMA
raises pooled expectancy in R.

## Arms (declared now, not tuned afterwards)
All four use `TurtleBreakout(trend_filter=..., trend_sma_period=200, trend_slope_lookback=5)`,
with every other parameter as persisted:
- `off`: no filter. This is the baseline.
- `above`: the decision bar's close is above its 200-day SMA.
- `slope`: the 200-day SMA is higher than 5 daily bars earlier.
- `both`: `above` AND `slope`.

## Data and engine (the same code path as `keel simulate`'s edge pass)
- **Rules:** every `turtle_breakout` row with status `paper` and daily granularity in the
  `--db` cache. For the run this record cites, that is a copy of `~/keel/keel.db` taken
  2026-09-26: the daily paper account's 19 rows. The one `candidate` row (AAVE) is excluded,
  since the paper account does not trade it.
- **Window:** 5 × 365 days ending `now_ts = 1790455996` (2026-09-26 16:53 UTC), the window of
  the 2026-09-26 hourly record, loaded by `commands.simulate.load_sim_candles`.
- **Costs:** 1.20% taker per leg plus per-product slippage from
  `commands.simulate.slippage_assumptions` (#259).
- **Fills and R:** `sim.report.edge_table`: next-bar-open fills, and R from the achieved fill
  against the original stop (#820).

## One comparison window for every arm
A filtered arm cannot enter until a product has 200 + 5 completed daily bars, since the rule
declines rather than trading blind (`trend_filter_history`). The unfiltered arm could, and
those early trades would make the comparison unequal. So **every arm, including `off`, is
evaluated only on entries at or after each product's eligibility time**: the timestamp of
its 206th daily bar in the window. The unrestricted `off` result is reported beside it for
reference only. One known imperfection is stated here rather than discovered later: dropping
an early `off` trade after the fact cannot re-open an entry that trade's open position had
blocked. The effect is at most one trade per product near the boundary.

## Statistics, per arm
Pooled N; win rate; expectancy_r; avg win / avg loss in R; profit factor in R;
break-even win rate `1 / (1 + avg_win_r / |avg_loss_r|)` and the edge over it;
`throughput.n_eff(N)` and `throughput.detectable_edge(n_eff)`, stated as ADR 0006's sentence.
Per-product rows are diagnostics only.

**Difference from the baseline:** expectancy_r(arm) − expectancy_r(`off`, restricted), with a
95% percentile interval from a **cluster bootstrap by entry UTC day**. Trades are resampled by
the day they entered, because breakouts herd (#427). Each arm and the baseline are resampled
independently: 10,000 draws, seed 830. Independent resampling ignores the overlap between the
two samples, which makes the interval wider (conservative), never narrower.

## Decision rule (pre-registered)
- An arm with **N < 100** pooled trades is reported and **not evaluated** (ADR 0006's floor).
- An arm is **"promising, to walk-forward"** only if N ≥ 100 **and** the lower bound of its
  95% difference interval is **> 0**.
- Otherwise the result is **"no improvement distinguishable from the baseline"**.

Nothing here promotes, demotes or changes a rule. A "promising" arm's next step is
`keel research walk-forward` as a separate, separately recorded run.

## Multiple testing
Three filtered arms are three trials. Each arm, including the baseline, is appended to the
trials ledger (`--ledger`) as `kind="ablation"`, `provenance="a_priori"` (the 200-day SMA is
the textbook choice, not fitted), `decision="diagnostic_only"`, with its per-trade R series.
The record states the number of arms beside any difference it reports.

## Expectation, recorded before running
Most likely: every filter removes a large share of entries (a guess of 40–60% retained);
expectancy_r moves by less than its interval width; and the honest headline is "no
improvement distinguishable from the baseline". The `above` arm could fall under the
100-trade floor. A lower bound above zero would be a surprise worth inspecting, not a finding
to celebrate: three arms is three chances.

## Provenance and safety
READ-ONLY against the candle cache (`mode=ro`). The only writes are `--out` (JSONL), the
`--ledger` append, and stdout.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
from decimal import Decimal
from typing import Any

from keel import agent
from keel.commands.fetch import DAYS_PER_YEAR
from keel.commands.simulate import SIM_SLIPPAGE_PCT, load_sim_candles, slippage_assumptions
from keel.data.repository import Repository
from keel.research import throughput
from keel.research.ledger import append_trial
from keel.sim import report as report_mod
from keel.strategy.rules.base import Rule, Trade
from keel.strategy.stats import summarize
from keel.types import Granularity

NOW_TS = 1790455996
FEE = Decimal("0.012")
ARMS = ("off", "above", "slope", "both")
SMA_PERIOD = 200
SLOPE_LOOKBACK = 5
FLOOR = 100
BOOTSTRAP_DRAWS = 10_000
SEED = 830
SESSION = "turtle-sma200-filter-2026-09-27"


def _repo(db_path: str) -> Repository:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return Repository(conn)


def _rules(repo: Repository, arm: str) -> list[Rule]:
    rules = []
    for row in repo.get_rules():
        params = dict(row["params"] or {})
        if row["kind"] != "turtle_breakout" or row["status"] != "paper":
            continue
        if params.get("granularity", "ONE_DAY") != "ONE_DAY":
            continue
        params.update(
            trend_filter=arm, trend_sma_period=SMA_PERIOD, trend_slope_lookback=SLOPE_LOOKBACK
        )
        rule = agent.build_rule_from_params("turtle_breakout", params)
        rule.rule_id = row["id"]
        rules.append(rule)
    return rules


def _eligible_ts(candles: dict, asset: str) -> int | None:
    daily = candles.get(asset, {}).get(Granularity.ONE_DAY, [])
    index = SMA_PERIOD + SLOPE_LOOKBACK
    return daily[index].ts if len(daily) > index else None


def _stats(trades: list[Trade]) -> dict[str, Any]:
    s = summarize(sorted(trades, key=lambda t: t.exit_ts or 0))
    out: dict[str, Any] = {
        "n": s.n_trades,
        "win_rate": str(round(Decimal(str(s.win_rate)), 4)),
        "expectancy_r": None if s.expectancy_r is None else str(round(s.expectancy_r, 4)),
        "avg_win_r": None if s.avg_win_r is None else str(round(s.avg_win_r, 4)),
        "avg_loss_r": None if s.avg_loss_r is None else str(round(s.avg_loss_r, 4)),
        "profit_factor_r": None if s.profit_factor_r is None else str(round(s.profit_factor_r, 4)),
    }
    if s.n_trades and s.avg_win_r and s.avg_loss_r:
        b = s.avg_win_r / abs(s.avg_loss_r)
        breakeven = Decimal(1) / (Decimal(1) + b)
        effective = throughput.n_eff(Decimal(s.n_trades))
        out["breakeven_win_rate"] = str(round(breakeven, 4))
        out["edge_over_breakeven"] = str(round(Decimal(str(s.win_rate)) - breakeven, 4))
        out["n_eff"] = str(round(effective, 1))
        out["detectable_edge"] = str(round(throughput.detectable_edge(effective), 4))
    return out


def _mean_r(trades: list[Trade]) -> float:
    rs = [float(t.r_multiple) for t in trades if t.r_multiple is not None]
    return sum(rs) / len(rs) if rs else 0.0


def _by_day(trades: list[Trade]) -> list[list[Trade]]:
    days: dict[int, list[Trade]] = {}
    for t in trades:
        days.setdefault(t.entry_ts // 86400, []).append(t)
    return list(days.values())


def _bootstrap_difference(arm: list[Trade], base: list[Trade]) -> tuple[float, float]:
    rng = random.Random(SEED)
    a_days, b_days = _by_day(arm), _by_day(base)
    diffs = []
    for _ in range(BOOTSTRAP_DRAWS):
        a = [t for day in rng.choices(a_days, k=len(a_days)) for t in day]
        b = [t for day in rng.choices(b_days, k=len(b_days)) for t in day]
        diffs.append(_mean_r(a) - _mean_r(b))
    diffs.sort()
    return diffs[int(0.025 * BOOTSTRAP_DRAWS)], diffs[int(0.975 * BOOTSTRAP_DRAWS) - 1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", required=True, help="candle cache holding the daily paper rules")
    parser.add_argument("--out", required=True)
    parser.add_argument("--ledger", help="trials ledger to append to (omit to skip)")
    args = parser.parse_args()

    repo = _repo(args.db)
    start_ts = NOW_TS - 5 * DAYS_PER_YEAR * 86400
    base_rules = _rules(repo, "off")
    products = sorted({rule.product_id for rule in base_rules})
    candles, _prices = load_sim_candles(repo, products, start_ts, NOW_TS)
    _rows, resolve = slippage_assumptions(candles, products, products, SIM_SLIPPAGE_PCT)
    eligible = {p.split("-")[0]: _eligible_ts(candles, p.split("-")[0]) for p in products}

    trades: dict[str, dict[str, list[Trade]]] = {}
    for arm in ARMS:
        rules = _rules(repo, arm)
        table = report_mod.edge_table(
            rules, candles, fee_pct=FEE, slippage_pct=SIM_SLIPPAGE_PCT, slippage_by_product=resolve
        )
        trades[arm] = {
            key: [t for t in result.trades if t.exit_ts is not None]
            for key, result in table.items()
            if key != report_mod.POOLED_KEY
        }

    def restricted(arm: str) -> dict[str, list[Trade]]:
        out = {}
        for key, ts in trades[arm].items():
            cutoff = eligible.get(key.split(":", 1)[1])
            out[key] = [t for t in ts if cutoff is not None and t.entry_ts >= cutoff]
        return out

    pooled = {arm: [t for ts in restricted(arm).values() for t in ts] for arm in ARMS}
    unrestricted_off = [t for ts in trades["off"].values() for t in ts]

    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "row": "meta",
                    "session": SESSION,
                    "now_ts": NOW_TS,
                    "start_ts": start_ts,
                    "fee_pct": str(FEE),
                    "arms": list(ARMS),
                    "sma_period": SMA_PERIOD,
                    "slope_lookback": SLOPE_LOOKBACK,
                    "eligible_ts": eligible,
                    "rules": len(base_rules),
                }
            )
            + "\n"
        )
        fh.write(
            json.dumps({"row": "reference", "arm": "off_unrestricted", **_stats(unrestricted_off)})
            + "\n"
        )
        for arm in ARMS:
            row: dict[str, Any] = {"row": "arm", "arm": arm, **_stats(pooled[arm])}
            row["evaluated"] = row["n"] >= FLOOR
            if arm != "off":
                low, high = _bootstrap_difference(pooled[arm], pooled["off"])
                row["diff_expectancy_r_ci95_low"] = str(round(Decimal(str(low)), 4))
                row["diff_expectancy_r_ci95_high"] = str(round(Decimal(str(high)), 4))
                row["verdict"] = (
                    "not evaluated (N < 100)"
                    if not row["evaluated"]
                    else "promising, to walk-forward"
                    if low > 0
                    else "no improvement distinguishable from the baseline"
                )
            fh.write(json.dumps(row) + "\n")
            for key, ts in sorted(restricted(arm).items()):
                fh.write(
                    json.dumps({"row": "product", "arm": arm, "key": key, **_stats(ts)}) + "\n"
                )
            if args.ledger:
                series = [t.r_multiple for t in pooled[arm] if t.r_multiple is not None]
                append_trial(
                    args.ledger,
                    trial_id=f"{SESSION}-{arm}",
                    session=SESSION,
                    rule="turtle_breakout",
                    params={
                        "trend_filter": arm,
                        "trend_sma_period": SMA_PERIOD,
                        "trend_slope_lookback": SLOPE_LOOKBACK,
                        "units": "per_trade_pnl is R, not dollars",
                        "verdict": row.get("verdict", "baseline"),
                    },
                    provenance="a_priori",
                    kind="ablation",
                    decision="diagnostic_only",
                    per_trade_pnl=series,
                    series_missing=not series,
                    # Numbers only: `read_trials` decodes every summary string as a Decimal, so
                    # the arm name and the verdict ride in `params`.
                    summary={k: v for k, v in row.items() if k not in ("row", "arm", "verdict")},
                )
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
