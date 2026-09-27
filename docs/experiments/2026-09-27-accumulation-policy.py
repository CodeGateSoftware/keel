"""PRE-REGISTERED BEFORE RUNNING: accumulation policy vs static DCA after fees (#831), the driver.

This docstring is the pre-registration. It freezes §3-§8 of the approved design,
`docs/superpowers/specs/2026-09-27-accumulation-policy-design.md` (revision 2, #834), with
decisions D1-D6 settled as that spec proposes them. It was committed before the driver had run
against any real candle, so no number existed when it was written. The harness is
`keel/sim/accumulation_policy.py`; its module docstring states the account conventions in full.

## The question
Given the same monthly deposits, does *how* the money is deployed (value averaging, steering
contributions to underweight assets, or band trimming) give better risk-adjusted accumulation
than static monthly DCA, net of the fees this account actually pays? This is accumulation
policy, not an edge claim: ADR 0006's trade-count floor does not apply, and nothing here changes
the live DCA rule or any rail.

## Universe, weights, windows (spec §3, decision D1)
- **Weights:** `target_weights` from `config.paperforward.yaml`: BTC 0.30, ETH 0.20, PAXG 0.20,
  SOL, XLM, LTC, ADA, LINK 0.06 each. The driver refuses to run if the file's weights differ.
- **Primary run, PAXG-free, 5 years:** the 7 non-PAXG assets, weights renormalised to sum to 1
  (BTC 0.375, ETH 0.25, the other five 0.075). The window is 5 x 365 days ending
  `now_ts = 1790455996` (2026-09-26 20:53:16 UTC), i.e. from 2021-09-27 20:53 UTC. This is the
  #830 window.
- **Secondary run, with PAXG, ~1.4 years:** all 8 assets at the config weights, from PAXG-USD's
  first cached daily bar (Coinbase's history starts 2025-05-08) to the same `now_ts`. It is
  short and covers one regime, and it is labelled as such. It is the only test of the PAXG
  prediction below.
- **Data:** daily candles from the `--db` cache via `commands.simulate.load_sim_candles`. Only
  COMPLETED daily bars are used (`ts + 86400 <= now_ts`), so the still-forming bar of 2026-09-26
  is excluded. Each run's panel is the intersection of its assets' daily timestamps; any day
  missing for one asset is dropped for all, and the count is written to the output.

## The account, common to every arm (spec §4, decisions D2, D3)
- **Deposits:** D = $500 on the first daily bar of each UTC calendar month in the panel (the 1st
  whenever that bar exists), identical for every arm. This is `sim.benchmark.
  dca_into_allowlist`'s convention, so a window that opens mid-month, as the primary one does,
  deposits on its first day. Undeployed cash stays cash and earns nothing.
- **Fills:** decided on the completed bar of the deposit day, filled at the next bar's open, as
  in `report.accumulation_table` (#821). A decision with no next bar is dropped.
- **Fees, allowance-aware (primary):** buys are fee-free until the calendar month's buy notional
  reaches A = $500 (Coinbase One Basic, `subscription.assumed_free_volume_usd`); the excess
  pays the 1.20% taker fee. Sells always pay 1.20%. A buy's fee comes off the top of its dollar
  amount.
- **Fees, flat taker (sensitivity):** 1.20% on every leg, as for an account with no subscription.
- **Slippage:** per product on every leg, from `commands.simulate.slippage_assumptions` (#259),
  computed from each run's own loaded daily bars, with `SIM_SLIPPAGE_PCT` as the fallback.
- **Holdings:** one lot per asset at average cost. No leverage, no shorting, no borrowing. Cash
  never goes negative.

## Arms, all parameters declared (spec §5, decisions D4, D5)
Let `w_i` be the weights, `H_i` the value held in asset i at the decision close before month t's
trades, `H = sum H_i`, and `t = 1, 2, ...` the month index.
- **A. Static DCA (baseline):** buy `w_i * D`. Under a zero-fee model on gapless candles it
  reproduces `dca_into_allowlist` to the cent (a harness test).
- **B. Bounded value averaging, buy-only:** `V = w_i * D * t` (g = 0);
  `buy_i = clamp(V - H_i, 0, 3 * w_i * D)`, every buy scaled by the same factor if cash is
  short. A surplus is never sold, and cash left idle is reported as cash drag.
- **C. Deposit steered by shortfall, buy-only:** `s_i = max(0, w_i (H + D) - H_i)`,
  `buy_i = D * s_i / sum s`, falling back to `w_i` if `sum s = 0`.
- **D. DCA plus selective trimming:** A's buy; then sell only assets strictly above the upper band
  `h_i / H > w_i + b_i`, down to `w_i`, with the band checked on the post-buy book; then
  redeploy the trims' net proceeds to the assets below `w_i`, by shortfall.
  `b_i = max(0.15 w_i, 0.015)`. A lower-band breach alone triggers no trade.

Three treatments against one baseline: three comparisons, stated beside every difference.

## Metrics (spec §7-§8)
Per arm, run and fee mode, on the historical path: terminal account value (holdings at the close
plus cash), IRR on deposits (`metrics.irr`, a per-deposit-period rate) and the money-weighted
CAGR beside it, a daily time-weighted return index with deposits neutralised, Sharpe, **Sortino**
and **max drawdown** on that index (`sim.metrics`, rf = 0, 365 periods), fees paid (buy and
sell), turnover (buy plus sell notional over deposits), and average cash share.

## Inference (spec §8)
A stationary block bootstrap (Politis-Romano) of JOINT daily rows: every asset takes the same
row indices, so cross-correlation survives. A row is `(open_t / close_{t-1}, close_t /
close_{t-1})`; each path is rebuilt from the historical first bar on the historical calendar.
Mean block lengths **20 and 60 days**, **2,000 paths** each, **seed 831** for every (run, fee
mode, block length), so both fee modes see identical paths. Every arm runs on the same paths.
Reported per arm and block length: the distribution of delta(arm - A) in Sortino and in max
drawdown, meaning `P(delta Sortino > 0)`, the median deltas, and the 5th and 95th percentiles of
delta Sortino. The historical path is shown beside both block lengths.

## Decision rule (decision D6, pre-registered)
For each of B, C and D, separately for each run and fee mode:
- **"better than static DCA"** only if, under BOTH the 20-day AND the 60-day blocks,
  `P(delta Sortino > 0) >= 0.95` (a tie is not an improvement) AND the median delta max drawdown
  is not worse (`<= 0`, drawdowns being positive fractions);
- **"block-length-dependent"** if it passes at exactly one block length;
- otherwise **"not better than static DCA after fees"**, which is a complete and useful result.

The headline verdict is the **primary run under the allowance-aware fees**. The flat-taker run
and the secondary (with-PAXG) run are reported beside it, labelled as sensitivity and short-
window evidence respectively, and cannot overturn it.

## Expectation, recorded before running (spec §8)
Arm C roughly equals A: it is nearly free and the effect is small. Arm B trails in rising
markets (cash drag) and leads after drawdowns, with no robust Sortino edge. Arm D is hurt on
the 5-year PAXG-free run (sell fees, correlated assets) and has its best case on the short run
with PAXG, because PAXG is the only low-correlation asset in `target_weights` and the
rebalancing premium lives in dispersion between weakly correlated assets (KB §51). If band
trimming helps anywhere, it should show through the PAXG leg and should not help a PAXG-free
universe much.

## Multiple testing and the ledger
Three treatment arms across two runs and two fee modes. With `--ledger`, one row per arm, run
and fee mode (16 rows, A included) goes to the trials ledger as `kind="ablation"`,
`provenance="a_priori"` (every parameter is declared, none fitted), `decision=
"diagnostic_only"`. Summary values are ints, bools or numeric strings only (#830: a float
breaks the hash chain and a word breaks the read-back); words go in `params`. `per_trade_pnl`
carries the arm's MONTHLY deposit-neutral account P&L on the historical path (value change
minus deposits, per calendar month), and `params` says so.

## Provenance and safety
READ-ONLY against the candle cache (`mode=ro`). The only writes are `--out` (JSONL), the
`--ledger` append and stdout. `--paths` exists only for plumbing smoke tests on synthetic
data. Any value other than 2,000 is not the pre-registered run: it is marked so in the output,
and the driver refuses to write the ledger with it.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from keel.commands.fetch import DAYS_PER_YEAR
from keel.commands.simulate import SIM_SLIPPAGE_PCT, load_sim_candles, slippage_assumptions
from keel.config import load_config
from keel.data.repository import Repository
from keel.execution.guards import _utc_month_bounds
from keel.research.ledger import append_trial
from keel.sim import accumulation_policy as ap
from keel.sim import metrics
from keel.types import Granularity

NOW_TS = 1790455996
PRIMARY_YEARS = 5
DAY = 86_400
SEED = 831
SESSION = "accumulation-policy-2026-09-27"
QUOTE = "USD"
FROZEN_WEIGHTS = {
    "BTC": Decimal("0.30"),
    "ETH": Decimal("0.20"),
    "PAXG": Decimal("0.20"),
    "SOL": Decimal("0.06"),
    "XLM": Decimal("0.06"),
    "LTC": Decimal("0.06"),
    "ADA": Decimal("0.06"),
    "LINK": Decimal("0.06"),
}
PRIMARY = "primary"
SECONDARY = "secondary"
HEADLINE = (PRIMARY, ap.ALLOWANCE)
ARM_LABELS = {
    "A": "static DCA",
    "B": "bounded value averaging, buy-only",
    "C": "deposit steered by shortfall",
    "D": "DCA plus selective trimming",
}
PLACES = Decimal("0.000001")


def _repo(db_path: str) -> Repository:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return Repository(conn)


def _num(value: Decimal) -> str:
    """A numeric string for JSON and the ledger: never a float, never a word."""
    return str(value.quantize(PLACES))


def _weights(config_path: str) -> dict[str, Decimal]:
    weights = dict(load_config(config_path).target_weights)
    if weights != FROZEN_WEIGHTS:
        raise SystemExit(
            f"{config_path} target_weights {weights} differ from the pre-registered "
            f"{FROZEN_WEIGHTS}; this is not the pre-registered run"
        )
    return weights


def _first_daily_ts(repo: Repository, asset: str) -> int:
    daily = repo.get_candles(f"{asset}-{QUOTE}", Granularity.ONE_DAY, None, NOW_TS)
    if not daily:
        raise SystemExit(f"no cached daily bars for {asset}-{QUOTE}")
    return daily[0].ts


def _panel(
    repo: Repository, assets: Sequence[str], start_ts: int, end_ts: int
) -> tuple[ap.PricePanel, dict[str, Decimal], dict[str, Any]]:
    """The run's aligned panel of COMPLETED daily bars, its slippage, and what was dropped."""
    products = [f"{a}-{QUOTE}" for a in assets]
    candles, _prices = load_sim_candles(repo, products, start_ts, end_ts)
    _rows, resolve = slippage_assumptions(candles, products, products, SIM_SLIPPAGE_PCT)
    slippage = {a: resolve(f"{a}-{QUOTE}") for a in assets}

    daily = {
        a: {c.ts: c for c in candles[a][Granularity.ONE_DAY] if c.ts + DAY <= end_ts}
        for a in assets
    }
    common = sorted(set.intersection(*(set(bars) for bars in daily.values())))
    if len(common) < 2:
        raise SystemExit(f"fewer than two common daily bars for {list(assets)}")
    panel = ap.PricePanel(
        ts=common,
        opens={a: [daily[a][ts].open for ts in common] for a in assets},
        closes={a: [daily[a][ts].close for ts in common] for a in assets},
    )
    meta = {
        "start_ts": start_ts,
        "end_ts": end_ts,
        "panel_days": len(common),
        "first_ts": common[0],
        "last_ts": common[-1],
        "dropped_days": {a: len(daily[a]) - len(common) for a in assets},
        "slippage_pct": {a: str(s) for a, s in slippage.items()},
    }
    return panel, slippage, meta


