"""Pins for the orders ledger service (#659).

`keel/commands/orders.py` is the ONE place the `orders` table becomes a report, and both
front-ends read that report rather than the table. So the properties pinned here are the ones
that would otherwise have to be pinned twice and would drift once:

* the read is UNFILTERED -- no `mode` reaches `get_orders`, because each deployment book holds
  exactly one mode and a filter renders empty on the books that hold the other;
* the reversal happens in the SERVICE, not in a renderer;
* `raw_response` is projected down to one short scalar and is not a field of `OrderRow` at all;
* an empty report says WHICH empty it is.
"""

from __future__ import annotations

import json
from dataclasses import fields as dataclass_fields
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from keel.cli import cli
from keel.commands import orders as orders_service
from keel.commands.orders import (
    AUTONOMOUS_CONFIRMATIONS,
    CONFIRMATION_MEANINGS,
    DEFAULT_ORDERS_LIMIT,
    DEFAULT_ORDERS_SCOPE,
    MAX_ORDERS_LIMIT,
    MAX_VENUE_ORDER_ID_CHARS,
    ORDERS_SCOPES,
    OrdersReport,
    _venue_order_id,
    gather_orders,
    normalise_limit,
    normalise_scope,
    render_orders,
    scope_start_ts,
)
from keel.data.db import connect, migrate
from keel.data.repository import Repository

#: 2027-01-15 00:00:00 UTC, so a "today" boundary is a round number in every assertion below.
DAY = 86_400
NOW_TS = 1_800_000_000  # 2027-01-15T08:00:00Z
TODAY_START = 1_799_971_200  # 2027-01-15T00:00:00Z


def _repo(tmp_path: Path) -> Repository:
    conn = connect(str(tmp_path / "keel.db"))
    migrate(conn)
    return Repository(conn)


def _order(**overrides: Any) -> dict[str, Any]:
    """A filled live BUY, with every column the report reads populated."""
    row: dict[str, Any] = {
        "mode": "live",
        "product_id": "BTC-USD",
        "side": "buy",
        "order_type": "market",
        "qty": Decimal("0.01"),
        "limit_price": None,
        "status": "filled",
        "fee": Decimal("1.18"),
        "expected_fill": Decimal("100000"),
        "actual_fill": Decimal("100050"),
        "filled_quantity": Decimal("0.01"),
        "raw_response": json.dumps({"order_id": "cb-order-1"}),
        "submit_best_bid": None,
        "submit_best_ask": None,
        "confirmation": "autonomous",
        "rule_id": None,
        "created_at": NOW_TS - 3600,
        "updated_at": NOW_TS - 3600,
    }
    row.update(overrides)
    return row


# -- the read is unfiltered -----------------------------------------------------------------------


