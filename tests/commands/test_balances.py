"""The balances report -- issue #702.

**`keel serve` makes no network call, and this report is why that stays true.** A balances page
is the obvious place to reach for a live venue read, and doing so would put credentials into the
web process, hand venue latency to a page that re-polls every 15 seconds, and spend an operator's
rate limit for every browser tab left open. Every figure here comes from what a CYCLE recorded:
`equity_points` (#698) for cash, the positions report (#701) for the per-asset rows.

That is not a consolation prize. The cash this shows is the cash the engine actually sized
against when it evaluated the rails, stamped with when it read it -- which is a more useful
answer than a fresher number the engine never saw.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from keel_core.types import Candle, EquityReading, Granularity

from keel.commands.balances import gather_balances
from keel.config import Config
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from tests.conftest import VALID_CONFIG_YAML

NOW_TS = 1_800_000_000
DAY = 86_400
FINEST = Granularity.FIFTEEN_MINUTE


@pytest.fixture()
def repo(tmp_path: Path) -> Repository:
    conn = connect(str(tmp_path / "keel.db"))
    migrate(conn)
    return Repository(conn)


def _config(tmp_path: Path) -> Config:
    from keel.config import load_config

    path = tmp_path / "config.yaml"
    path.write_text(VALID_CONFIG_YAML, encoding="utf-8")
    return load_config(str(path))


def _reading(ts: int, mode: str, cash: str | None, equity: str = "10000") -> EquityReading:
    return EquityReading(
        ts=ts,
        mode=mode,
        equity=Decimal(equity),
        cash=None if cash is None else Decimal(cash),
        unrealized=Decimal("0"),
        hwm=Decimal(equity),
    )


def _candle(ts: int, close: str) -> Candle:
    price = Decimal(close)
    return Candle(ts=ts, open=price, high=price, low=price, close=price, volume=Decimal("1"))


def _tranche(repo: Repository, product_id: str, qty: str, **overrides: Any) -> int:
    row: dict[str, Any] = {
        "product_id": product_id,
        "rule_name": "turtle_breakout",
        "opened_at": NOW_TS - DAY,
        "qty": Decimal(qty),
        "entry_fill": Decimal("100"),
        "entry_fee": Decimal("1"),
        "initial_stop": Decimal("90"),
    }
    row.update(overrides)
    return repo.open_position(**row)


# -- cash comes from the cycle that recorded it ---------------------------------------------------


def test_cash_is_the_newest_reading_for_the_mode_in_force(repo: Repository, tmp_path: Path) -> None:
    """The mode partition, again. `equity_points` holds paper and live rows in one database, and
    a page that took the newest row of ANY mode would show a $10,000 synthetic balance on a live
    deployment that holds $250."""
    repo.set_state("equity_state_mode", "live")
    # The PAPER row is the newer of the two, deliberately: an unfiltered "newest reading" read
    # would return it, so this fixture is what makes the mode filter observable. With the live
    # row newest, the test would pass against a report that ignored `mode` entirely.
    repo.record_equity_point(_reading(NOW_TS - 3600, "live", "250.10", equity="250.10"))
    repo.record_equity_point(_reading(NOW_TS - 60, "paper", "9000", equity="10000"))

    report = gather_balances(repo, _config(tmp_path), now_ts=NOW_TS)

    assert report.mode == "live"
    assert report.cash == Decimal("250.10"), "the live reading, not the newer paper one"
    assert report.cash_as_of == NOW_TS - 3600


def test_the_newest_reading_wins_within_the_mode(repo: Repository, tmp_path: Path) -> None:
    repo.set_state("equity_state_mode", "live")
    repo.record_equity_point(_reading(NOW_TS - DAY, "live", "100"))
    repo.record_equity_point(_reading(NOW_TS - 60, "live", "300"))

    assert gather_balances(repo, _config(tmp_path), now_ts=NOW_TS).cash == Decimal("300")


def test_a_deployment_that_has_recorded_nothing_says_so(repo: Repository, tmp_path: Path) -> None:
    """A fresh deployment, or one that has not completed a cycle since the v19 upgrade. Absent,
    never zero: a zero cash balance is a fact about an account, and this is the absence of any
    fact at all."""
    repo.set_state("equity_state_mode", "live")

    report = gather_balances(repo, _config(tmp_path), now_ts=NOW_TS)

    assert report.cash is None
    assert report.cash_as_of is None
    assert report.has_recorded_cash is False


def test_a_reading_whose_split_was_never_recorded_reports_no_cash(
    repo: Repository, tmp_path: Path
) -> None:
    """`equity_points.cash` is nullable -- a cycle can know its total and not the split. The
    equity is still shown; the cash is not invented from it."""
    repo.set_state("equity_state_mode", "live")
    repo.record_equity_point(_reading(NOW_TS - 60, "live", None, equity="250"))

    report = gather_balances(repo, _config(tmp_path), now_ts=NOW_TS)

    assert report.cash is None
    assert report.equity == Decimal("250")
    assert report.cash_as_of == NOW_TS - 60, "the reading still has a time, it just has no split"


def test_the_recorded_equity_and_unrealized_ride_along(repo: Repository, tmp_path: Path) -> None:
    """One reading, read once. Taking cash from one cycle and equity from another would show two
    moments as though they were one."""
    repo.set_state("equity_state_mode", "live")
    repo.record_equity_point(_reading(NOW_TS - 60, "live", "250.10", equity="1250.10"))

    report = gather_balances(repo, _config(tmp_path), now_ts=NOW_TS)

    assert report.equity == Decimal("1250.10")
    assert report.hwm == Decimal("1250.10")


# -- what is NOT recorded -------------------------------------------------------------------------


def test_the_settled_breakdown_is_reported_as_unrecorded(
    repo: Repository, tmp_path: Path
) -> None:
    """#702's centrepiece, and the honest answer to it today. `equity_points.cash` comes from
    `_fetch_available_quote`, which reads `Balance.available` and nothing else -- the venue's
    settled-versus-total pair is never written down. The report says so rather than presenting
    the available figure under a label that implies the distinction was checked."""
    repo.set_state("equity_state_mode", "live")
    repo.record_equity_point(_reading(NOW_TS - 60, "live", "250.10"))

    report = gather_balances(repo, _config(tmp_path), now_ts=NOW_TS)

    assert report.settled_cash is None
    assert report.total_cash is None
    assert report.settled_breakdown_recorded is False


# -- paper mode ------------------------------------------------------------------------------------


def test_paper_mode_reads_the_synthetic_cash_beside_the_recorded_one(
    repo: Repository, tmp_path: Path
) -> None:
    """`paper_cash_usdc` is the live value of the synthetic account, which moves the moment a
    paper fill happens -- while the recorded reading is from the last cycle. Both are shown: they
    answer "what does the paper account hold now" and "what did the cycle act on"."""
    repo.set_state("equity_state_mode", "paper")
    repo.set_state("paper_cash_usdc", Decimal("9500.25"))
    repo.record_equity_point(_reading(NOW_TS - 3600, "paper", "9000"))

    report = gather_balances(repo, _config(tmp_path), now_ts=NOW_TS)

    assert report.paper_cash == Decimal("9500.25")
    assert report.cash == Decimal("9000")


def test_live_mode_reports_no_paper_cash_even_if_the_key_survives(
    repo: Repository, tmp_path: Path
) -> None:
    """`paper_cash_usdc` outlives a paper->live flip in `agent_state`. Showing it on a live page
    would put a synthetic balance beside real money."""
    repo.set_state("equity_state_mode", "live")
    repo.set_state("paper_cash_usdc", Decimal("9500.25"))

    assert gather_balances(repo, _config(tmp_path), now_ts=NOW_TS).paper_cash is None


# -- the per-asset rows ----------------------------------------------------------------------------


def test_tranches_of_one_product_are_summed_into_one_asset_row(
    repo: Repository, tmp_path: Path
) -> None:
    """A balances page answers "what do I hold", which is per ASSET. The tranche breakdown is the
    Positions view's job, and the two read the same report so they cannot disagree."""
    repo.set_state("equity_state_mode", "live")
    _tranche(repo, "BTC-USD", "2")
    _tranche(repo, "BTC-USD", "3")
    repo.upsert_candles("BTC-USD", FINEST, [_candle(NOW_TS - 900, "150")])

    rows = gather_balances(repo, _config(tmp_path), now_ts=NOW_TS).assets

    assert [row.product_id for row in rows] == ["BTC-USD"]
    assert rows[0].qty == Decimal("5")
    assert rows[0].market_value == Decimal("5") * Decimal("150")


