"""The JSON API (#534), over a real bound server.

Driven against an actual `ThreadingHTTPServer` rather than a hand-built handler for the same
reason `tests/web/test_server.py` is: the properties worth pinning here -- that a read needs no
write header, that a refusal is still JSON, that `Cache-Control: no-store` reaches the wire on
every `/api/*` response -- are properties of the BYTES, and a test against a handler object could
pass while the served response said otherwise.

**This module deliberately does not reuse `tests/web/test_server.py`'s `_request`.** That helper
defaults `client_header="1"` on every POST, which is right for the module it lives in and wrong
here: the central question below is whether a *GET* is answered by a client that sends no custom
header at all (a `curl`, an address bar, a service worker's `NetworkOnly` fetch), so the helper
these tests need is one that sends nothing it was not asked to send. Sharing the other one would
have meant a default quietly answering the question the test is asking -- see `_get`'s docstring.

Five things are pinned, and each exists because a downstream issue (#536-#540) consumes it:

* **Every HTML read has a JSON counterpart**, `/glossary` excepted -- it becomes an outbound link
  in #539 and its renderer is deleted in #540, so an `/api/glossary` would be a surface built to
  be removed.
* **`GET /api/config` returns the running version**, because #538 keys a service-worker cache to
  it and #539 carries it as `?v=` on documentation links.
* **Sorting is a query parameter ordered with `Decimal`**, with the float-collision case that
  makes the choice of `Decimal` load-bearing rather than decorative.
* **A stopped engine is a stated fact, not an empty payload** -- `engine.value == "stopped"` with
  `data: null`, at HTTP 200, so a client renders "keel isn't running" instead of a blank view.
* **The write surface did not move.** `POST /api/*` still 404s behind the `X-Keel-Client` gate,
  and this issue adds no route to it.
"""

from __future__ import annotations

import ast
import http.client
import json
import sqlite3
import threading
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from keel.data.db import connect, migrate
from keel.web import api as web_api
from keel.web import payload
from keel.web import server as web_server
from keel.web.security import SESSION_COOKIE, csrf_token, new_session_token
from tests.conftest import VALID_CONFIG_YAML

#: Every read the HTML routes perform, as its JSON counterpart. `/glossary` is absent on purpose
#: and `test_the_glossary_has_no_api_counterpart` states that absence as an assertion, so deleting
#: it here later cannot silently mean "we forgot".
API_ROUTES = (
    "/api/config",
    "/api/status",
    "/api/setup",
    "/api/activity",
    "/api/orders",
    "/api/positions",
    "/api/balances",
    "/api/timeline",
    "/api/insights",
    "/api/journal",
    "/api/research/trials",
    "/api/rules",
    "/api/venues",
    "/api/gates",
)


def test_the_csv_export_is_deliberately_outside_the_json_route_table() -> None:
    """#703's export answers `text/csv`, so it must NOT be an `ApiRoute`.

    Everything in `API_ROUTES` is wrapped in the JSON envelope by `respond`, and every pin in
    this module is parametrised over that table -- the envelope, the no-JSON-number walk, the
    JSON MIME assertion. A CSV route inside it would either break those or force each one to
    grow an exception, and an exception carved into a security pin is how the pin stops meaning
    anything. Its headers are pinned separately, in `test_timeline_export.py`.
    """
    from keel.web import api as web_api

    assert web_api.CSV_EXPORT_PATH not in web_api.API_ROUTES
    assert web_api.CSV_EXPORT_PATH.startswith("/api/"), (
        "it still lives under /api/, so it still passes the same admission check"
    )


def test_this_module_pins_every_route_the_server_serves() -> None:
    """`API_ROUTES` above is hand-written, and everything in this file is parametrised over it --
    the envelope, the no-JSON-number walk, the cache headers, the nosniff header and the
    POST refusal. A route left out of it is served with none of those checked.

    That happened: `/api/positions` (#701) shipped outside the tuple, so its reader was executed
    by no test at all while its payload builder was well covered -- and this module's own header
    says those are not the same statement. Cross-checked here so the next one cannot.
    """
    from keel.web import api as web_api

    assert set(API_ROUTES) == set(web_api.API_ROUTES), (
        "API_ROUTES here must name every route the server actually serves"
    )


# -- a real server ------------------------------------------------------------------------------


def _bind(db_path: str, config_path: str, **extra: Any) -> Iterator[web_server.ServeConfig]:
    cfg = web_server.ServeConfig(
        host="127.0.0.1",
        port=0,
        token=new_session_token(),
        db_path=db_path,
        config_path=config_path,
        **extra,
    )
    server = web_server.build_server(cfg)
    bound = web_server.ServeConfig(
        host=cfg.host,
        port=int(server.server_address[1]),
        token=cfg.token,
        db_path=db_path,
        config_path=config_path,
        **extra,
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
def deployment(tmp_path: Path) -> tuple[str, str]:
    db_path = tmp_path / "keel.db"
    conn = connect(str(db_path))
    migrate(conn)
    conn.close()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(VALID_CONFIG_YAML)
    return str(db_path), str(config_path)


@pytest.fixture
def running(deployment: tuple[str, str]) -> Iterator[web_server.ServeConfig]:
    db_path, config_path = deployment
    yield from _bind(db_path, config_path)


@pytest.fixture
def empty_machine(tmp_path: Path) -> Iterator[web_server.ServeConfig]:
    """Nothing set up at all -- no config, no database. The first-run state, and the one an API
    client must be told about in words rather than left to infer from an empty list."""
    yield from _bind(str(tmp_path / "keel.db"), str(tmp_path / "config.yaml"))


def _get(
    cfg: web_server.ServeConfig,
    path: str,
    *,
    method: str = "GET",
    cookie: str | None = None,
    host: str | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], str]:
    """A request that sends NOTHING it was not asked to send.

    No `X-Keel-Client`, no `Sec-Fetch-Site`, no `Origin` unless a test names them. That is the
    whole point of not sharing `test_server.py`'s helper: a default header here would answer
    `test_a_read_needs_no_client_header` on the test's behalf, and the shipped consumers of this
    API include a `curl` and a browser address bar, neither of which sends one."""
    conn = http.client.HTTPConnection(cfg.host, cfg.port, timeout=10)
    sent = {"Host": host if host is not None else f"{cfg.host}:{cfg.port}"}
    if cookie:
        sent["Cookie"] = cookie
    sent.update(headers or {})
    try:
        conn.request(method, path, headers=sent)
        response = conn.getresponse()
        body = response.read().decode("utf-8", "replace")
        return response.status, dict(response.getheaders()), body
    finally:
        conn.close()


