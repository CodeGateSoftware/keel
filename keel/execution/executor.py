"""The order executor (P3 Task 4) -- turns a `Signal` into a guarded live order.

`execute()` is the only path from a strategy `Signal` to a real order: it sizes the candidate
(`execution.sizing`), runs the eighteen un-overridable §14 hard rails (`execution.guards.check`)
**before** anything reaches the broker, previews the order, honors the confirm/autonomous mode gate,
places it, and writes a full audit trail to the `orders` table both before and after the broker
call (so a crash mid-placement, or a broker-side rejection, still leaves a record). No path in
this module calls `broker.place_order` (or even `broker.preview_order`) without `guards.check`
having passed first -- that ordering is safety-critical and is exercised directly by
`tests/execution/test_executor.py`'s `NoNetworkBroker` (raises on any call at all).

**Sizing.** ENTER signals size via `sizing.size` (fixed-fractional risk, off the setup's
entry/stop) for risk-defined rules, or `sizing.dca_size` (budget/price, no stop) for the DCA
order class (`setup.context["order_class"] == "dca"` or `["no_stop"]`, matching
`strategy/engine.py`'s own class test). `execution/guards.py` documents the same design choice
this module reuses: `config.caps.max_exposure_usd` stands in for account equity in
fixed-fractional sizing, since neither module has a separate equity oracle -- it is the funded
trading-capital ceiling (§2.8) `max_per_asset_pct` is already a fraction of.

**EXIT signals** carry no `setup` (`strategy/rules/base.Signal` docstring: `setup` is `None` for
EXIT/NONE) -- the position being closed is reconstructed from the orders audit log
(`_held_position`), the same source of truth `guards.py` uses for exposure/averaging. An EXIT
signal for a product with no recorded open position is a deliberate no-op (`ExecutionResult`
with `placed=False`, `vetoed_by=[]` -- there is nothing to veto, just nothing to sell).

**Exit bracket.** A filled entry that carries a stop *and* target (i.e. not DCA) automatically
gets an exchange-side exit bracket (`place_bracket`): ONE native Coinbase trigger-bracket
order carrying both the take-profit (`limit_price`) and the stop (`stop_trigger_price`), so the
exchange itself owns the race between them. An earlier design placed two independent SELL legs
and paired them client-side in `agent_state`; that required us to observe a fill and cancel the
survivor, and a missed fill left a live order able to sell an already-closed position. It also
committed a 1x position 2x, since both legs carried the full qty. The native bracket removes
both problems by construction. It still runs through `guards.check` like any other order
(un-overridable); a vetoed bracket is simply never placed and `place_bracket` returns `None`.

**Stop management -- IMPLEMENTED, TESTED, AND NOT CALLED ON THE LIVE PATH (#442; be not
misled).** `roll_to_break_even` / `trail_stop_atr` cancel the existing protective stop leg
and replace it at a new price, never widening it: both delegate to `_roll_stop`, which
refuses (returns `None`, leaving the existing stop in force) if the proposed new stop is
below the last-recorded `open_stop:<product_id>` -- the same "only ratchet toward profit"
invariant rail 9 enforces on entries, applied directly here since the replacement order's
own `guards.check` call (still mandatory) can't reuse rail 9 as-is: rail 9 is paired with
the min-move/anti-scalping rail, which has no meaning for a stop-replacement order that has
no separate entry price of its own. `scale_out` closes part of a position (a rule-driven
partial profit-take) through the same guard+preview+place+log pipeline as a plain SELL leg.
NONE of the three has a live caller: nothing in the agent cycle manages a stop after
placement (`agent._handle_exits` executes rule EXIT signals only), so reading this module
alone OVERSTATES what keel does -- live, a position's exits are exactly the entry-time
bracket above and a rule EXIT signal, nothing that ratchets. Why unwired, established in
#442: (1) ratchet-only is rail-9-safe BY CONSTRUCTION (`_roll_stop` refuses a widening
proposal before `guards.check` ever runs; pinned by `tests/execution/
test_executor.py::test_a_ratchet_only_trail_can_never_trip_rail_9`); (2) live wiring needs
a cancel-and-replace of the native bracket, and the broker port has NO bracket/OCO
`OrderSpec` kind -- the bracket reaches the venue only because this module bypasses the
port with a raw configuration dict -- so live stop management is split out to issue #502
rather than solved here; (3) the exit POLICY those primitives encode (the same
ratchet-only ATR trail and break-even roll) IS wired where exits are driven per bar: the
sim/backtest engines, via `strategy/exit_policy.py` and the per-family `trail_atr_mult` /
`be_roll_rr` params on `pullback_continuation` and `rsi_meanrev`. `turtle_breakout`
deliberately carries neither knob -- its real exit is the Donchian channel, and a trail
would cut the long winners the system exists to let run. `scale_out` stays unwired until
#502 also covers its two pinned prerequisites (bracket resize for a partial SELL, and a
`trade_outcomes` row so rail 16 does not count a scaled-out winner as a loss).

**USDC-funding balance (rail 13, Issue #59).** For a BUY `_build_intent` fetches the live
available balance of the PRODUCT's quote leg from `broker.get_balances()` and hands
it to `guards.check` via `OrderIntent.available_quote` -- guards itself has no broker access, by
design. This happens *before* `guards.check` runs (the balance is an input to the rail, not
something guarded itself), so it is the one broker call this module makes ahead of the guard
gate; `_fetch_available_quote` swallows any exception from the call and returns `None`, which
rail 13 then treats as fail-closed (vetoes the BUY) exactly like an unknown balance from a
missing quote-currency account. SELL intents never fetch a balance (the rail exempts them).

**Entry routing is unconditional market (#258, #260).** Every signal -- whatever its
`Setup.entry` encodes -- is routed as an immediate market order; the rule's entry price is
recorded on the order row as `expected_fill` and then not used to execute. #258 pinned that as
the faithful-engine decision; #260 records the cost (`pullback_continuation`, whose
`signal_candle.high + buffer_ticks` entry is a follow-through filter, took 124 trades
market-filled where the rule intended 58, at gross PF 0.7736 vs 0.9219) and deliberately
defers resting-order routing until a price-conditional rule earns it. What is NOT deferred is
visibility: `_warn_if_market_routing_overrides_entry` logs at WARNING whenever a routed entry
sits materially off the venue's own book, so the override is visible rather than silent.

**The routing-time max-spread entry gate (#350).** A live BUY whose previewed book is too
wide to enter is REFUSED after the preview and before the confirm gate/placement:
`(best_ask - best_bid) / mid` at or beyond `execution.max_entry_spread_pct` (default 0.005,
50bp -- #334's slippage cap as the anchor) refuses the order, and a preview with no readable
bid/ask fails closed with a distinct reason. It sits BESIDE the eighteen rails, not among
them: `guards.check` is broker-less by design, and the book exists only in the preview this
module just fetched -- the same preview #332's warning reads (`_preview_book`: one helper,
two consumers). BUY-only (exits must execute, like rail 17 halting entries not exits) and
live-only (paper fills are synthetic, see no book, and accrue no evidence about it).
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from keel_broker_api.results import CancelOutcome, Preview, coerce_cancel_outcome
from keel_core.products import quote_currency_of
from keel_core.telemetry import log_event, log_exception, log_venue_failure

from keel.config import Config
from keel.data.repository import Repository
from keel.execution import guards, sizing
from keel.execution.guards import OrderIntent
from keel.strategy.rules.base import Action, Signal
from keel.types import Side

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExecutionResult:
    """The outcome of `execute()` (or one of the management actions below).

    `vetoed_by` is non-empty when `guards.check` rejected the intent (the specific rail
    names, verbatim from `GuardResult.violations`) OR when the routing-time entry spread gate
    refused a live BUY (#350 -- the tokens `max_entry_spread` / `book_unreadable`, see
    `_entry_spread_gate`; not a `guards.check` rail, but reported the same way so a caller
    reading `vetoed_by` sees one legible shape for "this order was refused before
    placement"). A confirm-gate rejection or a broker-side failure leaves it `[]` and
    explains itself via `reason` instead.
    """

    placed: bool
    order_id: int | None
    vetoed_by: list[str]
    # Whatever `broker.preview_order` handed back, verbatim. A dict from the pre-port
    # `CoinbaseClient` today; a `Preview` once Phase B migrates the call. Anything reading money
    # out of this must branch on the shape -- see `ConfirmFn` below and `_run_order`'s `fee`.
    preview: Preview | dict[str, Any] | None
    reason: str
    # The local `orders.id` of the exit bracket this entry left resting, when one was placed.
    # `execute` places the bracket itself, so this is the ONLY way a caller can learn its id --
    # and `agent.run_once` needs it to point the new `positions` row at its bracket. Discarding
    # it (as this did until Task 3) left the tranche with no way to name its own protective
    # order, which is what made `roll_to_break_even`/`trail_stop_atr` unreachable by
    # construction. `None` when no bracket was placed OR when it was vetoed.
    bracket_order_id: int | None = None


#: The human confirm gate for `mode="confirm"`. Takes BOTH shapes on purpose: `broker` here is
#: still the pre-port `CoinbaseClient`, whose `preview_order` returns a dict, but the port's
#: `Preview` is what carries `synthetic` -- the flag that tells a human whether they are
#: approving a venue's quote or an estimate keel computed. A gate typed to `dict` alone has
#: nowhere to render that (issue #199), and one typed to `Preview` alone breaks the only venue
#: that trades today. Both, until Phase B makes `preview_order` return `Preview` everywhere.
ConfirmFn = Callable[[Preview | Mapping[str, Any]], bool]


# -- main entry point -----------------------------------------------------------------------


def execute(
    signal: Signal,
    broker: Any,
    repo: Repository,
    config: Config,
    mode: Literal["confirm", "autonomous"],
    confirm_fn: ConfirmFn | None = None,
    now_ts: int | None = None,
) -> ExecutionResult:
    """Turn `signal` into a guarded order: size -> guards (veto on any violation) -> preview ->
    confirm|autonomous -> place -> log (before and after).

    `mode="confirm"` requires `confirm_fn(preview) -> bool`; a missing or rejecting `confirm_fn`
    means the order is not placed (fails closed, never silently proceeds). `mode="autonomous"`
    places without a prompt but is *not* exempt from `guards.check` -- rails run before every
    order in every mode, un-overridable, per the main spec §14.
    """
    if now_ts is None:
        now_ts = int(time.time())

    intent = _build_intent(signal, broker, repo, config, now_ts)
    if intent is None:
        return ExecutionResult(
            placed=False,
            order_id=None,
            vetoed_by=[],
            preview=None,
            reason=f"no open {signal.product_id} position to exit",
        )

    if intent.side == Side.SELL and not _clear_resting_bracket(
        broker, repo, intent.product_id, now_ts
    ):
        return ExecutionResult(
            placed=False,
            order_id=None,
            vetoed_by=[],
            preview=None,
            reason=(
                f"could not cancel the resting exit bracket for {intent.product_id} -- "
                "refusing to place a SELL that would be rejected for insufficient funds, or "
                "would fill and leave a live bracket able to sell inventory we no longer hold"
            ),
        )

    result = _run_order(intent, broker, repo, config, mode, confirm_fn, now_ts)

    if (
        result.placed
        and intent.side == Side.BUY
        and not intent.is_dca
        and intent.stop is not None
        and signal.setup is not None
    ):
        bracket_order_id = place_bracket(
            broker,
            repo,
            config,
            product_id=intent.product_id,
            qty=intent.qty,
            stop=intent.stop,
            target=signal.setup.target,
            rule_name=signal.rule_name,
            now_ts=now_ts,
        )
        # Surfaced rather than discarded so `run_once` can point the tranche at its bracket.
        # See `ExecutionResult.bracket_order_id`.
        result = replace(result, bracket_order_id=bracket_order_id)

    return result


# -- intent construction ---------------------------------------------------------------------


#: The locally-recorded statuses in which an exit bracket is still RESTING at the exchange
#: (#446). `pending` is "placed, nothing observed yet"; `partially_filled` is "the venue has
#: begun executing it and the unfilled REMAINDER is still working". Both commit base currency
#: that a replacement order would double-commit, so BOTH cancel-before-place sites below
#: (`_clear_resting_bracket`, `_roll_stop`) must query both. Pre-#446 a partial stayed
#: `pending`, which made a single-status query correct by accident; the distinct partial state
#: reintroduced the two-status reality, and skipping either status leaves a live bracket
#: beside a SELL for the same inventory (base-locked rejection, or an oversell after the fact).
#: `execution.reconcile._POLLED_STATUSES` is this same tuple under its other name: the sweep
#: that OBSERVES partials and the cancels that CLEAR them must never drift apart.
RESTING_STATUSES = ("pending", "partially_filled")


def _clear_resting_bracket(broker: Any, repo: Repository, product_id: str, now_ts: int) -> bool:
    """Cancel any resting exchange-side exit bracket for `product_id`. `False` if one could not
    be cleared, in which case the caller MUST NOT place its SELL.

    `place_bracket` leaves a native trigger bracket committing the ENTIRE base position, so a
    voluntary rule exit selling the same inventory collides with it: on spot the base is locked
    and the SELL is rejected, so `position_rule` is never cleared, no outcome is recorded, and
    the agent retries the same doomed sell every cycle while the position rides a stale stop. If
    it DID fill, the still-live bracket could later sell inventory we no longer hold.

    A `partially_filled` bracket counts as resting here (#446): its unfilled remainder is
    working at the exchange exactly like a `pending` bracket's whole size, so leaving it live
    reproduces the same base-locked rejection (or post-fill oversell) this function exists to
    prevent. Pre-#446 the partial case was caught only because a partial stayed `pending`.

    This lives in `execute` rather than in `agent._handle_exits` so every SELL path -- the rule
    exit today, `scale_out` or any future one -- gets it by construction rather than by each
    caller remembering. Failing closed (refuse the exit) is right: an uncancellable bracket means
    we do not know what the exchange will do with that inventory, and adding a second order to
    that uncertainty is strictly worse than waiting a cycle.
    """
    rows: list[dict[str, Any]] = []
    for status in RESTING_STATUSES:
        rows.extend(repo.get_orders(mode="live", product_id=product_id, status=status))
    for row in sorted(rows, key=lambda r: r["id"]):
        if str(row["side"]).upper() != Side.SELL.value.upper():
            continue
        try:
            _cancel_at_exchange(broker, repo, row)
        except CancelPending:
            # NOT a failure, and logged at INFO so it does not read as one: the venue took the
            # cancel and settles it asynchronously. The exit still waits -- placing now would
            # commit inventory the venue may still hold -- but an operator reading this line
            # should see a cancel in flight, not a position at risk.
            log_event(
                logger,
                logging.INFO,
                "executor.bracket_cancel_pending",
                product=product_id,
                order_id=row["id"],
            )
            return False
        except CancelUnavailable:
            log_exception(
                logger,
                "executor.bracket_cancel_failed",
                product=product_id,
                order_id=row["id"],
            )
            return False
        repo.update_order(row["id"], status="canceled", updated_at=now_ts)
        log_event(
            logger,
            logging.INFO,
            "executor.resting_bracket_cleared",
            product=product_id,
            order_id=row["id"],
        )
    return True


def _is_dca_setup(context: dict[str, Any]) -> bool:
    return bool(context.get("no_stop")) or context.get("order_class") == "dca"


#: How long a withdrawal-capability attestation stays fresh (§65.4). Deliberately short: the
#: attestation is about the account's CURRENT state, and a freeze can appear at any time, so a
#: stale attestation is no better than none. 7 days.
WITHDRAWAL_ATTESTATION_TTL_SEC = 7 * 24 * 3600


def _withdrawals_enabled(repo: Any, now_ts: int) -> bool | None:
    """Rail 17's input: is the account currently in a withdrawable state (§65.4)?

    Read LIVE from the operator's attestation on every intent -- never cached -- so
    `keel withdrawals attest` (or a revocation) takes effect on the very next order, the same
    posture rail 14 takes with the subscription record.

    Returns `None` when there is no attestation or it has gone stale, which fails rail 17 closed.
    An expired attestation is treated as UNKNOWN rather than as `False`: the difference matters
    in the message the operator sees, and "nobody has checked recently" is not the same claim as
    "the broker says withdrawals are suspended".
    """
    try:
        attested_at = int(repo.get_state("withdrawals_attested_at", default=0) or 0)
        enabled = repo.get_state("withdrawals_enabled", default=None)
    except Exception:
        log_exception(logger, "executor.withdrawal_attestation_read_failed")
        return None

    if not attested_at or enabled is None:
        return None
    if now_ts - attested_at > WITHDRAWAL_ATTESTATION_TTL_SEC:
        return None
    return bool(enabled)


#: `agent_state` key prefix for a product's venue-declared `base_increment` (#516).
BASE_INCREMENT_PREFIX = "base_increment:"

#: How long a cached increment is trusted. Increments change rarely -- a venue re-scaling a
#: product is an announced event -- so this is long, and the cost of being wrong is bounded:
#: a stale increment is still a multiple the venue accepts unless it got FINER, and a finer one
#: only means we round a little more than needed. Matches rail 17's 7-day attestation TTL rather
#: than inventing a second cadence.
BASE_INCREMENT_TTL_SEC = 7 * 24 * 60 * 60


def _base_increment_for(
    broker: Any, repo: Repository, product_id: str, now_ts: int
) -> Decimal | None:
    """The venue's finest acceptable `base_size` for `product_id`, cached, or `None` if unknown.

    `None` means UNKNOWN and, for a SELL, means "send the quantity unquantized" -- NOT "refuse".
    See `_order_configuration`. This function therefore **never raises**: every failure path
    (no broker in paper mode, a venue error, a malformed or absent field) returns `None`, and the
    exit proceeds exactly as it did before #516.

    **Exactly ONE row is written per miss, deliberately, even though the response carries every
    product.** `Repository.set_state` commits per call, so caching all ~900 would mean ~900
    commits -- 900 fsyncs -- inside the order-placement path, which is the most latency-sensitive
    moment in the engine. The alternative it buys is a handful of extra `list_products` calls:
    one per allowlisted product per TTL, so roughly six a week at the current allowlist. Trading
    900 disk syncs during an order to save six network calls a week is the wrong way round.
    """
    key = f"{BASE_INCREMENT_PREFIX}{product_id}"
    cached = repo.get_state(key)
    if isinstance(cached, dict):
        fetched_at = cached.get("fetched_at")
        raw = cached.get("increment")
        if isinstance(fetched_at, int) and now_ts - fetched_at < BASE_INCREMENT_TTL_SEC:
            return _coerce_increment(raw)

    if broker is None:
        # Paper mode passes no broker; expected, not an error (same reasoning as
        # `_fetch_available_quote`).
        return None
    try:
        products = broker.list_products()
    except Exception:
        log_venue_failure(logger, "executor.base_increment_fetch_failed", product=product_id)
        return None

    for product in products or []:
        if not isinstance(product, dict) or product.get("product_id") != product_id:
            continue
        increment = _coerce_increment(product.get("base_increment"))
        if increment is not None:
            repo.set_state(key, {"increment": str(increment), "fetched_at": now_ts})
        return increment
    return None


def _coerce_increment(raw: object) -> Decimal | None:
    """A positive `Decimal` from the venue's string, or `None` -- never raises."""
    if raw is None:
        return None
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return value if value > 0 else None


def _fetch_available_quote(broker: Any, quote_currency: str | None) -> Decimal | None:
    """Live available balance of `quote_currency`, from the port's `get_balances()`.

    `quote_currency` is the **product's own settlement leg** (`BTC-USD` -> `USD`), not
    `config.quote_currency`: the currency an order spends is a property of the product. Checking
    the configured currency instead could report a healthy balance for a currency the order never
    touches, letting rail 13 PASS an order the account cannot fund -- precisely the "never draw
    from a linked bank/ACH source" case the rail exists to prevent.

    `None` on any failure -- a broker error, a malformed response, an unresolvable product id, or
    simply no account for that currency -- so rail 13 fails closed rather than guessing. This is
    the one broker call `execute()` makes *before* `guards.check` runs: it's an input the rail
    needs, not itself something the guard gate protects (no funds move, no order is placed).
    """
    if not quote_currency:
        return None
    if broker is None:
        # Paper mode passes no broker. That is not an error and must not be logged as one --
        # an ERROR per paper entry would fill the operator's log with noise about a condition
        # that is expected and already handled (rail 13 is skipped offline).
        return None
    try:
        balances = broker.get_balances()
    except Exception:
        # `log_venue_failure`, not `log_exception`: an unreachable venue outside a trade cycle
        # is a dashboard balance refresh on a sleeping laptop, and this line is the SECOND
        # record for that one failure (`cb_client.get_accounts` logs it first) -- two full
        # tracebacks per poll, every 30s, for as long as the machine is offline. Inside a cycle
        # it escalates back to ERROR on its own: there it means rail 13 failed closed and an
        # order did not go out. Kept as its own event rather than dropped because it carries
        # `quote_currency`, and because a non-Coinbase broker may not log anything itself.
        log_venue_failure(logger, "executor.quote_fetch_failed", quote_currency=quote_currency)
        return None

    # ONE shape, since #524. This probed for two -- a dict key or an attribute, `currency` or
    # `.currency`, `available_balance` or `.available_balance` -- because it did not know whether
    # it held the pre-port `CoinbaseClient` or a port adapter. `CoinbaseClient.get_balances` now
    # answers in the port's `Balance` type as well, so there is one question and one answer, and
    # the fork that existed only to bridge them is gone.
    for balance in balances or []:
        if balance.currency.upper() == quote_currency.upper():
            return balance.available

    return None


def _build_intent(
    signal: Signal,
    broker: Any,
    repo: Repository,
    config: Config,
    now_ts: int,
    equity_override: Decimal | None = None,
) -> OrderIntent | None:
    """Size `signal` into an `OrderIntent`, or `None` for an EXIT with nothing open to sell.

    `equity_override`, when given, replaces `config.caps.max_exposure_usd` as the equity input
    to fixed-fractional sizing on the ENTER/non-DCA path -- used by the paper-trading enter path
    (Task 6) to size off the paper account's real equity instead of the live exposure cap. `None`
    (the default) preserves the live-path behavior exactly.
    """
    if signal.action == Action.ENTER:
        setup = signal.setup
        if setup is None:
            raise ValueError("execute: an ENTER signal must carry a setup to size an order from")

        is_dca = _is_dca_setup(setup.context)
        if is_dca:
            # CAREFUL: the live path sizes DCA from the CONFIG's `dca.budget_usd`, and ignores
            # the RULE's own `budget_usd` / the `size_usd` the rule computed from it (which is
            # sitting right there in `setup.context`). That is deliberate -- the config is the
            # operator-facing dial and a rule row is not reviewed on every deploy -- but it means
            # the two can disagree silently, and a rule row saying 25 while the config says 50
            # spends 50. It surprised us once; do not assume the rule's number is what moves.
            # The account simulator (`sim/portfolio_sim.py`) prefers `context["size_usd"]` and
            # only falls back to this config value, so a divergence also makes the sim and the
            # live path model different position sizes. Keep rule, config and
            # `deploy/live-rules.json` in agreement.
            qty = sizing.dca_size(config.dca.budget_usd, setup.entry)
            stop = None
        else:
            equity = (
                equity_override if equity_override is not None else config.caps.max_exposure_usd
            )
            qty = sizing.size(equity, config.risk_pct, setup.entry, setup.stop)
            stop = setup.stop

        # The PRODUCT's quote leg -- what this order actually spends.
        available_quote = _fetch_available_quote(broker, quote_currency_of(signal.product_id))
        withdrawals = _withdrawals_enabled(repo, now_ts)

        return OrderIntent(
            product_id=signal.product_id,
            side=Side.BUY,
            qty=qty,
            entry=setup.entry,
            stop=stop,
            notional=sizing.spend(qty, setup.entry),
            is_dca=is_dca,
            rule_kind=signal.rule_name,
            rule_id=signal.rule_id,
            available_quote=available_quote,
            withdrawals_enabled=withdrawals,
        )

    # EXIT: sell the currently held position, reconstructed from the orders audit log.
    qty, avg_cost = _held_position(repo, signal.product_id)
    if qty <= 0:
        return None

    entry = signal.setup.entry if signal.setup is not None else avg_cost
    return OrderIntent(
        product_id=signal.product_id,
        side=Side.SELL,
        qty=qty,
        entry=entry,
        stop=None,
        notional=sizing.spend(qty, entry),
        is_dca=False,
        rule_kind=signal.rule_name,
        rule_id=signal.rule_id,
        # #516. Fetched here, like `available_quote` above, so `_order_configuration` stays a
        # pure function of the intent. `None` is fine and means "send unquantized".
        base_increment=_base_increment_for(broker, repo, signal.product_id, now_ts),
    )


def _held_position(repo: Repository, product_id: str) -> tuple[Decimal, Decimal]:
    """Net held qty + average cost basis for `product_id`, from filled live orders."""
    buy_qty = Decimal("0")
    buy_cost = Decimal("0")
    sell_qty = Decimal("0")
    for order in repo.get_orders(mode="live", product_id=product_id, status="filled"):
        price = order.get("actual_fill") or order.get("limit_price") or order.get("expected_fill")
        qty = order["qty"] or Decimal("0")
        if order["side"] == Side.BUY.value:
            buy_qty += qty
            buy_cost += qty * (price or Decimal("0"))
        elif order["side"] == Side.SELL.value:
            sell_qty += qty

    net_qty = buy_qty - sell_qty
    avg_cost = (buy_cost / buy_qty) if buy_qty > 0 else Decimal("0")
    return (net_qty if net_qty > 0 else Decimal("0")), avg_cost


# -- shared guard -> preview -> confirm/autonomous -> place -> log pipeline ------------------------


def _run_order(
    intent: OrderIntent,
    broker: Any,
    repo: Repository,
    config: Config,
    mode: str,
    confirm_fn: ConfirmFn | None,
    now_ts: int,
    order_configuration: dict[str, Any] | None = None,
) -> ExecutionResult:
    guard_result = guards.check(intent, repo, config, now_ts)
    if not guard_result.ok:
        log_event(
            logger,
            logging.INFO,
            "executor.order_vetoed",
            product=intent.product_id,
            side=intent.side,
            violations=guard_result.violations,
        )
        return ExecutionResult(
            placed=False,
            order_id=None,
            vetoed_by=guard_result.violations,
            preview=None,
            reason="vetoed by guards: " + "; ".join(guard_result.violations),
        )

    if order_configuration is None:
        # A size that cannot be expressed in the venue's units REFUSES THIS ORDER, and refuses
        # only this order (#513). `agent.run_once` does not wrap its `executor.execute` call, so
        # letting `SizePrecisionUnavailable` escape here would abort the whole cycle and skip
        # every product after this one -- turning a single unserialisable order into a silent
        # outage. Refuse-and-log is what every other unknown in this engine does; a raise here
        # would be the one that behaves differently.
        try:
            order_configuration = _order_configuration(intent)
        except SizePrecisionUnavailable as exc:
            log_event(
                logger,
                logging.WARNING,
                "executor.size_precision_unavailable",
                product=intent.product_id,
                side=intent.side,
                notional=str(intent.notional),
                detail=str(exc),
            )
            return ExecutionResult(
                placed=False,
                order_id=None,
                vetoed_by=[],
                preview=None,
                reason=f"size precision unavailable: {exc}",
            )
    try:
        preview = broker.preview_order(intent.product_id, intent.side, order_configuration)
    except Exception:
        log_exception(
            logger, "executor.preview_failed", product=intent.product_id, side=intent.side
        )
        raise
    log_event(
        logger,
        logging.INFO,
        "executor.order_previewed",
        product=intent.product_id,
        side=intent.side,
        notional=intent.notional,
        mode=mode,
    )

    # #260: ROUTING AN ENTRY AS MARKET OVERRIDES WHATEVER CONDITION THE RULE ENCODED IN ITS
    # ENTRY PRICE. That is deliberate twice over. It is the behavior #258's faithful-engine
    # decision pinned -- live and the simulator agree that every signal becomes an immediate
    # market order, and the rule's own entry is recorded as `expected_fill` and then not used
    # to execute. And it is deliberately unchanged here, because #260's full remediation
    # (resting limit/stop orders, reconciliation across cycles, a cancel/replace policy) is
    # deferred until a price-conditional rule earns it -- the only rule whose entry currently
    # encodes a condition (`pullback_continuation`) is independently measured dead, and
    # upgrading money-moving order routing to rescue it is a bad trade. What is NOT deferred
    # is visibility: the landmine is the NEXT rule, which would be silently mis-executed the
    # same way. So the moment the venue's own book -- the `best_ask` in the preview just
    # fetched, no extra call -- says the intended entry is materially off the market, say so
    # at WARNING, before the confirm gate and before placement.
    _warn_if_market_routing_overrides_entry(intent, preview, order_configuration)

    # #350: THE ROUTING-TIME MAX-SPREAD ENTRY GATE. A live BUY whose book -- read from the
    # same preview the warning above just consumed -- is too wide (or unreadable) is refused
    # HERE, before the confirm gate and before placement. See `_entry_spread_gate` for the
    # full rationale; the ordering (warning first, gate second) is deliberate and pinned by
    # #332's tests.
    spread_refusal = _entry_spread_gate(intent, preview, config.execution.max_entry_spread_pct)
    if spread_refusal is not None:
        return ExecutionResult(
            placed=False,
            order_id=None,
            vetoed_by=[spread_refusal.veto],
            preview=preview,
            reason=spread_refusal.reason,
        )

    if mode == "confirm":
        approved = confirm_fn(preview) if confirm_fn is not None else False
        if not approved:
            log_event(
                logger,
                logging.INFO,
                "executor.confirm_declined",
                product=intent.product_id,
                side=intent.side,
                confirm_fn_present=confirm_fn is not None,
                approved=approved,
            )
            return ExecutionResult(
                placed=False,
                order_id=None,
                vetoed_by=[],
                preview=preview,
                reason="rejected at confirm gate",
            )
    elif mode != "autonomous":
        raise ValueError(f"execute: unknown mode {mode!r} -- must be 'confirm' or 'autonomous'")

    order_id = repo.insert_order(_order_row(intent, mode, now_ts))

    try:
        place_result = broker.place_order(intent.product_id, intent.side, order_configuration)
    except Exception:
        log_exception(
            logger,
            "executor.place_failed",
            product=intent.product_id,
            side=intent.side,
            order_id=order_id,
        )
        raise
    success = bool(place_result.get("success"))
    status = _initial_status(order_configuration) if success else "rejected"
    # `fee` was previously left NULL forever: nothing else in the live path ever wrote it, so
    # `streak.record_closed_trade` was always handed `fees=0` and every `pnl_net` was GROSS.
    # That defeats rail 16 precisely where it matters -- fees dominate small moves, so a trade
    # that is up gross and down net was recorded as a WIN and reset the loss counter.
    #
    # The PREVIEWED commission, and `actual_fill` the EXPECTED price -- the only figures
    # available at placement time. Both are upgraded to the exchange's observed values a few
    # lines below (`_upgrade_to_observed_economics`) for an immediate fill, and by
    # `execution.reconcile` for an order that fills later. These remain the fallback when the
    # status endpoint is unavailable.
    fee = preview.get("commission_total") if isinstance(preview, dict) else None
    repo.update_order(
        order_id,
        status=status,
        actual_fill=intent.entry if status == "filled" else None,
        fee=fee if status == "filled" else None,
        raw_response=json.dumps(place_result, default=str),
        updated_at=now_ts,
    )

    if success and status == "filled":
        _upgrade_to_observed_economics(broker, repo, order_id, place_result, now_ts, intent)

    if not success:
        log_event(
            logger,
            logging.INFO,
            "executor.order_rejected",
            product=intent.product_id,
            side=intent.side,
            order_id=order_id,
            error=place_result.get("error"),
        )
        return ExecutionResult(
            placed=False,
            order_id=order_id,
            vetoed_by=[],
            preview=preview,
            reason=f"broker rejected order: {place_result.get('error')}",
        )

    # `open_stop`/`open_target` are written by `place_bracket` ONLY, once the exchange has
    # actually accepted the bracket that establishes them. Writing `open_stop` here (before the
    # bracket is placed, and regardless of whether it is then vetoed) asserted a protective stop
    # that might not exist, and never wrote its `open_target` partner -- which later made
    # `_roll_stop` refuse with "no open_target recorded". The two are a pair; one writer.

    log_event(
        logger,
        logging.INFO,
        "executor.order_placed",
        product=intent.product_id,
        side=intent.side,
        order_id=order_id,
        status=status,
    )
    return ExecutionResult(
        placed=True, order_id=order_id, vetoed_by=[], preview=preview, reason="placed"
    )


def _upgrade_to_observed_economics(
    broker: Any,
    repo: Repository,
    order_id: int,
    place_result: dict[str, Any],
    now_ts: int,
    intent: OrderIntent | None = None,
) -> None:
    """Replace an immediately-filled order's ESTIMATED economics with the exchange's observed ones.

    A market order is marked `filled` at placement, so it never appears in
    `get_orders(status="pending")` and `execution.reconcile` never sees it. Without this, rail 16
    would count two different kinds of number: a bracket exit carrying the observed
    `average_filled_price`/`total_fees`, and a voluntary rule exit carrying the expected price
    and the PREVIEWED commission. A breaker swept on one definition and enforced on the other is
    miscalibrated by construction.

    Fails SOFT, unlike the cancel path: the order is already placed and we already hold a usable
    estimate, so a missing or broken status endpoint keeps the estimate rather than aborting the
    cycle. This is a refinement of a number, not a safety gate.
    """
    get_order = getattr(broker, "get_order", None)
    if get_order is None:
        return
    native_id = place_result.get("order_id")
    if not native_id:
        return
    try:
        observed = get_order(native_id)
    except Exception:
        log_exception(logger, "executor.observed_economics_unavailable", order_id=order_id)
        return

    fill = observed.get("average_filled_price")
    fees = observed.get("total_fees")
    if not fill or fill <= 0:
        return
    repo.update_order(order_id, actual_fill=fill, fee=fees, updated_at=now_ts)
    _record_observed_fill_quantity(repo, order_id, observed, intent, now_ts)
    _log_intent_divergence(order_id, intent, fill)


def _record_observed_fill_quantity(
    repo: Repository,
    order_id: int,
    observed: dict[str, Any],
    intent: OrderIntent | None,
    now_ts: int,
) -> None:
    """Record the venue-observed `filled_size` on an immediately-filled order (#446).

    A market IOC that only partly filled has its remainder cancelled at the venue, so this
    observation IS final -- but `qty` still says the ordered size, and everything sized from the
    order (the exit bracket placed next, the tranche the ledger opens) assumes it all filled.
    That mismatch is the oversized-bracket condition: a bracket able to sell more than is held.

    The WARNING is entry-only: its advice is about the ENTRY's exit bracket being placed for
    the ordered size, and an immediately-filled market SELL exit takes this same path. Firing
    entry wording on an exit would point an operator at a bracket this side never places --
    the exit-side over-booking is #502's to flag. The observation itself is recorded for
    BOTH sides: `filled_quantity` is what actually executed, whatever the order's direction.

    DELIBERATELY detect-and-surface only. Resizing the bracket means either amending a live
    native trigger-bracket or cancel-and-replace, and the broker port carries no bracket/OCO
    kind at all (#502) -- the live bracket already bypasses the port as a raw dict. Auto-cancelling
    a protective order on the strength of a snapshot that may still be settling is a wrong
    auto-action on live money; a loud warning is the safe half, and it is what this does.
    """
    filled = observed.get("filled_size")
    if not filled or filled <= 0:
        return
    row = repo.get_order(order_id) or {}
    ordered = intent.qty if intent is not None else row.get("qty")
    if ordered is None or ordered <= 0:
        return
    repo.update_order(order_id, filled_quantity=filled, updated_at=now_ts)
    side_value = (intent.side.value if intent is not None else row.get("side")) or ""
    if side_value.upper() != Side.BUY.value:
        # An exit partial stays silent (above); a side we cannot determine at all cannot
        # claim to be an entry either, so it stays silent too.
        return
    if filled < ordered:
        log_event(
            logger,
            logging.WARNING,
            "executor.entry_partially_filled",
            order_id=order_id,
            product=intent.product_id if intent is not None else None,
            filled=str(filled),
            ordered=str(ordered),
            shortfall=str(ordered - filled),
            detail=(
                "the venue executed only part of this order -- its bracket is placed for the "
                "ordered size and may be rejected or oversized for what is actually held. "
                "Cancel and re-place the bracket at the filled size, or verify the remainder "
                "at the venue; the automated resize policy is #502"
            ),
        )


def _log_intent_divergence(order_id: int, intent: OrderIntent | None, realized: Any) -> None:
    """Report how far the achieved fill sat from the price the RULE asked for.

    Both numbers were already persisted on the order row -- `expected_fill` at placement and
    `actual_fill` from the venue -- and nothing compared them. That gap is exactly how #257 went
    unnoticed: the executor places MARKET orders (`order_type="market"`, `limit_price=None`), so a
    rule's `Setup.entry` is recorded and then not used to execute. For a rule entering at the
    signal-bar close (`turtle_breakout`, `rsi_meanrev`) that is nearly free; for one whose entry
    encodes a CONDITION (`pullback_continuation` uses `signal_candle.high + buffer_ticks` to demand
    follow-through) production silently takes trades the rule meant to decline (#260).

    Logged UNCONDITIONALLY rather than past a threshold. A basis-point threshold that is right for
    BTC is wrong for a thin book -- the per-asset liquidity model that would set it now exists
    (#259, `strategy/backtest.slippage_for_quote_volume`) but lives on the RESEARCH side; the
    live path has no such statistic in hand at fill time, and gating on the cheap half behind
    the expensive half was declined when this shipped. This follows what #247 did with the fee
    rate: make the number visible first, act on it second. `divergence_bps` is signed, so
    direction is legible without recomputing it: positive means the fill came in ABOVE the
    rule's intended entry.

    Never raises. This is telemetry attached to an order that has already been placed and settled;
    a formatting problem here must not fail a cycle.
    """
    if intent is None:
        return
    try:
        expected = Decimal(str(intent.entry))
        actual = Decimal(str(realized))
        if expected <= 0:
            return
        divergence_bps = (actual - expected) / expected * Decimal(10_000)
    except (InvalidOperation, TypeError, ValueError):
        log_exception(logger, "executor.intent_divergence_uncomputable", order_id=order_id)
        return

    log_event(
        logger,
        logging.INFO,
        "executor.intent_divergence",
        order_id=order_id,
        product=intent.product_id,
        intent_entry=str(expected),
        realized_fill=str(actual),
        divergence_bps=f"{divergence_bps:.2f}",
    )


#: The routing-time entry-override visibility threshold, in basis points (#260).
#:
#: A VISIBILITY threshold, not a correctness one: crossing it changes no order, only whether
#: the operator is told. Anchored in this repo's own cost model, where a fill is priced at a
#: 1.2% taker fee per leg (`strategy/backtest.TAKER_FEE_PCT`) plus a slippage FLOOR of 5bp
#: (`strategy/backtest.SLIPPAGE_FLOOR_PCT`; #259 scales it up to a 50bp cap on thin books --
#: so "10x the slippage assumption" below holds at the liquid end and narrows toward the cap,
#: where a firing is all the more truthful). Against that, a deviation of a few bp is
#: microstructure -- the
#: drift any enter-at-close rule (`turtle_breakout`, `rsi_meanrev`) accumulates by routing one
#: cycle after its signal bar -- while tens of bp means the rule's entry encodes a CONDITION:
#: `pullback_continuation` enters at `signal_candle.high + buffer_ticks`, which sits above
#: the market by however much follow-through the rule demands, and THAT gap is what market
#: routing silently removes (#260 measured it: 124 trades taken where the rule intended 58).
#: 50bp sits between the two: 10x the slippage assumption, so ordinary conditions do not
#: trip it, yet small enough that any deliberate entry condition -- at least a fraction of
#: a bar's range from spot, by construction -- does. A genuine >50bp gap-up between an
#: enter-at-close rule's signal and the next cycle's ask CAN exceed the line in volatile
#: stretches; that firing is truthful (the fill really is that far off intent) and
#: informative, not spurious. This is a VISIBILITY threshold, not a correctness one.
#: (Its 50bp is COINCIDENTALLY equal to #259's slippage CAP -- different constants, different
#: modules, no coupling; do not retune one on the other's reasoning.)
#: The comparison is strictly greater: a deviation exactly at the line is "at", not
#: "beyond", and logs nothing.
ENTRY_OVERRIDE_WARN_BP = Decimal("50")


def _preview_book(preview: Preview | dict[str, Any]) -> tuple[Decimal | None, Decimal | None]:
    """The venue's book out of a preview response, as `(best_bid, best_ask)`, each field read
    INDEPENDENTLY and safely, in whichever of the preview's two shapes.

    One helper, two consumers (#350): #332's entry-override warning needs only the ask, and
    the routing-time max-spread gate needs both sides plus their midpoint. Both shapes
    already cross this module (`ConfirmFn`'s docstring explains why they coexist): the
    pre-port `CoinbaseClient.preview_order` dict, which maps `best_bid`/`best_ask` to
    `Decimal`s, and the port's `Preview`, whose Coinbase adapter carries the same book as
    strings inside `detail`.

    Each side is `None` when THE VENUE returned no usable value for that field -- absent key,
    non-numeric string, or a non-finite/non-positive number -- a degraded response, not an
    error. Per-field independence is load-bearing: the warning's contract (#332) is ask-only,
    so a book with a readable ask but no bid must still hand the warning its reference while
    telling the spread gate (which refuses on a half-readable book) that it cannot compute.
    Nothing downstream may compute against a guessed side.

    `is_finite()` is checked FIRST, deliberately outside any try: `Decimal('NaN') > 0`
    RAISES InvalidOperation, and a venue string of "nan" parses into exactly that
    (`cb_client` does `Decimal(value)` on venue strings with no finiteness check, so the
    input is reachable). A non-finite side is a degraded preview, not a routing failure.
    """
    raw: dict[str, Any] = {}
    if isinstance(preview, Mapping):
        raw = {"best_bid": preview.get("best_bid"), "best_ask": preview.get("best_ask")}
    else:
        detail = getattr(preview, "detail", None)
        raw = detail if detail is not None else {}

    def _side(key: str) -> Decimal | None:
        value = raw.get(key)
        if value is None:
            return None
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return None
        return parsed if parsed.is_finite() and parsed > 0 else None

    return _side("best_bid"), _side("best_ask")


def _preview_best_ask(preview: Preview | dict[str, Any]) -> Decimal | None:
    """The venue's best ask out of a preview response, in whichever of its two shapes.

    A thin consumer of `_preview_book` (above): same shapes, same per-field safety, ask only.
    """
    return _preview_book(preview)[1]


def _warn_if_market_routing_overrides_entry(
    intent: OrderIntent,
    preview: Preview | dict[str, Any] | None,
    order_configuration: dict[str, Any] | None = None,
) -> None:
    """WARNING, at routing time, when a BUY's intended entry is materially off the market.

    #260's minimum viable mitigation. Every entry is routed `market_market_ioc` (#258's
    faithful-engine decision), so a rule whose `Setup.entry` encodes a condition has that
    condition bypassed in production -- `pullback_continuation` demands follow-through via
    `signal_candle.high + buffer_ticks`, and the faithful measurement showed live taking 124
    trades the rule meant to decline at gross PF 0.7736 (vs 0.9219 intended). This reclaims
    only the VISIBILITY, the same principle as #247 printing the fee rate: the operator can
    see, order by order, which rule's design the routing overrode.

    The market reference is the venue's own `best_ask` from the preview `_run_order` JUST
    fetched -- the price a market BUY actually pays, drawn from the one book quote already in
    the hot path (no new broker call; a mid would misstate the gap by half the spread either
    way). Below `ENTRY_OVERRIDE_WARN_BP` nothing is logged: a warning that fires every
    order is a warning nobody reads.

    Scoped to BUYs on the market configuration only. SELL intents (exits, brackets, stop
    rolls) either carry no entry condition or hand their prices to the venue verbatim, and a
    caller passing its own non-market `order_configuration` -- a future resting order out of
    #260's remediation plan -- is not on the override path at all. Never raises: telemetry
    must not be able to fail a routing. `deviation_bps` is signed, positive meaning the rule
    intended to enter ABOVE the market (follow-through demanded, pullback's case), negative
    below it (a dip the market has not offered) -- the OPPOSITE sense of
    `executor.intent_divergence`'s `divergence_bps`, which measures the venue's fill against
    the intent; for one overridden trade the two readings have opposite signs, and a
    dashboard must not average across them.
    """
    if preview is None or intent.side != Side.BUY:
        return
    if order_configuration is None:
        # Same resolution `_run_order` performs: no explicit configuration means the default
        # routing, which today is always market.
        try:
            order_configuration = _order_configuration(intent)
        except SizePrecisionUnavailable:
            # This function is a DIAGNOSTIC (it records how far the venue's book sat from the
            # intent). An order whose size cannot be serialised has already been refused
            # upstream, so there is nothing to measure and nothing to report -- and a diagnostic
            # must never be the thing that raises out of the order path.
            return
    config_type = next(iter(order_configuration), "")
    if not config_type.startswith("market_"):
        return
    ref = _preview_best_ask(preview)
    if ref is None:
        return
    try:
        expected = Decimal(str(intent.entry))
    except (InvalidOperation, TypeError, ValueError):
        return
    if not expected.is_finite() or expected <= 0:
        return
    # The arithmetic stays INSIDE a try, matching `_log_intent_divergence`: `is_finite()`
    # admits extreme exponents (a rule bug like 1E+999999999 parses and compares fine), and
    # Decimal division/multiplication on such magnitudes raises ArithmeticError -- which
    # telemetry must swallow, never propagate into the routing it observes.
    try:
        deviation_bps = (expected - ref) / ref * Decimal(10_000)
    except ArithmeticError:
        return
    if abs(deviation_bps) <= ENTRY_OVERRIDE_WARN_BP:
        return
    log_event(
        logger,
        logging.WARNING,
        "executor.entry_override_market_routed",
        rule=intent.rule_kind,
        product=intent.product_id,
        expected_fill=str(expected),
        market_ref=str(ref),
        market_ref_source="preview_best_ask",
        deviation_bps=f"{deviation_bps:.2f}",
        threshold_bps=f"{ENTRY_OVERRIDE_WARN_BP:.2f}",
        detail=(
            "the rule's conditional entry price was OVERRIDDEN -- entries are always routed "
            "as market orders (#258), so the condition this rule encoded in its entry price "
            "was bypassed and the order is going out at the venue's price instead (#260)"
        ),
    )


# -- #350: the routing-time max-spread entry gate -------------------------------------------------


#: The `ExecutionResult.vetoed_by` token recorded when a live BUY is refused because the
#: previewed book's spread is at/beyond `execution.max_entry_spread_pct`. Deliberately the
#: same "one legible token" shape `GuardResult.violations` uses for rail vetoes, so a caller
#: (or operator) reading `vetoed_by` cannot confuse a gate refusal with a rail violation.
SPREAD_GATE_VETO = "max_entry_spread"

#: The same, for the fail-closed case: the preview carried no readable bid/ask, so the spread
#: is not "too wide" but UNKNOWN -- a different fact, reported differently on purpose.
SPREAD_GATE_BOOK_UNREADABLE_VETO = "book_unreadable"


@dataclass(frozen=True)
class _SpreadGateRefusal:
    """Why `_entry_spread_gate` refused a BUY: the `vetoed_by` token plus the human sentence
    for `ExecutionResult.reason` -- one return value so the two can never disagree."""

    veto: str
    reason: str


def _entry_spread_gate(
    intent: OrderIntent,
    preview: Preview | dict[str, Any] | None,
    max_entry_spread_pct: Decimal,
) -> _SpreadGateRefusal | None:
    """Refuse a live BUY whose previewed book is too wide to enter (#350). `None` = proceed.

    **What it decides.** For a BUY on the live path, `(best_ask - best_bid) / mid` at or
    beyond `execution.max_entry_spread_pct` (default 0.005 = 50bp) refuses the order BEFORE
    the confirm gate and placement. The anchor is #334's backtest slippage cap
    (`strategy.backtest.SLIPPAGE_CAP_PCT`): the backtest never assumes more than 50bp of
    per-leg slippage even on the thinnest book, so a spread AT the cap has already consumed
    the model's entire worst-case cost estimate and the taker fee rides outside the model --
    the fill economics are materially worse than anything the rule was measured on. The
    comparison is `>=`, the fail-closed side of the line, UNLIKE #332's visibility-only
    strictly-greater: at the threshold the spread alone equals the model's worst case, which
    is already too wide to enter on this reasoning.

    **Where it sits, and why.** AFTER `guards.check` and AFTER the preview: guards are
    broker-less by design (this module's docstring), so the book -- which only
    `broker.preview_order` returns -- cannot reach a `guards.check` rail. The gate is a
    routing-time check BESIDE the eighteen rails, not a numbered rail, and it consumes the
    SAME preview #332's `_warn_if_market_routing_overrides_entry` reads (one helper,
    `_preview_book`, two consumers). It runs after that warning so the warning's position --
    pinned by #332's tests -- is unchanged; on a wide book both facts are true at routing
    time (the entry was market-routed, AND the book is too wide), and the refusal event below
    is the terminal record. Paper mode never runs it at all: `_paper_enter` fills
    synthetically without a preview, so the paper-hourly profile accrues NO evidence about
    this gate -- which is why it ships before any live resumption rather than being validated
    on paper first.

    **SELLs are never gated** (`intent.side != Side.BUY` returns immediately): exits, exit
    brackets, stop rolls and scale-outs must execute -- the same principle that makes rail 17
    halt entries, not exits. A spread gate that trapped an exit would strand a position in
    exactly the book conditions the rule said to leave.

    **Fail-closed on an unreadable book.** A preview with no readable bid AND ask (missing
    keys, NaN, non-numeric, non-finite, non-positive, or a spread whose arithmetic
    overflows -- the extreme-exponent hazard #336 taught the warning about, refused rather
    than swallowed here because this is a money gate) is refused with the DISTINCT
    `book_unreadable` token: "cannot know" is a different fact from "too wide", and a gate
    that guessed a spread from half a book would be a gate that sometimes trades on fiction.
    The real venue's preview carries both sides for market orders (`cb_client.preview_order`
    maps `best_bid`/`best_ask` to `Decimal`), so an unreadable book on the live path means a
    degraded response -- exactly the moment not to spend.

    Every BUY routes market today (#258), so "every live BUY" and "every market-routed live
    BUY" are the same set; if #260's remediation ever lands resting BUY orders, revisit the
    scope -- a resting limit does not cross the spread it sits inside.
    """
    if intent.side != Side.BUY:
        return None
    if preview is None:
        # Unreachable from `_run_order` (it just previewed), but the function stays honest
        # standalone: no preview, no book, fail closed.
        return _book_unreadable_refusal(intent, "the preview response was empty")

    bid, ask = _preview_book(preview)
    if bid is None or ask is None:
        return _book_unreadable_refusal(
            intent,
            f"no readable {'best_bid' if bid is None else 'best_ask'} in the preview response",
        )
    # The arithmetic stays INSIDE a try, matching `_warn_if_market_routing_overrides_entry`:
    # `is_finite()` admits extreme exponents (1E+999999999 parses and compares fine), and
    # Decimal add/div on such magnitudes raises Overflow -- an ArithmeticError. Telemetry
    # swallows that (#336); a money gate refuses on it: an uncomputable spread is an
    # unreadable book, not a pass.
    try:
        mid = (bid + ask) / Decimal(2)
        spread_pct = (ask - bid) / mid
    except ArithmeticError:
        return _book_unreadable_refusal(
            intent, "spread uncomputable (extreme magnitudes in the book)"
        )
    if spread_pct < max_entry_spread_pct:
        return None
    log_event(
        logger,
        logging.WARNING,
        "executor.entry_spread_refused",
        rule=intent.rule_kind,
        product=intent.product_id,
        side=intent.side.value,
        best_bid=str(bid),
        best_ask=str(ask),
        mid=str(mid),
        spread_pct=str(spread_pct),
        threshold_pct=str(max_entry_spread_pct),
        veto=SPREAD_GATE_VETO,
        detail=(
            "refused at routing: the live book's spread alone is at/beyond "
            "execution.max_entry_spread_pct, so the fill would cost more than the worst "
            "per-leg cost the backtest ever models (#334's slippage cap) -- entries into "
            "this book wait for it to tighten (#350)"
        ),
    )
    return _SpreadGateRefusal(
        veto=SPREAD_GATE_VETO,
        reason=(
            f"refused by the routing-time entry spread gate: spread {spread_pct} of mid "
            f"{mid} is at/beyond execution.max_entry_spread_pct {max_entry_spread_pct}"
        ),
    )


def _book_unreadable_refusal(intent: OrderIntent, why: str) -> _SpreadGateRefusal:
    """The fail-closed arm of `_entry_spread_gate`, logged loudly: an unreadable book is a
    DEGRADED venue response, and an operator seeing repeated refusals here needs to know it
    is the preview shape that changed, not the market."""
    log_event(
        logger,
        logging.WARNING,
        "executor.entry_book_unreadable",
        rule=intent.rule_kind,
        product=intent.product_id,
        side=intent.side.value if isinstance(intent.side, Side) else str(intent.side),
        veto=SPREAD_GATE_BOOK_UNREADABLE_VETO,
        detail=(
            f"refused at routing: {why} -- the spread is UNKNOWN, not merely wide, and a "
            f"live BUY must not be sized against a book it cannot read (#350)"
        ),
    )
    return _SpreadGateRefusal(
        veto=SPREAD_GATE_BOOK_UNREADABLE_VETO,
        reason=f"refused by the routing-time entry spread gate: {why}",
    )


def _order_row(intent: OrderIntent, mode: str, now_ts: int) -> dict[str, Any]:
    # Routed MARKET unconditionally (#258's faithful-engine decision): `expected_fill` below
    # records the rule's intended entry even though execution ignores it -- the override
    # #260's routing-time warning (`_warn_if_market_routing_overrides_entry` in `_run_order`)
    # exists to make visible rather than silent.
    return dict(
        mode="live",
        product_id=intent.product_id,
        side=intent.side.value if isinstance(intent.side, Side) else intent.side,
        order_type="market",
        qty=intent.qty,
        limit_price=None,
        status="pending",
        fee=None,
        expected_fill=intent.entry,
        actual_fill=None,
        raw_response=None,
        # NOTE: rows written before 2026-07-21 carry `confirmation='bypass'` for what is now
        # called `'autonomous'`. Nothing reads this column back -- it is an audit trail only --
        # so the rows are deliberately left as-written rather than rewritten by a migration.
        confirmation=mode,
        rule_id=intent.rule_id,
        created_at=now_ts,
        updated_at=now_ts,
    )


class SizePrecisionUnavailable(RuntimeError):
    """No quote increment is known for this product, so no size can be safely serialised (#513).

    Raised rather than guessing a precision. An order whose size cannot be expressed in the
    venue's units is not an order that should be sent with a plausible-looking number instead.
    """


def _order_configuration(intent: OrderIntent) -> dict[str, dict[str, str]]:
    """Serialise the order's size at the VENUE's precision, not the engine's (#513).

    Everything upstream computes in full `Decimal` precision -- `sizing.size()` returns
    `(equity * risk_pct) / stop_distance`, which is essentially never round -- and `str(Decimal)`
    faithfully emits every digit of it. Coinbase answers `INVALID_SIZE_PRECISION` to anything
    finer than the product's increment, which is how the first live `turtle_breakout` entry was
    rejected (order 3, 2026-08-22, `quote_size: "23.00803473938010547532738517"`).

    DCA never tripped this only because its `budget_usd` is a round constant: `"50.000...0"` is
    26 decimal places too, and the venue accepts it because the VALUE is exactly 50.

    **SELL is quantized too (#516), but its UNKNOWN case is the opposite of BUY's, deliberately.**

    | increment | BUY | SELL |
    |---|---|---|
    | known | quantize down, send | quantize down, send |
    | unknown | REFUSE | send as-is, log |

    A refused BUY costs nothing -- the opportunity passes and no position is affected. A refused
    SELL strands a position that wanted to exit. Sending full precision at least *sometimes*
    works (a round quantity is accepted), so refusing would replace "sometimes exits" with
    "never exits" and make the engine worse than before the fix. **Do not "fix" this asymmetry
    into consistency.**

    Down, not nearest, on both sides: selling slightly less than held leaves dust, while selling
    more is rejected for insufficient funds anyway.
    """
    if intent.side == Side.BUY:
        increment = sizing.quote_increment_for(intent.product_id)
        if increment is None:
            raise SizePrecisionUnavailable(
                f"no quote increment known for {intent.product_id!r} -- refusing to guess a "
                "size precision for a live order"
            )
        notional = sizing.quantize_down(intent.notional, increment)
        if notional <= 0:
            raise SizePrecisionUnavailable(
                f"{intent.product_id!r} notional {intent.notional} quantizes to {notional} at "
                f"increment {increment} -- refusing to send a zero-size order"
            )
        return {"market_market_ioc": {"quote_size": str(notional)}}
    return {"market_market_ioc": {"base_size": str(_sell_base_size(intent))}}


def _sell_base_size(intent: OrderIntent) -> Decimal:
    """The SELL quantity at the venue's precision, or unchanged if we cannot know it (#516).

    Never raises and never returns zero-or-less: both would strand an exit, and this function's
    whole contract is that it can only ever make an exit MORE likely to be accepted.
    """
    increment = intent.base_increment
    if increment is None or increment <= 0:
        log_event(
            logger,
            logging.INFO,
            "executor.base_increment_unknown",
            product=intent.product_id,
            qty=str(intent.qty),
            detail="sending base_size unquantized -- refusing an exit is worse than imprecision",
        )
        return intent.qty
    quantized = sizing.quantize_down(intent.qty, increment)
    if quantized <= 0:
        # The whole position is smaller than one increment -- dust the venue cannot express. Send
        # the original and let the venue answer; suppressing the order would silently retire a
        # holding keel still believes it has, and an audited rejection beats a silent no-op.
        log_event(
            logger,
            logging.WARNING,
            "executor.base_size_quantizes_to_zero",
            product=intent.product_id,
            qty=str(intent.qty),
            increment=str(increment),
        )
        return intent.qty
    return quantized


def _initial_status(order_configuration: dict[str, Any]) -> str:
    """A market (IOC) order fills immediately; a limit/stop-limit order rests as `pending` on
    the exchange until a later fill event. `execution.reconcile`, run at the top of every
    cycle, observes that fill and marks it `filled` with the observed price and fees."""
    config_type = next(iter(order_configuration), "")
    return "filled" if config_type.startswith("market_") else "pending"


class CancelUnavailable(RuntimeError):
    """Raised when a resting order cannot be cancelled AT THE EXCHANGE.

    Never downgrade this to a no-op. Both cancel sites used to do
    `cancel = getattr(broker, "cancel_order", None)` and skip when absent -- and at the time the
    real client had no `cancel_order` at all, only the test fakes did, so in production the
    cancel was always skipped while the row was still marked `canceled`, leaving a LIVE resting
    SELL on the exchange that our own records said was gone.

    `CoinbaseClient.cancel_order` exists now, but the guard still matters and now covers a
    second case: it returns `False` when the exchange REFUSES a cancel (already filled, unknown
    id), and a refusal recorded as a success is the same lie by a different route. The
    `keel-broker-api` port and the Coinbase adapter still have no cancel method.

    Failing loudly is the safe direction: our state must never claim a cancel that did not
    happen. A caller that cannot tolerate the raise must reconcile with the exchange, not
    swallow it.
    """


class CancelPending(CancelUnavailable):
    """The venue ACCEPTED the cancel and has not settled it yet (#412).

    A SUBCLASS, deliberately, so every existing `except CancelUnavailable` keeps catching this
    and the control flow is provably unchanged by this distinction. What changes is only what the
    caller can say about it -- and that matters, because the two are not the same event and were
    being reported as though they were.

    Robinhood's cancel endpoint answers `200` with the order still `open`; the matching engine
    settles it about a second later. Under the old boolean contract that arrived as "the exchange
    did not confirm cancellation -- it may still be live", which told an operator a position was
    at risk when the cancel had in fact landed.

    It is still not safe to ACT on: until the venue settles it, the order can consume inventory,
    and the next thing both cancel sites do is place another order against that same inventory.
    So this still stops the caller. The reconciliation poll at the top of the next cycle
    (`keel.execution.reconcile.reconcile_open_orders`) reads the terminal state from the venue,
    which is where establishing it belongs -- not on an exit path that would have to sleep.
    """


def _cancel_at_exchange(broker: Any, repo: Repository, order_row: dict[str, Any]) -> None:
    """Cancel `order_row` at the exchange, or raise. Never marks local state on failure.

    The order of operations is the whole point: the exchange is the source of truth, so the
    cancel must SUCCEED before any caller records it. Every failure mode -- no `cancel_order` on
    the broker, no broker-side id to name, or the call itself raising -- propagates.
    """
    native_id = _native_order_id(order_row)
    if native_id is None:
        raise CancelUnavailable(
            f"order {order_row.get('id')} has no broker-side id in its placement response, so "
            "it cannot be cancelled at the exchange -- refusing to record it as canceled"
        )

    cancel = getattr(broker, "cancel_order", None)
    if cancel is None:
        raise CancelUnavailable(
            f"broker {type(broker).__name__} exposes no cancel_order, so resting order "
            f"{native_id} cannot be cancelled -- refusing to record it as canceled while it is "
            "still live at the exchange"
        )

    # The RETURN VALUE is the confirmation, not the absence of an exception. Coinbase's
    # batch_cancel answers per order, so a refused cancel (already filled, unknown id) comes back
    # `success: false` on a 200. Discarding it recorded a cancel that never happened -- the exact
    # failure this module exists to prevent.
    #
    # `coerce_cancel_outcome` also absorbs an adapter still written against the older boolean
    # contract, mapping `True`/`False`/anything-else to CONFIRMED/REFUSED/UNKNOWN. Only CONFIRMED
    # lets the caller proceed, which is the same bar `is not True` set -- so no adapter, old or
    # new, can turn an unconfirmed cancel into a permitted one.
    outcome = coerce_cancel_outcome(cancel(native_id))
    if outcome.settled:
        return
    if outcome is CancelOutcome.ACCEPTED:
        raise CancelPending(
            f"exchange accepted the cancellation of {native_id} but has not settled it yet -- "
            "waiting rather than placing against inventory it may still hold; the next "
            "reconciliation poll will read the terminal state"
        )
    if outcome is CancelOutcome.REFUSED:
        raise CancelUnavailable(
            f"exchange REFUSED to cancel {native_id} (already filled, already terminal, or an "
            "id it never issued) -- refusing to record it as canceled while it may still be live"
        )
    raise CancelUnavailable(
        f"exchange said nothing usable about cancelling {native_id} -- refusing to record it as "
        "canceled while it may still be live"
    )


def _native_order_id(order_row: dict[str, Any]) -> str | None:
    """The broker-native order id for a repo order row, read back out of the `place_order`
    response JSON stashed in `raw_response` -- used to cancel a specific broker order."""
    raw = order_row.get("raw_response")
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data.get("order_id")


def _bracket_order_configuration(
    qty: Decimal, target: Decimal, stop: Decimal, base_increment: Decimal | None = None
) -> dict[str, dict[str, str]]:
    """Coinbase's NATIVE trigger bracket: ONE order carrying both exit prices.

    `limit_price` is the take-profit and `stop_trigger_price` the stop-loss. The exchange owns
    the race between them, which is the entire reason for using it: the previous design placed
    two independent SELL legs and paired them client-side via `oco_sibling:` state, so a fill we
    failed to observe left the other leg live and able to sell an already-closed position. That
    whole failure mode does not exist here -- there is no sibling to cancel.

    It also fixes an inventory bug in that design: both legs were sized at the FULL qty, so a
    1x position was committed 2x. On spot the second leg should simply be rejected for
    insufficient funds.

    `trigger_bracket_gtc` is exactly what `RESTClient.trigger_bracket_order_gtc` builds, so this
    reaches it through the `place_order`/`create_order` path we already use -- no new broker API
    surface, no new transport method.
    """
    # 516: the protective legs carry a `base_size` too, and had the identical full-precision
    # defect as a plain SELL. Same rule, same asymmetry: quantize DOWN when the increment is
    # known, send unchanged when it is not. A bracket that the venue refuses leaves the position
    # unprotected, so this path must never become more likely to fail than it is today.
    size = (
        qty
        if base_increment is None or base_increment <= 0
        else _floor_or_original(qty, base_increment)
    )
    return {
        "trigger_bracket_gtc": {
            "base_size": str(size),
            "limit_price": str(target),
            "stop_trigger_price": str(stop),
        }
    }


def _floor_or_original(qty: Decimal, increment: Decimal) -> Decimal:
    """`qty` floored to `increment`, or `qty` unchanged if that would be zero-or-less."""
    quantized = sizing.quantize_down(qty, increment)
    return quantized if quantized > 0 else qty


# -- exit bracket ------------------------------------------------------------------------------

#: `agent_state` key prefix for a bracket that was ATTEMPTED and refused, holding the levels the
#: retry needs (`{"stop", "target", "qty"}`).
#:
#: ⚠️ Deliberately NOT `open_stop:`/`open_target:`. Those mean "this is resting at the exchange"
#: -- rail 9 reads `open_stop` as its no-widening reference and `_roll_stop` re-places from the
#: pair -- so writing them for an order that does not exist would tell rail 9 a stop is protecting
#: a position when none is, and would let a later roll "replace" a bracket that was never there.
#: The retry still needs the levels, because `_rebracket_or_escalate` refuses to invent them
#: ("silently re-risk the position on a level no rule produced"), so they get a key of their own.
UNBRACKETED_PREFIX = "unbracketed:"


def place_bracket(
    broker: Any,
    repo: Repository,
    config: Config,
    product_id: str,
    qty: Decimal,
    stop: Decimal,
    target: Decimal,
    rule_name: str,
    now_ts: int,
) -> int | None:
    """Place the exchange-side exit bracket for an open long position, or `None` if vetoed.

    ONE native trigger-bracket order (see `_bracket_order_configuration`), so the exchange owns
    the stop-vs-target race and the position is committed exactly once. It runs through
    `guards.check` like any other order (un-overridable).

    Two pieces of state are recorded: `open_stop:<product_id>` is rail 9's no-widening reference
    for future entries and rolls, and `open_target:<product_id>` is needed because a single order
    now carries both prices -- rolling the stop means re-placing the bracket, and the target is
    no longer recoverable from a separate leg.
    """
    intent = OrderIntent(
        product_id=product_id,
        side=Side.SELL,
        qty=qty,
        entry=stop,
        stop=None,
        # The trigger price again, this time where rail 9 can read it. `stop` stays None so rail
        # 7 does not measure a 0% entry-to-stop move and veto the bracket (issue #206).
        protective_stop=stop,
        notional=sizing.spend(qty, stop),
        is_dca=False,
        rule_kind=rule_name,
    )
    result = _run_order(
        intent,
        broker,
        repo,
        config,
        "autonomous",
        None,
        now_ts,
        order_configuration=_bracket_order_configuration(
            qty, target, stop, _base_increment_for(broker, repo, product_id, now_ts)
        ),
    )
    if not result.placed:
        # The entry has ALREADY filled by the time we get here, so this is a real position with
        # no stop at the exchange -- not a trade that simply did not happen. Record the levels so
        # `reconcile.reconcile_unbracketed_positions` can retry next cycle from the numbers the
        # rule actually produced; without them the retry has nothing to place and the position
        # stays naked until a human intervenes (issue #195).
        repo.set_state(
            f"{UNBRACKETED_PREFIX}{product_id}",
            {"stop": stop, "target": target, "qty": qty},
        )
        log_event(
            logger,
            logging.WARNING,
            "executor.bracket_not_placed",
            product=product_id,
            reason=result.reason,
            vetoed_by=result.vetoed_by,
        )
        return None

    repo.set_state(f"open_stop:{product_id}", stop)
    repo.set_state(f"open_target:{product_id}", target)
    # Retire the retry trigger, if this call WAS the retry. Left set, the sweep would re-place a
    # bracket the position already holds on every later cycle -- rejected for insufficient funds,
    # since the resting bracket already commits the inventory.
    repo.set_state(f"{UNBRACKETED_PREFIX}{product_id}", None)
    log_event(
        logger,
        logging.INFO,
        "executor.bracket_placed",
        product=product_id,
        order_id=result.order_id,
        stop=stop,
        target=target,
    )
    return result.order_id


# -- partial scale-out --------------------------------------------------------------------------


def scale_out(
    broker: Any,
    repo: Repository,
    config: Config,
    product_id: str,
    qty: Decimal,
    exit_price: Decimal,
    rule_name: str,
    now_ts: int,
) -> ExecutionResult:
    """Partially close `qty` of an open position at `exit_price` (a rule-driven profit-take,
    e.g. "sell half at the first target") -- runs the same guard+preview+place+log pipeline as
    `execute()` for a plain SELL leg, system-initiated so it proceeds in autonomous mode (still
    subject to every guard rail, still fully logged).
    """
    intent = OrderIntent(
        product_id=product_id,
        side=Side.SELL,
        qty=qty,
        entry=exit_price,
        stop=None,
        notional=sizing.spend(qty, exit_price),
        is_dca=False,
        rule_kind=rule_name,
    )
    log_event(
        logger,
        logging.INFO,
        "executor.scale_out_requested",
        product=product_id,
        qty=qty,
        exit_price=exit_price,
        rule=rule_name,
    )
    return _run_order(intent, broker, repo, config, "autonomous", None, now_ts)


# -- stop management: break-even roll + ATR trailing -------------------------------------------


def _roll_stop(
    broker: Any,
    repo: Repository,
    config: Config,
    product_id: str,
    old_stop_order_id: int,
    new_stop: Decimal,
    qty: Decimal,
    rule_name: str,
    now_ts: int,
) -> int | None:
    """Cancel the existing protective stop leg and replace it at `new_stop`.

    Refuses (returns `None`, the existing stop stays in force) if `new_stop` would widen the
    last-recorded `open_stop:<product_id>` -- ratchet-only, mirroring rail 9's invariant for
    entries. The replacement order still runs through `guards.check` (allowlist/caps/kill-switch/
    etc. -- every order, no exceptions) before it is placed.

    **The cancel-then-place window is not atomic and cannot be made so** (see the comment at the
    cancel below). What it CAN be is recoverable: an `unbracketed:<product_id>` record is written
    before the venue is touched and cleared only once the replacement rests, so a process that
    dies anywhere in between leaves levels the next cycle's sweep re-places from. Before #519 that
    record did not exist here and one crash window was not merely unprotected but SILENT --
    indistinguishable from a DCA tranche, which legitimately carries no stop.
    """
    prior_stop = repo.get_state(f"open_stop:{product_id}")
    if prior_stop is not None and new_stop < prior_stop:
        log_event(
            logger,
            logging.INFO,
            "executor.stop_roll_refused",
            product=product_id,
            new_stop=new_stop,
            prior_stop=prior_stop,
        )
        return None

    target = repo.get_state(f"open_target:{product_id}")
    if target is None:
        log_event(
            logger,
            logging.WARNING,
            "executor.stop_roll_refused",
            product=product_id,
            reason="no open_target recorded -- cannot re-place the bracket",
        )
        return None

    # A stop AT OR ABOVE the target is not a tighter stop; it is a coin flip.
    #
    # The replacement is a single native bracket carrying both prices, so a stop that has caught
    # up with the target describes two exits racing at the same level, where whichever side the
    # venue evaluates first decides whether this position took a profit or a loss.
    # `keel_broker_api.orders.BracketGTC` refuses exactly this shape at construction -- "a coin
    # flip wearing a protective order's name" -- but the live path does not build one of those
    # yet (#502 stage 2 is blocked on #524's port migration), so nothing between the ratchet and
    # Coinbase has been checking it.
    #
    # Reachable rather than theoretical: `trail_stop_atr` computes `price - atr * multiplier` and
    # the live agent cycles ONCE A DAY, so a gap through the target that has not yet been
    # reconciled leaves a position whose recorded target sits below the newly computed stop.
    #
    # Refusing here is the conservative half: the roll is abandoned and the EXISTING bracket stays
    # in force, so the position keeps the protection it already had. Placing the inverted pair
    # instead would cancel a working bracket to install a coin flip -- and if the venue refused it,
    # leave the position naked until the next sweep.
    if new_stop >= target:
        log_event(
            logger,
            logging.WARNING,
            "executor.stop_roll_refused",
            product=product_id,
            new_stop=new_stop,
            target=target,
            reason="new_stop is at or above the target -- the existing bracket stays in force",
        )
        return None

    # THE CRASH LEDGER, written BEFORE the venue is touched (#519).
    #
    # Everything below this line can die mid-flight, and until this record existed one of those
    # deaths was SILENT. `_run_order` marks the old bracket `canceled` locally and then places the
    # replacement; a process that dies in between left no resting bracket, no `unbracketed:`
    # record, and therefore nothing for `reconcile_unbracketed_positions` to act on -- it took the
    # skip branch that exists for DCA and the position stayed naked with no CRITICAL, looking
    # exactly like a holding that carries no stop by design.
    #
    # Writing the intent first turns every one of those deaths into a state the existing sweep
    # already converges: next cycle it finds no resting bracket, finds these levels, and re-places
    # from them. The record is deliberately the SAME key `place_bracket` uses rather than a new
    # `roll_intent:` one -- the sweep, its ledger-sized qty, its escalation and its
    # clear-on-success semantics are written and tested once, and a second key would mean a second
    # healer to keep correct.
    #
    # A record left behind by an aborted roll is harmless: the sweep skips any product whose
    # bracket is still resting, and the levels it holds are ratchet-consistent either way.
    repo.set_state(
        f"{UNBRACKETED_PREFIX}{product_id}",
        {"stop": new_stop, "target": target, "qty": qty},
    )

    # CANCEL FIRST, then place. This inverts the old two-leg order of operations, and it has to:
    # the resting native bracket already commits the whole position, so placing a replacement
    # first would be rejected for insufficient funds. `edit_order` cannot avoid the inversion
    # either -- it accepts only limit-GTC orders and edits only size/price, never
    # `stop_trigger_price`. The cost is a brief window in which the position has NO protective
    # stop; that is the trade-off the native bracket imposes, and the failure path below is why
    # it must never be silent.
    old_order = repo.get_order(old_stop_order_id)
    # BOTH resting statuses (#446): a `partially_filled` old bracket still commits its unfilled
    # remainder at the venue, and placing the replacement without cancelling it is the same
    # double-commit (or base-locked rejection) the cancel-first inversion exists to avoid.
    # Pre-#446 this `== "pending"` test was correct only because a partial stayed `pending`.
    if old_order is not None and old_order["status"] in RESTING_STATUSES:
        _cancel_at_exchange(broker, repo, old_order)
        repo.update_order(old_stop_order_id, status="canceled", updated_at=now_ts)

    intent = OrderIntent(
        product_id=product_id,
        side=Side.SELL,
        qty=qty,
        entry=new_stop,
        stop=None,
        # Belt and braces: this function already refuses a widening roll above, but that check is
        # local and overridable by a future caller, whereas rail 9 is not (issue #206).
        protective_stop=new_stop,
        notional=sizing.spend(qty, new_stop),
        is_dca=False,
        rule_kind=rule_name,
    )
    result = _run_order(
        intent,
        broker,
        repo,
        config,
        "autonomous",
        None,
        now_ts,
        order_configuration=_bracket_order_configuration(
            qty, target, new_stop, _base_increment_for(broker, repo, product_id, now_ts)
        ),
    )
    if not result.placed:
        # The old bracket is already cancelled, so the position is NAKED right now. The
        # `unbracketed:` record written before the cancel is DELIBERATELY LEFT STANDING: it is
        # what `reconcile_unbracketed_positions` re-places from on the next cycle (#519).
        #
        # The CRITICAL stays regardless. Automatic recovery next cycle is not a reason to
        # downgrade an alert about a position that is unprotected RIGHT NOW -- the deployment
        # cycles once per UTC day, so "next cycle" can be up to a day away. Never downgrade this.
        log_event(
            logger,
            logging.CRITICAL,
            "executor.position_unprotected",
            product=product_id,
            reason=result.reason,
            attempted_stop=new_stop,
            cancelled_order_id=old_stop_order_id,
            detail=(
                "the previous bracket was cancelled and its replacement was REJECTED -- this "
                "position currently has no protective stop at the exchange. The unbracketed "
                "record is retained so the next cycle's sweep re-places it."
            ),
        )
        return None

    repo.set_state(f"open_stop:{product_id}", new_stop)
    repo.set_state(f"open_target:{product_id}", target)
    # The replacement is resting, so the crash ledger has served its purpose. Clearing it matches
    # `place_bracket`'s own success path; leaving it would have the sweep re-place a bracket that
    # already exists on the next cycle.
    repo.set_state(f"{UNBRACKETED_PREFIX}{product_id}", None)
    log_event(
        logger,
        logging.INFO,
        "executor.stop_rolled",
        product=product_id,
        prior_stop=prior_stop,
        new_stop=new_stop,
        order_id=result.order_id,
    )
    return result.order_id


def roll_to_break_even(
    broker: Any,
    repo: Repository,
    config: Config,
    product_id: str,
    old_stop_order_id: int,
    entry_price: Decimal,
    qty: Decimal,
    rule_name: str,
    now_ts: int,
) -> int | None:
    """Roll the protective stop to break-even (the position's entry price), typically once a
    favorable move has covered enough ground to de-risk the trade. `None` if the roll would
    widen the existing stop (see `_roll_stop`)."""
    return _roll_stop(
        broker, repo, config, product_id, old_stop_order_id, entry_price, qty, rule_name, now_ts
    )


def trail_stop_atr(
    broker: Any,
    repo: Repository,
    config: Config,
    product_id: str,
    old_stop_order_id: int,
    current_price: Decimal,
    atr: Decimal,
    qty: Decimal,
    rule_name: str,
    now_ts: int,
    multiplier: Decimal = Decimal("1.5"),
) -> int | None:
    """Trail the protective stop `multiplier * atr` behind `current_price`. `None` if the
    computed trail level would widen the existing stop (see `_roll_stop`) -- naturally
    one-directional, so a temporary price dip never loosens a stop that already ratcheted up.
    """
    new_stop = current_price - (atr * multiplier)
    return _roll_stop(
        broker, repo, config, product_id, old_stop_order_id, new_stop, qty, rule_name, now_ts
    )