class _RecordingRepo:
    """A repository whose ONLY job is to remember how `get_orders` was called.

    A test that merely seeds two modes and finds both rows would also pass against an
    implementation that passed `mode=None` explicitly today and `mode="live"` tomorrow with a
    fixture that happened to be live. This records the arguments, so the pin is on the CALL."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def get_orders(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append((args, kwargs))
        return list(self.rows)


def test_gather_passes_no_filter_of_any_kind_to_get_orders() -> None:
    """THE pin the operator's scope note asked for.

    `get_orders(mode=...)` would render EMPTY on two of the three real deployment books, and an
    empty table reads as "keel has never traded" rather than "the filter excluded everything".
    Asserted on the call itself: no positional argument, no keyword, at all."""
    repo = _RecordingRepo([_order(id=1)])
    gather_orders(repo, now_ts=NOW_TS)  # type: ignore[arg-type]
    assert repo.calls == [((), {})], repo.calls


def test_both_modes_reach_the_report_and_the_mode_is_on_the_row(tmp_path: Path) -> None:
    """A paper fill and a live fill in one book both appear, each carrying its own mode -- so a
    reader never infers the mode from which window they happen to be looking at."""
    repo = _repo(tmp_path)
    repo.insert_order(_order(mode="live", confirmation="autonomous"))
    repo.insert_order(_order(mode="paper", confirmation="paper", raw_response=None))

    report = gather_orders(repo, now_ts=NOW_TS)

    assert [row.mode for row in report.rows] == ["paper", "live"]
    assert report.modes == ("live", "paper")


# -- newest first, reversed in the SERVICE --------------------------------------------------------


def test_rows_come_back_newest_first(tmp_path: Path) -> None:
    """The EXACT sequence, not "3 appears before 1".

    An implementation that sorted by `created_at` descending, or that reversed only the first
    page, or that happened to emit `[3, 1, 2]`, all satisfy a containment check. Only the whole
    list pins the order."""
    repo = _repo(tmp_path)
    for offset in (3 * DAY, 2 * DAY, DAY):
        repo.insert_order(_order(created_at=NOW_TS - offset, updated_at=NOW_TS - offset))

    report = gather_orders(repo, now_ts=NOW_TS)
    assert [row.id for row in report.rows] == [3, 2, 1]


def test_the_renderer_does_not_reorder_what_the_service_handed_it() -> None:
    """The other half of "reverse in the service, not the renderer".

    `test_rows_come_back_newest_first` passes against an implementation that leaves
    `gather_orders` oldest-first and reverses inside `render_orders`... no, it does not -- but
    it also does not FORBID a renderer that reverses again. Two reversals would restore the
    order the table has and the report's own list would be right while the screen was wrong, so
    this feeds the renderer a report whose rows are in a known order and reads the ids back off
    the lines."""
    report = _report_with(
        rows=(
            _row(id=7, created_at=NOW_TS - DAY),
            _row(id=3, created_at=NOW_TS - 2 * DAY),
        )
    )
    ids = [
        int(line.split("]")[0].lstrip("["))
        for line in render_orders(report)
        if line.startswith("[")
    ]
    assert ids == [7, 3]


def test_the_limit_keeps_the_newest_rows_not_the_oldest(tmp_path: Path) -> None:
    """The slice is applied AFTER the reversal. Applied before it, `--limit 2` on a book of
    three would answer the two oldest orders while claiming to be newest-first -- the exact
    shape of a page that is wrong in a way nobody notices."""
    repo = _repo(tmp_path)
    for offset in (3 * DAY, 2 * DAY, DAY):
        repo.insert_order(_order(created_at=NOW_TS - offset, updated_at=NOW_TS - offset))

    report = gather_orders(repo, now_ts=NOW_TS, limit=2)
    assert [row.id for row in report.rows] == [3, 2]
    assert (report.total_count, report.scoped_count, report.shown_count) == (3, 3, 2)


# -- raw_response -------------------------------------------------------------------------------


def test_the_row_type_has_no_raw_response_field() -> None:
    """Not "the renderer happens not to print it": the field does not exist, so no front-end can
    render it by accident and no future one can be taught to."""
    names = {f.name for f in dataclass_fields(orders_service.OrderRow)}
    assert "raw_response" not in names


def test_no_part_of_a_hostile_raw_response_reaches_a_rendered_line(tmp_path: Path) -> None:
    """The venue's JSON is unbounded and is the one column nothing in keel constrains. A blob
    carrying markup, a second key and a long string must contribute exactly one thing to the
    output -- the order id -- and nothing else."""
    blob = json.dumps(
        {
            "order_id": "cb-42",
            "note": "<script>alert(1)</script>",
            "filled": "x" * 5000,
            "nested": {"secret": "do-not-render-me"},
        }
    )
    repo = _repo(tmp_path)
    repo.insert_order(_order(raw_response=blob))

    text = "\n".join(render_orders(gather_orders(repo, now_ts=NOW_TS)))
    assert "cb-42" in text
    for leak in ("<script>", "alert(1)", "do-not-render-me", "x" * 200, "nested", "secret"):
        assert leak not in text, leak


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (json.dumps({"order_id": "cb-1"}), "cb-1"),
        (json.dumps({"order_id": 12345}), "12345"),
        (json.dumps({"order_id": "  padded  "}), "padded"),
        (None, ""),
        ("", ""),
        ("not json at all", ""),
        (json.dumps(["cb-1"]), ""),  # a bare list is not an object
        (json.dumps("cb-1"), ""),  # a bare string is not an object
        (json.dumps({"id": "cb-1"}), ""),  # the wrong key is not a fallback
        (json.dumps({"order_id": {"id": "cb-1"}}), ""),  # a structure is not an identifier
        (json.dumps({"order_id": ["cb-1"]}), ""),
        (json.dumps({"order_id": True}), ""),  # a bool is not an id, and str(True) is "True"
        (json.dumps({"order_id": None}), ""),
        (json.dumps({"order_id": "z" * (MAX_VENUE_ORDER_ID_CHARS + 1)}), ""),
    ],
)
def test_the_venue_order_id_reader_accepts_only_a_short_scalar(raw: Any, expected: str) -> None:
    assert _venue_order_id(raw) == expected


def test_an_id_exactly_at_the_cap_is_kept() -> None:
    """The boundary, in the direction that would silently drop a real id if it were `>=`."""
    ident = "z" * MAX_VENUE_ORDER_ID_CHARS
    assert _venue_order_id(json.dumps({"order_id": ident})) == ident


def test_a_paper_row_says_why_it_has_no_venue_id_rather_than_showing_a_blank(
    tmp_path: Path,
) -> None:
    """`PaperTrader` writes a `raw_response` with no `order_id` in it at all (it stores the
    rule name and the bracket). A blank cell there reads as missing data; the sentence reads as
    the answer."""
    repo = _repo(tmp_path)
    repo.insert_order(
        _order(mode="paper", confirmation="paper", raw_response=json.dumps({"role": "entry"}))
    )
    row = gather_orders(repo, now_ts=NOW_TS).rows[0]
    assert row.venue_order_id == ""
    assert row.venue_order_id_detail == orders_service.PAPER_NO_VENUE_DETAIL
    assert "paper trader" in "\n".join(render_orders(gather_orders(repo, now_ts=NOW_TS)))


# -- confirmation ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("word", "autonomous"),
    [
        ("autonomous", True),
        ("bypass", True),  # the pre-2026-07-21 spelling of the SAME fact
        ("confirm", False),
        ("paper", False),
    ],
)
def test_every_confirmation_word_the_engine_writes_is_explained(
    tmp_path: Path, word: str, autonomous: bool
) -> None:
    """All four words reach this column from the three write sites in the tree
    (`executor._order_row`'s `confirmation=mode` for two of them, its own note for `bypass`,
    `strategy.paper` for `paper`). A view that explained two would leave a reader of an old live
    book guessing about the row that matters most."""
    repo = _repo(tmp_path)
    repo.insert_order(_order(confirmation=word))
    row = gather_orders(repo, now_ts=NOW_TS).rows[0]
    assert row.confirmation == word
    assert row.confirmation_detail == CONFIRMATION_MEANINGS[word]
    assert row.confirmation_is_autonomous is autonomous


def test_a_missing_confirmation_says_it_is_unrecorded(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    repo.insert_order(_order(confirmation=None))
    row = gather_orders(repo, now_ts=NOW_TS).rows[0]
    assert row.confirmation == ""
    assert row.confirmation_detail == orders_service.UNRECORDED_CONFIRMATION_DETAIL
    assert row.confirmation_is_autonomous is False


def test_bypass_is_treated_as_autonomous_and_not_merely_labelled() -> None:
    """The membership set, read directly: a table that spelled the prose right while leaving
    `bypass` out of the autonomous set would answer "did I approve this" WRONGLY on every row an
    old live book holds."""
    assert AUTONOMOUS_CONFIRMATIONS == frozenset({"autonomous", "bypass"})


def test_the_placement_leads_the_row_ahead_of_the_product_and_the_price(tmp_path: Path) -> None:
    """Prominence, asserted as position rather than as presence. On an `autonomy: ON`
    deployment the first question about an order is who placed it, so `AUTONOMOUS` must appear
    on the row's FIRST line and before the product it bought."""
    repo = _repo(tmp_path)
    repo.insert_order(_order(confirmation="autonomous"))
    headline = next(
        line for line in render_orders(gather_orders(repo, now_ts=NOW_TS)) if line.startswith("[")
    )
    assert "AUTONOMOUS" in headline
    assert headline.index("AUTONOMOUS") < headline.index("BTC-USD")


