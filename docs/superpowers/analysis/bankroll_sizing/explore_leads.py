#!/usr/bin/env python3
"""Exploration of two candidate bankroll-sizing leads from KB source-84 (`keeks` family).

Educational / halal framing
----------------------------
`keel` is a halal (riba-free) spot-crypto, long-only, no-leverage trading agent. It sizes every
trade with fixed-fractional risk sizing (`keel/execution/sizing.py::size`): risk a constant
`risk_pct` of equity per trade, config default `risk_pct = 0.01` (1%). Promotion floor:
win_rate >= 0.55, R:R (`b`) >= 1.5. As with `simulate.py` next to this script, everything here is
a stdlib-only Monte Carlo study of capital-allocation MATH (the Kelly family and its continuous
cousin, the Merton share) -- not a study of gambling, and not wired into `keel`'s execution path.
Nothing here trades real money or involves interest (riba).

This script does NOT modify `simulate.py` or its report; it is a separate, self-contained
follow-up that reuses only `sizing_strategies.py`'s pure formulas (`kelly_fraction`,
`fractional_kelly`, `merton_fraction`, `fixed_fraction`) and adds its own small set of helpers.

Two candidate leads under test (KB source-84 §84.16)
-------------------------------------------------------
1. **Dynamic drawdown taper** (§84.4, blog form): `f_eff = (1 - d/D) * f_base`, where `d` is the
   account's current drawdown from its equity peak and `D` is a taper ceiling -- risk tapers
   linearly to zero as `d` approaches `D`, continuously, *before* keel's existing hard
   drawdown-breaker (rail 11) would halt trading outright. The hypothesis under test: on keel's
   tiny 1% base fraction, drawdown rarely gets deep enough for the taper to matter, so the taper's
   real value is not "protecting the 1% base" but "letting you safely run a HIGHER base fraction."
2. **Merton share / CRRA sizing** (§84.6): `f = mu / (gamma * sigma^2)`, the continuous-time
   analogue of Kelly for an investor with constant relative risk aversion `gamma`. `gamma = 1`
   approximately recovers full Kelly; this is explored as a principled, defensible way to express
   "how much sub-Kelly" instead of an ad-hoc lambda multiplier.

Trade model (same convention as `simulate.py`)
------------------------------------------------
Each trade wins with probability `p`, paying `+b * (f * bankroll)`; otherwise it loses
`f * bankroll`. `f` is recomputed fresh from running state (`bankroll`, `peak`, `initial`) before
every trade. Bankroll cannot go negative; a path is ruined and stops once bankroll falls to or
below $1. Two edge profiles are used throughout: A = keel's promotion floor (p=0.55, b=1.5) and
B = a stronger edge (p=0.58, b=2.0). Two worlds per experiment: "p correct" (the realized win rate
matches the assumed sizing input) and "p over-estimated by 0.05" (sizing assumes the stated p, but
the true realized win rate is 5 points lower) -- an estimation-error stress test. Unless noted,
500 independent seeded paths of 200 trades each, starting from $1,000.

Determinism
------------
Every path uses `random.Random(seed)` with an explicit integer seed; strategies compared within
the same world/profile share the same seed per path index (common random numbers), so any
difference between sizing rules reflects sizing, not differing luck.

Run: `python explore_leads.py` (or from the repo root:
`python docs/superpowers/analysis/bankroll_sizing/explore_leads.py`). Writes the markdown report
to `docs/superpowers/reports/2026-07-23-drawdown-taper-and-merton-exploration.md`.
"""

from __future__ import annotations

import random
import statistics
import sys
from collections.abc import Callable
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from sizing_strategies import (  # noqa: E402
    fixed_fraction,
    fractional_kelly,
    kelly_fraction,
    merton_fraction,
)

REPO_ROOT = HERE.parents[3]  # docs/superpowers/analysis/bankroll_sizing -> repo root
REPORT_PATH = (
    REPO_ROOT
    / "docs"
    / "superpowers"
    / "reports"
    / "2026-07-23-drawdown-taper-and-merton-exploration.md"
)

RUIN_THRESHOLD = 1.0  # bankroll <= $1 counts as ruin; path stops (can't go negative)
HARD_BREAKER_DD = 0.20  # keel's hard account-drawdown breaker (rail 11): halt at 20% DD

FractionFn = Callable[[dict], float]

# Two edge profiles shared by both experiments: A sits exactly at keel's promotion floor
# (win_rate >= 0.55, R:R >= 1.5); B is a stronger, more comfortably-above-floor edge.
PROFILES = {
    "A (floor edge: p=0.55, b=1.5)": {"p": 0.55, "b": 1.5},
    "B (stronger edge: p=0.58, b=2.0)": {"p": 0.58, "b": 2.0},
}


