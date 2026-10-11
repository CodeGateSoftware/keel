"""The console's Holdings view, as a service -- open tranches with what they cost and how they
were entered.

A VIEW over `gather_positions` and the orders book. The one thing it adds is the join the ledger
does not store: a tranche's ENTRY ORDER (`positions` carries no order id; no backfill exists
yet). Nothing here reaches a venue; `keel/web/__init__.py`'s posture is unchanged.

**The join is the fill, not the clock.** `agent._open_tranche` opens a tranche with
`opened_at=now_ts` (the cycle's clock) and copies two things verbatim from the entry order:
`entry_fill = order["actual_fill"]` and `rule_id = order.get("rule_id")`. The timestamps do NOT
reliably agree: live shares the cycle's `now_ts` (`executor._order_row`), but `PaperTrader` writes
the paper order with `created_at=signal.ts` -- the BAR's ts, possibly days before the cycle that
acted on it. So a candidate for a tranche is a BUY on the same product, created no later than the
tranche opened, whose `actual_fill` equals the tranche's `entry_fill` EXACTLY (`Decimal`
equality), and whose `rule_id` does not contradict the tranche's (a `None` on either side is
"unknown", not a mismatch -- pre-v21 tranches carry none).

**Closest before, claimed once.** Several tranches of one product can share a fill (a DCA buying
on consecutive days); the house reasoning is `sleeve.unverified_fill_orders`' -- the candidate
nearest BEFORE the lot's `opened_at` (ties: the higher order id) owns it, and each order is
claimed by at most one tranche, tranches walked oldest-first. "Oldest order first" would hand
yesterday's order to today's tranche.

**Unknown, never wrong.** A fill later rewritten by reconciliation of a partial fill no longer
equals what the tranche copied, so the tranche reads "entry order not identified". That is the
honest outcome: this module names an order only when the tranche's own copied figures vouch for
it, and says so when none does. The match is a strong inference, not a stored foreign key.

**Pure, no clock** (the split `pulse.py` documents): `now_ts` is the caller's one read. The orders
book is read ONCE PER PRODUCT, not per tranche, and rows are built with `orders._row_from_dict`
WITHOUT a `repo`, so no cancel classification ever runs for a join.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from keel.commands.orders import OrderRow
from keel.commands.orders import _row_from_dict as _order_row_from_dict
from keel.commands.positions import PositionRow, gather_positions
from keel.config import Config
from keel.data.repository import Repository
from keel.types import Side

#: Said when no order vouches for a tranche. Worded for what was checked, not for a cause: the
#: order may be missing, or its fill may have been rewritten after the tranche copied it.
ENTRY_ORDER_NOT_IDENTIFIED = (
    "entry order not identified -- no buy order on this product matches this tranche's fill "
    "price before it opened"
)


@dataclass(frozen=True)
class HoldingRow:
    position: PositionRow
    #: `positions.bracket_order_id is not None`.
    has_bracket: bool
    #: What the tranche paid to OPEN: `(qty + realized_qty) * entry_fill + entry_fee`. `qty` is
    #: what is still held after scale-outs while `entry_fee` is the whole entry's fee, so
    #: `qty * fill + fee` would mix the two once a partial exit has happened.
    cost: Decimal
    #: The matched entry order, or `None`. Its `cancel` is the inert default, never classified.
    entry_order: OrderRow | None
    #: `""` when matched, else `ENTRY_ORDER_NOT_IDENTIFIED`.
    entry_order_note: str


@dataclass(frozen=True)
class HoldingsReport:
    now_ts: int
    #: The deployment's `auto_trade.mode`, which the bracket judgement needs.
    mode: str
    #: Oldest tranche first.
    rows: tuple[HoldingRow, ...]

    @property
    def open_count(self) -> int:
        """Derived, not stored: `payload.py` may not call `len()` (Rule 6e)."""
        return len(self.rows)

    @property
    def rules(self) -> tuple[str, ...]:
        """Distinct rule names in first-seen (oldest-first) order, the unattributed `""` last --
        what a grouped view keys on, so no renderer builds its own list."""
        seen: list[str] = []
        for row in self.rows:
            if row.position.rule_name not in seen:
                seen.append(row.position.rule_name)
        return tuple(sorted(seen, key=lambda name: name == ""))

    @property
    def stop_row_ids(self) -> tuple[int, ...]:
        """Tranches that carry a stop, so the view can draw the stop column only when one does."""
        return tuple(r.position.id for r in self.rows if r.position.initial_stop is not None)


def _rule_consistent(tranche_rule_id: Any, order: dict[str, Any]) -> bool:
    order_rule_id = order.get("rule_id")
    if tranche_rule_id is None or order_rule_id is None:
        return True
    return int(tranche_rule_id) == int(order_rule_id)


def _entry_order(
    raw: dict[str, Any], candidates: list[dict[str, Any]], claimed: set[int]
) -> dict[str, Any] | None:
    opened_at = int(raw["opened_at"])
    matches = [
        o
        for o in candidates
        if int(o["id"]) not in claimed
        and o["side"] == Side.BUY.value
        and o.get("created_at") is not None
        and int(o["created_at"]) <= opened_at
        and o.get("actual_fill") is not None
        and o["actual_fill"] == raw["entry_fill"]
        and _rule_consistent(raw.get("rule_id"), o)
    ]
    if not matches:
        return None
    return max(matches, key=lambda o: (int(o["created_at"]), int(o["id"])))


def gather_holdings(repo: Repository, config: Config, *, now_ts: int) -> HoldingsReport:
    """Every open tranche with its cost and, where one vouches for it, its entry order."""
    positions = gather_positions(repo, config, now_ts=now_ts)
    raw_by_id = {int(r["id"]): r for r in repo.get_open_positions()}
    rule_names = {int(r["id"]): str(r["kind"]) for r in repo.get_rules(None)}
    book: dict[str, list[dict[str, Any]]] = {}
    claimed: set[int] = set()
    rows: list[HoldingRow] = []
    for position in positions.rows:
        raw = raw_by_id.get(position.id)
        if raw is None:
            # Closed between the two reads of the open book (the agent writes from its own
            # process). It is no longer held, so it is left out rather than shown with a bracket
            # and rule id nobody read.
            continue
        if position.product_id not in book:
            book[position.product_id] = repo.get_orders(product_id=position.product_id)
        match = _entry_order(raw, book[position.product_id], claimed)
        if match is not None:
            claimed.add(int(match["id"]))
        rows.append(
            HoldingRow(
                position=position,
                has_bracket=raw.get("bracket_order_id") is not None,
                cost=(position.qty + position.realized_qty) * position.entry_fill
                + position.entry_fee,
                entry_order=None if match is None else _order_row_from_dict(match, rule_names),
                entry_order_note="" if match is not None else ENTRY_ORDER_NOT_IDENTIFIED,
            )
        )
    return HoldingsReport(now_ts=now_ts, mode=str(config.auto_trade.mode), rows=tuple(rows))
