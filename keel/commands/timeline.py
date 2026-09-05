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

**Tamper-evidence, and its three honest readings (#721).** #703 asked the CSV to carry each row's
hash, and shipped with the column reading NOT RECORDED on every row because no store hashed
anything. `keel/data/audit.py` now chains an append-only event per write to `orders`,
`transactions` and both attestation tables, so this feed reads real hashes -- and has to keep
three readings apart:

- `chained` -- an event exists for this row and the chain vouches for it.
- `not chained` -- no event was ever written. Rows predating #721, and every engine-log row, which
  comes from a FILE rather than a chained store. An honest gap; deliberately NOT a break, so a
  deployment upgrading into the chain does not open its timeline to a page of red.
- `chain broken` -- an event exists and falls at or after the first break. The hash is shown and
  is NOT evidence: a chain proves a sequence, so past a break the sequence is unproven. Showing
  these as `chained` would present unverified values as evidence, which is the one thing the
  column exists to prevent.

The research trials ledger (`keel/research/ledger.py`) is still not in this feed: it records
experiments rather than trading activity, and borrowing its hashes to decorate a trading audit
trail would be provenance laundering. The two stores share `keel_core.hashchain` -- one definition
of canonical JSON, so one row can only ever have one hash -- and nothing else.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from keel.commands.orders import normalise_scope, scope_start_ts
from keel.data.audit import ChainState
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

#: What the hash column says where no event was recorded. NOT blank: an empty cell in a column
#: headed `row_hash` reads as "nothing to report", and the honest reading is "nobody checked".
HASH_NOT_RECORDED = "NOT RECORDED"

#: What the chain says about one row, as a closed vocabulary (#721). The full reasoning is in the
#: module docstring; the short version is that a hash and a verdict are two different facts, and a
#: hash printed without one is a number an auditor cannot use.
CHAIN_STATUSES: tuple[str, ...] = ("chained", "not chained", "chain broken")

CHAINED = "chained"
NOT_CHAINED = "not chained"
CHAIN_BROKEN = "chain broken"

#: The characters a spreadsheet treats as the start of a formula. OWASP's list.
#:
#: `\n` and `\r` are here alongside `\t` because a cell can only be re-parsed from its start,
#: and a leading line break is one of the ways a value smuggles itself into that position.
#:
#: `-` and `+` are here because they are formulas too, not only signs -- which means a negative
#: figure gets quoted. That is the correct trade for an audit export: a spreadsheet shows
#: `'-12.30` as text rather than evaluating it, the value is still readable and still
#: re-importable, and losing numeric typing is a smaller harm than executing a cell.
_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r", "\n")


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
    # Checked against the LEADING-WHITESPACE-STRIPPED text, and the ORIGINAL is what gets quoted.
    # A strict first-character test is defeated by one space: `" =cmd|..."` is a legal
    # `coinbase_id` out of an imported venue CSV, it lands in a cell by itself, and Google Sheets
    # and LibreOffice trim on import before deciding whether a cell is a formula. Excel treats a
    # leading space as text -- but a defence that holds in one spreadsheet and not the two this
    # file will also be opened in is not a defence.
    # `lstrip(" ")` and not a bare `lstrip()`: tab and carriage return are TRIGGERS themselves,
    # so stripping all whitespace would consume the very characters being looked for and let
    # "\t=cmd" through. Spaces are the only thing skipped over.
    if text.lstrip(" ").startswith(_FORMULA_TRIGGERS):
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
    #: The row's own tamper-evidence: the `row_hash` of the latest `audit_events` statement about
    #: it, or `HASH_NOT_RECORDED` where none was written.
    row_hash: str = HASH_NOT_RECORDED
    #: One of `CHAIN_STATUSES`. Carried BESIDE the hash rather than inferred from it, because
    #: "a hash is present" and "the chain vouches for it" are different facts and the second is
    #: the one an auditor is actually asking about. A client that inferred the verdict from the
    #: presence of a 64-character string would call a broken row verified.
    chain_status: str = NOT_CHAINED


@dataclass(frozen=True)
class TimelineReport:
    now_ts: int
    scope: str
    scope_start_ts: int | None
    #: The applied `kind` filter, echoed back, or `""` for every kind.
    kind: str
    #: The applied row cap, or `None` when the caller asked for no slice (the CSV export).
    limit: int | None
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

    #: Whether the `audit_events` chain holds ANY events (#721). Distinct from `chain_errors`
    #: being empty: a verification over zero rows reports nothing and has verified nothing, and a
    #: green badge over a table nothing wrote is the failure this codebase keeps re-learning. A
    #: deployment that predates the chain and one that has done nothing since are both False here.
    chain_recorded: bool = False
    #: Every break the chain walk found, verbatim from `keel_core.hashchain`. Reported rather
    #: than raised, so both renderers can STATE the chain's condition instead of asserting it.
    chain_errors: tuple[str, ...] = ()

    #: `read_log_window`'s own word for how the engine log read went: `ok`, `missing`, `empty`,
    #: `oversized`, `unreadable`. Carried rather than acted on, so the page and the CSV can SAY
    #: the log did not reach this report.
    #:
    #: The alternative was tried and was wrong in both directions. Discarding it made an
    #: unreadable log indistinguishable from an idle engine -- under-reporting reality while
    #: looking healthy, which `activity.py`'s own docstring names as the failure. RAISING on it
    #: made an EMPTY log (an ordinary state: a fresh handler, the moment after a rotation) take
    #: out orders, flows and attestations too, a total outage to report a non-problem.
    log_status: str = "ok"

    @property
    def shown_count(self) -> int:
        """How many rows this report carries. Derived rather than stored, and held here because
        `keel/web/payload.py` may not call `len()` (Rule 6e)."""
        return len(self.rows)

    @property
    def chain_intact(self) -> bool:
        """Whether the chain found nothing wrong.

        TRUE over an empty chain, and that is the deliberate reading: nothing was checked, so
        nothing is broken, and a deployment upgrading into #721 must not open its timeline to a
        page of red. `chain_recorded` is the companion that says whether anything was checked at
        all, and the two are read together -- exactly the pairing
        `payload.py::_chain_payload` holds for the research ledger's badge.
        """
        return not self.chain_errors

    @property
    def log_gap(self) -> bool:
        """Whether the engine log's contents are MISSING FROM this report.

        `missing` is not a gap: a deployment that has never run has no log, and that is an
        ordinary fact rather than a hole in the record. Every other non-`ok` status is -- the
        file is there and what it holds did not reach this report, which is precisely what an
        auditor reading the CSV needs told rather than left to infer from an absence of rows.
        """
        return self.log_status not in ("ok", "missing")



#: The cap on one PAGE of the merged feed -- the response slice, not the read.
#:
#: Stated precisely because the first version of this note was wrong: the four reads underneath
#: are unfiltered (`get_orders`, `get_transactions` and both attestation reads are `SELECT *`,
#: scoped in Python afterwards), so the READ cost is the deployment's whole history whatever this
#: number says. That matches `gather_orders`' own convention and is not a regression -- but the
#: cap bounds what crosses the wire and what a browser renders, and claiming more than that is
#: the kind of comfortable inaccuracy this codebase's documentation standard exists to catch.
#:
#: Newest-first and capped means the page always answers, and the counts say how much it did not
#: show. `export_rows` deliberately does not use it.
DEFAULT_TIMELINE_LIMIT = 200
MAX_TIMELINE_LIMIT = 2000

class _Chain:
    """The chain state, as the two fields a `TimelineRow` carries.

    A thin adapter and not a second source of truth: `keel/data/audit.py` decides what the chain
    says, and this decides only how to SAY it on a row. Held as a class so the four row builders
    ask one object the same question rather than each reproducing the three-way reading.
    """

    def __init__(self, state: ChainState) -> None:
        self._state = state

    def of(self, store: str, reference: str) -> dict[str, str]:
        """`row_hash` and `chain_status` for one row, as kwargs.

        A row with no event is `not chained` -- an honest gap, deliberately not a break. A row
        whose event falls AT OR AFTER the first break is `chain broken`: its hash is still shown,
        because hiding it would destroy the very value an auditor would use to establish what the
        row said, but the status refuses to call it evidence. A chain proves a SEQUENCE, so past
        a break the sequence is unproven -- which is why this compares `seq_id` against the break
        rather than re-verifying the single row, whose own hash may well still match.
        """
        seen = self._state.hashes.get((store, reference))
        if seen is None:
            return {"row_hash": HASH_NOT_RECORDED, "chain_status": NOT_CHAINED}
        broken_from = self._state.first_broken_seq
        status = CHAINED if broken_from is None or seen.seq_id < broken_from else CHAIN_BROKEN
        return {"row_hash": seen.row_hash, "chain_status": status}


def _order_rows(repo: Repository, since_ts: int | None, chain: _Chain) -> list[TimelineRow]:
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
                **chain.of("orders", str(raw.get("id") or "")),
                summary=f"{status} {side} {product} ({mode})".strip(),
                product_id=product,
                amount=raw.get("actual_fill"),
                amount_kind="fill price" if raw.get("actual_fill") is not None else "",
            )
        )
    return rows


