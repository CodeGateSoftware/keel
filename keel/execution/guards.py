"""THE HARD RAILS (§14) — enforced before every order, un-overridable.

`check()` runs the twelve safety rails from the main spec's §14, plus three later, equally
un-overridable safety-critical rails: 13/14 added by Issue #59 (USDC-funding + monthly-allowance),
and 16, the consecutive-loss circuit breaker (Task 4), before any order is placed, in every
`auto_trade` mode (confirm *and* bypass) and for both rule-trading and DCA order classes. It never
short-circuits: every violated rail is collected and reported so an operator (or the executor,
Task 4) sees the full picture, not just the first trip-wire.

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
per-asset cap, and the kill-switch (§12.6) -- and, explicitly, rails 13/14 below: DCA orders are
themselves the recurring "subscription" spend rail 14 exists to cap.

Rails 13/14 (Issue #59, safety-critical, un-overridable like every rail above):

- Rail 13 (USDC-funding) never lets a BUY draw from a linked bank/ACH source -- it may only
  spend an already-settled quote-currency (default USDC) balance that is `> 0` AND covers the
  order's notional. The balance isn't computed here (guards has no broker access, by design --
  it's a pure checker); the caller (the executor) fetches it live from the broker and hands it
  in via `OrderIntent.available_quote`. **Fails closed**: `None` (broker error, missing quote
  account, anything unknown) vetoes the BUY exactly like rail 12 treats an unset kill-switch or
  a never-recorded feed timestamp -- silence is not consent to spend. SELL is exempt (it
  produces USDC, it doesn't consume it).
- Rail 14 (monthly subscription-allowance) caps this calendar month's live BUY notional (own
  spend, from the orders audit log, `_monthly_buy_spend_usd`) plus this order's notional against
  the allowance derived from the venue's **attested subscription record**
  (`repo.get_broker_subscription`, `data/repository.py`) -- read fresh on every `check()` call,
  never cached, so `keel subscription attest` takes effect on the very next order. The cap is
  `free_volume_usd` from that record, so upgrading a tier changes exactly one place; it is NOT
  typed into config. **Fails closed** like rails 12/13: an unattested venue, a `suspect` or
  `lapsed` record, or one whose `attest_due_ts` has passed all fall back to
  `config.subscription.unsubscribed_allowance_usd` (default 0, i.e. no spending) -- silence is
  not consent to spend. A record with `free_volume_usd IS NULL` (Premium, unlimited and in
  force) has no cap and the rail does not apply. Optional `pacing="even_daily"`, read from the
  record, additionally caps cumulative month-to-date spend to
  `allowance / business_days_in_month * business_days_elapsed` (Mon-Fri, no holiday calendar);
  `pacing="opportunistic"` (default) skips that extra check.

Rail 16 (consecutive-loss circuit breaker, Task 4, safety-critical, un-overridable): a SEQUENCE
breaker where rail 11 is a MAGNITUDE breaker -- it detects that the edge may have stopped working
BEFORE the drawdown accumulates. It reads exactly one precomputed `agent_state` key,
`streak_halt_until` (owned by the producer, `execution/streak.py`), and never the
`consecutive_losses` counter itself, so the "is the threshold reached" decision lives in one place
and cannot disagree with itself. ENTRIES ONLY, and DCA-exempt like rail 11 (§12.6) -- a breaker
that blocked exits would trap capital in a losing position, inverting its own purpose. Ships
DISABLED (`config.money_mgmt.max_consecutive_losses` defaults to 0).
"""

from __future__ import annotations

import calendar
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from keel_core.subscription import SubscriptionStatus
from keel_core.telemetry import log_event

from keel.config import Config
from keel.data.repository import Repository
from keel.types import Side

logger = logging.getLogger(__name__)

# -- rail constants not (yet) carried by config.yaml -------------------------------------------

