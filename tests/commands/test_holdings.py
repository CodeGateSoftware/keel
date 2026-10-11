"""The Holdings service -- the tranche <-> entry-order join and the per-tranche cost.

`positions` does not name its entry order (no backfill exists yet), so `gather_holdings` finds it
by the one thing `agent._open_tranche` copies verbatim from it: the fill. These tests seed the
two shapes the join must serve -- live (both rows written with the cycle clock) and paper (the
order carries the BAR's ts, the tranche the cycle's) -- and the ways it must refuse to guess.
"""

from __future__ import annotations

import ast
import re
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from keel.commands import holdings as holdings_mod
from keel.commands import orders as orders_mod
from keel.commands.holdings import (
    ENTRY_ORDER_NOT_IDENTIFIED,
    gather_holdings,
)
from keel.config import Config, load_config
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.types import Side
from tests.conftest import VALID_CONFIG_YAML

NOW_TS = 1_800_000_000
DAY = 86_400
PRODUCT = "BTC-USD"
FILL = Decimal("100050")


@pytest.fixture()
def repo(tmp_path: Path) -> Repository:
    conn = connect(str(tmp_path / "keel.db"))
    migrate(conn)
    return Repository(conn)


@pytest.fixture()
def config(tmp_path: Path) -> Config:
    path = tmp_path / "config.yaml"
    path.write_text(VALID_CONFIG_YAML, encoding="utf-8")
    return load_config(str(path))


def _order(repo: Repository, **overrides: Any) -> int:
    row: dict[str, Any] = {
        "mode": "live",
        "product_id": PRODUCT,
        "side": Side.BUY.value,
        "order_type": "market",
        "qty": Decimal("2"),
        "limit_price": None,
        "status": "filled",
        "fee": Decimal("1.20"),
        "expected_fill": Decimal("100000"),
        "actual_fill": FILL,
        "filled_quantity": Decimal("2"),
        "raw_response": None,
        "confirmation": "autonomous",
        "rule_id": None,
        "created_at": NOW_TS - DAY,
        "updated_at": NOW_TS - DAY,
    }
    row.update(overrides)
    return repo.insert_order(row)


def _tranche(repo: Repository, **overrides: Any) -> int:
    row: dict[str, Any] = {
        "product_id": PRODUCT,
        "rule_name": "turtle_breakout",
        "opened_at": NOW_TS - DAY,
        "qty": Decimal("2"),
        "entry_fill": FILL,
        "entry_fee": Decimal("1.20"),
        "initial_stop": Decimal("90000"),
    }
    row.update(overrides)
    return repo.open_position(**row)


def test_a_live_tranche_finds_its_entry_order_by_the_fill_and_the_shared_clock(
    repo: Repository, config: Config
) -> None:
    order_id = _order(repo, created_at=NOW_TS - DAY)
    _tranche(repo, opened_at=NOW_TS - DAY)
    (row,) = gather_holdings(repo, config, now_ts=NOW_TS).rows
    assert row.entry_order is not None and row.entry_order.id == order_id
    assert row.entry_order_note == ""


def test_the_side_is_compared_in_the_spelling_the_writers_store(
    repo: Repository, config: Config
) -> None:
    """Orders store `BUY`/`SELL` (`Side.value`). A lowercase comparison would leave every tranche
    unidentified while a lowercase-seeded fixture stayed green."""
    assert Side.BUY.value == "BUY"
    _order(repo)
    stored = repo.get_orders(product_id=PRODUCT)[0]["side"]
    assert stored == "BUY"
    _tranche(repo)
    (row,) = gather_holdings(repo, config, now_ts=NOW_TS).rows
    assert row.entry_order is not None


def test_a_paper_tranche_finds_its_order_though_the_order_carries_the_bar_ts(
    repo: Repository, config: Config
) -> None:
    """`PaperTrader` writes the order with `signal.ts` (the bar's), the tranche opens at the
    cycle's `now_ts`: more than a day apart, and still the same entry."""
    order_id = _order(repo, mode="paper", created_at=NOW_TS - 3 * DAY)
    _tranche(repo, opened_at=NOW_TS - DAY)
    (row,) = gather_holdings(repo, config, now_ts=NOW_TS).rows
    assert row.entry_order is not None and row.entry_order.id == order_id


def test_a_sell_at_the_same_second_and_price_is_never_the_entry_order(
    repo: Repository, config: Config
) -> None:
    _order(repo, side=Side.SELL.value, created_at=NOW_TS - DAY)
    _tranche(repo, opened_at=NOW_TS - DAY)
    (row,) = gather_holdings(repo, config, now_ts=NOW_TS).rows
    assert row.entry_order is None


