"""Promotion / demotion lifecycle for strategy rules (P2 Task 9).

Per spec §11 (proving gate) and §6.3/§20.7 (edge decay / demotion): a rule starts as a
`candidate`, is promoted to `paper` once a backtest clears the floor, promoted again to
`live` once its forward paper track record also clears the floor, and is demoted to
`disabled` the moment its rolling live stats fall back below floor:

    candidate --(backtest passes)--> paper --(paper track record passes)--> live
        --(rolling stats drop below floor)--> disabled

This module is deliberately built against `backtest.BacktestResult` as a plain input
shape — it does **not** import `paper.py` or `engine.py` (built in parallel in the same
wave); `paper.track_record()` returns a `BacktestResult`, so any caller that has one
(from a backtest or from paper trading) can drive `can_promote`/`should_demote`/
`transition` identically.

**Rules-table access:** `data/repository.py`'s `Repository` exposes typed `rules`-table
methods (`insert_rule`/`get_rules`/`update_rule_status`, P3 Task 1); this module drives
the lifecycle transitions purely through that surface. The `rules` table (see
`data/db.py`) has no separate "name" column — `kind` is a rule's stored identifier,
matching `Rule.name`/`Rule.describe()["name"]` from `rules/base.py`, so `rule_name` here
is matched against `rules.kind`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from keel.data.repository import Repository
from keel.strategy.backtest import BacktestResult

# Lifecycle order; `transition()` advances one step at a time (or demotes to the
# terminal state). `disabled` is terminal in v1 — reactivation is a future decision,
# not modeled here.
_PROMOTE_NEXT: dict[str, str] = {"candidate": "paper", "paper": "live"}


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


def can_promote(stats: BacktestResult, cfg: PromotionConfig) -> tuple[bool, list[str]]:
    """Whether `stats` clears every promotion floor. Returns `(True, [])` on pass, else
    `(False, reasons)` — one human-readable reason per failing floor, so all failures
    are visible at once rather than only the first.
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


def should_demote(rolling_stats: BacktestResult, cfg: PromotionConfig) -> bool:
    """Whether a `live` rule's rolling stats have decayed below the promotion floor.

    Mirrors `can_promote`'s performance checks (expectancy/rr/win-rate) but
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


def _fetch_rule(repo: Repository, rule_name: str) -> tuple[int, str]:
    """The most recently inserted `rules` row for `kind == rule_name` (id/status)."""
    matches = [r for r in repo.get_rules() if r["kind"] == rule_name]
    if not matches:
        raise ValueError(f"no rule found in the rules table for kind={rule_name!r}")
    rule = max(matches, key=lambda r: r["id"])
    return rule["id"], rule["status"]


def transition(
    repo: Repository, rule_name: str, stats: BacktestResult, cfg: PromotionConfig
) -> str:
    """Advance (or demote) `rule_name`'s lifecycle status in the `rules` table given
    fresh `stats`, and return the resulting status.

    - `candidate`/`paper`: promotes one step (to `paper`/`live`) if `can_promote(stats,
      cfg)` passes, else stays put.
    - `live`: demotes to `disabled` if `should_demote(stats, cfg)`, else stays `live`.
    - `disabled`: terminal; always stays `disabled`.
    """
    rule_id, status = _fetch_rule(repo, rule_name)

    if status in _PROMOTE_NEXT:
        if can_promote(stats, cfg)[0]:
            new_status = _PROMOTE_NEXT[status]
            repo.update_rule_status(rule_id, new_status)
            return new_status
        return status

    if status == "live":
        if should_demote(stats, cfg):
            repo.update_rule_status(rule_id, "disabled")
            return "disabled"
        return status

    return status  # "disabled" (or any unrecognized status): terminal, no-op


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
