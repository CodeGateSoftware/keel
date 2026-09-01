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

**Stop management -- wired on the live cycle, DEFAULT OFF per rule (#442 wired the policy;
#502 stage 2 wired the live step; the port migration that once blocked this is DONE -- the
bracket has been an `OrderSpec` (`BracketGTC`) since #569, reaching the venue through
`place_order` like every other order, and the port flip finished in #582).**
`roll_to_break_even` / `trail_stop_atr` cancel the existing protective bracket and replace
it at a new price, never widening it: both delegate to `_roll_stop`, which refuses (returns
`None`, leaving the existing stop in force) if the proposed new stop is below the
last-recorded `open_stop:<product_id>` -- the same "only ratchet toward profit" invariant
rail 9 enforces on entries, applied directly here since the replacement order's own
`guards.check` call (still mandatory) can't reuse rail 9 as-is: rail 9 is paired with the
min-move/anti-scalping rail, which has no meaning for a stop-replacement order that has no
separate entry price of its own. Ratchet-only is rail-9-safe BY CONSTRUCTION (`_roll_stop`
refuses a widening proposal before `guards.check` ever runs; pinned by
`tests/execution/test_executor.py::test_a_ratchet_only_trail_can_never_trip_rail_9`).
`roll_stop_to` is the general single-roll form the agent's per-cycle step drives (see its
docstring for why one roll, not one per arm). The live caller is `agent.run_once`'s
stop-management step (#502): per held tranche whose owning rule carries `trail_atr_mult` /
`be_roll_rr` (the `pullback_continuation` / `rsi_meanrev` knobs), it applies the same
`strategy/exit_policy` the sim/backtest engines apply and rolls the resting bracket to the
ratcheted level. A rule whose params carry NEITHER knob is never managed -- the #442
experiment (docs/experiments/2026-08-22-trailing-vs-static-exits.md) measured trailing
WORSE and the break-even roll no better than the static exit at the 120 bp fee, so the
capability ships and the operator opts in per rule; `turtle_breakout` deliberately offers
neither knob (its real exit is the Donchian channel, and a trail would cut the long winners
the system exists to let run). `scale_out` is the partial profit-take, and since #502 it is
COMPLETE rather than merely present: it cancels the resting bracket, sells the fraction,
books it against the `positions` ledger and re-places a bracket for the remainder, all
behind #519's crash ledger. It carried a tripwire test for as long as those two
prerequisites were unbuilt -- a partial SELL beside a bracket committing the full position,
and a scaled-out winner rail 16 counted as a loss; both are now discharged INSIDE the
function, where no future caller can forget them. It still has no live rule driving it: the
rule-side "sell half at T1" knob is separate work.

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
50bp -- until #523 numerically #334's slippage cap; now an independent threshold that #523
deliberately left where it was) refuses the order, and a preview with no readable
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
from collections.abc import Callable
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from keel_broker_api.orders import (
    BracketGTC,
    MarketIOCByBase,
    MarketIOCByQuote,
    OrderSpec,
)
from keel_broker_api.port import TradeScopeDenied
from keel_broker_api.results import (
    CancelOutcome,
    OrderStatus,
    PlaceResult,
    Preview,
    coerce_cancel_outcome,
)
from keel_core.credential_identity import current_credential_fingerprint
from keel_core.products import quote_currency_of
from keel_core.telemetry import current_venue, log_event, log_exception, log_venue_failure
from keel_core.trade_scope import TradeScopeState, VenueTradeScope

from keel.config import Config
from keel.data.repository import Repository
from keel.execution import guards, sizing, streak
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
    # Whatever `broker.preview_order` handed back, verbatim -- the port's `Preview` since #524
    # finished the broker-port migration: every broker the live path can construct answers in
    # that type, so there is no second shape to branch on anywhere downstream.
    preview: Preview | None
    reason: str
    # The local `orders.id` of the exit bracket this entry left resting, when one was placed.
    # `execute` places the bracket itself, so this is the ONLY way a caller can learn its id --
    # and `agent.run_once` needs it to point the new `positions` row at its bracket. Discarding
    # it (as this did until Task 3) left the tranche with no way to name its own protective
    # order, which is what made `roll_to_break_even`/`trail_stop_atr` unreachable by
    # construction. `None` when no bracket was placed OR when it was vetoed.
    bracket_order_id: int | None = None