MIN_MOVE_PCT = Decimal("0.005")  # rail 7: conservative spread+fees clearance floor
CORRELATED_SIZE_SCALE = Decimal("0.5")  # rail 5: half-size when correlated exposure is open
UNCORRELATED_ASSETS = frozenset({"PAXG"})  # gold-backed; not "long crypto beta" (§4.1)
FEED_STALENESS_CYCLES = 3  # rail 12: 3 missed polling cycles = stale feed

# Rail 14: the engine is single-venue until the broker port lands. `OrderIntent` carries no
# venue to key on, and inventing one before then would be a guess -- but `broker_subscriptions`
# is venue-keyed from birth because that costs nothing and is the right shape. This constant is
# the one line the multi-venue migration deletes (monorepo design spec §8).
DEFAULT_VENUE = "coinbase"

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
    # Rail 13 (USDC-funding): the live available quote-currency (default USDC) balance, fetched
    # by the caller (the executor) from the broker -- guards has no broker access of its own.
    # `None` means "unknown/unavailable" and fails the BUY closed, same as a missing quote
    # account or a broker error while fetching it.
    available_quote: Decimal | None = None

    # Rail 17 (§65.4 qabd/possession). Whether the account is currently in a state where the
    # asset could be withdrawn on demand. Supplied by the caller (the executor) from the
    # operator's attestation and any broker-reported restriction -- guards has no broker or
    # clock access of its own. `None` means "unknown" and fails the BUY closed.
    withdrawals_enabled: bool | None = None


@dataclass(frozen=True)
class GuardResult:
    """The outcome of running all fifteen rails: `ok` iff `violations` is empty."""

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


def _utc_month_bounds(ts: int) -> tuple[int, int]:
    """Return `[start, end)` epoch seconds for the UTC calendar month containing `ts`."""
    dt = datetime.fromtimestamp(ts, tz=UTC)
    start = datetime(dt.year, dt.month, 1, tzinfo=UTC)
    end_year, end_month = (dt.year + 1, 1) if dt.month == 12 else (dt.year, dt.month + 1)
    end = datetime(end_year, end_month, 1, tzinfo=UTC)
    return int(start.timestamp()), int(end.timestamp())


def _monthly_buy_spend_usd(repo: Repository, now_ts: int) -> Decimal:
    """Sum of this (UTC) calendar month's BUY notional across all products, from the orders
    audit log -- rail 14's month-to-date figure."""
    start, end = _utc_month_bounds(now_ts)
    total = Decimal("0")
    for order in repo.get_orders(mode="live"):
        if order["side"] != Side.BUY.value or order["status"] not in _ACTIVE_ORDER_STATUSES:
            continue
        created_at = order.get("created_at")
        if created_at is None or not (start <= created_at < end):
            continue
        total += _order_notional(order)
    return total


def _is_business_day(year: int, month: int, day: int) -> bool:
    return datetime(year, month, day, tzinfo=UTC).weekday() < 5  # Mon-Fri, no holiday calendar


def _business_days_in_month(year: int, month: int) -> int:
    days_in_month = calendar.monthrange(year, month)[1]
    return sum(1 for day in range(1, days_in_month + 1) if _is_business_day(year, month, day))


def _business_days_elapsed(year: int, month: int, day: int) -> int:
    """Business days from the 1st of the month through `day`, inclusive."""
    return sum(1 for d in range(1, day + 1) if _is_business_day(year, month, d))


