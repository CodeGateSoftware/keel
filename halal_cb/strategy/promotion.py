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

from halal_cb.data.repository import Repository
from halal_cb.strategy.backtest import BacktestResult

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
