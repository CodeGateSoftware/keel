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

    pnl_net = (exit_fill - entry_fill) * exit_qty - fees

    repo.insert_trade_outcome(
        {
            "product_id": product_id,
            "rule_name": position.get("rule_name"),
            "is_dca": is_dca,
            "opened_at": position.get("opened_at") or now_ts,
            "closed_at": now_ts,
            "qty": exit_qty,
            "entry_fill": entry_fill,
            "exit_fill": exit_fill,
            "fees": fees,
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