def _session(cfg: web_server.ServeConfig) -> str:
    return f"{SESSION_COOKIE}={cfg.token}"


def _json(cfg: web_server.ServeConfig, path: str) -> tuple[int, dict[str, str], Any]:
    status, headers, body = _get(cfg, path, cookie=_session(cfg))
    return status, headers, json.loads(body)


# -- seeding ------------------------------------------------------------------------------------


def _seed_positions(db_path: str, rows: tuple[tuple[str, str, str], ...]) -> None:
    """Open tranches, written straight into the table `gather_status` reads.

    `qty` and `entry_fill` are TEXT columns holding exact decimal strings, which is what makes the
    float-collision case below reachable end to end rather than only in a unit test: the value the
    API sorts is the value the ledger stored, character for character."""
    conn = sqlite3.connect(db_path)
    try:
        for index, (product_id, qty, entry_fill) in enumerate(rows):
            conn.execute(
                "INSERT INTO positions (product_id, rule_name, opened_at, qty, entry_fill, "
                "entry_fee, status) VALUES (?, ?, ?, ?, ?, ?, 'open')",
                (product_id, f"rule-{index}", 1_700_000_000 + index, qty, entry_fill, "0"),
            )
        conn.commit()
    finally:
        conn.close()


def _seed_orders(db_path: str, rows: tuple[tuple[str, str, str], ...]) -> None:
    """Placed orders, written straight into the table `gather_timeline` reads.

    Separate from `_seed_positions` because the two tables are not the same store and the
    timeline does not read `positions` at all -- which is how `/api/timeline` joined the sort
    pin below asserting on rows that nothing in this module had ever written. The assertion
    caught it (`assert rows` is not decorative), so the fix is here rather than in the pin."""
    conn = sqlite3.connect(db_path)
    try:
        for index, (product_id, side, fill) in enumerate(rows):
            conn.execute(
                "INSERT INTO orders (mode, product_id, side, qty, status, actual_fill, "
                "created_at) VALUES ('paper', ?, ?, '0.01', 'filled', ?, ?)",
                (product_id, side, fill, 1_700_000_000 + index),
            )
        conn.commit()
    finally:
        conn.close()


def _seed_rules(db_path: str, kinds: tuple[str, ...]) -> None:
    conn = sqlite3.connect(db_path)
    try:
        for index, kind in enumerate(kinds):
            conn.execute(
                "INSERT INTO rules (kind, params, status, created_at) VALUES (?, ?, ?, ?)",
                (kind, json.dumps({"product_id": "BTC-USD"}), "candidate", 1_700_000_000 + index),
            )
        conn.commit()
    finally:
        conn.close()


# -- every read is available as JSON --------------------------------------------------------------


@pytest.mark.parametrize("path", API_ROUTES)
def test_every_route_answers_json_with_the_envelope(
    running: web_server.ServeConfig, path: str
) -> None:
    """The uniform half of the contract: one shape, so #536's single `fetch` wrapper needs no
    per-endpoint branch. `as_of` and `engine` are present on EVERY success, including the three
    endpoints that describe the binary rather than the deployment -- a client that had to know
    which endpoints carry the liveness word would be branching on payload shape, which is the
    thing `payload.py`'s Rule 3 exists to remove."""
    status, headers, document = _json(running, path)

    assert status == 200, (path, document)
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert set(document) >= {"as_of", "engine", "data"}
    assert document["as_of"].endswith("Z")
    assert document["engine"]["value"] in payload.ENGINE_STATES
    assert document["data"] is not None


def test_the_glossary_has_no_api_counterpart(running: web_server.ServeConfig) -> None:
    """Stated as an assertion rather than left as an omission. `/glossary` becomes an outbound
    keeltrading.com link in #539 and `render_glossary` is deleted in #540, so an `/api/glossary`
    would be a surface built in order to be removed -- and a client written against it would
    break on the release that removes it."""
    status, headers, document = _json(running, "/api/glossary")

    assert status == 404
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert document["error"]["status"] == "404"


def test_the_api_routes_cover_every_html_route_that_reads(
    running: web_server.ServeConfig,
) -> None:
    """**This pin lost its other side at #540, and what is left still earns its place.**

    It compared `server.ROUTES` -- the HTML route table -- against the JSON endpoints, so that a
    page added without a counterpart failed here. There are no pages. What the comparison was
    really protecting is that every VIEW the client offers has an endpoint behind it, and that
    survives: `staticfiles.CLIENT_ROUTES` is the client's own list of views, and every one of them
    must be covered by an endpoint whose `html_route` names it.

    `/insights` is named by two endpoints (`/api/insights` and `/api/journal`) and that is the
    server's own decision, recorded in `api.API_ROUTES`; a set comparison absorbs it."""
    from keel.web import staticfiles

    views = {"/" + name for name in staticfiles.CLIENT_ROUTES}
    # The client's `status` view is served by the endpoint that named the old landing page `/`.
    views = {"/" if view == "/status" else view for view in views}
    covered = {web_api.HTML_ROUTE_FOR[name] for name in web_api.API_ROUTES}

    assert views <= covered, views - covered


