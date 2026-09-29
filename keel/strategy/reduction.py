"""The sell side's value types: `Lot`, `Holding`, `SellCosts`, `Reduction` (#857).

Spec: `docs/superpowers/specs/2026-09-28-dca-sleeve-sell-side-design.md` §3.2 (the `Reduction`
a sleeve-sell rule emits through `Rule.reduce_signal`) and §3.3 (the `Holding` it is computed
over). Plan: `docs/superpowers/plans/2026-09-28-dca-sleeve-sell-side-build.md` R6-R8.

**Pure, and deliberately outside both `rules/base.py` and `keel.data` (R7).** A rule, the
backtester and the account sim must all build a `Holding` without a repository, so the type
cannot live beside the repo adapter (`execution.sleeve.holding_of`). And `base.py` is the
contract every rule imports; it imports these types for `reduce_signal`'s signature, and
nothing here imports `keel.data`, so the contract module stays free of the storage layer. The
sim constructs `Holding(product_id, lots)` from its own lots, so live and sim size a sale from
ONE definition of cost basis.

**A `Holding` is the product's, not a rule's (R8, spec §3.3).** `from_rows` takes every open
tranche it is given and filters nothing by `rule_name`: on a mixed-ownership product (PAXG,
where turtle tranche 3 is the oldest row) a sleeve-sell rule governs the whole holding, and a
FIFO sale reaches the turtle tranche first. That leg is booked honestly as a rule outcome by
`streak.book_exit(is_dca=None)` (R9, spec Q10).

**Order is the ledger's.** `lots` keep the order they were given in. `get_open_positions` is
FIFO (`ORDER BY opened_at, id`) by contract, and `book_exit` consumes in that same order, so a
preview built from `fifo_legs` names the tranches the booking will actually hit. Re-sorting here
would be a second, possibly different, definition of "oldest".

**Entry fees are in the basis (spec Q4).** Break-even means both legs' fees, so `cost_basis`
and `vwae` include each lot's entry fee, prorated by what is still held:
`entry_fee * qty / (qty + realized_qty)` -- `qty` is what REMAINS after a #502 scale-out and
`realized_qty` is what was already sold, so their sum is the tranche's original size
(`positions` has no `original_qty` column).

**No `weight` field.** A sleeve weight is `value / sleeve total`, and one product's `Holding`
cannot know the sleeve total. The bands view (P13) computes it over several holdings.

**The ledger can overstate the venue by about a fee (#900).** A `Holding` is exactly as good as
the tranches it is built from. Since #905 a tranche is booked at the venue-filled size, but the
tranches booked before it carry the ORDERED size until a backfill runs, so for those the lot's
`qty` can exceed the base the venue delivered by roughly the fee. Nothing here clamps to the
venue: the executor clamps every SELL to the venue's holding (`_clamped_sell_qty`), and
`ledger.venue_drift` reports the gap before a sale is proposed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

_ZERO = Decimal("0")


@dataclass(frozen=True)
class Lot:
    """One open `positions` tranche, as the sell side reads it.

    `qty` is what is STILL held; `realized_qty` is what a #502 scale-out already sold from it
    (read as 0 when the row has none), so `qty + realized_qty` is the tranche's original size.
    """

    position_id: int
    rule_name: str
    opened_at: int
    qty: Decimal
    entry_fill: Decimal
    entry_fee: Decimal
    realized_qty: Decimal = _ZERO

    @property
    def entry_fee_share(self) -> Decimal:
        """The part of the entry fee that belongs to the units still held (spec §3.3)."""
        original = self.qty + self.realized_qty
        return _ZERO if original <= 0 else self.entry_fee * self.qty / original

    @property
    def cost(self) -> Decimal:
        """What the units still held cost, entry fee included (spec Q4)."""
        return self.qty * self.entry_fill + self.entry_fee_share


@dataclass(frozen=True)
class Holding:
    """Every open tranche of one product, oldest first, and an optional mark (spec §3.3).

    `qty` is defined as `execution.sleeve.ledger_qty` over the same rows, and a test pins the
    two equal: the quantity a sale is proposed from is the quantity doctor's `ledger.drift`
    compares. Like that figure, it can overstate the venue's holding by about a fee for a
    tranche booked before #905 (see the module docstring, #900).
    """

    product_id: str
    lots: tuple[Lot, ...]
    mark: Decimal | None = None

    @property
    def qty(self) -> Decimal:
        return sum((lot.qty for lot in self.lots), _ZERO)

    @property
    def cost_basis(self) -> Decimal:
        return sum((lot.cost for lot in self.lots), _ZERO)

    @property
    def vwae(self) -> Decimal | None:
        """Volume-weighted average entry, fees included; `None` -- not zero -- when nothing is
        held, because an average of nothing is undefined, and a zero would read as free."""
        qty = self.qty
        return None if qty <= 0 else self.cost_basis / qty

    @property
    def unrealised(self) -> Decimal | None:
        """Marked value less the fee-inclusive basis; `None` when there is no mark."""
        return None if self.mark is None else self.qty * self.mark - self.cost_basis

    def fifo_legs(self, qty: Decimal) -> tuple[tuple[Lot, Decimal], ...]:
        """The `(lot, units)` a sale of `qty` consumes, oldest first -- `book_exit`'s walk.

        Clamped to what is held, as `book_exit` clamps: a sale larger than the ledger reaches no
        tranche that does not exist."""
        legs: list[tuple[Lot, Decimal]] = []
        remaining = qty
        for lot in self.lots:
            if remaining <= 0:
                break
            take = min(lot.qty, remaining)
            legs.append((lot, take))
            remaining -= take
        return tuple(legs)

    def fifo_cost(self, qty: Decimal) -> Decimal:
        """The fee-inclusive basis of the units a sale of `qty` consumes, each lot prorated."""
        return sum(
            (
                take * lot.entry_fill + lot.entry_fee_share * take / lot.qty
                for lot, take in self.fifo_legs(qty)
                if lot.qty > 0
            ),
            _ZERO,
        )

    @classmethod
    def from_rows(
        cls,
        product_id: str,
        rows: Iterable[Mapping[str, Any]],
        mark: Decimal | None = None,
    ) -> Holding:
        """A `Holding` over `positions` rows, IN THE ORDER GIVEN (see the module docstring).

        A NULL `entry_fee` or `realized_qty` reads as zero, as the repository and
        `record_closed_trade` already read them.
        """
        return cls(
            product_id,
            tuple(
                Lot(
                    position_id=int(r["id"]),
                    rule_name=str(r["rule_name"]),
                    opened_at=int(r["opened_at"]),
                    qty=Decimal(r["qty"]),
                    entry_fill=Decimal(r["entry_fill"]),
                    entry_fee=Decimal(r.get("entry_fee") or 0),
                    realized_qty=Decimal(r.get("realized_qty") or 0),
                )
                for r in rows
            ),
            mark,
        )


@dataclass(frozen=True)
class SellCosts:
    """The cost rates a rule sizes a sale against, resolved by its caller (plan R6).

    A rule is built from params alone and cannot read `config` or the repo, so the caller hands
    it the fallback fee rate (`config.fees.taker_pct`) and the product's slippage. `fee_source`
    says which figure it is (`"fallback:config.fees.taker_pct"`). The venue's previewed fee, when
    there is one, is recorded by `executor.reduce` on the proposal; it is not decided here.

    `fee_pct + slippage_pct` must be in `[0, 1)`: sizing gross from net divides by
    `1 - fee - slippage` (spec §6), which is undefined at 1 and negative beyond it.
    """

    fee_pct: Decimal
    slippage_pct: Decimal
    fee_source: str

    def __post_init__(self) -> None:
        if self.fee_pct < 0 or self.slippage_pct < 0 or self.fee_pct + self.slippage_pct >= 1:
            raise ValueError(
                f"SellCosts: fee {self.fee_pct} + slippage {self.slippage_pct} must be in [0, 1)"
            )


@dataclass(frozen=True)
class Reduction:
    """A sleeve-sell rule's proposal to sell part of a holding (spec §3.2).

    It never names a buy: a redeploy leg, where a kind has one, is a separate `ENTER` on a later
    cycle. `qty` is an upper bound in base units; the pipeline floors and slices it (R11).
    `reason` is the rule kind, the vocabulary rail 10 reads. `expected_price` is the completed
    bar's close the rule decided on. A `Reduction` is never turned into a `Signal`: its audit row
    is `sell_proposals` (P6).
    """

    product_id: str
    qty: Decimal
    reason: str
    trigger: dict[str, Any]
    expected_price: Decimal
    ts: int

    def __post_init__(self) -> None:
        # Finiteness FIRST, and not only for the message: `Decimal("NaN") <= 0` raises
        # `InvalidOperation` rather than answering, so the positivity checks below cannot be the
        # ones to refuse it, and `Infinity` passes them outright -- an infinite `qty` would reach
        # every FIFO lot and an infinite price would size an infinite gross. Every refusal here
        # is a `ValueError`, so a caller catches one type.
        for name, value in (("qty", self.qty), ("expected_price", self.expected_price)):
            if not value.is_finite():
                raise ValueError(f"Reduction.{name} must be a finite number, got {value}")
        if self.qty <= 0:
            raise ValueError(f"Reduction.qty must be positive, got {self.qty}")
        if self.expected_price <= 0:
            raise ValueError(
                f"Reduction.expected_price must be positive, got {self.expected_price}"
            )
        if not self.reason:
            raise ValueError("Reduction.reason must name the rule kind (rail 10)")