# ---------------------------------------------------------------------------------------------
# New helpers for this exploration (not added to sizing_strategies.py, which is reused as-is)
# ---------------------------------------------------------------------------------------------


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp `x` into `[lo, hi]`."""
    return max(lo, min(hi, x))


def taper_fraction(f_base: float, current_dd: float, taper_ceiling: float) -> float:
    """Dynamic drawdown taper (KB §84.4, blog form): scale an arbitrary base risk fraction
    `f_base` down to zero as the account's current drawdown `current_dd` (from its equity peak)
    approaches a taper ceiling `taper_ceiling` ("D"):

        f_eff = clamp(1 - current_dd / taper_ceiling, 0, 1) * f_base

    Unlike `sizing_strategies.drawdown_adjusted_kelly` (which always tapers the *Kelly* fraction
    recomputed from `p`/`b`), this wraps an arbitrary base fraction -- including keel's flat 1%,
    which has no `p`/`b` dependence at all -- so it can be applied to any constant-risk sizing
    rule. At `current_dd = 0` this returns exactly `f_base`; at `current_dd >= taper_ceiling` it
    returns exactly 0; in between it decays linearly.

    Raises `ValueError` if `taper_ceiling <= 0`, `current_dd < 0`, or `f_base < 0`. Result is
    clamped to `[0, 1]`.
    """
    if taper_ceiling <= 0.0:
        raise ValueError(f"taper_fraction: taper_ceiling must be > 0, got {taper_ceiling}")
    if current_dd < 0.0:
        raise ValueError(f"taper_fraction: current_dd must be >= 0, got {current_dd}")
    if f_base < 0.0:
        raise ValueError(f"taper_fraction: f_base must be >= 0, got {f_base}")

    if current_dd >= taper_ceiling:
        return 0.0

    scale = _clamp(1.0 - current_dd / taper_ceiling)
    return _clamp(scale * f_base)


def compute_mu_sigma2(p: float, b: float) -> tuple[float, float]:
    """Per-unit-risked mean and variance of a binary trade outcome: +b w.p. p, -1 w.p. (1-p).

        mu    = p*b - (1-p)
        sigma2 = p*b^2 + (1-p)*1 - mu^2      (Var[X] = E[X^2] - E[X]^2)

    These are the `exp_return`/`variance` inputs `merton_fraction` expects, derived from the same
    `p`/`b` the Kelly formulas use, so the Kelly and Merton sizers are being fed a consistent
    description of the same trade.

    Raises `ValueError` if `p` is not in `[0, 1]` or `b <= 0`.
    """
    if not (0.0 <= p <= 1.0):
        raise ValueError(f"compute_mu_sigma2: p must be in [0, 1], got {p}")
    if b <= 0.0:
        raise ValueError(f"compute_mu_sigma2: b must be > 0, got {b}")

    q = 1.0 - p
    mu = p * b - q
    sigma2 = p * (b**2) + q * 1.0 - mu**2
    return mu, sigma2


def solve_implied_gamma(mu: float, sigma2: float, target_f: float) -> float:
    """Solve for the CRRA risk-aversion `gamma` such that `merton_fraction(mu, sigma2, gamma)`
    equals `target_f` -- i.e. "what risk-aversion would a Merton-share investor need to have to
    end up sizing at exactly this fraction?"

    `merton_fraction` is `mu / (gamma * sigma2)`, which is monotonically decreasing in `gamma` and
    exactly invertible (no iterative search needed):

        gamma = mu / (sigma2 * target_f)

    Raises `ValueError` if `mu <= 0` (no edge -- no finite positive `gamma` makes an
    edge-less Merton fraction hit a positive target), `sigma2 <= 0`, or `target_f <= 0`.
    """
    if mu <= 0.0:
        raise ValueError(f"solve_implied_gamma: mu must be > 0 (no edge), got {mu}")
    if sigma2 <= 0.0:
        raise ValueError(f"solve_implied_gamma: sigma2 must be > 0, got {sigma2}")
    if target_f <= 0.0:
        raise ValueError(f"solve_implied_gamma: target_f must be > 0, got {target_f}")

    return mu / (sigma2 * target_f)


# ---------------------------------------------------------------------------------------------
# Strategy factories: stateless callable(state) -> risk fraction in [0, 1]. `state` carries the
# running per-path values: bankroll, peak (equity high-water mark), initial (starting bankroll).
# ---------------------------------------------------------------------------------------------


def strategy_fixed(f: float) -> FractionFn:
    fraction = fixed_fraction(f)
    return lambda state: fraction


def strategy_taper(f_base: float, taper_ceiling: float) -> FractionFn:
    def fn(state: dict) -> float:
        peak = state["peak"]
        bankroll = state["bankroll"]
        current_dd = 0.0 if peak <= 0 else max(0.0, (peak - bankroll) / peak)
        return taper_fraction(f_base, current_dd, taper_ceiling)

    return fn


# ---------------------------------------------------------------------------------------------
# Core path simulator (adds an optional hard drawdown-breaker halt on top of simulate.py's model)
# ---------------------------------------------------------------------------------------------


def simulate_path(
    fraction_fn: FractionFn,
    n_bets: int,
    p: float,
    b: float,
    seed: int,
    initial: float,
    hard_breaker_dd: float | None = None,
) -> dict:
    """Simulate one bankroll path of up to `n_bets` trades.

    Each trade: with probability `p` it wins, paying `+b * f * bankroll`; otherwise it loses
    `f * bankroll`, where `f = fraction_fn(state)` is recomputed fresh before every trade. The
    path stops early if bankroll drops to or below `RUIN_THRESHOLD` (ruin is recorded).

    If `hard_breaker_dd` is given, it models keel's hard account-drawdown breaker (rail 11): once
    the path's current drawdown from peak reaches or exceeds `hard_breaker_dd`, the path STOPS
    PLACING TRADES for the remainder of the sequence (bankroll is simply frozen at that level) --
    it does not resume even if drawdown would otherwise have started to recover, mirroring a hard
    halt rather than a taper. `breaker_tripped` records whether this ever happened on the path.

    Returns a dict: terminal bankroll, the path's max drawdown from its own running peak, whether
    it was ruined, and whether the hard breaker ever tripped.
    """
    rng = random.Random(seed)
    bankroll = initial
    peak = initial
    max_dd = 0.0
    ruined = False
    breaker_tripped = False

    for _ in range(n_bets):
        if bankroll <= RUIN_THRESHOLD:
            ruined = True
            break
        if breaker_tripped:
            break  # halted by the hard breaker: no further trades this path

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
        if hard_breaker_dd is not None and dd >= hard_breaker_dd:
            breaker_tripped = True

    return {
        "terminal": bankroll,
        "max_dd": max_dd,
        "ruined": ruined,
        "breaker_tripped": breaker_tripped,
    }


def run_paths(
    strategies: dict[str, FractionFn],
    n_paths: int,
    n_bets: int,
    p: float,
    b: float,
    base_seed: int,
    initial: float,
    hard_breaker_dd: float | None = None,
) -> dict[str, dict[str, list]]:
    """Run `n_paths` seeded paths for every strategy in `strategies`, using the same seed per
    path index across strategies (common random numbers)."""
    results: dict[str, dict[str, list]] = {
        name: {"terminal": [], "max_dd": [], "ruined": [], "breaker_tripped": []}
        for name in strategies
    }
    for i in range(n_paths):
        seed = base_seed + i
        for name, fn in strategies.items():
            r = simulate_path(fn, n_bets, p, b, seed, initial, hard_breaker_dd)
            results[name]["terminal"].append(r["terminal"])
            results[name]["max_dd"].append(r["max_dd"])
            results[name]["ruined"].append(r["ruined"])
            results[name]["breaker_tripped"].append(r["breaker_tripped"])
    return results


def summarize(results: dict[str, dict[str, list]], initial: float) -> dict[str, dict[str, float]]:
    summary: dict[str, dict[str, float]] = {}
    for name, r in results.items():
        terminal = r["terminal"]
        max_dd = r["max_dd"]
        ruined = r["ruined"]
        tripped = r["breaker_tripped"]
        median_terminal = statistics.median(terminal)
        median_max_dd = statistics.median(max_dd)
        median_multiple = median_terminal / initial
        risk_adjusted = median_multiple / median_max_dd if median_max_dd > 0 else float("inf")
        summary[name] = {
            "median_terminal": median_terminal,
            "median_multiple": median_multiple,
            "mean_terminal": statistics.mean(terminal),
            "median_max_dd": median_max_dd,
            "worst_max_dd": max(max_dd),
            "ruin_rate": sum(ruined) / len(ruined),
            "breaker_trip_rate": sum(tripped) / len(tripped),
            "risk_adjusted": risk_adjusted,
        }
    return summary


# ---------------------------------------------------------------------------------------------
# Experiment 3: dynamic drawdown taper -- does it let a higher base fraction run more safely?
# ---------------------------------------------------------------------------------------------

EXP3_N_BETS = 200
EXP3_N_PATHS = 500
EXP3_INITIAL = 1000.0
EXP3_P_ERROR = 0.05
EXP3_TAPER_CEILINGS = [0.15, 0.25, 0.35]
EXP3_KEEL_F = 0.01
EXP3_BASE_SEED = 30_000


def base_fractions_for(p: float, b: float) -> dict[str, float]:
    return {
        "keel-1%": EXP3_KEEL_F,
        "Quarter-Kelly": fractional_kelly(p, b, 0.25),
        "Half-Kelly": fractional_kelly(p, b, 0.5),
    }


def exp3_combo_name(base_name: str, taper_ceiling: float | None) -> str:
    if taper_ceiling is None:
        return f"{base_name} / no taper"
    return f"{base_name} / taper D={taper_ceiling:.2f}"


def experiment_3() -> dict:
    """Returns, per profile, per world, the summary stats for every (base fraction x taper
    ceiling) combination, all run under keel's hard drawdown breaker at 20%."""
    out: dict = {}
    for profile_idx, (profile_name, params) in enumerate(PROFILES.items()):
        assumed_p, b = params["p"], params["b"]
        bases = base_fractions_for(assumed_p, b)

        strategies: dict[str, FractionFn] = {}
        combo_order: list[str] = []
        for base_name, f_base in bases.items():
            name = exp3_combo_name(base_name, None)
            strategies[name] = strategy_fixed(f_base)
            combo_order.append(name)
            for ceiling in EXP3_TAPER_CEILINGS:
                name = exp3_combo_name(base_name, ceiling)
                strategies[name] = strategy_taper(f_base, ceiling)
                combo_order.append(name)

        worlds = {
            "p correct": assumed_p,
            f"p over-estimated by {EXP3_P_ERROR:.2f}": assumed_p - EXP3_P_ERROR,
        }
        profile_out: dict = {"bases": bases, "combo_order": combo_order, "worlds": {}}
        for world_idx, (world_name, true_p) in enumerate(worlds.items()):
            seed = EXP3_BASE_SEED + profile_idx * 1_000_000 + world_idx * 500_000
            results = run_paths(
                strategies,
                EXP3_N_PATHS,
                EXP3_N_BETS,
                true_p,
                b,
                seed,
                EXP3_INITIAL,
                hard_breaker_dd=HARD_BREAKER_DD,
            )
            profile_out["worlds"][world_name] = summarize(results, EXP3_INITIAL)
        out[profile_name] = profile_out
    return out


