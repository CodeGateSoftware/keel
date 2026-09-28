"""The DCA sleeve's ledger-side logic (docs/superpowers/specs/2026-09-28-dca-sleeve-sell-side-
design.md §3.3).

THE `positions` LEDGER IS THE TRUTH FOR WHAT IS HELD, LOT BY LOT. `executor._held_position` derives
a quantity and an average from `orders`, and the rails size from that. The two legitimately
disagree only by dust. Beyond one base increment they are telling different stories: #799's
filled entry with no tranche (the orders say more), or #798's out-of-band sale (both say more than
the venue). Each is a doctor finding (`ledger.drift`, `ledger.venue_drift`), never an assumption
a sale is sized from.

Read-only. Nothing here writes, and nothing here holds a broker: these are the quantities doctor
compares, and doctor is pinned read-only and broker-free (`gather_findings`).
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from keel.data.repository import Repository
from keel.types import Side


def ledger_qty(positions: Iterable[dict[str, Any]]) -> Decimal:
    """Sum of `qty` over the given OPEN tranches -- what is still held. `Holding.qty` (P5) is
    defined as exactly this, and a test pins the two equal.

    `qty` on an open tranche is what REMAINS after any scale-out (the sold part moves to
    `realized_qty`), so the sum is the held quantity, not the quantity ever bought."""
    return sum((Decimal(p["qty"]) for p in positions), Decimal("0"))


def orders_qty(repo: Repository, product_id: str, mode: str) -> Decimal:
    """Net filled BUY - SELL qty for `product_id`, from `orders` tagged `mode` (#881).

    Mirrors `executor._held_position`'s arithmetic, but is NOT `_held_position`: that function
    hardcodes `mode="live"` deliberately (`agent._book_paper_exit`'s docstring -- the live exit
    path is unreachable from paper BY CONSTRUCTION, not by accident). `ledger.drift` is a
    read-only doctor comparison, not a placement path, and it must compare the ledger against
    the SAME mode's orders: on a paper profile every fill is `mode="paper"`, and comparing it
    against `_held_position`'s always-empty `mode="live"` total would WARN on every open paper
    tranche as though it were #799's stranded fill.

    Floored at zero, as `_held_position` is: a net short is not a holding keel models.

    Each row counts what the venue DELIVERED (`filled_quantity`) when it said, its ordered `qty`
    otherwise (#900) -- the same reading as `_held_position` and R33
    (`guards._open_exposure_by_asset`). A tranche is booked at that same delivered figure
    (`agent._open_tranche`), and `keel positions close` writes `qty == filled_quantity` from the
    tranche, so a product whose every tranche is closed nets to exactly zero here, and R-f
    (`declared_close_target`) compares a tranche against the figure it was booked from. Reading
    `qty` alone would leave the fee's worth of a quote-sized BUY as a phantom holding.
    """
    buy_qty = Decimal("0")
    sell_qty = Decimal("0")
    for order in repo.get_orders(mode=mode, product_id=product_id, status="filled"):
        qty = order.get("filled_quantity") or order["qty"] or Decimal("0")
        if order["side"] == Side.BUY.value:
            buy_qty += qty
        elif order["side"] == Side.SELL.value:
            sell_qty += qty
    net = buy_qty - sell_qty
    return net if net > 0 else Decimal("0")