# -- expected vs actual, and the divergence -------------------------------------------------------


@pytest.mark.parametrize(
    ("side", "expected", "actual", "difference", "adverse"),
    [
        # A BUY that filled ABOVE its expected price paid more than the engine sized against.
        ("buy", "100000", "100050", "50", True),
        ("buy", "100000", "99950", "-50", False),
        # A SELL that filled BELOW its expected price received less.
        ("sell", "100000", "99950", "-50", True),
        ("sell", "100000", "100050", "50", False),
        # Exactly as expected is a FACT, not an absence of one.
        ("buy", "100000", "100000", "0", False),
        ("sell", "100000", "100000", "0", False),
    ],
)
def test_the_divergence_verdict_is_side_aware(
    tmp_path: Path, side: str, expected: str, actual: str, difference: str, adverse: bool
) -> None:
    """The whole reason the verdict is computed in Python. The SIGN is not the verdict: -50 is
    good news on a BUY and bad news on a SELL, and a front-end colouring the minus sign red
    would be wrong on half these rows -- which is also why Rule 3 forbids it from doing the
    subtraction at all."""
    repo = _repo(tmp_path)
    repo.insert_order(
        _order(side=side, expected_fill=Decimal(expected), actual_fill=Decimal(actual))
    )
    row = gather_orders(repo, now_ts=NOW_TS).rows[0]
    assert row.fill_divergence == Decimal(difference)
    assert row.fill_divergence_adverse is adverse


