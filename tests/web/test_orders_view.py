"""The orders view, on the wire and in the client (#659).

The service (`keel/commands/orders.py`) decides everything; `tests/commands/test_orders.py` pins
those decisions. What is pinned HERE is that the browser gets them unaltered and that the two
front-ends cannot come to disagree:

* the payload PLACES values and derives none -- no rate, no count it measured itself, no
  `raw_response`;
* `/api/orders` reaches the service with no `mode` filter and no way for a caller to add one;
* the client's route table, nav and renderer all know the view exists, in step with Python's.

**What no test in this file covers**: there is no JavaScript runtime in this suite. Every
assertion about `render.js` and `main.js` is over the SOURCE TEXT -- that the function is
exported, that the columns are declared in a given order, that no forbidden call appears. That
`ordersView` produces the DOM those declarations describe, that the disclosure opens, and that
the scope buttons dispatch, are properties of a real browser and are NOT established here.
"""

from __future__ import annotations

import http.client
import inspect
import json
import re
import threading
import time
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from keel.commands.orders import OrdersReport, gather_orders, render_orders
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.web import api as web_api
from keel.web import payload as web_payload
from keel.web import server as web_server
from keel.web import staticfiles
from keel.web.security import SESSION_COOKIE, new_session_token
from tests.conftest import VALID_CONFIG_YAML

NOW_TS = 1_800_000_000
DAY = 86_400
_STATIC = Path(staticfiles.__file__).parent / "static"


def _source(name: str) -> str:
    return (_STATIC / "js" / name).read_text(encoding="utf-8")


def _code(name: str) -> str:
    """`name`'s source with comments gone and string literals intact.

    `test_client_assets.py::_comments_only`, reused rather than re-walked: the comments in
    `ordersView` DESCRIBE `raw_response` and the arithmetic this file forbids, at length and on
    purpose, so a raw substring search would find the prose instead of the code -- the same trap
    `_markup_only` exists for on `index.html`."""
    from tests.web.test_client_assets import _comments_only

    return _comments_only(_source(name))


