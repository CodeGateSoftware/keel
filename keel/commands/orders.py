"""The orders ledger, as a report (#659).

`orders` is keel's audit trail: one row per order the engine placed, holding what was
intended, what the venue gave back, what it cost, and -- the field an operator on an
`autonomy: ON` deployment needs first -- whether a human approved it or keel placed it
alone. `Repository.get_orders()` has existed since the beginning and, before this module,
nothing in either front-end called it. Reading what keel had actually bought or sold meant
opening SQLite by hand.

This is the SERVICE half: one gather, one renderer, one click command. `keel/web/payload.py`
projects the same `OrdersReport` onto the browser's JSON, so the terminal listing and the web
view cannot disagree about a figure -- the arrangement #641 used for the bracket column, and
the reason no arithmetic below is repeated in either front-end.

WHAT IS DELIBERATE HERE, AND WHY

**No `mode` filter reaches `get_orders`.** Each deployment book holds exactly one mode --
`keel-live.db` is all `live`, `keel.db` and `keel-paperhourly.db` are all `paper` -- so a
report that quietly passed `mode="live"` would render EMPTY on two of the three real books
and read as "no orders yet" rather than "the filter excluded everything". `mode` is carried
on the ROW instead, so a paper fill and a live one are distinguishable at a glance and
nobody has to infer it from which window they are looking at.

**The reversal is here, not in a renderer.** `get_orders` returns oldest-first by `id`;
every front-end wants the opposite. Reversing in each renderer would be the same decision
made twice, and the second copy is the one that drifts.

**Three counts, because "empty" has two causes.** `total_count` is the whole book,
`scoped_count` what survived the scope, `shown_count` what survived `limit`. An empty table
that could mean either "this book has never traded" or "the window you chose excluded
everything" is a surface asserting something it has not established, so `empty_reason` says
which -- decided here, where the counts are, rather than by a client comparing numbers.

**`raw_response` never leaves this module.** It is unbounded venue JSON and the one column
that could carry something unexpected into a page. `_venue_order_id` reads the single key
anything has ever read from it (`order_id`, the same key `executor._native_order_id` and
`reconcile._native_order_id` read), rejects anything that is not a short scalar, and returns
a string. The blob itself is not a field of `OrderRow` and cannot be rendered by accident.

**The divergence is signed AND judged, and both are done in Python.** `actual_fill -
expected_fill` is realised slippage, and its sign alone is not a verdict: paying MORE than
expected is adverse on a BUY and favourable on a SELL. A front-end that coloured a negative
difference red would be wrong half the time, and `tests/commands/test_console_thinness.py`'s
Rule 3 forbids the web layer from doing the subtraction at all. So `fill_divergence` carries
the number and `fill_divergence_adverse` carries the verdict.
"""

from __future__ import annotations

import datetime
import json
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

import click
from keel_core.telemetry import log_exception

from keel.commands._common import _open_repo_ro, with_disclaimer
from keel.data.repository import Repository
from keel.execution.executor import RESTING_STATUSES

logger = logging.getLogger(__name__)

#: The scope vocabulary, spelled exactly as `keel.commands.activity.ACTIVITY_SCOPES` spells
#: it. Two views over the same deployment answering "how far back" with different words would
#: be a needless thing for an operator to hold in their head, and the meanings are identical:
#: UTC calendar days, counting the current one.
ORDERS_SCOPES: tuple[str, ...] = ("today", "7d", "all")

#: **`"all"`, unlike Activity's `"today"`, and the difference is not an oversight.** Activity
#: reads a log the deployment writes every cycle, so "today" is populated on any running book.
#: Orders are placed rarely -- four rows on the live book, fifteen on the paper one, spread
#: over months -- so a `"today"` default would render empty on every real deployment and read
#: as "keel has never traded". The default a report opens at must be the one that tells the
#: truth without an argument.
DEFAULT_ORDERS_SCOPE = "all"

#: How many UTC calendar days each bounded scope spans, counting the current one -- the same
#: table, with the same reasoning, as `activity._SCOPE_DAYS`.
_SCOPE_DAYS: dict[str, int] = {"today": 1, "7d": 7}

#: Rows served when a caller names no limit. The journal's cap (`api.DEFAULT_JOURNAL_LIMIT`),
#: for the same reason: a bounded read against a book that may grow for years, sized so the
#: common case is never truncated at all.
DEFAULT_ORDERS_LIMIT = 50

#: The ceiling on a caller-supplied limit. A row count from a query string is a memory
#: primitive against a server with no proxy in front of it; a bulk export is `--limit` in the
#: operator's own process against their own machine's limits.
MAX_ORDERS_LIMIT = 1000

#: What each `orders.confirmation` word MEANS, in the words an operator needs.
#:
#: Four words reach this column and the issue that asked for this view knew of two. Read off
#: the write sites rather than guessed:
#:
#: * `autonomous` -- `executor._order_row(intent, mode, ...)` with `mode="autonomous"`: no
#:   human saw this order before it went to the venue.
#: * `confirm` -- the same row with `mode="confirm"`, which `executor.execute` writes only
#:   AFTER `confirm_fn(preview)` returned truthy. A declined confirmation writes no row at
#:   all, so this word means a human approved it, never merely that they were asked.
#: * `bypass` -- rows written before 2026-07-21, when `autonomous` was spelled that way.
#:   `executor._order_row`'s own note says they are deliberately left as written rather than
#:   rewritten by a migration, so a reader of an old live book will meet this word and must
#:   not be left to guess.
#: * `paper` -- `strategy.paper.PaperTrader`, which asks nobody and calls no venue.
CONFIRMATION_MEANINGS: dict[str, str] = {
    "autonomous": "keel placed this alone -- no human approved it",
    "confirm": "a human approved this at the terminal before it was placed",
    "bypass": "keel placed this alone (pre-2026-07-21 spelling of 'autonomous')",
    "paper": "simulated by the paper trader -- no human, no venue",
}

#: The words that mean "no human saw this order". `bypass` is here because it is the SAME
#: fact under an older name, and a view that answered "did I approve this" correctly only for
#: rows written after a particular Tuesday would be worse than one that did not answer at all.
AUTONOMOUS_CONFIRMATIONS: frozenset[str] = frozenset({"autonomous", "bypass"})

#: What a row says when `confirmation` is NULL or blank. Not "unknown" as a shrug: a row with
#: no confirmation word predates the column being written at all, which is itself the fact.
UNRECORDED_CONFIRMATION_DETAIL = "not recorded on this row"

#: The longest `order_id` this module will carry out of `raw_response`. Coinbase's is a UUID
#: (36); Alpaca's is a UUID; Kraken's is 19. 128 is far above every venue in the tree and far
#: below anything that could be a payload rather than an identifier -- the point is that the
#: cap exists, not its exact value, because the column it is read from is the one field on
#: this table whose contents no keel code has ever constrained.
MAX_VENUE_ORDER_ID_CHARS = 128

#: Why a row has no venue order id, chosen by the fact that explains it rather than left
#: blank. A blank cell reads as missing data; "the paper trader placed this" reads as the
#: answer it is.
PAPER_NO_VENUE_DETAIL = "no venue order id -- the paper trader placed this, not an exchange"
LIVE_NO_VENUE_DETAIL = "no venue order id recorded"

