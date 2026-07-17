"""A pure-`Decimal` simulation account ledger (Sim Task 2).

`SimAccount` is a self-contained, read-only-of-the-broker stand-in for a live keel account: it
tracks cash, open positions, and cumulative contributions, and enforces the *spend-cap subset* of
`execution.guards.check` -- per-order, per-day, total-exposure, per-asset%, USDC-funding (rail 13),
and monthly-allowance (rail 14) -- before a candidate order is allowed to open. It never touches
the live engine/executor/rails/ledger modules and is never imported by them; the two are kept in
parity by a dedicated test (`tests/sim/test_account.py::test_parity_with_guards_check_*`) that
builds equivalent `Repository`/`Config` state and asserts `can_open` agrees with `guards.check`
across a grid of notionals spanning every cap boundary.

Deliberately NOT enforced here (outside the spend-cap subset, per the plan): the halal allowlist,
min-move/anti-scalping, no-averaging-into-losers, no-stop-widening, sell-only-on-rule, the
account-drawdown breaker, correlation-adjusted sizing, and stale-data/kill-switch -- those rails
need state (an audit log, `agent_state`, a live feed) this pure ledger doesn't model. DCA is NOT
exempt from any cap enforced here, matching rail 14's explicit non-exemption for DCA spend.

Bookkeeping notes:

- `can_open`'s spend-cap checks are against the *candidate* order's `notional` (as `guards.check`
  checks `intent.notional`), never against the fill-time economics (`fee_pct`/`slippage_pct`),
  which only affect `open`/`close`'s cash and P&L. This mirrors `guards.check` comparing a
  caller-supplied candidate notional against historical, already-recorded order notionals.
- Day/month spend and per-asset/total exposure are derived from an append-only ledger of
  `(ts, notional)` opens (`_buy_log`) and the currently open positions' at-open notionals
  (`_position_notional`), not from a live-recomputed counter -- so there is no separate "reset on
  rollover" step: `_day_spend`/`_month_spend` are always freshly windowed against `now_ts` using
  the *same* UTC month/day boundary helpers `execution.guards` uses (imported, not reimplemented),
  so a UTC day/month rollover is handled correctly whether or not `deposit` was called in between.
- Fees/slippage follow `strategy/backtest`'s convention: `entry_fill = price * (1 + slippage_pct)`,
  `exit_fill = price * (1 - slippage_pct)`, `fee = fill * qty * fee_pct` on each leg.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from keel.config import Config
from keel.execution.guards import (
    _business_days_elapsed,
    _business_days_in_month,
    _utc_day_bounds,
    _utc_month_bounds,
)


@dataclass
class OpenPosition:
    """A currently open, filled position -- one per asset (no partial layering)."""

    asset: str
    qty: Decimal
    entry_fill: Decimal
    entry_ts: int
    stop: Decimal | None
    rule_kind: str


@dataclass
class OpenIntent:
    """A sim-local candidate order (pre-cap, pre-fill) -- the `SimAccount` analog of
    `execution.guards.OrderIntent`, minus the fields guards needs for the non-spend-cap rails
    (`product_id`/`side`/`available_quote`) this account doesn't enforce."""

    asset: str
    qty: Decimal
    entry: Decimal
    stop: Decimal | None
    notional: Decimal
    is_dca: bool
    rule_kind: str