def _monthly_pnl(result: ap.AccountResult) -> list[Decimal]:
    """Deposit-neutral account P&L per calendar month on the path, in dollars."""
    pnl = metrics.bar_pnl(result.equity_curve, result.deposits)
    months: dict[int, Decimal] = {}
    for (ts, _), value in zip(result.equity_curve[1:], pnl, strict=True):
        key, _ = _utc_month_bounds(ts)
        months[key] = months.get(key, Decimal(0)) + value
    return [months[key] for key in sorted(months)]


def _quantile(sorted_values: Sequence[Decimal], q: Decimal) -> Decimal:
    index = min(len(sorted_values) - 1, max(0, int(q * len(sorted_values))))
    return sorted_values[index]


def _block_stats(deltas: Sequence[tuple[Decimal, Decimal]]) -> dict[str, Any]:
    ds = sorted(d for d, _ in deltas)
    return {
        "p_sortino_positive": _num(ap.p_sortino_positive(deltas)),
        "median_d_sortino": _num(Decimal(statistics.median(ds))),
        "median_d_mdd": _num(ap.median_delta_mdd(deltas)),
        "d_sortino_p05": _num(_quantile(ds, Decimal("0.05"))),
        "d_sortino_p95": _num(_quantile(ds, Decimal("0.95"))),
        "passes": ap.passes(deltas),
    }