#: Why a paper row's `fee` is not evidence about what trading costs. `PaperTrader` computes it
#: as `fill * qty * fee_pct` from the configured rate, so reading a paper fee back as a
#: measurement of that rate is circular. Live rows carry the venue's own previewed commission,
#: upgraded to the observed one by `executor._upgrade_to_observed_economics` or by
#: `execution.reconcile`, and those are the rows that evidence anything.
MODELLED_FEE_DETAIL = "modelled from the configured rate, not charged by a venue"
CHARGED_FEE_DETAIL = "as charged by the venue"

#: Why a row carries no `submit_best_bid`/`submit_best_ask` pair (#626 added the columns while
#: this view was being built, and they are exactly the audit columns it exists to surface).
#:
#: NULL there means NOT OBSERVED, never zero, and the two causes are different facts. A paper row
#: has no book BY DESIGN -- `PaperTrader` fills synthetically against no venue at all, and #626's
#: schema note says a fabricated book sharing a column name with a real one would poison the very
#: measurement the columns exist for. A live row with no book is a preview that carried nothing
#: readable, which is a gap in the record rather than a property of the mode.
PAPER_NO_BOOK_DETAIL = "no venue book -- the paper trader fills synthetically, against no venue"
LIVE_NO_BOOK_DETAIL = "no readable book was recorded at submit"


def normalise_scope(scope: str) -> str:
    """Any unrecognised scope collapses to the default rather than raising.

    `activity.normalise_scope`'s reasoning, unchanged: this value arrives from a query string,
    and an empty screen is the one outcome a report like this exists to never produce. The
    resolved value is carried on the report (`OrdersReport.scope`) so a caller can see that
    its input was changed rather than honoured.
    """
    return scope if scope in ORDERS_SCOPES else DEFAULT_ORDERS_SCOPE


def normalise_limit(limit: int | None) -> int:
    """Clamp a caller-supplied row count into `[1, MAX_ORDERS_LIMIT]`.

    A zero or a negative would reach the slice below as a bound and answer an EMPTY report
    that looks like an empty book -- exactly the confusion `empty_reason` exists to prevent,
    arriving through the one door that bypasses it. Clamped rather than refused for the same
    reason `normalise_scope` normalises: the resolved value is echoed on the report.
    """
    if limit is None:
        return DEFAULT_ORDERS_LIMIT
    return max(1, min(int(limit), MAX_ORDERS_LIMIT))


def scope_start_ts(scope: str, now_ts: float) -> int | None:
    """The epoch second a scope begins at -- 00:00 UTC of the first calendar day it covers --
    or `None` for `"all"`, which has no lower bound.

    UTC, and derived from the caller's clock rather than a `time.time()` buried in here, for
    `activity.scope_start_ts`'s reasons: the deployment's unit of work is one UTC day, a
    boundary in one frame with timestamps in another is how a "today" view comes to exclude a
    row whose printed date IS today's, and an injectable boundary is the only kind every test
    of it can be deterministic about.

    Never raises: a `now_ts` outside the platform's range returns `None`, which degrades to an
    unfiltered report rather than an empty one.
    """
    days = _SCOPE_DAYS.get(scope)
    if days is None:
        return None
    try:
        day = datetime.datetime.fromtimestamp(now_ts, datetime.UTC).date() - datetime.timedelta(
            days=days - 1
        )
        return int(datetime.datetime.combine(day, datetime.time.min, datetime.UTC).timestamp())
    except OSError, OverflowError, ValueError:
        return None


@dataclass(frozen=True)
class OrderRow:
    """One `orders` row, projected onto what a reader needs and nothing else.

    `raw_response` is deliberately NOT a field. Everything anything has ever read from it is
    `venue_order_id`; the rest is unbounded venue JSON, and a dataclass that carried it would
    make rendering it wholesale a one-line mistake in a front-end rather than an impossible
    one.
    """

    #: The `orders.id` primary key -- the number `keel doctor` and the log lines name.
    id: int

    #: `live` or `paper`, per ROW. Never inferred from the book: see the module docstring.
    mode: str

    product_id: str
    side: str
    order_type: str
    status: str

    #: The raw `confirmation` word, or `""`. Kept alongside `confirmation_detail` so a machine
    #: consumer can match on the word while a human reads the sentence.
    confirmation: str

    #: One sentence saying what that word means, from `CONFIRMATION_MEANINGS`.
    confirmation_detail: str

    #: True when no human saw this order -- `autonomous` or its legacy spelling. The single
    #: most important bit on an `autonomy: ON` deployment, which is why it is a decided
    #: boolean here rather than a word a front-end has to know how to compare.
    confirmation_is_autonomous: bool

    #: The ORDERED size.
    qty: Decimal | None

    #: What actually executed (#446). NULL on rows written before the column existed, which
    #: means "not observed" rather than "zero".
    filled_quantity: Decimal | None

    limit_price: Decimal | None

    #: The rule's intended entry, recorded at placement. Entries route MARKET unconditionally
    #: (#258), so this is what the engine SIZED against, never what it waited for.
    expected_fill: Decimal | None

    #: The price actually achieved.
    actual_fill: Decimal | None

    #: `actual_fill - expected_fill` -- realised slippage, or `None` when either side is
    #: missing or the subtraction is not finite.
    fill_divergence: Decimal | None

    #: Whether that divergence went AGAINST this order: paying more than expected on a BUY,
    #: receiving less than expected on a SELL. `None` when there is no divergence to judge or
    #: the side is not one this can reason about. A sign is not a verdict; see the module
    #: docstring.
    fill_divergence_adverse: bool | None

    #: The fee as recorded. Never a rate: a percentage computed for display would be this
    #: layer inventing a figure the report does not hold.
    fee: Decimal | None

    #: True when `fee` was computed by the paper trader from the configured rate rather than
    #: charged by a venue -- so nobody reads a paper row back as evidence about real costs.
    fee_is_modelled: bool

    #: One sentence saying which of those two this fee is.
    fee_detail: str

    #: The venue's own best bid at the moment this order was submitted (#626), or `None` for
    #: NOT OBSERVED. Carried as the PAIR, and no spread is derived from it anywhere in this
    #: module or in either renderer -- #626 stored two columns rather than one delta for the
    #: same reason `expected_fill` and `actual_fill` are two: half-spread-from-mid,
    #: half-spread-from-the-side-crossed and relative spread are three different questions off
    #: one pair, and a view that silently answered one of them would be choosing on the
    #: reader's behalf. Same rule as `fee`: the figure as recorded, never a rate.
    submit_best_bid: Decimal | None

    #: The venue's own best ask at submit, on the same terms.
    submit_best_ask: Decimal | None

    #: True when BOTH sides of that pair were recorded. A half-observed book is not a book.
    submit_book_observed: bool

    #: Why the pair is absent, when it is: `""` when both sides are present.
    submit_book_detail: str

    #: The venue's own id for this order, or `""`. The ONLY thing read out of `raw_response`.
    venue_order_id: str

    #: Why it is absent, when it is: `""` when there is an id to show.
    venue_order_id_detail: str

    #: The NAME of the rule that placed this order -- `rules.kind`, the same string
    #: `build_rule_track_record` calls `rule_name`, so the Orders view and the track-record
    #: table name one rule one way. `""` when there is no name to show, for either of the two
    #: reasons `rule_name_detail` tells apart.
    rule_name: str

    #: Why the name is absent, when it is. Two states that both render as an empty cell and are
    #: NOT the same fact: no rule was recorded against this order at all, or a rule WAS and that
    #: rule is no longer in the book. The second is the one worth investigating, so it names the
    #: id. `""` when there is a name.
    rule_name_detail: str

    rule_id: int | None
    created_at: int | None
    updated_at: int | None
    #: What cancelling this order would be, and what it would take (#707).
    #:
    #: On the ROW rather than looked up by a front-end, because both front-ends need it and the
    #: classification is the feature: the console renders a modal from it and the CLI gates on it,
    #: and a console that classified for itself could describe an order as a frictionless entry
    #: while the terminal demanded the typed phrase for it.
    #:
    #: Defaulted and LAST, so every existing `OrderRow(...)` construction in this module and its
    #: tests is untouched -- a dataclass cannot carry a default ahead of a required field.
    cancel: CancelDecision = field(
        default_factory=lambda: CancelDecision(0, "unknown", False, True)
    )


