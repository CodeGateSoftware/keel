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


def reconcile_open_orders(
    broker: Any, repo: Repository, config: Config, now_ts: int
) -> list[int]:
    """Bring every locally-`pending` live order into agreement with the exchange.

    Returns the ids of orders whose state changed. Never raises for a single unreadable order:
    this runs at the top of every cycle, and one bad id must not blind the agent to every other
    fill. A broker failure leaves that order `pending` for the next cycle, which is the truthful
    state -- we simply do not know yet.
    """
    changed: list[int] = []

    for row in repo.get_orders(mode="live", status="pending"):
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
            # Still resting -- including a PARTIAL fill, which has NOT closed the position.
            # Recording a partial as a full exit would book P&L for size that never sold and
            # release a position still partly held. Left for a later cycle.
            continue

        _try_record_fill(broker, repo, config, row, observed, now_ts)
        changed.append(row["id"])

    return changed




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


def _escalate_unprotected(
    repo: Repository, row: dict[str, Any], qty: Decimal, why: str
) -> None:
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