def _transaction_rows(
    repo: Repository, since_ts: int | None, chain: _Chain
) -> list[TimelineRow]:
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
                **chain.of("transactions", str(raw.get("coinbase_id") or raw.get("id") or "")),
                summary=f"{kind_word} {asset}".strip() + (f" -- {note}" if note else ""),
                product_id="",
                amount=raw.get("total"),
                amount_kind="flow total" if raw.get("total") is not None else "",
            )
        )
    return rows


def _attestation_rows(
    repo: Repository, since_ts: int | None, chain: _Chain
) -> list[TimelineRow]:
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
                **chain.of("asset_attestations", asset),
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
                **chain.of("instrument_attestations", f"{venue}:{product}"),
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
    limit: int | None = DEFAULT_TIMELINE_LIMIT,
    cycles: Sequence[Any] = (),
    log_status: str = "ok",
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
    # An unrecognised kind is COLLAPSED to "every kind", not applied. `?kind=trades` -- the
    # obvious typo, since the chips read "trade" -- would otherwise return a page that looks like
    # an empty deployment: a non-zero `scoped_count`, zero rows, and no chip marked current.
    # That is the outcome `normalise_scope`, which this function sits beside and reuses, exists
    # to never produce. The applied value is echoed in `kind`, so the substitution is visible.
    requested_kind = (kind or "").strip().lower()
    resolved_kind = requested_kind if requested_kind in TIMELINE_KINDS else ""
    # `None` means NO SLICE, and it has to be a distinct case rather than a very large number.
    # The first attempt at an uncapped export passed `2**31` through this clamp, which is
    # `min(2**31, 2000)` -- so the "whole scope" export quietly stopped at 2000 rows, and the
    # test written for it seeded 225 and could not see that. A sentinel that the clamp silently
    # eats is not a sentinel.
    resolved_limit = None if limit is None else max(1, min(int(limit), MAX_TIMELINE_LIMIT))
    since = scope_start_ts(resolved_scope, now_ts)

    # ONE read of `audit_events`, shared by all three store readers (#721). Not one lookup per
    # row: the hash printed beside a row and the verdict printed above it would then describe
    # different reads of the table, and an event appended between them would have the verdict
    # cover a row this report never showed.
    chain = repo.audit_chain()
    chained = _Chain(chain)

    scoped: list[TimelineRow] = []
    scoped.extend(_order_rows(repo, since, chained))
    scoped.extend(_transaction_rows(repo, since, chained))
    scoped.extend(_attestation_rows(repo, since, chained))
    # No chain argument, and never one: `_cycle_rows` reads the engine's own log FILE, which is
    # not a chained store. A cycle row carrying a hash would be this module attesting to something
    # it merely read.
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
        rows=tuple(filtered if resolved_limit is None else filtered[:resolved_limit]),
        log_status=log_status,
        kinds_present=tuple(kind for kind in TIMELINE_KINDS if kind in present),
        chain_recorded=chain.event_count > 0,
        chain_errors=chain.errors,
    )