@dataclass(frozen=True)
class OrdersReport:
    """`gather_orders`'s result: the rows, the scope that produced them, and the three counts
    that make an empty table interpretable."""

    now_ts: int

    #: The resolved scope -- one of `ORDERS_SCOPES`, after `normalise_scope`.
    scope: str

    #: 00:00 UTC the scope begins at, or `None` for `"all"` (and for a `now_ts` so broken that
    #: no boundary could be computed, which degrades to showing everything).
    scope_start_ts: int | None

    #: The resolved row cap, after `normalise_limit`.
    limit: int

    #: Rows in the book -- every mode, every status, no scope. The denominator that turns
    #: "nothing here" into a fact rather than a guess.
    total_count: int

    #: Rows inside the scope, before `status` and before `limit`. Its meaning is unchanged by
    #: the status filter on purpose: it is the denominator that says whether the SCOPE is what
    #: emptied the table, and a count that moved with the open tab could not answer that.
    scoped_count: int

    #: Rows inside the scope AND matching `status`, before `limit` -- what `rows` is a page of.
    #: Equal to `scoped_count` when no status filter is applied. It exists because "1 of 3" on
    #: a Canceled tab must count canceled orders; counting the scope there would be a true
    #: number answering a question nobody asked.
    filtered_count: int

    #: Rows in `rows`. Held on the report rather than measured by a front-end: Rule 6e of
    #: `test_console_thinness.py` bans `len()` in the serialiser precisely so a count on the
    #: wire is one the report already carries.
    shown_count: int

    #: Every distinct `mode` present in the whole book, sorted. A deployment book holds one
    #: mode in practice, and stating which one it is here means a reader never has to conclude
    #: it from an empty live section.
    modes: tuple[str, ...]

    #: The resolved status filter, lowercased, or `""` for "every status". Echoed back for the
    #: reason `scope` is: a client rendering the active tab reads what was APPLIED rather than
    #: trusting what it asked for.
    status: str

    #: Every distinct `status` present in the whole book, sorted -- what a tab bar is built
    #: from. From the WHOLE book and not the scope, deliberately: scoped, the Canceled tab
    #: would vanish on a quiet day and return tomorrow, and a control that comes and goes is
    #: worse than one that is sometimes empty. Same reasoning as `modes`.
    statuses: tuple[str, ...]

    #: `""` when there are rows; `"book"` when the book has never held an order; `"scope"`
    #: when it holds orders and this window excluded all of them; `"status"` when the window
    #: holds orders and none of them wear the filtered status. Decided here, where the counts
    #: are -- four different facts that all render as zero rows, and a reader who cannot tell
    #: them apart learns the wrong one.
    empty_reason: str

    #: NEWEST FIRST. Reversed here, never in a renderer.
    rows: tuple[OrderRow, ...]


#: What an order with no `rule_id` says. A real state, not a defect: a manual order, or one
#: placed before the column was written.
NO_RULE_DETAIL = "no rule recorded against this order"

#: What an order whose rule has left the book says. Deliberately different wording AND the id,
#: because "nothing placed this" and "rule 7 placed this and rule 7 is gone" are different facts
#: and only the second is worth chasing.
MISSING_RULE_DETAIL = "rule {rule_id} placed this order and is no longer in the book"

#: What a row says when nobody LOOKED. A caller that passed no rule map has not searched the
#: book, so reporting the rule as gone would state the result of a search that never ran -- and
#: would send a reader hunting for a deleted rule that is sitting in the table. The same sentence
#: covers a rule that WAS found and has no `kind` to show: it is present, so "no longer in the
#: book" would be false, and the only honest report is that no name is available.
UNRESOLVED_RULE_DETAIL = "rule {rule_id} placed this order; its name is unresolved here"


def _venue_order_id(raw_response: Any) -> str:
    """The venue's own order id, and nothing else, out of `raw_response`.

    `executor._native_order_id` and `reconcile._native_order_id` both read `order_id` and
    nothing else from this column; that is the whole of what has ever been read from it, and
    this reads the same key rather than inventing a second interpretation of the blob.

    What it adds, because this value reaches a browser and theirs does not:

    * a non-object document (a bare list, a string, a number) yields `""` rather than
      raising -- nothing constrains what a venue wrote here;
    * a non-scalar `order_id` (a dict, a list) yields `""`, because an identifier that is a
      structure is not an identifier;
    * anything longer than `MAX_VENUE_ORDER_ID_CHARS` yields `""`, so a column that turned
      out to hold a document under this key cannot become a page through it.
    """
    if not raw_response:
        return ""
    try:
        data = json.loads(raw_response)
    except TypeError, ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    value = data.get("order_id")
    if value is None or isinstance(value, (dict, list, tuple, set, bool)):
        return ""
    text = str(value).strip()
    if not text or len(text) > MAX_VENUE_ORDER_ID_CHARS:
        return ""
    return text


def _divergence(
    expected: Decimal | None, actual: Decimal | None
) -> tuple[Decimal | None, bool | None]:
    """`actual - expected`, or `(None, None)` when there is nothing to subtract.

    The subtraction is guarded the way `executor._warn_if_market_routing_overrides_entry`
    guards its own: `Decimal.is_finite()` admits extreme exponents (a rule bug like `1E+999999`
    parses and compares fine) and arithmetic on such magnitudes raises. A report that crashed
    because one historical row held a nonsense price would take the whole ledger down with it,
    so the row loses its divergence and keeps everything else.

    The verdict is returned unresolved (`None`) here; `_adverse` applies the side.
    """
    if expected is None or actual is None:
        return None, None
    try:
        if not expected.is_finite() or not actual.is_finite():
            return None, None
        difference = actual - expected
        if not difference.is_finite():
            return None, None
    except ArithmeticError, InvalidOperation, TypeError, ValueError:
        return None, None
    return difference, None


