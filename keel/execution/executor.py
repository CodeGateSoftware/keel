"""The order executor (P3 Task 4) -- turns a `Signal` into a guarded live order.

`execute()` is the only path from a strategy `Signal` to a real order: it sizes the candidate
(`execution.sizing`), runs the twelve un-overridable §14 hard rails (`execution.guards.check`)
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

**Stop management** (`roll_to_break_even` / `trail_stop_atr`) cancels the existing protective
stop leg and replaces it at a new price, but never widens it: both delegate to `_roll_stop`,
which refuses (returns `None`, leaving the existing stop in force) if the proposed new stop is
below the last-recorded `open_stop:<product_id>` -- the same "only ratchet toward profit"
invariant rail 9 enforces on entries, applied directly here since the replacement order's own
`guards.check` call (still mandatory) can't reuse rail 9 as-is: rail 9 is paired with the
min-move/anti-scalping rail, which has no meaning for a stop-replacement order that has no
separate entry price of its own. `scale_out` closes part of a position (a rule-driven partial
profit-take) through the same guard+preview+place+log pipeline as a plain SELL leg.

**USDC-funding balance (rail 13, Issue #59).** For a BUY `_build_intent` fetches the live
available `config.quote_currency` (default USDC) balance from `broker.get_accounts()` and hands
it to `guards.check` via `OrderIntent.available_quote` -- guards itself has no broker access, by
design. This happens *before* `guards.check` runs (the balance is an input to the rail, not
something guarded itself), so it is the one broker call this module makes ahead of the guard
gate; `_fetch_available_quote` swallows any exception from the call and returns `None`, which
rail 13 then treats as fail-closed (vetoes the BUY) exactly like an unknown balance from a
missing quote-currency account. SELL intents never fetch a balance (the rail exempts them).
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any, Literal

from keel_core.telemetry import log_event, log_exception

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

    `vetoed_by` is non-empty only when `guards.check` rejected the intent (the specific rail
    names, verbatim from `GuardResult.violations`) -- a confirm-gate rejection or a broker-side
    failure leaves it `[]` and explains itself via `reason` instead.
    """

    placed: bool
    order_id: int | None
    vetoed_by: list[str]
    preview: dict[str, Any] | None
    reason: str
    # The local `orders.id` of the exit bracket this entry left resting, when one was placed.
    # `execute` places the bracket itself, so this is the ONLY way a caller can learn its id --
    # and `agent.run_once` needs it to point the new `positions` row at its bracket. Discarding
    # it (as this did until Task 3) left the tranche with no way to name its own protective
    # order, which is what made `roll_to_break_even`/`trail_stop_atr` unreachable by
    # construction. `None` when no bracket was placed OR when it was vetoed.
    bracket_order_id: int | None = None


ConfirmFn = Callable[[dict[str, Any]], bool]


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



def _clear_resting_bracket(broker: Any, repo: Repository, product_id: str, now_ts: int) -> bool:
    """Cancel any resting exchange-side exit bracket for `product_id`. `False` if one could not
    be cleared, in which case the caller MUST NOT place its SELL.

    `place_bracket` leaves a native trigger bracket committing the ENTIRE base position, so a
    voluntary rule exit selling the same inventory collides with it: on spot the base is locked
    and the SELL is rejected, so `position_rule` is never cleared, no outcome is recorded, and
    the agent retries the same doomed sell every cycle while the position rides a stale stop. If
    it DID fill, the still-live bracket could later sell inventory we no longer hold.

    This lives in `execute` rather than in `agent._handle_exits` so every SELL path -- the rule
    exit today, `scale_out` or any future one -- gets it by construction rather than by each
    caller remembering. Failing closed (refuse the exit) is right: an uncancellable bracket means
    we do not know what the exchange will do with that inventory, and adding a second order to
    that uncertainty is strictly worse than waiting a cycle.
    """
    for row in repo.get_orders(mode="live", product_id=product_id, status="pending"):
        if str(row["side"]).upper() != Side.SELL.value.upper():
            continue
        try:
            _cancel_at_exchange(broker, repo, row)
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