def check(intent: OrderIntent, repo: Repository, config: Config, now_ts: int) -> GuardResult:
    """Run all fifteen §14 (+ Issue #59, Task 4) hard rails against `intent`. Never short-circuits.

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

    # 4. Total open-exposure cap (§4.1).
    #
    # ⚠️ THIS IS A NOTIONAL CAP, NOT AN AT-RISK CAP (KB §83.3). An earlier comment here read
    # "sum of at-risk capital across all open positions", which the code does not do and never
    # did: it compares summed NOTIONAL against `max_exposure_usd`, so $5k behind a 2% stop and
    # $5k behind a 20% stop are treated identically. The rail is therefore VOLATILITY-BLIND.
    #
    # Not a defect to fix here. A true aggregate at-risk cap (`sum(qty * ATR) <= V% * equity`)
    # is INERT at one tranche per asset -- the 1% risk rail already pins the aggregate near 3%
    # -- so it belongs with concurrent slots / pyramiding, not before them. Fixing the COMMENT
    # matters regardless: a comment that overstates what a safety rail enforces is worse than
    # no comment.
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

    # 13. USDC-funding — a BUY may only spend an already-settled quote-currency balance, never
    #     a linked bank/ACH source. Fails closed: an unknown balance (`None`) vetoes the BUY.
    #     SELL is exempt -- it produces quote currency, it doesn't consume it (Issue #59).
    if is_buy:
        balance = intent.available_quote
        if balance is None:
            violations.append(
                f"usdc_funding: available {config.quote_currency} balance is unknown/"
                "unavailable -- failing closed, BUY vetoed"
            )
        elif balance <= 0:
            violations.append(
                f"usdc_funding: available {config.quote_currency} balance {balance} is not "
                "greater than 0"
            )
        elif balance < intent.notional:
            shortfall = intent.notional - balance
            violations.append(
                f"usdc_funding: available {config.quote_currency} balance {balance} is short "
                f"{shortfall} of the {intent.notional} order notional"
            )

    # 14. Monthly subscription-allowance — month-to-date live BUY spend + this order must not
    #     exceed the allowance derived from the venue's *attested* subscription record
    #     (`repo.get_broker_subscription`), read fresh on every call so an attestation takes
    #     effect on the very next order. Fails closed: unattested, suspect, lapsed, or overdue
    #     all fall back to `unsubscribed_allowance_usd` (default 0). DCA is NOT exempt -- it is
    #     exactly the recurring spend this rail exists to cap (Issue #59).
    if is_buy:
        record = repo.get_broker_subscription(DEFAULT_VENUE)
        unsubscribed = config.subscription.unsubscribed_allowance_usd

        if record is None:
            allowance: Decimal | None = unsubscribed
            degraded_reason = "no subscription has been attested"
            # No record means there is no record-level pacing to read -- the user's configured
            # pacing is the best available statement of intent (e.g. a raised
            # unsubscribed_allowance_usd paired with pacing="even_daily" should still be paced,
            # not given a flat, unpaced cap).
            pacing = config.subscription.pacing
        else:
            allowance = record.allowance_usd(now_ts, unsubscribed)
            pacing = record.pacing
            effective = record.effective_status(now_ts)
            overdue = record.attest_due_ts <= now_ts
            if overdue:
                log_event(
                    logger,
                    logging.WARNING,
                    "subscription.attestation_overdue",
                    venue=DEFAULT_VENUE,
                    attested_at=record.attested_at,
                    attest_due_ts=record.attest_due_ts,
                )
            if effective is SubscriptionStatus.ACTIVE:
                degraded_reason = ""
            elif record.status is SubscriptionStatus.LAPSED:
                # Report the more serious condition when a record is both LAPSED and overdue:
                # LAPSED is a definite statement the subscription ended, while overdue is merely
                # an unrefreshed assertion -- checked ahead of the (less serious) overdue branch.
                degraded_reason = "its subscription is lapsed"
            elif overdue:
                degraded_reason = "its attestation is overdue"
            else:
                degraded_reason = f"its subscription is {effective.value}"

        # An unlimited allowance (Premium, in force) has no cap to exceed, and pacing a cap that
        # does not exist is meaningless -- the rail simply does not apply.
        if allowance is not None:
            monthly_spend = _monthly_buy_spend_usd(repo, now_ts)
            projected_monthly = monthly_spend + intent.notional

            effective_cap = allowance
            pacing_note = ""
            if pacing == "even_daily":
                dt = datetime.fromtimestamp(now_ts, tz=UTC)
                biz_days_in_month = _business_days_in_month(dt.year, dt.month)
                biz_days_elapsed = _business_days_elapsed(dt.year, dt.month, dt.day)
                if biz_days_in_month > 0:
                    paced_cap = (allowance / biz_days_in_month) * biz_days_elapsed
                    if paced_cap < effective_cap:
                        effective_cap = paced_cap
                        pacing_note = (
                            f" (even_daily pacing: {biz_days_elapsed}/{biz_days_in_month} "
                            f"business days elapsed -> paced cap {paced_cap})"
                        )

            if projected_monthly > effective_cap:
                if degraded_reason:
                    # A user in this state is not over budget -- they have no budget. Telling
                    # them "0 exceeds 0" would be true and useless.
                    violations.append(
                        f"subscription_unattested: {DEFAULT_VENUE} cannot spend because "
                        f"{degraded_reason}, so its allowance is the unsubscribed default "
                        f"{unsubscribed}{pacing_note}. Run `keel subscription attest --venue "
                        f"{DEFAULT_VENUE} --tier <tier>` to restore it."
                    )
                else:
                    remaining = max(effective_cap - monthly_spend, Decimal("0"))
                    violations.append(
                        "monthly_subscription_allowance: month-to-date BUY spend "
                        f"{monthly_spend} + {intent.notional} = {projected_monthly} exceeds the "
                        f"allowance cap {effective_cap}{pacing_note} -- remaining allowance "
                        f"{remaining}"
                    )

    # 16. Consecutive-loss circuit breaker — a SEQUENCE breaker where rail 11 is a MAGNITUDE
    #     breaker: it detects that the edge may have stopped working BEFORE the drawdown
    #     accumulates, which is a cheap regime-degradation proxy needing no regime classifier.
    #     ENTRIES ONLY — a breaker that blocked exits would trap capital in a losing position,
    #     inverting its own purpose. DCA exempt (§12.6). The counter lives in the producer
    #     (`execution/streak.py`); this rail reads only the halt timestamp, so the
    #     threshold decision exists in exactly one place.
    if is_buy and not intent.is_dca:
        halt_until = int(repo.get_state("streak_halt_until", default=0) or 0)
        if now_ts < halt_until:
            violations.append(
                f"consecutive_loss_breaker: {config.money_mgmt.max_consecutive_losses} "
                f"consecutive losing trades tripped the breaker; new entries are halted for "
                f"another {halt_until - now_ts}s. Exits, stop-outs and DCA are unaffected. "
                f"Clear it early with `keel resume-entries`."
            )

    # 17. Withdrawal capability — a COMPLIANCE rail, not an operational one (§65.4).
    #     Ayub's constructive-possession test (`qabd`) has a live condition attached: possession
    #     holds only while "there is nothing to prevent the buyer from taking physical possession
    #     whenever he desires". An asset we cannot withdraw is an asset we may not validly
    #     POSSESS — so acquiring more of it is the thing to stop.
    #     ENTRIES ONLY, exactly like rails 11/16: existing holdings are already ours, and forcing
    #     a sale to "fix" a withdrawal freeze would be strictly worse than holding through it.
    #     Fails CLOSED on None, like rails 12/13 — silence is not evidence of possession.
    if is_buy:
        if intent.withdrawals_enabled is None:
            violations.append(
                "withdrawal_capability: UNKNOWN (no fresh attestation, or the broker did not "
                "report). Under §65.4 possession requires that nothing prevents withdrawal on "
                "demand; an unverified state is not evidence that it holds. Attest with "
                "`keel withdrawals attest` (exits and DCA-exempt paths are unaffected)."
            )
        elif intent.withdrawals_enabled is False:
            violations.append(
                "withdrawal_capability: withdrawals are suspended/restricted for this account. "
                "Under §65.4 an asset that cannot be withdrawn may not have been validly "
                "possessed, so new ENTRIES are halted. Existing holdings and exits are "
                "deliberately unaffected."
            )

    for violation in violations:
        log_event(
            logger,
            logging.INFO,
            "guards.check_failed",
            product=intent.product_id,
            side=intent.side,
            violation=violation,
        )

    return GuardResult(ok=not violations, violations=violations)
