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
from keel.commands.status import shown_stop
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
    #: `positions.rule_id`, the `rules` ROW that opened the tranche (#803), or `None` for a
    #: tranche that predates v21 or whose entry order carried no id -- unknown, never "no rule".
    #: Defaulted so a `HoldingRow(...)` written before #960 stays valid.
    rule_id: int | None = None


@dataclass(frozen=True)
class HoldingGroup:
    """One Holdings table: the tranches of one rule ROW, or the unattributed ones of one kind.

    `key` is built from an integer -- `rule-<rules.id>` -- or, for unattributed tranches, from an
    ordinal (`unattributed-<n>`), and never from a stored name: the view makes element ids from
    it (`h-hold-<key>`), and a rule name with a space split the `aria-labelledby` it fed (#960).
    `heading` is the words the view prints, made here so no renderer composes them.
    """

    key: str
    heading: str


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
    def groups(self) -> tuple[HoldingGroup, ...]:
        """One group per rule ROW (`positions.rule_id`, #960), in first-seen (oldest-first)
        order, then the unattributed tranches -- one group per kind, so a pre-v21 DCA leg and the
        PAXG turtle tranche are not drawn as one table -- last. What a grouped view keys on, so
        no renderer builds its own list.

        Per row, not per kind: every DCA rule carries the kind `dca` (`Dca.name`'s default), so
        grouping by name drew the live book's seven DCA rules as ONE table across products.
        Kind and product do not identify a row either (two BTC DCA rules differ only by id and
        params), so the heading names the id -- the handle the `keel rules` commands take.
        """
        attributed: dict[int, HoldingGroup] = {}
        for row in self.rows:
            if row.rule_id is not None and row.rule_id not in attributed:
                kind = row.position.rule_name or "rule"
                attributed[row.rule_id] = HoldingGroup(
                    key=f"rule-{row.rule_id}",
                    heading=f"{kind} · {row.position.product_id} · rule {row.rule_id}",
                )
        unattributed = tuple(
            HoldingGroup(
                key=f"unattributed-{n}",
                heading=f"{kind} · unattributed" if kind else "unattributed",
            )
            for n, kind in enumerate(self._unattributed_kinds(), start=1)
        )
        return (*attributed.values(), *unattributed)

    def group_key(self, row: HoldingRow) -> str:
        """The `HoldingGroup.key` `row` is drawn under."""
        if row.rule_id is not None:
            return f"rule-{row.rule_id}"
        return f"unattributed-{self._unattributed_kinds().index(row.position.rule_name) + 1}"

    def _unattributed_kinds(self) -> list[str]:
        """The kinds of the tranches with no `rule_id`, first-seen -- the ONE ordering both
        `groups` and `group_key` number the unattributed groups by."""
        kinds: list[str] = []
        for row in self.rows:
            if row.rule_id is None and row.position.rule_name not in kinds:
                kinds.append(row.position.rule_name)
        return kinds

    @property
    def stop_row_ids(self) -> tuple[int, ...]:
        """Tranches that carry a stop, so the view can draw the stop column only when one does.
        A live DCA tranche's recorded `0` is `Dca.detect`'s sentinel, not a stop; paper's is
        listed as it always was (`shown_stop`)."""
        return tuple(
            r.position.id
            for r in self.rows
            if shown_stop(r.position.initial_stop, self.mode) is not None
        )


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
                rule_id=None if raw.get("rule_id") is None else int(raw["rule_id"]),
            )
        )
    return HoldingsReport(now_ts=now_ts, mode=str(config.auto_trade.mode), rows=tuple(rows))