@pytest.mark.parametrize(
    ("expected", "actual"),
    [(None, Decimal("100")), (Decimal("100"), None), (None, None)],
)
def test_a_missing_side_of_the_divergence_yields_no_divergence_and_no_verdict(
    tmp_path: Path, expected: Decimal | None, actual: Decimal | None
) -> None:
    """A pending order has no `actual_fill`. Subtracting from nothing must not produce a zero
    that reads as "filled exactly as expected"."""
    repo = _repo(tmp_path)
    repo.insert_order(_order(status="pending", expected_fill=expected, actual_fill=actual))
    row = gather_orders(repo, now_ts=NOW_TS).rows[0]
    assert row.fill_divergence is None
    assert row.fill_divergence_adverse is None


def test_an_unreasonable_price_loses_its_divergence_and_keeps_everything_else(
    tmp_path: Path,
) -> None:
    """`Decimal.is_finite()` admits extreme exponents, and arithmetic on them raises. A report
    that propagated that would take the whole ledger down because one historical row held a
    nonsense price."""
    repo = _repo(tmp_path)
    repo.insert_order(
        _order(expected_fill=Decimal("1E+999999999"), actual_fill=Decimal("-1E+999999999"))
    )
    row = gather_orders(repo, now_ts=NOW_TS).rows[0]
    assert row.fill_divergence is None
    assert row.product_id == "BTC-USD"
    assert row.confirmation == "autonomous"


def test_expected_and_actual_are_rendered_on_one_line_with_the_difference(
    tmp_path: Path,
) -> None:
    """ "Side by side" is the requirement, and it is a layout fact: the two figures and the
    difference between them share a line, so nobody has to hold one in their head while they
    look for the other."""
    repo = _repo(tmp_path)
    repo.insert_order(_order(expected_fill=Decimal("100000"), actual_fill=Decimal("100050")))
    line = next(ln for ln in render_orders(gather_orders(repo, now_ts=NOW_TS)) if "expected=" in ln)
    assert "expected=100000" in line
    assert "actual=100050" in line
    assert "divergence 50" in line
    assert "adverse" in line


# -- the fee --------------------------------------------------------------------------------------


def test_the_fee_is_the_recorded_figure_and_never_a_rate(tmp_path: Path) -> None:
    """These rows are the only direct evidence about what trading actually costs. A percentage
    computed for display would be the report inventing a figure it does not hold -- and would be
    the first thing a reader mistook for a measurement."""
    repo = _repo(tmp_path)
    repo.insert_order(_order(fee=Decimal("11.838"), actual_fill=Decimal("100000")))
    lines = render_orders(gather_orders(repo, now_ts=NOW_TS))
    fee_line = next(ln for ln in lines if "fee=" in ln)
    assert "fee=11.838" in fee_line
    assert "%" not in "\n".join(lines)