def _adverse(side: str, difference: Decimal | None) -> bool | None:
    """Whether a fill divergence went against the order.

    A BUY that filled ABOVE its expected price paid more than the engine sized against; a SELL
    that filled BELOW it received less. Zero is neither, and is reported as not-adverse rather
    than as `None`: "exactly as expected" is a fact, not an absence of one.

    `None` for a side this cannot reason about, which is the honest answer for a row whose
    `side` is neither `buy` nor `sell` -- nothing writes one today, and a report that guessed
    would be asserting a direction the row does not carry.
    """
    if difference is None:
        return None
    word = side.strip().lower()
    if word == "buy":
        return difference > 0
    if word == "sell":
        return difference < 0
    return None


def _row_from_dict(
    row: dict[str, Any],
    rule_names: Mapping[int, str] | None = None,
    *,
    repo: Repository | None = None,
    bracket_ids: frozenset[int] | None = None,
) -> OrderRow:
    """One repository dict, projected. Every judgement this report makes about a row is made
    here, once, so neither renderer has to make it twice.

    `rule_names` is `{rules.id: rules.kind}`, read ONCE by `gather_orders` and passed down. A
    lookup per row would be one query per order against a book capped at `MAX_ORDERS_LIMIT`, on
    a route with no proxy in front of it -- and would be invisible on any fixture small enough
    to read. Omitted (the default), every row reports its rule as unresolved rather than
    claiming there is none: a caller that did not supply the map has not established that.
    """
    mode = str(row.get("mode") or "")
    side = str(row.get("side") or "")
    confirmation = str(row.get("confirmation") or "")
    rule_id = None if row.get("rule_id") is None else int(row["rule_id"])
    rule_name = "" if rule_id is None or rule_names is None else rule_names.get(rule_id, "")
    if rule_name:
        rule_detail = ""
    elif rule_id is None:
        rule_detail = NO_RULE_DETAIL
    elif rule_names is None or rule_id in rule_names:
        # Either nobody looked, or the rule was found and carries no name. Both are "present, or
        # at least not shown to be absent" -- and neither supports the claim below.
        rule_detail = UNRESOLVED_RULE_DETAIL.format(rule_id=rule_id)
    else:
        rule_detail = MISSING_RULE_DETAIL.format(rule_id=rule_id)
    expected = row.get("expected_fill")
    actual = row.get("actual_fill")
    difference, _ = _divergence(expected, actual)
    venue_id = _venue_order_id(row.get("raw_response"))
    modelled_fee = mode == "paper"
    best_bid = row.get("submit_best_bid")
    best_ask = row.get("submit_best_ask")
    book_observed = best_bid is not None and best_ask is not None

    if venue_id:
        venue_detail = ""
    elif modelled_fee:
        venue_detail = PAPER_NO_VENUE_DETAIL
    else:
        venue_detail = LIVE_NO_VENUE_DETAIL

    if book_observed:
        book_detail = ""
    elif modelled_fee:
        book_detail = PAPER_NO_BOOK_DETAIL
    else:
        book_detail = LIVE_NO_BOOK_DETAIL

    order_id = int(row["id"])
    return OrderRow(
        id=order_id,
        cancel=(
            CancelDecision(order_id, "unknown", False, True)
            if repo is None
            else classify_cancel(repo, order_id, row=row, bracket_ids=bracket_ids)
        ),
        mode=mode,
        product_id=str(row.get("product_id") or ""),
        side=side,
        order_type=str(row.get("order_type") or ""),
        status=str(row.get("status") or ""),
        confirmation=confirmation,
        confirmation_detail=CONFIRMATION_MEANINGS.get(confirmation, UNRECORDED_CONFIRMATION_DETAIL),
        confirmation_is_autonomous=confirmation in AUTONOMOUS_CONFIRMATIONS,
        qty=row.get("qty"),
        filled_quantity=row.get("filled_quantity"),
        limit_price=row.get("limit_price"),
        expected_fill=expected,
        actual_fill=actual,
        fill_divergence=difference,
        fill_divergence_adverse=_adverse(side, difference),
        fee=row.get("fee"),
        fee_is_modelled=modelled_fee,
        fee_detail=MODELLED_FEE_DETAIL if modelled_fee else CHARGED_FEE_DETAIL,
        submit_best_bid=best_bid,
        submit_best_ask=best_ask,
        submit_book_observed=book_observed,
        submit_book_detail=book_detail,
        venue_order_id=venue_id,
        venue_order_id_detail=venue_detail,
        rule_name=rule_name,
        rule_name_detail=rule_detail,
        rule_id=None if row.get("rule_id") is None else int(row["rule_id"]),
        created_at=None if row.get("created_at") is None else int(row["created_at"]),
        updated_at=None if row.get("updated_at") is None else int(row["updated_at"]),
    )


