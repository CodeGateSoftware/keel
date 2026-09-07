"""The positions view, on the wire -- issue #701.

The service (`keel/commands/positions.py`) decides everything: the mark, the P&L, the stop
distance and the entry-gate verdict. What is pinned HERE is that the browser gets those
decisions unaltered -- no figure derived in this layer, no absence rendered as a zero, and no
JSON number anywhere.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from keel.commands.positions import gather_positions
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.web import payload as web_payload

POS_NOW_TS = 1_800_000_000


def _walk(node: Any, path: str = "$") -> list[tuple[str, Any]]:
    """Every leaf in a parsed JSON document, with the path that reaches it."""
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


# -- the positions payload (#701) ---------------------------------------------------------------


def _positions_report(tmp_path: Path, **overrides: Any) -> Any:
    from tests.commands.test_positions import _config, _mark, _open_tranche

    conn = connect(str(tmp_path / "positions.db"))
    migrate(conn)
    repo = Repository(conn)
    _open_tranche(repo, **overrides.pop("tranche", {}))
    if "mark" in overrides:
        mark = overrides.pop("mark")
        if mark is not None:
            _mark(repo, mark)
    else:
        _mark(repo, "150")
    return gather_positions(repo, _config(tmp_path), now_ts=POS_NOW_TS)


def test_no_wire_value_in_the_positions_payload_is_ever_a_json_number(tmp_path: Path) -> None:
    """#533's contract, over this payload. Positions carry more money per row than any other
    view -- entry, fee, mark, market value, unrealized, stop, realized -- so it is the one most
    likely to leak a float."""
    document = json.loads(
        json.dumps(web_payload.positions_payload(_positions_report(tmp_path)))
    )
    numbers = [
        path
        for path, leaf in _walk(document)
        if not isinstance(leaf, bool) and isinstance(leaf, (int, float))
    ]
    assert numbers == [], numbers


def test_the_unrealized_leg_carries_its_verdict_and_the_balances_do_not(tmp_path: Path) -> None:
    """Rule 3, applied per figure. Unrealized P&L is a gain-or-loss and gets the glyph and the
    state; a market value is a magnitude -- an account is not "good" for being worth something,
    and a green number beside every held position would make the one that matters invisible."""
    row = web_payload.positions_payload(_positions_report(tmp_path))["rows"][0]

    assert row["unrealized"]["state"] == "good"
    assert row["unrealized"]["display"].startswith("\u25b2")
    assert row["market_value"]["state"] == "neutral"
    assert row["entry_fill"]["state"] == "neutral"


def test_a_losing_tranche_reads_as_bad_without_the_client_seeing_a_minus(
    tmp_path: Path,
) -> None:
    row = web_payload.positions_payload(_positions_report(tmp_path, mark="80"))["rows"][0]

    assert row["unrealized"]["state"] == "bad"


def test_an_unmarked_tranche_crosses_as_absent_not_as_zero(tmp_path: Path) -> None:
    """The distinction the service drew, surviving serialisation. `$0.00` market value would
    render a held position as a total loss on the strength of a missing candle."""
    row = web_payload.positions_payload(_positions_report(tmp_path, mark=None))["rows"][0]

    assert row["mark"]["state"] == "unknown"
    assert row["market_value"]["state"] == "unknown"
    assert row["unrealized"]["state"] == "unknown"
    # The entry side is still known -- it was recorded when the tranche opened.
    assert row["entry_fill"]["value"] == "100"


def test_the_stop_distance_crosses_as_both_a_price_and_a_fraction(tmp_path: Path) -> None:
    """Two questions, two fields. The fraction crosses UNRESCALED and with no `%`, the same
    posture `ratio` documents for the drawdown scalars -- multiplying by 100 here would be the
    serialiser inventing a figure the report never held."""
    row = web_payload.positions_payload(_positions_report(tmp_path))["rows"][0]

    assert row["stop_distance"]["value"] == "60"
    assert row["stop_distance_pct"]["value"] == "0.4"
    assert "%" not in json.dumps(row)


def test_a_tranche_through_its_stop_is_judged_bad(tmp_path: Path) -> None:
    """The state an operator most needs to find by scanning. Below the stop the distance is
    negative, and the sign is turned into a verdict HERE so no client reads a minus."""
    row = web_payload.positions_payload(_positions_report(tmp_path, mark="85"))["rows"][0]

    assert row["stop_distance"]["state"] == "bad"


def test_a_tranche_with_no_recorded_stop_says_so_rather_than_showing_zero(
    tmp_path: Path,
) -> None:
    report = _positions_report(tmp_path, tranche={"initial_stop": None})
    row = web_payload.positions_payload(report)["rows"][0]

    assert row["initial_stop"]["state"] == "unknown"
    assert row["stop_distance"]["state"] == "unknown"


def test_the_freshness_chip_carries_the_entry_gate_verdict(tmp_path: Path) -> None:
    """The gate outcome, not a data age. `missing`/`behind`/`unconfirmed` are the agent's own
    words for why it would refuse to trade this product right now, and a chip that showed hours
    since the last candle instead would answer a softer question than the engine asks."""
    row = web_payload.positions_payload(_positions_report(tmp_path))["rows"][0]

    assert row["freshness"]["value"] in ("ready", "missing", "behind", "unconfirmed")
    assert row["freshness"]["state"] in ("good", "warn", "bad", "neutral", "unknown")
    assert row["freshness"]["display"], "the chip must say something a human can read"


def test_an_unready_product_is_a_warning_not_a_neutral_note(tmp_path: Path) -> None:
    """A product the entry gate would refuse is a fact about whether keel can act, so it is
    styled as one. Neutral would leave it indistinguishable from an ordinary row."""
    report = _positions_report(tmp_path, mark=None)
    row = web_payload.positions_payload(report)["rows"][0]

    assert row["freshness"]["value"] == "missing"
    assert row["freshness"]["state"] == "warn"


def test_the_report_counts_and_products_cross_from_the_report(tmp_path: Path) -> None:
    """Rule 6e: `payload.py` may not call `len()`, so both come off the report."""
    built = web_payload.positions_payload(_positions_report(tmp_path))

    assert built["open_count"]["value"] == "1"
    assert built["products"] == ["BTC-USD"]


# -- the view, in the client ----------------------------------------------------------------------
#
# Source-text assertions: there is no JavaScript runtime in this suite, so what is pinned is that
# the declarations exist and read the right keys -- not that the DOM behaves.


def _source(name: str) -> str:
    from keel.web import staticfiles

    return (Path(staticfiles.__file__).parent / "static" / "js" / name).read_text(encoding="utf-8")


def _code(name: str) -> str:
    from tests.web.test_client_assets import _comments_only

    return _comments_only(_source(name))


def _function_body(name: str, function: str) -> str:
    """The source of ONE exported function, ending where the next one begins.

    A fixed `[:4000]` slice ran 1362 characters past `positionsView` into `ordersView`, which
    made the no-close-action assertion below partly a statement about a different view: it would
    have started failing, or passing, on edits to code it does not describe.
    """
    after = _code(name).split("export function " + function)[1]
    end = after.find("export function ")
    return after if end == -1 else after[:end]


def test_the_positions_view_is_wired_into_the_client_router() -> None:
    """A view in `main.js` alone routes on a click and 404s on a reload; a route in Python alone
    is a page with nothing to render. Both tables and the renderer, or none of them."""
    from keel.web import staticfiles

    assert "positions" in staticfiles.CLIENT_ROUTES
    main_code = _code("main.js")
    assert "positionsView" in main_code
    assert 'route.name === "positions"' in main_code
    assert "export function positionsView" in _code("render.js")


def test_the_positions_view_has_no_close_action_anywhere() -> None:
    """#701's central refusal, pinned rather than trusted.

    "No close button. Ever." A position is closed through the typed-phrase friction of the exit
    path, because a panic tap on a table row must never be the last line of defence. This is the
    kind of affordance that arrives later as an obvious convenience, so the absence is asserted
    on the source and will fail the build the day someone adds one.
    """
    view = _function_body("render.js", "positionsView")
    for banned in ("close", "sell", "exit", "cancel", "liquidate"):
        assert banned not in view.lower(), f"the positions view must offer no {banned} action"


def test_the_positions_view_groups_by_the_reports_own_product_list() -> None:
    """`data.products`, not a list the client assembles. Two answers to "which products does this
    book hold" is one too many, and the report already holds the ordered one."""
    assert "data.products" in _code("render.js")