# -- /api/config ----------------------------------------------------------------------------------


def test_config_returns_the_running_version(deployment: tuple[str, str]) -> None:
    """#538 keys a service-worker cache name to `build`, and #539 carries `version` as `?v=` on
    every documentation link. Both consumers need the value to be the RUNNING build's, resolved
    once at start-up rather than re-derived per request -- `keel.version.build_info()` shells out
    to git, and a service worker polling an endpoint that forks a subprocess is a bad trade."""
    db_path, config_path = deployment
    fake = _FakeBuild()
    for cfg in _bind(db_path, config_path, build="keel 9.9.9+abc [checkout]", build_info=fake):
        status, _headers, document = _json(cfg, "/api/config")

        assert status == 200
        assert document["data"]["version"] == "9.9.9"
        assert document["data"]["build"] == "9.9.9+abc123456789"
        assert document["data"]["source"] == "checkout"
        assert document["data"]["describe"] == "keel 9.9.9+abc [checkout]"
        assert document["data"]["reproducible"]["value"] == "true"


def test_config_survives_a_build_it_could_not_resolve(running: web_server.ServeConfig) -> None:
    """An environment with no package metadata and no git is not an error worth a 500 for: the
    footer already degrades to an empty string there (`serve.py::_build_line`), and a cache key
    of `"unknown"` still keys a cache. Reported as absent rather than invented."""
    status, _headers, document = _json(running, "/api/config")

    assert status == 200
    assert document["data"]["version"] == ""
    assert document["data"]["reproducible"]["state"] == "unknown"


def test_config_names_the_served_deployment(running: web_server.ServeConfig) -> None:
    """#597's mode badge answers "which deployment is this" from the header, on EVERY view.

    `/api/config` is the one endpoint the client reads regardless of route -- it keys the
    service worker and the docs links at boot -- so the deployment's identity rides the answer
    that is already arriving, rather than a second endpoint or a badge that only hydrates where
    `/api/status` happens to be the view.

    `mode` is the config's own word for `auto_trade.mode`, copied verbatim and not judged:
    `paper` beside `confirm` is neither good nor bad, and a `Field` with a `state` would force
    exactly that judgement. The two paths are the "where am I" of `keel serve` -- one process
    serves ONE `--db`/`--config` pair, and naming them here makes paper-vs-live confusion
    answerable at a glance instead of by asking the terminal.
    """
    status, _headers, document = _json(running, "/api/config")

    assert status == 200
    # `VALID_CONFIG_YAML` declares no `auto_trade` block, and the config layer's default mode
    # is paper -- the badge must move with the deployment's own answer, never with a guess here.
    assert document["data"]["mode"] == "paper"
    assert document["data"]["db_path"] == running.db_path
    assert document["data"]["config_path"] == running.config_path


def test_config_reports_the_mode_the_config_file_declares(tmp_path: Path) -> None:
    """The same binary serving a confirm-mode config is a different deployment, and an operator
    with two of them open in two tabs must be able to tell them apart from the header alone."""
    config_path = tmp_path / "confirm.yaml"
    config_path.write_text(VALID_CONFIG_YAML + "\nauto_trade:\n  mode: confirm\n")
    for cfg in _bind(str(tmp_path / "keel.db"), str(config_path)):
        status, _headers, document = _json(cfg, "/api/config")

        assert status == 200
        assert document["data"]["mode"] == "confirm"


def test_config_survives_a_config_it_could_not_read(
    empty_machine: web_server.ServeConfig,
) -> None:
    """The whole shell boots off this endpoint -- the worker registration, the docs links, the
    footer AND the badge. A config that is missing (the first-run state this fixture binds) or
    unreadable must degrade the mode to absent rather than 500 the page, exactly as a build that
    could not be resolved already does above."""
    status, _headers, document = _json(empty_machine, "/api/config")

    assert status == 200
    assert document["data"]["mode"] == ""
    # The paths are this process's own arguments; they are known even when the config is not.
    assert document["data"]["db_path"] == empty_machine.db_path


class _FakeBuild:
    """A resolved `keel.version.BuildInfo`, without shelling out to git in a test."""

    version = "9.9.9"
    commit = "abc123456789"
    dirty = False
    source = "checkout"
    full_version = "9.9.9+abc123456789"
    is_reproducible = True


# -- money crosses the wire as a string ------------------------------------------------------------


def _walk(node: Any, path: str = "$") -> list[tuple[str, Any]]:
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


def _json_numbers(document: Any) -> list[str]:
    """`bool` first and deliberately: Python's `bool` is a subclass of `int`, so an `isinstance`
    test alone would flag every JSON `true`. Duplicated from `test_payload.py` rather than
    imported so that weakening one guard cannot weaken the other."""
    return [
        path
        for path, leaf in _walk(document)
        if not isinstance(leaf, bool) and isinstance(leaf, (int, float))
    ]