def _fetch_available_quote(broker: Any, quote_currency: str) -> Decimal | None:
    """Live available `quote_currency` (default USDC) balance from `broker.get_accounts()`.

    `None` on any failure -- a broker error, a malformed response, or simply no account for
    `quote_currency` -- so rail 13 (USDC-funding) fails closed rather than guessing. This is
    the one broker call `execute()` makes *before* `guards.check` runs: it's an input the rail
    needs, not itself something the guard gate protects (no funds move, no order is placed).
    """
    if broker is None:
        # Paper mode passes no broker. That is not an error and must not be logged as one --
        # an ERROR per paper entry would fill the operator's log with noise about a condition
        # that is expected and already handled (rail 13 is skipped offline).
        return None
    try:
        accounts = broker.get_accounts()
    except Exception:
        log_exception(logger, "executor.quote_fetch_failed", quote_currency=quote_currency)
        return None

    for account in accounts or []:
        currency = account.get("currency") if isinstance(account, dict) else getattr(
            account, "currency", None
        )
        if currency != quote_currency:
            continue
        balance = account.get("available_balance") if isinstance(account, dict) else getattr(
            account, "available_balance", None
        )
        if balance is None:
            return None
        return balance if isinstance(balance, Decimal) else Decimal(str(balance))

    return None


def _build_intent(
    signal: Signal, broker: Any, repo: Repository, config: Config, now_ts: int
) -> OrderIntent | None:
    """Size `signal` into an `OrderIntent`, or `None` for an EXIT with nothing open to sell."""
    if signal.action == Action.ENTER:
        setup = signal.setup
        if setup is None:
            raise ValueError("execute: an ENTER signal must carry a setup to size an order from")

        is_dca = _is_dca_setup(setup.context)
        if is_dca:
            qty = sizing.dca_size(config.dca.budget_usd, setup.entry)
            stop = None
        else:
            equity = config.caps.max_exposure_usd
            qty = sizing.size(equity, config.risk_pct, setup.entry, setup.stop)
            stop = setup.stop

        available_quote = _fetch_available_quote(broker, config.quote_currency)
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
    )


def _held_position(repo: Repository, product_id: str) -> tuple[Decimal, Decimal]:
    """Net held qty + average cost basis for `product_id`, from filled live orders."""
    buy_qty = Decimal("0")
    buy_cost = Decimal("0")
    sell_qty = Decimal("0")
    for order in repo.get_orders(mode="live", product_id=product_id, status="filled"):
        price = order.get("actual_fill") or order.get("limit_price") or order.get(
            "expected_fill"
        )
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
        order_configuration = _order_configuration(intent)
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
        _upgrade_to_observed_economics(broker, repo, order_id, place_result, now_ts)

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
    broker: Any, repo: Repository, order_id: int, place_result: dict[str, Any], now_ts: int
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


def _order_row(intent: OrderIntent, mode: str, now_ts: int) -> dict[str, Any]:
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
        rule_id=None,
        created_at=now_ts,
        updated_at=now_ts,
    )


def _order_configuration(intent: OrderIntent) -> dict[str, dict[str, str]]:
    if intent.side == Side.BUY:
        return {"market_market_ioc": {"quote_size": str(intent.notional)}}
    return {"market_market_ioc": {"base_size": str(intent.qty)}}


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
    # failure this module exists to prevent. `is not True` so a fake/broker returning None (no
    # confirmation) also fails closed.
    if cancel(native_id) is not True:
        raise CancelUnavailable(
            f"exchange did not confirm cancellation of {native_id} -- refusing to record it as "
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
    qty: Decimal, target: Decimal, stop: Decimal
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
    return {
        "trigger_bracket_gtc": {
            "base_size": str(qty),
            "limit_price": str(target),
            "stop_trigger_price": str(stop),
        }
    }


# -- exit bracket ------------------------------------------------------------------------------


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
        order_configuration=_bracket_order_configuration(qty, target, stop),
    )
    if not result.placed:
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

    # CANCEL FIRST, then place. This inverts the old two-leg order of operations, and it has to:
    # the resting native bracket already commits the whole position, so placing a replacement
    # first would be rejected for insufficient funds. `edit_order` cannot avoid the inversion
    # either -- it accepts only limit-GTC orders and edits only size/price, never
    # `stop_trigger_price`. The cost is a brief window in which the position has NO protective
    # stop; that is the trade-off the native bracket imposes, and the failure path below is why
    # it must never be silent.
    old_order = repo.get_order(old_stop_order_id)
    if old_order is not None and old_order["status"] == "pending":
        _cancel_at_exchange(broker, repo, old_order)
        repo.update_order(old_stop_order_id, status="canceled", updated_at=now_ts)

    intent = OrderIntent(
        product_id=product_id,
        side=Side.SELL,
        qty=qty,
        entry=new_stop,
        stop=None,
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
        order_configuration=_bracket_order_configuration(qty, target, new_stop),
    )
    if not result.placed:
        # The old bracket is already cancelled, so the position is NAKED right now. There is no
        # silent recovery: the caller must retry or close the position. Never downgrade this.
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
                "position currently has no protective stop at the exchange"
            ),
        )
        return None

    repo.set_state(f"open_stop:{product_id}", new_stop)
    repo.set_state(f"open_target:{product_id}", target)
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
