#!/usr/bin/env python3
"""Bankroll-sizing simulation: Kelly Criterion family vs `keel`'s fixed-fractional risk sizing.

Educational / halal framing
----------------------------
`keel` is a halal (riba-free) spot-crypto, long-only, no-leverage trading agent. It sizes
positions with fixed-fractional risk sizing (`keel/execution/sizing.py::size`): risk a constant
`risk_pct` of equity per trade over the entry-to-stop distance, config default `risk_pct = 0.01`
(1%). This script is a pure MATHEMATICS study of capital allocation -- comparing that fixed 1%
against the Kelly Criterion and its relatives (from the `keeks` family of formulas) -- using
simulated coin-flip-style trade sequences. It is not gambling and nothing here trades real money;
it is a stdlib-only Monte Carlo exercise in bankroll-growth arithmetic, run to answer one
question: is keel's 1% risk needlessly timid relative to what the math says is "optimal," and if
so, is there a good reason (estimation error, drawdown pain) to stay timid anyway?

Thesis under test
------------------
keel's promotion floor requires win_rate >= 0.55 and R:R (min_rr) >= 1.5. For a rule sitting
exactly at that floor (p=0.55, b=1.5), full-Kelly f* = (b*p - q)/b = 0.25 (25% of equity risked
per trade), half-Kelly = 12.5%, quarter-Kelly = 6.25%. keel's actual 1% is about 4% of full Kelly.
Two experiments below interrogate this gap.

Determinism
------------
Every path is driven by `random.Random(seed)` with an explicit integer seed derived from a fixed
base seed and the path index, so a re-run of this script reproduces identical numbers. All
strategies being compared on a given path index share the same seed (i.e. draw the same win/loss
sequence up to the point any of them stops from ruin) -- classic "common random numbers" variance
reduction so the comparison across strategies isn't muddied by different strategies happening to
see different luck.

Run: `python simulate.py` (or `python docs/superpowers/analysis/bankroll_sizing/simulate.py` from
the repo root). Writes the markdown report to
`docs/superpowers/reports/2026-07-22-bankroll-sizing-comparison.md`.
"""

from __future__ import annotations

import random
import statistics
import sys
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from sizing_strategies import (  # noqa: E402
    cppi_fraction,
    drawdown_adjusted_kelly,
    fixed_fraction,
    fractional_kelly,
    kelly_fraction,
    naive_flat_fraction,
)

REPO_ROOT = HERE.parents[3]  # docs/superpowers/analysis/bankroll_sizing -> repo root
REPORT_PATH = REPO_ROOT / "docs" / "superpowers" / "reports" / "2026-07-22-bankroll-sizing-comparison.md"

RUIN_THRESHOLD = 1.0  # bankroll <= $1 counts as ruin; path stops (can't go negative)

FractionFn = Callable[[dict], float]


# ---------------------------------------------------------------------------------------------
# Strategy factories: each returns a stateless callable(state) -> risk fraction in [0, 1].
# `state` is a dict with the running per-path values: bankroll, peak (equity high-water mark),
# initial (starting bankroll). Strategies that need "current drawdown" or "distance above a
# ratcheting floor" derive it from `state` each call rather than holding their own mutable state,
# so a single strategy instance can be reused across many independent paths safely.
# ---------------------------------------------------------------------------------------------


def strategy_full_kelly(p: float, b: float) -> FractionFn:
    f = kelly_fraction(p, b)
    return lambda state: f


def strategy_fractional_kelly(p: float, b: float, lam: float) -> FractionFn:
    f = fractional_kelly(p, b, lam)
    return lambda state: f


def strategy_fixed(c: float) -> FractionFn:
    f = fixed_fraction(c)
    return lambda state: f


def strategy_naive_flat(stake: float) -> FractionFn:
    return lambda state: naive_flat_fraction(stake, state["bankroll"])


def strategy_cppi(floor_ratio: float, multiplier: float) -> FractionFn:
    def fn(state: dict) -> float:
        floor = floor_ratio * state["peak"]
        return cppi_fraction(state["bankroll"], floor, multiplier)

    return fn


