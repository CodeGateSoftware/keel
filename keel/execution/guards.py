"""THE HARD RAILS (§14) — enforced before every order, un-overridable.

`check()` runs the twelve safety rails from the main spec's §14 before any order is placed, in
every `auto_trade` mode (confirm *and* bypass) and for both rule-trading and DCA order classes.
It never short-circuits: every violated rail is collected and reported so an operator (or the
executor, Task 4) sees the full picture, not just the first trip-wire.

Design notes on rails that need state this repo doesn't compute anywhere else yet (Task 3 lands
before the executor/money_mgmt modules that would normally produce some of these numbers):

- Rails 3/4/5/6/8 (day spend / open exposure / correlation / concentration / averaging-into-
  losers) are derived here from `repo.get_orders(mode="live", ...)` — the audit-log source of
  truth — rather than from a separate position-tracker module that doesn't exist yet. A stored
  order's notional is `qty * (actual_fill or limit_price or expected_fill)`.
- Rail 9 (no stop-loss widening) reads an `agent_state` key `open_stop:<product_id>` holding the
  last-known protective stop for that product; the executor (Task 4) is expected to keep it
  current as brackets are placed/rolled. No prior stop recorded means there is nothing to widen
  against, so the rail passes (an intent's *first* stop is never "widening").
- Rail 11 (drawdown breaker) reads precomputed `agent_state` keys `drawdown_total_pct` /
  `drawdown_weekly_pct` (owned by `money_mgmt`/`pnl`, later phases) rather than recomputing an
  equity curve here — guards is a pure checker, not a P&L engine.
- Rail 12 fails *closed*: an unset `kill_switch` is treated as engaged, and a never-recorded
  `last_feed_ts` is treated as stale. Silence is not consent to trade.
- `MIN_MOVE_PCT` / `CORRELATED_SIZE_SCALE` / `UNCORRELATED_ASSETS` / `FEED_STALENESS_CYCLES` are
  conservative constants `config.yaml` doesn't (yet) carry fields for; they live here, next to
  the rail that uses them, so they stay easy to find and are still un-overridable from anywhere
  outside this module.

DCA exemptions (explicit): DCA is a "distinct order class" (§8/§12.1) designed to keep buying
through drawdowns on a fixed small budget/cadence, not a rule-trading signal. It is exempt from
rail 11 (the DD breaker — stated explicitly in the plan) and, for the same functional reason,
rail 8 (no-averaging-into-losers would otherwise block the exact buying-the-dip behavior DCA
exists to do). DCA remains bound by every other rail, explicitly including the allowlist, the
per-asset cap, and the kill-switch (§12.6).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from keel.config import Config
from keel.data.repository import Repository
from keel.types import Side

# -- rail constants not (yet) carried by config.yaml -------------------------------------------

MIN_MOVE_PCT = Decimal("0.005")  # rail 7: conservative spread+fees clearance floor
CORRELATED_SIZE_SCALE = Decimal("0.5")  # rail 5: half-size when correlated exposure is open
UNCORRELATED_ASSETS = frozenset({"PAXG"})  # gold-backed; not "long crypto beta" (§4.1)
FEED_STALENESS_CYCLES = 3  # rail 12: 3 missed polling cycles = stale feed

_ACTIVE_ORDER_STATUSES = ("pending", "filled")


@dataclass(frozen=True)
class OrderIntent:
    """A candidate order, already sized, awaiting the hard rails before preview/place."""

    product_id: str
    side: Side
    qty: Decimal
    entry: Decimal
    stop: Decimal | None
    notional: Decimal
    is_dca: bool
    rule_kind: str


@dataclass(frozen=True)
class GuardResult:
    """The outcome of running all twelve rails: `ok` iff `violations` is empty."""

    ok: bool
    violations: list[str]


def _asset(product_id: str) -> str:
    return product_id.split("-")[0]


def _utc_day_bounds(ts: int) -> tuple[int, int]:
    """Return `[start, end)` epoch seconds for the UTC calendar day containing `ts`."""
    day_start = datetime.fromtimestamp(ts, tz=UTC).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    start = int(day_start.timestamp())
    return start, start + 86400


def _order_notional(order: dict[str, Any]) -> Decimal:
    qty = order.get("qty") or Decimal("0")
    price = order.get("actual_fill") or order.get("limit_price") or order.get("expected_fill")
    if price is None:
        return Decimal("0")
    return qty * price


def _open_exposure_by_asset(repo: Repository) -> dict[str, Decimal]:
    """Net at-risk notional per asset from filled live orders (BUY adds, SELL reduces)."""
    exposure: dict[str, Decimal] = {}
    for order in repo.get_orders(mode="live", status="filled"):
        asset = _asset(order["product_id"])
        amount = _order_notional(order)
        if order["side"] == Side.BUY.value:
            exposure[asset] = exposure.get(asset, Decimal("0")) + amount
        elif order["side"] == Side.SELL.value:
            exposure[asset] = exposure.get(asset, Decimal("0")) - amount
    return {asset: amt for asset, amt in exposure.items() if amt > 0}


def _daily_spend_usd(repo: Repository, now_ts: int) -> Decimal:
    """Sum of today's (UTC) BUY notional across all products, from the orders audit log."""
    start, end = _utc_day_bounds(now_ts)
    total = Decimal("0")
    for order in repo.get_orders(mode="live"):
        if order["side"] != Side.BUY.value or order["status"] not in _ACTIVE_ORDER_STATUSES:
            continue
        created_at = order.get("created_at")
        if created_at is None or not (start <= created_at < end):
            continue
        total += _order_notional(order)
    return total


