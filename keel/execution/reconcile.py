"""Observe what resting orders actually did at the exchange, and record it.

This is the half of bracket handling that never existed. `place_bracket` leaves a native
trigger-bracket resting at Coinbase until price reaches the stop or the target, but a placement
response only says the order was ACCEPTED -- nothing in it reveals the later fill. `run_once`
never re-read order status, so a stop-out (the dominant source of losses) closed a position the
agent never noticed: the row stayed `pending`, `_held_position` kept counting sold inventory as
held, `position_rule` was never cleared, and no `trade_outcomes` row was written. Rails 11 and 16
were blind to the entire category, and rail 16 saw only voluntary rule exits -- systematically
under-counting exactly the losing side it exists to react to.

It also upgrades two numbers from modelled to OBSERVED. The executor records `actual_fill` as the
*expected* price and `fee` as the *previewed* commission, because at placement time those are the
only figures available. `average_filled_price` and `total_fees` are what the exchange actually
charged, so a reconciled exit carries real economics into `pnl_net` rather than an estimate.

PARTIAL FILLS (#446) are a distinct, NON-terminal state here. When the venue reports an order
with `0 < filled_size < ordered qty`, the row moves to `partially_filled` and carries the
observed fill in `filled_quantity` (`qty` stays the ordered size -- a partial is two numbers,
not one rewritten) with the venue's running average price in `actual_fill`. The venue reports
that average already weighted across the fills so far, so each poll records the exact
quantity-weighted average; the sweep keeps polling the row -- `partially_filled` never leaves
the polling set -- until it goes terminal (full fill, or dead with the sold part booked).

How often this fires, honestly: rarely, by construction. keel places market IOC orders on
liquid spot pairs at risk-sized notional ($400-24k typical per `config.live.yaml`, sized at 1%
equity risk), where a market IOC sweeps the top of a deep book and partial fills are the thin-book
case, not the norm. No production frequency data exists to cite -- the state simply was not
recorded before this change, which is the gap itself. That rarity changes the priority of
auto-remediation, not the validity of recognizing the state.

What is deliberately NOT done here: resizing or amending the bracket when a partially-filled
entry leaves it oversized for what is held. The broker port has no bracket/OCO kind (#502); the
live bracket bypasses it with a raw dict, and auto-cancelling live protective orders on the
strength of a possibly-still-settling partial snapshot is strictly worse than a loud warning.
This module records and surfaces; the amend-vs-cancel-and-replace policy is #502's.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any

from keel_core.telemetry import log_event, log_exception

from keel.config import Config
from keel.data.repository import Repository
from keel.execution import executor, streak
from keel.execution.executor import _held_position
from keel.types import Side

logger = logging.getLogger(__name__)

# Coinbase order states. Anything not terminal is left alone for a later cycle.
_FILLED = "FILLED"
_DEAD = frozenset({"CANCELLED", "CANCELED", "EXPIRED", "FAILED"})

#: The locally-recorded statuses the sweep keeps polling (#446). `pending` is "nothing observed
#: yet"; `partially_filled` is "the venue has begun executing it" -- non-terminal, so it MUST
#: stay in the polling set, or the state this module just wrote would take the order out of its
#: own sweep and the remainder would never be observed. It is the SAME tuple executor treats as
#: "a bracket is still resting" (`executor.RESTING_STATUSES`), aliased rather than restated so
#: the sweep that observes partials and the cancel-before-place paths that clear them can never
#: drift apart: a status one side stopped seeing would be a bracket nobody cancels, or a cancel
#: for an order nobody watches.
_POLLED_STATUSES = executor.RESTING_STATUSES

#: The distinct non-terminal partial-fill state (#446). A free-text column, not a constrained
#: enum, so no migration was needed for the VALUE -- only for the `filled_quantity` column that
#: carries the observed fill alongside it.
_PARTIALLY_FILLED = "partially_filled"


def reconcile_open_orders(broker: Any, repo: Repository, config: Config, now_ts: int) -> list[int]:
    """Bring every locally-unterminated live order into agreement with the exchange.

    Returns the ids of orders whose state changed. Never raises for a single unreadable order:
    this runs at the top of every cycle, and one bad id must not blind the agent to every other
    fill. A broker failure leaves that order `pending` for the next cycle, which is the truthful
    state -- we simply do not know yet.
    """
    changed: list[int] = []

    for row in _polled_rows(repo):
        native_id = _native_order_id(row)
        if native_id is None:
            # Placed but with no id echoed back: we cannot ask about it, and guessing would be
            # worse than leaving it. Logged so it does not sit here silently forever.
            log_event(
                logger,
                logging.WARNING,
                "reconcile.order_has_no_broker_id",
                order_id=row["id"],
                product=row["product_id"],
            )
            continue

        try:
            observed = broker.get_order(native_id)
        except Exception:
            log_exception(
                logger,
                "reconcile.order_status_unavailable",
                order_id=row["id"],
                native_order_id=native_id,
            )
            continue

        status = (observed.get("status") or "").upper()

        if status in _DEAD:
            # A CANCELLED/EXPIRED order can still have SOLD something: Coinbase reports
            # `filled_size > 0` for an order that partly filled before being cancelled (thin
            # book, self-trade prevention). Marking it merely `canceled` drops that fill on the
            # floor -- `_held_position` sums only `filled` rows, so it would keep reporting the
            # FULL position held, and the realized P&L on the sold portion would never reach
            # rails 11 or 16. Record what actually sold, then stop tracking the order.
            if (observed.get("filled_size") or Decimal("0")) > 0:
                _try_record_fill(broker, repo, config, row, observed, now_ts)
            else:
                repo.update_order(row["id"], status="canceled", updated_at=now_ts)
                log_event(
                    logger,
                    logging.INFO,
                    "reconcile.order_closed_unfilled",
                    order_id=row["id"],
                    product=row["product_id"],
                    status=status,
                )
                _rebracket_or_escalate(broker, repo, config, row, now_ts)
            changed.append(row["id"])
            continue

        if status != _FILLED:
            # Still resting -- but possibly one the venue has BEGUN executing. A partial fill
            # has not closed the position: recording it as a full exit would book P&L for size
            # that never sold and release a position still partly held. It IS, however, its own
            # non-terminal state with its own observed economics (#446) -- not a `pending` row
            # indistinguishable from one the venue never touched.
            if _record_partial_fill(repo, row, observed, now_ts):
                changed.append(row["id"])
            continue

        _try_record_fill(broker, repo, config, row, observed, now_ts)
        changed.append(row["id"])

    return changed


def _polled_rows(repo: Repository) -> list[dict[str, Any]]:
    """The live rows this sweep owns: every status that is not terminal, oldest first."""
    rows: list[dict[str, Any]] = []
    for status in _POLLED_STATUSES:
        rows.extend(repo.get_orders(mode="live", status=status))
    return sorted(rows, key=lambda r: r["id"])


def _record_partial_fill(
    repo: Repository, row: dict[str, Any], observed: dict[str, Any], now_ts: int
) -> bool:
    """Record `0 < filled_size < ordered qty` as the distinct non-terminal `partially_filled`
    state. Returns whether anything changed.

    `qty` stays the ORDERED size and the observed fill goes in `filled_quantity` -- a partial is
    two numbers, and rewriting `qty` would destroy the comparison that makes the state
    recognizable. `actual_fill`/`fee` take the venue's running figures, which for
    `average_filled_price` are already the quantity-weighted average across every fill so far.

    Idempotent by observation: a resting partial that has not moved re-writes nothing and stays
    quiet, because this sweep runs every cycle and a warning that fires nightly for an unchanged
    order trains the operator to ignore the one that matters. A price the venue did not report
    (its normalized 0) is left alone rather than recorded -- a zero would read downstream as a
    free position in the rail-8 basis.
    """
    ordered = row["qty"] or Decimal("0")
    filled = observed.get("filled_size") or Decimal("0")
    if not (Decimal("0") < filled < ordered):
        return False

    average = observed.get("average_filled_price") or Decimal("0")
    fees = observed.get("total_fees") or Decimal("0")
    previously = row.get("filled_quantity")

    fields: dict[str, Any] = {
        "status": _PARTIALLY_FILLED,
        "filled_quantity": filled,
        "updated_at": now_ts,
    }
    if average > 0:
        fields["actual_fill"] = average
    if fees > 0:
        fields["fee"] = fees

    # An unchanged observation (same filled quantity on an already-partial row) is a no-op.
    if previously is not None and previously == filled and row["status"] == _PARTIALLY_FILLED:
        return False

    repo.update_order(row["id"], **fields)
    log_event(
        logger,
        logging.WARNING,
        "reconcile.order_partially_filled",
        order_id=row["id"],
        product=row["product_id"],
        filled=str(filled),
        ordered=str(ordered),
        remaining=str(ordered - filled),
        average_price=str(average) if average > 0 else "unreported",
        detail=(
            "the venue has executed part of this order and the rest is still resting -- the "
            "position basis now includes this fill; if this is an entry leg, check that its "
            "bracket is not sized for more than is held (#502 tracks the resize policy)"
        ),
    )
    return True


def reconcile_unbracketed_positions(
    broker: Any, repo: Repository, config: Config, now_ts: int
) -> list[int]:
    """Give a bracket to every held tranche that has none, or escalate. Returns healed ids.

    `_rebracket_or_escalate` below heals a bracket that WAS accepted and later died. It cannot
    reach a bracket that was never placed at all, because it is driven from
    `reconcile_open_orders`, which iterates the non-terminal rows (`pending` and
    `partially_filled`, `_POLLED_STATUSES`): a broker rejection writes `rejected` and a rails
    veto writes no row whatsoever (the insert happens after the guard gate). Neither is polled,
    so before this pass nothing revisited the tranche and the position stayed naked for a full
    cycle -- one per UTC day -- behind a WARNING (issue #195).

    Driven from the `positions` ledger rather than from `orders`, because the ledger is the only
    place that knows a tranche is HELD. A tranche is considered protected while its bracket is
    still working at the exchange -- `pending`, `partially_filled` (the unfilled remainder still
    rests, #446), or `filled` (the position is on its way out, and re-placing against it would
    double-sell) -- see `_has_resting_bracket`; any other status means nothing is resting at the
    exchange.

    A tranche with no recorded `unbracketed:` levels is skipped SILENTLY, not escalated. That is
    DCA's correct resting state -- it carries no stop by design -- and escalating it would fire a
    CRITICAL for every DCA tranche on every cycle, which trains the alert to be ignored.
    """
    healed: list[int] = []

    for position in repo.get_open_positions():
        product_id = position["product_id"]
        if _has_resting_bracket(repo, position):
            continue

        intent = repo.get_state(f"{executor.UNBRACKETED_PREFIX}{product_id}")
        if not intent:
            continue

        # Stale ledger state, not an exposure. A protective SELL with nothing to sell would be a
        # naked short -- a shape keel cannot hold -- so the record is retired rather than acted on.
        held_qty, _avg_cost = _held_position(repo, product_id)
        if held_qty <= 0:
            repo.set_state(f"{executor.UNBRACKETED_PREFIX}{product_id}", None)
            continue

        # Size from the TRANCHE, matching `_rebracket_or_escalate`: the recorded `qty` is what the
        # failed attempt tried, but the ledger is what is actually held now.
        qty = position["qty"]

        # Per-position isolation, for the same reason the status fetch has it: placing an order
        # reaches the network, and one unreachable product must not abandon the rest of the sweep
        # -- nor swallow the escalation below, which would leave the position naked AND silent.
        try:
            new_id = executor.place_bracket(
                broker,
                repo,
                config,
                product_id=product_id,
                qty=qty,
                stop=intent["stop"],
                target=intent["target"],
                rule_name=position.get("rule_name") or "rebracket",
                now_ts=now_ts,
            )
        except Exception:
            log_exception(
                logger,
                "reconcile.rebracket_failed",
                position_id=position["id"],
                product=product_id,
            )
            new_id = None

        if new_id is None:
            # `place_bracket` has re-written the `unbracketed:` record on its way out, so the
            # retry survives to the next cycle rather than being stranded here.
            _escalate_unprotected_position(
                position, qty, "bracket placement was vetoed or rejected again"
            )
            continue

        repo.set_position_bracket(position["id"], new_id)
        healed.append(position["id"])
        log_event(
            logger,
            logging.WARNING,
            "reconcile.unbracketed_position_protected",
            product=product_id,
            position_id=position["id"],
            new_order_id=new_id,
            stop=intent["stop"],
            target=intent["target"],
        )

    return healed


def _has_resting_bracket(repo: Repository, position: dict[str, Any]) -> bool:
    """Whether this tranche still has a bracket working at the exchange."""
    bracket_id = position.get("bracket_order_id")
    if bracket_id is None:
        return False
    order = repo.get_order(bracket_id)
    if order is None:
        return False
    # `partially_filled` is still RESTING (#446): the unfilled remainder is working at the
    # exchange, so the tranche is protected -- treating it as gone would re-place a bracket
    # against inventory the remainder already commits.
    return str(order["status"]) in {"pending", "filled", _PARTIALLY_FILLED}


def _escalate_unprotected_position(position: dict[str, Any], qty: Decimal, why: str) -> None:
    log_event(
        logger,
        logging.CRITICAL,
        "reconcile.position_unprotected",
        product=position["product_id"],
        position_id=position["id"],
        held_qty=str(qty),
        reason=why,
        detail=(
            "this tranche is held with NO protective stop at the exchange and a replacement "
            "could not be placed -- re-place one or close it before trading on"
        ),
    )


def _rebracket_or_escalate(
    broker: Any, repo: Repository, config: Config, row: dict[str, Any], now_ts: int
) -> None:
    """Re-place the exit bracket for a still-held position whose bracket died, or escalate.

    Leaving a naked position and logging CRITICAL is right at the instant of detection but wrong
    as a resting state: nothing else revisits the order (it is no longer `pending`), so without
    this the position stays unprotected until a human notices.

    The recorded `open_stop`/`open_target` are reused deliberately rather than recomputed: they
    are the levels the ORIGINAL trade was risk-sized against, and inventing new ones here would
    silently re-risk the position on a level no rule produced.
    """
    if str(row["side"]).upper() != Side.SELL.value.upper():
        return
    product_id = row["product_id"]
    if _held_position(repo, product_id)[0] <= 0:
        return

    # Size from the TRANCHE that owned this bracket, not from `_held_position` (the whole
    # product). With two tranches open, re-placing at the aggregate would commit inventory a
    # sibling's bracket already holds -- rejected on spot for insufficient funds, turning a
    # recoverable single-bracket death into a CRITICAL and a genuinely naked position.
    position = repo.get_position_for_bracket(row["id"])
    if position is None:
        qty, _avg_cost = _held_position(repo, product_id)
        _escalate_unprotected(repo, row, qty, "no ledger tranche owns this bracket")
        return
    qty = position["qty"]

    stop = repo.get_state(f"open_stop:{product_id}")
    target = repo.get_state(f"open_target:{product_id}")
    if stop is None or target is None:
        _escalate_unprotected(repo, row, qty, "no recorded stop/target to re-place from")
        return

    # The SAME per-order isolation the status fetch and `_record_fill` get. Placing an order
    # reaches the network, and an exception here propagated out of `reconcile_open_orders` and
    # out of `run_once` -- abandoning every remaining pending order, at the top of the cycle,
    # over one unreachable product. It also swallowed the escalation below, so the position was
    # left naked AND silent, which is worse than the warn-only behaviour this replaced.
    try:
        new_id = executor.place_bracket(
            broker,
            repo,
            config,
            product_id=product_id,
            qty=qty,
            stop=stop,
            target=target,
            rule_name=position.get("rule_name") or "rebracket",
            now_ts=now_ts,
        )
    except Exception:
        log_exception(
            logger,
            "reconcile.rebracket_failed",
            order_id=row["id"],
            product=product_id,
        )
        new_id = None

    if new_id is None:
        _escalate_unprotected(repo, row, qty, "replacement bracket was vetoed or rejected")
        return

    # Re-point the tranche at its NEW bracket. Without this the tranche still names the dead
    # order, so when the replacement fills `get_position_for_bracket` finds nothing, `_record_fill`
    # takes the "exit without position context" skip, and the position closes with NO
    # `trade_outcomes` row -- rail 16 blind to the stop-out, which is the exact failure this
    # module was built to end.
    repo.set_position_bracket(position["id"], new_id)

    log_event(
        logger,
        logging.WARNING,
        "reconcile.bracket_replaced",
        product=product_id,
        dead_order_id=row["id"],
        new_order_id=new_id,
    )


def _escalate_unprotected(repo: Repository, row: dict[str, Any], qty: Decimal, why: str) -> None:
    log_event(
        logger,
        logging.CRITICAL,
        "reconcile.position_unprotected",
        product=row["product_id"],
        order_id=row["id"],
        held_qty=str(qty),
        reason=why,
        detail=(
            "the exit bracket is gone from the exchange and could not be replaced -- this "
            "position has NO protective stop. Re-place one or close it before trading on."
        ),
    )


def _try_record_fill(
    broker: Any,
    repo: Repository,
    config: Config,
    row: dict[str, Any],
    observed: dict[str, Any],
    now_ts: int,
) -> None:
    """`_record_fill` with the SAME per-order isolation the status fetch gets.

    Without this a raise inside recording (a locked DB, a bad decimal) propagated out of
    `reconcile_open_orders` and out of `run_once`, leaving every REMAINING pending order
    unreconciled -- which defeats the "one bad order must not blind the agent" contract that was
    only ever enforced for the `get_order` call.
    """
    try:
        _record_fill(broker, repo, config, row, observed, now_ts)
    except Exception:
        log_exception(
            logger,
            "reconcile.record_fill_failed",
            order_id=row["id"],
            product=row["product_id"],
        )


def _record_fill(
    broker: Any,
    repo: Repository,
    config: Config,
    row: dict[str, Any],
    observed: dict[str, Any],
    now_ts: int,
) -> None:
    """Mark `row` filled from OBSERVED economics and, for an exit, close out the position."""
    exit_fill = observed.get("average_filled_price") or Decimal("0")
    fees = observed.get("total_fees") or Decimal("0")
    filled_qty = observed.get("filled_size") or row["qty"]

    if exit_fill <= 0:
        # A FILLED order that reports no price. Feeding 0 to the producer computes
        # (0 - entry_fill) * qty -- a full-notional PHANTOM loss that would be written to
        # `trade_outcomes` and could trip rail 16 on a number nobody observed.
        # `record_closed_trade` already refuses to guess a missing ENTRY price; the exit side is
        # held to the same standard. The row is still marked filled (it did fill) so the order is
        # not re-processed forever -- only the P&L is withheld.
        repo.update_order(row["id"], status="filled", updated_at=now_ts)
        log_event(
            logger,
            logging.WARNING,
            "reconcile.fill_without_observed_price",
            order_id=row["id"],
            product=row["product_id"],
        )
        return

    repo.update_order(
        row["id"],
        status="filled",
        actual_fill=exit_fill,
        fee=fees,
        qty=filled_qty,
        filled_quantity=filled_qty,
        updated_at=now_ts,
    )
    log_event(
        logger,
        logging.INFO,
        "reconcile.order_filled",
        order_id=row["id"],
        product=row["product_id"],
        fill=str(exit_fill),
        fees=str(fees),
    )

    # Only a SELL closes a position. A reconciled BUY (an entry that filled after we recorded it
    # pending) needs its economics corrected, which the update above already did, but it opens a
    # position rather than closing one -- there is no outcome to record.
    if str(row["side"]).upper() != "SELL":
        return

    product_id = row["product_id"]
    # The TRANCHE that owns this bracket -- not `position_rule:<product>`, which held at most one
    # tranche per product and, after averaging up, held the NEWEST one's entry price against the
    # whole holding. An older tranche's bracket filling then booked its P&L against a price it
    # never paid.
    position = repo.get_position_for_bracket(row["id"])
    if position is None:
        # An exit we have no entry context for -- the same case `record_closed_trade` refuses to
        # guess at. Recording it would fabricate a P&L against an entry price nobody observed.
        # Also the resting state for a position opened before the v4 ledger existed.
        log_event(
            logger,
            logging.WARNING,
            "reconcile.exit_without_position_context",
            order_id=row["id"],
            product=product_id,
        )
        return

    streak.record_closed_trade(
        repo,
        config,
        product_id=product_id,
        position=position,
        exit_fill=exit_fill,
        exit_qty=filled_qty,
        fees=fees,
        is_dca=position.get("rule_name") == "dca",
        now_ts=now_ts,
    )

    # Close the tranche only when the fill actually covers it. A bracket CANCELLED after a
    # partial sale routes here too (`filled_size > 0`), and marking that tranche closed would
    # drop the still-held remainder out of the ledger while `_held_position` -- which reads the
    # orders log, not this table -- kept reporting it. The outcome for what did sell is recorded
    # either way; only the release is withheld.
    if filled_qty >= position["qty"]:
        repo.close_position(position["id"], closed_at=now_ts)
    else:
        log_event(
            logger,
            logging.WARNING,
            "reconcile.tranche_partially_closed",
            order_id=row["id"],
            product=product_id,
            position_id=position["id"],
            sold=str(filled_qty),
            tranche_qty=str(position["qty"]),
        )

    # `position_rule` survives only as the exit-rule OWNERSHIP marker its docstring always
    # described; the entry context it used to carry now lives in `positions`. Cleared here only
    # once no tranche of this product remains open, since it names the rule that owns them all.
    if not repo.get_open_positions(product_id):
        repo.set_state(f"position_rule:{product_id}", None)
        repo.set_state(f"open_stop:{product_id}", None)
        repo.set_state(f"open_target:{product_id}", None)
        repo.set_state(f"{executor.UNBRACKETED_PREFIX}{product_id}", None)


def _native_order_id(order_row: dict[str, Any]) -> str | None:
    """The broker-native id stashed in `raw_response` at placement time."""
    raw = order_row.get("raw_response")
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data.get("order_id")