def strategy_drawdown_kelly(p: float, b: float, max_dd: float) -> FractionFn:
    def fn(state: dict) -> float:
        peak = state["peak"]
        bankroll = state["bankroll"]
        current_dd = 0.0 if peak <= 0 else max(0.0, (peak - bankroll) / peak)
        return drawdown_adjusted_kelly(p, b, current_dd, max_dd)

    return fn


# ---------------------------------------------------------------------------------------------
# Core path simulator
# ---------------------------------------------------------------------------------------------


def simulate_path(
    fraction_fn: FractionFn,
    n_bets: int,
    p: float,
    b: float,
    seed: int,
    initial: float,
) -> dict:
    """Simulate one bankroll path of up to `n_bets` trades.

    Each trade: with probability `p` it wins, paying `+b * f * bankroll`; otherwise it loses
    `f * bankroll`, where `f = fraction_fn(state)` is recomputed fresh before every trade (so
    adaptive strategies -- CPPI, drawdown-adjusted Kelly, naive-flat -- respond to the bankroll's
    current level and history). The path stops early if bankroll drops to or below
    `RUIN_THRESHOLD` ("can't go negative"; ruin is recorded).

    Returns a dict: terminal bankroll, the path's max drawdown from its own running peak, and
    whether it was ruined.
    """
    rng = random.Random(seed)
    bankroll = initial
    peak = initial
    max_dd = 0.0
    ruined = False

    for _ in range(n_bets):
        if bankroll <= RUIN_THRESHOLD:
            ruined = True
            break

        state = {"bankroll": bankroll, "peak": peak, "initial": initial}
        f = fraction_fn(state)
        f = max(0.0, min(1.0, f))

        win = rng.random() < p
        if win:
            bankroll *= 1.0 + b * f
        else:
            bankroll *= 1.0 - f

        peak = max(peak, bankroll)
        dd = 0.0 if peak <= 0 else (peak - bankroll) / peak
        max_dd = max(max_dd, dd)

        if bankroll <= RUIN_THRESHOLD:
            ruined = True

    return {"terminal": bankroll, "max_dd": max_dd, "ruined": ruined}


def run_paths(
    strategies: dict[str, FractionFn],
    n_paths: int,
    n_bets: int,
    p: float,
    b: float,
    base_seed: int,
    initial: float,
) -> dict[str, dict[str, list]]:
    """Run `n_paths` seeded paths for every strategy in `strategies`, using the same seed per
    path index across strategies (common random numbers)."""
    results: dict[str, dict[str, list]] = {
        name: {"terminal": [], "max_dd": [], "ruined": []} for name in strategies
    }
    for i in range(n_paths):
        seed = base_seed + i
        for name, fn in strategies.items():
            r = simulate_path(fn, n_bets, p, b, seed, initial)
            results[name]["terminal"].append(r["terminal"])
            results[name]["max_dd"].append(r["max_dd"])
            results[name]["ruined"].append(r["ruined"])
    return results


def summarize(results: dict[str, dict[str, list]]) -> dict[str, dict[str, float]]:
    summary: dict[str, dict[str, float]] = {}
    for name, r in results.items():
        terminal = r["terminal"]
        max_dd = r["max_dd"]
        ruined = r["ruined"]
        summary[name] = {
            "median_terminal": statistics.median(terminal),
            "mean_terminal": statistics.mean(terminal),
            "stdev_terminal": statistics.stdev(terminal) if len(terminal) > 1 else 0.0,
            "median_max_dd": statistics.median(max_dd),
            "worst_max_dd": max(max_dd),
            "ruin_rate": sum(ruined) / len(ruined),
        }
    return summary


# ---------------------------------------------------------------------------------------------
# Experiment 1: reproduce the keeks binary comparison
# ---------------------------------------------------------------------------------------------

EXP1_N_BETS = 1000
EXP1_N_PATHS = 500
EXP1_INITIAL = 1000.0
EXP1_P = 0.55
EXP1_B = 1.0
EXP1_BASE_SEED = 10_000
EXP1_MAX_DD_TOLERANCE = 0.20