def test_the_positions_view_shows_the_freshness_chip_per_row() -> None:
    """The entry-gate verdict, on the page. It is the thing that explains an idle deployment, so
    a view that carried every figure and not this one would leave the most common question
    unanswered.

    PER ROW, not per product: the gate granularity comes from the RULE that opened the tranche,
    so one product holding two tranches from rules on different timeframes has two verdicts. A
    chip read off the first row and captioned for the whole product would state one tranche's
    verdict over another's."""
    view = _function_body("render.js", "positionsView")

    assert '"freshness"' in view, "the entry-gate verdict must be a column of the table"
    assert "held[0]" not in view, (
        "a per-product chip read off one row cannot represent tranches whose rules gate on "
        "different granularities"
    )


def test_the_positions_view_names_the_stop_distance_both_ways() -> None:
    view = _function_body("render.js", "positionsView")
    assert "stop_distance" in view
    assert "stop_distance_pct" in view


# -- the attestation chip on the wire (#701) ------------------------------------------------------


def _payload_mod():
    from keel.web import payload as payload_mod

    return payload_mod


def _attested_report(tmp_path: Path, *, due, **overrides):
    from tests.commands.test_positions import NOW_TS as POS_NOW
    from tests.commands.test_positions import _config, _mark, _open_tranche

    conn = connect(str(tmp_path / "attest.db"))
    migrate(conn)
    repo = Repository(conn)
    _open_tranche(repo)
    _mark(repo, "150")
    if due is not ...:
        repo.upsert_asset_attestation(
            asset="BTC",
            sector="tech",
            backing="native",
            pays_yield=False,
            source="prospectus",
            attested_by="operator",
            attested_at=POS_NOW - 86_400,
            attest_due_ts=due,
        )
    return gather_positions(repo, _config(tmp_path), now_ts=POS_NOW)