@pytest.mark.parametrize("path", API_ROUTES)
def test_no_wire_value_from_any_endpoint_is_ever_a_json_number(
    running: web_server.ServeConfig, path: str
) -> None:
    """#533's rule, asserted over the bytes each ENDPOINT actually serves rather than over the
    serialiser's return value. The two are not the same statement: the envelope, the sort echo and
    every refusal body are written by the routing layer, and a plain `int` in any of them is a
    double in a browser exactly as a mis-serialised price would be."""
    db_path = running.db_path
    _seed_positions(db_path, (("BTC-USD", "0.01", "50000"),))
    _seed_rules(db_path, ("breakout",))

    _status, _headers, document = _json(running, path)

    assert _json_numbers(document) == [], path


def test_the_number_walker_is_proven_false_capable() -> None:
    """The guard's positive control. A walker that matched nothing would make every parametrised
    case above green over a payload full of doubles."""
    assert _json_numbers({"a": [{"b": 0.1}], "ok": True, "text": "1"}) == ["$.a[0].b"]


def test_even_a_refusal_carries_no_json_number(running: web_server.ServeConfig) -> None:
    """The HTTP status crosses as `"404"`, not `404`. It would survive JSON's number type intact,
    and it still goes as a string: a payload with "only a few" numbers in it needs a per-field rule
    about which ones, and that rule is what rots -- `payload.count`'s docstring makes the same
    argument for a trade count."""
    _status, _headers, document = _json(running, "/api/nope")

    assert _json_numbers(document) == []


# -- server-side sort ------------------------------------------------------------------------------


def test_sorting_is_a_query_parameter(running: web_server.ServeConfig) -> None:
    _seed_rules(running.db_path, ("momentum", "breakout", "reversal"))

    _status, _headers, ascending = _json(running, "/api/rules?sort=kind")
    _status, _headers, descending = _json(running, "/api/rules?sort=kind&dir=desc")

    assert [row["kind"] for row in ascending["data"]["rules"]] == [
        "breakout",
        "momentum",
        "reversal",
    ]
    assert [row["kind"] for row in descending["data"]["rules"]] == [
        "reversal",
        "momentum",
        "breakout",
    ]


def test_the_sort_is_echoed_so_a_client_needs_no_hardcoded_column_list(
    running: web_server.ServeConfig,
) -> None:
    """#537 renders sortable headers. It reads the column list off the response rather than
    holding a copy, so a column added here reaches the interface without a second edit -- and a
    column removed here cannot leave a header that sorts by nothing."""
    _status, _headers, document = _json(running, "/api/rules?sort=kind&dir=desc")

    assert document["sort"]["column"] == "kind"
    assert document["sort"]["direction"] == "desc"
    assert "kind" in document["sort"]["columns"]


def test_an_unknown_sort_column_is_refused_not_ignored(running: web_server.ServeConfig) -> None:
    """Silently ignoring an unknown column is how a client ships a sort that does nothing and
    nobody notices for a release. The refusal names the columns that do exist, so the fix is in
    the response."""
    status, _headers, document = _json(running, "/api/rules?sort=expectancy")

    assert status == 400
    assert "expectancy" in document["error"]["detail"]
    assert "kind" in document["error"]["detail"]


def test_an_unknown_sort_direction_is_refused(running: web_server.ServeConfig) -> None:
    status, _headers, document = _json(running, "/api/rules?sort=kind&dir=sideways")

    assert status == 400
    assert "sideways" in document["error"]["detail"]


def test_an_endpoint_with_nothing_to_sort_says_so(running: web_server.ServeConfig) -> None:
    """`/api/setup`'s steps are in runbook order -- the order IS the information, since a checklist
    sorted by title is a checklist you cannot work down -- so it declares no sortable columns, and
    a `?sort=` against it is refused rather than quietly obeyed."""
    status, _headers, refused = _json(running, "/api/setup?sort=title")
    _status, _headers, plain = _json(running, "/api/setup")

    assert status == 400
    assert "no sortable table" in refused["error"]["detail"]
    # And the successful response says the same thing in the shape a client reads: `sort` is
    # `null`, not an object with an empty column list, so "this endpoint does not sort" and "this
    # endpoint sorts but you have not asked it to" stay distinguishable.
    assert plain["sort"] is None


def test_an_endpoint_that_sorts_echoes_its_columns_before_anything_is_sorted(
    running: web_server.ServeConfig,
) -> None:
    """The other half of the distinction above."""
    _status, _headers, document = _json(running, "/api/rules")

    assert document["sort"] == {
        "column": "",
        "direction": "asc",
        "columns": list(web_api.API_ROUTES["/api/rules"].sortable),
    }


def test_every_declared_sort_column_is_a_column_the_rows_actually_have(
    running: web_server.ServeConfig,
) -> None:
    """The guard against the one way a hand-written column list rots: a key renamed in
    `payload.py` leaves a `sortable` entry that names nothing, and `?sort=` by it would be accepted
    and then order every row identically -- an accepted request that silently does nothing, which
    is exactly what refusing an unknown column exists to prevent.

    Checked over every endpoint this test can seed rows into. A collection with no rows proves
    nothing here, which is why the seeding is not optional.

    `/api/timeline` (#703) joined this list after shipping with `sortable=("ts", ...)` while its
    payload emits `at` -- accepted, echoed back as applied, and ordering nothing. That is the
    exact rot this pin exists for, and it went unnoticed because the route was not in it."""
    _seed_positions(running.db_path, (("BTC-USD", "0.01", "50000"),))
    _seed_rules(running.db_path, ("breakout",))
    _seed_orders(running.db_path, (("BTC-USD", "buy", "50000"),))

    for path, collection in (
        ("/api/status", "open_positions"),
        ("/api/rules", "rules"),
        ("/api/timeline", "rows"),
    ):
        _status, _headers, document = _json(running, path)
        rows = document["data"][collection]

        assert rows, path
        for row in rows:
            missing = set(web_api.API_ROUTES[path].sortable) - set(row)
            assert not missing, (path, missing)