def test_an_order_placed_after_the_tranche_opened_never_matches(
    repo: Repository, config: Config
) -> None:
    _order(repo, created_at=NOW_TS - DAY + 1)
    _tranche(repo, opened_at=NOW_TS - DAY)
    (row,) = gather_holdings(repo, config, now_ts=NOW_TS).rows
    assert row.entry_order is None


def test_a_different_fill_never_matches(repo: Repository, config: Config) -> None:
    _order(repo, actual_fill=FILL + 1)
    _tranche(repo)
    (row,) = gather_holdings(repo, config, now_ts=NOW_TS).rows
    assert row.entry_order is None


def test_a_different_rule_id_never_matches(repo: Repository, config: Config) -> None:
    r1 = repo.insert_rule("turtle_breakout", {}, status="live")
    r2 = repo.insert_rule("dca", {}, status="live")
    _order(repo, rule_id=r2)
    _tranche(repo, rule_id=r1)
    (row,) = gather_holdings(repo, config, now_ts=NOW_TS).rows
    assert row.entry_order is None


def test_an_unknown_rule_id_on_either_side_does_not_block_the_match(
    repo: Repository, config: Config
) -> None:
    r1 = repo.insert_rule("turtle_breakout", {}, status="live")
    a = _order(repo, rule_id=None, created_at=NOW_TS - 2 * DAY)
    _tranche(repo, rule_id=r1, opened_at=NOW_TS - 2 * DAY)
    b = _order(repo, rule_id=r1, created_at=NOW_TS - DAY)
    _tranche(repo, rule_id=None, opened_at=NOW_TS - DAY)
    rows = gather_holdings(repo, config, now_ts=NOW_TS).rows
    assert [r.entry_order.id for r in rows if r.entry_order] == [a, b]


def test_two_same_product_tranches_each_get_their_own_order(
    repo: Repository, config: Config
) -> None:
    """A DCA buying the same price on two days: closest-BEFORE plus claim-once, the reasoning
    `sleeve.unverified_fill_orders` documents -- oldest-first would hand yesterday's order to
    today's tranche."""
    yesterday = _order(repo, created_at=NOW_TS - 2 * DAY)
    today = _order(repo, created_at=NOW_TS - DAY)
    _tranche(repo, opened_at=NOW_TS - 2 * DAY)
    _tranche(repo, opened_at=NOW_TS - DAY)
    rows = gather_holdings(repo, config, now_ts=NOW_TS).rows
    assert [r.entry_order.id for r in rows if r.entry_order] == [yesterday, today]


def test_the_closest_order_before_the_tranche_wins_over_an_older_one(
    repo: Repository, config: Config
) -> None:
    _order(repo, created_at=NOW_TS - 5 * DAY)  # an earlier buy at the same price, long closed
    nearest = _order(repo, created_at=NOW_TS - DAY)
    _tranche(repo, opened_at=NOW_TS - DAY)
    (row,) = gather_holdings(repo, config, now_ts=NOW_TS).rows
    assert row.entry_order is not None and row.entry_order.id == nearest


def test_an_order_is_claimed_by_at_most_one_tranche(repo: Repository, config: Config) -> None:
    order_id = _order(repo, created_at=NOW_TS - 2 * DAY)
    _tranche(repo, opened_at=NOW_TS - 2 * DAY)
    _tranche(repo, opened_at=NOW_TS - DAY)  # same fill, no order of its own
    first, second = gather_holdings(repo, config, now_ts=NOW_TS).rows
    assert first.entry_order is not None and first.entry_order.id == order_id
    assert second.entry_order is None


def test_a_tranche_with_no_matching_order_says_so_rather_than_guessing(
    repo: Repository, config: Config
) -> None:
    _tranche(repo)
    (row,) = gather_holdings(repo, config, now_ts=NOW_TS).rows
    assert row.entry_order is None
    assert row.entry_order_note == ENTRY_ORDER_NOT_IDENTIFIED


def test_cost_is_what_the_tranche_paid_to_open(repo: Repository, config: Config) -> None:
    _tranche(repo, qty=Decimal("2"), entry_fill=Decimal("100"), entry_fee=Decimal("1.20"))
    (row,) = gather_holdings(repo, config, now_ts=NOW_TS).rows
    assert row.cost == Decimal("201.20")


def test_cost_of_a_scaled_out_tranche_counts_the_whole_entry(
    repo: Repository, config: Config
) -> None:
    """`qty` is what is still held; the entry fee covers the whole entry. Cost is therefore
    (qty + realized_qty) * fill + fee, not qty * fill + fee."""
    pid = _tranche(repo, qty=Decimal("4"), entry_fill=Decimal("100"), entry_fee=Decimal("2"))
    repo.reduce_position(
        pid,
        remaining_qty=Decimal("3"),
        realized_qty=Decimal("1"),
        realized_proceeds=Decimal("120"),
        realized_fees=Decimal("0.5"),
    )
    (row,) = gather_holdings(repo, config, now_ts=NOW_TS).rows
    assert row.position.qty == Decimal("3") and row.position.realized_qty == Decimal("1")
    assert row.cost == Decimal("402")


