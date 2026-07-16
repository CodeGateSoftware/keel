"""Import Coinbase transaction-history CSV exports into the `transactions` table.

Coinbase's account-export UI produces two different CSV shapes, both landing under
`transactions/*.csv` in this repo:

- The **"Transactions" export** — one row per ledger entry, headed
  `ID,Timestamp,Transaction Type,Asset,Quantity Transacted,Price Currency,Price at
  Transaction,Subtotal,Total (inclusive of fees and/or spread),Fees and/or Spread,Notes,...`.
  This is the shape that maps onto the `transactions` table and gets imported.
- The **Gain/Loss report** — a derived, informational-only tax report headed
  `Transaction Type,Transaction ID,Tax lot ID,...`. It has no stable one-row-per-transaction
  identity (a single transaction can be split across several tax-lot rows) and no
  qty/price/fee columns matching the `transactions` schema, so it duplicates data already
  present in the "Transactions" export rather than adding new facts. It is detected and its
  rows are skipped (counted, with a warning) rather than guessed at.

Both shapes are preceded by a variable number of preamble lines (disclaimers, report
titles, a `User,...` line, blank lines) before the real header row, so the header is
located by scanning rather than assumed to live on a fixed line number.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from halal_cb.data.repository import Repository

# Columns that identify the "Transactions" export header (order-independent subset check;
# the real export also has Sender/Recipient Address columns we don't need).
_TRANSACTIONS_HEADER_MARKERS = (
    "ID",
    "Timestamp",
    "Transaction Type",
    "Asset",
    "Quantity Transacted",
)
# Columns that identify the Gain/Loss report header.
_GAIN_LOSS_HEADER_MARKERS = ("Transaction ID", "Tax lot ID", "Gains (Losses) (USD)")

_SOURCE = "csv_import"
_MAX_PREAMBLE_LINES = 30


@dataclass
class ImportResult:
    """Outcome of importing one CSV file (or a directory of them)."""

    imported: int = 0
    skipped: int = 0
    warnings: list[str] = field(default_factory=list)

    def _merge(self, other: ImportResult) -> None:
        self.imported += other.imported
        self.skipped += other.skipped
        self.warnings.extend(other.warnings)


def _parse_money(raw: str | None) -> Decimal | None:
    """Parse a Coinbase money field: strips a leading `$` and thousands commas.

    Returns `None` for an empty/blank field. Raises `decimal.InvalidOperation` on
    unparseable input, letting callers treat that as a malformed row.
    """
    if raw is None:
        return None
    text = raw.strip()
    if text == "":
        return None
    text = text.replace("$", "").replace(",", "")
    return Decimal(text)


def _parse_ts(raw: str) -> int:
    """Parse a Coinbase `YYYY-MM-DD HH:MM:SS UTC` timestamp to epoch seconds."""
    text = raw.strip()
    if text.endswith(" UTC"):
        text = text[: -len(" UTC")]
    dt = datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    return int(dt.timestamp())


def _detect_shape(header: list[str]) -> str:
    """Classify a candidate header row as `"transactions"`, `"gain_loss"`, or `"unknown"`."""
    cells = {cell.strip() for cell in header}
    if set(_TRANSACTIONS_HEADER_MARKERS) <= cells:
        return "transactions"
    if set(_GAIN_LOSS_HEADER_MARKERS) <= cells:
        return "gain_loss"
    return "unknown"


def _find_header(rows: list[list[str]]) -> tuple[int, str] | None:
    """Scan the leading preamble rows for the real header row; return `(index, shape)`."""
    for i, row in enumerate(rows[:_MAX_PREAMBLE_LINES]):
        shape = _detect_shape(row)
        if shape != "unknown":
            return i, shape
    return None


def _row_to_tx(header: list[str], row: list[str]) -> dict[str, Any]:
    """Map one "Transactions"-shape data row to an `upsert_transaction()` dict.

    Raises `ValueError`/`decimal.InvalidOperation` on malformed input; `import_csv` catches
    these and counts the row as skipped-with-warning rather than dropping it silently.
    """
    if len(row) != len(header):
        raise ValueError(f"expected {len(header)} columns, got {len(row)}")
    fields = dict(zip(header, row, strict=True))

    coinbase_id = fields["ID"].strip()
    if not coinbase_id:
        raise ValueError("missing ID")
    tx_type = fields["Transaction Type"].strip()
    if not tx_type:
        raise ValueError("missing Transaction Type")
    asset = fields["Asset"].strip()
    if not asset:
        raise ValueError("missing Asset")

    ts = _parse_ts(fields["Timestamp"])
    qty = _parse_money(fields["Quantity Transacted"])
    if qty is None:
        raise ValueError("missing Quantity Transacted")

    return dict(
        coinbase_id=coinbase_id,
        source=_SOURCE,
        type=tx_type,
        asset=asset,
        ts=ts,
        qty=qty,
        price=_parse_money(fields.get("Price at Transaction")),
        subtotal=_parse_money(fields.get("Subtotal")),
        total=_parse_money(fields.get("Total (inclusive of fees and/or spread)")),
        fees=_parse_money(fields.get("Fees and/or Spread")),
        notes=fields.get("Notes") or None,
        rule_id=None,
        order_id=None,
    )


def import_csv(path: str | Path, repo: Repository) -> ImportResult:
    """Import one Coinbase CSV export into `repo`.

    Detects the "Transactions" vs Gain/Loss report shape by header (see module docstring).
    Only the "Transactions" shape maps onto the `transactions` table; Gain/Loss rows, and
    rows from a file whose header shape isn't recognized at all, are skipped — counted in
    `skipped` and explained in `warnings`, never silently dropped.

    Idempotent: a row whose `ID` already exists in `repo` is counted as skipped rather than
    re-imported (`Repository.upsert_transaction` would otherwise just overwrite it).
    """
    path = Path(path)
    result = ImportResult()

    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))

    located = _find_header(rows)
    if located is None:
        non_blank = [r for r in rows if any(cell.strip() for cell in r)]
        result.skipped += len(non_blank)
        result.warnings.append(f"{path.name}: could not detect a known CSV header shape")
        return result

    header_idx, shape = located
    data_rows = [r for r in rows[header_idx + 1 :] if any(cell.strip() for cell in r)]

    if shape == "gain_loss":
        result.skipped += len(data_rows)
        result.warnings.append(
            f"{path.name}: Gain/Loss report rows are informational-only and are not "
            f"imported ({len(data_rows)} row(s) skipped)"
        )
        return result

    header = rows[header_idx]
    existing_ids = {tx["coinbase_id"] for tx in repo.get_transactions()}

    for line_no, row in enumerate(data_rows, start=header_idx + 2):
        try:
            tx = _row_to_tx(header, row)
        except (ValueError, InvalidOperation, TypeError) as exc:
            result.skipped += 1
            result.warnings.append(f"{path.name}:{line_no}: malformed row skipped ({exc})")
            continue

        if tx["coinbase_id"] in existing_ids:
            result.skipped += 1
            continue

        repo.upsert_transaction(tx)
        existing_ids.add(tx["coinbase_id"])
        result.imported += 1

    return result


def import_dir(dir_path: str | Path, repo: Repository) -> ImportResult:
    """Import every `*.csv` file directly under `dir_path` (e.g. the `transactions/` export dir).

    Idempotent across the whole directory: each file re-queries `repo` for already-seen
    `coinbase_id`s (including ones just imported from earlier files in this same call), so
    re-running an import — or importing overlapping export files — counts already-present
    transactions as skipped rather than re-importing them.
    """
    dir_path = Path(dir_path)
    result = ImportResult()
    for csv_path in sorted(dir_path.glob("*.csv")):
        result._merge(import_csv(csv_path, repo))
    return result