def _run_rows(
    run: str,
    fee_mode: str,
    panel: ap.PricePanel,
    weights: dict[str, Decimal],
    slippage: dict[str, Decimal],
    n_paths: int,
) -> list[dict[str, Any]]:
    fees = ap.FeeModel(
        mode=fee_mode, taker_pct=ap.TAKER_PCT, allowance_usd=ap.ALLOWANCE_USD, slippage=slippage
    )
    results = {arm: ap.run_account(panel, weights, fn, fees) for arm, fn in ap.ARMS.items()}
    history = {arm: ap.summarize(result) for arm, result in results.items()}
    boot = {
        block: ap.bootstrap_deltas(
            panel, weights, fees, mean_block=block, n_paths=n_paths, seed=SEED
        )
        for block in ap.BLOCK_LENGTHS
    }
    rows = []
    for arm in ap.ARMS:
        row: dict[str, Any] = {
            "row": "arm",
            "run": run,
            "fee_mode": fee_mode,
            "arm": arm,
            "arm_label": ARM_LABELS[arm],
            "headline": (run, fee_mode) == HEADLINE,
            "n_paths": n_paths,
            **{f"hist_{k}": _num(v) for k, v in history[arm].items()},
            "hist_d_sortino": _num(history[arm]["sortino"] - history["A"]["sortino"]),
            "hist_d_mdd": _num(history[arm]["max_drawdown"] - history["A"]["max_drawdown"]),
            "monthly_pnl": [_num(v) for v in _monthly_pnl(results[arm])],
        }
        if arm != "A":
            for block in ap.BLOCK_LENGTHS:
                for key, value in _block_stats(boot[block][arm]).items():
                    row[f"{key}_b{block}"] = value
            row["verdict"] = ap.verdict({block: boot[block][arm] for block in ap.BLOCK_LENGTHS})
        else:
            row["verdict"] = "baseline"
        rows.append(row)
    return rows