class SimAccount:
    """A pure-`Decimal` account ledger enforcing the spend-cap subset of `guards.check`."""

    def __init__(self, fee_pct: Decimal, slippage_pct: Decimal) -> None:
        self.fee_pct = fee_pct
        self.slippage_pct = slippage_pct

        self.cash_usdc: Decimal = Decimal("0")
        self.positions: dict[str, OpenPosition] = {}
        self.contributed: Decimal = Decimal("0")
        self.realized_pnl: Decimal = Decimal("0")

        # Cap-arithmetic bookkeeping: an append-only log of every opened order's *candidate*
        # notional (for day/month spend) and the at-open notional of each currently open position
        # (for exposure/per-asset), kept deliberately separate from fill-time economics.
        self._buy_log: list[tuple[int, Decimal]] = []
        self._position_notional: dict[str, Decimal] = {}

    # -- deposits -----------------------------------------------------------------------------

    def deposit(self, amount: Decimal, now_ts: int) -> None:
        """Add `amount` to both cash and lifetime contributions.

        Day/month spend are always computed fresh against `now_ts` from `_buy_log` (see module
        docstring), so a UTC day/month rollover is already handled correctly without any explicit
        counter reset here.
        """
        self.cash_usdc += amount
        self.contributed += amount

    # -- spend-cap bookkeeping ------------------------------------------------------------------

    def _day_spend(self, now_ts: int) -> Decimal:
        start, end = _utc_day_bounds(now_ts)
        return sum(
            (notional for ts, notional in self._buy_log if start <= ts < end), Decimal("0")
        )

    def _month_spend(self, now_ts: int) -> Decimal:
        start, end = _utc_month_bounds(now_ts)
        return sum(
            (notional for ts, notional in self._buy_log if start <= ts < end), Decimal("0")
        )

    def _monthly_allowance_cap(self, config: Config, now_ts: int) -> Decimal:
        allowance = config.subscription.monthly_allowance_usd
        if config.subscription.pacing != "even_daily":
            return allowance

        dt = datetime.fromtimestamp(now_ts, tz=UTC)
        biz_days_in_month = _business_days_in_month(dt.year, dt.month)
        biz_days_elapsed = _business_days_elapsed(dt.year, dt.month, dt.day)
        if biz_days_in_month == 0:
            return allowance
        paced_cap = (allowance / biz_days_in_month) * biz_days_elapsed
        return min(allowance, paced_cap)

    # -- can_open: the spend-cap subset of guards.check, never short-circuiting ------------------

    def can_open(self, intent: OpenIntent, config: Config, now_ts: int) -> tuple[bool, list[str]]:
        """Check `intent` against every spend cap, collecting *all* violations (no
        short-circuit), mirroring `execution.guards.check`. DCA is not exempt from any of these
        (matches rail 14)."""
        reasons: list[str] = []

        # per-order $ cap
        if intent.notional > config.caps.max_per_order_usd:
            reasons.append(
                f"per_order_cap: notional {intent.notional} exceeds max_per_order_usd "
                f"{config.caps.max_per_order_usd}"
            )

        # per-day $ cap
        day_spend = self._day_spend(now_ts)
        projected_day = day_spend + intent.notional
        if projected_day > config.caps.max_per_day_usd:
            reasons.append(
                f"per_day_cap: today's spend {day_spend} + {intent.notional} = "
                f"{projected_day} exceeds max_per_day_usd {config.caps.max_per_day_usd}"
            )

        # total open-exposure cap
        total_exposure = sum(self._position_notional.values(), Decimal("0"))
        projected_exposure = total_exposure + intent.notional
        if projected_exposure > config.caps.max_exposure_usd:
            reasons.append(
                f"total_exposure_cap: open exposure {total_exposure} + {intent.notional} = "
                f"{projected_exposure} exceeds max_exposure_usd {config.caps.max_exposure_usd}"
            )

        # per-asset concentration cap
        per_asset_limit = config.caps.max_per_asset_pct * config.caps.max_exposure_usd
        asset_exposure = self._position_notional.get(intent.asset, Decimal("0"))
        projected_asset_exposure = asset_exposure + intent.notional
        if projected_asset_exposure > per_asset_limit:
            reasons.append(
                f"per_asset_concentration_cap: {intent.asset} exposure "
                f"{projected_asset_exposure} exceeds {config.caps.max_per_asset_pct} of "
                f"max_exposure_usd ({per_asset_limit})"
            )

        # USDC-funding (rail 13): cash must be strictly positive AND cover the notional.
        if not (self.cash_usdc > 0 and self.cash_usdc >= intent.notional):
            reasons.append(
                f"usdc_funding: available cash {self.cash_usdc} does not cover notional "
                f"{intent.notional} (must be > 0 and >= notional)"
            )

        # monthly subscription-allowance (rail 14) -- DCA is NOT exempt.
        month_spend = self._month_spend(now_ts)
        effective_cap = self._monthly_allowance_cap(config, now_ts)
        projected_month = month_spend + intent.notional
        if projected_month > effective_cap:
            reasons.append(
                "monthly_subscription_allowance: month-to-date spend "
                f"{month_spend} + {intent.notional} = {projected_month} exceeds the "
                f"allowance cap {effective_cap}"
            )

        return (not reasons, reasons)

    # -- open / close: fill-time economics -----------------------------------------------------

    def open(self, intent: OpenIntent, fill_price: Decimal, now_ts: int) -> None:
        """Open (or replace) the position in `intent.asset`, debiting cash for the slipped fill
        plus entry fee, and recording `intent.notional` against the day/month spend ledger and
        the per-asset/exposure cap bookkeeping."""
        entry_fill = fill_price * (Decimal(1) + self.slippage_pct)
        entry_fee = entry_fill * intent.qty * self.fee_pct
        self.cash_usdc -= entry_fill * intent.qty + entry_fee

        self.positions[intent.asset] = OpenPosition(
            asset=intent.asset,
            qty=intent.qty,
            entry_fill=entry_fill,
            entry_ts=now_ts,
            stop=intent.stop,
            rule_kind=intent.rule_kind,
        )
        self._position_notional[intent.asset] = intent.notional
        self._buy_log.append((now_ts, intent.notional))

    def close(self, asset: str, fill_price: Decimal, now_ts: int) -> Decimal:
        """Close the open position in `asset`, crediting cash for the slipped fill net of exit
        fee, and return the realized P&L (net of both legs' fees)."""
        position = self.positions.pop(asset)
        self._position_notional.pop(asset, None)

        exit_fill = fill_price * (Decimal(1) - self.slippage_pct)
        entry_fee = position.entry_fill * position.qty * self.fee_pct
        exit_fee = exit_fill * position.qty * self.fee_pct
        pnl = (exit_fill - position.entry_fill) * position.qty - entry_fee - exit_fee

        self.cash_usdc += exit_fill * position.qty - exit_fee
        self.realized_pnl += pnl
        return pnl

    # -- reporting --------------------------------------------------------------------------------

    def exposure_usd(self, prices: dict[str, Decimal]) -> Decimal:
        """Mark every open position to `prices` and sum: `Σ qty * price`."""
        return sum(
            (position.qty * prices[position.asset] for position in self.positions.values()),
            Decimal("0"),
        )

    def mark_to_market(self, prices: dict[str, Decimal]) -> Decimal:
        """Total account equity: cash plus every open position marked to `prices`."""
        return self.cash_usdc + self.exposure_usd(prices)