def gather_orders(
    repo: Repository,
    *,
    now_ts: int,
    scope: str = DEFAULT_ORDERS_SCOPE,
    status: str = "",
    limit: int | None = DEFAULT_ORDERS_LIMIT,
) -> OrdersReport:
    """Every order in the book, newest first, scoped and capped.

    `repo.get_orders()` is still called with NO arguments: no `mode`, no `product_id`, no
    `status`. That is the load-bearing choice in this function, and the reason is about MODE --
    each deployment book holds exactly one, so any mode filter renders empty on the books that
    hold the other one, and a report that showed nothing while the book held fifteen rows would
    be a surface asserting a fact it had not established. The mode is carried per ROW instead.

    `status` (#700) narrows the report WITHOUT touching that call: the filter runs in the same
    pass as the scope, over rows the repository handed back whole, so `total_count` and
    `statuses` still describe the entire book and the empty states below can still tell "keel
    has never traded" from "nothing wears this status". Pushing it into the query would trade
    those for nothing -- the cap is applied after both narrowings either way.

    Filtering server-side rather than in the browser is the same argument `?sort=` makes: a
    client filtering a CAPPED page would be filtering the fifty rows it happened to receive and
    presenting the result as every rejected order.

    An unrecognised status is not refused. It is applied, matches nothing, and comes back with
    `empty_reason == "status"` naming what was asked for -- which shows a typo plainly, and
    cannot wrongly refuse a status some venue or some older row spells differently. `statuses`
    is the list a client should build tabs from.

    A row with no `created_at` cannot be placed in time, so it is never excluded by a scope:
    excluding it would hide a row on the strength of a timestamp that does not exist. It counts
    toward `scoped_count` and sorts by `id`, which is what it has.
    """
    resolved_scope = normalise_scope(scope)
    resolved_limit = normalise_limit(limit)
    resolved_status = (status or "").strip().lower()
    start_ts = scope_start_ts(resolved_scope, now_ts)

    raw = repo.get_orders()
    # ONE read of a small table, before the row loop -- see `_row_from_dict`'s note on why this
    # is not a per-row lookup. `kind` IS the rule's name here (`build_rule_track_record` reads
    # the same column under the same meaning); there is no separate name column to prefer.
    rule_names: dict[int, str] = {
        int(rule["id"]): str(rule.get("kind") or "")
        for rule in repo.get_rules()
        if rule.get("id") is not None
    }
    total = 0
    modes: set[str] = set()
    statuses: set[str] = set()
    scoped: list[dict[str, Any]] = []
    filtered: list[dict[str, Any]] = []
    for row in raw:
        total += 1
        modes.add(str(row.get("mode") or ""))
        # From every row in the BOOK, before either narrowing -- see `statuses`' own note.
        # LOWERCASED, like `resolved_status` below and for the same reason: the client marks the
        # active tab with `name === current`, so a raw `Filled` tab would send `?status=Filled`,
        # get `filled` echoed back, and highlight neither itself nor "all" -- while a book
        # holding both spellings would show two tabs returning identical rows. `api.py` declines
        # to refuse unknown statuses precisely because such spellings exist in real books, so
        # they have to be folded here rather than assumed away.
        statuses.add(str(row.get("status") or "").strip().lower())
        created = row.get("created_at")
        if start_ts is not None and created is not None and int(created) < start_ts:
            continue
        scoped.append(row)
        # Lowercased on BOTH sides. The stored word is lowercase today, but matching the
        # caller's casing against a raw column would make a hand-typed `?status=FILLED` fail
        # while the tab that sends `filled` works -- one control, two behaviours.
        if resolved_status and str(row.get("status") or "").strip().lower() != resolved_status:
            continue
        filtered.append(row)

    # Newest first, HERE. `get_orders` orders by `id` ascending; reversing in each renderer
    # would be the same decision made twice and the second copy is the one that drifts.
    filtered.reverse()
    # ONE query for every protective link on the page, not one per row (#707). `classify_cancel`
    # takes the set when it has one and falls back to the single lookup when it does not, so this
    # is the same rule applied efficiently rather than a second one.
    bracket_ids = repo.open_bracket_order_ids()
    shown = tuple(
        _row_from_dict(row, rule_names, repo=repo, bracket_ids=bracket_ids)
        for row in filtered[:resolved_limit]
    )

    # Ordered widest cause first: a book with nothing in it is not a scope problem, and a scope
    # that excluded everything is not the tab's doing. Reporting the narrowest true cause would
    # send a reader to change the filter when the book is simply empty.
    if total == 0:
        empty_reason = "book"
    elif not scoped:
        empty_reason = "scope"
    elif not filtered:
        empty_reason = "status"
    else:
        empty_reason = ""

    return OrdersReport(
        now_ts=now_ts,
        scope=resolved_scope,
        scope_start_ts=start_ts,
        limit=resolved_limit,
        status=resolved_status,
        total_count=total,
        scoped_count=len(scoped),
        filtered_count=len(filtered),
        shown_count=len(shown),
        modes=tuple(sorted(modes)),
        statuses=tuple(sorted(s for s in statuses if s)),
        empty_reason=empty_reason,
        rows=shown,
    )


# -- the terminal rendering ------------------------------------------------------------------


def _utc(ts: int | None) -> str:
    """An epoch second as `YYYY-MM-DD HH:MM:SSZ`, or `"--"`.

    UTC because every other timestamp this deployment prints is UTC, and a ledger with one
    row in local time is a ledger nobody can order by eye."""
    if ts is None:
        return "--"
    try:
        return datetime.datetime.fromtimestamp(ts, datetime.UTC).strftime("%Y-%m-%d %H:%M:%SZ")
    except OSError, OverflowError, ValueError:
        return "--"


def _figure(value: Decimal | None) -> str:
    """A money or quantity figure for the terminal, or `"--"`.

    `format(value, "f")` and nothing else: `str(Decimal("5E+1"))` is `"5E+1"`, and an exponent
    in a price column is the failure `payload._plain` exists to prevent on the wire. The
    terminal deserves the same guarantee -- an operator reading `5E+1` as five is the same
    mistake whichever front-end showed it to them.
    """
    if value is None:
        return "--"
    try:
        return format(value, "f")
    except ArithmeticError, InvalidOperation, TypeError, ValueError:
        return "--"


def render_orders(report: OrdersReport) -> list[str]:
    """`keel orders`'s rendering, as a list of lines -- a pure function of the report, so it is
    testable without a CliRunner and so `keel/web/payload.py` can be pinned against the same
    figures it places.

    THE LAYOUT IS THE ARGUMENT. `confirmation` leads each row, ahead of the product and the
    price, because on a deployment running `autonomy: ON` the first question about an order is
    not what it bought but whether anybody agreed to it. `expected` and `actual` sit adjacent
    with the difference between them on the same line, because that difference is realised
    slippage and a reader should not have to do the subtraction. The fee is the number as
    recorded, never a rate -- the whole reason the taker-percentage argument has any evidence
    behind it is that these are the charges as they landed.

    `raw_response` appears nowhere. The venue's order id does, when there is one, and a
    sentence saying why there is not, when there is not.
    """
    lines: list[str] = []
    lines.append(f"orders: scope={report.scope} limit={report.limit}")
    if report.scope_start_ts is not None:
        lines.append(f"  since: {_utc(report.scope_start_ts)}")
    lines.append(
        f"  showing {report.shown_count} of {report.scoped_count} in scope "
        f"({report.total_count} in this book)"
    )
    lines.append(f"  modes in this book: {', '.join(report.modes) if report.modes else 'none'}")
    lines.append("")

    if report.empty_reason == "book":
        # The two empty states are DIFFERENT FACTS and are never rendered as the same blank
        # table. "This book has never held an order" is a statement about the deployment;
        # "your window excluded them" is a statement about the question that was asked.
        lines.append("no orders in this book at all -- keel has placed none here.")
        return lines
    if report.empty_reason == "scope":
        lines.append(
            f"no orders in this window -- the book holds {report.total_count}, all of them "
            f"outside scope={report.scope}. Widen it (--scope all) to see them."
        )
        return lines
    if report.empty_reason == "status":
        # The fourth empty state needs the fourth sentence. Without it this renderer prints its
        # header, "showing 0 of N", and then nothing -- the silent blank the two branches above
        # exist to prevent, and the one a reader is most likely to read as "keel never traded".
        # Names the statuses the book DOES hold, because the next thing a reader wants is which
        # filter would have worked.
        held = ", ".join(report.statuses) if report.statuses else "none"
        lines.append(
            f"no orders with status={report.status} in this window -- the book holds "
            f"{report.total_count}, with statuses: {held}."
        )
        return lines

    for row in report.rows:
        placement = "AUTONOMOUS" if row.confirmation_is_autonomous else (row.confirmation or "--")
        lines.append(
            f"[{row.id}] {placement} | {row.mode} {row.side} {row.product_id} "
            f"{row.status} ({row.order_type or 'unknown type'})"
        )
        lines.append(f"      placement: {row.confirmation_detail}")
        lines.append(
            f"      qty={_figure(row.qty)} filled={_figure(row.filled_quantity)} "
            f"limit={_figure(row.limit_price)}"
        )
        if row.fill_divergence is None:
            divergence = "divergence --"
        else:
            verdict = (
                "adverse"
                if row.fill_divergence_adverse
                else ("in keel's favour" if row.fill_divergence_adverse is False else "unjudged")
            )
            divergence = f"divergence {_figure(row.fill_divergence)} ({verdict})"
        lines.append(
            f"      expected={_figure(row.expected_fill)} actual={_figure(row.actual_fill)} "
            f"{divergence}"
        )
        lines.append(f"      fee={_figure(row.fee)} -- {row.fee_detail}")
        if row.submit_book_observed:
            # The PAIR as the venue gave it. No spread is computed from it here or anywhere
            # else in this view -- see `OrderRow.submit_best_bid`.
            lines.append(
                f"      book at submit: bid={_figure(row.submit_best_bid)} "
                f"ask={_figure(row.submit_best_ask)}"
            )
        else:
            lines.append(f"      {row.submit_book_detail}")
        if row.venue_order_id:
            lines.append(f"      venue order id: {row.venue_order_id}")
        else:
            lines.append(f"      {row.venue_order_id_detail}")
        # The service resolves this for BOTH front-ends; rendering it only in the browser would
        # leave `keel orders` unable to answer a question the report already holds.
        if row.rule_name:
            lines.append(f"      rule: {row.rule_name}")
        else:
            lines.append(f"      {row.rule_name_detail}")
        rule = "--" if row.rule_id is None else str(row.rule_id)
        lines.append(
            f"      rule={rule} placed={_utc(row.created_at)} updated={_utc(row.updated_at)}"
        )
        lines.append("")

    return lines