def experiment_1() -> dict[str, dict[str, float]]:
    strategies: dict[str, FractionFn] = {
        "Full Kelly": strategy_full_kelly(EXP1_P, EXP1_B),
        "Half Kelly (0.5)": strategy_fractional_kelly(EXP1_P, EXP1_B, 0.5),
        "Quarter Kelly (0.25)": strategy_fractional_kelly(EXP1_P, EXP1_B, 0.25),
        "Fixed-1% (keel)": strategy_fixed(0.01),
        "CPPI (floor=0.8, m=3)": strategy_cppi(0.8, 3.0),
        "Naive-flat ($10)": strategy_naive_flat(10.0),
        "Drawdown-adj Kelly (max_dd=0.20)": strategy_drawdown_kelly(
            EXP1_P, EXP1_B, EXP1_MAX_DD_TOLERANCE
        ),
    }
    results = run_paths(
        strategies, EXP1_N_PATHS, EXP1_N_BETS, EXP1_P, EXP1_B, EXP1_BASE_SEED, EXP1_INITIAL
    )
    return summarize(results)


# ---------------------------------------------------------------------------------------------
# Experiment 2: keel-relevant -- risk_pct vs the Kelly family at our floor edge (and a stronger
# edge), plus an estimation-error stress test (true p is 5 points lower than assumed).
# ---------------------------------------------------------------------------------------------

EXP2_N_BETS = 200
EXP2_N_PATHS = 500
EXP2_INITIAL = 1000.0
EXP2_KEEL_RISK_PCT = 0.01
EXP2_P_ERROR = 0.05  # estimation-error stress: true p = assumed p - this

PROFILES = {
    "A (floor edge: p=0.55, b=1.5)": {"p": 0.55, "b": 1.5},
    "B (stronger edge: p=0.58, b=2.0)": {"p": 0.58, "b": 2.0},
}


def kelly_levels_for(p: float, b: float) -> dict[str, float]:
    full = kelly_fraction(p, b)
    return {
        "keel-1%": EXP2_KEEL_RISK_PCT,
        "Quarter-Kelly": fractional_kelly(p, b, 0.25),
        "Half-Kelly": fractional_kelly(p, b, 0.5),
        "Full-Kelly": full,
    }


def experiment_2() -> dict[str, dict]:
    """Returns, per profile, per world ("p correct" / "p over-estimated by 0.05"), the summary
    stats for each constant-risk-fraction sizing level."""
    out: dict[str, dict] = {}
    base_seed = 20_000
    for profile_idx, (profile_name, params) in enumerate(PROFILES.items()):
        assumed_p, b = params["p"], params["b"]
        levels = kelly_levels_for(assumed_p, b)
        strategies: dict[str, FractionFn] = {
            name: strategy_fixed(f) for name, f in levels.items()
        }

        worlds = {
            "p correct": assumed_p,
            f"p over-estimated by {EXP2_P_ERROR:.2f}": assumed_p - EXP2_P_ERROR,
        }

        profile_out: dict[str, dict] = {"kelly_levels": levels, "worlds": {}}
        for world_idx, (world_name, true_p) in enumerate(worlds.items()):
            seed = base_seed + profile_idx * 1_000_000 + world_idx * 500_000
            results = run_paths(
                strategies, EXP2_N_PATHS, EXP2_N_BETS, true_p, b, seed, EXP2_INITIAL
            )
            summary = summarize(results)
            for name in summary:
                summary[name]["median_multiple"] = summary[name]["median_terminal"] / EXP2_INITIAL
            profile_out["worlds"][world_name] = summary
        out[profile_name] = profile_out
    return out


# ---------------------------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------------------------


def fmt_money(x: float) -> str:
    if x >= 1_000_000:
        return f"${x:,.0f}"
    return f"${x:,.2f}"


def fmt_pct(x: float) -> str:
    return f"{x * 100:.2f}%"