def check(intent: OrderIntent, repo: Repository, config: Config, now_ts: int) -> GuardResult:
    """Run all twelve §14 hard rails against `intent`. Never short-circuits.

    Called before every order in every `auto_trade` mode (confirm *and* bypass) — un-overridable.
    """
    violations: list[str] = []
    asset = _asset(intent.product_id)
    is_buy = intent.side == Side.BUY

    # 1. Halal allowlist — un-overridable, applies to every intent including DCA.
    if asset not in config.allowlist:
        violations.append(
            f"halal_allowlist: {asset!r} (from {intent.product_id}) is not in the allowlist "
            f"{config.allowlist!r}"
        )

    # 2. Per-order $ cap.
    if intent.notional > config.caps.max_per_order_usd:
        violations.append(
            f"per_order_cap: notional {intent.notional} exceeds max_per_order_usd "
            f"{config.caps.max_per_order_usd}"
        )

    # 3. Per-day $ cap — running total of today's BUY spend, from the orders audit log.
    if is_buy:
        daily_spend = _daily_spend_usd(repo, now_ts)
        projected_daily = daily_spend + intent.notional
        if projected_daily > config.caps.max_per_day_usd:
            violations.append(
                f"per_day_cap: today's spend {daily_spend} + {intent.notional} = "
                f"{projected_daily} exceeds max_per_day_usd {config.caps.max_per_day_usd}"
            )

    exposure = _open_exposure_by_asset(repo)
    total_exposure = sum(exposure.values(), Decimal("0"))

    # 4. Total open-exposure cap — sum of at-risk capital across all open positions (§4.1).
    if is_buy:
        projected_exposure = total_exposure + intent.notional
        if projected_exposure > config.caps.max_exposure_usd:
            violations.append(
                f"total_exposure_cap: open exposure {total_exposure} + {intent.notional} = "
                f"{projected_exposure} exceeds max_exposure_usd {config.caps.max_exposure_usd}"
            )

    # 5. Correlation-adjusted sizing — scale down when another correlated asset is already
    #    open (crypto assets move as one "long crypto beta" bet; PAXG/gold is excluded, §4.1).
    if is_buy and asset not in UNCORRELATED_ASSETS:
        other_correlated_exposure = sum(
            (amt for a, amt in exposure.items() if a != asset and a not in UNCORRELATED_ASSETS),
            Decimal("0"),
        )
        if other_correlated_exposure > 0:
            correlated_cap = config.caps.max_per_order_usd * CORRELATED_SIZE_SCALE
            if intent.notional > correlated_cap:
                violations.append(
                    f"correlation_adjusted_sizing: {asset} notional {intent.notional} exceeds "
                    f"the correlated-size cap {correlated_cap} while {other_correlated_exposure} "
                    "is already open in correlated assets"
                )

    # 6. Per-asset concentration cap — portfolio proxied by max_exposure_usd (§10.3), since
    #    guards has no equity oracle: the configured total exposure ceiling is the funded
    #    trading capital (§2.8) that max_per_asset_pct is a fraction of.
    if is_buy:
        per_asset_limit = config.caps.max_per_asset_pct * config.caps.max_exposure_usd
        projected_asset_exposure = exposure.get(asset, Decimal("0")) + intent.notional
        if projected_asset_exposure > per_asset_limit:
            violations.append(
                f"per_asset_concentration_cap: {asset} exposure {projected_asset_exposure} "
                f"exceeds {config.caps.max_per_asset_pct} of max_exposure_usd ({per_asset_limit})"
            )

    # 7. Min-move / anti-scalping — the entry-to-stop distance must clear an assumed
    #    spread+fees floor; DCA/no-stop intents have no move to evaluate.
    if intent.stop is not None and intent.entry != 0:
        move_pct = abs(intent.entry - intent.stop) / intent.entry
        if move_pct < MIN_MOVE_PCT:
            violations.append(
                f"min_move_anti_scalping: entry/stop move {move_pct:.4%} is tighter than the "
                f"{MIN_MOVE_PCT:.4%} spread+fees floor"
            )

    # 8. No averaging into losers (no martingale, §5.1). DCA is exempt — its whole design is
    #    to keep buying through drawdowns on a fixed small budget (§8/§12.1).
    if is_buy and not intent.is_dca:
        buy_orders = [
            o
            for o in repo.get_orders(mode="live", product_id=intent.product_id, status="filled")
            if o["side"] == Side.BUY.value
        ]
        total_qty = sum((o["qty"] for o in buy_orders), Decimal("0"))
        if total_qty > 0:
            total_cost = sum((_order_notional(o) for o in buy_orders), Decimal("0"))
            avg_cost = total_cost / total_qty
            if intent.entry < avg_cost:
                violations.append(
                    f"no_averaging_into_losers: entry {intent.entry} is below the average cost "
                    f"basis {avg_cost} of the existing {intent.product_id} position"
                )

    # 9. No stop-loss widening — stops only ratchet toward profit vs. the last recorded stop.
    if intent.stop is not None:
        prior_stop = repo.get_state(f"open_stop:{intent.product_id}")
        if prior_stop is not None and intent.stop < prior_stop:
            violations.append(
                f"no_stop_widening: proposed stop {intent.stop} is wider (lower) than the prior "
                f"stop {prior_stop}"
            )

    # 10. Sell-only-on-rule — no arbitrary liquidation; every SELL must cite a defined rule.
    if intent.side == Side.SELL and not intent.rule_kind:
        violations.append(
            "sell_only_on_rule: SELL intent has no rule_kind -- sells are only valid on a "
            "defined exit/harvest rule"
        )

    # 11. Account-drawdown circuit breaker — total AND weekly; DCA exempt (§12.6).
    if is_buy and not intent.is_dca:
        dd_total = repo.get_state("drawdown_total_pct", default=Decimal("0"))
        dd_weekly = repo.get_state("drawdown_weekly_pct", default=Decimal("0"))
        if dd_total >= config.money_mgmt.max_total_dd_pct:
            violations.append(
                f"account_dd_breaker_total: drawdown {dd_total} >= max_total_dd_pct "
                f"{config.money_mgmt.max_total_dd_pct}"
            )
        if dd_weekly >= config.money_mgmt.max_weekly_dd_pct:
            violations.append(
                f"account_dd_breaker_weekly: drawdown {dd_weekly} >= max_weekly_dd_pct "
                f"{config.money_mgmt.max_weekly_dd_pct}"
            )

    # 12. Stale-data/feed-health + kill-switch — fails closed on missing state.
    if repo.get_state("kill_switch", default=True):
        violations.append("kill_switch: agent_state.kill_switch is engaged (or never resumed)")

    last_feed_ts = repo.get_state("last_feed_ts")
    staleness_threshold = config.auto_trade.interval_sec * FEED_STALENESS_CYCLES
    if last_feed_ts is None or (now_ts - last_feed_ts) > staleness_threshold:
        age = "never recorded" if last_feed_ts is None else f"{now_ts - last_feed_ts}s old"
        violations.append(f"stale_data: feed is stale ({age}; threshold {staleness_threshold}s)")

    return GuardResult(ok=not violations, violations=violations)
