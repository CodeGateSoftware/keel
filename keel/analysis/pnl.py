"""FIFO cost-basis P&L: realized/unrealized P&L, open position sizing, daily
`pnl_daily` snapshot rows, and drawdown + recovery-table metrics (KB §4.6/§10.5).

Pure functions over the transaction-dict shape returned by
`Repository.get_transactions()` (see `keel/data/repository.py`): each dict
carries at least `type`, `asset`, `ts`, `qty`, `price`, and optionally `fees`.
`type` is free text from the source (CSV import or live fills) — classified
case-insensitively as a buy (opens/adds to a FIFO lot) or a sell/convert
(closes lots oldest-first, realizing P&L on the disposed asset). Unrecognized
types (deposits, rewards, sends, ...) have no defined cost basis and are
ignored here.

No network, no global state — data in, values out. All money is `Decimal`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

_BUY_KEYWORDS = ("buy",)
_SELL_KEYWORDS = ("sell", "convert")

_ZERO = Decimal("0")


@dataclass
class Position:
    """An open FIFO position: total remaining quantity and its weighted-average cost."""

    qty: Decimal
    avg_cost: Decimal


@dataclass
class _Lot:
    qty: Decimal
    price: Decimal


@dataclass
class _AssetBook:
    lots: list[_Lot] = field(default_factory=list)
    realized: Decimal = _ZERO


def _classify(tx_type: str | None) -> str | None:
    """Map a free-text transaction `type` to `"buy"`, `"sell"`, or `None` (ignored)."""
    if not tx_type:
        return None
    t = tx_type.strip().lower()
    if any(keyword in t for keyword in _BUY_KEYWORDS):
        return "buy"
    if any(keyword in t for keyword in _SELL_KEYWORDS):
        return "sell"
    return None


def _unit_price(tx: dict[str, Any]) -> Decimal:
    """Per-unit price for `tx`, falling back to subtotal/qty or total/qty."""
    price = tx.get("price")
    if price is not None:
        return price
    qty = tx.get("qty") or _ZERO
    if qty:
        subtotal = tx.get("subtotal")
        if subtotal is not None:
            return subtotal / qty
        total = tx.get("total")
        if total is not None:
            return total / qty
    return _ZERO


def _sorted_transactions(transactions: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(transactions, key=lambda tx: (tx.get("ts") or 0, tx.get("id") or 0))


def _build_books(transactions: Iterable[dict[str, Any]]) -> dict[str, _AssetBook]:
    """Replay `transactions` in ts order, building per-asset FIFO lot books."""
    books: dict[str, _AssetBook] = {}
    for tx in _sorted_transactions(transactions):
        action = _classify(tx.get("type"))
        if action is None:
            continue
        asset = tx.get("asset")
        if asset is None:
            continue
        qty = tx.get("qty")
        if not qty:
            continue

        book = books.setdefault(asset, _AssetBook())
        price = _unit_price(tx)
        fees = tx.get("fees") or _ZERO
        fee_per_unit = fees / qty

        if action == "buy":
            # Buy fees increase the effective cost basis of the new lot.
            book.lots.append(_Lot(qty=qty, price=price + fee_per_unit))
        else:
            # Sell fees reduce effective proceeds; close lots oldest-first.
            proceeds_price = price - fee_per_unit
            remaining = qty
            while remaining > 0 and book.lots:
                lot = book.lots[0]
                matched = min(lot.qty, remaining)
                book.realized += matched * (proceeds_price - lot.price)
                lot.qty -= matched
                remaining -= matched
                if lot.qty == 0:
                    book.lots.pop(0)
            # Selling more than is held (no matching lots) leaves no cost basis
            # for the excess; it is not counted as realized P&L.
    return books


def _open_qty_and_avg_cost(book: _AssetBook) -> tuple[Decimal, Decimal]:
    total_qty = sum((lot.qty for lot in book.lots), _ZERO)
    if not total_qty:
        return _ZERO, _ZERO
    total_cost = sum((lot.qty * lot.price for lot in book.lots), _ZERO)
    return total_qty, total_cost / total_qty


def realized_pnl(transactions: list[dict[str, Any]], asset: str | None = None) -> Decimal:
    """Total realized P&L from FIFO-matched sells/converts against buy lots.

    Sums across all assets when `asset` is `None`, else scopes to one asset.
    """
    books = _build_books(transactions)
    if asset is not None:
        book = books.get(asset)
        return book.realized if book else _ZERO
    return sum((book.realized for book in books.values()), _ZERO)


def position(transactions: list[dict[str, Any]], asset: str) -> Position:
    """The current open FIFO position (remaining qty + weighted-average cost) for `asset`."""
    books = _build_books(transactions)
    book = books.get(asset)
    if book is None:
        return Position(qty=_ZERO, avg_cost=_ZERO)
    qty, avg_cost = _open_qty_and_avg_cost(book)
    return Position(qty=qty, avg_cost=avg_cost)


def unrealized_pnl(
    transactions: list[dict[str, Any]], marks: dict[str, Decimal]
) -> dict[str, Decimal]:
    """Mark-to-market P&L on open lots, keyed by asset.

    Only assets with both an open (nonzero) position and a supplied mark are included.
    """
    books = _build_books(transactions)
    result: dict[str, Decimal] = {}
    for asset, book in books.items():
        qty, avg_cost = _open_qty_and_avg_cost(book)
        if not qty:
            continue
        mark = marks.get(asset)
        if mark is None:
            continue
        result[asset] = (mark - avg_cost) * qty
    return result


def daily_snapshot(
    transactions: list[dict[str, Any]], marks: dict[str, Decimal], date: str
) -> list[dict[str, Any]]:
    """Build one `pnl_daily` row per asset seen in `transactions` or `marks`.

    Row shape mirrors the `pnl_daily` table columns: date, asset, qty, avg_cost,
    price, realized, unrealized.
    """
    books = _build_books(transactions)
    assets = sorted(set(books) | set(marks))
    rows: list[dict[str, Any]] = []
    for asset in assets:
        book = books.get(asset, _AssetBook())
        qty, avg_cost = _open_qty_and_avg_cost(book)
        price = marks.get(asset, _ZERO)
        unrealized = (price - avg_cost) * qty if qty else _ZERO
        rows.append(
            {
                "date": date,
                "asset": asset,
                "qty": qty,
                "avg_cost": avg_cost,
                "price": price,
                "realized": book.realized,
                "unrealized": unrealized,
            }
        )
    return rows


def max_drawdown(equity_curve: list[Decimal]) -> tuple[Decimal, int]:
    """Max peak-to-trough drawdown depth (fraction, e.g. `0.5` = -50%) and the
    time-in-drawdown duration (in bars) of that episode: from the peak to the
    point the curve first recovers to that peak's level, or to the end of the
    curve if it never recovers.
    """
    if not equity_curve:
        return _ZERO, 0

    peak = equity_curve[0]
    peak_idx = 0
    max_depth = _ZERO
    max_duration = 0

    for i, value in enumerate(equity_curve):
        if value > peak:
            peak = value
            peak_idx = i
            continue
        if not peak:
            continue
        depth = (peak - value) / peak
        if depth <= max_depth:
            continue
        max_depth = depth
        recovery_idx = len(equity_curve) - 1
        for j in range(i + 1, len(equity_curve)):
            if equity_curve[j] >= peak:
                recovery_idx = j
                break
        max_duration = recovery_idx - peak_idx

    return max_depth, max_duration


def recovery_pct(dd_pct: Decimal) -> Decimal:
    """The gain required to recover from a drawdown of `dd_pct` (fraction): `dd/(1-dd)`."""
    if not dd_pct:
        return _ZERO
    return dd_pct / (Decimal("1") - dd_pct)