def test_an_expired_attestation_warns_on_the_wire(tmp_path: Path) -> None:
    from tests.commands.test_positions import NOW_TS as POS_NOW

    body = _payload_mod().positions_payload(_attested_report(tmp_path, due=POS_NOW - 10))
    chip = body["rows"][0]["attestation"]

    assert chip["value"] == "expired"
    assert chip["state"] == "warn"
    assert "EXPIRED" in chip["display"]


def test_an_absent_attestation_is_unknown_and_not_a_failure(tmp_path: Path) -> None:
    """`screen_asset` already rejects an unclassified asset, so the holding is gated. The page
    reports a missing claim rather than grading the operator for it."""
    body = _payload_mod().positions_payload(_attested_report(tmp_path, due=...))
    chip = body["rows"][0]["attestation"]

    assert chip["value"] == "unattested"
    assert chip["state"] == "unknown"


def test_no_attestation_state_is_ever_bad(tmp_path: Path) -> None:
    """WARN and never FAIL, which is doctor's own choice for this column and for the same reason:
    an expired window vetoes nothing (#718 left that to a human), and a `bad` chip would tell an
    operator the opposite."""
    from tests.commands.test_positions import NOW_TS as POS_NOW

    for due in (..., None, POS_NOW - 10, POS_NOW + 86_400, POS_NOW + 400 * 86_400):
        body = _payload_mod().positions_payload(_attested_report(tmp_path, due=due))
        assert body["rows"][0]["attestation"]["state"] != "bad", due


def test_the_window_crosses_as_a_moment_not_an_epoch(tmp_path: Path) -> None:
    from tests.commands.test_positions import NOW_TS as POS_NOW

    body = _payload_mod().positions_payload(_attested_report(tmp_path, due=POS_NOW + 86_400))
    assert body["rows"][0]["attest_due_at"]["display"]
    assert isinstance(body["rows"][0]["attest_due_at"]["value"], str)


def test_the_attestation_chip_is_separate_from_the_freshness_chip(tmp_path: Path) -> None:
    """The two disagree in both directions -- a perfectly attested asset with a cold series, or a
    freshly priced one with a lapsed claim -- and a reader has to be able to tell which is which."""
    from tests.commands.test_positions import NOW_TS as POS_NOW

    row = _payload_mod().positions_payload(_attested_report(tmp_path, due=POS_NOW - 10))["rows"][0]
    assert row["attestation"]["value"] == "expired"
    assert "ready" in row or "freshness" in row


def test_the_positions_view_shows_the_attestation_chip_beside_the_entry_gate() -> None:
    """Two columns, not one merged verdict. A reader has to be able to tell a lapsed claim about
    the asset from a cold price series."""
    source = _source("render.js")
    start = source.index("export function positionsView(")
    end = source.index("\nfunction ", start)
    view = source[start:end]

    assert 'label: "attestation"' in view
    assert "row.attestation" in view
    assert 'label: "entry gate"' in view
    assert "row.freshness" in view
