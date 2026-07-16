"""The order executor (P3 Task 4) -- turns a `Signal` into a guarded live order.

`execute()` is the only path from a strategy `Signal` to a real order: it sizes the candidate
(`execution.sizing`), runs the twelve un-overridable §14 hard rails (`execution.guards.check`)
**before** anything reaches the broker, previews the order, honors the confirm/bypass mode gate,
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

**OCO bracket.** A filled entry that carries a stop *and* target (i.e. not DCA) automatically
gets a linked stop+target exit bracket (`place_oco_bracket`): two SELL legs, each recorded as
the other's sibling in `agent_state` (`oco_sibling:<order_id>`) so that once monitoring code
(the Task 8 agent loop) observes one leg fill, `handle_oco_fill` cancels the other -- a filled
position must never be sold twice. Both bracket legs run through `guards.check` too (every order,
no exceptions); a vetoed leg is simply never placed (`place_oco_bracket` returns `None` for that
leg's id).

**Stop management** (`roll_to_break_even` / `trail_stop_atr`) cancels the existing protective
stop leg and replaces it at a new price, but never widens it: both delegate to `_roll_stop`,
which refuses (returns `None`, leaving the existing stop in force) if the proposed new stop is
below the last-recorded `open_stop:<product_id>` -- the same "only ratchet toward profit"
invariant rail 9 enforces on entries, applied directly here since the replacement order's own
`guards.check` call (still mandatory) can't reuse rail 9 as-is: rail 9 is paired with the
min-move/anti-scalping rail, which has no meaning for a stop-replacement order that has no
separate entry price of its own. `scale_out` closes part of a position (a rule-driven partial
profit-take) through the same guard+preview+place+log pipeline as a plain SELL leg.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from halal_cb.config import Config
from halal_cb.data.repository import Repository
from halal_cb.execution import guards, sizing
from halal_cb.execution.guards import OrderIntent
from halal_cb.strategy.rules.base import Action, Signal
from halal_cb.types import Side


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


ConfirmFn = Callable[[dict[str, Any]], bool]


# -- main entry point -----------------------------------------------------------------------


def execute(
    signal: Signal,
    broker: Any,
    repo: Repository,
    config: Config,
    mode: Literal["confirm", "bypass"],
    confirm_fn: ConfirmFn | None = None,
    now_ts: int | None = None,
) -> ExecutionResult:
    """Turn `signal` into a guarded order: size -> guards (veto on any violation) -> preview ->
    confirm|bypass -> place -> log (before and after).

    `mode="confirm"` requires `confirm_fn(preview) -> bool`; a missing or rejecting `confirm_fn`
    means the order is not placed (fails closed, never silently proceeds). `mode="bypass"`
    places without a prompt but is *not* exempt from `guards.check` -- rails run before every
    order in every mode, un-overridable, per the main spec §14.
    """
    if now_ts is None:
        now_ts = int(time.time())

    intent = _build_intent(signal, repo, config)
    if intent is None:
        return ExecutionResult(
            placed=False,
            order_id=None,
            vetoed_by=[],
            preview=None,
            reason=f"no open {signal.product_id} position to exit",
        )

    result = _run_order(intent, broker, repo, config, mode, confirm_fn, now_ts)

    if (
        result.placed
        and intent.side == Side.BUY
        and not intent.is_dca
        and intent.stop is not None
        and signal.setup is not None
    ):
        place_oco_bracket(
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

    return result


# -- intent construction ---------------------------------------------------------------------


def _is_dca_setup(context: dict[str, Any]) -> bool:
    return bool(context.get("no_stop")) or context.get("order_class") == "dca"


def _build_intent(signal: Signal, repo: Repository, config: Config) -> OrderIntent | None:
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

        return OrderIntent(
            product_id=signal.product_id,
            side=Side.BUY,
            qty=qty,
            entry=setup.entry,
            stop=stop,
            notional=sizing.spend(qty, setup.entry),
            is_dca=is_dca,
            rule_kind=signal.rule_name,
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


# -- shared guard -> preview -> confirm/bypass -> place -> log pipeline ------------------------


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
        return ExecutionResult(
            placed=False,
            order_id=None,
            vetoed_by=guard_result.violations,
            preview=None,
            reason="vetoed by guards: " + "; ".join(guard_result.violations),
        )

    if order_configuration is None:
        order_configuration = _order_configuration(intent)
    preview = broker.preview_order(intent.product_id, intent.side, order_configuration)

    if mode == "confirm":
        approved = confirm_fn(preview) if confirm_fn is not None else False
        if not approved:
            return ExecutionResult(
                placed=False,
                order_id=None,
                vetoed_by=[],
                preview=preview,
                reason="rejected at confirm gate",
            )
    elif mode != "bypass":
        raise ValueError(f"execute: unknown mode {mode!r} -- must be 'confirm' or 'bypass'")

    order_id = repo.insert_order(_order_row(intent, mode, now_ts))

    place_result = broker.place_order(intent.product_id, intent.side, order_configuration)
    success = bool(place_result.get("success"))
    status = _initial_status(order_configuration) if success else "rejected"
    repo.update_order(
        order_id,
        status=status,
        actual_fill=intent.entry if status == "filled" else None,
        raw_response=json.dumps(place_result, default=str),
        updated_at=now_ts,
    )

    if not success:
        return ExecutionResult(
            placed=False,
            order_id=order_id,
            vetoed_by=[],
            preview=preview,
            reason=f"broker rejected order: {place_result.get('error')}",
        )

    if intent.stop is not None:
        repo.set_state(f"open_stop:{intent.product_id}", intent.stop)

    return ExecutionResult(
        placed=True, order_id=order_id, vetoed_by=[], preview=preview, reason="placed"
    )


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
    the exchange until a later fill event (`handle_oco_fill`/monitoring) marks it `filled`."""
    config_type = next(iter(order_configuration), "")
    return "filled" if config_type.startswith("market_") else "pending"