def test_a_paper_fee_says_it_is_modelled_and_a_live_fee_says_it_is_charged(
    tmp_path: Path,
) -> None:
    """`PaperTrader` computes `fee = fill * qty * fee_pct` from the configured rate, so reading
    a paper fee back as evidence about that rate is circular. The row says so rather than
    leaving the reader to know it."""
    repo = _repo(tmp_path)
    repo.insert_order(_order(mode="live"))
    repo.insert_order(_order(mode="paper", confirmation="paper"))
    newest, oldest = gather_orders(repo, now_ts=NOW_TS).rows
    assert (newest.mode, newest.fee_is_modelled) == ("paper", True)
    assert newest.fee_detail == orders_service.MODELLED_FEE_DETAIL
    assert (oldest.mode, oldest.fee_is_modelled) == ("live", False)
    assert oldest.fee_detail == orders_service.CHARGED_FEE_DETAIL


def test_no_figure_is_ever_rendered_in_exponent_form(tmp_path: Path) -> None:
    """`str(Decimal("5E+1"))` is `"5E+1"`. Fifty read as five is the same mistake in a terminal
    as it is on the wire, and `payload._plain` guarantees only the wire."""
    repo = _repo(tmp_path)
    repo.insert_order(_order(fee=Decimal("5E+1"), qty=Decimal("1E-3")))
    text = "\n".join(render_orders(gather_orders(repo, now_ts=NOW_TS)))
    assert "fee=50" in text
    assert "qty=0.001" in text
    assert "E+" not in text
    assert "E-" not in text


# -- the venue's book at submit (#626) ------------------------------------------------------------


def test_the_book_at_submit_is_shown_as_the_pair_and_never_as_a_spread(tmp_path: Path) -> None:
    """#626 stored `submit_best_bid` and `submit_best_ask` as TWO columns rather than one delta,
    and said why: half-spread-from-mid, half-spread-from-the-side-crossed and relative spread are
    three different questions off one pair, and a derivation cannot be re-derived differently
    later. A view that computed one of them would answer one of those questions on the reader's
    behalf -- the same mistake as showing a fee as a rate."""
    repo = _repo(tmp_path)
    repo.insert_order(
        _order(submit_best_bid=Decimal("99999.5"), submit_best_ask=Decimal("100000.5"))
    )
    row = gather_orders(repo, now_ts=NOW_TS).rows[0]
    assert row.submit_best_bid == Decimal("99999.5")
    assert row.submit_best_ask == Decimal("100000.5")
    assert row.submit_book_observed is True
    assert row.submit_book_detail == ""

    text = "\n".join(render_orders(gather_orders(repo, now_ts=NOW_TS)))
    assert "bid=99999.5" in text
    assert "ask=100000.5" in text
    # The delta is 1.0 and the relative spread is 0.001%. Neither appears, and no field of
    # `OrderRow` holds one.
    assert "spread" not in text.lower()
    assert not any("spread" in f.name for f in dataclass_fields(orders_service.OrderRow)), sorted(
        f.name for f in dataclass_fields(orders_service.OrderRow)
    )


@pytest.mark.parametrize(
    ("bid", "ask"),
    [(None, None), (Decimal("1"), None), (None, Decimal("2"))],
)
def test_a_half_observed_book_is_not_a_book(
    tmp_path: Path, bid: Decimal | None, ask: Decimal | None
) -> None:
    """NULL means NOT OBSERVED, never zero (#626's schema note). One side alone cannot evidence
    anything about spread, so it is reported as absent rather than as a book with a hole in it."""
    repo = _repo(tmp_path)
    repo.insert_order(_order(submit_best_bid=bid, submit_best_ask=ask))
    row = gather_orders(repo, now_ts=NOW_TS).rows[0]
    assert row.submit_book_observed is False
    assert row.submit_book_detail == orders_service.LIVE_NO_BOOK_DETAIL