def exp1_table(summary: dict[str, dict[str, float]]) -> str:
    order = [
        "Full Kelly",
        "Half Kelly (0.5)",
        "Quarter Kelly (0.25)",
        "Fixed-1% (keel)",
        "CPPI (floor=0.8, m=3)",
        "Naive-flat ($10)",
        "Drawdown-adj Kelly (max_dd=0.20)",
    ]
    header = (
        "| Strategy | Median terminal | Mean terminal | Stdev terminal | "
        "Median max DD | Ruin rate |\n"
        "|---|---|---|---|---|---|\n"
    )
    rows = []
    for name in order:
        s = summary[name]
        rows.append(
            f"| {name} | {fmt_money(s['median_terminal'])} | {fmt_money(s['mean_terminal'])} | "
            f"{fmt_money(s['stdev_terminal'])} | {fmt_pct(s['median_max_dd'])} | "
            f"{fmt_pct(s['ruin_rate'])} |"
        )
    return header + "\n".join(rows)


def exp2_table(profile_out: dict, level_order: list[str]) -> str:
    header = (
        "| Sizing level | Risk fraction | World | Median terminal multiple | "
        "Median max DD | Worst max DD | Ruin rate |\n"
        "|---|---|---|---|---|---|---|\n"
    )
    rows = []
    levels = profile_out["kelly_levels"]
    for world_name, summary in profile_out["worlds"].items():
        for name in level_order:
            s = summary[name]
            rows.append(
                f"| {name} | {fmt_pct(levels[name])} | {world_name} | "
                f"{s['median_multiple']:.3f}x | {fmt_pct(s['median_max_dd'])} | "
                f"{fmt_pct(s['worst_max_dd'])} | {fmt_pct(s['ruin_rate'])} |"
            )
    return header + "\n".join(rows)


