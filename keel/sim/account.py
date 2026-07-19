"""A pure-`Decimal` simulation account ledger (Sim Task 2; reconciled by Issue #85).

`SimAccount` is a self-contained, read-only-of-the-broker stand-in for a live keel account: it
tracks cash, open positions, and cumulative contributions, and enforces the *spend-cap subset* of
`execution.guards.check` -- per-order, per-day, total-exposure, per-asset%, USDC-funding (rail 13),
and monthly-allowance (rail 14), plus rail 16 (the consecutive-loss circuit breaker, Task 4) --
before a candidate order is allowed to open. It never touches the live engine/executor/rails/
ledger modules and is never imported by them; the two are kept in parity by a dedicated test
(`tests/sim/test_account.py::test_parity_with_guards_check_*`) that builds equivalent
`Repository`/`Config` state and asserts `can_open` agrees with `guards.check` across a grid of
notionals spanning every cap boundary -- see the "sim/live divergence" note below for the one rail
where the two are now intentionally NOT compared.

Rail 16 parity: `SimAccount` has no `Repository`/`agent_state` of its own (by design -- it's a
pure ledger, not a state store), so it carries `streak_halt_until` and `consecutive_losses` as
plain instance attributes mirroring the live `agent_state` keys of the same names. `guards.check`
reads the live `streak_halt_until`; `can_open` reads this one. Both default to `0`, matching the
fail-inert-by-default posture: `now_ts < 0` is never true for a real timestamp.

`record_trade_outcome` is this module's PRODUCER for those attributes -- the sim-side twin of
`execution/streak.py`, which is live-side only (it writes through a `Repository`). It exists
because rail 16 ships DISABLED so its threshold can be set from a `keel simulate` sweep, and
without a producer here the sim could never halt: every `max_consecutive_losses` value returned a
byte-identical backtest, making the sweep a provable no-op. `sim/portfolio_sim.py` calls it on
every closed RULE position; `tests/sim/test_portfolio_sim.py::
test_sweeping_max_consecutive_losses_changes_the_backtest` is the test that holds that wiring in
place -- the `can_open`/`guards.check` parity test below is structural and would NOT catch its
loss (it proves the two agree when both are told the same thing, not that anything tells the sim).

Deliberately NOT enforced here (outside the spend-cap subset, per the plan): the halal allowlist,
min-move/anti-scalping, no-averaging-into-losers, no-stop-widening, sell-only-on-rule, the
account-drawdown breaker, correlation-adjusted sizing, and stale-data/kill-switch -- those rails
need state (an audit log, `agent_state`, a live feed) this pure ledger doesn't model. DCA is NOT
exempt from any cap enforced here, matching rail 14's explicit non-exemption for DCA spend.

Two separate position slots (Issue #85): a single per-asset RULE-trade slot (`positions`, one
open/close position per asset -- risk-defined entries from `pullback_continuation`,
`rsi_mean_reversion`, etc.) and a separate, accumulating DCA sleeve (`dca_positions`, one
weighted-average-cost lot per asset that only ever grows). Before this fix, DCA and rule trades
shared a single per-asset slot, so an asset accumulating DCA (which never signals an exit, by
design -- `strategy/rules/dca.py`) permanently froze that asset's rule slot. Both slots debit the
same `cash_usdc` and both are marked-to-market in `exposure_usd`/`mark_to_market`, and both count
against the SAME per-asset concentration cap (concentration risk doesn't care which order class
bought the exposure) -- but only the rule slot's position ever `close()`s; DCA lots accumulate via
`open(..., dca=True)` and are simply carried, never exited, by this ledger.

Bookkeeping notes:

- `can_open`'s spend-cap checks are against the *candidate* order's `notional` (as `guards.check`
  checks `intent.notional`), never against the fill-time economics (`fee_pct`/`slippage_pct`),
  which only affect `open`/`close`'s cash and P&L. This mirrors `guards.check` comparing a
  caller-supplied candidate notional against historical, already-recorded order notionals.
- Day/month VOLUME and per-asset/total exposure are derived from an append-only ledger of
  `(ts, notional)` opens AND closes (`_volume_log`) and the currently open positions' at-open
  notionals (`_position_notional` / `_dca_notional`), not from a live-recomputed counter -- so
  there is no separate "reset on rollover" step: `_day_volume`/`_month_volume` are always freshly
  windowed against `now_ts` using the *same* UTC month/day boundary helpers `execution.guards`
  uses (imported, not reimplemented), so a UTC day/month rollover is handled correctly whether or
  not `deposit` was called in between.
- Fees/slippage follow `strategy/backtest`'s convention: `entry_fill = price * (1 + slippage_pct)`,
  `exit_fill = price * (1 - slippage_pct)`, `fee = fill * qty * fee_pct` on each leg.

**Sim/live divergence, intentional and documented (Issue #85):** `_day_volume`/`_month_volume`
sum BOTH opens (buys) AND closes (sells) as trading VOLUME -- matching Coinbase One's actual
fee-free perk, which is a monthly *volume* allowance, not a buy-only spend cap.
`execution.guards.check` (rails 3/14, `_daily_spend_usd`/`_monthly_buy_spend_usd`) still counts
BUY notional only, and is deliberately left untouched by this issue (live executor/guards are a
separate, safety-reviewed follow-up). This means `SimAccount.can_open` and `guards.check` can
disagree specifically on the per-day/monthly-allowance rails once a SELL has occurred in the
sim's ledger -- every other rail (per-order, exposure, per-asset, USDC-funding) stays in full
parity regardless. The parity tests below deliberately keep their shared scenario buy-only so
they exercise genuine full-rail parity; `test_month_and_day_volume_include_sell_notional_*` in
`tests/sim/test_account.py` exercises the volume-vs-buy-only divergence directly.
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

SECONDS_PER_DAY = 86_400


@dataclass
class OpenPosition:
    """A currently open, filled position -- one per asset per slot (rule XOR DCA; no partial
    layering within a slot). A DCA lot's `entry_fill` is the running weighted-average cost basis
    across every accumulation buy; `stop` is always `None` for a DCA lot (no stop by design)."""

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
        self.dca_positions: dict[str, OpenPosition] = {}
        self.contributed: Decimal = Decimal("0")
        self.realized_pnl: Decimal = Decimal("0")

        # Rail 16 (consecutive-loss circuit breaker): the sim-side mirror of the live
        # `agent_state["streak_halt_until"]`/`["consecutive_losses"]` keys. Written by
        # `record_trade_outcome` -- see the module docstring's "Rail 16 parity" note.
        self.streak_halt_until: int = 0
        self.consecutive_losses: int = 0

        # Cap-arithmetic bookkeeping: an append-only log of every opened AND closed order's
        # *candidate* notional (for day/month VOLUME -- buys and sells both count, see module
        # docstring) and the at-open notional of each currently open position (for
        # exposure/per-asset), kept deliberately separate from fill-time economics. Rule-slot and
        # DCA-sleeve notionals are tracked in separate dicts but combined (`_asset_notional`,
        # `_total_notional`) for cap purposes -- concentration/exposure risk doesn't care which
        # order class bought the exposure.
        self._volume_log: list[tuple[int, Decimal]] = []
        self._position_notional: dict[str, Decimal] = {}
        self._dca_notional: dict[str, Decimal] = {}

    # -- deposits -----------------------------------------------------------------------------

    def deposit(self, amount: Decimal, now_ts: int) -> None:
        """Add `amount` to both cash and lifetime contributions.

        Day/month volume are always computed fresh against `now_ts` from `_volume_log` (see
        module docstring), so a UTC day/month rollover is already handled correctly without any
        explicit counter reset here.
        """
        self.cash_usdc += amount
        self.contributed += amount

    # -- combined (rule + DCA) notional, for cap purposes --------------------------------------

    def _asset_notional(self, asset: str) -> Decimal:
        """Combined rule-slot + DCA-sleeve notional currently open in `asset`."""
        return self._position_notional.get(asset, Decimal("0")) + self._dca_notional.get(
            asset, Decimal("0")
        )

    def _total_notional(self) -> Decimal:
        """Combined rule-slot + DCA-sleeve notional currently open across every asset."""
        return sum(self._position_notional.values(), Decimal("0")) + sum(
            self._dca_notional.values(), Decimal("0")
        )

    # -- spend-cap bookkeeping ------------------------------------------------------------------

    def _day_volume(self, now_ts: int) -> Decimal:
        start, end = _utc_day_bounds(now_ts)
        return sum(
            (notional for ts, notional in self._volume_log if start <= ts < end), Decimal("0")
        )

    def _month_volume(self, now_ts: int) -> Decimal:
        start, end = _utc_month_bounds(now_ts)
        return sum(
            (notional for ts, notional in self._volume_log if start <= ts < end), Decimal("0")
        )

    def _monthly_allowance_cap(self, config: Config, now_ts: int) -> Decimal:
        # The SIMULATOR's assumed allowance, deliberately from config and never from the
        # attested `broker_subscriptions` record: reading live state here would make backtests
        # non-reproducible. See the subscription design spec §6.1.
        allowance = config.subscription.assumed_free_volume_usd
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
        (matches rail 14). This is the hard safety veto -- callers (e.g. `sim.portfolio_sim`)
        that want to avoid tripping it should CLAMP a candidate notional down to
        `max_affordable_notional` first, not weaken this check."""
        reasons: list[str] = []

        # per-order $ cap (optional -- non-binding by default, see keel.config.Caps)
        if intent.notional > config.caps.max_per_order_usd:
            reasons.append(
                f"per_order_cap: notional {intent.notional} exceeds max_per_order_usd "
                f"{config.caps.max_per_order_usd}"
            )

        # per-day $ cap (VOLUME -- buys + sells, optional/non-binding by default)
        day_volume = self._day_volume(now_ts)
        projected_day = day_volume + intent.notional
        if projected_day > config.caps.max_per_day_usd:
            reasons.append(
                f"per_day_cap: today's volume {day_volume} + {intent.notional} = "
                f"{projected_day} exceeds max_per_day_usd {config.caps.max_per_day_usd}"
            )

        # total open-exposure cap (rule + DCA combined)
        total_exposure = self._total_notional()
        projected_exposure = total_exposure + intent.notional
        if projected_exposure > config.caps.max_exposure_usd:
            reasons.append(
                f"total_exposure_cap: open exposure {total_exposure} + {intent.notional} = "
                f"{projected_exposure} exceeds max_exposure_usd {config.caps.max_exposure_usd}"
            )

        # per-asset concentration cap (rule + DCA combined)
        per_asset_limit = config.caps.max_per_asset_pct * config.caps.max_exposure_usd
        asset_exposure = self._asset_notional(intent.asset)
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

        # monthly subscription-allowance (rail 14) -- DCA is NOT exempt. VOLUME (buys + sells);
        # see the module docstring's "sim/live divergence" note -- guards counts BUY-only.
        month_volume = self._month_volume(now_ts)
        effective_cap = self._monthly_allowance_cap(config, now_ts)
        projected_month = month_volume + intent.notional
        if projected_month > effective_cap:
            reasons.append(
                "monthly_subscription_allowance: month-to-date volume "
                f"{month_volume} + {intent.notional} = {projected_month} exceeds the "
                f"allowance cap {effective_cap}"
            )

        # consecutive-loss circuit breaker (rail 16) -- ENTRIES ONLY, DCA exempt, matching
        # `guards.check`'s `is_buy and not intent.is_dca` gate (every `can_open` candidate is
        # itself an entry, so `is_buy` is implicit here).
        if not intent.is_dca and now_ts < self.streak_halt_until:
            reasons.append(
                "consecutive_loss_breaker: new entries are halted for another "
                f"{self.streak_halt_until - now_ts}s (consecutive-loss circuit breaker); "
                "DCA is unaffected"
            )

        return (not reasons, reasons)

    def max_affordable_notional(
        self,
        asset: str,
        config: Config,
        now_ts: int,
        monthly_volume_cap: Decimal | None = None,
    ) -> Decimal:
        """The binding headroom for a new order in `asset` right now -- the same six caps
        `can_open` enforces, expressed as a single $ ceiling instead of a pass/fail, so a caller
        (`sim.portfolio_sim`) can CLAMP a risk-sized candidate notional down to what `can_open`
        would actually allow instead of rejecting the signal outright. Never negative -- returns
        `Decimal("0")` once any cap is already exhausted.

        `monthly_volume_cap`, if given, is an EXTERNAL fee-free monthly trading-volume ceiling
        (Issue #86's Coinbase One tier/fee analysis -- not one of `can_open`'s six caps), used
        only by `sim.portfolio_sim`'s RULE-slot clamp (a caller opening a position that WILL
        later close, unlike a single-leg DCA buy). Headroom is floored to HALF of whatever
        volume remains before `monthly_volume_cap` this UTC month -- reserving the other half
        for this same position's own eventual CLOSE leg, since Issue #85's volume convention
        counts both legs (buys+sells) but only the OPEN leg is clamped here; the close, when it
        happens, is never rejected (an open position must always be allowed to exit). This is a
        conservative approximation, not an exact guarantee, for a position whose exit price ends
        up far from its entry -- see `sim.portfolio_sim`'s `monthly_volume_cap` docstring.
        """
        headroom = self.cash_usdc  # rail 13: USDC-funding
        headroom = min(headroom, config.caps.max_per_order_usd)
        headroom = min(headroom, config.caps.max_per_day_usd - self._day_volume(now_ts))
        headroom = min(headroom, config.caps.max_exposure_usd - self._total_notional())
        per_asset_limit = config.caps.max_per_asset_pct * config.caps.max_exposure_usd
        headroom = min(headroom, per_asset_limit - self._asset_notional(asset))
        effective_monthly_cap = self._monthly_allowance_cap(config, now_ts)
        headroom = min(headroom, effective_monthly_cap - self._month_volume(now_ts))
        if monthly_volume_cap is not None:
            remaining = monthly_volume_cap - self._month_volume(now_ts)
            headroom = min(headroom, max(remaining, Decimal("0")) / 2)
        return max(headroom, Decimal("0"))

    def month_volume(self, now_ts: int) -> Decimal:
        """Public accessor for month-to-date trading volume (buys+sells) as of `now_ts` (Issue
        #86) -- callers outside this ledger (e.g. `sim.portfolio_sim`'s DCA-sleeve throttle) read
        this instead of the private `_month_volume` to check remaining headroom against an
        external monthly allowance."""
        return self._month_volume(now_ts)

    def monthly_volume(self) -> dict[int, Decimal]:
        """The full volume ledger (every opened AND closed order's notional -- see module
        docstring) aggregated by UTC calendar month, keyed by each month's start ts
        (`execution.guards._utc_month_bounds`). Feeds the Coinbase One tier/fee analysis
        (Issue #86, `sim.tiers`), which needs each month's trading volume to compute over-cap
        fees per month, not just a running total."""
        out: dict[int, Decimal] = {}
        for ts, notional in self._volume_log:
            month_start, _ = _utc_month_bounds(ts)
            out[month_start] = out.get(month_start, Decimal("0")) + notional
        return out

    # -- open / close: fill-time economics -----------------------------------------------------

    def open(
        self, intent: OpenIntent, fill_price: Decimal, now_ts: int, *, dca: bool = False
    ) -> None:
        """Open (or, for `dca=True`, accumulate into) the position in `intent.asset`, debiting
        cash for the slipped fill plus entry fee, and recording `intent.notional` against the
        day/month volume ledger and the per-asset/exposure cap bookkeeping.

        `dca=False` (default) replaces the single RULE-slot position in `intent.asset` (one
        position per asset, no partial layering -- unchanged pre-#85 behavior). `dca=True`
        accumulates into the separate DCA sleeve instead: a second (or Nth) DCA buy in the same
        asset ADDS to the existing lot's quantity and rolls its `entry_fill` into a running
        weighted-average cost basis, rather than replacing it -- DCA is scheduled accumulation,
        not a single round-trip position.
        """
        entry_fill = fill_price * (Decimal(1) + self.slippage_pct)
        entry_fee = entry_fill * intent.qty * self.fee_pct
        self.cash_usdc -= entry_fill * intent.qty + entry_fee

        if dca:
            existing = self.dca_positions.get(intent.asset)
            if existing is None:
                total_qty = intent.qty
                avg_entry_fill = entry_fill
                entry_ts = now_ts
            else:
                total_qty = existing.qty + intent.qty
                avg_entry_fill = (
                    existing.entry_fill * existing.qty + entry_fill * intent.qty
                ) / total_qty
                entry_ts = existing.entry_ts
            self.dca_positions[intent.asset] = OpenPosition(
                asset=intent.asset,
                qty=total_qty,
                entry_fill=avg_entry_fill,
                entry_ts=entry_ts,
                stop=None,
                rule_kind=intent.rule_kind,
            )
            self._dca_notional[intent.asset] = (
                self._dca_notional.get(intent.asset, Decimal("0")) + intent.notional
            )
        else:
            self.positions[intent.asset] = OpenPosition(
                asset=intent.asset,
                qty=intent.qty,
                entry_fill=entry_fill,
                entry_ts=now_ts,
                stop=intent.stop,
                rule_kind=intent.rule_kind,
            )
            self._position_notional[intent.asset] = intent.notional

        self._volume_log.append((now_ts, intent.notional))

    def close(self, asset: str, fill_price: Decimal, now_ts: int) -> Decimal:
        """Close the RULE-slot open position in `asset` (the DCA sleeve is never closed by this
        ledger -- see module docstring), crediting cash for the slipped fill net of exit fee,
        and return the realized P&L (net of both legs' fees). The (pre-slippage) exit notional
        (`fill_price * qty`) is recorded against the day/month VOLUME ledger too -- Coinbase
        One's fee-free allowance is trading volume, buys and sells both (Issue #85)."""
        position = self.positions.pop(asset)
        self._position_notional.pop(asset, None)

        exit_fill = fill_price * (Decimal(1) - self.slippage_pct)
        entry_fee = position.entry_fill * position.qty * self.fee_pct
        exit_fee = exit_fill * position.qty * self.fee_pct
        pnl = (exit_fill - position.entry_fill) * position.qty - entry_fee - exit_fee

        self.cash_usdc += exit_fill * position.qty - exit_fee
        self.realized_pnl += pnl
        self._volume_log.append((now_ts, fill_price * position.qty))
        return pnl

    # -- rail 16: the streak producer -----------------------------------------------------------

    def record_trade_outcome(
        self, pnl_net: Decimal, config: Config, now_ts: int, *, is_dca: bool
    ) -> None:
        """Fold one closed trade's realized, fee-net P&L into the consecutive-loss streak,
        arming `streak_halt_until` once the threshold is reached.

        The sim-side twin of `execution.streak.record_closed_trade`, and deliberately identical
        to it in every decision it makes -- `pnl_net >= 0` resets (so an exactly-flat scratch is
        NOT a loss), DCA is exempt in both directions (§12.6: it buys through drawdowns on a
        fixed budget, so neither its losses nor its wins may move a rule-trade streak), and the
        halt is armed only when `max_consecutive_losses > 0`. Where the live producer persists
        both counters through `Repository.set_state`, this keeps them as plain instance
        attributes: `SimAccount` has no `agent_state` by design (it's a ledger, not a state
        store), and a backtest's streak must not outlive its run.

        With the SHIPPED default of `max_consecutive_losses = 0` this is completely inert, which
        is the point -- the threshold is meant to be set from a sweep, and a sweep is only
        meaningful because this producer makes the sim actually halt.
        """
        if is_dca:
            return

        if pnl_net >= 0:
            self.consecutive_losses = 0
            return

        self.consecutive_losses += 1

        threshold = config.money_mgmt.max_consecutive_losses
        if threshold > 0 and self.consecutive_losses >= threshold:
            self.streak_halt_until = (
                now_ts + config.money_mgmt.streak_cooloff_days * SECONDS_PER_DAY
            )

    # -- reporting --------------------------------------------------------------------------------

    def exposure_usd(self, prices: dict[str, Decimal]) -> Decimal:
        """Mark every open position -- rule slot AND DCA sleeve -- to `prices` and sum:
        `Σ qty * price`."""
        rule_exposure = sum(
            (position.qty * prices[position.asset] for position in self.positions.values()),
            Decimal("0"),
        )
        dca_exposure = sum(
            (position.qty * prices[position.asset] for position in self.dca_positions.values()),
            Decimal("0"),
        )
        return rule_exposure + dca_exposure

    def mark_to_market(self, prices: dict[str, Decimal]) -> Decimal:
        """Total account equity: cash plus every open position (rule + DCA) marked to `prices`."""
        return self.cash_usdc + self.exposure_usd(prices)