def test_a_paper_row_says_its_missing_book_is_by_design(tmp_path: Path) -> None:
    """A paper row has no book because `PaperTrader` fills against no venue, and #626 says a
    fabricated book sharing a column name with a real one would poison the measurement the
    columns exist for. That is a different fact from a live preview that carried nothing
    readable, and the two must not render as the same blank."""
    repo = _repo(tmp_path)
    repo.insert_order(_order(mode="paper", confirmation="paper"))
    row = gather_orders(repo, now_ts=NOW_TS).rows[0]
    assert row.submit_book_detail == orders_service.PAPER_NO_BOOK_DETAIL
    assert row.submit_book_detail != orders_service.LIVE_NO_BOOK_DETAIL
    assert "against no venue" in "\n".join(render_orders(gather_orders(repo, now_ts=NOW_TS)))


# -- scope, and the two empties -------------------------------------------------------------------


def test_the_scope_vocabulary_matches_activitys_word_for_word() -> None:
    """Two views over one deployment answering "how far back" with different words is a thing
    an operator would have to hold in their head for no reason."""
    from keel.commands.activity import ACTIVITY_SCOPES

    assert ORDERS_SCOPES == ACTIVITY_SCOPES


def test_the_default_scope_is_all_unlike_activitys() -> None:
    """Orders are placed rarely -- four rows on the live book over months. A `"today"` default
    would render empty on every real deployment and read as "keel has never traded"."""
    assert DEFAULT_ORDERS_SCOPE == "all"
    assert scope_start_ts("all", NOW_TS) is None


def test_today_is_a_utc_calendar_boundary() -> None:
    assert scope_start_ts("today", NOW_TS) == TODAY_START
    assert scope_start_ts("7d", NOW_TS) == TODAY_START - 6 * DAY


def test_an_unrecognised_scope_collapses_to_the_default_rather_than_filtering_to_nothing() -> None:
    assert normalise_scope("last-tuesday") == DEFAULT_ORDERS_SCOPE
    assert normalise_scope("") == DEFAULT_ORDERS_SCOPE
    assert normalise_scope("7d") == "7d"


@pytest.mark.parametrize(
    ("given", "resolved"),
    [
        (None, DEFAULT_ORDERS_LIMIT),
        (0, 1),
        (-5, 1),
        (7, 7),
        (MAX_ORDERS_LIMIT + 1, MAX_ORDERS_LIMIT),
    ],
)
def test_the_limit_is_clamped_at_both_ends(given: int | None, resolved: int) -> None:
    """A zero or a negative would reach the slice as a bound and answer an EMPTY report that
    looks like an empty book -- the very confusion `empty_reason` exists to prevent, arriving
    through the one door that bypasses it."""
    assert normalise_limit(given) == resolved