def _stop_leg_order_configuration(qty: Decimal, stop: Decimal) -> dict[str, dict[str, str]]:
    return {
        "stop_limit_stop_limit_gtc": {
            "base_size": str(qty),
            "limit_price": str(stop),
            "stop_price": str(stop),
            "stop_direction": "STOP_DIRECTION_STOP_DOWN",
        }
    }


def _target_leg_order_configuration(qty: Decimal, target: Decimal) -> dict[str, dict[str, str]]:
    return {"limit_limit_gtc": {"base_size": str(qty), "limit_price": str(target)}}


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


# -- OCO bracket ------------------------------------------------------------------------------


def place_oco_bracket(
    broker: Any,
    repo: Repository,
    config: Config,
    product_id: str,
    qty: Decimal,
    stop: Decimal,
    target: Decimal,
    rule_name: str,
    now_ts: int,
) -> tuple[int | None, int | None]:
    """Place a linked stop+target exit bracket for an open long position.

    Both legs run through `guards.check` like any other order (un-overridable); a vetoed leg is
    never placed and its slot in the returned tuple is `None`. Successfully placed legs are
    recorded as each other's OCO sibling in `agent_state` (for `handle_oco_fill`), and the stop
    price is recorded as `open_stop:<product_id>` for rail 9 (no stop-loss widening) to check
    future entries/rolls against.
    """
    stop_intent = OrderIntent(
        product_id=product_id,
        side=Side.SELL,
        qty=qty,
        entry=stop,
        stop=None,
        notional=sizing.spend(qty, stop),
        is_dca=False,
        rule_kind=rule_name,
    )
    stop_result = _run_order(
        stop_intent,
        broker,
        repo,
        config,
        "bypass",
        None,
        now_ts,
        order_configuration=_stop_leg_order_configuration(qty, stop),
    )

    target_intent = OrderIntent(
        product_id=product_id,
        side=Side.SELL,
        qty=qty,
        entry=target,
        stop=None,
        notional=sizing.spend(qty, target),
        is_dca=False,
        rule_kind=rule_name,
    )
    target_result = _run_order(
        target_intent,
        broker,
        repo,
        config,
        "bypass",
        None,
        now_ts,
        order_configuration=_target_leg_order_configuration(qty, target),
    )

    stop_order_id = stop_result.order_id if stop_result.placed else None
    target_order_id = target_result.order_id if target_result.placed else None

    if stop_order_id is not None and target_order_id is not None:
        repo.set_state(f"oco_sibling:{stop_order_id}", target_order_id)
        repo.set_state(f"oco_sibling:{target_order_id}", stop_order_id)

    if stop_order_id is not None:
        repo.set_state(f"open_stop:{product_id}", stop)

    return stop_order_id, target_order_id


def handle_oco_fill(broker: Any, repo: Repository, filled_order_id: int, now_ts: int) -> int | None:
    """Mark `filled_order_id` filled and cancel its OCO sibling leg, if any and still pending.

    Call this once monitoring code (the Task 8 agent loop) observes that one leg of a bracket
    has filled -- the sibling must never be allowed to also fill (that would sell an already-
    closed position again). Returns the cancelled sibling's order id, or `None` if there was no
    live sibling to cancel.
    """
    repo.update_order(filled_order_id, status="filled", updated_at=now_ts)

    sibling_id = repo.get_state(f"oco_sibling:{filled_order_id}")
    if sibling_id is None:
        return None

    sibling = repo.get_order(sibling_id)
    if sibling is None or sibling["status"] != "pending":
        return None

    cancel = getattr(broker, "cancel_order", None)
    if cancel is not None:
        native_id = _native_order_id(sibling)
        if native_id is not None:
            cancel(native_id)

    repo.update_order(sibling_id, status="canceled", updated_at=now_ts)
    return sibling_id


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
    `execute()` for a plain SELL leg, system-initiated so it proceeds in bypass mode (still
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
    return _run_order(intent, broker, repo, config, "bypass", None, now_ts)


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
        return None

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
        "bypass",
        None,
        now_ts,
        order_configuration=_stop_leg_order_configuration(qty, new_stop),
    )
    if not result.placed:
        return None

    old_order = repo.get_order(old_stop_order_id)
    if old_order is not None and old_order["status"] == "pending":
        cancel = getattr(broker, "cancel_order", None)
        if cancel is not None:
            native_id = _native_order_id(old_order)
            if native_id is not None:
                cancel(native_id)
        repo.update_order(old_stop_order_id, status="canceled", updated_at=now_ts)

    sibling_id = repo.get_state(f"oco_sibling:{old_stop_order_id}")
    if sibling_id is not None:
        repo.set_state(f"oco_sibling:{result.order_id}", sibling_id)
        repo.set_state(f"oco_sibling:{sibling_id}", result.order_id)

    repo.set_state(f"open_stop:{product_id}", new_stop)
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