def test_rows_group_by_rule_row_in_first_seen_order_with_unattributed_last(
    repo: Repository, config: Config
) -> None:
    """#960: one group per `positions.rule_id`, not per kind. The live book's seven DCA rules all
    carry the kind `dca`, so grouping by name drew them as one table across products."""
    _tranche(repo, rule_name="", opened_at=NOW_TS - 6 * DAY)
    _tranche(repo, rule_name="dca", rule_id=6, opened_at=NOW_TS - 5 * DAY)
    _tranche(repo, rule_name="dca", rule_id=8, product_id="ETH-USD", opened_at=NOW_TS - 4 * DAY)
    _tranche(repo, rule_name="turtle_breakout", opened_at=NOW_TS - 3 * DAY)
    _tranche(repo, rule_name="dca", rule_id=6, opened_at=NOW_TS - 2 * DAY)
    _tranche(repo, rule_name="dca", opened_at=NOW_TS - DAY)
    report = gather_holdings(repo, config, now_ts=NOW_TS)

    assert [(g.key, g.heading) for g in report.groups] == [
        ("rule-6", "dca · BTC-USD · rule 6"),
        ("rule-8", "dca · ETH-USD · rule 8"),
        ("unattributed-1", "unattributed"),
        ("unattributed-2", "turtle_breakout · unattributed"),
        ("unattributed-3", "dca · unattributed"),
    ]
    assert [report.group_key(r) for r in report.rows] == [
        "unattributed-1",
        "rule-6",
        "rule-8",
        "unattributed-2",
        "rule-6",
        "unattributed-3",
    ]
    assert report.open_count == 6


def test_two_rules_of_one_kind_on_one_product_get_two_groups(
    repo: Repository, config: Config
) -> None:
    """Kind and product alone do not identify a rule: two BTC DCA rules (the paper ledger holds
    rules 19 and 20) differ only by their id and params. The id is in the heading."""
    _tranche(repo, rule_name="dca", rule_id=19, opened_at=NOW_TS - 2 * DAY)
    _tranche(repo, rule_name="dca", rule_id=20, opened_at=NOW_TS - DAY)
    headings = [g.heading for g in gather_holdings(repo, config, now_ts=NOW_TS).groups]
    assert headings == ["dca · BTC-USD · rule 19", "dca · BTC-USD · rule 20"]


def test_group_keys_are_unique_and_safe_whatever_the_rule_name(
    repo: Repository, config: Config
) -> None:
    """The view builds element ids from the key (`h-hold-<key>`), and `aria-labelledby` splits on
    whitespace. A key is built from an integer or an ordinal only, never from a stored name."""
    names = ["has space", 'quo"te', "<b>", "dca", "has space "]
    for i, name in enumerate(names):
        _tranche(repo, rule_name=name, opened_at=NOW_TS - (i + 2) * DAY)
    _tranche(repo, rule_name="has space", rule_id=7, opened_at=NOW_TS - DAY)
    keys = [g.key for g in gather_holdings(repo, config, now_ts=NOW_TS).groups]
    assert len(keys) == len(names) + 1
    assert len(set(keys)) == len(keys)
    assert all(re.fullmatch(r"(rule|unattributed)-\d+", key) for key in keys), keys


def test_stop_row_ids_are_the_tranches_that_carry_a_stop(repo: Repository, config: Config) -> None:
    with_stop = _tranche(repo, initial_stop=Decimal("1"), opened_at=NOW_TS - 2 * DAY)
    _tranche(repo, initial_stop=None, opened_at=NOW_TS - DAY)
    (group,) = gather_holdings(repo, config, now_ts=NOW_TS).groups
    assert group.stop_row_ids == (with_stop,)


def test_the_dca_stop_sentinel_is_not_listed_as_a_stop(repo: Repository, config: Config) -> None:
    """Every attributed DCA tranche records `Dca.detect`'s `stop=0` sentinel (the live book's
    tranches 4-6). Zero is not a stop, so it does not draw the stop column -- the same reading
    `status.bracket_verdict` takes (#961)."""
    with_stop = _tranche(repo, initial_stop=Decimal("1"), opened_at=NOW_TS - 2 * DAY)
    _tranche(repo, rule_name="dca", initial_stop=Decimal("0"), opened_at=NOW_TS - DAY)
    report = replace(gather_holdings(repo, config, now_ts=NOW_TS), mode="confirm")
    assert {g.key: g.stop_row_ids for g in report.groups} == {
        "unattributed-1": (with_stop,),
        "unattributed-2": (),
    }