def test_an_asset_with_an_unmarked_tranche_reports_no_value_rather_than_a_partial_sum(
    repo: Repository, tmp_path: Path
) -> None:
    """A partial sum is the most dangerous shape here: it looks like a total and is not one. If
    any tranche of a product has no mark, the product's value is unknown -- and saying so is the
    only reading that cannot be mistaken for a smaller holding."""
    repo.set_state("equity_state_mode", "live")
    _tranche(repo, "BTC-USD", "2")
    _tranche(repo, "ETH-USD", "3")
    repo.upsert_candles("BTC-USD", FINEST, [_candle(NOW_TS - 900, "150")])

    by_product = {
        row.product_id: row
        for row in gather_balances(repo, _config(tmp_path), now_ts=NOW_TS).assets
    }

    assert by_product["BTC-USD"].market_value == Decimal("300")
    assert by_product["ETH-USD"].qty == Decimal("3"), "the holding is known"
    assert by_product["ETH-USD"].market_value is None, "its value is not"


def test_the_asset_mark_carries_the_time_it_was_read(repo: Repository, tmp_path: Path) -> None:
    """Every figure on this page is stamped. A mark with no time is a claim about now that was
    made at some other now."""
    repo.set_state("equity_state_mode", "live")
    _tranche(repo, "BTC-USD", "2")
    repo.upsert_candles("BTC-USD", FINEST, [_candle(NOW_TS - 900, "150")])

    row = gather_balances(repo, _config(tmp_path), now_ts=NOW_TS).assets[0]

    assert row.mark == Decimal("150")
    assert row.mark_as_of == NOW_TS - 900


