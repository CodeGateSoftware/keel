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
from keel.execution import streak

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
            repo.update_order(row["id"], status="canceled", updated_at=now_ts)
            changed.append(row["id"])
            log_event(
                logger,
                logging.INFO,
                "reconcile.order_closed_unfilled",
                order_id=row["id"],
                product=row["product_id"],
                status=status,
            )
            continue

        if status != _FILLED:
            # Still resting -- including a PARTIAL fill, which has NOT closed the position.
            # Recording a partial as a full exit would book P&L for size that never sold and
            # release a position still partly held. Left for a later cycle.
            continue

        _record_fill(broker, repo, config, row, observed, now_ts)
        changed.append(row["id"])

    return changed


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

    repo.update_order(
        row["id"],
        status="filled",
        actual_fill=exit_fill,
        fee=fees,
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
    position = repo.get_state(f"position_rule:{product_id}")
    if position is None:
        # An exit we have no entry context for -- the same case `record_closed_trade` refuses to
        # guess at. Recording it would fabricate a P&L against an entry price nobody observed.
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