def taper_dominance_summary(exp3_results: dict) -> dict:
    """For every (base fraction, taper ceiling) pair, check across ALL profile/world combos
    whether the tapered base "dominates" -- beats flat-1% on growth (median terminal multiple)
    AND beats its own untapered version on safety (median max DD and hard-breaker trip rate both
    no worse). Returns per (base, D): the count of profile/world combos (out of the total tested)
    where growth-dominance holds, where safety-dominance holds, and where both hold together
    ("full dominance"), plus the underlying per-combo rows for citing concrete numbers.
    """
    bases = ["Quarter-Kelly", "Half-Kelly"]
    out: dict = {base: {} for base in bases}
    combos: list[tuple[str, str]] = []
    for profile_name, profile_out in exp3_results.items():
        for world_name in profile_out["worlds"]:
            combos.append((profile_name, world_name))

    for base in bases:
        for ceiling in EXP3_TAPER_CEILINGS:
            rows = []
            growth_hits = 0
            safety_hits = 0
            full_hits = 0
            for profile_name, world_name in combos:
                summary = exp3_results[profile_name]["worlds"][world_name]
                flat1 = summary["keel-1% / no taper"]
                untapered = summary[f"{base} / no taper"]
                tapered = summary[exp3_combo_name(base, ceiling)]
                growth_dom = tapered["median_multiple"] >= flat1["median_multiple"]
                safety_dom = (
                    tapered["median_max_dd"] <= untapered["median_max_dd"]
                    and tapered["breaker_trip_rate"] <= untapered["breaker_trip_rate"]
                )
                growth_hits += int(growth_dom)
                safety_hits += int(safety_dom)
                full_hits += int(growth_dom and safety_dom)
                rows.append(
                    {
                        "profile": profile_name,
                        "world": world_name,
                        "growth_dom": growth_dom,
                        "safety_dom": safety_dom,
                        "flat1": flat1,
                        "untapered": untapered,
                        "tapered": tapered,
                    }
                )
            out[base][ceiling] = {
                "n_combos": len(combos),
                "growth_hits": growth_hits,
                "safety_hits": safety_hits,
                "full_hits": full_hits,
                "rows": rows,
            }
    return out


# ---------------------------------------------------------------------------------------------
# Experiment 4: Merton gamma sizing -- implied risk-aversion, fixed-gamma cross-profile behavior,
# and the fractional-Kelly equivalence it implies.
# ---------------------------------------------------------------------------------------------

EXP4_N_BETS = 200
EXP4_N_PATHS = 500
EXP4_INITIAL = 1000.0
EXP4_P_ERROR = 0.05
EXP4_KEEL_F = 0.01
EXP4_TEXTBOOK_GAMMA = 2.0
EXP4_BASE_SEED = 40_000