def export_rows(
    repo: Repository,
    *,
    now_ts: int,
    scope: str = "all",
    kind: str = "",
    cycles: Sequence[Any] = (),
    log_status: str = "ok",
) -> TimelineReport:
    """The whole scope, uncapped -- what the CSV export reads.

    **Deliberately not `gather_timeline`'s cap.** That cap exists because the console polls the
    JSON route every 15 seconds; an export is a deliberate download, requested once, of a record
    an operator may hand to an auditor or a tax preparer. Inheriting the page's limit made the
    file 200 rows of a 5,000-event deployment with nothing in it saying so -- a partial record
    that reads as complete, which is worse than no export at all.

    The SCOPE still bounds it: `?scope=today|7d|all` is the operator's own choice about how much
    they are asking for, and `all` on a long-lived deployment is a large file by request rather
    than by accident.

    **The whole file is materialised in memory** -- as a `str`, then as `bytes`, because the
    response carries a `Content-Length`. That is an accepted cost for a download an operator
    asked for once, and it is the reason the paged route keeps its cap: the same read on a
    15-second poll would not be acceptable. Streaming it is the change to make if `all` on a
    multi-year deployment ever stops fitting comfortably.
    """
    return gather_timeline(
        repo,
        now_ts=now_ts,
        scope=scope,
        kind=kind,
        limit=None,
        cycles=cycles,
        log_status=log_status,
    )