def test_a_scope_excludes_older_rows_and_the_report_says_so(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    repo.insert_order(_order(created_at=NOW_TS - 30 * DAY, updated_at=NOW_TS - 30 * DAY))
    repo.insert_order(_order(created_at=NOW_TS - 600, updated_at=NOW_TS - 600))

    report = gather_orders(repo, now_ts=NOW_TS, scope="today")
    assert [row.id for row in report.rows] == [2]
    assert (report.total_count, report.scoped_count, report.shown_count) == (2, 1, 1)
    assert report.empty_reason == ""


def test_a_row_with_no_created_at_is_never_excluded_by_a_scope(tmp_path: Path) -> None:
    """A row that cannot be placed in time must not be hidden ON THE STRENGTH of a timestamp
    that does not exist. It is still an order keel placed."""
    repo = _repo(tmp_path)
    repo.insert_order(_order(created_at=None, updated_at=None))
    report = gather_orders(repo, now_ts=NOW_TS, scope="today")
    assert [row.id for row in report.rows] == [1]
    assert report.rows[0].created_at is None


def test_an_empty_book_and_an_empty_window_are_different_facts(tmp_path: Path) -> None:
    """THE pin the operator's note asked for. An empty table that could mean either is a
    surface asserting something it has not established."""
    repo = _repo(tmp_path)

    empty_book = gather_orders(repo, now_ts=NOW_TS, scope="today")
    assert empty_book.empty_reason == "book"
    assert empty_book.total_count == 0
    book_text = "\n".join(render_orders(empty_book))
    assert "no orders in this book at all" in book_text

    repo.insert_order(_order(created_at=NOW_TS - 30 * DAY, updated_at=NOW_TS - 30 * DAY))
    empty_window = gather_orders(repo, now_ts=NOW_TS, scope="today")
    assert empty_window.empty_reason == "scope"
    assert (empty_window.total_count, empty_window.scoped_count) == (1, 0)
    window_text = "\n".join(render_orders(empty_window))
    assert "no orders in this window" in window_text
    assert "the book holds 1" in window_text
    # And the two prose answers must not be interchangeable.
    assert "no orders in this book at all" not in window_text


def test_the_counts_are_held_on_the_report_not_measured_downstream(tmp_path: Path) -> None:
    """Rule 6e of `test_console_thinness.py` bans `len()` in the serialiser precisely so a count
    on the wire is one the report already carries. That is only possible if the report carries
    it."""
    repo = _repo(tmp_path)
    for _ in range(3):
        repo.insert_order(_order())
    report = gather_orders(repo, now_ts=NOW_TS, limit=2)
    assert report.shown_count == len(report.rows) == 2
    assert report.scoped_count == 3
    assert report.total_count == 3


# -- the command ----------------------------------------------------------------------------------


def test_the_command_lists_orders_from_both_modes(tmp_path: Path) -> None:
    db = tmp_path / "keel.db"
    repo = _repo(tmp_path)
    repo.insert_order(_order(mode="live", confirmation="autonomous"))
    repo.insert_order(_order(mode="paper", confirmation="paper", raw_response=None))

    result = CliRunner().invoke(cli, ["--db", str(db), "orders"])
    assert result.exit_code == 0, result.output
    assert "AUTONOMOUS" in result.output
    assert "live buy BTC-USD" in result.output
    assert "paper buy BTC-USD" in result.output


def test_the_command_refuses_a_database_that_does_not_exist(tmp_path: Path) -> None:
    """`_open_repo_ro`'s refusal, reached through this command: a read-only view must not
    CREATE the book it was asked to read. A typo in `--db` would otherwise be this surface's
    first write."""
    result = CliRunner().invoke(cli, ["--db", str(tmp_path / "nope.db"), "orders"])
    assert result.exit_code != 0
    assert "will not create one" in result.output
    assert not (tmp_path / "nope.db").exists()


def test_the_command_opens_the_book_read_only(tmp_path: Path, monkeypatch) -> None:
    """Not "it happens not to write": the connection is opened through the `mode=ro` seam, so a
    write raises. Proven by attempting one on the very repository the command used."""
    db = tmp_path / "keel.db"
    _repo(tmp_path).insert_order(_order())
    seen: list[Repository] = []
    real = orders_service._open_repo_ro
    monkeypatch.setattr(
        orders_service, "_open_repo_ro", lambda ctx: seen.append(real(ctx)) or seen[-1]
    )

    result = CliRunner().invoke(cli, ["--db", str(db), "orders"])
    assert result.exit_code == 0, result.output
    with pytest.raises(Exception, match="readonly"):
        seen[0].insert_order(_order())


def test_the_command_renders_the_same_figures_the_service_report_holds(tmp_path: Path) -> None:
    """The command is a renderer over `gather_orders`, not a second read."""
    db = tmp_path / "keel.db"
    repo = _repo(tmp_path)
    repo.insert_order(_order())
    result = CliRunner().invoke(cli, ["--db", str(db), "orders", "--scope", "all"])
    assert result.exit_code == 0, result.output
    for line in render_orders(gather_orders(repo, now_ts=NOW_TS)):
        if line:
            assert line in result.output, line


# -- helpers for the renderer-only tests ----------------------------------------------------------


def _row(**overrides: Any) -> Any:
    row = _order(**{k: v for k, v in overrides.items() if k != "id"})
    row["id"] = overrides.get("id", 1)
    return orders_service._row_from_dict(row)


def _report_with(**overrides: Any) -> OrdersReport:
    rows = overrides.pop("rows", ())
    base: dict[str, Any] = {
        "now_ts": NOW_TS,
        "scope": "all",
        "scope_start_ts": None,
        "limit": DEFAULT_ORDERS_LIMIT,
        "total_count": len(rows),
        "scoped_count": len(rows),
        "shown_count": len(rows),
        "modes": ("live",),
        "empty_reason": "",
        "rows": rows,
    }
    base.update(overrides)
    return OrdersReport(**base)