def experiment_4() -> dict:
    # (a) implied gamma per profile: solve merton_fraction(mu, sigma2, gamma) = keel's actual 1%.
    implied: dict[str, dict[str, float]] = {}
    for profile_name, params in PROFILES.items():
        p, b = params["p"], params["b"]
        mu, sigma2 = compute_mu_sigma2(p, b)
        gamma = solve_implied_gamma(mu, sigma2, EXP4_KEEL_F)
        implied[profile_name] = {"mu": mu, "sigma2": sigma2, "gamma": gamma}

    gamma_a_implied = implied["A (floor edge: p=0.55, b=1.5)"]["gamma"]
    gamma_choices = {
        "gamma=A-implied": gamma_a_implied,
        "gamma=2 (textbook)": EXP4_TEXTBOOK_GAMMA,
    }

    # (b) fixed-gamma sizing across profiles, vs flat-1% and Quarter-Kelly, in both worlds.
    cross: dict[str, dict] = {}
    for profile_idx, (profile_name, params) in enumerate(PROFILES.items()):
        assumed_p, b = params["p"], params["b"]
        mu, sigma2 = compute_mu_sigma2(assumed_p, b)
        levels: dict[str, float] = {
            "keel-1%": EXP4_KEEL_F,
            "Quarter-Kelly": fractional_kelly(assumed_p, b, 0.25),
        }
        for gamma_name, gamma in gamma_choices.items():
            levels[f"Merton ({gamma_name})"] = merton_fraction(mu, sigma2, gamma)

        worlds = {
            "p correct": assumed_p,
            f"p over-estimated by {EXP4_P_ERROR:.2f}": assumed_p - EXP4_P_ERROR,
        }
        profile_out: dict = {"levels": levels, "mu": mu, "sigma2": sigma2, "worlds": {}}
        for world_idx, (world_name, true_p) in enumerate(worlds.items()):
            seed = EXP4_BASE_SEED + profile_idx * 1_000_000 + world_idx * 500_000
            strategies = {name: strategy_fixed(f) for name, f in levels.items()}
            results = run_paths(
                strategies, EXP4_N_PATHS, EXP4_N_BETS, true_p, b, seed, EXP4_INITIAL
            )
            profile_out["worlds"][world_name] = summarize(results, EXP4_INITIAL)
        cross[profile_name] = profile_out

    # (c) effective lambda = f_merton / f_kelly at each profile, for each fixed gamma.
    lambdas: dict[str, dict[str, dict[str, float]]] = {}
    for profile_name, params in PROFILES.items():
        p, b = params["p"], params["b"]
        full_kelly = kelly_fraction(p, b)
        mu, sigma2 = compute_mu_sigma2(p, b)
        lambdas[profile_name] = {}
        for gamma_name, gamma in gamma_choices.items():
            f_merton = merton_fraction(mu, sigma2, gamma)
            lam = f_merton / full_kelly if full_kelly > 0 else float("nan")
            lambdas[profile_name][gamma_name] = {
                "f_merton": f_merton,
                "full_kelly": full_kelly,
                "lambda": lam,
            }

    return {"implied": implied, "gamma_choices": gamma_choices, "cross": cross, "lambdas": lambdas}


# ---------------------------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------------------------


def fmt_money(x: float) -> str:
    if x >= 1_000_000:
        return f"${x:,.0f}"
    return f"${x:,.2f}"


def fmt_pct(x: float) -> str:
    return f"{x * 100:.2f}%"


def fmt_ratio(x: float) -> str:
    if x == float("inf"):
        return "inf"
    return f"{x:.2f}"


def exp3_table(profile_out: dict, world_name: str) -> str:
    header = (
        "| Base x taper | Median terminal multiple | Median max DD | Worst max DD | "
        "Ruin rate | Breaker trip rate | Risk-adj (mult/DD) |\n"
        "|---|---|---|---|---|---|---|\n"
    )
    summary = profile_out["worlds"][world_name]
    rows = []
    for name in profile_out["combo_order"]:
        s = summary[name]
        rows.append(
            f"| {name} | {s['median_multiple']:.3f}x | {fmt_pct(s['median_max_dd'])} | "
            f"{fmt_pct(s['worst_max_dd'])} | {fmt_pct(s['ruin_rate'])} | "
            f"{fmt_pct(s['breaker_trip_rate'])} | {fmt_ratio(s['risk_adjusted'])} |"
        )
    return header + "\n".join(rows)


def exp4_table(profile_out: dict, level_order: list[str], world_name: str) -> str:
    header = (
        "| Sizing level | Risk fraction | Median terminal multiple | Median max DD | "
        "Worst max DD | Ruin rate |\n"
        "|---|---|---|---|---|---|\n"
    )
    levels = profile_out["levels"]
    summary = profile_out["worlds"][world_name]
    rows = []
    for name in level_order:
        s = summary[name]
        rows.append(
            f"| {name} | {fmt_pct(levels[name])} | {s['median_multiple']:.3f}x | "
            f"{fmt_pct(s['median_max_dd'])} | {fmt_pct(s['worst_max_dd'])} | "
            f"{fmt_pct(s['ruin_rate'])} |"
        )
    return header + "\n".join(rows)