def test_no_open_positions_is_an_empty_asset_list_not_an_error(
    repo: Repository, tmp_path: Path
) -> None:
    repo.set_state("equity_state_mode", "live")

    assert gather_balances(repo, _config(tmp_path), now_ts=NOW_TS).assets == ()


def test_an_unstamped_mode_reports_no_cash_rather_than_guessing(
    repo: Repository, tmp_path: Path
) -> None:
    """Before the first cycle, `equity_state_mode` is unset. Picking a mode to read would be
    choosing which account's balance to show on a deployment that has not said."""
    repo.record_equity_point(_reading(NOW_TS - 60, "live", "250"))

    report = gather_balances(repo, _config(tmp_path), now_ts=NOW_TS)

    assert report.mode == ""
    assert report.cash is None
    assert report.has_recorded_cash is False


# -- the mixed-mark guard, at the level where it can exist (#702 review) ------------------------


def _position_row(product_id: str, qty: str, mark: str | None) -> Any:
    from keel.commands.positions import PositionRow

    price = None if mark is None else Decimal(mark)
    return PositionRow(
        id=1,
        product_id=product_id,
        rule_name="turtle_breakout",
        opened_at=NOW_TS - DAY,
        qty=Decimal(qty),
        entry_fill=Decimal("100"),
        entry_fee=Decimal("1"),
        mark=price,
        mark_ts=None if price is None else NOW_TS - 900,
        market_value=None if price is None else Decimal(qty) * price,
        unrealized_pnl=None,
        initial_stop=None,
        stop_distance=None,
        stop_distance_pct=None,
        realized_qty=Decimal("0"),
        realized_proceeds=Decimal("0"),
        realized_fees=Decimal("0"),
        ready=True,
        ready_reason=None,
    )