def to_csv(report: TimelineReport) -> str:
    """The audit export: one row per event, every text cell neutralised (`csv_safe`).

    **Provenance is a column, not a footnote.** The point of this file is that a reader can tell
    a venue-reported fill from a line someone imported from a spreadsheet -- so `provenance` and
    `source` sit beside every figure, `row_hash` carries the chain's own hash where one was
    recorded and says NOT RECORDED rather than being blank where none was, and `chain_status`
    says whether that hash is evidence. All three in the same file, on every row.

    `amount_kind` rides beside `amount` for the same reason: a fill price and a cash-flow total
    in one column, with nothing saying which is which, is a column that will be summed.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    if not report.chain_intact:
        # Stated ABOVE the header, for the same reason the log note below is: an auditor holding
        # this file cannot ask the page anything, and a finding that changes how the whole file
        # should be read must travel with the file. Per-row `chain_status` says WHICH rows; this
        # says the record has been altered, which is the sentence that matters first.
        writer.writerow(
            [
                csv_safe(
                    f"# NOTE: the audit chain does not verify ({report.chain_errors[0]}); "
                    "every row marked `chain broken` below is shown but is NOT evidence"
                )
            ]
        )
    if report.log_gap:
        # Stated IN THE FILE, above the header, because this file leaves the application. An
        # auditor holding a CSV cannot ask the page whether a source was missing from it, and
        # rows that are simply absent look identical to rows that never existed.
        writer.writerow(
            [
                csv_safe(
                    f"# NOTE: the engine log could not be read ({report.log_status}); "
                    "cycle rows are missing from this export"
                )
            ]
        )
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
            # Beside the hash, never instead of it. A 64-character string with nothing saying
            # whether it verifies is a number an auditor cannot use, and the reading a reader
            # defaults to is the flattering one.
            "chain_status",
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
                csv_safe(row.chain_status),
            ]
        )
    return buffer.getvalue()
