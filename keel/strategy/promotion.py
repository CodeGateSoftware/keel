"""Promotion / demotion lifecycle for strategy rules (P2 Task 9).

Per spec §11 (proving gate) and §6.3/§20.7 (edge decay / demotion): a rule starts as a
`candidate`, is promoted to `paper` once a backtest clears the floor, promoted again to
`live` once its forward paper track record also clears the floor, and is demoted to
`disabled` the moment its rolling live stats fall back below floor:

    candidate --(backtest passes)--> paper --(paper track record passes)--> live
        --(rolling stats drop below floor)--> disabled

"Passes" means BOTH gates, not just the performance floors: `can_promote` requires the four
floors (G2, `check_floors`) **and** the PBO/CSCV overfitting gate (G4, `g4_pbo_gate`). Until
#247 only the floors were consulted -- `g4_pbo_gate` and `config.research.pbo_max`/`slope_floor`
both shipped, and nothing called either -- so a rule advanced toward real money on in-sample
performance alone, with a dormant gate that looked live. An overfitting check that was never
run is now reported as `NOT_RUN` and blocks promotion; it is not silently treated as a pass.

This module is deliberately built against `backtest.BacktestResult` as a plain input
shape — it does **not** import `paper.py` or `engine.py` (built in parallel in the same
wave); `paper.track_record()` returns a `BacktestResult`, so any caller that has one
(from a backtest or from paper trading) can drive `can_promote`/`should_demote`/
`transition` identically.

**Cross-product pooling of the sample-size axis (#338).** `min_trades` used to be judged
per rule per product, which at this project's measured daily rates (1.19–3.20
trades/asset-year) made the floor a 31–84-year wait. The operator-approved change
(2026-08-17, the agreement a gate change requires) is to the gate's UNIT OF EVALUATION,
not its floors: the sample-size axis may be cleared EITHER by the rule's own backtest
exactly as before OR by the same parameter set's pooled evidence across products in
paper — pooled n ≥ min_trades AND a diversity floor (see `MIN_POOLED_PRODUCTS`).
`min_trades = 100` itself is untouched: the win-rate axis was relaxed ALONE once, and
axes move only with their own justification. The G4/overfitting gate is NOT pooled: it
judges the parameter SELECTION (the trial matrix behind `--pbo-session`), which is
per-parameter-set evidence already, and this change does not alter its scope.

**Rules-table access:** `data/repository.py`'s `Repository` exposes typed `rules`-table
methods (`insert_rule`/`get_rules`/`update_rule_status`, P3 Task 1); this module drives
the lifecycle transitions purely through that surface. The `rules` table (see
`data/db.py`) has no separate "name" column — `kind` is a rule's stored identifier,
matching `Rule.name`/`Rule.describe()["name"]` from `rules/base.py`, so `rule_name` here
is matched against `rules.kind`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from keel_core.config import ResearchConfig

from keel.data.repository import Repository
from keel.research.cscv import PBOResult
from keel.strategy.backtest import BacktestResult

# Lifecycle order; `transition()` advances one step at a time (or demotes to the
# terminal state). `disabled` is terminal in v1 — reactivation is a future decision,
# not modeled here.
_PROMOTE_NEXT: dict[str, str] = {"candidate": "paper", "paper": "live"}


def next_status(status: str) -> str | None:
    """The lifecycle status one un-gated step ahead of `status` (`candidate`->`paper`,
    `paper`->`live`), or `None` when `status` has no next step (`live`, `disabled`, or any
    unrecognized status).

    Public wrapper around `_PROMOTE_NEXT`, for a caller that wants to advance a rule's status
    WITHOUT going through `transition`'s backtest/`can_promote` gate -- e.g. `rules promote
    --force`, a deliberate, auditable bypass for starting a paper-forward whose backtest can
    never reach the promotion floor (see that command's docstring).
    """
    return _PROMOTE_NEXT.get(status)


@dataclass
class PromotionConfig:
    """Performance floors a rule's stats must clear to promote (spec §11/§4.5)."""

    min_trades: int = 100
    min_expectancy: Decimal = Decimal("0")
    min_rr: Decimal = Decimal("1.5")
    min_win_rate: float = 0.55


# -- Per-rule-class promotion floors (KB §25.5) --------------------------------
# A rule's promotion floor depends on its *class* of edge. The canonical floor (100 trades,
# 55% win) suits mean-reversion/continuation rules that win most trades for small gains. A
# trend-follower (Donchian/Turtle breakout) is the opposite: it wins well under half its
# trades by design but pays off asymmetrically (avg win >> avg loss). Judging it by the
# global 55%-win floor rejects a genuinely-positive-expectancy edge -- exactly the
# "a good trade is not always a winning trade" principle (KB §25.5). So trend-follow rules
# get a low-win / high-R:R floor instead. Classes not listed here fall back to the caller's
# default floor (the config-supplied `PromotionConfig`).
#
# THIS CLASS RELAXES THE WIN-RATE AXIS ONLY -- `min_trades` stays at the canonical 100.
# The two axes are independent, and only the first has a justification. `min_trades` was
# originally relaxed 100 -> 30 in the same change that relaxed the win rate, as though they
# were one concession; two independent lines of evidence say the sample-size axis needed no
# relaxation at all:
#   * the 2026-07-20 random-entry control arm measured the requirement at ~68 trades
#     (`docs/experiments/2026-07-20-adx-ablation-and-random-entry-control.md`);
#   * KB §73.3's Minimum Backtest Length independently reproduces that figure (~68 at a
#     trials budget of 26) and puts it at ~143 at our actual trials count.
# At 30, a rule could promote on roughly HALF the sample its own edge would need to be
# statistically distinguishable from random entries through the same exit -- which is the
# one thing a promotion gate exists to prevent. A low win rate is a legitimate property of
# a trend-follower; a small sample is not a property of anything, it is just less evidence.
DEFAULT_CLASS = "default"
TREND_FOLLOW = "trend_follow"

_CLASS_FLOORS: dict[str, PromotionConfig] = {
    TREND_FOLLOW: PromotionConfig(
        min_trades=100,
        min_expectancy=Decimal("0"),
        min_rr=Decimal("1.5"),
        min_win_rate=0.30,
    ),
}


def floor_for_class(
    class_name: str, default: PromotionConfig | None = None
) -> PromotionConfig:
    """The promotion floor for a rule's `class_name`.

    A class with a fixed, code-defined floor (currently only `trend_follow`) returns that
    floor; any other class -- including `default` -- returns `default` (the config-supplied
    floor), or the canonical `PromotionConfig()` when `default` is omitted.
    """
    floor = _CLASS_FLOORS.get(class_name)
    if floor is not None:
        return floor
    return default if default is not None else PromotionConfig()


def promotion_class_of(rule: object) -> str:
    """A rule's promotion class -- its `promotion_class` attribute, or `DEFAULT_CLASS`."""
    return getattr(rule, "promotion_class", DEFAULT_CLASS)


def _realized_rr(stats: BacktestResult) -> Decimal:
    """Realized reward:risk = avg win / avg loss magnitude.

    `avg_loss` is stored as a non-positive `Decimal` (it's the mean pnl of losing
    trades); a rule with no losing trades yet has no realized risk to measure against,
    so it is treated as clearing any rr floor.
    """
    if stats.avg_loss == 0:
        return Decimal("Infinity")
    return stats.avg_win / abs(stats.avg_loss)


def check_floors(stats: BacktestResult, cfg: PromotionConfig) -> tuple[bool, list[str]]:
    """Whether `stats` clears every performance floor — spec §6.2's **G2**, and only G2.

    Returns `(True, [])` on pass, else `(False, reasons)` — one human-readable reason per
    failing floor, so all failures are visible at once rather than only the first.

    **This is not the promotion decision.** It was called `can_promote` until #247, which was
    the whole problem: a rule clearing four performance floors on one in-sample parameter set is
    precisely what §78's overfitting gate exists to be suspicious of, and a function with that
    name reads as authoritative to every caller and reader. The promotion decision is
    `can_promote` below, which checks this **and** G4. Call this one only where the floors axis
    alone is genuinely what's wanted — the simulate report's G2 line, and `insights`' "how far
    is this rule from its floor?" distance — both of which report G4 separately or not at all.
    """
    reasons: list[str] = []

    if stats.n_trades < cfg.min_trades:
        reasons.append(f"n_trades {stats.n_trades} < min_trades {cfg.min_trades}")

    if stats.expectancy <= cfg.min_expectancy:
        reasons.append(f"expectancy {stats.expectancy} <= min_expectancy {cfg.min_expectancy}")

    rr = _realized_rr(stats)
    if rr < cfg.min_rr:
        reasons.append(f"rr {rr} < min_rr {cfg.min_rr}")

    if stats.win_rate < cfg.min_win_rate:
        reasons.append(f"win_rate {stats.win_rate} < min_win_rate {cfg.min_win_rate}")

    return (len(reasons) == 0, reasons)


# -- Cross-product pooling of the sample-size axis (#338) -----------------------
#
# `min_trades` judged per rule per product made the floor unreachable in a human
# timespan at this project's measured daily rates (1.19-3.20 trades/asset-year: the
# go-live runbook's own table puts a single product at 31-84 years to 100 trades).
# The operator approved (2026-08-17) changing the gate's UNIT OF EVALUATION -- the same
# parameter set's evidence may be POOLED across products in paper -- not its floors:
# 100 stays 100. The recorded discipline above (the win-rate axis was relaxed ALONE;
# axes move only with their own justification) is respected: nothing here edits
# `PromotionConfig`.
#
# The diversity floor is the honest discount on pooling: crypto assets correlate, so a
# pool of correlated samples carries less information than its trade count claims --
# pooled-but-correlated evidence overstates its statistical power. Requiring breadth
# (several products each with an independently meaningful sample) is how the pooled
# path pays for the larger n instead of just collecting it.
MIN_POOLED_PRODUCTS = 5
MIN_TRADES_PER_PRODUCT_POOLED = 10


@dataclass(frozen=True)
class ProductSample:
    """One product's evidence for a shared parameter set: its id and its stats.

    `product_id` rides beside the `BacktestResult` (which is product-agnostic) because
    the pooled path's diversity floor is a claim about DISTINCT PRODUCTS, not about
    rows or results.
    """

    product_id: str
    stats: BacktestResult


@dataclass(frozen=True)
class PooledReading:
    """The pooled half of the gate's two readings, for reporting.

    `n_pooled` is the pool's total trades. `per_product` is the census (one entry per
    distinct product, its total contribution). `products_contributing` counts only
    products meeting `MIN_TRADES_PER_PRODUCT_POOLED` -- the diversity floor's question
    is how many products have independently meaningful samples, not how many exist.
    `min_contribution` is the smallest per-product total in the pool (the weakest link,
    whatever its size).
    """

    n_pooled: int
    per_product: tuple[tuple[str, int], ...]
    products_contributing: int
    min_contribution: int


def pool_stats(samples: Sequence[ProductSample]) -> tuple[BacktestResult, PooledReading]:
    """Pool per-product stats into one aggregate plus the diversity census.

    The pooling arithmetic, stated field by field. Everything is recomputed from the
    per-result aggregates `BacktestResult` carries; the underlying trade lists are NOT
    merged (see the note on path-dependent fields below):

    - pooled ``n_trades``  = ``Σ n_i``.
    - per-result wins      = ``round(n_i * win_rate_i)``: `win_rate` is stored as the
                             float ``wins / n_trades``, whose round-trip error (~1e-16)
                             is far below 0.5, so this recovers the integer win count.
    - pooled ``win_rate``  = ``Σ wins_i / Σ n_i``.
    - pooled ``avg_win``   = ``Σ(wins_i * avg_win_i) / Σ wins_i`` -- the win-weighted
                             mean, i.e. exactly the avg_win of the union's winning
                             trades (0 when the union has no wins).
    - pooled ``avg_loss``  = ``Σ(losses_i * avg_loss_i) / Σ losses_i`` with
                             ``losses_i = n_i - wins_i`` -- the same argument on the
                             loss side (0 when the union has no losses).
    - pooled ``expectancy``= ``Σ(n_i * expectancy_i) / Σ n_i`` -- the trade-weighted
                             mean; a size-weighted mean of means IS the overall mean,
                             so this is exactly the union's expectancy.
    - pooled ``profit_factor`` = ``Σ(wins_i * avg_win_i) / |Σ(losses_i * avg_loss_i)|``,
                             with `stats.summarize`'s Infinity / 0 conventions.
    - pooled ``avg_mfe``/``avg_mae`` = trade-weighted means, same as expectancy.
    - ``trades``/``max_drawdown``/``max_losing_streak`` are NOT pooled: drawdown and
                             streak are path-dependent -- they depend on the ORDER of
                             trades across the union, which no per-result aggregate
                             carries -- so any value would be fabricated. They are set
                             to empty/0, and the promotion gate reads none of them; a
                             caller wanting real cross-product drawdown must pool
                             equity curves, not stats.
    """
    n_total = sum(s.stats.n_trades for s in samples)
    wins = [round(s.stats.n_trades * s.stats.win_rate) for s in samples]
    losses = [s.stats.n_trades - w for s, w in zip(samples, wins)]
    wins_total = sum(wins)
    losses_total = sum(losses)

    gross_win = sum((w * s.stats.avg_win for w, s in zip(wins, samples)), Decimal(0))
    gross_loss = sum(
        (n_losses * s.stats.avg_loss for n_losses, s in zip(losses, samples)), Decimal(0)
    )

    counts: dict[str, int] = {}
    for sample in samples:
        counts[sample.product_id] = counts.get(sample.product_id, 0) + sample.stats.n_trades

    pooled_stats = BacktestResult(
        trades=[],
        n_trades=n_total,
        win_rate=(wins_total / n_total) if n_total else 0.0,
        avg_win=(gross_win / wins_total) if wins_total else Decimal(0),
        avg_loss=(gross_loss / losses_total) if losses_total else Decimal(0),
        expectancy=(
            sum((s.stats.n_trades * s.stats.expectancy for s in samples), Decimal(0)) / n_total
            if n_total
            else Decimal(0)
        ),
        profit_factor=(
            (gross_win / abs(gross_loss))
            if gross_loss != 0
            else (Decimal("Infinity") if gross_win > 0 else Decimal(0))
        ),
        # path-dependent fields, not pooled -- see docstring; the gate reads neither
        max_drawdown=Decimal(0),
        max_losing_streak=0,
        avg_mfe=(
            sum((s.stats.n_trades * s.stats.avg_mfe for s in samples), Decimal(0)) / n_total
            if n_total
            else Decimal(0)
        ),
        avg_mae=(
            sum((s.stats.n_trades * s.stats.avg_mae for s in samples), Decimal(0)) / n_total
            if n_total
            else Decimal(0)
        ),
    )

    reading = PooledReading(
        n_pooled=n_total,
        per_product=tuple(sorted(counts.items())),
        products_contributing=sum(
            1 for n in counts.values() if n >= MIN_TRADES_PER_PRODUCT_POOLED
        ),
        min_contribution=min(counts.values()) if counts else 0,
    )
    return pooled_stats, reading


def _pooled_floors(
    pooled: BacktestResult, reading: PooledReading, cfg: PromotionConfig
) -> tuple[bool, list[str]]:
    """Path (b): pooled n ≥ `cfg.min_trades` AND the diversity floor AND the quality
    floors judged on the POOLED aggregates.

    The floors themselves are the same `PromotionConfig` the per-rule path uses --
    100 stays 100; what differs is which sample answers the sample-size question, and
    the diversity requirement that pooled sample must additionally clear. Reasons name
    their path ("pooled ..."), so an operator can tell a pooled failure from a per-rule
    one at a glance.
    """
    reasons: list[str] = []

    if reading.n_pooled < cfg.min_trades:
        reasons.append(
            f"pooled n {reading.n_pooled} < min_trades {cfg.min_trades} across "
            f"{len(reading.per_product)} products"
        )

    if reading.products_contributing < MIN_POOLED_PRODUCTS:
        reasons.append(
            f"pooled diversity {reading.products_contributing} products < required "
            f"{MIN_POOLED_PRODUCTS} -- each contributing product must supply at least "
            f"{MIN_TRADES_PER_PRODUCT_POOLED} trades (crypto assets correlate, so a "
            "pool narrow enough to be correlated overstates its power; breadth is the "
            "discount pooled evidence pays for its larger n)"
        )

    if pooled.expectancy <= cfg.min_expectancy:
        reasons.append(
            f"pooled expectancy {pooled.expectancy} <= min_expectancy {cfg.min_expectancy}"
        )

    rr = _realized_rr(pooled)
    if rr < cfg.min_rr:
        reasons.append(f"pooled rr {rr} < min_rr {cfg.min_rr}")

    if pooled.win_rate < cfg.min_win_rate:
        reasons.append(f"pooled win_rate {pooled.win_rate} < min_win_rate {cfg.min_win_rate}")

    return (len(reasons) == 0, reasons)


def paper_sibling_rows(
    repo: Repository, kind: str, params: dict[str, Any]
) -> list[dict[str, Any]]:
    """The `rules` rows that count as pooled evidence for the (kind, params) being
    promoted: same `kind`, params IDENTICAL to `params` after dropping `product_id`,
    a DIFFERENT `product_id`, and status `paper`.

    Exact equality on the stored JSON-plain form (`"2"` vs `2` is a real difference --
    they rebuild differently), because pooling another parameter set's trades would
    launder a different experiment's sample into this one's evidence. Status `paper`
    because the pooled path exists to count out-of-sample paper track record, which is
    what that status means.

    One row per product, the most recently inserted (like `_fetch_rule`): two rows for
    one (params, product) are one rule observed twice, and pooling both would
    double-count its trades. The candidate's OWN product never appears here -- its
    trades enter the pool through the candidate's own stats, exactly once.
    """
    own_product = (params or {}).get("product_id")
    fingerprint = {k: v for k, v in (params or {}).items() if k != "product_id"}

    by_product: dict[str, dict[str, Any]] = {}
    for row in repo.get_rules(status="paper"):
        if row["kind"] != kind:
            continue
        row_params = row["params"] or {}
        product = row_params.get("product_id")
        if product is None or product == own_product:
            continue
        row_fingerprint = {k: v for k, v in row_params.items() if k != "product_id"}
        if row_fingerprint != fingerprint:
            continue
        current = by_product.get(product)
        if current is None or row["id"] > current["id"]:
            by_product[product] = row
    return list(by_product.values())


# -- G4: PBO overfitting gate (spec §7, KB §78) --------------------------------


@dataclass
class PBOGate:
    """G4 thresholds. Defaults mirror `keel_core.config.ResearchConfig`.

    ⛔ NEVER TUNE THESE TO OBTAIN A DESIRED VERDICT (§78.7's Strathern warning).
    """

    pbo_max: Decimal = Decimal("0.05")
    slope_floor: Decimal = Decimal("-0.5")


def g4_pbo_gate(
    pbo: Decimal, degradation_slope: Decimal, gate: PBOGate | None = None
) -> tuple[bool, list[str]]:
    """G4: fail only on `pbo > pbo_max` AND `degradation_slope < slope_floor`.

    A CONJUNCTION, deliberately, not the bare scalar. §78.7's limitation 4: "it is entirely
    possible that all the N strategies have high but similar Sharpe ratios... PBO will be
    high. Here overfitting is among many 'skillful' strategies." That is this project's
    plateau case exactly, and §54.10/§73.13 direct us to PREFER a broad plateau -- so a bare
    0.05 gate would reject the robust choice by construction.

    §78.7 supplies the reading rule this encodes: high PBO with a flat, positive OOS scatter
    is the GOOD outcome; high PBO with a steeply negative degradation slope is the bad one.

    Boundaries are strict (`>` and `<`), so landing exactly on both thresholds passes.
    """
    gate = gate or PBOGate()
    if pbo > gate.pbo_max and degradation_slope < gate.slope_floor:
        return False, [
            f"PBO {pbo:.2f} > {gate.pbo_max} AND degradation slope "
            f"{degradation_slope:.2f} < {gate.slope_floor}: the IS-best configuration "
            "underperforms the OOS median and OOS performance degrades steeply in IS "
            "performance -- the signature of a fitted, not a robust, selection (§78.8)"
        ]
    return True, []


def pbo_gate_from_config(research: ResearchConfig) -> PBOGate:
    """Build the G4 gate from a deployment's `research:` block.

    `config.research.pbo_max`/`slope_floor` have shipped in every config file since KB §78 was
    written, carrying a comment forbidding their tuning -- and nothing read them; `PBOGate`'s
    own defaults, which merely happen to match, were all that existed. Thresholds a deployment
    declares and an auditor can diff are the ones the gate must actually apply, so this is the
    seam that makes `research:` load-bearing rather than decorative.
    """
    return PBOGate(pbo_max=research.pbo_max, slope_floor=research.slope_floor)


# -- The promotion decision ----------------------------------------------------

#: `PromotionDecision.overfitting`. `NOT_RUN` is deliberately its own state rather than a
#: `bool | None`: "we did not check" is a different claim from "we checked and it failed", and
#: collapsing either into a boolean is how a dormant gate stays invisible.
NOT_RUN = "not_run"
PASSED = "pass"
FAILED = "fail"


@dataclass(frozen=True)
class PromotionDecision:
    """The outcome of `can_promote`, split so no axis can be mistaken for another.

    `promotable` is the only field that authorises a status change. `floors_pass` is reported
    separately because a rule can be perfect on performance and still un-promotable for want of
    an overfitting check -- an operator needs to see which of the two they are looking at.

    `pooled` is the pooled half of the sample-size reading (#338) when pooled samples were
    supplied, else `None`. It is REPORTING, not authorisation: it is present whenever a pool
    existed, whether or not the pooled path carried the decision.
    """

    promotable: bool
    reasons: list[str]
    floors_pass: bool
    overfitting: str
    pooled: PooledReading | None = None


def can_promote(
    stats: BacktestResult,
    cfg: PromotionConfig,
    pbo: PBOResult | None = None,
    gate: PBOGate | None = None,
    pooled_samples: Sequence[ProductSample] | None = None,
) -> PromotionDecision:
    """The promotion decision: performance floors (G2) **and** the overfitting gate (G4).

    `pbo` is a CSCV result for the trial matrix this rule's parameters were selected from
    (`keel.research.matrix.build_matrix` -> `keel.research.cscv.pbo`, the same pipeline behind
    `keel trials pbo`). `gate` supplies the thresholds; omit it to use `PBOGate()`'s defaults,
    or build one from the deployment's own config with `pbo_gate_from_config`.

    `pooled_samples` (#338) is the cross-product evidence pool for this rule's parameter
    set — the candidate's own sample PLUS one `ProductSample` per same-parameter `paper`
    sibling (see `paper_sibling_rows`). It changes the UNIT OF EVALUATION for the
    sample-size axis, never the floors:

    - With no pool (`pooled_samples=None` or empty), the decision is byte-for-byte the
      pre-#338 one: `check_floors(stats, cfg)` alone answers the floors.
    - With a pool, the per-rule reading is computed exactly as before, and — only when
      the rule's OWN sample is short (`n_trades < cfg.min_trades`) — the pooled path is
      also evaluated: pooled n ≥ `cfg.min_trades` AND the diversity floor
      (`MIN_POOLED_PRODUCTS` products each contributing `MIN_TRADES_PER_PRODUCT_POOLED`
      trades) AND the quality floors judged on the POOLED aggregates. If the pooled path
      clears, IT carries the decision and the per-rule sample-size/quality failures do
      not count against it. A rule whose own backtest already clears `min_trades` is
      judged on its own stats exactly as before: this is a unit change for rules that
      lack a sample, not a loosening for rules that have one.

    The G4/overfitting gate is NOT pooled: `pbo` judges the parameter selection (the
    trial matrix), which is per-parameter-set evidence, and its scope is unchanged.

    **`pbo=None` is not a pass.** It is `NOT_RUN`, it appears in `reasons`, and it makes
    `promotable` False. This is the entire point of the function: the four floors and the G4
    thresholds both already existed, `config.research.pbo_max`/`slope_floor` shipped in every
    config, and nothing ever called either — so promotion was decided on performance
    alone while the overfitting gate sat dormant, which is indistinguishable from having no
    gate except that it looked like having one.

    A gate that passes when it lacks evidence is worse than no gate at all: it converts
    "nobody checked" into "checked and fine" at exactly the moment a rule moves toward real
    money. So the missing case is loud and blocking, and the escape hatch is the pre-existing,
    deliberately noisy `rules promote --force` (see `next_status`), which leaves a WARNING-level
    audit record. An operator who must override this has a documented door; nobody walks through
    it by accident.

    Returns a `PromotionDecision` rather than a bare `(bool, reasons)` tuple, so that "the
    floors were fine but nothing was checked" cannot collapse into a single boolean that a
    caller might read the wrong way round.
    """
    floors_pass, reasons = check_floors(stats, cfg)

    pooled: PooledReading | None = None
    if pooled_samples:
        pooled_stats, pooled = pool_stats(pooled_samples)
        # The pooled path is reached only when the rule's OWN sample is short: a rule
        # whose backtest clears min_trades has its own adequate sample and is judged on
        # it, exactly as before. Anything else would turn a unit change for the
        # sample-starved into a quality rescue for the sample-rich.
        if stats.n_trades < cfg.min_trades:
            pooled_ok, pooled_reasons = _pooled_floors(pooled_stats, pooled, cfg)
            if pooled_ok:
                # The pooled path carries the decision; `reasons` on a pass is [] by
                # the same invariant `check_floors` keeps. The per-rule reading stays
                # visible to the caller through `stats` and `decision.pooled`.
                floors_pass, reasons = True, []
            else:
                reasons = reasons + pooled_reasons

    if pbo is None:
        overfitting = NOT_RUN
        reasons = reasons + [
            "overfitting check (G4 / PBO-CSCV) NOT RUN: no trial matrix was supplied, so the "
            "probability that this rule's parameters were selected by overfitting is UNKNOWN "
            "-- which is not the same as low, and is not a pass. Supply a CSCV result (see "
            "`keel trials pbo`), or bypass deliberately and on the record with "
            "`keel rules promote --force`."
        ]
    else:
        gate_ok, gate_reasons = g4_pbo_gate(pbo.pbo, pbo.degradation_slope, gate)
        overfitting = PASSED if gate_ok else FAILED
        reasons = reasons + gate_reasons

    return PromotionDecision(
        promotable=floors_pass and overfitting == PASSED,
        reasons=reasons,
        floors_pass=floors_pass,
        overfitting=overfitting,
        pooled=pooled,
    )


def should_demote(rolling_stats: BacktestResult, cfg: PromotionConfig) -> bool:
    """Whether a `live` rule's rolling stats have decayed below the promotion floor.

    Mirrors `check_floors`' performance checks (expectancy/rr/win-rate) but
    deliberately excludes `min_trades`: a live rule's rolling window is sized for
    timely decay detection (spec §6.3/§20.7), not for re-proving the original sample
    size, so requiring `min_trades` here would make demotion undetectable in practice.
    """
    if rolling_stats.expectancy <= cfg.min_expectancy:
        return True
    if _realized_rr(rolling_stats) < cfg.min_rr:
        return True
    if rolling_stats.win_rate < cfg.min_win_rate:
        return True
    return False


def _fetch_rule(repo: Repository, rule_name: str, rule_id: int | None = None) -> tuple[int, str]:
    """The `rules` row to act on (id/status): the row with `rule_id` when one is named,
    else the most recently inserted row for `kind == rule_name`.

    The kind-level fallback predates multi-row kinds (`rules seed` writes one row per
    (kind, product)); with several rows of one kind it targets the NEWEST, which is
    only right for the single-row case it was written for. A caller that knows which
    row it means -- `keel rules promote <id>` always does -- must not have that guess
    made for it: promoting "the latest row of this kind" would advance a sibling the
    operator never named, which is exactly the wrong row now that pooling (#338) makes
    same-kind sibling rows the normal shape of the table.
    """
    if rule_id is not None:
        for r in repo.get_rules():
            if r["id"] == rule_id:
                if r["kind"] != rule_name:
                    raise ValueError(
                        f"rule id {rule_id} is kind {r['kind']!r}, not {rule_name!r}"
                    )
                return r["id"], r["status"]
        raise ValueError(f"no rule found in the rules table with id={rule_id}")
    matches = [r for r in repo.get_rules() if r["kind"] == rule_name]
    if not matches:
        raise ValueError(f"no rule found in the rules table for kind={rule_name!r}")
    rule = max(matches, key=lambda r: r["id"])
    return rule["id"], rule["status"]


def transition(
    repo: Repository,
    rule_name: str,
    stats: BacktestResult,
    cfg: PromotionConfig,
    pbo: PBOResult | None = None,
    gate: PBOGate | None = None,
    pooled_samples: Sequence[ProductSample] | None = None,
    rule_id: int | None = None,
) -> str:
    """Advance (or demote) `rule_name`'s lifecycle status in the `rules` table given
    fresh `stats`, and return the resulting status.

    - `candidate`/`paper`: promotes one step (to `paper`/`live`) if `can_promote(stats, cfg,
      pbo, gate, pooled_samples)` passes, else stays put. `pooled_samples` is the same
      cross-product pool the caller showed the operator (#338) — passing it here is what
      keeps the decision the DB obeys identical to the decision the operator read.
      **Without `pbo` it never promotes** -- see
      `can_promote` for why an unrun overfitting check blocks rather than waves through.
    - `live`: demotes to `disabled` if `should_demote(stats, cfg)`, else stays `live`.
    - `disabled`: terminal; always stays `disabled`.

    `rule_id`, when supplied, is the exact row to act on (see `_fetch_rule`); omit it for
    the historical kind-level lookup. The CLI always names the row, so a promotion lands
    on the rule the operator typed, never on a same-kind sibling that happens to be newer.

    Demotion deliberately does NOT consult `pbo`, and the asymmetry is the safety property:
    missing evidence must block a rule moving toward real money and must never block pulling
    one back from it.
    """
    target_id, status = _fetch_rule(repo, rule_name, rule_id)

    if status in _PROMOTE_NEXT:
        if can_promote(stats, cfg, pbo, gate, pooled_samples).promotable:
            new_status = _PROMOTE_NEXT[status]
            repo.update_rule_status(target_id, new_status)
            return new_status
        return status

    if status == "live":
        if should_demote(stats, cfg):
            repo.update_rule_status(target_id, "disabled")
            return "disabled"
        return status

    return status  # "disabled" (or any unrecognized status): terminal, no-op