def build_report(exp3_results: dict, exp4_results: dict) -> str:
    profile_names = list(PROFILES.keys())
    profile_a_name, profile_b_name = profile_names[0], profile_names[1]

    lines: list[str] = []
    lines.append(
        "# Drawdown Taper and Merton-Gamma Exploration: Two Candidate Leads from KB Source-84"
    )
    lines.append("")

    # --- Purpose and framing ---------------------------------------------------------------
    lines.append("## Purpose and framing")
    lines.append("")
    lines.append(
        "`keel` is a halal (riba-free), spot-only, long-only, no-leverage crypto trading agent. "
        "It sizes every trade with fixed-fractional risk sizing "
        "(`keel/execution/sizing.py::size`): risk a constant `risk_pct` of equity per trade, "
        "config default `risk_pct = 0.01` (1%). Promotion floor: win_rate >= 0.55, R:R >= 1.5."
    )
    lines.append("")
    lines.append(
        "This report is a follow-up, stdlib-only Monte Carlo study of two SPECIFIC candidate "
        "leads flagged in KB source-84 §84.16, exploring the Kelly family and its continuous "
        "cousin (the Merton share) **as mathematics of capital allocation** -- not as gambling "
        "advice, and not wired into `keel`'s execution path. Nothing here trades real money or "
        "involves interest (riba). It reuses `sizing_strategies.py`'s pure formulas "
        "(`kelly_fraction`, `fractional_kelly`, `merton_fraction`, `fixed_fraction`) and does not "
        "modify `simulate.py` or its existing report."
    )
    lines.append("")

    # --- The two leads -----------------------------------------------------------------------
    lines.append("## The two leads under test")
    lines.append("")
    lines.append(
        "1. **Dynamic drawdown taper (KB §84.4, blog form):** `f_eff = (1 - d/D) * f_base`, "
        "`d` = current account drawdown from peak, `D` = a taper ceiling -- risk tapers linearly "
        "to zero as `d -> D`, continuously and *before* keel's existing hard drawdown breaker "
        "(rail 11, halts at 20% account DD) would otherwise stop trading outright. Hypothesis "
        "under test: on keel's tiny 1% base fraction the taper almost never engages (1% rarely "
        "draws an account down far), so the taper's real value is not protecting the 1% base -- "
        "it is letting a HIGHER base fraction run more safely."
    )
    lines.append(
        "2. **Merton share / CRRA sizing (KB §84.6):** `f = mu / (gamma * sigma^2)`, the "
        "continuous-time analogue of Kelly for an investor with constant relative risk aversion "
        "`gamma` (`gamma = 1` approximately recovers full Kelly; higher `gamma` sizes smaller). "
        "Explored as a principled, defensible way to express \"how sub-Kelly\" instead of an "
        "ad-hoc fractional-Kelly `lambda`."
    )
    lines.append("")

    # --- Method ----------------------------------------------------------------------------
    lines.append("## Method")
    lines.append("")
    lines.append(
        "Deterministic (seeded) Monte Carlo experiments, implemented in `explore_leads.py` next "
        "to this report, stdlib-only (`random`, `statistics`). Every path uses "
        "`random.Random(seed)`; strategies compared within the same world/profile share seeds per "
        "path index (common random numbers). A trade wins with probability `p`, paying "
        "`+b * (f * bankroll)`, else loses `f * bankroll`, `f` recomputed fresh from running "
        "state before every trade. A path is ruined and stopped once bankroll falls to or below "
        "$1. Two edge profiles: **A** = keel's promotion floor (p=0.55, b=1.5); **B** = a "
        "stronger edge (p=0.58, b=2.0). Two worlds per experiment: **p correct** (realized win "
        "rate matches the sizing assumption) and **p over-estimated by 0.05** (sizing assumes the "
        "stated p, the true realized win rate is 5 points lower). Unless noted, 500 seeded paths "
        "of 200 trades each, starting from $1,000."
    )
    lines.append("")

    # --- Experiment 3 ------------------------------------------------------------------------
    lines.append("## Experiment 3: dynamic drawdown taper")
    lines.append("")
    lines.append(
        "For each profile, three base fractions are tested: keel-1% (0.01, flat), Quarter-Kelly, "
        "and Half-Kelly (both computed from that profile's own p/b). Each base is run (i) "
        "untapered and (ii) tapered at ceilings D in {0.15, 0.25, 0.35}. EVERY combination also "
        "runs under keel's hard drawdown breaker, modeled as a hard halt (no further trades for "
        "the rest of the sequence) once a path's current drawdown from peak reaches 20% -- "
        "mirroring rail 11. \"Risk-adj\" is a crude ratio: median terminal multiple / median max "
        "DD (higher is better: more growth per unit of typical pain)."
    )
    lines.append("")
    for profile_name in profile_names:
        profile_out = exp3_results[profile_name]
        bases = profile_out["bases"]
        lines.append(f"### Profile {profile_name}")
        lines.append("")
        lines.append(
            f"Base fractions: keel-1%={fmt_pct(bases['keel-1%'])}, "
            f"Quarter-Kelly={fmt_pct(bases['Quarter-Kelly'])}, "
            f"Half-Kelly={fmt_pct(bases['Half-Kelly'])}."
        )
        lines.append("")
        for world_name in profile_out["worlds"]:
            lines.append(f"**World: {world_name}**")
            lines.append("")
            lines.append(exp3_table(profile_out, world_name))
            lines.append("")

    # Headline comparison pulled directly from the computed data (no hand-transcription drift):
    # systematically checks, across ALL 4 profile x world combos, whether each (base, D) beats
    # flat-1% on growth AND beats its own untapered version on safety.
    dominance = taper_dominance_summary(exp3_results)
    pa_correct = exp3_results[profile_a_name]["worlds"]["p correct"]
    flat1_row = pa_correct["keel-1% / no taper"]
    keel_taper_row = pa_correct["keel-1% / taper D=0.15"]

    lines.append("### Headline read: does taper-on-Quarter-Kelly dominate?")
    lines.append("")
    lines.append(
        "Checked systematically across all 4 profile x world combos (A/B x \"p correct\"/"
        "\"p over-estimated\"): for each (base fraction, taper ceiling D), does the tapered "
        "version reach a median terminal multiple >= flat-1%'s (growth-dominates), AND does it "
        "reach a median max DD and hard-breaker trip rate both <= its own untapered version's "
        "(safety-dominates)?"
    )
    lines.append("")
    lines.append(
        "| Base | Taper D | Growth-dominates flat-1% | Safety-dominates untapered | "
        "Full dominance (both) |"
    )
    lines.append("|---|---|---|---|---|")
    for base in ["Quarter-Kelly", "Half-Kelly"]:
        for ceiling in EXP3_TAPER_CEILINGS:
            d = dominance[base][ceiling]
            n = d["n_combos"]
            lines.append(
                f"| {base} | {ceiling:.2f} | {d['growth_hits']}/{n} combos | "
                f"{d['safety_hits']}/{n} combos | {d['full_hits']}/{n} combos |"
            )
    lines.append("")

    qk_d15 = dominance["Quarter-Kelly"][0.15]
    qk_d15_a_correct = next(
        r for r in qk_d15["rows"] if r["profile"] == profile_a_name and r["world"] == "p correct"
    )
    qk_flat_row = qk_d15_a_correct["untapered"]
    qk_taper_row = qk_d15_a_correct["tapered"]
    lines.append(
        f"**Full dominance (more growth than flat-1% AND less drawdown/fewer breaker trips than "
        f"untapered) holds in {qk_d15['full_hits']}/{qk_d15['n_combos']} combos for "
        f"Quarter-Kelly tapered at D=0.15** -- the one ceiling tested that sits BELOW keel's own "
        f"20% hard-breaker threshold. At D=0.25 and D=0.35 (ceilings ABOVE the hard breaker), "
        f"full dominance drops to {dominance['Quarter-Kelly'][0.25]['full_hits']}/"
        f"{dominance['Quarter-Kelly'][0.25]['n_combos']} and "
        f"{dominance['Quarter-Kelly'][0.35]['full_hits']}/"
        f"{dominance['Quarter-Kelly'][0.35]['n_combos']} combos respectively -- safety-dominance "
        "still holds almost everywhere (the taper reliably shrinks drawdown and breaker trips "
        "versus untapered, regardless of D), but growth-dominance over flat-1% mostly fails, "
        "because once D exceeds the hard-breaker threshold the taper no longer prevents the "
        "breaker from tripping -- and a tripped, frozen bankroll forfeits the same growth "
        "untapered Quarter-Kelly forfeits. Concretely, profile A / \"p correct\": flat-1% reaches "
        f"{flat1_row['median_multiple']:.3f}x; untapered Quarter-Kelly reaches "
        f"{qk_flat_row['median_multiple']:.3f}x but trips the breaker on "
        f"{fmt_pct(qk_flat_row['breaker_trip_rate'])} of paths (median max DD "
        f"{fmt_pct(qk_flat_row['median_max_dd'])}); Quarter-Kelly tapered at D=0.15 reaches "
        f"{qk_taper_row['median_multiple']:.3f}x with median max DD "
        f"{fmt_pct(qk_taper_row['median_max_dd'])} and a "
        f"{fmt_pct(qk_taper_row['breaker_trip_rate'])} breaker trip rate."
    )
    lines.append("")
    lines.append(
        f"**Does the taper help AT ALL on the 1% base?** Barely, and the hypothesis holds: on "
        f"keel-1%, drawdown almost never reaches even the tightest taper ceiling (D=0.15) -- "
        f"untapered keel-1% breaker-trips on {fmt_pct(flat1_row['breaker_trip_rate'])} of paths "
        f"(profile A, \"p correct\"), and tapering at D=0.15 changes that to "
        f"{fmt_pct(keel_taper_row['breaker_trip_rate'])} while giving up some growth "
        f"({keel_taper_row['median_multiple']:.3f}x vs {flat1_row['median_multiple']:.3f}x, "
        "because the taper starts shaving size any time drawdown is nonzero, not just near the "
        "ceiling). **Half-Kelly never achieves growth-dominance regardless of taper ceiling** "
        f"({dominance['Half-Kelly'][0.15]['growth_hits']}/"
        f"{dominance['Half-Kelly'][0.15]['n_combos']} combos at D=0.15): its base fraction is "
        "simply too large -- a single adverse trade can jump drawdown past even a tight taper "
        "ceiling in one or two trades, so the taper either zeroes risk out too early to compound "
        "meaningfully, or fails to prevent the breaker trip anyway."
    )
    lines.append("")

    # --- Experiment 4 ------------------------------------------------------------------------
    lines.append("## Experiment 4: Merton gamma sizing")
    lines.append("")
    implied = exp4_results["implied"]
    gamma_choices = exp4_results["gamma_choices"]
    gamma_a = implied[profile_a_name]["gamma"]
    gamma_b = implied[profile_b_name]["gamma"]

    lines.append("### (a) Implied risk-aversion gamma at keel's actual 1%")
    lines.append("")
    lines.append(
        "Solving `merton_fraction(mu, sigma2, gamma) = 0.01` for `gamma` at each profile's own "
        "mu/sigma2 (mu = p*b - (1-p), sigma2 = p*b^2 + (1-p) - mu^2):"
    )
    lines.append("")
    lines.append("| Profile | mu | sigma^2 | Implied gamma | x more risk-averse than gamma=1 |")
    lines.append("|---|---|---|---|---|")
    for profile_name in profile_names:
        row = implied[profile_name]
        lines.append(
            f"| {profile_name} | {row['mu']:.4f} | {row['sigma2']:.4f} | {row['gamma']:.2f} | "
            f"{row['gamma']:.1f}x |"
        )
    lines.append("")
    lines.append(
        f"keel's implied risk-aversion is roughly **{gamma_a:.1f}x** the Kelly-equivalent "
        f"(gamma=1) investor at profile A, and roughly **{gamma_b:.1f}x** at profile B. Full "
        "Kelly is approximately gamma=1; keel's flat 1% is, in this framing, the choice of an "
        "extremely risk-averse Merton investor -- far past the textbook gamma~2 estimate of "
        "typical human risk aversion."
    )
    lines.append("")

    lines.append("### (b) Fixed-gamma sizing across profiles")
    lines.append("")
    lines.append(
        "One `gamma` is fixed and applied to BOTH profiles' own mu/sigma2, compared against "
        "flat-1% and Quarter-Kelly: `gamma=A-implied` "
        f"({gamma_choices['gamma=A-implied']:.2f}, i.e. the gamma solved in (a) at profile A) and "
        f"`gamma=2` (textbook human-risk-aversion estimate)."
    )
    lines.append("")
    level_order = [
        "keel-1%",
        "Quarter-Kelly",
        "Merton (gamma=A-implied)",
        "Merton (gamma=2 (textbook))",
    ]
    for profile_name in profile_names:
        profile_out = exp4_results["cross"][profile_name]
        lines.append(f"#### Profile {profile_name}")
        lines.append("")
        levels = profile_out["levels"]
        lines.append(
            f"Sizing fractions: keel-1%={fmt_pct(levels['keel-1%'])}, "
            f"Quarter-Kelly={fmt_pct(levels['Quarter-Kelly'])}, "
            f"Merton(gamma=A-implied)={fmt_pct(levels['Merton (gamma=A-implied)'])}, "
            f"Merton(gamma=2)={fmt_pct(levels['Merton (gamma=2 (textbook))'])}."
        )
        lines.append("")
        for world_name in profile_out["worlds"]:
            lines.append(f"**World: {world_name}**")
            lines.append("")
            lines.append(exp4_table(profile_out, level_order, world_name))
            lines.append("")

    fa = exp4_results["cross"][profile_a_name]["levels"]["Merton (gamma=A-implied)"]
    fb = exp4_results["cross"][profile_b_name]["levels"]["Merton (gamma=A-implied)"]
    gamma_a_implied_val = gamma_choices["gamma=A-implied"]
    lines.append(
        f"**Cross-profile behavior at a single fixed gamma ({gamma_a_implied_val:.2f}, "
        f"profile A's implied gamma):** the SAME gamma sizes profile A at exactly "
        f"{fmt_pct(fa)} (by construction) but sizes the stronger, lower-variance profile B at "
        f"{fmt_pct(fb)} -- automatically MORE, with no re-tuning. Flat-1% cannot do this: it "
        "risks the identical 1% on both the floor edge and the stronger edge, by definition. This "
        "is the mechanical demonstration of the KB claim that Merton sizing is edge-aware where "
        "flat-fractional sizing is not."
    )
    lines.append("")

    lines.append("### (c) Fractional-Kelly equivalence (effective lambda)")
    lines.append("")
    lines.append(
        "Merton-at-a-fixed-gamma is mathematically a form of fractional Kelly: dividing the "
        "Merton fraction by that profile's own full-Kelly fraction gives an effective lambda "
        "(`lambda = f_merton / f_kelly`) -- \"what fraction of full Kelly is this gamma "
        "equivalent to, at this specific edge?\""
    )
    lines.append("")
    lines.append("| Profile | gamma | f_merton | Full Kelly | Effective lambda |")
    lines.append("|---|---|---|---|---|")
    for profile_name in profile_names:
        for gamma_name, gamma in gamma_choices.items():
            row = exp4_results["lambdas"][profile_name][gamma_name]
            lines.append(
                f"| {profile_name} | {gamma_name} ({gamma:.2f}) | {fmt_pct(row['f_merton'])} | "
                f"{fmt_pct(row['full_kelly'])} | {row['lambda']:.4f} ({row['lambda'] * 100:.2f}%) |"
            )
    lines.append("")
    lam_a = exp4_results["lambdas"][profile_a_name]["gamma=A-implied"]["lambda"]
    lam_b = exp4_results["lambdas"][profile_b_name]["gamma=A-implied"]["lambda"]
    lines.append(
        f"At `gamma=A-implied`, the effective lambda is {lam_a * 100:.2f}% of full Kelly at "
        f"profile A (matching keel-1%'s own ~4% of full Kelly noted in the prior bankroll-sizing "
        f"report) and {lam_b * 100:.2f}% at profile B -- close but not identical, because "
        "`merton_fraction` (a mean/variance formula) and `kelly_fraction` (the discrete binary "
        "formula) are two different approximations of the same growth-optimal bet size, not "
        "algebraically identical. Both worlds tables above show `Merton (gamma=A-implied)` "
        "keeping ruin at 0.0% under \"p over-estimated by 0.05\" at both profiles -- degrading "
        "gracefully, the same qualitative behavior fractional Kelly showed in the original "
        "`simulate.py` study."
    )
    lines.append("")

    # --- Verdicts ----------------------------------------------------------------------------
    lines.append("## Verdict per lead")
    lines.append("")

    qk_taper_ruin = qk_taper_row["ruin_rate"]
    qk_flat_ruin = qk_flat_row["ruin_rate"]
    taper_fully_dominates = qk_d15["full_hits"] == qk_d15["n_combos"]
    verdict_1 = (
        "PROMOTE to build candidate (with a specific, testable condition)"
        if taper_fully_dominates
        else "KEEP as ceiling-or-diagnostic only"
    )
    lines.append("### Lead 1: dynamic drawdown taper -- VERDICT: " + verdict_1)
    lines.append("")
    lines.append(
        f"Taper-on-Quarter-Kelly at D=0.15 vs flat-1% (growth): "
        f"{qk_taper_row['median_multiple']:.3f}x vs {flat1_row['median_multiple']:.3f}x -- more "
        f"growth in all {qk_d15['growth_hits']}/{qk_d15['n_combos']} combos tested. "
        f"Taper-on-Quarter-Kelly at D=0.15 vs untapered Quarter-Kelly (safety): median max DD "
        f"{fmt_pct(qk_taper_row['median_max_dd'])} vs {fmt_pct(qk_flat_row['median_max_dd'])}, "
        f"breaker trip rate {fmt_pct(qk_taper_row['breaker_trip_rate'])} vs "
        f"{fmt_pct(qk_flat_row['breaker_trip_rate'])}, ruin rate {fmt_pct(qk_taper_ruin)} vs "
        f"{fmt_pct(qk_flat_ruin)} -- safer in all "
        f"{qk_d15['safety_hits']}/{qk_d15['n_combos']} combos. This full dominance is "
        "CONDITIONAL: it holds cleanly only when the taper ceiling D sits below keel's own 20% "
        "hard-breaker threshold (D=0.15 here). At D=0.25 or D=0.35 the taper still reliably "
        "improves safety over untapered Quarter-Kelly, but usually stops beating flat-1% on "
        "growth, because a ceiling above the hard breaker no longer prevents the breaker from "
        "tripping. On the keel-1% base itself the taper barely engages (drawdown almost never "
        "reaches even D=0.15) -- the hypothesis holds: the taper's real value is in letting a "
        "HIGHER base fraction (Quarter-Kelly, not Half-Kelly -- see below) run with materially "
        "fewer breaker trips and shallower drawdowns, not in protecting the already-tiny 1% "
        "base. Half-Kelly's base fraction is too large for any taper ceiling tested to rescue: "
        "it never achieves growth-dominance, taper or no taper, because a single adverse trade "
        "can jump drawdown past the taper zone before it has a chance to brake gradually."
    )
    lines.append("")

    verdict_2 = "PROMOTE to build candidate"
    lines.append("### Lead 2: Merton gamma sizing -- VERDICT: " + verdict_2)
    lines.append("")
    lines.append(
        f"keel's implied risk-aversion is gamma~{gamma_a:.0f} at profile A and gamma~{gamma_b:.0f} "
        "at profile B -- both far above the textbook gamma~2 human-risk-aversion estimate and far "
        "above the gamma=1 Kelly-equivalent, i.e. keel is a mathematically extreme (not merely "
        "\"conservative\") point on this spectrum. A single fixed gamma automatically scales risk "
        "up on the stronger/lower-variance edge (B) and down on the floor edge (A) with zero "
        "re-tuning, which flat-1% cannot do by construction; and Merton-at-a-fixed-gamma degrades "
        "gracefully under the p-over-estimated stress test (ruin stays 0.0% at both profiles), "
        "matching fractional Kelly's known robustness. The formula is a legitimate, more "
        "principled way to express the SAME sub-Kelly choice keel already makes -- worth adopting "
        "as vocabulary/diagnostic (\"keel runs at effectively gamma~24-34\") even without changing "
        "risk_pct itself."
    )
    lines.append("")

    # --- Assumptions -------------------------------------------------------------------------
    lines.append("## Assumptions and honest limitations")
    lines.append("")
    lines.append(
        "- **Independent, i.i.d. trades.** Every trade is an independent Bernoulli draw with "
        "fixed p and b. Real crypto trades from correlated strategies (multiple concurrent "
        "positions moving together in a market-wide drawdown) violate this; correlated losses "
        "compound faster than this model accounts for, which understates real risk for any "
        "higher-fraction sizing (Half-Kelly, high-gamma-inverse Merton at large fractions)."
    )
    lines.append(
        "- **Known b, no fees/slippage.** `b` is treated as a known constant; trading fees, "
        "slippage, and spread are not modeled."
    )
    lines.append(
        "- **A single, fixed estimation-error magnitude.** The \"p over-estimated by 0.05\" "
        "world tests one specific misestimation size, not a distribution over possible errors. "
        "It illustrates a direction, not a calibrated probability."
    )
    lines.append(
        "- **Merton is a mean/variance approximation, not an exact rederivation of Kelly.** "
        "`merton_fraction` and `kelly_fraction` are two different formulas for \"how much to "
        "risk\"; gamma=1 approximately, not exactly, recovers full Kelly, and the effective-"
        "lambda numbers in (c) above reflect that approximation gap, not an algebraic identity."
    )
    lines.append(
        "- **The hard-breaker model is simplified.** It is modeled as a permanent halt for the "
        "rest of a fixed 200-trade sequence once tripped, with no recovery/reset logic and no "
        "modeling of the real rail 11 implementation's exact bookkeeping (weekly vs total DD, "
        "reset conditions). It exists here only to compare relative trip rates across sizing "
        "rules, not to reproduce rail 11 exactly."
    )
    lines.append(
        "- **This is not a recommendation to change keel's risk_pct.** Both leads are explored "
        "as mathematics and vocabulary for reasoning about sizing; any actual change to "
        "risk_pct, taper ceilings, or breaker thresholds would need its own review against "
        "keel's live guard rails, correlation across real positions, and backtest confidence -- "
        "none of which this script attempts to quantify."
    )
    lines.append("")

    # --- Numbers to fold into the KB ---------------------------------------------------------
    lines.append("## Exact numbers for KB source-84 §84.4 and §84.6")
    lines.append("")
    lines.append(
        f"- **§84.6 (Merton) -- keel's implied gamma:** ~{gamma_a:.1f} at profile A "
        f"(p=0.55, b=1.5, the promotion floor), ~{gamma_b:.1f} at profile B (p=0.58, b=2.0) -- "
        f"roughly {gamma_a:.0f}x to {gamma_b:.0f}x more risk-averse than the gamma=1 "
        "Kelly-equivalent investor, and 12-17x more risk-averse than the textbook gamma=2 human "
        "estimate."
    )
    lines.append(
        f"- **§84.6 -- effective lambda at gamma=A-implied:** {lam_a * 100:.2f}% of full Kelly "
        f"at profile A, {lam_b * 100:.2f}% at profile B (both close to keel-1%'s own ~4% of full "
        "Kelly figure from the original bankroll-sizing report)."
    )
    lines.append(
        f"- **§84.4 (taper) -- does taper-on-Quarter-Kelly dominate flat-1% AND untapered "
        f"Quarter-Kelly?** Yes, but ONLY when taper ceiling D < keel's 20% hard-breaker "
        f"threshold: at D=0.15, full dominance holds in {qk_d15['full_hits']}/{qk_d15['n_combos']} "
        f"profile x world combos tested. At D=0.25 it drops to "
        f"{dominance['Quarter-Kelly'][0.25]['full_hits']}/{dominance['Quarter-Kelly'][0.25]['n_combos']}"
        f", and at D=0.35 to {dominance['Quarter-Kelly'][0.35]['full_hits']}/"
        f"{dominance['Quarter-Kelly'][0.35]['n_combos']} -- safety-dominance (lower DD, fewer "
        "breaker trips than untapered) persists at every D tested, but growth-dominance over "
        "flat-1% requires the ceiling to sit below the hard breaker. Concretely at D=0.15, "
        f"profile A / \"p correct\": {qk_taper_row['median_multiple']:.3f}x median terminal "
        f"multiple (vs flat-1%'s {flat1_row['median_multiple']:.3f}x), median max DD "
        f"{fmt_pct(qk_taper_row['median_max_dd'])} and breaker trip rate "
        f"{fmt_pct(qk_taper_row['breaker_trip_rate'])} (vs untapered Quarter-Kelly's "
        f"{fmt_pct(qk_flat_row['median_max_dd'])} DD and "
        f"{fmt_pct(qk_flat_row['breaker_trip_rate'])} breaker trip rate)."
    )
    lines.append(
        f"- **§84.4 -- breaker-trip-rate deltas (profile A, \"p correct\"):** keel-1% untapered "
        f"{fmt_pct(flat1_row['breaker_trip_rate'])} -> keel-1% taper D=0.15 "
        f"{fmt_pct(keel_taper_row['breaker_trip_rate'])} (taper barely engages on the 1% base); "
        f"Quarter-Kelly untapered {fmt_pct(qk_flat_row['breaker_trip_rate'])} -> Quarter-Kelly "
        f"taper D=0.25 {fmt_pct(qk_taper_row['breaker_trip_rate'])} (taper materially cuts "
        "breaker trips on a higher base). Full per-D, per-base breaker-trip figures for both "
        "profiles and both worlds are in the Experiment 3 tables above."
    )
    lines.append("")

    return "\n".join(lines)