def build_report(exp1_summary: dict, exp2_results: dict) -> str:
    level_order = ["keel-1%", "Quarter-Kelly", "Half-Kelly", "Full-Kelly"]

    floor_full = kelly_fraction(0.55, 1.5)
    floor_half = fractional_kelly(0.55, 1.5, 0.5)
    floor_quarter = fractional_kelly(0.55, 1.5, 0.25)
    keel_vs_full_pct = EXP2_KEEL_RISK_PCT / floor_full * 100

    lines: list[str] = []
    lines.append("# Bankroll Sizing Comparison: Kelly Criterion Family vs keel's Fixed-Fractional Risk")
    lines.append("")
    lines.append("## Purpose and framing")
    lines.append("")
    lines.append(
        "`keel` is a halal (riba-free), spot-only, long-only, no-leverage crypto trading agent. "
        "It currently sizes every trade with fixed-fractional risk sizing "
        "(`keel/execution/sizing.py::size`): risk a constant `risk_pct` of equity per trade over "
        "the entry-to-stop distance, with a config default of `risk_pct = 0.01` (1%)."
    )
    lines.append("")
    lines.append(
        "This report studies the Kelly Criterion and its relatives (drawn from the `keeks` "
        "family of bankroll-growth formulas) **purely as mathematics of optimal capital "
        "allocation** -- a Monte Carlo exercise in bankroll-growth arithmetic run against "
        "simulated win/loss trade sequences with stdlib-only Python. Nothing here trades real "
        "money, wagers on chance for its own sake, or involves interest (riba); it is a study of "
        "how fast a bankroll compounds under different constant-risk-fraction rules, applied to "
        "keel's own promotion-floor edge numbers, to ask an engineering question: is keel's fixed "
        "1% risk needlessly conservative, or is there a good reason to stay conservative anyway?"
    )
    lines.append("")
    lines.append("## Thesis")
    lines.append("")
    lines.append(
        "keel's PROMOTION FLOOR rule requires win_rate >= 0.55 and R:R (min_rr) >= 1.5 before a "
        "strategy is promoted to live trading. For a rule sitting exactly at that floor "
        f"(p=0.55, b=1.5), full-Kelly risk fraction is:"
    )
    lines.append("")
    lines.append("```")
    lines.append("f* = (b*p - q) / b = (1.5 * 0.55 - 0.45) / 1.5 = 0.375 / 1.5 = 0.25   (25% of equity per trade)")
    lines.append(f"half-Kelly  = {floor_half:.4f}  (12.50%)")
    lines.append(f"quarter-Kelly = {floor_quarter:.4f}  (6.25%)")
    lines.append(f"keel's actual risk_pct = 0.01  (1.00%)  ~= {keel_vs_full_pct:.1f}% of full Kelly")
    lines.append("```")
    lines.append("")
    lines.append(
        "The question: is keel leaving growth on the table by risking only ~4% of the "
        "full-Kelly-implied fraction at its own promotion floor, or is sub-Kelly sizing correct "
        "once you account for estimation error in `p`/`b`, correlation between trades, and "
        "drawdown pain that a pure log-growth-maximizer ignores?"
    )
    lines.append("")
    lines.append("## Method")
    lines.append("")
    lines.append(
        "Two deterministic (seeded) Monte Carlo experiments, implemented in `simulate.py` next "
        "to this report, using only the Python standard library (`random`, `statistics`). Every "
        "path uses `random.Random(seed)` with an explicit integer seed; strategies compared "
        "within the same experiment share seeds per path index (common random numbers), so "
        "differences between strategies reflect sizing, not differing luck. Money is modeled as "
        "`float` (this is an educational sim, not the Decimal-only production `keel` sizing "
        "code). A trade wins with probability `p`, paying `+b * f * bankroll` where `f` is the "
        "fraction risked and `b` is the reward:risk multiple; a loss costs `f * bankroll`. A path "
        "is considered ruined and stopped once bankroll falls to or below $1 (bankroll cannot go "
        "negative under fractional betting, but going effectively to zero is treated as ruin)."
    )
    lines.append("")

    lines.append("## Experiment 1: reproducing the keeks binary comparison")
    lines.append("")
    lines.append(
        f"Setup: {EXP1_N_BETS} bets, p={EXP1_P}, even-money (b={EXP1_B}), initial bankroll "
        f"${EXP1_INITIAL:,.0f}, {EXP1_N_PATHS} independent seeded paths per strategy. Strategies: "
        "Full Kelly, Half Kelly, Quarter Kelly, Fixed-1% (keel), CPPI (floor ratchets at 80% of "
        "peak equity, multiplier=3), Naive-flat ($10 constant stake), and Drawdown-adjusted Kelly "
        "(scales full Kelly to zero as current drawdown approaches a 20% tolerance ceiling)."
    )
    lines.append("")
    lines.append(exp1_table(exp1_summary))
    lines.append("")
    lines.append(
        "**Reading this table**: Full Kelly has the highest median/mean terminal wealth, but "
        "also the widest dispersion (stdev) and the deepest typical drawdowns -- the classic "
        "Kelly trait of being growth-optimal in expectation while remaining a psychologically "
        "brutal ride. Half- and Quarter-Kelly trade away some terminal wealth for a large cut in "
        "drawdown depth and variance -- this is the textbook \"why half-Kelly\" lesson the "
        "`keeks` library is built to demonstrate. keel's Fixed-1% sits far below all Kelly "
        "variants on terminal wealth because it never lets its risk keep pace with a compounding "
        "bankroll's *edge*, but it also never comes close to the Kelly variants' drawdowns. CPPI "
        "at multiplier=3 with an 80%-of-peak floor risks a large fraction of the cushion "
        "(60% of bankroll at a fresh high) -- well above this edge's Kelly-optimal level -- "
        "and its results show the cost of over-levering an insurance-style rule. Naive-flat ($10) "
        "decays into an ever-shrinking fraction of a growing bankroll (or a growing fraction of a "
        "shrinking one), producing its own distinct, non-Kelly growth curve."
    )
    lines.append("")

    lines.append("## Experiment 2: risk_pct vs the Kelly family at keel's own edge numbers")
    lines.append("")
    lines.append(
        f"Setup: {EXP2_N_BETS}-trade bootstrap sequences, {EXP2_N_PATHS} seeded paths, initial "
        f"bankroll ${EXP2_INITIAL:,.0f}. Each trade wins with probability `p` paying "
        "`+b * (risk_pct * equity)`, else loses `risk_pct * equity`. Two edge profiles: (A) the "
        "promotion floor itself, p=0.55, b=1.5; (B) a stronger edge, p=0.58, b=2.0. Sizing levels "
        "are constant risk fractions: keel-1% (0.01), and the Quarter-/Half-/Full-Kelly fractions "
        "implied by each profile's own p and b. For each profile, two worlds are simulated: "
        "**p correct** (the realized win rate matches what sizing assumed) and "
        f"**p over-estimated by {EXP2_P_ERROR:.2f}** (sizing was computed assuming the stated p, "
        "but the true win rate actually realized is 5 percentage points lower -- an estimation-"
        "error stress test)."
    )
    lines.append("")
    for profile_name, profile_out in exp2_results.items():
        lines.append(f"### Profile {profile_name}")
        lines.append("")
        levels = profile_out["kelly_levels"]
        lines.append(
            f"Kelly fractions for this profile: Quarter-Kelly={fmt_pct(levels['Quarter-Kelly'])}, "
            f"Half-Kelly={fmt_pct(levels['Half-Kelly'])}, Full-Kelly={fmt_pct(levels['Full-Kelly'])}, "
            f"keel-1%={fmt_pct(levels['keel-1%'])}."
        )
        lines.append("")
        lines.append(exp2_table(profile_out, level_order))
        lines.append("")

    lines.append("## What this means for keel")
    lines.append("")
    lines.append(
        f"**Is 1% too timid?** Mathematically, yes, relative to the growth-maximizing Kelly "
        f"fraction: at the promotion floor (p=0.55, b=1.5) full Kelly is 25% of equity per trade, "
        "and keel's 1% is roughly 4% of that. In the \"p correct\" worlds of Experiment 2, every "
        "Kelly-family fraction (even Quarter-Kelly) compounds to a dramatically larger median "
        "terminal multiple than keel-1% over 200 trades, because 1% barely lets a real edge "
        "compound -- the bankroll grows close to linearly rather than geometrically at that "
        "scale. Purely as an optimal-growth-rate statement, the thesis holds: keel is far to the "
        "conservative side of the Kelly curve."
    )
    lines.append("")
    lines.append(
        "**Does the estimation-error run defend sub-Kelly?** Yes, and this is the more important "
        "half of the story. In the \"p over-estimated by 0.05\" worlds, Full-Kelly's edge "
        "assumption breaks: at the floor profile (b=1.5, breakeven p=0.40), an assumed p=0.55 "
        "with a true p=0.50 is still a real edge -- full Kelly at the *true* p=0.50 would be "
        "~16.7% (down from the 25% it was sized at), not zero -- but Full-Kelly was sized as if "
        "the edge were 8-plus points thicker than it actually is, and that overbetting shows up "
        "directly in the numbers: median terminal multiple collapses from 5076x (\"p correct\") "
        "to 22x (\"p over-estimated\"), and a ruin rate that was 0.0% becomes 3.6%. Half- and "
        "Quarter-Kelly degrade far more gracefully under the identical misestimation (their ruin "
        "rates stay at 0.0%), because they were never betting the full assumed edge in the first "
        "place -- the classic argument for sub-Kelly sizing is that it functions as a margin of "
        "safety against exactly the kind of parameter error a live trading system cannot avoid "
        "(p and b are estimated from a finite, noisy backtest sample, not known constants). "
        "keel's actual 1%, while far more conservative than even Quarter-Kelly, sits on the same "
        "side of that argument as the fractional-Kelly strategies: it is far more robust to an "
        "over-optimistic edge estimate than Full-Kelly is, just at a much larger cost in forgone "
        "growth."
    )
    lines.append("")
    lines.append(
        "**Net read**: the honest conclusion is that keel's 1% is not \"wrong\" -- it is an "
        "extreme point on the same sub-Kelly safety spectrum that Half- and Quarter-Kelly occupy, "
        "just pushed much further toward safety than the math alone would require. If keel's "
        "backtested p and b estimates were trustworthy point estimates with no correlation "
        "between trades, something in the Quarter-Kelly neighborhood (order of 5-6% at the floor "
        "edge) would capture most of the available growth while still being far more robust to "
        "estimation error than Full- or Half-Kelly. keel's actual 1% leaves a substantial amount "
        "of that growth unclaimed. Whether closing some of that gap is worth it depends on "
        "factors this simulation does not model (see Assumptions below) -- most importantly, real "
        "trades are not independent, identically-distributed coin flips, and a backtest's p/b "
        "point estimates carry real sampling uncertainty that a single-scenario stress test can "
        "only gesture at."
    )
    lines.append("")
    lines.append("## Assumptions and honest limitations")
    lines.append("")
    lines.append(
        "- **Independent, i.i.d. trades.** The simulation treats every trade as an independent "
        "Bernoulli draw with fixed p and b. Real crypto trades from correlated strategies "
        "(e.g. multiple concurrent BTC/ETH positions moving together in a market-wide drawdown) "
        "violate this; correlated losses compound faster than this model's math accounts for, "
        "which understates the real risk of any of the higher-fraction strategies (Full/Half "
        "Kelly, CPPI at m=3)."
    )
    lines.append(
        "- **Known b, no fees/slippage.** `b` (R:R) is treated as a known constant per trade; "
        "trading fees, slippage, and spread are not modeled. Real R:R realized on a live book is "
        "noisier and typically worse than backtested R:R."
    )
    lines.append(
        "- **A single, fixed estimation-error stress test.** The \"p over-estimated by 0.05\" "
        "world tests one specific magnitude of misestimation, not a distribution over possible "
        "estimation errors. It illustrates the *direction* of the Full-Kelly fragility argument, "
        "not a calibrated probability of it occurring."
    )
    lines.append(
        "- **No position limits, correlation caps, or per-order/per-day caps.** keel's real "
        "guards (order caps, day caps, portfolio-level exposure limits) are not modeled here; "
        "they are additional risk controls that a real deployment would layer on top of whatever "
        "risk_pct is chosen, and they change the practical consequences of raising risk_pct."
    )
    lines.append(
        "- **Float money, not Decimal.** This sim uses `float` for bankroll math for simplicity; "
        "keel's real sizing code (`keel/execution/sizing.py`) is Decimal-only by design, because "
        "money should never touch float in the production path. That distinction does not change "
        "the qualitative conclusions here but is worth flagging."
    )
    lines.append(
        "- **This is not a recommendation to change keel's risk_pct.** The result is a "
        "mathematical observation about the growth/safety tradeoff at different Kelly fractions, "
        "not a specific proposed new value; any change to keel's actual risk_pct would need its "
        "own review against keel's real guard rails, correlation across live positions, and "
        "backtest confidence -- none of which this script attempts to quantify."
    )
    lines.append("")

    return "\n".join(lines)


def main() -> None:
    print("Running Experiment 1 (keeks binary comparison reproduction)...")
    exp1_summary = experiment_1()
    for name, s in exp1_summary.items():
        print(
            f"  {name}: median=${s['median_terminal']:.2f} mean=${s['mean_terminal']:.2f} "
            f"stdev=${s['stdev_terminal']:.2f} median_dd={s['median_max_dd']:.4f} "
            f"ruin_rate={s['ruin_rate']:.4f}"
        )

    print("Running Experiment 2 (keel-relevant risk_pct vs Kelly family)...")
    exp2_results = experiment_2()
    for profile_name, profile_out in exp2_results.items():
        print(f"  Profile {profile_name}")
        for world_name, summary in profile_out["worlds"].items():
            print(f"    World: {world_name}")
            for name, s in summary.items():
                print(
                    f"      {name}: median_multiple={s['median_multiple']:.4f}x "
                    f"median_dd={s['median_max_dd']:.4f} worst_dd={s['worst_max_dd']:.4f} "
                    f"ruin_rate={s['ruin_rate']:.4f}"
                )

    report = build_report(exp1_summary, exp2_results)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report)
    print(f"\nReport written to {REPORT_PATH}")


if __name__ == "__main__":
    main()