def _append_ledger(ledger: str, row: dict[str, Any], meta: dict[str, Any]) -> None:
    words = ("row", "run", "fee_mode", "arm", "arm_label", "verdict", "monthly_pnl")
    summary = {k: v for k, v in row.items() if k not in words}
    series = [Decimal(v) for v in row["monthly_pnl"]]
    append_trial(
        ledger,
        trial_id=f"{SESSION}-{row['run']}-{row['fee_mode']}-{row['arm']}",
        session=SESSION,
        rule="accumulation_policy",
        params={
            "arm": row["arm"],
            "arm_label": row["arm_label"],
            "run": row["run"],
            "fee_mode": row["fee_mode"],
            "verdict": row["verdict"],
            "headline": row["headline"],
            "window_start_ts": meta["start_ts"],
            "window_end_ts": meta["end_ts"],
            "panel_days": meta["panel_days"],
            "weights": {a: str(w) for a, w in meta["weights"].items()},
            "deposit_usd": str(ap.DEPOSIT_USD),
            "allowance_usd": str(ap.ALLOWANCE_USD),
            "taker_pct": str(ap.TAKER_PCT),
            "block_lengths": list(ap.BLOCK_LENGTHS),
            "seed": SEED,
            "units": (
                "per_trade_pnl is the arm's deposit-neutral account P&L per calendar month on "
                "the historical path, in USD; not trades"
            ),
        },
        provenance="a_priori",
        kind="ablation",
        decision="diagnostic_only",
        per_trade_pnl=series,
        series_missing=not series,
        summary=summary,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--db", required=True, help="a COPY of the candle cache (opened mode=ro)")
    parser.add_argument("--out", required=True, help="JSONL results")
    parser.add_argument("--ledger", help="trials ledger to append to (omit to skip)")
    parser.add_argument("--config", default="config.paperforward.yaml")
    parser.add_argument(
        "--paths",
        type=int,
        default=ap.N_PATHS,
        help="plumbing smoke tests only: any value but 2000 is not the pre-registered run",
    )
    args = parser.parse_args()
    pre_registered = args.paths == ap.N_PATHS
    if args.ledger and not pre_registered:
        parser.error("--ledger is refused unless --paths is the pre-registered 2000")

    repo = _repo(args.db)
    config_weights = _weights(args.config)
    runs = {
        PRIMARY: (
            ap.renormalise(config_weights, exclude=["PAXG"]),
            NOW_TS - PRIMARY_YEARS * DAYS_PER_YEAR * DAY,
        ),
        SECONDARY: (ap.renormalise(config_weights), _first_daily_ts(repo, "PAXG")),
    }

    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "row": "meta",
                    "session": SESSION,
                    "pre_registered_run": pre_registered,
                    "now_ts": NOW_TS,
                    "seed": SEED,
                    "n_paths": args.paths,
                    "block_lengths": list(ap.BLOCK_LENGTHS),
                    "deposit_usd": str(ap.DEPOSIT_USD),
                    "allowance_usd": str(ap.ALLOWANCE_USD),
                    "taker_pct": str(ap.TAKER_PCT),
                    "fee_modes": list(ap.FEE_MODES),
                    "headline": list(HEADLINE),
                }
            )
            + "\n"
        )
        for run, (weights, start_ts) in runs.items():
            panel, slippage, meta = _panel(repo, list(weights), start_ts, NOW_TS)
            meta["weights"] = weights
            fh.write(
                json.dumps(
                    {
                        "row": "run",
                        "run": run,
                        **{k: v for k, v in meta.items() if k != "weights"},
                        "weights": {a: str(w) for a, w in weights.items()},
                    }
                )
                + "\n"
            )
            for fee_mode in ap.FEE_MODES:
                print(f"{run} / {fee_mode}: {meta['panel_days']} days, {args.paths} paths x 2")
                for row in _run_rows(run, fee_mode, panel, weights, slippage, args.paths):
                    fh.write(json.dumps(row) + "\n")
                    fh.flush()
                    if args.ledger:
                        _append_ledger(args.ledger, row, meta)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