# -- Decimal ordering, and the case where a float would differ -------------------------------------

#: Three quantities in the ledger's own text form. The first two differ by 1e-18 -- one wei, the
#: base increment of any 18-decimal ERC-20 -- and `float()` maps BOTH to the same IEEE-754 double,
#: because at 0.1 the gap between adjacent doubles is about 1.4e-17.
WEI_APART = (
    "0.100000000000000002",
    "0.100000000000000001",
    "0.099000000000000000",
)


def test_float_and_decimal_orderings_of_the_same_column_genuinely_differ() -> None:
    """The premise of the test below, asserted rather than assumed.

    If `float` and `Decimal` agreed on these three strings, the endpoint test would be green for
    no reason -- it would prove only that sorting sorts. So this states the disagreement first:
    the two finest values are DISTINCT as `Decimal` and EQUAL as `float`, which means a float-keyed
    sort cannot separate them and leaves them in whatever order they arrived in."""
    finer, coarser = Decimal(WEI_APART[1]), Decimal(WEI_APART[0])

    assert finer < coarser
    assert float(WEI_APART[1]) == float(WEI_APART[0])
    # Ascending by float keeps the input order of the tied pair; ascending by Decimal swaps it.
    assert sorted(WEI_APART, key=float) == [WEI_APART[2], WEI_APART[0], WEI_APART[1]]
    assert sorted(WEI_APART, key=Decimal) == [WEI_APART[2], WEI_APART[1], WEI_APART[0]]


def test_the_endpoint_orders_with_decimal_not_float(running: web_server.ServeConfig) -> None:
    """The end-to-end half: the same three quantities through the real table, the real report
    builder, the real serialiser and the real sort.

    A float-keyed implementation would answer `[0.099, ...002, ...001]` here -- the tied pair left
    in insertion order, because the comparison cannot tell them apart. `Decimal` answers
    `[0.099, ...001, ...002]`. The difference is one wei on one row, which is exactly the size of
    error that survives review and shows up in a reconciliation."""
    _seed_positions(
        running.db_path, tuple((f"TKN{i}-USD", qty, "50000") for i, qty in enumerate(WEI_APART))
    )

    _status, _headers, unsorted = _json(running, "/api/status")
    _status, _headers, document = _json(running, "/api/status?sort=qty")

    as_built = [row["qty"]["value"] for row in unsorted["data"]["open_positions"]]
    ordered = [row["qty"]["value"] for row in document["data"]["open_positions"]]

    assert ordered == [WEI_APART[2], WEI_APART[1], WEI_APART[0]]
    # The comparison that makes this test say something: the SAME rows, in the order the report
    # built them, keyed by `float` instead. Python's sort is stable, so the tied pair keeps its
    # arrival order and the answer differs from the one above by one row.
    assert sorted(as_built, key=float) == [WEI_APART[2], WEI_APART[0], WEI_APART[1]]
    assert ordered != sorted(as_built, key=float)


def test_a_missing_figure_sorts_last_in_both_directions() -> None:
    """A row with no recorded value is not a row with a zero -- that collapse is the shape of the
    always-passing fee rail (#198), and `payload.absent` exists to keep the two apart. So an absent
    cell trails the ordered rows whichever way they run, rather than heading a descending sort by
    being the largest thing in it."""
    rows = [
        {"id": "a", "n": payload.money(Decimal("2"))},
        {"id": "b", "n": payload.absent()},
        {"id": "c", "n": payload.money(Decimal("1"))},
    ]

    ascending = payload.order_rows(rows, column="n", descending=False)
    descending = payload.order_rows(rows, column="n", descending=True)

    assert [row["id"] for row in ascending] == ["c", "a", "b"]
    assert [row["id"] for row in descending] == ["a", "c", "b"]


def test_a_column_that_is_not_numeric_orders_as_text() -> None:
    """One column, one ordering. A column whose values do not ALL parse as finite `Decimal`s is
    ordered as text -- deciding per ROW would put a `Decimal` and a `str` in the same comparison,
    which raises, and falling back per row would make the order depend on which values happened to
    look like numbers."""
    rows = [
        {"outcome": payload.label("win")},
        {"outcome": payload.label("dca")},
        {"outcome": payload.label("loss")},
    ]

    ordered = payload.order_rows(rows, column="outcome", descending=False)

    assert [row["outcome"]["value"] for row in ordered] == ["dca", "loss", "win"]


def test_an_instant_orders_chronologically_through_its_iso_string() -> None:
    """`moment().value` is ISO-8601 UTC with a fixed width, so lexicographic order IS chronological
    order and no date parsing happens in the sort. This is the reason `moment` puts ISO in `value`
    rather than epoch seconds -- `payload.moment`'s docstring records the other."""
    rows = [
        {"at": payload.moment(1_700_000_100)},
        {"at": payload.moment(1_700_000_000)},
        {"at": payload.moment(1_700_000_200)},
    ]

    ordered = payload.order_rows(rows, column="at", descending=True)

    assert [row["at"]["value"] for row in ordered] == [
        "2023-11-14T22:16:40Z",
        "2023-11-14T22:15:00Z",
        "2023-11-14T22:13:20Z",
    ]


