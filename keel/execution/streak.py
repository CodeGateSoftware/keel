"""Closed-trade outcome recording and the consecutive-loss streak counters.

This is the PRODUCER half of the split rail 11 was designed for and never got: the agent owns
computation, `guards.py` stays pure and reads precomputed scalars. Rail 16 reads exactly one key
from here -- `streak_halt_until` -- and never the counter, so the "is the threshold reached"
decision lives in one place and cannot disagree with itself.

`pnl_net` is realized and NET OF FEES. That is not a detail: rail 7 exists because fees dominate
small moves, so a trade that is up gross and down net is a loss, and the streak must agree.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from keel_core.telemetry import log_event

from keel.config import Config
from keel.data.repository import Repository

logger = logging.getLogger(__name__)

SECONDS_PER_DAY = 86_400


def record_closed_trade(
    repo: Repository,
    config: Config,
    *,
    product_id: str,
    position: dict[str, Any],
    exit_fill: Decimal,
    exit_qty: Decimal,
    fees: Decimal,
    is_dca: bool,
    now_ts: int,
) -> None:
    """Append the outcome of one fully-closed trade and update the streak counters.

    A `position` with no `entry_fill` (the legacy bare-string state form) is SKIPPED rather than
    guessed: inventing an entry price would fabricate a P&L that could trip a live-money breaker on
    a number nobody observed.
    """
    entry_fill = position.get("entry_fill")
    if entry_fill is None:
        log_event(
            logger,
            logging.WARNING,
            "streak.outcome_skipped_no_entry_context",
            product=product_id,
        )
        return

    # BOTH legs' fees, matching `SimAccount.close`. Counting only the exit leg would make live's
    # loss definition strictly looser than the sim's -- and rail 16's threshold is meant to come
    # from a sim sweep, so the breaker would fire later on real money than the sweep predicted.
    # A position with no `entry_fee` (legacy/degraded state) contributes 0 rather than skipping
    # the record: unlike a missing entry PRICE, which would fabricate the P&L's sign, a missing
    # fee only understates cost, and dropping the row would hide the trade from rail 16 entirely.
    entry_fee = position.get("entry_fee") or Decimal("0")

    # LEGS ALREADY SOLD are folded in here (#502), which is why this is the only place that
    # reads them: a scaled-out tranche reaches this function having already sold part of itself
    # at a different price on a different day, and the row it writes is the WHOLE trade.
    #
    # §2 of the trade-outcomes design settles the shape and it is worth restating, because the
    # alternative is superficially tidier: "a half-off-at-target that later stops out at
    # breakeven is ONE trade, not two. Its P&L is the sum across all partial exits", with the
    # consequence accepted explicitly -- "a trade's outcome is unknown until the last unit
    # closes, so a half-closed position contributes nothing to the streak yet."
    #
    # Booking each leg as its own row instead would put a WIN row and then a fee-sized LOSS row
    # on every profitable scale-out, because the runner half of a de-risked trade ends at or
    # near break-even by construction. Rail 16 counts consecutive losses, so the strategy that
    # works best under scaling out is the one that would trip the breaker fastest. That is the
    # inversion #502 names -- "rail 16 counts a scaled-out net winner as a loss" -- and summing
    # is what prevents it.
    prior_qty = position.get("realized_qty") or Decimal("0")
    prior_proceeds = position.get("realized_proceeds") or Decimal("0")
    prior_fees = position.get("realized_fees") or Decimal("0")

    total_qty = exit_qty + prior_qty
    total_fees = fees + prior_fees
    proceeds = exit_fill * exit_qty + prior_proceeds
    # The entry fee is the WHOLE tranche's and is charged exactly ONCE, on this closing row --
    # not apportioned across legs, because only one row is ever written. `entry_fill` is
    # likewise the tranche's, unchanged: a partial sale does not move the price it was bought at.
    pnl_net = proceeds - entry_fill * total_qty - total_fees - entry_fee

    # Byte-for-byte the old value when nothing was sold earlier. The quantity-weighted average
    # is computed ONLY when there are prior legs to average with: `proceeds / total_qty` is
    # algebraically `exit_fill` in the single-leg case but not always DECIMALLY so, and every
    # existing outcome row asserts the venue's fill price exactly.
    recorded_exit_fill = exit_fill if prior_qty <= 0 else proceeds / total_qty

    repo.insert_trade_outcome(
        {
            "product_id": product_id,
            "rule_name": position.get("rule_name"),
            "is_dca": is_dca,
            "opened_at": position.get("opened_at") or now_ts,
            "closed_at": now_ts,
            "qty": total_qty,
            "entry_fill": entry_fill,
            "exit_fill": recorded_exit_fill,
            "fees": total_fees,
            "pnl_net": pnl_net,
        }
    )

    # DCA is exempt from the STREAK, not from the RECORD: its P&L is real and rail 11 needs it,
    # but DCA is designed to buy through drawdowns on a fixed budget (§12.6).
    if is_dca:
        return

    if pnl_net >= 0:
        repo.set_state("consecutive_losses", 0)
        return

    losses = int(repo.get_state("consecutive_losses", default=0)) + 1
    repo.set_state("consecutive_losses", losses)

    threshold = config.money_mgmt.max_consecutive_losses
    if threshold > 0 and losses >= threshold:
        halt_until = now_ts + config.money_mgmt.streak_cooloff_days * SECONDS_PER_DAY
        repo.set_state("streak_halt_until", halt_until)
        log_event(
            logger,
            logging.WARNING,
            "streak.breaker_tripped",
            product=product_id,
            consecutive_losses=losses,
            threshold=threshold,
            halt_until=halt_until,
        )


#: The smallest fee slice recorded. Matches the quantization `_close_tranches` has apportioned
#: with since the tranche ledger existed, and is deliberately finer than any venue's fee
#: precision: the parts must sum to the whole, and the LAST leg absorbs whatever rounding is
#: left rather than letting the shares fall short. Understating total cost would flatter
#: `pnl_net`, whose SIGN is the only thing rail 16 reads.
_FEE_QUANTUM = Decimal("0.00000001")


def observed_sold_qty(exit_order: dict[str, Any]) -> Decimal | None:
    """How much of `exit_order` the venue actually sold, or `None` for "all of it" (#446).

    `orders.qty` is the ORDERED size and `orders.filled_quantity` is what the venue reported
    executing (#446 added the column; `_record_observed_fill_quantity` writes it on both sides).
    A market IOC that fills short has its remainder cancelled, so a short `filled_quantity` is
    FINAL, not a snapshot still settling.

    `None` is returned for the two cases that must keep behaving exactly as they did:

    * **Nothing observed** (`filled_quantity` NULL -- the venue's status endpoint was
      unavailable, or the row predates v11). Guessing a partial from an absent observation
      would leave tranches open on the strength of a number nobody recorded.
    * **A full fill.** `book_exit` then closes EVERY open tranche of the product, which is not
      the same as consuming `qty` FIFO: `_build_intent` sizes an exit from the filled-order
      history while the tranches come from the `positions` ledger, and the two legitimately
      disagree for holdings that predate the ledger. Consuming FIFO would strand those tranches
      open forever; closing them all is what has always happened and stays.

    So the ONLY case that changes is the one #446 named: an exit the venue partly filled, which
    until now booked `exit_qty=position.qty` for every tranche and over-booked the sale.
    """
    filled = exit_order.get("filled_quantity")
    ordered = exit_order.get("qty")
    if filled is None or filled <= 0:
        return None
    if ordered is None or filled >= ordered:
        return None
    return Decimal(filled)


def book_exit(
    repo: Repository,
    config: Config,
    *,
    product_id: str,
    exit_order: dict[str, Any],
    sold_qty: Decimal | None,
    is_dca: bool,
    now_ts: int,
) -> None:
    """Attribute a sale of `sold_qty` across `product_id`'s open tranches, OLDEST FIRST.

    `sold_qty=None` means "the whole held position": every open tranche closes, which is what
    a rule exit has always done and what `agent._close_tranches` was. A number means a PARTIAL
    sale -- `scale_out`'s deliberate one, or #446's short market exit -- and the tranches are
    consumed FIFO until it is exhausted. The tranche the sale stops inside is REDUCED, not
    closed: its remainder keeps running with a bracket, and the legs it has sold are carried on
    the row until it finally closes (see `record_closed_trade`).

    FIFO is not an arbitrary tie-break. It is the order `get_open_positions` documents as part
    of its contract, it is the order the ledger exists to make possible (booking an aggregate
    against one blob of entry context computes the older tranche's P&L against the newer one's
    entry price), and it is the convention the whole codebase already reads the ledger with.

    **The exit order carries ONE fee for the whole sale**, so it is apportioned pro-rata across
    the legs by the quantity each leg CONTRIBUTED -- not by tranche size, which differs the
    moment a sale stops part-way inside a tranche. The last leg takes the rounding remainder.

    A product with no ledger rows records nothing rather than guessing an entry price -- the
    same refusal `record_closed_trade` makes for a tranche with no `entry_fill`.
    """
    positions = repo.get_open_positions(product_id)
    if not positions:
        log_event(
            logger,
            logging.WARNING,
            "agent.exit_without_ledger_tranche",
            product=product_id,
            order_id=exit_order["id"],
        )
        return

    exit_fill = exit_order["actual_fill"]
    total_fee = exit_order["fee"] or Decimal("0")

    # The legs this sale actually consumes, and how much of each. `sold_qty=None` takes every
    # tranche whole; a number walks FIFO and stops when it is spent. A `sold_qty` larger than
    # the ledger holds is CLAMPED by the loop rather than rejected: over-booking a tranche that
    # does not exist is the failure mode, and the venue's fill is the authority on what was sold.
    legs: list[tuple[dict[str, Any], Decimal]] = []
    remaining = sold_qty
    for position in positions:
        if remaining is None:
            legs.append((position, position["qty"]))
            continue
        if remaining <= 0:
            break
        legs.append((position, min(position["qty"], remaining)))
        remaining -= legs[-1][1]

    sold_total = sum((leg for _, leg in legs), Decimal("0"))
    apportioned = Decimal("0")

    for index, (position, leg_qty) in enumerate(legs):
        is_last = index == len(legs) - 1
        if is_last or sold_total <= 0:
            fee_share = total_fee - apportioned
        else:
            fee_share = (total_fee * leg_qty / sold_total).quantize(_FEE_QUANTUM)
            apportioned += fee_share

        if leg_qty >= position["qty"]:
            record_closed_trade(
                repo,
                config,
                product_id=product_id,
                position=position,
                exit_fill=exit_fill,
                exit_qty=leg_qty,
                fees=fee_share,
                is_dca=is_dca,
                now_ts=now_ts,
            )
            repo.close_position(position["id"], closed_at=now_ts)
            continue

        # The tranche the sale stopped INSIDE. No outcome row yet -- the trade is not over --
        # so the leg is carried and the tranche shrinks to what is still held.
        repo.reduce_position(
            position["id"],
            remaining_qty=position["qty"] - leg_qty,
            realized_qty=position["realized_qty"] + leg_qty,
            realized_proceeds=position["realized_proceeds"] + exit_fill * leg_qty,
            realized_fees=position["realized_fees"] + fee_share,
        )
        log_event(
            logger,
            logging.INFO,
            "streak.tranche_partially_exited",
            product=product_id,
            position_id=position["id"],
            sold=str(leg_qty),
            remaining=str(position["qty"] - leg_qty),
            order_id=exit_order["id"],
        )