def test_one_unmarked_tranche_makes_the_whole_asset_value_unknown() -> None:
    """The guard is DEFENSIVE and this is the only way to reach it.

    `gather_positions` reads the mark once per product and hands every tranche of it the same
    figure, so a product whose tranches disagree cannot arise through that caller -- which means
    a test going through `gather_balances` cannot exercise this branch, and one that seeds two
    different PRODUCTS (as the first version of this test did) is not exercising it either.

    Driven through `_assets_from` directly, because the guard protects the FOLD, not that
    caller: a future caller that assembles rows from more than one read, or a mark cache that
    stops being per-product, would produce exactly this state -- and a sum over the priced subset
    would render a holding as worth less than it is, which is the failure worth engineering
    against on the page an operator checks to see what they have.
    """
    from keel.commands.balances import _assets_from

    rows = [
        _position_row("BTC-USD", "2", "150"),
        _position_row("BTC-USD", "3", None),
    ]

    assets = _assets_from(rows, ("BTC-USD",))

    assert assets[0].qty == Decimal("5"), "the holding is known"
    assert assets[0].market_value is None, "its value is not -- never the priced subset's sum"


def test_a_fully_marked_asset_still_sums(tmp_path: Path) -> None:
    """The other side of the guard: nothing is withheld when every tranche has a mark."""
    from keel.commands.balances import _assets_from

    rows = [
        _position_row("BTC-USD", "2", "150"),
        _position_row("BTC-USD", "3", "150"),
    ]

    assert _assets_from(rows, ("BTC-USD",))[0].market_value == Decimal("750")


def test_the_asset_order_follows_the_reports_product_order(tmp_path: Path) -> None:
    """Pinned rather than argued. Sorted or set-ordered, the page would reorder itself between
    reads for no reason a reader could see."""
    from keel.commands.balances import _assets_from

    rows = [
        _position_row("SOL-USD", "1", "10"),
        _position_row("BTC-USD", "2", "150"),
    ]

    assets = _assets_from(rows, ("SOL-USD", "BTC-USD"))

    assert [row.product_id for row in assets] == ["SOL-USD", "BTC-USD"]


# -- the read cost of one request (#702 review) ------------------------------------------------


def test_balances_does_not_pay_for_the_entry_gate_it_never_renders(
    repo: Repository, tmp_path: Path
) -> None:
    """This page shows quantity, mark and value. It does not show the entry-gate verdict, and it
    must not pay to compute one.

    Measured before the fix: three products cost 12 candle reads and a rules read per request --
    four reads per product, three of them for `entry_bar_ready` across every configured
    granularity, on an endpoint the console re-polls every 15 seconds. The mark needs one read
    per product and nothing else.
    """
    repo.set_state("equity_state_mode", "live")
    repo.record_equity_point(_reading(NOW_TS - 3600, "live", "250"))
    repo.insert_rule("turtle_breakout", {"product_id": "BTC-USD", "granularity": "ONE_DAY"})
    for product in ("BTC-USD", "ETH-USD", "SOL-USD"):
        _tranche(repo, product, "2")
        repo.upsert_candles(product, FINEST, [_candle(NOW_TS - 900, "150")])

    reads = {"candles": 0}
    original = repo.get_candles

    def counting(*args: Any, **kwargs: Any) -> Any:
        reads["candles"] += 1
        return original(*args, **kwargs)

    repo.get_candles = counting  # type: ignore[method-assign]
    gather_balances(repo, _config(tmp_path), now_ts=NOW_TS)

    assert reads["candles"] == 3, (
        f"one mark read per product and no more; got {reads['candles']} for 3 products"
    )
