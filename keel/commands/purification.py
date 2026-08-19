"""The §65.9 purification-report renderer behind `keel purification`.

Issue #387 C1 (the TUI-operator-console PRD, O2): the COMPUTE has always been shared
(`keel.compliance.purification.build_report` -- which credits are non-compliant, what is owed,
what needs review); only the rendering lived inline in `keel/cli.py`'s command body. That
rendering lives here now so the TUI's Compliance menu (C3) can show the byte-identical report
without re-deriving or re-formatting it. ⛔ REPORT-ONLY, like the command: nothing here moves
funds -- it computes an amount owed and says so, exactly as the zakat estimate does.
"""

from __future__ import annotations

from decimal import Decimal

from keel.compliance.purification import PurificationReport


def render_purification_report(report: PurificationReport) -> list[str]:
    """The exact `keel purification` lines, as a pure function of the report."""
    if not report.entries and not report.needs_review:
        return ["no non-compliant credits found"]

    lines: list[str] = []
    if report.entries:
        lines.append(f"non-compliant credits: {len(report.entries)}")
        lines.append(f"\n{'asset':<8} {'units received':>20} {'owed (USD)':>14}")
        qty_by_asset = report.qty_by_asset
        for asset, owed in report.owed_by_asset.items():
            lines.append(
                f"{asset:<8} {qty_by_asset.get(asset, Decimal(0)):>20} {owed:>14.2f}"
            )
        lines.append(f"\nTOTAL OWED TO CHARITY: ${report.total_owed_usd:.2f}")
        lines.append(
            "\nThis is excluded from realised P&L and from the equity base sizing computes "
            "from -- otherwise riba would compound into position size (§65.9). Zakat, if "
            "estimated, is on purified wealth, so this runs first."
        )

    if report.needs_review:
        lines.append(
            f"\n⚠️  {len(report.needs_review)} credit(s) of UNRECOGNISED type need review:"
        )
        for entry in report.needs_review[:20]:
            lines.append(
                f"    {entry.tx_type!r} {entry.asset} qty={entry.qty} ${entry.amount_usd}"
            )
        lines.append(
            "    Classified neither way on purpose: calling them clean would let riba into "
            "P&L, calling them non-compliant would state an obligation as fact."
        )
    return lines