def _order(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "mode": "live",
        "product_id": "BTC-USD",
        "side": "buy",
        "order_type": "market",
        "qty": Decimal("0.01"),
        "limit_price": None,
        "status": "filled",
        "fee": Decimal("11.838"),
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


def _report(tmp_path: Path, *rows: dict[str, Any], **kwargs: Any) -> OrdersReport:
    conn = connect(str(tmp_path / "report.db"))
    migrate(conn)
    repo = Repository(conn)
    for row in rows:
        repo.insert_order(row)
    return gather_orders(repo, now_ts=NOW_TS, **kwargs)


# -- the payload places, and derives nothing ------------------------------------------------------


def _walk(node: Any, path: str = "$") -> list[tuple[str, Any]]:
    """Every leaf in a parsed JSON document, with the path that reaches it. Written here rather
    than imported so the guard cannot be weakened by a change to production code."""
    if isinstance(node, dict):
        out: list[tuple[str, Any]] = []
        for key, value in node.items():
            out.extend(_walk(value, f"{path}.{key}"))
        return out
    if isinstance(node, list):
        out = []
        for index, value in enumerate(node):
            out.extend(_walk(value, f"{path}[{index}]"))
        return out
    return [(path, node)]


def test_no_wire_value_in_the_orders_payload_is_ever_a_json_number(tmp_path: Path) -> None:
    """#533's contract, over this payload. `bool` is excluded first and deliberately: Python's
    `bool` subclasses `int`, so an `isinstance(x, int)` alone would flag every JSON `true`."""
    document = json.loads(json.dumps(web_payload.orders_payload(_report(tmp_path, _order()))))
    numbers = [
        path
        for path, leaf in _walk(document)
        if not isinstance(leaf, bool) and isinstance(leaf, (int, float))
    ]
    assert numbers == [], numbers


def test_the_orders_payload_never_states_a_fee_rate(tmp_path: Path) -> None:
    """These rows are the only direct evidence about what trading costs. A percentage computed
    for display would be a figure the report does not hold, and would be the first thing a reader
    mistook for a measurement -- so no `%` and no derived ratio crosses at all."""
    document = web_payload.orders_payload(
        _report(tmp_path, _order(fee=Decimal("11.838"), actual_fill=Decimal("100000")))
    )
    text = json.dumps(document)
    assert "%" not in text
    row = document["rows"][0]
    assert row["fee"]["value"] == "11.838"
    assert not any("rate" in key or "pct" in key for key in row), sorted(row)


def test_the_payload_carries_no_raw_response_and_leaks_nothing_from_one(tmp_path: Path) -> None:
    """Unbounded venue JSON is the one column that could carry something unexpected into a page.
    The service already reduced it to a short scalar; this asserts the reduction survives
    serialisation, over a blob designed to be noticed if any of it crossed."""
    blob = json.dumps(
        {
            "order_id": "cb-42",
            "note": "<script>alert(1)</script>",
            "filler": "q" * 4000,
            "nested": {"secret": "do-not-render-me"},
        }
    )
    text = json.dumps(web_payload.orders_payload(_report(tmp_path, _order(raw_response=blob))))
    assert "cb-42" in text
    for leak in ("raw_response", "<script>", "alert(1)", "do-not-render-me", "q" * 200):
        assert leak not in text, leak


def test_the_counts_on_the_wire_are_the_ones_the_report_holds(tmp_path: Path) -> None:
    """Rule 6e bans `len()` in `payload.py` so a count on the wire is one the report already
    carries. This is the other half: that the three counts cross and match."""
    report = _report(tmp_path, _order(), _order(), _order(), limit=2)
    document = web_payload.orders_payload(report)
    assert document["shown_count"]["value"] == str(report.shown_count) == "2"
    assert document["scoped_count"]["value"] == str(report.scoped_count) == "3"
    assert document["total_count"]["value"] == str(report.total_count) == "3"


def test_the_placement_is_the_first_key_on_a_row(tmp_path: Path) -> None:
    """Prominence, as a property of the document rather than of a stylesheet. The client renders
    a row's keys in the order the columns declare, and this is the fact the columns rest on: on
    an `autonomy: ON` deployment the first thing about an order is who placed it."""
    row = web_payload.orders_payload(_report(tmp_path, _order()))["rows"][0]
    assert list(row)[0] == "placement"
    assert row["placement"]["display"] == "AUTONOMOUS"
    assert row["placement"]["state"] == "warn"


@pytest.mark.parametrize(
    ("side", "actual", "expected_state", "expected_display"),
    [
        ("buy", "100050", "bad", "against keel"),
        ("buy", "99950", "good", "in keel's favour"),
        ("sell", "99950", "bad", "against keel"),
        ("sell", "100050", "good", "in keel's favour"),
    ],
)
def test_the_divergence_state_is_the_services_verdict_not_the_sign(
    tmp_path: Path, side: str, actual: str, expected_state: str, expected_display: str
) -> None:
    """`money(signed=True)` would derive `state` from the SIGN, which is wrong on half these
    rows: -50 is good news on a sell. The state is passed in from the service instead, and this
    is the pin that would fail if someone deleted the `state=` keyword as redundant."""
    row = web_payload.orders_payload(
        _report(tmp_path, _order(side=side, actual_fill=Decimal(actual)))
    )["rows"][0]
    assert row["fill_divergence"]["state"] == expected_state
    assert row["fill_divergence_adverse"]["display"] == expected_display


def test_the_mode_crosses_on_every_row(tmp_path: Path) -> None:
    """A deployment book holds one mode, so a reader comparing two consoles needs the
    distinction on the row rather than inferred from which window they are looking at."""
    document = web_payload.orders_payload(
        _report(tmp_path, _order(mode="live"), _order(mode="paper", confirmation="paper"))
    )
    assert [row["mode"]["display"] for row in document["rows"]] == ["paper", "live"]
    assert document["modes"] == ["live", "paper"]


def test_a_paper_row_carries_its_two_caveats_as_prose(tmp_path: Path) -> None:
    """`fee` is modelled and there is no venue order id. Both are stated, because a blank reads
    as missing data and neither of these is missing."""
    row = web_payload.orders_payload(
        _report(
            tmp_path,
            _order(mode="paper", confirmation="paper", raw_response=json.dumps({"role": "entry"})),
        )
    )["rows"][0]
    assert row["fee_modelled"]["display"] == "modelled"
    assert "not charged by a venue" in row["fee_note"]
    assert row["venue_order_id"] == ""
    assert "paper trader" in row["venue_order_id_note"]


def test_the_two_empties_cross_as_different_prose(tmp_path: Path) -> None:
    """Selecting prose from an enum word is a branch on a value, which `render.js` is forbidden
    to make -- so the sentence crosses already chosen, and the two are not interchangeable."""
    empty_book = web_payload.orders_payload(_report(tmp_path, scope="today"))
    assert empty_book["empty_reason"] == "book"
    assert "no orders in this book at all" in empty_book["empty_note"]

    empty_window = web_payload.orders_payload(
        _report(tmp_path, _order(created_at=NOW_TS - 90 * DAY), scope="today")
    )
    assert empty_window["empty_reason"] == "scope"
    assert "outside this scope" in empty_window["empty_note"]
    assert empty_window["empty_note"] != empty_book["empty_note"]


def test_a_report_with_rows_carries_no_empty_note(tmp_path: Path) -> None:
    document = web_payload.orders_payload(_report(tmp_path, _order()))
    assert document["empty_reason"] == ""
    assert document["empty_note"] == ""


def test_the_book_at_submit_crosses_as_the_pair_and_no_spread_is_derived(tmp_path: Path) -> None:
    """#626's two columns, on the wire. A spread computed here would be arithmetic Rule 3
    forbids in this layer AND would answer one of three different spread questions on the
    reader's behalf -- the same mistake as a fee rate, which this payload also refuses."""
    document = web_payload.orders_payload(
        _report(
            tmp_path,
            _order(submit_best_bid=Decimal("99999.5"), submit_best_ask=Decimal("100000.5")),
        )
    )
    row = document["rows"][0]
    assert row["submit_best_bid"]["value"] == "99999.5"
    assert row["submit_best_ask"]["value"] == "100000.5"
    assert row["submit_book_observed"]["display"] == "recorded"
    assert "spread" not in json.dumps(document).lower()
    assert not any("spread" in key for key in row), sorted(row)


def test_an_unobserved_book_says_which_kind_of_absence_it_is(tmp_path: Path) -> None:
    """NULL is NOT OBSERVED, never zero, and a paper row's absence is by design while a live
    row's is a gap in the record. The two sentences differ, and neither is a blank."""
    document = web_payload.orders_payload(
        _report(tmp_path, _order(mode="live"), _order(mode="paper", confirmation="paper"))
    )
    paper, live = document["rows"]  # newest first: the paper row was inserted second
    assert live["submit_book_observed"]["display"] == "not observed"
    assert live["submit_book_observed"]["state"] == "warn"
    assert live["submit_best_bid"]["display"] == "\u2014"
    assert "no readable book" in live["submit_book_note"]
    assert "against no venue" in paper["submit_book_note"]
    assert paper["submit_book_note"] != live["submit_book_note"]


def test_the_renderer_shows_the_book_pair_without_computing_one() -> None:
    """The client half. Already covered by the arithmetic ban over `ordersView`'s code, but
    stated by name so the reason survives with it."""
    source = _code("render.js")
    start = source.index("export function ordersView(")
    end = source.index("export function activityView(", start)
    body = source[start:end]
    assert "submit_best_bid" in body
    assert "submit_best_ask" in body
    assert "spread" not in body.lower()


# -- the two front-ends agree ---------------------------------------------------------------------


def test_the_browser_and_the_terminal_show_the_same_figures(tmp_path: Path) -> None:
    """The #641 arrangement, stated as an assertion rather than as an intention: both renderers
    read ONE report, so every figure the payload places must appear, character for character, in
    what `keel orders` prints. A second read, or a second rounding, fails here."""
    report = _report(
        tmp_path,
        _order(
            fee=Decimal("11.838"),
            expected_fill=Decimal("100000"),
            actual_fill=Decimal("100050"),
            qty=Decimal("0.01"),
        ),
    )
    terminal = "\n".join(render_orders(report))
    row = web_payload.orders_payload(report)["rows"][0]
    for key in ("qty", "expected_fill", "actual_fill", "fill_divergence", "fee"):
        assert row[key]["value"] in terminal, (key, row[key]["value"])
    assert row["venue_order_id"] in terminal
    # And the judgement is the same one, not two that happen to agree today.
    assert (row["fill_divergence"]["state"] == "bad") is (report.rows[0].fill_divergence_adverse)


# -- the endpoint ---------------------------------------------------------------------------------


def _bind(db_path: str, config_path: str) -> Iterator[web_server.ServeConfig]:
    cfg = web_server.ServeConfig(
        host="127.0.0.1",
        port=0,
        token=new_session_token(),
        db_path=db_path,
        config_path=config_path,
    )
    server = web_server.build_server(cfg)
    bound = web_server.ServeConfig(
        host=cfg.host,
        port=int(server.server_address[1]),
        token=cfg.token,
        db_path=db_path,
        config_path=config_path,
    )
    server.RequestHandlerClass.cfg = bound  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield bound
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def book(tmp_path: Path) -> Iterator[web_server.ServeConfig]:
    """A deployment whose book holds one live order and one paper order."""
    db_path = tmp_path / "keel.db"
    conn = connect(str(db_path))
    migrate(conn)
    repo = Repository(conn)
    # Anchored on the REAL clock, because `read_orders` reads `time.time()` -- a fixture
    # anchored on a fixed future instant would put every row inside every scope and make the
    # scope assertion below pass without the scope doing anything.
    now = int(time.time())
    repo.insert_order(
        _order(mode="live", confirmation="autonomous", created_at=now - 60, updated_at=now - 60)
    )
    repo.insert_order(
        _order(
            mode="paper",
            confirmation="paper",
            created_at=now - 90 * DAY,
            updated_at=now - 90 * DAY,
            raw_response=json.dumps({"role": "entry"}),
        )
    )
    conn.close()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(VALID_CONFIG_YAML)
    yield from _bind(str(db_path), str(config_path))


def _json_get(cfg: web_server.ServeConfig, path: str) -> tuple[int, Any]:
    conn = http.client.HTTPConnection(cfg.host, cfg.port, timeout=10)
    try:
        conn.request(
            "GET",
            path,
            headers={"Host": f"{cfg.host}:{cfg.port}", "Cookie": f"{SESSION_COOKIE}={cfg.token}"},
        )
        response = conn.getresponse()
        return response.status, json.loads(response.read().decode("utf-8", "replace"))
    finally:
        conn.close()


def test_the_endpoint_answers_with_both_modes(book: web_server.ServeConfig) -> None:
    status, document = _json_get(book, "/api/orders")
    assert status == 200, document
    modes = [row["mode"]["display"] for row in document["data"]["rows"]]
    assert sorted(modes) == ["live", "paper"]


def test_the_endpoint_offers_no_way_to_filter_by_mode(book: web_server.ServeConfig) -> None:
    """A `?mode=` would put the empty-on-two-of-three-books failure back through the query
    string. It is not that the parameter is refused -- there is no such parameter, and an
    unknown query key changes nothing."""
    _status, unfiltered = _json_get(book, "/api/orders")
    _status, attempted = _json_get(book, "/api/orders?mode=live")
    assert attempted["data"]["rows"] == unfiltered["data"]["rows"]
    assert len(unfiltered["data"]["rows"]) == 2, "the fixture must hold both modes"

    # And the reader never reaches for the key at all: an implementation that read `?mode=` and
    # happened to ignore it today is one edit away from honouring it.
    body = inspect.getsource(web_api.read_orders)
    code = "\n".join(line for line in body.splitlines() if not line.lstrip().startswith(("#", "*")))
    assert '_first(query, "mode")' not in code
    assert "mode=" not in code.split('"""')[-1]


def test_the_scope_is_normalised_and_echoed_rather_than_refused(
    book: web_server.ServeConfig,
) -> None:
    """`activity`'s rule: the scope has a SERVICE with a default behind it, so refusing here
    would have the browser and the terminal disagreeing about the same input. The resolved value
    is echoed, so a client can see its input was changed."""
    status, document = _json_get(book, "/api/orders?scope=last-tuesday")
    assert status == 200
    assert document["data"]["scope"] == "all"

    _status, scoped = _json_get(book, "/api/orders?scope=today")
    assert scoped["data"]["scope"] == "today"
    # The 90-day-old paper row falls outside; the book still says it holds two.
    assert scoped["data"]["scoped_count"]["value"] == "1"
    assert scoped["data"]["total_count"]["value"] == "2"


@pytest.mark.parametrize("bad", ["0", "-1", "abc", "99999"])
def test_a_bad_limit_is_refused_rather_than_silently_substituted(
    book: web_server.ServeConfig, bad: str
) -> None:
    """Unlike a scope, a limit has no echo that would make a substitution visible: a caller who
    asked for two thousand rows and silently got fifty has been told nothing."""
    status, document = _json_get(book, f"/api/orders?limit={bad}")
    assert status == 400, document
    assert "limit" in json.dumps(document).lower()


def test_the_endpoint_is_registered_for_the_orders_view() -> None:
    route = web_api.API_ROUTES["/api/orders"]
    assert route.html_route == "/orders"
    assert route.collection == "rows"
    assert "placement" in route.sortable


def test_the_orders_view_needs_no_capability_row() -> None:
    """The view is READ-ONLY, and the proof is that `keel/capabilities.py` did not have to
    change: nothing this endpoint reaches arms, releases or spends anything.
    `test_no_capability_increasing_action_is_reachable_from_the_web_layer` scans the whole web
    package for the eight; this states the narrower fact that adding this view needed no ninth."""
    from keel.capabilities import CAPABILITIES

    functions = {cap.function for cap in CAPABILITIES}
    assert "read_orders" not in functions
    assert "orders_cmd" not in functions
    assert "gather_orders" not in functions


# -- the client, over its source text -------------------------------------------------------------


def test_the_route_tables_agree_about_orders() -> None:
    """Python's `CLIENT_ROUTES`, JavaScript's `ROUTES` and the shell's nav are three tables that
    cannot import each other. A view in one alone routes on a click and 404s on a reload."""
    assert "orders" in staticfiles.CLIENT_ROUTES
    names = re.findall(
        r'\{\s*name:\s*"([a-z]+)"',
        _source("main.js")[
            _source("main.js").index("const ROUTES = [") : _source("main.js").index(
                "];", _source("main.js").index("const ROUTES = [")
            )
        ],
    )
    assert names == list(staticfiles.CLIENT_ROUTES)
    html = (_STATIC / "index.html").read_text(encoding="utf-8")
    assert '<li><a href="/orders">Orders</a></li>' in html


def test_the_renderer_declares_the_placement_column_first() -> None:
    """The layout argument, pinned in the source that makes it. `placement` before `product` is
    the whole reason this view is not a generic table dump."""
    source = _source("render.js")
    start = source.index("export function ordersView(")
    end = source.index("function emptyOrders(", start)
    body = source[start:end]
    assert body.index('label: "placement"') < body.index('label: "product"')
    assert body.index('label: "placement"') < body.index('label: "fee"')
    # expected / actual / divergence adjacent, in that order.
    assert (
        body.index('label: "expected"')
        < body.index('label: "actual"')
        < body.index('label: "divergence"')
    )


def test_the_renderer_never_mentions_raw_response_and_computes_no_rate() -> None:
    """It could not render the blob -- the payload has no such key -- but a future author
    reaching for `row.raw_response` would get `undefined` and a blank cell rather than an error,
    which is exactly the sort of change this pin exists to fail."""
    source = _code("render.js")
    start = source.index("export function ordersView(")
    # Bounded at `activityView`, the next export, so this scans THIS view's three functions
    # (`ordersView`, `emptyOrders`, `orderDetail`) and does not quietly borrow another view's
    # cleanliness or fail on another view's legitimate arithmetic.
    end = source.index("export function activityView(", start)
    body = source[start:end]
    assert "raw_response" not in body
    # No arithmetic of any kind in this view's code. A percentage is the specific thing being
    # forbidden -- a fee rate derived in the browser would be a figure no report holds -- and an
    # operator ban is the only form of that rule a scan can check.
    for operator in ("*", "/", "%", "+", "-="):
        assert operator not in body, operator


def test_the_orders_view_is_wired_into_the_client_router() -> None:
    source = _source("main.js")
    assert "ordersView," in source
    assert 'route.name === "orders"' in source
    assert "ordersView(data, primary.sort, onSort," in source


def test_the_scope_switch_names_itself_per_view() -> None:
    """Two scope controls on one site announcing themselves as "Activity scope" would leave a
    screen-reader user unable to tell which view's window they had landed in."""
    source = _source("render.js")
    assert "function scopeSwitch(current, onScope, label)" in source
    assert '"Orders scope"' in source


def test_the_client_added_no_new_asset_to_precache() -> None:
    """`ordersView` lives in `render.js` rather than in a module of its own, so `sw.js`'s
    `PRECACHE` needed no entry and `tests/web/test_pwa.py`'s shipped-versus-precached comparison
    needed no exemption. Stated as an assertion so a later split of this view into its own file
    fails here with the reason attached."""
    precache = (_STATIC / "sw.js").read_text(encoding="utf-8")
    assert "orders.js" not in precache
    assert not (_STATIC / "js" / "orders.js").exists()
