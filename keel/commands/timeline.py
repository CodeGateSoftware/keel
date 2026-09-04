"""One chronology over everything keel has done -- issue #703.

Four stores record activity and none of them knew about the others: the engine's JSONL log
(cycles), the `orders` table (fills), the `transactions` ledger (cash flows), and the attestation
tables (what a human swore to). This module merges them into one timeline WITHOUT letting them
blur, which is the whole difficulty: a venue-reported fill, a line imported from a venue's CSV,
and a sentence a human typed are three different kinds of evidence, and a feed that presented
them identically would be worse than four separate tables.

Every row therefore carries its PROVENANCE as a first-class field, from a closed vocabulary, and
the provenance is never inferred from the row's shape -- it is a property of which store the row
came out of, decided here, once.

**Read-only, no broker, no network.** Same posture as every other service in this package.

**Nothing here is tamper-evident, and the export says so.** #703 asked the CSV to carry each
row's hash. None of these four stores hashes its rows: `orders`, `transactions`,
`asset_attestations` and `instrument_attestations` have no hash column between them, and the only
hash-chained store in this codebase is the research trials ledger (`keel/research/ledger.py`),
which records experiments rather than trading activity and does not belong in this feed. So the
hash column is emitted as NOT RECORDED rather than left blank -- blank invites the reader to
assume the check passed -- and hashing these tables is filed as engine work.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from keel.commands.orders import normalise_scope, scope_start_ts
from keel.data.repository import Repository

#: The type chips, and the only words `kind` ever takes.
TIMELINE_KINDS: tuple[str, ...] = ("trade", "flow", "attestation", "system")

#: How a row came to be known, as a closed vocabulary. The distinction this feed exists to keep:
#:
#: - `venue-reported` -- a live order; the venue told us it happened.
#: - `simulated` -- a paper order. The paper trader wrote it, no venue was involved, and calling
#:   it venue-reported would put synthetic fills and real ones under one word.
#: - `imported-ledger` -- a `transactions` row, read out of a venue's own CSV export.
#: - `human-attested` -- someone typed it and signed their name to it.
#: - `engine-log` -- the agent's own structured log of what it did.
PROVENANCES: tuple[str, ...] = (
    "venue-reported",
    "simulated",
    "imported-ledger",
    "human-attested",
    "engine-log",
)

#: What the hash column says until the engine records one. NOT blank: an empty cell in a column
#: headed `row_hash` reads as "nothing to report", and the honest reading is "nobody checked".
HASH_NOT_RECORDED = "NOT RECORDED"

#: The characters a spreadsheet treats as the start of a formula. OWASP's list.
#:
#: `-` and `+` are here because they are formulas too, not only signs -- which means a negative
#: figure gets quoted. That is the correct trade for an audit export: a spreadsheet shows
#: `'-12.30` as text rather than evaluating it, the value is still readable and still
#: re-importable, and losing numeric typing is a smaller harm than executing a cell.
_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value: Any) -> str:
    """One cell, safe to open in Excel or Google Sheets.

    **The security control on this export.** Both applications EXECUTE a cell whose text starts
    with a formula trigger, and several columns here carry text keel did not write: a
    transaction's `notes` (imported from a venue's CSV), an attestation's `source` and
    `attested_by` (typed by a human), `product_id` and `rule_name` (config). A cell reading
    `=cmd|...` in any of them runs when an auditor opens the file.

    Applied to EVERY text cell, not to a list of the risky ones: a maintained list of which
    columns are attacker-influenced is exactly the thing that rots, and the cost of over-applying
    is one leading quote on a figure.

    Quoting and comma/newline escaping belong to `csv.writer` and are deliberately not done here
    -- doing both would double-escape every field.
    """
    if value is None:
        return ""
    text = str(value)
    if text.startswith(_FORMULA_TRIGGERS):
        return "'" + text
    return text


@dataclass(frozen=True)
class TimelineRow:
    """One thing that happened, and how we know it happened."""

    ts: int
    #: One of `TIMELINE_KINDS` -- what the chips filter on.
    kind: str
    #: One of `PROVENANCES`. Never inferred from the row's shape by a renderer.
    provenance: str
    #: The store this came out of, named plainly (`orders`, `transactions`, ...), so a reader
    #: chasing a row knows which table to open.
    source: str
    #: That store's own identifier for the row -- an `orders.id`, a `coinbase_id`, an asset.
    reference: str
    #: One line a human can read. Assembled here so both renderers say the same sentence.
    summary: str
    #: The product this concerns, or `""` where the record has none (a cash flow, an
    #: asset-level attestation).
    product_id: str
    #: The figure this row is ABOUT, and what that figure is. `amount_kind` exists so a fill
    #: price, a cash flow and a fee are never summed by a reader who assumed one column meant
    #: one thing.
    amount: Decimal | None
    amount_kind: str
    #: The row's own tamper-evidence, when its store records one. None of the four does today.
    row_hash: str = HASH_NOT_RECORDED


@dataclass(frozen=True)
class TimelineReport:
    now_ts: int
    scope: str
    scope_start_ts: int | None
    #: The applied `kind` filter, echoed back, or `""` for every kind.
    kind: str
    limit: int
    #: Rows across all four sources inside the scope, before `kind` and before `limit`.
    scoped_count: int
    #: Rows after `kind`, before `limit` -- what `rows` is a page of.
    filtered_count: int
    rows: tuple[TimelineRow, ...]

    #: Every kind present in the SCOPED set, in `TIMELINE_KINDS` order -- what a chip bar is
    #: built from. STORED rather than derived from `rows`, because `rows` is what the chip and
    #: the cap left: derived from those, selecting Flows would delete the Trades chip and leave
    #: no way back, and a kind whose only rows fell past the limit would vanish from the bar
    #: while still being in the window. A control that deletes its own alternatives is worse
    #: than one that is sometimes empty.
    #:
    #: In the DECLARED order rather than first-seen: these four are a fixed vocabulary, and a
    #: bar that reordered itself as history arrived would move under the reader.
    kinds_present: tuple[str, ...]

    @property
    def shown_count(self) -> int:
        """How many rows this report carries. Derived rather than stored, and held here because
        `keel/web/payload.py` may not call `len()` (Rule 6e)."""
        return len(self.rows)



#: The cap on one merged read. Four stores, three of them unbounded, joined into one response:
#: without a cap this route's cost is the size of the deployment's whole history. Newest-first
#: and capped means the page always answers, and the counts below say how much it did not show.
DEFAULT_TIMELINE_LIMIT = 200
MAX_TIMELINE_LIMIT = 2000


def _order_rows(repo: Repository, since_ts: int | None) -> list[TimelineRow]:
    """`orders` -> trade rows.

    A PAPER order is `simulated`, not `venue-reported`. The paper trader wrote that row with no
    venue involved, and one word covering both would put synthetic fills and real ones in the
    same bucket -- which is the thing four separate tables at least never did.
    """
    rows: list[TimelineRow] = []
    for raw in repo.get_orders():
        created = raw.get("created_at")
        if created is None or (since_ts is not None and int(created) < since_ts):
            continue
        mode = str(raw.get("mode") or "")
        side = str(raw.get("side") or "")
        product = str(raw.get("product_id") or "")
        status = str(raw.get("status") or "")
        rows.append(
            TimelineRow(
                ts=int(created),
                kind="trade",
                provenance="simulated" if mode == "paper" else "venue-reported",
                source="orders",
                reference=str(raw.get("id") or ""),
                summary=f"{status} {side} {product} ({mode})".strip(),
                product_id=product,
                amount=raw.get("actual_fill"),
                amount_kind="fill price" if raw.get("actual_fill") is not None else "",
            )
        )
    return rows


def _transaction_rows(repo: Repository, since_ts: int | None) -> list[TimelineRow]:
    """`transactions` -> flow rows.

    `imported-ledger`, never `venue-reported`: these lines came out of a CSV the operator
    downloaded, and that the venue produced the file does not make the row a report -- nothing
    verified it on the way in.
    """
    rows: list[TimelineRow] = []
    for raw in repo.get_transactions():
        ts = raw.get("ts")
        if ts is None or (since_ts is not None and int(ts) < since_ts):
            continue
        kind_word = str(raw.get("type") or "")
        asset = str(raw.get("asset") or "")
        note = str(raw.get("notes") or "")
        rows.append(
            TimelineRow(
                ts=int(ts),
                kind="flow",
                provenance="imported-ledger",
                source="transactions",
                reference=str(raw.get("coinbase_id") or raw.get("id") or ""),
                summary=f"{kind_word} {asset}".strip() + (f" -- {note}" if note else ""),
                product_id="",
                amount=raw.get("total"),
                amount_kind="flow total" if raw.get("total") is not None else "",
            )
        )
    return rows


def _attestation_rows(repo: Repository, since_ts: int | None) -> list[TimelineRow]:
    """The attestation tables -> attestation rows.

    `human-attested`: someone typed this and signed their name to it, which is a different kind
    of evidence from anything a machine reported. The name is in the summary because "who swore
    to this" is the first thing an auditor asks of an attestation.
    """
    rows: list[TimelineRow] = []
    for raw in repo.get_asset_attestations():
        ts = raw.get("attested_at")
        if ts is None or (since_ts is not None and int(ts) < since_ts):
            continue
        asset = str(raw.get("asset") or "")
        rows.append(
            TimelineRow(
                ts=int(ts),
                kind="attestation",
                provenance="human-attested",
                source="asset_attestations",
                reference=asset,
                summary=(
                    f"{asset} attested by {raw.get('attested_by') or 'unnamed'} "
                    f"(source: {raw.get('source') or 'unstated'})"
                ),
                product_id="",
                amount=None,
                amount_kind="",
            )
        )
    for raw in repo.get_instrument_attestations():
        ts = raw.get("attested_at")
        if ts is None or (since_ts is not None and int(ts) < since_ts):
            continue
        product = str(raw.get("product_id") or "")
        venue = str(raw.get("venue") or "")
        rows.append(
            TimelineRow(
                ts=int(ts),
                kind="attestation",
                provenance="human-attested",
                source="instrument_attestations",
                reference=f"{venue}:{product}",
                summary=(
                    f"{product} on {venue} attested by "
                    f"{raw.get('attested_by') or 'unnamed'} "
                    f"(wrapper: {raw.get('wrapper') or 'unstated'})"
                ),
                product_id=product,
                amount=None,
                amount_kind="",
            )
        )
    return rows


def _cycle_rows(cycles: Iterable[Any], since_ts: int | None) -> list[TimelineRow]:
    """`ActivityCycle`s -> system rows.

    `engine-log`: the agent's own account of what it did. Distinct from `venue-reported` because
    nothing outside this process confirmed it, and distinct from `human-attested` because nobody
    signed it.
    """
    rows: list[TimelineRow] = []
    for cycle in cycles:
        ts = int(getattr(cycle, "started_ts", 0) or 0)
        if since_ts is not None and ts < since_ts:
            continue
        products: tuple[str, ...] = tuple(getattr(cycle, "products", ()) or ())
        rows.append(
            TimelineRow(
                ts=ts,
                kind="system",
                provenance="engine-log",
                source="engine log",
                reference=str(getattr(cycle, "cycle_id", "") or getattr(cycle, "key", "")),
                summary=(
                    f"cycle: {getattr(cycle, 'signals', 0)} signal(s), "
                    f"{getattr(cycle, 'entered', 0)} entered, "
                    f"{getattr(cycle, 'exited', 0)} exited, "
                    f"{getattr(cycle, 'errors', 0)} error(s)"
                ),
                product_id=products[0] if len(products) == 1 else "",
                amount=None,
                amount_kind="",
            )
        )
    return rows


def gather_timeline(
    repo: Repository,
    *,
    now_ts: int,
    scope: str = "all",
    kind: str = "",
    limit: int = DEFAULT_TIMELINE_LIMIT,
    cycles: Sequence[Any] = (),
) -> TimelineReport:
    """One chronology over four stores, newest first, scoped, chip-filtered and capped.

    `cycles` is passed IN rather than read here: the engine log lives on disk behind a config
    path, and `keel/commands/activity.py` already owns finding it, reading a bounded window of it
    and parsing it. Re-doing any of that would be a second answer to "what did the agent do", and
    the caller that has a config is the one that can supply them.

    The scope is `orders`' own `normalise_scope`/`scope_start_ts` rather than a third copy: this
    is the same question those already answer, and a third implementation is a third thing to
    drift.

    Filtering is server-side because the cap is: a client filtering the capped page would be
    filtering the rows it happened to receive and calling the result "every flow this month".
    """
    resolved_scope = normalise_scope(scope)
    resolved_kind = (kind or "").strip().lower()
    resolved_limit = max(1, min(int(limit), MAX_TIMELINE_LIMIT))
    since = scope_start_ts(resolved_scope, now_ts)

    scoped: list[TimelineRow] = []
    scoped.extend(_order_rows(repo, since))
    scoped.extend(_transaction_rows(repo, since))
    scoped.extend(_attestation_rows(repo, since))
    scoped.extend(_cycle_rows(cycles, since))

    # Newest first, HERE -- so neither renderer sorts, and they cannot disagree about what "the
    # latest thing" is. `reference` breaks a tie so two rows at the same second keep a stable
    # order across repaints rather than an arbitrary one.
    scoped.sort(key=lambda row: (row.ts, row.reference), reverse=True)

    filtered = [row for row in scoped if not resolved_kind or row.kind == resolved_kind]
    present = {row.kind for row in scoped}
    return TimelineReport(
        now_ts=now_ts,
        scope=resolved_scope,
        scope_start_ts=since,
        kind=resolved_kind,
        limit=resolved_limit,
        scoped_count=len(scoped),
        filtered_count=len(filtered),
        rows=tuple(filtered[:resolved_limit]),
        kinds_present=tuple(kind for kind in TIMELINE_KINDS if kind in present),
    )


def to_csv(report: TimelineReport) -> str:
    """The audit export: one row per event, every text cell neutralised (`csv_safe`).

    **Provenance is a column, not a footnote.** The point of this file is that a reader can tell
    a venue-reported fill from a line someone imported from a spreadsheet -- so `provenance` and
    `source` sit beside every figure, and `row_hash` says NOT RECORDED rather than being blank.

    `amount_kind` rides beside `amount` for the same reason: a fill price and a cash-flow total
    in one column, with nothing saying which is which, is a column that will be summed.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "ts",
            "kind",
            "provenance",
            "source",
            "reference",
            "product_id",
            "amount",
            "amount_kind",
            "summary",
            "row_hash",
        ]
    )
    for row in report.rows:
        writer.writerow(
            [
                csv_safe(row.ts),
                csv_safe(row.kind),
                csv_safe(row.provenance),
                csv_safe(row.source),
                csv_safe(row.reference),
                csv_safe(row.product_id),
                csv_safe("" if row.amount is None else format(row.amount, "f")),
                csv_safe(row.amount_kind),
                csv_safe(row.summary),
                csv_safe(row.row_hash),
            ]
        )
    return buffer.getvalue()