#: The human confirm gate for `mode="confirm"`. One shape: the port's `Preview`, the only thing
#: `broker.preview_order` returns since the migration finished. `Preview` is what carries
#: `synthetic` -- the flag that tells a human whether they are approving a venue's quote or an
#: estimate keel computed (issue #199) -- so a gate typed to anything less has nowhere to
#: render that distinction.
ConfirmFn = Callable[[Preview], bool]


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

    This lives in the executor rather than in `agent._handle_exits` so every SELL path gets it
    by construction rather than by each caller remembering: `execute` calls it for the rule
    exit, and `scale_out` calls it directly for the partial one (it runs `_run_order`, the
    pipeline BELOW `execute`, and bypassing this was exactly the defect #502 closed).

    Failing closed (refuse the exit) is right: an uncancellable bracket means
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
    See `_order_spec`. This function therefore **never raises**: every failure path
    (no broker in paper mode, a venue error, a malformed or absent field) returns `None`, and the
    exit proceeds exactly as it did before #516.

    **The venue is asked for ONE product, and that is a change from how this read began (#524).**
    It called `list_products()` -- about 900 rows on Coinbase -- and cached exactly one of them,
    because `Repository.set_state` commits per call and caching all of them would have meant ~900
    fsyncs inside the order-placement path, the most latency-sensitive moment in the engine. The
    argument was sound and the shape was not: the port's `get_instrument` asks the venue for the
    product the caller actually wants, so there is no longer a catalogue to decline to cache.
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
        instrument = broker.get_instrument(product_id)
    except Exception:
        # Every failure is the same answer here -- unknown, send the quantity unquantized, never
        # refuse the exit. `NotImplementedError` from an adapter that has not written the read
        # (keel-broker-alpaca) lands here too, and correctly: it is unknown to THIS deployment.
        log_venue_failure(logger, "executor.base_increment_fetch_failed", product=product_id)
        return None

    if instrument is None:
        return None
    increment = instrument.base_increment
    repo.set_state(key, {"increment": str(increment), "fetched_at": now_ts})
    return increment


def _coerce_increment(raw: object) -> Decimal | None:
    """A positive `Decimal` from the venue's string, or `None` -- never raises."""
    if raw is None:
        return None
    try:
        value = Decimal(str(raw))
    except InvalidOperation, TypeError, ValueError:
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
        # record for that one failure (`cb_client.get_balances` logs it first) -- two full
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
        # #516. Fetched here, like `available_quote` above, so `_order_spec` stays a
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


# -- #233: the venue's half of the trade-scope record ------------------------------------------
#
# `keel scope attest` is the operator's writer. These two functions are the VENUE's, and they are
# the reason the record can ever correct itself. Before them this module stood exactly where
# venue truth arrived and threw it away: a `place_order` failure was `log_exception` + re-raise,
# recorded nowhere, so a confidently wrong attestation stayed wrong forever and the next cycle
# could not tell a read-only credential from a healthy venue. That was the 2026-08-19 incident's
# real cost -- not the 403 itself, which moved no money, but that nothing wrote it down.
#
# Both are reached ONLY from `_run_order`, which is the live path by construction (`_order_row`
# hardcodes `mode="live"`; paper fills synthetically through `_paper_enter` and never previews or
# places). So there is no mode check here: a paper fill cannot reach these lines to record a
# confirmation the venue never gave.

#: How much of a venue's refusal survives into `venue_trade_scopes.refuted_reason`.
#:
#: The message is the venue's own words and is printed to a terminal by `doctor` and
#: `keel scope show`. Robinhood's is one sentence and Coinbase's is a short JSON object, but a
#: venue answering a 403 with an HTML error page is an ordinary thing for a venue to do, and a
#: page in this column would be a page in that output. Truncated, and MARKED truncated with an
#: ellipsis, so nobody reads the cut as the venue's complete answer.
_REFUTED_REASON_MAX = 500


def _trade_scope_venue() -> str:
    """The venue key both writers and rail 20 agree on.

    Identical to rail 20's own `current_venue() or DEFAULT_VENUE` (`guards.py`), and that
    identity is load-bearing rather than tidy: a writer keyed differently from the reader would
    refute a venue that refused nothing while leaving the venue that did refuse still permitted
    -- wrong in both directions at once, and silent in both.
    """
    return current_venue() or guards.DEFAULT_VENUE


def _record_trade_scope_confirmed(repo: Repository, now_ts: int) -> None:
    """The venue proved this credential can trade, by accepting a placement.

    Stronger evidence than the attestation it replaces, because the venue supplied it. Every
    accepted live placement re-confirms, which is why this record needs no TTL: the subscription
    record expires on a clock because nothing observes it, and this one is observed by every
    order that lands.

    `attested_scope`/`attested_ts` and the refusal history are carried FORWARD unchanged.
    Confirmation is a new fact, not a reset: what a human claimed and what a previous credential
    on this venue once did are both still true, and `doctor` renders both. `apply_scope_attest`
    carries the same fields forward in the other direction for the same reason.

    `credential_fingerprint` (#633) is the ONE exception to "carry forward": it is stamped with
    the CURRENT credential's fingerprint, replacing whatever was there. Unlike `attested_scope`
    or the refusal history, a fingerprint is not a fact about the past that should survive
    untouched -- it is a NEW fact about which credential just produced THIS evidence, and this
    placement is proof about the credential that placed it, not about whichever one an earlier
    write happened to be stamped with. `current_credential_fingerprint` never raises, so this
    call needs no guard the way the refute path's write does.
    """
    venue = _trade_scope_venue()
    existing = repo.get_venue_trade_scope(venue)
    repo.upsert_venue_trade_scope(
        VenueTradeScope(
            venue=venue,
            state=TradeScopeState.CONFIRMED,
            attested_scope=existing.attested_scope if existing is not None else None,
            attested_ts=existing.attested_ts if existing is not None else None,
            confirmed_ts=now_ts,
            refuted_ts=existing.refuted_ts if existing is not None else None,
            refuted_reason=existing.refuted_reason if existing is not None else None,
            credential_fingerprint=current_credential_fingerprint(venue),
        )
    )


def _record_trade_scope_refuted(repo: Repository, reason: str, now_ts: int) -> None:
    """The venue refused a placement on permission grounds. Rail 20 vetoes live ENTRIES on this
    venue from here until a human re-attests at a terminal.

    **"Until a human" is literal, and it is the confirm side that has to keep it true.** Nothing
    on the SELL side of this pipeline can clear this row -- exits, brackets, scale-outs and stop
    rolls all run through `_run_order` on a credential rail 20 never gated, and `_manage_stops`
    walks it every cycle on any open position, so a confirmation from any of them would erase the
    venue's own refusal unattended within hours. `_run_order` therefore confirms only from a BUY.
    The one path that CAN move this row forward automatically is a successful live ENTRY, and
    rail 20 refuses to let an entry be placed while this row stands -- so the loop is closed:
    a refutation is cleared by `keel scope attest`, or not at all.

    ⚠️ **Only ever called for a `TradeScopeDenied`.** Every adapter that raises it has already
    narrowed to that venue's observed permission refusal, and everything else -- a 5xx, a
    timeout, a 429, a 401, an unparseable body -- reaches `_run_order` as a plain exception and
    never arrives here. That is the single most important property of this PR: this write LATCHES,
    and a transient network failure that latched it would take a healthy live deployment off the
    market and require an operator at a terminal to restore it. A missed refusal costs one more
    refused order; a false one costs an outage.

    `attested_scope`/`attested_ts` and any earlier `confirmed_ts` survive, so `doctor` can say
    the sentence that actually helps -- "you attested this for trading and the venue then refused
    it" -- rather than presenting a bare refusal with no history behind it.

    `credential_fingerprint` (#633) is stamped with the CURRENT credential's fingerprint, same as
    the confirm side and for the same reason: this refusal is evidence about whichever credential
    the venue just refused, not about whatever fingerprint an earlier write happened to carry.
    `current_credential_fingerprint` never raises -- `_try_record_trade_scope_refuted`'s own
    reasoning applies here too: losing the venue's REFUSAL to a fingerprinting error would be
    strictly worse than writing a `None` fingerprint alongside it, and this function never has to
    choose between the two.
    """
    venue = _trade_scope_venue()
    existing = repo.get_venue_trade_scope(venue)
    if len(reason) > _REFUTED_REASON_MAX:
        reason = reason[: _REFUTED_REASON_MAX - 3] + "..."
    repo.upsert_venue_trade_scope(
        VenueTradeScope(
            venue=venue,
            state=TradeScopeState.REFUTED,
            attested_scope=existing.attested_scope if existing is not None else None,
            attested_ts=existing.attested_ts if existing is not None else None,
            confirmed_ts=existing.confirmed_ts if existing is not None else None,
            refuted_ts=now_ts,
            refuted_reason=reason,
            credential_fingerprint=current_credential_fingerprint(venue),
        )
    )


def _try_record_trade_scope_refuted(
    repo: Repository, reason: str, now_ts: int, intent: OrderIntent, order_id: int | None
) -> None:
    """`_record_trade_scope_refuted`, but a failed WRITE must never replace the venue's REFUSAL.

    Without this, a `sqlite3.OperationalError` raised while recording the refusal would propagate
    in place of the `TradeScopeDenied` the caller is in the middle of re-raising -- so the one
    signal an operator needs ("the venue says this credential may not trade") would be swapped
    for a database error, and the ERROR log below would never fire either.

    Failing to record costs one repeated refusal next cycle, which is exactly the pre-#233
    behaviour and survivable. Losing the venue's answer is not.
    """
    try:
        _record_trade_scope_refuted(repo, reason, now_ts)
    except Exception:
        log_exception(
            logger,
            "executor.trade_scope_refute_write_failed",
            product=intent.product_id,
            venue=_trade_scope_venue(),
            order_id=order_id,
        )


def _log_trade_scope_refusal(intent: OrderIntent, order_id: int | None) -> None:
    """The refusal, once, at ERROR with the traceback.

    ERROR because this is the event an operator greps for when entries stop: rail 20 will veto
    every live entry on this venue from the next cycle, and the reason must not be filed at INFO
    among the ordinary vetoes it causes.
    """
    log_exception(
        logger,
        "executor.trade_scope_refuted",
        product=intent.product_id,
        side=intent.side,
        venue=_trade_scope_venue(),
        order_id=order_id,
    )


def _run_order(
    intent: OrderIntent,
    broker: Any,
    repo: Repository,
    config: Config,
    mode: str,
    confirm_fn: ConfirmFn | None,
    now_ts: int,
    spec: OrderSpec | None = None,
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

    if spec is None:
        # A size that cannot be expressed in the venue's units REFUSES THIS ORDER, and refuses
        # only this order (#513). `agent.run_once` does not wrap its `executor.execute` call, so
        # letting `SizePrecisionUnavailable` escape here would abort the whole cycle and skip
        # every product after this one -- turning a single unserialisable order into a silent
        # outage. Refuse-and-log is what every other unknown in this engine does; a raise here
        # would be the one that behaves differently.
        try:
            spec = _order_spec(intent)
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
        preview = broker.preview_order(spec)
    except TradeScopeDenied as exc:
        # #233. Coinbase's preview IS a trade-scoped venue call, and this deployment's venue is
        # coinbase -- a credential with View but not Trade is refused HERE and never reaches
        # `place_order`. Handling only placement would leave the record's second writer
        # unreachable on the one venue that trades live.
        _try_record_trade_scope_refuted(repo, str(exc), now_ts, intent, None)
        _log_trade_scope_refusal(intent, None)
        raise
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
    _warn_if_market_routing_overrides_entry(intent, preview, spec)

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

    order_id = repo.insert_order(_order_row(intent, mode, now_ts, preview))

    try:
        place_result = broker.place_order(spec)
    except TradeScopeDenied as exc:
        # #233's whole point: the venue has just falsified the record, and this is the only place
        # in the engine that hears it.
        #
        # Written BEFORE the re-raise, and that ordering is the whole mechanism. NOTHING upstream
        # catches this: `agent.run_once` does not wrap `executor.execute`, and neither does the
        # `run_loop` above it, so the exception leaves the process. (#233's design says a
        # "cycle-survival handler" catches it -- that handler is `_manage_stops`' per-tranche
        # one, which this path does not pass through. Verified against `agent.py`.) A write
        # deferred to a caller would therefore never happen at all, which is precisely the state
        # before this PR: the venue answered and nothing wrote it down.
        #
        # The refusal still aborts the cycle, exactly as every other placement failure has
        # always done -- deliberately unchanged here. But it now aborts it ONCE: the row this
        # writes means the next cycle is vetoed cleanly by rail 20, with the venue's own words in
        # `doctor`, instead of walking into the same refusal again every day.
        _try_record_trade_scope_refuted(repo, str(exc), now_ts, intent, order_id)
        _log_trade_scope_refusal(intent, order_id)
        raise
    except Exception:
        log_exception(
            logger,
            "executor.place_failed",
            product=intent.product_id,
            side=intent.side,
            order_id=order_id,
        )
        raise
    success = place_result.success
    status = _initial_status(spec) if success else "rejected"
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
    fee = preview.est_fee
    repo.update_order(
        order_id,
        status=status,
        actual_fill=intent.entry if status == "filled" else None,
        fee=fee if status == "filled" else None,
        # The venue's own id for this order, which is what `_native_order_id` reads back to
        # cancel it. It was `json.dumps(place_result)` -- the whole pre-port response dict -- and
        # the id was dug out of that blob afterwards. `PlaceResult.broker_order_id` names it
        # directly, so the column now stores the one field anything ever read from it.
        raw_response=json.dumps({"order_id": place_result.broker_order_id}),
        updated_at=now_ts,
    )

    # #233. AFTER `update_order`: the order row is the audit record of money that moved and is
    # written first; the scope record is metadata about the credential that moved it.
    #
    # TWO gates, and the second one is a safety property rather than a nicety.
    #
    # `success`, not merely "no exception": `PlaceResult(success=False)` is the venue refusing
    # THIS ORDER -- no funds, a size out of band -- which proves nothing about permissions.
    #
    # `intent.side == Side.BUY`, spelled exactly as rail 20's own `is_buy`, because THIS PIPELINE
    # IS SHARED. `_run_order` serves entries, exits, brackets, scale-outs and stop rolls, and
    # rail 20 gates only entries -- so every SELL-side path here reaches the venue on a credential
    # the rail would have refused an ENTRY on. Confirming from one of those would let the engine
    # clear its own safety latch with nobody in the loop: a venue that REFUSED a placement, or a
    # credential an operator deliberately attested `--read-only` (ungated precisely because it
    # only ever REDUCES capability), would be silently promoted back to CONFIRMED by the next
    # successful exit -- and `_manage_stops` rolls a stop through here EVERY CYCLE on any open
    # position, so "the next successful exit" is hours away, not hypothetical.
    #
    # It is also simply what the record means. `may_place_live_entry` is the question; a
    # successful SELL is evidence about SELL scope and says nothing about BUY scope. Reading it
    # as proof is a category error that happens to unlatch the safety state.
    if success and intent.side == Side.BUY:
        # Unguarded, this would abort the cycle AFTER a live fill and BEFORE the caller places
        # the protective bracket -- a locked database costing a real position its stop. Every
        # other write on this path is an audit record of money that moved; this one is metadata
        # about the credential that moved it, and metadata must never outrank a bracket.
        try:
            _record_trade_scope_confirmed(repo, now_ts)
        except Exception:
            log_exception(
                logger,
                "executor.trade_scope_confirm_write_failed",
                product=intent.product_id,
                venue=_trade_scope_venue(),
                order_id=order_id,
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
            error=place_result.reason,
        )
        return ExecutionResult(
            placed=False,
            order_id=order_id,
            vetoed_by=[],
            preview=preview,
            reason=f"broker rejected order: {place_result.reason}",
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
    place_result: PlaceResult,
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
    native_id = place_result.broker_order_id
    if not native_id:
        return
    try:
        observed = get_order(native_id)
    except Exception:
        log_exception(logger, "executor.observed_economics_unavailable", order_id=order_id)
        return

    fill = observed.average_filled_price
    fees = observed.total_fees
    if not fill or fill <= 0:
        return
    repo.update_order(order_id, actual_fill=fill, fee=fees, updated_at=now_ts)
    _record_observed_fill_quantity(repo, order_id, observed, intent, now_ts)
    _log_intent_divergence(order_id, intent, fill)


def _record_observed_fill_quantity(
    repo: Repository,
    order_id: int,
    observed: OrderStatus,
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

    DELIBERATELY detect-and-surface only, and it stays that way even now that a resize EXISTS
    (`scale_out` re-places a bracket at a smaller size since #502). The two are not the same
    decision: a scale-out resizes because a RULE asked to sell a fraction, so the quantity is
    known before the venue is touched. Here the only evidence is a post-placement snapshot of
    an ENTRY, and auto-cancelling a protective order on the strength of a snapshot that may
    still be settling is a wrong auto-action on live money. The loud warning is the safe half,
    and the entry-side policy is still nobody's.
    """
    filled = observed.filled_size
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
    except InvalidOperation, TypeError, ValueError:
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
#: (`strategy/backtest.SLIPPAGE_FLOOR_PCT`; #259 scales it up on thin books, to a cap #523 then
#: moved from 50bp to the corpus tail at 183.8bp -- so "10x the slippage assumption" below holds
#: at the liquid end and inverts on the thin end, where the model now assumes MORE per leg than
#: this threshold and a firing says less than it used to). Against that, a deviation of a few bp is
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


def _preview_book(preview: Preview) -> tuple[Decimal | None, Decimal | None]:
    """The venue's book out of a preview, as `(best_bid, best_ask)`, each field read
    INDEPENDENTLY and safely.

    One helper, two consumers (#350): #332's entry-override warning needs only the ask, and
    the routing-time max-spread gate needs both sides plus their midpoint. The book lives in
    `Preview.detail` as strings -- the port's one shape since #524 finished the migration, so
    there is no dict arm to keep in agreement with it.

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

    `getattr` rather than `preview.detail`, because this helper feeds #332's warning whose
    contract is NEVER-RAISES: an object without a `detail` -- a contract-violating broker, a
    stale dict from a pre-port fake -- reads as no book at all, which is already this
    function's answer to "cannot know". No shape is probed back into existence; garbage just
    falls into the same fail-closed arm as an absent key.
    """
    raw = getattr(preview, "detail", None) or {}

    def _side(key: str) -> Decimal | None:
        value = raw.get(key)
        if value is None:
            return None
        try:
            parsed = Decimal(str(value))
        except InvalidOperation, TypeError, ValueError:
            return None
        return parsed if parsed.is_finite() and parsed > 0 else None

    return _side("best_bid"), _side("best_ask")


def _preview_best_ask(preview: Preview) -> Decimal | None:
    """The venue's best ask out of a preview.

    A thin consumer of `_preview_book` (above): same read, same per-field safety, ask only.
    """
    return _preview_book(preview)[1]


def _submit_book(preview: Preview | None) -> tuple[Decimal | None, Decimal | None]:
    """The book to RECORD on the order row (#626) -- `(best_bid, best_ask)`, and NEVER raising.

    #626 option 1: at this deployment's clip sizes spread IS the cost, and nothing stored it.
    keel already has the number -- `_run_order` previews before every live placement and #350's
    max-entry-spread gate reads exactly these two fields out of that preview -- so this is
    persistence of a value already in hand, not a new venue call.

    **Recorded on EVERY live row, deliberately wider than the gate that fetches it.** #350
    gates BUYs only, because trapping an exit in a wide book strands a position the rule said
    to leave. Measurement has the opposite requirement: a round trip costs the entry
    half-spread AND the exit half-spread, so recording only the entry would hand #523 half a
    number and call it whole. A SELL is never refused on its book here -- it is written down.

    **A resting order records the book it was SUBMITTED into, which is not the book it fills
    in.** `place_bracket` comes through `_run_order` too, and its `BracketGTC` waits at the
    exchange for hours or days. That is why the columns are named `submit_`. `order_type`
    cannot be the discriminator -- `_order_row` writes `'market'` on every row it produces --
    so a reader measuring realised spread cost excludes the bracket legs by their id in
    `positions.bracket_order_id`. Recorded rather than suppressed because one uniform rule
    ("whatever the preview carried, on every live row") is easier to reason about than a
    per-side allowlist, and because a bracket's submit-time book is real data about the venue.

    **The wrapper, not the `try` inside `_preview_book`.** `_preview_book` is safe against the
    fields it reads but not against a `detail` that is not a mapping at all (a list would raise
    `AttributeError` on `.get`) -- and until now nothing called it on the SELL path, so this
    change would newly expose exits to that. The precedent is `_record_trade_scope_confirmed`,
    wrapped so a failing metadata write could not cost a live position its protective bracket:
    a DIAGNOSTIC column must never be able to decide whether an order is placed. `(None, None)`
    is the same "not observed" this column already means, and the failure is logged rather than
    swallowed silently, because a preview shape that stopped being readable is a venue change
    an operator needs to see.
    """
    if preview is None:
        return None, None
    try:
        return _preview_book(preview)
    except Exception:
        log_exception(logger, "executor.submit_book_unreadable")
        return None, None


def _warn_if_market_routing_overrides_entry(
    intent: OrderIntent,
    preview: Preview | None,
    spec: OrderSpec | None = None,
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
    caller passing its own non-market spec -- a future resting order out of
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
    if spec is None:
        # Same resolution `_run_order` performs: no explicit spec means the default routing,
        # which today is always market.
        try:
            spec = _order_spec(intent)
        except SizePrecisionUnavailable:
            # This function is a DIAGNOSTIC (it records how far the venue's book sat from the
            # intent). An order whose size cannot be serialised has already been refused
            # upstream, so there is nothing to measure and nothing to report -- and a diagnostic
            # must never be the thing that raises out of the order path.
            return
    # The KIND, off the spec, rather than the first key of a wire dict. `market_ioc_by_quote`
    # and `market_ioc_by_base` are the two market kinds; a limit, stop or bracket spec is not on
    # the override path.
    if not spec.kind.startswith("market_"):
        return
    ref = _preview_best_ask(preview)
    if ref is None:
        return
    try:
        expected = Decimal(str(intent.entry))
    except InvalidOperation, TypeError, ValueError:
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
    preview: Preview | None,
    max_entry_spread_pct: Decimal,
) -> _SpreadGateRefusal | None:
    """Refuse a live BUY whose previewed book is too wide to enter (#350). `None` = proceed.

    **What it decides.** For a BUY on the live path, `(best_ask - best_bid) / mid` at or
    beyond `execution.max_entry_spread_pct` (default 0.005 = 50bp) refuses the order BEFORE
    the confirm gate and placement. 50bp was chosen (#334) as the backtest's slippage cap:
    the backtest then assumed no more than 50bp of per-leg slippage even on the thinnest book,
    so a spread AT the threshold had already consumed the model's entire worst-case cost while
    the taker fee rode outside it.

    #523 moved that cap to the corpus tail (183.8bp) and deliberately did NOT move this gate,
    which is why the two are now separate numbers rather than one. Loosening a live entry rail
    is its own decision, not a side effect of correcting a research cost model, and the drift
    runs in the safe direction: the gate is now STRICTER than the model's worst case rather
    than equal to it, refusing books the model would be willing to price. 50bp remains the
    per-leg cost the model assumes for a $5M/day book -- a spread that wide is already several
    times what any liquid book charges, and the fill economics are materially worse than
    anything the rule was measured on. The
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
    The real venue's preview carries both sides for market orders (the Coinbase adapter maps
    `best_bid`/`best_ask` into `Preview.detail`), so an unreadable book on the live path means
    a degraded response -- exactly the moment not to spend.

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
            "execution.max_entry_spread_pct, so the spread by itself costs more per leg than "
            "the model assumes for a $5M/day book (#334 set this at the backtest's slippage "
            "cap; #523 moved that cap and left this threshold where it was) -- entries into "
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


def _order_row(
    intent: OrderIntent, mode: str, now_ts: int, preview: Preview | None = None
) -> dict[str, Any]:
    # Routed MARKET unconditionally (#258's faithful-engine decision): `expected_fill` below
    # records the rule's intended entry even though execution ignores it -- the override
    # #260's routing-time warning (`_warn_if_market_routing_overrides_entry` in `_run_order`)
    # exists to make visible rather than silent.
    submit_best_bid, submit_best_ask = _submit_book(preview)
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
        # #626: the venue's own book at submit, from the preview `_run_order` JUST fetched --
        # the same one #350's spread gate read. Written on the INSERT that already precedes
        # placement, so this adds no statement, no round trip and no failure mode to the order
        # path: the row either goes in with two more columns or the order was never placed,
        # exactly as today. `_submit_book` cannot raise. The RAW PAIR, never a spread: see the
        # column comment in `data/db.py` for why a derivation would be the lossy choice.
        submit_best_bid=submit_best_bid,
        submit_best_ask=submit_best_ask,
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


def _order_spec(intent: OrderIntent) -> OrderSpec:
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
        return MarketIOCByQuote(product_id=intent.product_id, side=Side.BUY, quote_size=notional)
    return MarketIOCByBase(
        product_id=intent.product_id, side=Side.SELL, base_size=_sell_base_size(intent)
    )


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


def _initial_status(spec: OrderSpec) -> str:
    """A market (IOC) order fills immediately; a limit/stop-limit/bracket order rests as
    `pending` on the exchange until a later fill event. `execution.reconcile`, run at the top of
    every cycle, observes that fill and marks it `filled` with the observed price and fees.

    **Driven off `spec.kind`, and deliberately NOT off `spec.initial_status` (#524).** Every
    `OrderSpec` carries an `initial_status` ClassVar, and reaching for it here is the obvious
    move and the wrong one: the port's vocabulary is the VENUE's (`filled_or_rejected`, `open`)
    and this column is KEEL's (`filled`, `pending`). They describe the same moment in different
    words, and the words are not interchangeable -- `reconcile` sweeps for `pending`, so writing
    `open` into this column would leave every resting order invisible to the sweep that exists to
    observe its fill. A bracket recorded as `open` is a protective order keel would never look at
    again.

    The dict inspection this replaced (`next(iter(order_configuration), "")`) is gone either way;
    what stays is keel's own status vocabulary, mapped explicitly.
    """
    return "filled" if spec.kind.startswith("market_") else "pending"


class CancelUnavailable(RuntimeError):
    """Raised when a resting order cannot be cancelled AT THE EXCHANGE.

    Never downgrade this to a no-op. Both cancel sites used to do
    `cancel = getattr(broker, "cancel_order", None)` and skip when absent -- and at the time the
    real client had no `cancel_order` at all, only the test fakes did, so in production the
    cancel was always skipped while the row was still marked `canceled`, leaving a LIVE resting
    SELL on the exchange that our own records said was gone.

    `CoinbaseClient.cancel_order` exists now, but the guard still matters and now covers a
    second case: the port's `cancel_order` answers `CancelOutcome`, and a REFUSED (already
    filled, unknown id) recorded as a success is the same lie by a different route.

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
    """The broker-native order id for a repo order row, read back out of `raw_response` -- used
    to cancel a specific broker order.

    Rows written before #524 hold the entire pre-port placement response; rows written since hold
    `{"order_id": ...}` alone. Both are read by the same `data.get("order_id")`, which is why the
    narrowing needed no migration: the key that mattered is the key that was kept."""
    raw = order_row.get("raw_response")
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except TypeError, ValueError:
        return None
    return data.get("order_id")


def _bracket_spec(
    product_id: str,
    qty: Decimal,
    target: Decimal,
    stop: Decimal,
    base_increment: Decimal | None = None,
) -> BracketGTC:
    """The exit bracket as a port value: ONE order carrying both protective prices.

    **This used to render Coinbase's wire dict itself.** #502 stage 1 added `BracketGTC` to the
    port and shipped a test pinning the two byte-identical, because two renderers of one order
    existed and had to be kept in agreement. #524 removed the second one: the spec goes to
    the broker's `place_order`, which renders it through the adapter package's own
    `to_order_configuration`, and the parity test goes with the duplicate it was pinning.

    What the port adds beyond a shared renderer is REFUSAL. `BracketGTC.__post_init__` rejects a
    non-positive price and a stop that does not sit on the losing side of the target -- equal legs
    as firmly as inverted ones, because an equal-leg "bracket" is a stop and a target racing at
    the same price. The dict this replaced checked none of that; `_roll_stop` grew its own guard
    for the same hazard (#560) and now has a second line behind it.

    #516's quantization is unchanged and still happens HERE, before the spec is built: quantize
    down when the increment is known, send unchanged when it is not. A bracket the venue refuses
    leaves a position unprotected, so this path must never become more likely to fail than it was.
    """
    size = (
        qty
        if base_increment is None or base_increment <= 0
        else _floor_or_original(qty, base_increment)
    )
    return BracketGTC(
        product_id=product_id,
        # A bracket keel places always EXITS a long: keel enters with a market IOC and protects
        # afterwards, so the protective order is a SELL. `BracketGTC` derives the stop's trigger
        # direction from this rather than taking it as a field.
        side=Side.SELL,
        base_size=size,
        take_profit_price=target,
        stop_trigger_price=stop,
    )


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

    ONE native trigger-bracket order (see `_bracket_spec`), so the exchange owns
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
        spec=_bracket_spec(
            product_id, qty, target, stop, _base_increment_for(broker, repo, product_id, now_ts)
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
    """Sell `qty` of an open position and RESIZE its protective bracket down to the remainder.

    The rule-driven profit-take -- "sell half at the first target, let the rest run" -- and the
    one primitive in this module that changes how much a position is worth AND how much of it is
    protected in the same breath. Both halves happen here, because a caller that did one without
    the other would be worse than a caller that did neither.

    **Why the sell alone was never enough.** Until #502 this function placed the SELL through
    `_run_order` and stopped. `_run_order` is the pipeline BELOW `execute`, so the partial sell
    skipped `_clear_resting_bracket` -- and the resting native bracket commits the ENTIRE base
    position. On spot the base is locked and the partial SELL is simply rejected; if it did fill,
    a bracket sized for the whole position is left able to sell inventory no longer held. Nor
    was any outcome recorded, so the profit taken here vanished and the runner's eventual
    break-even stop-out was booked as the trade's only result: rail 16 counting a net winner as
    a loss. `test_scale_out_has_no_production_caller` was the tripwire holding the door shut on
    all of that, and it is retired with this change rather than by it.

    **The choreography, and why it is in this order.**

    1. The crash ledger is written FIRST, before any venue call (#519's pattern -- see
       `_roll_stop`, which this deliberately mirrors line for line). Everything after it can die
       mid-flight, and the cancel below removes the position's only protection.
    2. The resting bracket is CANCELLED. It has to go first: it already commits the whole
       position, so a partial SELL placed beside it is the base-locked rejection above.
    3. The partial SELL is placed, through the same guard -> preview -> place -> log pipeline as
       every other order (autonomous because it is system-initiated, never guard-exempt).
    4. The sold fraction is BOOKED against the `positions` ledger -- FIFO, the tranche it stops
       inside reduced rather than closed (`streak.book_exit`). This must happen before the
       re-place, because the sweep sizes a healing bracket from the tranche's `qty`.
    5. A bracket for the REMAINDER is placed at the levels the position already had.

    **Re-placing SMALLER is the genuinely new behaviour.** `_roll_stop` cancels and re-places at
    the SAME quantity at a new price; this re-places at the same price at a NEW quantity. The
    order itself needs nothing new -- `BracketGTC` has expressed it since #569 -- but no path
    before this one asked the venue to protect less than it did a moment ago.

    **Refusals, all before the venue is touched.** A non-positive `qty`; a `qty` at or above what
    is held (that is a full exit, and a full exit is `execute`'s EXIT path, which also retires
    `position_rule:` and the rest of the per-position state this function does not own); and a
    position with no recorded `open_stop`/`open_target`. The last is the subtle one: without
    levels there is nothing to re-place, so cancelling would leave the remainder naked with no
    ledger entry saying so -- and that silence is worse than not scaling out at all.
    """
    if qty <= 0:
        return ExecutionResult(
            placed=False,
            order_id=None,
            vetoed_by=[],
            preview=None,
            reason=f"scale_out: qty must be positive, got {qty}",
        )

    held, _avg_cost = _held_position(repo, product_id)
    if qty >= held:
        return ExecutionResult(
            placed=False,
            order_id=None,
            vetoed_by=[],
            preview=None,
            reason=(
                f"scale_out: {qty} is not a fraction of the {held} held in {product_id} -- a "
                "full exit must go through execute()'s EXIT path, which also retires the "
                "position's rule ownership and levels"
            ),
        )
    remainder = held - qty

    stop = repo.get_state(f"open_stop:{product_id}")
    target = repo.get_state(f"open_target:{product_id}")
    if stop is None or target is None:
        return ExecutionResult(
            placed=False,
            order_id=None,
            vetoed_by=[],
            preview=None,
            reason=(
                f"scale_out: no open_stop/open_target recorded for {product_id} -- refusing to "
                "cancel a bracket that could not then be re-placed for the remainder, which "
                "would leave the rest of the position unprotected and with no levels to heal "
                "from"
            ),
        )

    log_event(
        logger,
        logging.INFO,
        "executor.scale_out_requested",
        product=product_id,
        qty=qty,
        exit_price=exit_price,
        rule=rule_name,
        remainder=remainder,
    )

    # THE CRASH LEDGER, written BEFORE the venue is touched (#519), for the same reason and with
    # the same key as `_roll_stop`: the cancel below is not atomic with the re-place at the end,
    # and a process that dies in the gap must leave behind something the next cycle's sweep can
    # converge -- not a live position that looks exactly like a DCA holding with no stop.
    #
    # The `qty` recorded is the REMAINDER, the size this sequence intends to end at. Note the
    # sweep does not read it: `reconcile_unbracketed_positions` sizes from the TRANCHE ("the
    # ledger is what is actually held now") and takes only the levels from here. That is what
    # makes the record correct in BOTH crash windows rather than only one -- die before the sell
    # and the tranche still says the full size, so the sweep heals the full position; die after
    # the sell is booked and the tranche says the remainder, so it heals the remainder.
    repo.set_state(
        f"{UNBRACKETED_PREFIX}{product_id}",
        {"stop": stop, "target": target, "qty": remainder},
    )

    if not _clear_resting_bracket(broker, repo, product_id, now_ts):
        # Nothing was cancelled, so the bracket is still resting and the position is still fully
        # protected. The ledger record just written is LEFT STANDING and is harmless for exactly
        # the reason `_roll_stop` documents: the sweep skips any product whose bracket is still
        # resting, and the levels it holds are the ones already in force.
        return ExecutionResult(
            placed=False,
            order_id=None,
            vetoed_by=[],
            preview=None,
            reason=(
                f"could not cancel the resting exit bracket for {product_id} -- refusing to "
                "place a partial SELL that would be rejected for insufficient funds, or would "
                "fill and leave a live bracket able to sell inventory we no longer hold"
            ),
        )

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
    result = _run_order(intent, broker, repo, config, "autonomous", None, now_ts)

    if not result.placed:
        # The bracket is already cancelled and the sell did not happen, so the FULL position is
        # naked right now. The ledger record stays standing -- it is what the sweep re-places
        # from -- and the sweep will size it from the tranche, which still says the full
        # quantity because nothing was booked. The CRITICAL is not downgraded on the strength of
        # that automatic recovery: this deployment cycles once per UTC day.
        log_event(
            logger,
            logging.CRITICAL,
            "executor.position_unprotected",
            product=product_id,
            reason=result.reason,
            attempted_qty=str(qty),
            detail=(
                "the resting bracket was cancelled for a partial scale-out and the SELL was "
                "then REJECTED -- the whole position currently has no protective stop at the "
                "exchange. The unbracketed record is retained so the next cycle's sweep "
                "re-places it at the full held size."
            ),
        )
        return result

    _book_scale_out(repo, config, product_id, result.order_id, now_ts)

    # The remainder's bracket, at the levels the position already had. `place_bracket` owns the
    # rest of the protocol on both outcomes: it writes `open_stop`/`open_target` and clears the
    # crash ledger on success, and re-writes the ledger (now sized from the tranche the booking
    # just shrank) and warns on a veto or rejection.
    bracket_order_id = place_bracket(
        broker,
        repo,
        config,
        product_id=product_id,
        qty=remainder,
        stop=stop,
        target=target,
        rule_name=rule_name,
        now_ts=now_ts,
    )
    if bracket_order_id is None:
        log_event(
            logger,
            logging.CRITICAL,
            "executor.position_unprotected",
            product=product_id,
            attempted_qty=str(remainder),
            detail=(
                "the partial SELL filled but the REMAINDER's bracket was vetoed or rejected -- "
                "the rest of the position has no protective stop at the exchange. The "
                "unbracketed record is retained so the next cycle's sweep re-places it."
            ),
        )
    else:
        # Repoint the surviving tranche at the new bracket, for the reason `_roll_stop`
        # documents: `get_position_for_bracket` is the ONE linkage direction reconciliation has,
        # and a tranche still naming the bracket cancelled above would orphan this one's
        # eventual fill -- dropping the `trade_outcomes` row that closes the scaled-out trade,
        # which is the very row this function exists to make correct.
        for position in repo.get_open_positions(product_id):
            repo.set_position_bracket(position["id"], bracket_order_id)

    return replace(result, bracket_order_id=bracket_order_id)


def _book_scale_out(
    repo: Repository,
    config: Config,
    product_id: str,
    order_id: int | None,
    now_ts: int,
) -> None:
    """Attribute a filled partial SELL to the `positions` ledger (#502).

    Booking lives INSIDE `scale_out` rather than in its caller so that wiring a rule to this
    primitive cannot get half of it. The tripwire that used to stand here said "before wiring
    it: cancel/resize the resting bracket, AND record a trade outcome" -- two obligations on a
    caller that does not exist yet. A caller cannot forget an obligation it does not carry.

    The quantity booked is the VENUE's (`filled_quantity`, falling back to the ordered size when
    the status endpoint said nothing), never the requested one: a scale-out the venue filled
    short must reduce the tranche by what actually sold, or the ledger claims base was released
    that the account still holds.

    A sell with no observed fill PRICE is not booked, matching `_handle_exits` and
    `record_closed_trade`: inventing an exit price would fabricate the P&L that feeds rail 16.
    The tranche then keeps its full size, which is the conservative direction -- the sweep will
    size a healing bracket for MORE than is held and be refused loudly, rather than for less and
    leave part of the position quietly unprotected.
    """
    exit_order = repo.get_order(order_id) if order_id is not None else None
    if exit_order is None or exit_order["actual_fill"] is None:
        log_event(
            logger,
            logging.WARNING,
            "executor.scale_out_unbooked",
            product=product_id,
            order_id=order_id,
            detail=(
                "the partial SELL was placed but carries no observed fill price, so no ledger "
                "attribution was made -- the tranche keeps its full size and its outcome will "
                "be computed against a quantity larger than is now held"
            ),
        )
        return

    sold = exit_order.get("filled_quantity") or exit_order["qty"]
    streak.book_exit(
        repo,
        config,
        product_id=product_id,
        exit_order=exit_order,
        sold_qty=sold,
        # A scale-out is a rule-driven profit-take and never DCA: the `OrderIntent` above already
        # commits to that (`is_dca=False`), so deriving a different answer here would have the
        # rails and the streak disagree about the same order. DCA accumulates on a fixed budget
        # and has no target to take half off at.
        is_dca=False,
        now_ts=now_ts,
    )


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

    **The kill-switch window inside a roll.** A cancel is not rail-gated -- it REMOVES risk, and
    there is nothing for a guard to veto -- so a kill switch that engages mid-roll, AFTER the
    cancel, fails only the REPLACEMENT: rail 12 fails every order closed and the position is left
    unbracketed until the switch clears. Never trading against a thrown switch is the correct
    failure direction, but the window must be understood, not discovered: the CRITICAL below is
    loud, and `reconcile_unbracketed_positions` heals from the crash ledger when cycles resume.

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
    # flip wearing a protective order's name" -- and the spec built below IS one of those
    # (#524/#569: every order this module places is a port value now), so the construction check
    # itself is one line behind this guard. #560 added this earlier, explicit refusal for the
    # same hazard because it predates the spec-shaped live path; it stays because it refuses
    # BEFORE the cancel, leaving the existing bracket in force.
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
        spec=_bracket_spec(
            product_id, qty, target, new_stop, _base_increment_for(broker, repo, product_id, now_ts)
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
    # Repoint the owning tranche at the replacement (#502). `get_position_for_bracket` is the
    # ONE linkage direction reconciliation has: it resolves a bracket FILL back to the trade it
    # closed. Until rolls were reachable this link could not go stale here -- `place_bracket`
    # and the sweep's re-place both set it -- but a roll that cancels the bracket a tranche
    # names and leaves the name behind orphans the replacement: when it eventually fills, the
    # fill resolves to no tranche, its `trade_outcomes` row is dropped, and rail 16 miscounts a
    # managed winner as nothing at all. `None` (no tranche names the old order -- e.g. a tranche
    # predating the ledger) is skipped silently: the roll is still correct, only the attribution
    # is absent, exactly as it was before the ledger existed.
    position = repo.get_position_for_bracket(old_stop_order_id)
    if position is not None:
        repo.set_position_bracket(position["id"], result.order_id)
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


def roll_stop_to(
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
    """Roll the protective bracket's stop to `new_stop` -- the GENERAL, single-roll entry point.

    `roll_to_break_even` and `trail_stop_atr` are the two named special cases (a level derived
    from the entry, a level derived from an ATR multiple), and each performs its own full
    cancel-and-replace. A rule carrying BOTH exit knobs can win on both in one cycle, and
    calling the two named primitives back-to-back would walk #519's cancel-before-place window
    TWICE against the same position for no benefit. The caller that faces that case --
    `agent.run_once`'s live stop-management step (#502) -- therefore computes ONE ratcheted
    level (`strategy.exit_policy.next_stop`, the max over the arms, the same function the
    sim/backtest engines apply) and hands it here, so exactly one replacement is placed.

    Every `_roll_stop` guarantee applies unchanged: refusal on widening, refusal at/above the
    target, the crash ledger before the venue is touched, cancel before place, and the
    tranche-bracket repoint on success. `None` means the existing bracket stays in force.
    """
    return _roll_stop(
        broker, repo, config, product_id, old_stop_order_id, new_stop, qty, rule_name, now_ts
    )


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
