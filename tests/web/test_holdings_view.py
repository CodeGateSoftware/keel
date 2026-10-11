"""The Holdings view -- what is held, what each tranche cost, and how it was entered (console PR2).

It replaced the Positions and Orders views. The service (`keel/commands/holdings.py`) decides
everything: the per-tranche cost, the tranche <-> entry-order join, the entry-gate verdict and the
slippage verdict. What is pinned HERE is (1) that the browser gets those decisions unaltered on the
wire -- the entry-gate and attestation chips, the stop that is absent rather than zero -- and (2)
the shape of the client, over its SOURCE TEXT (there is no JavaScript runtime in this suite):

* the view is wired in, and the two views it replaced are declared nowhere;
* it offers NO close, cancel or any other control of its own -- the only buttons on the page are
  the sort headers and the scope/status bars, which are built by `table()`/`scopeSwitch`/
  `statusSwitch` outside the region these pins scan;
* the stop column is drawn on the PRESENCE of a list (`stop_rows_present`), never by reading a
  `Field`'s `display` or `value`;
* a recent-orders read that failed is said so, and never rendered as an empty table.

Every client pin asserts a count or a pairing over a PARSED function body, never a bare substring a
declaration alone would satisfy (the lesson of #775).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from keel.commands.holdings import gather_holdings
from keel.config import load_config
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.web import api as web_api
from keel.web import payload as web_payload
from keel.web import staticfiles
from tests.conftest import VALID_CONFIG_YAML
from tests.web.test_client_assets import (
    _balanced_block,
    _code_only,
    _comments_only,
    _function_body,
    _table_calls,
)
from tests.web.test_view_sections import _decl

NOW_TS = 1_800_000_000

_JS = Path(staticfiles.__file__).parent / "static" / "js"
_INDEX = Path(staticfiles.__file__).parent / "static" / "index.html"


def _source(name: str) -> str:
    return (_JS / name).read_text(encoding="utf-8")


# -- the holdings payload, on the wire -----------------------------------------------------------


def _ledger(tmp_path: Path, *, tranche: dict[str, Any] | None = None) -> tuple[Repository, Any]:
    conn = connect(str(tmp_path / "keel.db"))
    migrate(conn)
    repo = Repository(conn)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(VALID_CONFIG_YAML, encoding="utf-8")
    fields: dict[str, Any] = {
        "product_id": "BTC-USD",
        "rule_name": "turtle_breakout",
        "opened_at": NOW_TS - 7200,
        "qty": "2",
        "entry_fill": "100050",
        "entry_fee": "1.20",
        "initial_stop": "90000",
    }
    fields.update(tranche or {})
    from decimal import Decimal

    for key in ("qty", "entry_fill", "entry_fee", "initial_stop"):
        if fields[key] is not None:
            fields[key] = Decimal(fields[key])
    repo.open_position(**fields)
    return repo, load_config(str(config_path))


def _holdings_row(tmp_path: Path, **kwargs: Any) -> dict[str, Any]:
    repo, config = _ledger(tmp_path, **kwargs)
    report = gather_holdings(repo, config, now_ts=NOW_TS)
    (row,) = web_payload.holdings_payload(report)["rows"]
    return row


def test_the_freshness_chip_carries_the_entry_gate_verdict(tmp_path: Path) -> None:
    """The gate outcome, not a data age. `missing`/`behind`/`unconfirmed` are the agent's own
    words for why it would refuse to trade this product right now, and a chip that showed hours
    since the last candle instead would answer a softer question than the engine asks."""
    row = _holdings_row(tmp_path)

    assert row["freshness"]["value"] in ("ready", "missing", "behind", "unconfirmed")
    assert row["freshness"]["state"] in ("good", "warn", "bad", "neutral", "unknown")
    assert row["freshness"]["display"], "the chip must say something a human can read"


def test_a_product_with_no_candles_is_a_warning_not_a_neutral_note(tmp_path: Path) -> None:
    """A product the entry gate would refuse is a fact about whether keel can act, so it is
    styled as one. The ledger here holds no candles at all, so the gate cannot be ready."""
    row = _holdings_row(tmp_path)

    assert row["freshness"]["value"] != "ready", "the premise: this ledger has no price series"
    assert row["freshness"]["state"] == "warn"


def test_a_tranche_with_no_recorded_stop_says_so_and_is_not_listed_as_stopped(
    tmp_path: Path,
) -> None:
    """The stop is ABSENT, never a zero, and the list the view draws its stop column from does not
    name a tranche that has none."""
    repo, config = _ledger(tmp_path, tranche={"initial_stop": None})
    document = web_payload.holdings_payload(gather_holdings(repo, config, now_ts=NOW_TS))

    assert document["rows"][0]["stop"]["state"] == "unknown"
    assert document["stop_rows_present"] == []


def test_a_tranche_with_a_stop_is_listed_so_the_view_can_draw_the_column(tmp_path: Path) -> None:
    repo, config = _ledger(tmp_path)
    document = web_payload.holdings_payload(gather_holdings(repo, config, now_ts=NOW_TS))

    assert document["stop_rows_present"] == [document["rows"][0]["id"]]


# -- the attestation chip on the wire (#701) -----------------------------------------------------


def _attested_row(tmp_path: Path, *, due: Any) -> dict[str, Any]:
    repo, config = _ledger(tmp_path)
    if due is not ...:
        repo.upsert_asset_attestation(
            asset="BTC",
            sector="tech",
            backing="native",
            pays_yield=False,
            source="prospectus",
            attested_by="operator",
            attested_at=NOW_TS - 86_400,
            attest_due_ts=due,
        )
    report = gather_holdings(repo, config, now_ts=NOW_TS)
    (row,) = web_payload.holdings_payload(report)["rows"]
    return row


def test_an_expired_attestation_warns_on_the_wire(tmp_path: Path) -> None:
    chip = _attested_row(tmp_path, due=NOW_TS - 10)["attestation"]

    assert chip["value"] == "expired"
    assert chip["state"] == "warn"
    assert "EXPIRED" in chip["display"]


def test_an_absent_attestation_is_unknown_and_not_a_failure(tmp_path: Path) -> None:
    """`screen_asset` already rejects an unclassified asset, so the holding is gated. The page
    reports a missing claim rather than grading the operator for it."""
    chip = _attested_row(tmp_path, due=...)["attestation"]

    assert chip["value"] == "unattested"
    assert chip["state"] == "unknown"


def test_no_attestation_state_is_ever_bad(tmp_path: Path) -> None:
    """WARN and never FAIL, which is doctor's own choice for this column and for the same reason:
    an expired window vetoes nothing (#718 left that to a human), and a `bad` chip would tell an
    operator the opposite."""
    for index, due in enumerate((..., None, NOW_TS - 10, NOW_TS + 86_400, NOW_TS + 400 * 86_400)):
        home = tmp_path / str(index)
        home.mkdir()
        assert _attested_row(home, due=due)["attestation"]["state"] != "bad", due


def test_the_window_crosses_as_a_moment_not_an_epoch(tmp_path: Path) -> None:
    row = _attested_row(tmp_path, due=NOW_TS + 86_400)

    assert row["attest_due_at"]["display"]
    assert isinstance(row["attest_due_at"]["value"], str)


def test_the_attestation_chip_is_separate_from_the_freshness_chip(tmp_path: Path) -> None:
    """The two disagree in both directions -- a perfectly attested asset with a cold series, or a
    freshly priced one with a lapsed claim -- and a reader has to be able to tell which is which."""
    row = _attested_row(tmp_path, due=NOW_TS - 10)

    assert row["attestation"]["value"] == "expired"
    assert row["freshness"]["value"] != row["attestation"]["value"]
    assert "freshness" in row


# -- the view, in the client ---------------------------------------------------------------------
#
# The region: `holdingsView` and the section builders it calls. `recentOrdersSection` and
# `holdingsSection` are private and sit BELOW the export, so `_view_keys`' region (the export to
# the next export) contains them; `_function_body` bounds each at the next top-level function.

_HOLDINGS_BUILDERS = ("holdingsView", "holdingsSection", "recentOrdersSection", "emptyOrders")


def _builder(name: str) -> str:
    """One function's body, comments stripped and string literals KEPT -- the labels and the words
    these pins look for are string literals, which `_code_only` would blank."""
    return _function_body(_source("render.js"), name)


def _region() -> str:
    """Every function the Holdings view is made of, as one text.

    Excludes `statusSwitch` and `scopeSwitch`, which legitimately build buttons and are called
    from the region rather than part of it, and excludes `table()`, which builds the sort headers.
    """
    declared = {
        name: len(re.findall(rf"^(?:export )?function {name}\(", _source("render.js"), re.M))
        for name in _HOLDINGS_BUILDERS
    }
    assert declared == dict.fromkeys(_HOLDINGS_BUILDERS, 1), declared
    return "\n".join(_builder(name) for name in _HOLDINGS_BUILDERS)


def _table_labels(body: str) -> list[str]:
    """The header labels of the FIRST `table(` call in `body`, in declared order."""
    at = body.index("table(")
    columns_at = body.index("[", at)
    block, _after = _balanced_block(body, columns_at)
    return re.findall(r'label:\s*"([^"]+)"', block)


def _table_keys(body: str) -> list[str]:
    at = body.index("table(")
    block, _after = _balanced_block(body, body.index("[", at))
    return re.findall(r'key:\s*"([^"]+)"', block)


def test_the_holdings_view_is_wired_into_the_client_router() -> None:
    """A view in `main.js` alone routes on a click and 404s on a reload; a route in Python alone is
    a page with nothing to render. Both tables, the nav and the renderer, or none of them."""
    main = _comments_only(_source("main.js"))

    assert "holdings" in staticfiles.CLIENT_ROUTES
    assert _source("render.js").count("export function holdingsView(") == 1
    # The branch RETURNS the view, rather than merely mentioning both words.
    branch = re.search(r'if \(route\.name === "holdings"\) \{(.*?)\n  \}', main, re.S)
    assert branch is not None, 'main.js has no `route.name === "holdings"` branch'
    assert "return holdingsView(" in branch.group(1)
    # The route reads its own endpoint first and the orders endpoint second.
    entry = re.search(r'\{ name: "holdings", label: "Holdings", endpoints: \[([^\]]*)\] \}', main)
    assert entry is not None
    assert [part.strip() for part in entry.group(1).split(",")] == ['"holdings"', '"orders"']


def test_the_nav_runs_pulse_setup_holdings_in_that_order() -> None:
    hrefs = re.findall(r'<li><a href="([^"]+)">', _INDEX.read_text(encoding="utf-8"))

    assert hrefs[:3] == ["/pulse", "/setup", "/holdings"]
    assert staticfiles.CLIENT_ROUTES[:3] == ("pulse", "setup", "holdings")
    assert '<li><a href="/holdings">Holdings</a></li>' in _INDEX.read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["orders", "positions"])
def test_the_two_retired_views_are_gone_from_every_table(name: str) -> None:
    """Retired, not removed: the name still serves the shell (so a bookmark lands on Pulse) but is
    in no live table, and the view functions that drew them are declared nowhere."""
    main = _comments_only(_source("main.js"))
    routes = main[main.index("const ROUTES = [") : main.index("];", main.index("const ROUTES"))]
    hrefs = re.findall(r'<li><a href="([^"]+)">', _INDEX.read_text(encoding="utf-8"))

    assert name not in staticfiles.CLIENT_ROUTES
    assert name in staticfiles.RETIRED_CLIENT_ROUTES
    assert f'name: "{name}"' not in routes
    assert f"/{name}" not in hrefs
    assert f'route.name === "{name}"' not in main


@pytest.mark.parametrize(
    "name", ["positionsView", "ordersView", "cancelCell", "openCancelHelp", "orderDetail"]
)
def test_the_views_and_helpers_the_cancel_flow_needed_are_declared_nowhere(name: str) -> None:
    for source in ("render.js", "main.js"):
        code = _code_only(_source(source))
        assert not re.search(rf"\bfunction {name}\b", code), (source, name)
        assert not re.search(rf"\b{name}\b", code), (source, name)


def test_the_region_is_what_the_key_scan_scans() -> None:
    """The premise of the pins below: `holdingsSection` and `recentOrdersSection` sit between the
    export and the NEXT export, so `_view_keys` (and `_row_reads`) cover them too."""
    code = _code_only(_source("render.js"))
    start = code.index("export function holdingsView(")
    end = code.find("\nexport function ", start + 1)
    raw = code[start : end if end != -1 else len(code)]

    assert "\nfunction holdingsSection(" in raw
    assert "\nfunction recentOrdersSection(" in raw


def test_the_holdings_view_has_no_close_or_cancel_action_anywhere() -> None:
    """#701's refusal ("no close button, ever") and #707's (the console cannot cancel), pinned as
    STRUCTURE over the region rather than trusted.

    A position is closed through the typed-phrase friction of the exit path and an order is
    cancelled at a terminal; either arriving here later would look like an obvious convenience.
    The words are ones the legitimate code never contains; the countable constructs are what a
    control is made of. The sort headers and the scope/status bars are built outside the region
    (`table()`, `scopeSwitch`, `statusSwitch`), so the region owns no button at all."""
    region = _region()
    lowered = region.lower()

    for banned in ("close", "cancel", "liquidate", "sell", "exit", "fetch("):
        assert banned not in lowered, f"the Holdings view must offer no {banned!r}"
    assert region.count('el("button"') == 0
    assert region.count("addEventListener") == 0
    assert region.count('el("form"') == 0

    whole = _comments_only(_source("render.js")).lower()
    assert "cancelhelp" not in whole
    assert "showmodal" not in whole


def test_the_holdings_view_groups_by_the_reports_own_rule_list() -> None:
    """`data.rules`, not a set the client assembles from the rows: two answers to "which rules
    hold tranches" is one too many, and the report already carries the ordered one."""
    region = _region()
    section = _builder("holdingsSection")

    assert section.count("data.rules") == 1
    assert re.search(r"for \(const \w+ of data\.rules[^)]*\)", section)
    assert "new Set(" not in region
    assert "Object.keys(" not in region


def test_each_rule_gets_one_heading_and_one_table_for_its_own_rows() -> None:
    section = _builder("holdingsSection")

    assert section.count("heading(") == 1
    assert re.findall(r'const id = \[("h-hold"), \w+\]\.join\("-"\);', section) == ['"h-hold"']
    assert section.count("table(") == 1
    # The rows are filtered by the grouping key's equality with a bare string, as positionsView
    # did with the product: a key comparison, not a judgement of any value.
    assert re.findall(r"rows\.filter\(.*?\(row\) => (row\.rule_name === \w+)\)", section) == [
        "row.rule_name === rule"
    ]
    # And the heading names the rule, or says it is unattributed -- never a blank heading.
    assert 'plain(rule) || "unattributed"' in section


def test_an_empty_book_says_so_in_a_sentence_before_any_table() -> None:
    section = _builder("holdingsSection")
    empty = section.index("rows.length === 0")

    assert empty < section.index("table(")
    guard = section[empty : section.index("return", empty) + len("return")]
    assert 'el("p", "empty", "No open tranches' in guard


def test_tranche_rows_carry_the_columns_the_view_exists_for() -> None:
    """Parsed from the table call, not searched for: the labels as a LIST, each of the named ones
    exactly once, and none of the mark-derived ones the console dropped."""
    labels = _table_labels(_builder("holdingsSection"))

    for wanted in (
        "entry gate",
        "attestation",
        "placement",
        "expected",
        "actual",
        "slippage",
        "cost",
        "bracket",
    ):
        assert labels.count(wanted) == 1, (wanted, labels)
    for banned in ("mark", "value", "unrealized", "to stop", "to stop %"):
        assert banned not in labels, (banned, labels)
    # The ledger's own order, so the verdict columns sit together at the end of the row.
    assert labels == [
        "tranche",
        "opened (UTC)",
        "product",
        "qty",
        "entry",
        "fee",
        "cost",
        "stop",
        "bracket",
        "entry gate",
        "attestation",
        "placement",
        "expected",
        "actual",
        "slippage",
    ]


def test_the_tranche_table_is_seen_by_the_header_cell_pairing_scan() -> None:
    """`test_every_table_emits_one_cell_per_declared_header` pairs headers with cells by position;
    that only protects this table if the scan FINDS it. Fifteen headers, fifteen cells."""
    pairs = _table_calls(_code_only(_builder("holdingsSection")))

    assert pairs == [(15, 15)]
    orders = _table_calls(_code_only(_builder("recentOrdersSection")))
    assert orders == [(9, 9)]


def _split_top_level(block: str) -> list[str]:
    """The items of an array literal's body, split on commas at depth zero (strings respected)."""
    inner = block.strip()[1:-1]
    items: list[str] = []
    depth = 0
    quote = ""
    current = ""
    for char in inner:
        if quote:
            current += char
            if char == quote:
                quote = ""
            continue
        if char in "\"'`":
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            items.append(current.strip())
            current = ""
            continue
        current += char
    if current.strip():
        items.append(current.strip())
    return items


def _flatten_splice(item: str) -> str:
    """`...(withStop ? [X] : [])` -> `X`; any other item is returned unchanged."""
    match = re.fullmatch(r"\.\.\.\(withStop \? \[(.*)\] : \[\]\)", item, re.S)
    return match.group(1).strip() if match else item


def _paired(body: str) -> list[tuple[str, str]]:
    """`(header label, cell expression)` for the FIRST `table(` call in `body`, by position, with
    the `withStop` header splice and its cell splice flattened (both must sit at the same index)."""
    at = body.index("table(")
    columns, after = _balanced_block(body, body.index("[", at))
    cells_at = body.index("[", body.index(".map(", after))
    cells, _ = _balanced_block(body, cells_at)
    header_items = _split_top_level(columns)
    cell_items = _split_top_level(cells)
    assert [i.startswith("...") for i in header_items] == [
        i.startswith("...") for i in cell_items
    ], "the header splice and the cell splice must sit at the same index"
    labels = [re.search(r'label:\s*"([^"]+)"', _flatten_splice(h)).group(1) for h in header_items]  # type: ignore[union-attr]
    return list(zip(labels, (_flatten_splice(c) for c in cell_items), strict=True))


def _reads_own_field(cell: str, field: str) -> bool:
    """The cell reads `row.<field>` and no other `row.` field."""
    return re.findall(r"row\.(\w+)", cell) == [field]


_TRANCHE_PAIRS = (
    ("tranche", "id"),
    ("opened (UTC)", "opened_at"),
    ("product", "product_id"),
    ("qty", "qty"),
    ("entry", "entry_fill"),
    ("fee", "entry_fee"),
    ("cost", "cost"),
    ("stop", "stop"),
    ("bracket", "bracket"),
    ("entry gate", "freshness"),
    ("attestation", "attestation"),
    ("placement", "placement"),
    ("expected", "expected_fill"),
    ("actual", "actual_fill"),
    ("slippage", "fill_divergence"),
)

_ORDER_PAIRS = (
    ("placement", "placement"),
    ("rule", "rule_name"),
    ("side", "side"),
    ("product", "product_id"),
    ("status", "status"),
    ("expected", "expected_fill"),
    ("actual", "actual_fill"),
    ("slippage", "fill_divergence"),
    ("placed (UTC)", "created_at"),
)


def test_each_tranche_header_sits_over_its_own_cell() -> None:
    """Counts match is not pairing: swapping `row.expected_fill` and `row.actual_fill` kept 14/14
    and drew a favourable fill as adverse beside the server's slippage verdict."""
    pairs = _paired(_builder("holdingsSection"))

    assert [label for label, _ in pairs] == [label for label, _ in _TRANCHE_PAIRS]
    for (label, cell), (_, field) in zip(pairs, _TRANCHE_PAIRS, strict=True):
        assert _reads_own_field(cell, field), (label, cell, field)


def test_each_recent_orders_header_sits_over_its_own_cell() -> None:
    pairs = _paired(_builder("recentOrdersSection"))

    assert [label for label, _ in pairs] == [label for label, _ in _ORDER_PAIRS]
    for (label, cell), (_, field) in zip(pairs, _ORDER_PAIRS, strict=True):
        assert _reads_own_field(cell, field), (label, cell, field)


def test_the_realized_legs_summary_names_the_tranche_and_its_product() -> None:
    """The disclosures sit after a table that may hold several products (every DCA rule is named
    "dca"), so "tranche N" alone cannot be tied to a row: the summary appends id AND product."""
    section = _builder("holdingsSection")
    at = section.index('summary.append(')
    call, _ = _balanced_block(section, section.index("(", at))
    parts = _split_top_level("[" + call[1:-1] + "]")

    assert sum("row.id" in part for part in parts) == 1, parts
    assert sum("row.product_id" in part for part in parts) == 1, parts
    assert any("realized legs" in part for part in parts), parts


def test_the_tranche_sort_keys_are_ones_the_endpoint_declares() -> None:
    declared = set(web_api.API_ROUTES["/api/holdings"].sortable)
    keys = _table_keys(_builder("holdingsSection"))

    assert keys, "the key scan found nothing"
    assert set(keys) <= declared, sorted(set(keys) - declared)
    assert keys.count("cost") == 1 and keys.count("fill_divergence") == 1


def test_the_stop_column_is_drawn_only_when_the_report_lists_a_stop() -> None:
    """The branch is the EMPTINESS of a list the payload sends, exactly how `why_not` is branched
    on. The client never reads a `Field`'s `display` or `value` to decide it.

    Kills: splicing the column unconditionally (the guard is read once and gates BOTH the header
    and the cell), and deciding it from a row's `stop`."""
    region = _region()
    section = _builder("holdingsSection")

    assert region.count("stop_rows_present") == 1
    guards = re.findall(
        r"const (\w+) = \(data\.stop_rows_present \|\| \[\]\)\.length !== 0;", section
    )
    assert len(guards) == 1, guards
    guard = guards[0]
    # Two splices, one in the headers and one in the cells, and nothing else uses the guard.
    spliced = re.findall(rf"\.\.\.\({guard} \? \[(.*?)\] : \[\]\)", section, re.S)
    assert len(spliced) == 2, spliced
    assert section.count(guard) == 3  # the declaration and the two splices
    labels = [part for part in spliced if 'label: "stop"' in part]
    cells = [part for part in spliced if "row.stop" in part]
    assert len(labels) == 1 and len(cells) == 1, spliced
    assert labels != cells
    # The header text and the cell value occur exactly once each, inside the guarded splices.
    assert section.count('label: "stop"') == 1
    assert section.count("row.stop") == 1
    assert ".value" not in region and ".display" not in region


def test_the_realized_legs_sit_under_a_disclosure_and_name_no_mark() -> None:
    section = _builder("holdingsSection")

    assert section.count('el("details", "row")') == 1
    for key in ("realized_qty", "realized_proceeds", "realized_fees"):
        assert section.count("row." + key) == 1, key
    assert "marked at" not in section and "mark_at" not in section


def test_the_row_keys_the_view_reads_are_keys_a_tranche_row_sends() -> None:
    """`_row_reads` covers only what a `.map()` reads; the realized-legs loop is a `for`, so its
    reads are checked here against the payload's own row keys."""
    from tests.web.test_payload import _HOLDING_ROW_KEYS

    reads = set(re.findall(r"\brow\.(\w+)", _builder("holdingsSection")))

    assert reads, "the scan found no row reads"
    assert reads <= set(_HOLDING_ROW_KEYS), sorted(reads - set(_HOLDING_ROW_KEYS))


def test_holdings_view_places_every_section_once() -> None:
    """Every section builder is called exactly once, and the set is DERIVED from the source.

    A hand-listed tuple drops a section silently (`pulseRules` once was missing from one, so
    deleting its call passed the suite): the expected names are every `*Section` declaration in the
    view's region, and what `holdingsView` calls must equal that set, in declaration order."""
    code = _comments_only(_source("render.js"))
    start = code.index("export function holdingsView(")
    end = code.find("\nexport function ", start + 1)
    declared = re.findall(r"^function (\w+Section)\(", code[start:end], re.M)
    body = _builder("holdingsView")
    # `fragment.append(el("h1", ...))` is a heading, not a section: only `*Section` builders count.
    called = [n for n in re.findall(r"fragment\.append\((\w+)\(", body) if n.endswith("Section")]

    assert declared == ["holdingsSection", "recentOrdersSection"], declared
    assert called == declared, called
    for name in declared:
        assert len(re.findall(rf"\b{name}\(", body)) == 1, name


def test_recent_orders_are_a_compact_table_with_no_cancel_or_fee_column() -> None:
    labels = _table_labels(_builder("recentOrdersSection"))

    assert labels == [
        "placement",
        "rule",
        "side",
        "product",
        "status",
        "expected",
        "actual",
        "slippage",
        "placed (UTC)",
    ]


def test_the_recent_orders_sort_keys_are_ones_the_orders_endpoint_declares() -> None:
    declared = set(web_api.API_ROUTES["/api/orders"].sortable)
    keys = _table_keys(_builder("recentOrdersSection"))

    assert keys, "the key scan found nothing"
    assert set(keys) <= declared, sorted(set(keys) - declared)
    assert keys[0] == "placement"


def test_a_failed_orders_read_is_said_in_a_sentence_and_draws_nothing_else() -> None:
    """The secondary read can fail while the primary stands. An empty table there would read as
    "keel placed no orders", a claim nobody made, so the section's first act is to check for the
    missing payload, say so under the heading, and return before any control or table is built.

    Kills: removing the guard (the next line reads `orders.write_posture` of null and throws,
    blanking the whole Holdings page)."""
    body = _builder("recentOrdersSection")
    guard = re.search(r"if \(!orders\) \{(.*?)\n  \}", body, re.S)

    assert guard is not None, "the missing-orders guard is gone"
    assert body.index("heading(") < guard.start(), "the heading is drawn before the guard"
    block = guard.group(1)
    assert re.search(r'el\(\s*"p",\s*"empty",', block) and "could not be read" in block
    assert block.rstrip().endswith("return fragment;")
    # The guard precedes everything that reads the payload or builds a control.
    for later in ("table(", "scopeSwitch(", "statusSwitch(", "emptyOrders("):
        assert later in body
        assert body.index(later) > guard.end(), later
    assert "orders." not in body[: guard.start()]


def test_the_orders_section_has_its_switches_and_its_one_empty_sentence() -> None:
    body = _builder("recentOrdersSection")

    assert body.count("scopeSwitch(") == 1 and '"Orders scope"' in body
    assert body.count("statusSwitch(") == 1
    # Guarded, so a caller that has not wired the tabs renders the section unchanged.
    assert re.search(r"if \(onStatus\) \{\s*fragment\.append\(statusSwitch\(", body)
    assert body.count("emptyOrders(") == 1
    # An empty book answers with the specific sentence and RETURNS, so the section never also draws
    # a bare empty table beside it.
    assert re.search(
        r"if \(placed\.length === 0\) \{\s*fragment\.append\(emptyOrders\(orders\)\);"
        r"\s*return fragment;\s*\}",
        body,
    )
    assert body.index("emptyOrders(") < body.index("table(")
    assert 'heading("h-orders", "Recent orders by keel")' in body


def test_the_orders_table_names_the_rule_that_placed_each_order() -> None:
    """`rule_id` alone is a foreign key -- a number a reader cannot act on. The rule's NAME is a
    column, and it is positional: the cell sits where the header does."""
    body = _builder("recentOrdersSection")
    labels = _table_labels(body)

    assert labels.index("rule") == 1
    assert body.count("row.rule_name") == 1


def test_the_stylesheet_no_longer_carries_the_cancel_modal() -> None:
    css = (_JS.parent / "css" / "keel.css").read_text(encoding="utf-8")

    assert ".cancelhelp" not in css
    assert ".linklike" not in css
    # `.invocation` is NOT the cancel modal's: the research matrix wraps a command in it.
    assert ".invocation" in css
    assert css.count(".invocation") >= 1
    assert re.search(r'el\("\w+", "invocation"', _comments_only(_source("render.js")))


def test_the_helpers_it_keeps_are_still_declared_exactly_once() -> None:
    code = _comments_only(_source("render.js"))
    for name in ("statusSwitch", "scopeSwitch", "emptyOrders"):
        assert len(re.findall(rf"^function {name}\(", code, re.M)) == 1, name
    assert _decl(code, "SCOPES").startswith("const SCOPES")