def test_a_non_finite_value_does_not_drag_a_column_into_numeric_ordering() -> None:
    """`Decimal("NaN")` and `Decimal("Infinity")` both PARSE, and both are unorderable against a
    real figure -- `NaN` compares false to everything, which would make the sort's output depend on
    the comparison order the algorithm happened to use. Non-finite means "not a number this column
    can be ordered by", so the column falls to text ordering as a whole."""
    rows = [
        {"n": {"value": "Infinity", "display": "inf", "state": "unknown"}},
        {"n": {"value": "2", "display": "2", "state": "neutral"}},
    ]

    ordered = payload.order_rows(rows, column="n", descending=False)

    assert [row["n"]["value"] for row in ordered] == ["2", "Infinity"]


# -- the engine's state is a fact, not an inference ------------------------------------------------


def test_a_stopped_engine_says_so_rather_than_answering_an_empty_payload(
    empty_machine: web_server.ServeConfig,
) -> None:
    """THE requirement the service worker enforces from the other side (#538): a client must be
    able to say "keel isn't running" rather than render a blank view or, worse, a stale figure.

    `data` is `null` and not `{}`: an empty object is a payload with every figure missing, and a
    view given one renders zeros. `null` cannot be rendered by accident."""
    status, _headers, document = _json(empty_machine, "/api/status")

    assert status == 200
    assert document["engine"]["value"] == "stopped"
    assert document["data"] is None
    assert document["as_of"].endswith("Z")
    assert document["engine"]["display"]
    assert document["engine"]["state"] in {"warn", "bad"}


def test_a_stopped_engine_is_a_200_not_an_error(empty_machine: web_server.ServeConfig) -> None:
    """Reported at 200 on purpose. A 4xx/5xx is what #538's service worker and #536's `fetch`
    wrapper both read as "the server is unreachable", which would put a first-run user in an
    outage state when their actual position is that they have not set anything up yet -- and the
    two need different words on screen."""
    status, _headers, _document = _json(empty_machine, "/api/status")

    assert status == 200


def test_a_stopped_engine_still_answers_the_endpoints_that_describe_the_binary(
    empty_machine: web_server.ServeConfig,
) -> None:
    """`/api/config` and `/api/gates` read no deployment at all -- they describe the binary that
    is answering. `/api/venues` is `needs_database=False` for the same reason (still answers
    with nothing set up) but, since #233 PR4, DOES attempt a best-effort read of this
    deployment's venue readiness -- `read_venues` checks `state.has_usable_database` first and
    skips the repo entirely here, where it is `False`, so the property this test actually cares
    about (200 with data, nothing required to exist first) still holds. All three still report
    `engine`, because a client showing the "keel isn't running" banner should not have to fetch a
    different endpoint to know whether to show it."""
    for path in ("/api/config", "/api/venues", "/api/gates"):
        status, _headers, document = _json(empty_machine, path)

        assert status == 200, path
        assert document["engine"]["value"] == "stopped", path
        assert document["data"] is not None, path


# -- venue readiness (#233 PR4) -------------------------------------------------------------------


def test_venues_carries_a_sibling_readiness_key_never_merged_into_venues(
    running: web_server.ServeConfig,
) -> None:
    """`venues` stays exactly the capability-declaration shape it always was; `readiness` is a
    SIBLING top-level key, never a field added onto a `venues` row -- the same separation
    `keel/commands/brokers.py` keeps between its declarations block and its readiness block."""
    status, _headers, document = _json(running, "/api/venues")
    assert status == 200
    data = document["data"]
    assert "readiness" in data
    assert isinstance(data["readiness"], list)
    assert data["readiness"]  # at least the CREDENTIALED_VENUES catalog
    for row in data["venues"]:
        assert "readiness" not in row
        assert "state" not in row  # the readiness vocabulary must not leak onto a venues row


def test_a_credential_less_dev_venue_stays_not_tradeable_even_with_a_confirmed_record(
    running: web_server.ServeConfig,
) -> None:
    """End to end through the real route: a venue that presents NO credentials stays
    `not_tradeable` EVEN WITH a CONFIRMED trade-scope record sitting in this deployment's
    database.

    This test used to assert the opposite -- that the record alone turned `fake` `ready` -- and
    that was the defect. `fake` is the deterministic dev venue and `kraken` is a stub with no
    network path at all; neither is something this deployment can trade, whatever a row says
    about it. Grading them on trade scope produced `not_permitted` with
    `fix: keel scope attest --trading --venue kraken`, advice that cannot work because there is
    no credential behind it to attest ABOUT, and put a permanent red row on the venues card for
    a venue nobody was going to trade.

    The record is written anyway, and asserted NOT to resurrect the row, because a credential
    question that stopped being asked once a record existed would reintroduce exactly that.
    """
    from keel_core.trade_scope import TradeScopeState, VenueTradeScope

    from keel.data.repository import Repository

    conn = connect(running.db_path)
    Repository(conn).upsert_venue_trade_scope(
        VenueTradeScope(
            venue="fake",
            state=TradeScopeState.CONFIRMED,
            attested_scope=None,
            attested_ts=None,
            confirmed_ts=1_700_000_000,
            refuted_ts=None,
            refuted_reason=None,
            credential_fingerprint=None,
        )
    )
    conn.commit()
    conn.close()

    _status, _headers, document = _json(running, "/api/venues")
    rows = {row["venue"]: row for row in document["data"]["readiness"]}
    assert rows["fake"]["state"]["value"] == "not_tradeable"
    # NEUTRAL, not warn/bad: an adapter that was never a trading venue is not a fault to fix.
    assert rows["fake"]["state"]["state"] == "neutral"
    assert rows["fake"]["next_step"] == "", "no advice is better than advice that cannot work"
    assert "scope attest" not in rows["fake"]["explanation"]


