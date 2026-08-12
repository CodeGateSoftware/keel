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
    """

    promotable: bool
    reasons: list[str]
    floors_pass: bool
    overfitting: str


def can_promote(
    stats: BacktestResult,
    cfg: PromotionConfig,
    pbo: PBOResult | None = None,
    gate: PBOGate | None = None,
) -> PromotionDecision:
    """The promotion decision: performance floors (G2) **and** the overfitting gate (G4).

    `pbo` is a CSCV result for the trial matrix this rule's parameters were selected from
    (`keel.research.matrix.build_matrix` -> `keel.research.cscv.pbo`, the same pipeline behind
    `keel trials pbo`). `gate` supplies the thresholds; omit it to use `PBOGate()`'s defaults,
    or build one from the deployment's own config with `pbo_gate_from_config`.

    **`pbo=None` is not a pass.** It is `NOT_RUN`, it appears in `reasons`, and it makes
    `promotable` False. This is the entire point of the function: the four floors and the G4
    thresholds both already existed, `config.research.pbo_max`/`slope_floor` shipped in every
    config, and nothing ever called `g4_pbo_gate` — so promotion was decided on performance
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


def _fetch_rule(repo: Repository, rule_name: str) -> tuple[int, str]:
    """The most recently inserted `rules` row for `kind == rule_name` (id/status)."""
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
) -> str:
    """Advance (or demote) `rule_name`'s lifecycle status in the `rules` table given
    fresh `stats`, and return the resulting status.

    - `candidate`/`paper`: promotes one step (to `paper`/`live`) if `can_promote(stats, cfg,
      pbo, gate)` passes, else stays put. **Without `pbo` it never promotes** -- see
      `can_promote` for why an unrun overfitting check blocks rather than waves through.
    - `live`: demotes to `disabled` if `should_demote(stats, cfg)`, else stays `live`.
    - `disabled`: terminal; always stays `disabled`.

    Demotion deliberately does NOT consult `pbo`, and the asymmetry is the safety property:
    missing evidence must block a rule moving toward real money and must never block pulling
    one back from it.
    """
    rule_id, status = _fetch_rule(repo, rule_name)

    if status in _PROMOTE_NEXT:
        if can_promote(stats, cfg, pbo, gate).promotable:
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
