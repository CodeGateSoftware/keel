"""The balances view, on the wire -- issue #702.

The service decides; this pins that the browser gets the decision unaltered. The property that
matters most here is negative: **no figure on this page came from a network call**, and the
as-of stamps are what make that honest rather than hidden.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from keel_core.types import CycleBalance

from keel.commands.balances import gather_balances
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.web import payload as web_payload

BAL_NOW_TS = 1_800_000_000


def _record_cycle_balance(
    repo: Repository,
    ts: int,
    mode: str,
    *,
    currency: str = "USD",
    available: str | None,
    total: str | None,
) -> None:
    repo.record_cycle_balance(
        CycleBalance(
            ts=ts,
            mode=mode,
            currency=currency,
            available=None if available is None else Decimal(available),
            total=None if total is None else Decimal(total),
        )
    )


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


def _report(tmp_path: Path, *, mode: str = "live", cash: str | None = "250.10", **kw: Any) -> Any:
    from tests.commands.test_balances import FINEST, _candle, _config, _reading, _tranche

    conn = connect(str(tmp_path / "balances.db"))
    migrate(conn)
    repo = Repository(conn)
    repo.set_state("equity_state_mode", mode)
    if cash is not None:
        repo.record_equity_point(_reading(BAL_NOW_TS - 3600, mode, cash))
    if kw.get("paper_cash") is not None:
        repo.set_state("paper_cash_usdc", Decimal(kw["paper_cash"]))
    if kw.get("holding", True):
        _tranche(repo, "BTC-USD", "2")
        repo.upsert_candles("BTC-USD", FINEST, [_candle(BAL_NOW_TS - 900, "150")])
    return gather_balances(repo, _config(tmp_path), now_ts=BAL_NOW_TS)


def test_no_wire_value_in_the_balances_payload_is_ever_a_json_number(tmp_path: Path) -> None:
    document = json.loads(json.dumps(web_payload.balances_payload(_report(tmp_path))))
    numbers = [
        path
        for path, leaf in _walk(document)
        if not isinstance(leaf, bool) and isinstance(leaf, (int, float))
    ]
    assert numbers == [], numbers


def test_every_recorded_figure_carries_the_instant_it_was_recorded(tmp_path: Path) -> None:
    """The as-of stamp is what makes a recorded page honest instead of stale. A cash figure with
    no time on it is a claim about now that was made at some other now."""
    built = web_payload.balances_payload(_report(tmp_path))

    assert built["cash_as_of"]["value"].endswith("Z")
    assert built["assets"][0]["mark_as_of"]["value"].endswith("Z")


def test_the_settled_breakdown_says_it_is_unrecorded_rather_than_showing_available_as_settled(
    tmp_path: Path,
) -> None:
    """#702's centrepiece, still true when no cycle has written `cycle_balances` (`_report` here
    seeds only an `equity_points` row): the settled/unsettled split is not a number this specific
    deployment has. Labelling the available figure "settled" would answer the question the page
    was built to ask honestly."""
    built = web_payload.balances_payload(_report(tmp_path))

    assert built["settled_cash"]["state"] == "unknown"
    assert built["total_cash"]["state"] == "unknown"
    assert "UNRECORDED" in built["settled_breakdown"]["display"].upper()
    assert built["settled_breakdown"]["state"] == "unknown"


def test_a_recorded_settled_split_crosses_to_the_wire(tmp_path: Path) -> None:
    """#719: once a live cycle has recorded a `cycle_balances` row for the settlement currency,
    the page shows it -- the one case #702's original centrepiece test above could not reach."""
    conn = connect(str(tmp_path / "balances.db"))
    migrate(conn)
    repo = Repository(conn)
    repo.set_state("equity_state_mode", "live")

    from tests.commands.test_balances import _config, _reading

    repo.record_equity_point(_reading(BAL_NOW_TS - 3600, "live", "250.10"))
    _record_cycle_balance(repo, BAL_NOW_TS - 3600, "live", available="250.10", total="300.00")

    built = web_payload.balances_payload(
        gather_balances(repo, _config(tmp_path), now_ts=BAL_NOW_TS)
    )

    assert built["settled_cash"]["value"] == "250.10"
    assert built["total_cash"]["value"] == "300.00"
    assert built["settled_breakdown"]["state"] != "unknown"


def test_a_total_never_observed_reports_as_absent_all_the_way_to_the_payload(
    tmp_path: Path,
) -> None:
    """NULL means NOT OBSERVED, never zero -- checked at the wire, not just at the report."""
    conn = connect(str(tmp_path / "balances.db"))
    migrate(conn)
    repo = Repository(conn)
    repo.set_state("equity_state_mode", "live")
    _record_cycle_balance(repo, BAL_NOW_TS - 3600, "live", available="250.10", total=None)

    from tests.commands.test_balances import _config

    built = web_payload.balances_payload(
        gather_balances(repo, _config(tmp_path), now_ts=BAL_NOW_TS)
    )

    assert built["settled_cash"]["value"] == "250.10"
    assert built["total_cash"]["state"] == "unknown"
    assert built["total_cash"]["value"] == ""


def test_a_deployment_with_no_recorded_cycle_says_so(tmp_path: Path) -> None:
    """Not "$0.00", and not a blank tile. A deployment that has not completed a cycle since the
    series began recording has nothing to show, which is a different fact from an empty account."""
    built = web_payload.balances_payload(_report(tmp_path, cash=None))

    assert built["cash"]["state"] == "unknown"
    assert built["recorded"]["value"] == "false"
    assert built["recorded"]["display"]