def test_setup_is_answerable_with_nothing_set_up(empty_machine: web_server.ServeConfig) -> None:
    """The one deployment-reading endpoint that must work when there is no deployment: it is the
    checklist that says how to make one. `needs_database`'s HTML counterpart serves this same page
    for the same reason."""
    status, _headers, document = _json(empty_machine, "/api/setup")

    assert status == 200
    assert document["engine"]["value"] == "stopped"
    assert document["data"]["is_new"]["value"] == "true"
    assert document["data"]["steps"]
    assert document["data"]["actions"]


def test_setup_carries_the_csrf_token_and_only_setup_does(
    empty_machine: web_server.ServeConfig,
) -> None:
    """**This test asserted the opposite until #540, and the reversal is recorded rather than
    silently applied.** It read: "a CSRF token authorises a WRITE, this issue ships reads only,
    and minting one into a read response would put a live write credential into every cached and
    logged copy of a GET."

    Two of those concerns were already answered -- `/api/*` is `no-store` and the service worker
    refuses to cache it, and the server binds loopback -- and the third does not survive the
    observation that this token authorises NOTHING without the session cookie. Anyone who can
    read this endpoint holds that cookie and can mint the same token. `payload.setup_payload`'s
    docstring carries the full argument.

    What is still asserted is the scope: the token appears on `/api/setup`, the one endpoint whose
    view performs writes, and on no other. An endpoint every view reads at boot -- `/api/config`
    -- would spread a live credential across every page load for no benefit."""
    _status, _headers, document = _json(empty_machine, "/api/setup")
    assert document["data"]["csrf"] == csrf_token(empty_machine.token)

    for path in ("/api/config", "/api/status", "/api/venues", "/api/gates"):
        _status, _headers, other = _json(empty_machine, path)
        assert csrf_token(empty_machine.token) not in json.dumps(other), path


