"""What the account holds, as the last cycle recorded it -- issue #702.

**NO BROKER CALL, AND THAT IS THE DESIGN.** A balances page is the obvious place to reach for a
live venue read, and this module deliberately does not. `keel serve`'s defining property is that
it is a loopback reader over SQLite with no credentials, no broker handle and no outbound
network: putting a venue read behind a page that re-polls every 15 seconds (`main.js`'s
`POLL_MS`) would hand an operator's rate limit to every browser tab left open, and would put
credentials into the one process a browser can reach. Every other read route already holds that
line (`gather_status`: "no broker, no network"; `list_installed_brokers`: "no broker handle, no
network, no config, no credentials"), and a balances view is not the place to break it.

WHAT IS SHOWN INSTEAD IS BETTER, NOT MERELY SAFER. Cash comes from `equity_points` (#698) --
the figure the agent read and SIZED AGAINST when it evaluated the rails that cycle -- stamped
with when it read it. A fresher number the engine never saw would explain nothing about why it
did what it did.

WHAT IS NOT RECORDED IS SAID, NOT GUESSED. `equity_points.cash` comes from
`executor._fetch_available_quote`, which reads `Balance.available` and stops there; the venue's
settled-versus-total pair is never written down. This report carries that absence explicitly
rather than presenting the available figure under a label implying the distinction was checked
(see `settled_breakdown_recorded`). When a cycle records the pair, this becomes a read.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from keel.commands.positions import PositionRow, gather_positions
from keel.config import Config
from keel.data.repository import Repository


@dataclass(frozen=True)
class AssetBalanceRow:
    """One PRODUCT's holding, summed across its tranches.

    Per asset, because that is the question a balances page answers. The tranche breakdown is
    the Positions view's, and both read `gather_positions` so the two cannot disagree about what
    is held.
    """

    product_id: str

    #: Quantity still held, summed over every open tranche of this product.
    qty: Decimal

    #: The mark those tranches were valued at, and when it was read. `None` when the product has
    #: no cached candle -- the same absence `PositionRow.mark` carries, for the same reason.
    mark: Decimal | None
    mark_as_of: int | None

    #: `qty * mark`, or `None` if ANY tranche of this product lacks a mark.
    #:
    #: A partial sum is the most dangerous shape available here: it looks like a total and is
    #: not one, so a holding half of which could not be priced would render as a smaller holding
    #: rather than an unknown one. Unknown is the only reading that cannot be misread.
    market_value: Decimal | None


@dataclass(frozen=True)
class BalancesReport:
    now_ts: int

    #: `paper`, `live`, or `""` before the first cycle stamps one. The partition every figure
    #: below is read through: `equity_points` holds both modes in one database.
    mode: str

    #: The newest recorded reading FOR THAT MODE, and the instant it was recorded. `cash` is
    #: `None` when nothing has been recorded, and also when the recorded cycle knew its total
    #: but not its split -- both are absences, never zero.
    cash: Decimal | None
    cash_as_of: int | None
    equity: Decimal | None
    unrealized: Decimal | None
    hwm: Decimal | None

    #: Whether ANY reading exists for this mode. Distinct from `cash is None`: a deployment that
    #: has never completed a cycle and one whose last cycle could not read a split are different
    #: facts, and only the first is "this page has nothing to show yet".
    has_recorded_cash: bool

    #: The synthetic account's CURRENT cash, in paper mode only (`agent_state`'s
    #: `paper_cash_usdc`). It moves on every paper fill, so it answers "what does the paper
    #: account hold now" beside `cash`'s "what did the cycle act on". `None` in live mode even
    #: though the key survives a paper->live flip: a synthetic balance beside real money would be
    #: the most confusing thing this page could show.
    paper_cash: Decimal | None

    #: The venue's settled/total split. Both `None` and `settled_breakdown_recorded` False,
    #: always, today -- see the module docstring. They are fields rather than an omission so the
    #: page can SAY the distinction is unrecorded, and so that recording it later is a change to
    #: the producer rather than to this shape.
    settled_cash: Decimal | None
    total_cash: Decimal | None
    settled_breakdown_recorded: bool

    assets: tuple[AssetBalanceRow, ...]

    @property
    def asset_count(self) -> int:
        """How many products this report holds. Derived, and held here rather than measured by a
        renderer: Rule 6e bans `len()` in `keel/web/payload.py`."""
        return len(self.assets)


def gather_balances(repo: Repository, config: Config, *, now_ts: int) -> BalancesReport:
    """Everything the account holds, from what a cycle wrote down. No broker, no network.

    The mode is read FIRST and everything else is read through it. `equity_state_mode` is the
    same stamp `agent._clear_live_mode_if_needed` maintains, and an unstamped one (before the
    first cycle) yields no cash at all rather than a guess about which account to show.
    """
    mode = str(repo.get_state("equity_state_mode") or "")

    reading = None
    if mode:
        recorded = repo.get_equity_points(mode=mode, limit=1)
        # `limit=1` keeps the MOST RECENT reading (`get_equity_points`' own contract), so this is
        # one row off an index rather than the whole series read to take its last element.
        reading = recorded[-1] if recorded else None

    positions = gather_positions(repo, config, now_ts=now_ts)
    return BalancesReport(
        now_ts=now_ts,
        mode=mode,
        cash=None if reading is None else reading.cash,
        cash_as_of=None if reading is None else reading.ts,
        equity=None if reading is None else reading.equity,
        unrealized=None if reading is None else reading.unrealized,
        hwm=None if reading is None else reading.hwm,
        has_recorded_cash=reading is not None,
        paper_cash=repo.get_state("paper_cash_usdc") if mode == "paper" else None,
        settled_cash=None,
        total_cash=None,
        settled_breakdown_recorded=False,
        assets=_assets_from(positions.rows, positions.products),
    )


def _assets_from(
    rows: Sequence[PositionRow], products: tuple[str, ...]
) -> tuple[AssetBalanceRow, ...]:
    """Fold the per-tranche rows into one row per product, in the report's own product order.

    `products` comes from `PositionsReport`, not from a set built here: two answers to "which
    products does this book hold" is one too many, and a set would reorder the page between
    reads for no reason a reader could see.
    """
    by_product: dict[str, list[PositionRow]] = {product: [] for product in products}
    for row in rows:
        by_product.setdefault(row.product_id, []).append(row)

    assets: list[AssetBalanceRow] = []
    for product in products:
        held = by_product.get(product) or []
        if not held:
            continue
        qty = sum((row.qty for row in held), Decimal("0"))
        marks = [row.mark for row in held]
        # ANY missing mark makes the VALUE unknown -- never a sum over the priced subset. The
        # quantity is still known and still shown: what is held is a fact, what it is worth is
        # the part nobody observed.
        if any(mark is None for mark in marks):
            value: Decimal | None = None
        else:
            value = sum((row.market_value or Decimal("0") for row in held), Decimal("0"))
        # Any tranche will do for the mark: `gather_positions` reads it ONCE PER PRODUCT and
        # hands the same figure to every tranche of it, so "the first" and "the newest" are the
        # same row here. Picking a maximum would imply they could differ.
        assets.append(
            AssetBalanceRow(
                product_id=product,
                qty=qty,
                mark=held[0].mark,
                mark_as_of=held[0].mark_ts,
                market_value=value,
            )
        )
    return tuple(assets)