# -- the command -----------------------------------------------------------------------------


# -- the cancel asymmetry (#707) -------------------------------------------------------------------
#
# Cancelling an open ENTRY is refusing risk. The constitution makes refusing risk frictionless, so
# it asks once and does it.
#
# Cancelling an open EXIT or a protective bracket is REMOVING PROTECTION -- the same class of
# action as disabling a stop -- and it takes the typed friction every capability-increasing step in
# this program takes. No venue draws this distinction; it falls out of keel's own rails, and the
# whole feature is getting the classification right in the cases where a row does not announce
# which kind it is.

#: The statuses a cancel can reach, READ from the executor rather than restated.
#:
#: Two copies would drift the day the engine learned a third resting state, and this surface would
#: then refuse to cancel something the engine still considers live. `partially_filled` is in the
#: list for the reason `_clear_resting_bracket` gives: its unfilled remainder is working at the
#: exchange exactly like a pending order's whole size.
CANCELLABLE_STATUSES = RESTING_STATUSES

#: What a cancel is, as a closed vocabulary.
#:
#: `protective` is separate from `exit` because they are refused for different reasons and an
#: operator should be told which: an `exit` is liquidating inventory, a `protective` leg is the
#: stop that stands under a live tranche.
CANCEL_KINDS: tuple[str, ...] = ("entry", "exit", "protective", "unknown")

#: The command that cancels one order. Composed in Python and placed by the client, so the console
#: cannot come to print a command that does not exist.
CANCEL_INVOCATION = "keel orders cancel {order_id}"


#: The badge every order page carries, and the sentence behind it (#707).
#:
#: Not an apology for a missing feature. `keel serve` holds no venue credential and no broker
#: handle, and that is the property that keeps the worst case of a bug in the HTTP layer at "reads
#: a local SQLite file" rather than "exfiltrates live trading keys". Unlocking the keychain and
#: signing a request to a venue happens during a terminal invocation the operator started, never
#: from an ambient loopback daemon.
WEB_READ_ONLY_BADGE = "read-only"
WEB_READ_ONLY_NOTE = (
    "The web console is read-only for security: it holds no venue credentials. "
    "State-changing actions run through the CLI."
)

#: What each kind of cancel MEANS, for the modal that hands over the command. The word is a term of
#: art; the sentence is what an operator decides on.
#: Checked at import: a kind with no headline or no note would render an empty modal, and a table
#: that has drifted from the vocabulary is exactly the thing nobody notices until a reader sees the
#: gap. This is what `CANCEL_KINDS` is FOR -- declared and never read, it was decoration.
CANCEL_HEADLINES: Mapping[str, str] = {
    "entry": "Entry order #{order_id} — resting",
    "exit": "⚠️ Exit order #{order_id} — live liquidation",
    "protective": "⚠️ Protective bracket #{order_id} — live protection",
    "unknown": "⚠️ Order #{order_id} — unclassified",
}

CANCEL_NOTES: Mapping[str, str] = {
    "entry": "Cancelling a resting entry refuses risk. Nothing is protecting anything here.",
    "exit": (
        "This order liquidates inventory you hold. Cancelling it leaves the position open with "
        "no exit working."
    ),
    "protective": (
        "This is live downside protection — a position relies on it for its stop. Cancelling it "
        "leaves that position with nothing beneath it, and the terminal will ask you to type a "
        "phrase before it does."
    ),
    "unknown": "keel could not classify this order, so it is treated as protection.",
}



# The vocabulary and its tables, checked here rather than hoped about. `CANCEL_KINDS` existed and
# nothing read it; now a kind added to the set without a headline or a note fails at import.
assert set(CANCEL_HEADLINES) == set(CANCEL_KINDS), "CANCEL_HEADLINES does not cover CANCEL_KINDS"
assert set(CANCEL_NOTES) == set(CANCEL_KINDS), "CANCEL_NOTES does not cover CANCEL_KINDS"

@dataclass(frozen=True)
class CancelDecision:
    """What this order is, and therefore what it takes to cancel it.

    ONE classification, read by both front-ends. The CLI turns `typed` into a phrase prompt and the
    console turns it into a 403; if each decided for itself, the console could one-click something
    the terminal makes you type.
    """

    order_id: int
    kind: str
    cancellable: bool
    #: Whether cancelling this needs the typed phrase rather than a `y/N`. True for everything but
    #: an entry -- including `unknown`, so a row this build cannot classify is never the easy case.
    typed: bool
    #: Why not, when `cancellable` is False. Always a sentence naming the order and its state: a
    #: cancel that silently does nothing is the worst answer here, because the operator then
    #: believes they have cancelled something still live at the venue.
    reason: str = ""
    #: Whether cancelling this must also clear the product's resting bracket.
    #:
    #: TRUE ONLY FOR A ZERO-FILLED ENTRY. `executor.execute` places the bracket as soon as the
    #: entry is PLACED rather than once it fills, so a resting entry can already have a protective
    #: leg -- and if nothing filled, that leg commits base inventory which was never acquired. It
    #: is an orphan and goes with the entry (the #519 protocol).
    #:
    #: A PARTIALLY FILLED entry is the opposite case and the issue's rule does not cover it: the
    #: operator holds real inventory, the bracket is what protects it, and clearing it "because we
    #: cancelled an entry" would strip a stop from a live tranche. That is the exit-side hazard
    #: reappearing inside an entry-side action, which is the thing this asymmetry exists to
    #: prevent. The remainder is cancelled; the protection stays.
    clears_bracket: bool = False
    product_id: str = ""
    #: The exact terminal command, composed HERE (Rule 2) so the console places it rather than
    #: building it. `""` when the order cannot be cancelled: handing an operator a command that
    #: would be refused is worse than handing them nothing -- they run it, it fails, and they
    #: learn the console does not know what it is looking at.
    invocation: str = ""
    #: The modal's heading, composed HERE because it is a judgement about what the reader is
    #: looking at -- "Entry order #42" and "Protective bracket #43 — live protection" are two
    #: different warnings, and choosing between them is Rule 2's territory. It also keeps the
    #: client from having to read `Field.value` to find the id, which `render.js` may not do.
    headline: str = ""