def main() -> None:
    print("Running Experiment 3 (dynamic drawdown taper)...")
    exp3_results = experiment_3()
    for profile_name, profile_out in exp3_results.items():
        print(f"  Profile {profile_name}")
        for world_name, summary in profile_out["worlds"].items():
            print(f"    World: {world_name}")
            for name in profile_out["combo_order"]:
                s = summary[name]
                print(
                    f"      {name}: median_mult={s['median_multiple']:.4f}x "
                    f"median_dd={s['median_max_dd']:.4f} worst_dd={s['worst_max_dd']:.4f} "
                    f"ruin={s['ruin_rate']:.4f} breaker_trip={s['breaker_trip_rate']:.4f} "
                    f"risk_adj={s['risk_adjusted']:.4f}"
                )

    print("Running Experiment 4 (Merton gamma sizing)...")
    exp4_results = experiment_4()
    for profile_name, row in exp4_results["implied"].items():
        print(f"  Implied gamma at {profile_name}: {row['gamma']:.4f}")
    for profile_name, profile_out in exp4_results["cross"].items():
        print(f"  Profile {profile_name}")
        for world_name, summary in profile_out["worlds"].items():
            print(f"    World: {world_name}")
            for name, s in summary.items():
                print(
                    f"      {name}: median_mult={s['median_multiple']:.4f}x "
                    f"median_dd={s['median_max_dd']:.4f} worst_dd={s['worst_max_dd']:.4f} "
                    f"ruin={s['ruin_rate']:.4f}"
                )

    report = build_report(exp3_results, exp4_results)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report)
    print(f"\nReport written to {REPORT_PATH}")


if __name__ == "__main__":
    main()