def test_a_recorded_cycle_with_no_split_reads_as_recorded_and_absent(tmp_path: Path) -> None:
    """The combination `has_recorded_cash` exists to draw: a cycle DID run and record a reading,
    and that reading had no cash split. "Nothing has been recorded" and "the last cycle could not
    read a split" are different facts, and only the first means this page has nothing yet."""
    from tests.commands.test_balances import _config, _reading

    conn = connect(str(tmp_path / "split.db"))
    migrate(conn)
    repo = Repository(conn)
    repo.set_state("equity_state_mode", "live")
    repo.record_equity_point(_reading(BAL_NOW_TS - 3600, "live", None, equity="250"))

    built = web_payload.balances_payload(
        gather_balances(repo, _config(tmp_path), now_ts=BAL_NOW_TS)
    )

    assert built["recorded"]["value"] == "true", "a cycle did record a reading"
    assert built["cash"]["state"] == "unknown", "it just had no split to record"
    assert built["equity"]["value"] == "250"
    assert built["cash_as_of"]["value"].endswith("Z"), "the reading still has a time"


def test_paper_cash_crosses_only_in_paper_mode(tmp_path: Path) -> None:
    live = web_payload.balances_payload(_report(tmp_path, mode="live", paper_cash="9500"))
    nested = tmp_path / "p"
    nested.mkdir()
    paper = web_payload.balances_payload(_report(nested, mode="paper", paper_cash="9500"))

    assert live["paper_cash"]["state"] == "unknown"
    assert paper["paper_cash"]["value"] == "9500"


def test_the_asset_rows_carry_quantity_and_value(tmp_path: Path) -> None:
    row = web_payload.balances_payload(_report(tmp_path))["assets"][0]

    assert row["product_id"] == "BTC-USD"
    assert row["qty"]["value"] == "2"
    assert row["market_value"]["value"] == "300"


def test_an_unpriced_asset_shows_its_holding_and_no_value(tmp_path: Path) -> None:
    """The quantity is a fact; its worth is not. A zero here would read as a worthless holding."""
    from tests.commands.test_balances import _config, _reading, _tranche

    conn = connect(str(tmp_path / "b.db"))
    migrate(conn)
    repo = Repository(conn)
    repo.set_state("equity_state_mode", "live")
    repo.record_equity_point(_reading(BAL_NOW_TS - 3600, "live", "250"))
    _tranche(repo, "ETH-USD", "3")

    built = web_payload.balances_payload(
        gather_balances(repo, _config(tmp_path), now_ts=BAL_NOW_TS)
    )
    row = built["assets"][0]

    assert row["qty"]["value"] == "3"
    assert row["market_value"]["state"] == "unknown"


def test_the_mode_and_counts_come_off_the_report(tmp_path: Path) -> None:
    """Rule 6e: no `len()` in the serialiser."""
    built = web_payload.balances_payload(_report(tmp_path))

    assert built["mode"] == "live"
    assert built["asset_count"]["value"] == "1"


def test_the_payload_offers_no_buying_power_and_no_action(tmp_path: Path) -> None:
    """#702's refusal, on the wire rather than only in the view. "Buying power" is a leverage
    invitation, and a balances page that carried one would be inviting the operator to spend
    money the cash-spot constitution refuses to lend them."""
    text = json.dumps(web_payload.balances_payload(_report(tmp_path))).lower()

    for banned in ("buying_power", "buying power", "deposit", "withdraw", "transfer"):
        assert banned not in text, banned


# -- the view, in the client -----------------------------------------------------------------


def _source(name: str) -> str:
    from keel.web import staticfiles

    return (Path(staticfiles.__file__).parent / "static" / "js" / name).read_text(encoding="utf-8")


def _code(name: str) -> str:
    from tests.web.test_client_assets import _comments_only

    return _comments_only(_source(name))


def _view_body() -> str:
    after = _code("render.js").split("export function balancesView")[1]
    end = after.find("export function ")
    return after if end == -1 else after[:end]


def test_the_balances_view_is_wired_into_the_client_router() -> None:
    from keel.web import staticfiles

    assert "balances" in staticfiles.CLIENT_ROUTES
    main_code = _code("main.js")
    assert "balancesView" in main_code
    assert 'route.name === "balances"' in main_code
    assert "export function balancesView" in _code("render.js")


def test_the_balances_view_offers_no_action_of_any_kind() -> None:
    """#702's refusal, pinned on the source. Cash is a fact, not an affordance -- and this is the
    page where a deposit button or a "buying power" tile would look most like a courtesy."""
    body = _view_body().lower()

    for banned in ("buying", "deposit", "withdraw", "transfer", "button", "addeventlistener"):
        assert banned not in body, f"the balances view must offer no {banned}"


def test_the_view_stamps_the_recorded_figures() -> None:
    """A recorded page is honest only if it says when. EVERY recorded figure on this page, with
    no exceptions -- the cash reading's stamp, each asset's mark, and the settled/total split's.

    The split was the exception for one release: it shipped as two tiles with no time on them,
    on the page whose own module docstring says "a tile with no time on it is a claim about now
    that was made at some other now". Enumerated here rather than spot-checked, so a fourth
    recorded figure added without a stamp is a decision someone has to make deliberately."""
    body = _view_body()

    for stamp in ("cash_as_of", "mark_as_of", "settled_as_of"):
        assert stamp in body, f"a recorded figure with no {stamp} beside it"


def test_the_view_names_the_settled_split_as_unrecorded() -> None:
    """Omitting the tiles would let a reader take the available figure for the settled one."""
    assert "settled_breakdown" in _view_body()