def classify_cancel(
    repo: Repository,
    order_id: int,
    *,
    row: dict[str, Any] | None = None,
    bracket_ids: frozenset[int] | None = None,
) -> CancelDecision:
    """What cancelling `order_id` would be, and what it therefore takes.

    Reads only. It decides nothing about whether the operator may proceed -- that is the caller's
    gate -- and it never touches a broker, which is why the web console can call it: `keel serve`
    holds no venue credential and no broker handle, and #707 settled that it never will.

    ONE function for both front-ends. The CLI turns `typed` into a phrase prompt; the console
    turns it into a modal that hands over `invocation`. If each classified for itself, the console
    could describe an order as a frictionless entry while the terminal demanded the phrase for it.

    `row` and `bracket_ids` are the BATCH form, for a caller classifying a whole page: the row is
    already in hand and `repo.open_bracket_order_ids()` answers every protective link in one query
    rather than one per order. Same rule either way -- the lookups below are what this function
    falls back to when a caller has neither, not a second implementation.
    """
    if row is None:
        row = repo.get_order(order_id)
    if row is None:
        return CancelDecision(
            order_id=order_id,
            kind="unknown",
            cancellable=False,
            typed=True,
            reason=f"no order {order_id} in this deployment's book",
        )

    product_id = str(row.get("product_id") or "")
    status = str(row.get("status") or "")
    # PROTECTIVE first, and the order of these two checks is the guard. A protective leg is the
    # real hazard rather than the word "sell": a row wearing the BUY side while a position points
    # at it as its protection would pass a side-only rule and be cancelled one-click, stripping a
    # stop from a live tranche.
    protective = (
        order_id in bracket_ids
        if bracket_ids is not None
        else repo.get_position_for_bracket(order_id) is not None
    )
    if protective:
        kind = "protective"
    elif str(row.get("side") or "").lower() == "sell":
        kind = "exit"
    else:
        kind = "entry"

    if status not in CANCELLABLE_STATUSES:
        return CancelDecision(
            order_id=order_id,
            kind=kind,
            cancellable=False,
            typed=kind != "entry",
            reason=(
                f"order {order_id} is {status}, and only a resting order can be cancelled "
                f"({', '.join(CANCELLABLE_STATUSES)})"
            ),
            product_id=product_id,
        )

    return CancelDecision(
        order_id=order_id,
        kind=kind,
        cancellable=True,
        typed=kind != "entry",
        clears_bracket=kind == "entry" and status == "pending",
        product_id=product_id,
        invocation=CANCEL_INVOCATION.format(order_id=order_id),
        headline=CANCEL_HEADLINES[kind].format(order_id=order_id),
    )


@click.group("orders", invoke_without_command=True)
@click.option(
    "--scope",
    type=click.Choice(ORDERS_SCOPES),
    default=DEFAULT_ORDERS_SCOPE,
    show_default=True,
    help="How far back to read: a UTC calendar window, or every order in the book.",
)
@click.option(
    "--limit",
    type=int,
    default=DEFAULT_ORDERS_LIMIT,
    show_default=True,
    help=f"How many rows to show, newest first (1-{MAX_ORDERS_LIMIT}).",
)
@click.pass_context
@with_disclaimer
def orders_cmd(ctx: click.Context, scope: str, limit: int) -> None:
    """What keel actually bought and sold, and at what price.

    THE LISTING is read-only; `cancel` below is not, and it is the only write in this group.

    `keel orders` still LISTS, with no subcommand and the same options it always took (#707 turned
    it into a group and `invoke_without_command=True` is what keeps that true). `keel orders list`
    is the same thing under its own name, and `keel orders cancel <id>` is the write.

    One row per order the engine placed, newest first, straight from the `orders` table: who
    placed it (a human at the terminal, or keel on its own), what was expected versus what the
    venue gave, the fee as charged, and the venue's own order id. No broker call and no write:
    the database is opened read-only, so this cannot migrate a schema or take a lock against a
    book the agent may be mid-cycle on.

    Rows from BOTH modes are shown and the mode is on the row. A deployment book holds one
    mode, so a filter here would render empty on the other books and read as "nothing traded".
    """
    if ctx.invoked_subcommand is not None:
        return
    _list_orders(ctx, scope=scope, limit=limit)


def _list_orders(ctx: click.Context, *, scope: str, limit: int) -> None:
    repo = _open_repo_ro(ctx)
    report = gather_orders(repo, now_ts=int(time.time()), scope=scope, limit=limit)
    for line in render_orders(report):
        click.echo(line)


@orders_cmd.command("list")
@click.option(
    "--scope",
    type=click.Choice(ORDERS_SCOPES),
    default=DEFAULT_ORDERS_SCOPE,
    show_default=True,
    help="How far back to read: a UTC calendar window, or every order in the book.",
)
@click.option(
    "--limit",
    type=int,
    default=DEFAULT_ORDERS_LIMIT,
    show_default=True,
    help=f"How many rows to show, newest first (1-{MAX_ORDERS_LIMIT}).",
)
@click.pass_context
def orders_list(ctx: click.Context, scope: str, limit: int) -> None:
    """The same listing `keel orders` prints, under its own name.

    NO `@with_disclaimer`: click runs the group callback first, and it carries one. Both would
    print it twice -- and on `--help`, print it above the usage text.
    """
    _list_orders(ctx, scope=scope, limit=limit)


#: What an operator types to cancel protection. Long enough that it cannot be muscle memory, and
#: it NAMES the order, so a phrase copied from one prompt cannot answer a different one.
CANCEL_EXIT_PHRASE = "remove protection from order {order_id}"