def test_each_group_lists_its_own_stop_carrying_tranches(repo: Repository, config: Config) -> None:
    """The stop column is a per-TABLE decision: a PAXG-like turtle tranche with a stop and a live
    DCA rule's tranche with the sentinel 0. The DCA group lists nothing, the turtle's lists its
    id, so the DCA table draws no column of dashes beside the PAXG one."""
    paxg = _tranche(
        repo,
        rule_name="turtle_breakout",
        initial_stop=Decimal("4521.76"),
        opened_at=NOW_TS - 3 * DAY,
    )
    dca = _tranche(
        repo, rule_name="dca", rule_id=6, initial_stop=Decimal("0"), opened_at=NOW_TS - DAY
    )
    report = replace(gather_holdings(repo, config, now_ts=NOW_TS), mode="confirm")
    by_key = {g.key: g.stop_row_ids for g in report.groups}
    assert by_key == {"rule-6": (), "unattributed-1": (paxg,)}
    assert dca not in by_key["rule-6"]


def test_a_paper_tranches_zero_stop_is_listed_as_it_was_before_961(
    repo: Repository, config: Config
) -> None:
    """Paper is unchanged by #961: its bracket cell reads "paper stop $0.00" for a recorded 0, so
    the stop list must name that tranche too (`status.shown_stop`)."""
    zero = _tranche(repo, rule_name="dca", initial_stop=Decimal("0"), opened_at=NOW_TS - DAY)
    report = replace(gather_holdings(repo, config, now_ts=NOW_TS), mode="paper")
    assert [g.stop_row_ids for g in report.groups] == [(zero,)]
    live = replace(report, mode="confirm")
    assert [g.stop_row_ids for g in live.groups] == [()]  # live: the sentinel is no stop


def test_has_bracket_follows_bracket_order_id(repo: Repository, config: Config) -> None:
    bracket = _order(repo, side=Side.SELL.value, actual_fill=None)
    _tranche(repo, bracket_order_id=bracket, opened_at=NOW_TS - 2 * DAY)
    _tranche(repo, bracket_order_id=None, opened_at=NOW_TS - DAY)
    first, second = gather_holdings(repo, config, now_ts=NOW_TS).rows
    assert (first.has_bracket, second.has_bracket) == (True, False)


def test_the_report_carries_the_deployments_mode(repo: Repository, config: Config) -> None:
    assert gather_holdings(repo, config, now_ts=NOW_TS).mode == str(config.auto_trade.mode)


def test_the_join_never_classifies_a_cancel(
    repo: Repository, config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("classify_cancel must not run for a holdings join")

    monkeypatch.setattr(orders_mod, "classify_cancel", boom)
    _order(repo)
    _tranche(repo)
    (row,) = gather_holdings(repo, config, now_ts=NOW_TS).rows
    assert row.entry_order is not None


def test_orders_are_read_once_per_product_not_once_per_tranche(
    repo: Repository, config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    for d in (3, 2, 1):
        _order(repo, created_at=NOW_TS - d * DAY)
        _tranche(repo, opened_at=NOW_TS - d * DAY)
    calls: list[dict[str, Any]] = []
    real = repo.get_orders

    def counting(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        calls.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(repo, "get_orders", counting)
    report = gather_holdings(repo, config, now_ts=NOW_TS)
    assert all(r.entry_order is not None for r in report.rows)  # the fixture reached the reader
    assert len(report.rows) == 3
    assert len(calls) == 1


def test_the_service_reads_no_clock() -> None:
    tree = ast.parse(Path(holdings_mod.__file__).read_text(encoding="utf-8"))
    calls = [ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)]
    assert calls  # the scan saw real calls
    assert not {"time.time", "datetime.datetime.now", "datetime.now"} & set(calls)


def test_a_tranche_that_closes_between_the_two_reads_is_left_out_not_a_crash(
    repo: Repository, config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`gather_positions` reads the open book, then this service reads it again for
    `bracket_order_id` and `rule_id`. The agent writes on its own process while the console
    reads, so a tranche can close in between. It is no longer open, so it is left out -- a
    `KeyError` would fail the whole page, and a row built without its raw read would claim
    "NO bracket" about a tranche nobody looked at."""
    _tranche(repo)
    _tranche(repo, opened_at=NOW_TS - 2 * DAY)
    real = repo.get_open_positions
    calls = {"n": 0}

    def closing_one(product_id: str | None = None) -> list[dict[str, Any]]:
        calls["n"] += 1
        rows = real(product_id)
        return rows if calls["n"] == 1 else rows[:1]

    monkeypatch.setattr(repo, "get_open_positions", closing_one)
    report = gather_holdings(repo, config, now_ts=NOW_TS)

    assert calls["n"] == 2, "both reads must reach the patched book"
    assert report.open_count == 1
