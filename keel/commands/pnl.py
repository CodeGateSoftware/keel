"""The account P&L report service behind `keel pnl` (FIFO, from imported transactions).

Issue #387 C1 (the TUI-operator-console PRD, O2): the report assembly (which branches run,
what unrealized is computed against which marks) lived inline in `keel/cli.py`'s command body.
It lives here now: `build_pnl_report` is pure over the transaction rows and marks it is handed
(all the FIFO math itself is `keel.analysis.pnl`, unchanged), and `render_pnl_report` returns
the exact lines the CLI echoes -- so the TUI's Account menu can show the same report without a
re-implementation drifting from it.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from keel.analysis import pnl as pnl_analysis


@dataclass(frozen=True)
class AssetPnl:
    """One asset's realized/position reading, plus its unrealized mark when one was supplied."""

    asset: str
    realized: Decimal
    open_qty: Decimal
    avg_cost: Decimal
    #: Set only when the caller supplied (or scoped to) a mark for this asset.
    mark: Decimal | None = None
    unrealized: Decimal | None = None


@dataclass(frozen=True)
class PnlReport:
    #: `None` means the OVERALL report (every asset); a value scopes to that one asset.
    asset: str | None
    total_realized: Decimal | None
    #: Per-asset rows. The scoped report carries exactly one; the overall report carries one
    #: per asset that currently has an open position.
    rows: tuple[AssetPnl, ...]


def build_pnl_report(
    transactions: list[dict[str, Any]], asset: str | None, marks: dict[str, Decimal]
) -> PnlReport:
    """Assemble the realized + unrealized FIFO report from imported transactions (read-only)."""
    if asset is not None:
        realized = pnl_analysis.realized_pnl(transactions, asset)
        pos = pnl_analysis.position(transactions, asset)
        row = AssetPnl(
            asset=asset,
            realized=realized,
            open_qty=pos.qty,
            avg_cost=pos.avg_cost,
            mark=marks.get(asset),
            unrealized=(
                (marks[asset] - pos.avg_cost) * pos.qty if asset in marks else None
            ),
        )
        return PnlReport(asset=asset, total_realized=None, rows=(row,))

    rows: list[AssetPnl] = []
    unrealized_by_asset = (
        pnl_analysis.unrealized_pnl(transactions, marks) if marks else {}
    )
    for asset_code in sorted({tx["asset"] for tx in transactions}):
        pos = pnl_analysis.position(transactions, asset_code)
        if not pos.qty:
            continue
        rows.append(
            AssetPnl(
                asset=asset_code,
                realized=pnl_analysis.realized_pnl(transactions, asset_code),
                open_qty=pos.qty,
                avg_cost=pos.avg_cost,
                mark=marks.get(asset_code),
                unrealized=unrealized_by_asset.get(asset_code),
            )
        )
    return PnlReport(
        asset=None,
        total_realized=pnl_analysis.realized_pnl(transactions),
        rows=tuple(rows),
    )


def render_pnl_report(report: PnlReport) -> list[str]:
    """The exact `keel pnl` lines, as a pure function of the report."""
    if report.asset is not None:
        (row,) = report.rows
        lines = [
            f"{row.asset}: realized={row.realized} open_qty={row.open_qty} "
            f"avg_cost={row.avg_cost}"
        ]
        if row.mark is not None:
            lines.append(
                f"{row.asset}: mark={row.mark} unrealized={row.unrealized}"
            )
        return lines

    lines = [f"total realized P&L: {report.total_realized}"]
    for row in report.rows:
        line = f"  {row.asset}: open_qty={row.open_qty} avg_cost={row.avg_cost}"
        if row.unrealized is not None:
            line += f" unrealized={row.unrealized}"
        lines.append(line)
    return lines