@orders_cmd.command("cancel")
@click.argument("order_id", type=int)
@click.pass_context
def orders_cancel(ctx: click.Context, order_id: int) -> None:
    """Cancel a resting order. Entries ask once; exits and protective legs are typed.

    THE ASYMMETRY. Cancelling an entry is refusing risk, and the constitution makes refusing risk
    frictionless -- so it asks `y/N` and does it. Cancelling an exit or a protective bracket is
    REMOVING PROTECTION, the same class of action as disabling a stop, and it takes the typed
    friction every capability-increasing step in this program takes.

    A protective leg is classified by the POSITION that points at it, not by its side label:
    `positions.bracket_order_id` is the link, and a row wearing the entry side while a tranche
    relies on it for protection would pass a side-only rule.

    Needs a terminal either way. The venue is reached only after the confirmation, and only the
    venue's own confirmation lets the local row be marked -- `executor._cancel_at_exchange` owns
    that rule and this calls it rather than restating it.
    """
    from keel.commands._common import _build_broker, _is_interactive, _load_cfg, _open_repo
    from keel.execution.executor import CancelPending, CancelUnavailable, _cancel_at_exchange

    if not _is_interactive():
        raise click.ClickException(
            f"refusing to cancel order {order_id}: this needs an interactive terminal."
        )

    repo = _open_repo(ctx)
    decision = classify_cancel(repo, order_id)
    if not decision.cancellable:
        # A NAMED refusal, never a silent success. An operator told "cancelled" about an order
        # still live at the venue is worse off than one told why it could not be.
        raise click.ClickException(decision.reason)

    click.echo(f"order {order_id}: {decision.kind} on {decision.product_id}")
    if decision.typed:
        phrase = CANCEL_EXIT_PHRASE.format(order_id=order_id)
        click.echo(
            "This is protection, not risk. Cancelling it leaves the position it stands under "
            "with nothing beneath it."
        )
        typed = click.prompt(f'Type "{phrase}" to confirm', default="", show_default=False)
        if typed.strip() != phrase:
            raise click.ClickException("aborted (phrase not typed).")
    elif not click.confirm("Cancel this resting entry?", default=False):
        raise click.ClickException("aborted.")

    now_ts = int(time.time())
    broker = _build_broker(_load_cfg(ctx))

    # RE-READ AND RE-CLASSIFY after the prompt. A typed phrase is 34 characters, and a resting
    # order can fill while it is being typed -- at which point `clears_bracket`, decided before the
    # prompt, would run an orphan sweep against inventory that now exists. Everything downstream
    # reads `fresh`, and a status change is a refusal rather than a proceed: the operator answered
    # a question about a different order than the one in front of them now.
    row = repo.get_order(order_id)
    if row is None:
        raise click.ClickException(f"order {order_id} vanished from the book while you answered.")
    fresh = classify_cancel(repo, order_id, row=row)
    if not fresh.cancellable:
        raise click.ClickException(
            f"not cancelling: {fresh.reason}. That changed while you were answering."
        )

    try:
        _cancel_at_exchange(broker, repo, row)
    except (CancelUnavailable, CancelPending) as exc:
        # A CLI failure, not a traceback. This is the LIKELY outcome of the window above -- the
        # order filled while the operator typed -- and "the exchange refused" is a sentence they
        # can act on where a `RuntimeError` is not. Nothing local was written: `_cancel_at_exchange`
        # marks no state on failure, which is its own first rule.
        raise click.ClickException(f"the venue did not cancel order {order_id}: {exc}") from exc

    # The fill BEFORE the terminal status, because `canceled` is terminal and `execution.reconcile`
    # states the rule this would otherwise break: "A CANCELLED/EXPIRED order can still have SOLD
    # something -- Coinbase reports `filled_size > 0` for an order that partly filled before being
    # cancelled." `_polled_rows` only revisits RESTING statuses, so a fill dropped here is dropped
    # for good -- and `CANCELLABLE_STATUSES` deliberately includes `partially_filled`, which is
    # exactly the row that carries one.
    _record_fill_before_cancel(broker, repo, row, now_ts)
    repo.update_order(order_id, status="canceled", updated_at=now_ts)
    click.echo(f"cancelled order {order_id} at the venue.")

    if fresh.clears_bracket:
        cleared, stranded = _clear_orphaned_brackets(broker, repo, fresh.product_id, now_ts)
        for cleared_id in cleared:
            click.echo(f"cleared orphaned bracket {cleared_id} on {fresh.product_id}.")
        if stranded:
            # LOUD. The entry is gone and a protective leg may still be working at the venue over
            # inventory that was never acquired, which is a state an operator has to know about
            # rather than discover from a fill.
            raise click.ClickException(
                f"order {order_id} was cancelled, but orphaned bracket(s) "
                f"{', '.join(str(one) for one in stranded)} on {fresh.product_id} could NOT be "
                "cleared -- they may still be live at the venue over inventory that was never "
                "acquired. Check the venue directly."
            )


def _record_fill_before_cancel(
    broker: Any, repo: Repository, row: dict[str, Any], now_ts: int
) -> None:
    """Book whatever the order filled before it is marked terminal.

    Reuses `execution.reconcile`'s own recorder rather than restating it: that module owns the
    rule that a cancelled order can still have filled, and owns what booking a fill means.

    WRAPPED, because this is the one step here that must not be able to strip the cancel it
    follows. The venue has already confirmed; failing to read back a fill is a reporting gap the
    next reconcile pass can close, while raising would leave the order live locally and cancelled
    at the exchange -- the disagreement this whole command exists to avoid.
    """
    from keel.execution import reconcile as reconcile_mod

    recorder = getattr(reconcile_mod, "_try_record_fill", None)
    if recorder is None:  # pragma: no cover - the seam is present in every shipped build
        return
    try:
        recorder(broker, repo, row, now_ts)
    except Exception:
        log_exception(logger, "orders.cancel_fill_readback_failed", order_id=row.get("id"))


def _clear_orphaned_brackets(
    broker: Any, repo: Repository, product_id: str, now_ts: int
) -> tuple[list[int], list[int]]:
    """Cancel the resting SELLs on `product_id` that NO OPEN TRANCHE relies on.

    `(cleared, stranded)`.

    **Deliberately NOT `executor._clear_resting_bracket`, and the difference nearly shipped as a
    one-`y` path to a naked position.** That function is product-wide: it cancels every resting
    SELL for the product, which is right where the executor calls it because the caller is about to
    place a REPLACEMENT sell over the same inventory. Here nothing replaces anything -- the entry
    is going away -- so cancelling every resting sell strips the stop from any other open tranche
    on that product, on the one path built to be frictionless. Cancelling that bracket directly
    demands the typed phrase; reaching it sideways through an entry must not be a shortcut past
    that.

    So the filter is `open_bracket_order_ids()`, the same set `classify_cancel` calls `protective`.
    What is left is a resting sell nothing depends on -- an order committing base inventory that no
    open position accounts for, which is what "orphan" means here.

    Failures are COLLECTED rather than raised, so one uncancellable orphan does not hide the others
    from the operator, and the caller reports them all at once.
    """
    from keel.execution.executor import CancelPending, CancelUnavailable, _cancel_at_exchange

    protected = repo.open_bracket_order_ids()
    rows: list[dict[str, Any]] = []
    for status in CANCELLABLE_STATUSES:
        rows.extend(repo.get_orders(mode="live", product_id=product_id, status=status))

    cleared: list[int] = []
    stranded: list[int] = []
    for row in sorted(rows, key=lambda one: int(one["id"])):
        order_id = int(row["id"])
        if str(row.get("side") or "").lower() != "sell" or order_id in protected:
            continue
        try:
            _cancel_at_exchange(broker, repo, row)
        except (CancelUnavailable, CancelPending):
            log_exception(logger, "orders.orphan_bracket_cancel_failed", order_id=order_id)
            stranded.append(order_id)
            continue
        repo.update_order(order_id, status="canceled", updated_at=now_ts)
        cleared.append(order_id)
    return cleared, stranded