def test_a_report_that_cannot_be_built_is_reported_not_swallowed(
    running: web_server.ServeConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken read is a 500 with a JSON body, never an HTML error page a `fetch()` client would
    fail to parse and never a 200 with empty data. `engine` reads `stopped` alongside it: from a
    client's point of view a report that cannot be built and an engine that is not there require
    the SAME behaviour -- show no figures -- and the difference is already carried by the status
    code and `error.detail`."""
    monkeypatch.setattr(
        web_api, "_status_report", _raise, raising=True
    )

    status, headers, document = _json(running, "/api/status")

    assert status == 500
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert document["data"] is None
    assert "RuntimeError" in document["error"]["detail"]


def _raise(*_args: Any, **_kwargs: Any) -> Any:
    raise RuntimeError("the ledger is on fire")


# -- headers and caching ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", API_ROUTES)
def test_no_api_response_may_be_cached(running: web_server.ServeConfig, path: str) -> None:
    """The design spec's service-worker table says `/api/*` is `NetworkOnly`, no exceptions,
    because "opening the app to last week's equity styled as current is worse than an error". This
    is the layer BELOW that promise: a `no-store` on the response means the HTTP cache cannot hold
    a balance either, whether or not a service worker is installed."""
    _status, headers, _document = _json(running, path)

    assert headers["Cache-Control"] == "no-store, max-age=0"


@pytest.mark.parametrize("path", API_ROUTES)
def test_json_is_served_with_nosniff_and_no_csp(
    running: web_server.ServeConfig, path: str
) -> None:
    """`nosniff` matters more here than on the HTML: a JSON body a browser is free to sniff as
    HTML is a stored-XSS primitive wearing a `Content-Type`. CSP is absent for the reason
    `_static_headers` already records -- it is a response header with no defined meaning outside a
    browsing context, and `application/json` is not one."""
    _status, headers, _document = _json(running, path)

    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert "Content-Security-Policy" not in headers


def test_head_returns_the_headers_and_no_body(running: web_server.ServeConfig) -> None:
    status, headers, body = _get(
        running, "/api/status", method="HEAD", cookie=_session(running)
    )

    assert status == 200
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert body == ""


# -- admission -------------------------------------------------------------------------------------


def test_a_read_needs_no_client_header(running: web_server.ServeConfig) -> None:
    """`X-Keel-Client` gates `POST /api/*` and deliberately not `GET`.

    The header buys one thing: it forces a CORS preflight a hostile origin cannot satisfy, closing
    the plain-form-POST gap that `SameSite=Strict` and the HMAC token both assume shut. A GET is
    not that gap -- a cross-origin read cannot see this response at all without CORS headers this
    server never sends, and `SameSite=Strict` denies the cookie to the cross-site request in the
    first place, so the read is refused at admission before any of this matters.

    Requiring it anyway would cost the thing §4 of the design philosophy is about: `curl
    http://127.0.0.1:8765/api/status` and a browser address bar are how an operator checks that
    the interface is telling the truth, and neither can set a header. Reverse this the day a GET
    can change something."""
    status, _headers, document = _json(running, "/api/status")

    assert status == 200
    assert document["data"] is not None


def test_a_read_without_the_session_cookie_is_refused_in_json(
    running: web_server.ServeConfig,
) -> None:
    """Same admission as every rendered page -- never weakened for being an API -- but the refusal
    speaks the caller's language. An HTML error page handed to `res.json()` is a parse error in
    the client, which is a worse diagnostic than the 403 it is hiding."""
    status, headers, body = _get(running, "/api/status")

    assert status == 403
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert json.loads(body)["error"]["status"] == "403"


def test_a_read_from_a_foreign_host_header_is_refused_in_json(
    running: web_server.ServeConfig,
) -> None:
    """DNS rebinding lands here exactly as it does for a page: the packet arrived on loopback, so
    the bind check passed, and only the header tells the truth about who the browser thinks it is
    talking to."""
    status, headers, body = _get(
        running, "/api/status", cookie=_session(running), host="keel.example.com"
    )

    assert status == 403
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert "Refused" in json.loads(body)["error"]["title"]


def test_a_navigation_still_refuses_in_something_a_person_can_read(
    running: web_server.ServeConfig,
) -> None:
    """The other half of the same statement: `/api/*` speaks JSON, and a NAVIGATION does not.

    This asserted `text/html` until #540, because `server._refuse` rendered an error page through
    `render.page`. There is no renderer now -- the server generates no markup at all -- so the
    refusal is plain text. The property is unchanged and is the one that matters: someone who
    opens `http://127.0.0.1:8765/` in a browser without the token gets the sentence telling them
    to use the URL keel printed, not a JSON envelope around it and not an empty page.

    #634 rewrote the sentence and this test with it. The old assertion was the literal opening
    words, which pinned the prose rather than the property; what is asserted now is that the text
    still does the two jobs the property is about. It must name the TOKEN, because "keel refused
    you" without saying what would not is a dead end -- and it must name an action that can be
    taken from where it is being read, because the old text's only instruction was to open the
    address keel printed, which is an instruction a window with no address bar cannot follow.
    That was the whole of #634's complaint about this page."""
    status, headers, body = _get(running, "/insights")

    assert status == 403
    assert headers["Content-Type"].startswith("text/plain")
    assert not body.lstrip().startswith("{"), "a navigation must not be refused in JSON"
    assert "token" in body
    assert "Paste" in body, (
        "the refusal names no action the reader can take from where they are -- see #634"
    )
    assert "<" not in body, "the refusal is markup again"


# -- the read surface answers no write ------------------------------------------------------------


@pytest.mark.parametrize("path", API_ROUTES)
def test_no_api_read_route_answers_a_post(running: web_server.ServeConfig, path: str) -> None:
    """Every READ endpoint is a 404 for a POST, reached only after the `X-Keel-Client` gate.

    #540 gave this server a write surface under `/api/` for the first time, at
    `API_SETUP_PREFIX`. This is the pin that keeps it there and only there: not one of the
    documents below became writable by the prefix acquiring a POST."""
    status, _headers, body = _get(
        running,
        path,
        method="POST",
        cookie=_session(running),
        headers={"X-Keel-Client": "1", "Content-Length": "0"},
    )

    assert status == 404, path
    assert json.loads(body)["error"]["status"] == "404"


def test_the_client_header_gate_still_guards_every_api_post(
    running: web_server.ServeConfig,
) -> None:
    """#535's third CSRF layer, still in front of `/api/*` writes and still refusing before the
    404 -- unchanged by this issue, and asserted here as well as in `test_server.py` because the
    route table under that prefix is no longer empty."""
    status, _headers, _body = _get(
        running, "/api/status", method="POST", cookie=_session(running),
        headers={"Content-Length": "0"},
    )

    assert status == 403


def test_the_handler_still_declares_exactly_three_verbs() -> None:
    verbs = {
        name
        for klass in web_server.KeelHandler.__mro__
        for name in vars(klass)
        if name.startswith("do_")
    }
    assert verbs == {"do_GET", "do_HEAD", "do_POST"}


# -- the routing layer computes nothing ------------------------------------------------------------


def test_the_api_module_is_inside_the_thinness_scan() -> None:
    """`test_console_thinness.py` globs `keel/web/*.py`, so the API layer is covered by Rules 1-5
    by construction rather than by anyone remembering to list it. Asserted here, from this side,
    because that file names its web modules explicitly for exactly this reason and a module that
    dropped out of the glob would leave the rules green over less code."""
    from tests.commands.test_console_thinness import _console_module_paths

    stems = {Path(p).stem for p in _console_module_paths()}

    assert "api" in stems


def test_rule_6_holds_in_the_api_layer_too() -> None:
    """Rule 6 is scoped by stem to the serialiser, which is right -- it is the module whose output
    is the money contract. But the routing layer sits directly on top of that output and re-parses
    it to sort, so the same five spellings of "a Decimal became a double" are reachable there:
    `float(row["value"])` as a sort key is the whole failure in one expression.

    Run here, over `keel/web/api.py`, rather than by adding a stem to `SERIALISER_STEMS`: the pin
    in that file is #533's contract and it stays as it was written, while this states the extra
    thing #534 needs. If the API layer ever needs an allowance, it gets one HERE, named."""
    from tests.commands.test_console_thinness import (
        _collect_aliases,
        _enclosing_functions,
        _rule6_findings,
    )

    source = Path(web_api.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    findings = _rule6_findings("api", tree, _collect_aliases(tree), _enclosing_functions(tree))

    assert findings.get("rule6_serialisation", []) == []


def test_the_routing_layer_formats_nothing() -> None:
    """Every displayable string in an API response was written by `payload.py`.

    Checked by shape rather than by reading: a `format(...)` call or an f-string carrying a format
    spec in the routing layer is how a second money renderer starts, and the second one is never
    the one with `_plain`'s no-exponent guarantee in it."""
    tree = ast.parse(Path(web_api.__file__).read_text(encoding="utf-8"))
    formatting = [
        node.lineno
        for node in ast.walk(tree)
        if _is_format_call(node)
        or (isinstance(node, ast.FormattedValue) and node.format_spec is not None)
    ]

    assert formatting == []


def _is_format_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "format"
    )
