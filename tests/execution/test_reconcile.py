"""Tests for the exit-reconciliation pass -- the half of bracket handling that was missing.

A bracket rests at the exchange until price reaches the stop or the target. Nothing in a
placement response reveals that later fill, and `run_once` never re-read order status, so a
stop-out -- the dominant source of losses -- closed a position the agent never noticed: the DB
row stayed `pending`, `_held_position` still counted the position as held, `position_rule` was
never cleared, and no `trade_outcomes` row was written. Rails 11 and 16 were blind to the entire
category.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from keel.config import Caps, Config, MarketDataConfig, MoneyMgmtConfig
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.execution import reconcile
from keel.types import Side

NOW = 1_800_000_000
PRODUCT = "BTC-USD"


def _config(**overrides: Any) -> Config:
    base: dict[str, Any] = dict(
        allowlist=["BTC"],
        target_weights={},
        risk_pct=Decimal("0.01"),
        caps=Caps(
            max_per_order_usd=Decimal("100000"),
            max_per_day_usd=Decimal("300000"),
            max_exposure_usd=Decimal("1000000"),
            max_per_asset_pct=Decimal("1"),
        ),
        market_data=MarketDataConfig(granularities=[], history_days=365),
        money_mgmt=MoneyMgmtConfig(),
    )
    base.update(overrides)
    return Config(**base)


@pytest.fixture
def repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    return Repository(conn)


class _Broker:
    """Returns a canned observed state per broker-native order id."""

    def __init__(self, orders: dict[str, dict[str, Any]] | None = None) -> None:
        self._orders = orders or {}
        self.get_order_calls: list[str] = []

    def get_order(self, order_id: str) -> dict[str, Any]:
        self.get_order_calls.append(order_id)
        return self._orders[order_id]


def _seed_bracket(repo: Repository, *, native_id: str = "cb-1") -> int:
    """A held position plus a resting SELL bracket, as `execute` would leave them."""
    repo.insert_order(
        dict(mode="live", product_id=PRODUCT, side=Side.BUY.value, order_type="market",
             qty=Decimal("0.01"), limit_price=Decimal("50000"), status="filled",
             fee=Decimal("3"), expected_fill=Decimal("50000"), actual_fill=Decimal("50000"),
             created_at=NOW - 1000, updated_at=NOW - 1000)
    )
    bracket_id = repo.insert_order(
        dict(mode="live", product_id=PRODUCT, side=Side.SELL.value, order_type="market",
             qty=Decimal("0.01"), limit_price=None, status="pending",
             fee=None, expected_fill=Decimal("49000"), actual_fill=None,
             raw_response=f'{{"order_id": "{native_id}"}}',
             created_at=NOW - 1000, updated_at=NOW - 1000)
    )
    repo.set_state(f"position_rule:{PRODUCT}", {
        "rule_name": "turtle_breakout", "opened_at": NOW - 1000,
        "entry_fill": Decimal("50000"), "qty": Decimal("0.01"), "entry_fee": Decimal("3"),
    })
    repo.set_state(f"open_stop:{PRODUCT}", Decimal("49000"))
    return bracket_id


def test_a_filled_bracket_records_the_trade_with_OBSERVED_price_and_fees(repo):
    """The whole point. A stop-out must produce a `trade_outcomes` row, and it must use the
    price and fee the exchange actually charged -- not the expected price and previewed
    commission the executor guessed at placement time."""
    _seed_bracket(repo)
    broker = _Broker({"cb-1": {
        "order_id": "cb-1", "status": "FILLED", "filled_size": Decimal("0.01"),
        "average_filled_price": Decimal("48900"), "total_fees": Decimal("2.93"),
    }})

    reconcile.reconcile_open_orders(broker, repo, _config(), now_ts=NOW)

    outcomes = repo.get_trade_outcomes()
    assert len(outcomes) == 1
    assert outcomes[0]["exit_fill"] == Decimal("48900")     # observed, not the 49000 expected
    # (48900 - 50000) * 0.01 - 2.93 exit fee - 3 entry fee
    assert outcomes[0]["pnl_net"] == Decimal("-16.93")


def test_a_filled_bracket_marks_the_order_and_releases_the_position(repo):
    """Left `pending`, `_held_position` keeps counting sold inventory as held -- which feeds
    both position sizing and rail 11's equity."""
    bracket_id = _seed_bracket(repo)
    broker = _Broker({"cb-1": {
        "order_id": "cb-1", "status": "FILLED", "filled_size": Decimal("0.01"),
        "average_filled_price": Decimal("48900"), "total_fees": Decimal("2.93"),
    }})

    reconcile.reconcile_open_orders(broker, repo, _config(), now_ts=NOW)

    row = repo.get_order(bracket_id)
    assert row["status"] == "filled"
    assert row["actual_fill"] == Decimal("48900")
    assert row["fee"] == Decimal("2.93")
    assert repo.get_state(f"position_rule:{PRODUCT}") is None


def test_a_still_resting_bracket_changes_nothing(repo):
    """The negative control: an OPEN order must not be recorded, released, or counted."""
    bracket_id = _seed_bracket(repo)
    broker = _Broker({"cb-1": {
        "order_id": "cb-1", "status": "OPEN", "filled_size": Decimal("0"),
        "average_filled_price": Decimal("0"), "total_fees": Decimal("0"),
    }})

    reconcile.reconcile_open_orders(broker, repo, _config(), now_ts=NOW)

    assert repo.get_order(bracket_id)["status"] == "pending"
    assert repo.get_trade_outcomes() == []
    assert repo.get_state(f"position_rule:{PRODUCT}") is not None


def test_a_stop_out_feeds_the_consecutive_loss_streak(repo):
    """Rail 16 counts losses. Before reconciliation it could only ever see voluntary rule exits,
    systematically under-counting the losing side -- stops are where losses COME from."""
    _seed_bracket(repo)
    broker = _Broker({"cb-1": {
        "order_id": "cb-1", "status": "FILLED", "filled_size": Decimal("0.01"),
        "average_filled_price": Decimal("48900"), "total_fees": Decimal("2.93"),
    }})

    reconcile.reconcile_open_orders(broker, repo, _config(), now_ts=NOW)

    assert repo.get_state("consecutive_losses") == 1


def test_a_cancelled_or_expired_bracket_is_closed_out_without_recording_a_trade(repo):
    """A cancelled bracket never sold anything, so recording a P&L would fabricate a trade."""
    bracket_id = _seed_bracket(repo)
    broker = _Broker({"cb-1": {
        "order_id": "cb-1", "status": "CANCELLED", "filled_size": Decimal("0"),
        "average_filled_price": Decimal("0"), "total_fees": Decimal("0"),
    }})

    reconcile.reconcile_open_orders(broker, repo, _config(), now_ts=NOW)

    assert repo.get_order(bracket_id)["status"] == "canceled"
    assert repo.get_trade_outcomes() == []
    assert repo.get_state(f"position_rule:{PRODUCT}") is not None  # still held, now unprotected


def test_a_broker_error_on_one_order_does_not_abandon_the_rest(repo):
    """Reconciliation runs at the top of every cycle. One unreadable order must not stop the
    others from being reconciled, or a single bad id blinds the agent to every fill."""
    _seed_bracket(repo, native_id="cb-broken")
    second = repo.insert_order(
        dict(mode="live", product_id=PRODUCT, side=Side.SELL.value, order_type="market",
             qty=Decimal("0.01"), limit_price=None, status="pending", fee=None,
             expected_fill=Decimal("49000"), actual_fill=None,
             raw_response='{"order_id": "cb-2"}', created_at=NOW, updated_at=NOW)
    )

    class _PartlyBroken(_Broker):
        def get_order(self, order_id: str) -> dict[str, Any]:
            if order_id == "cb-broken":
                raise RuntimeError("broker blew up")
            return super().get_order(order_id)

    broker = _PartlyBroken({"cb-2": {
        "order_id": "cb-2", "status": "FILLED", "filled_size": Decimal("0.01"),
        "average_filled_price": Decimal("48900"), "total_fees": Decimal("2.93"),
    }})

    reconcile.reconcile_open_orders(broker, repo, _config(), now_ts=NOW)

    assert repo.get_order(second)["status"] == "filled"


def test_a_partial_fill_is_left_alone_rather_than_recorded_as_a_full_exit(repo):
    """A partially-filled bracket has NOT closed the position. Recording it as a full exit
    would book a P&L for size that never sold and release a position still partly held."""
    bracket_id = _seed_bracket(repo)
    broker = _Broker({"cb-1": {
        "order_id": "cb-1", "status": "OPEN", "filled_size": Decimal("0.004"),
        "average_filled_price": Decimal("48900"), "total_fees": Decimal("1.2"),
    }})

    reconcile.reconcile_open_orders(broker, repo, _config(), now_ts=NOW)

    assert repo.get_trade_outcomes() == []
    assert repo.get_order(bracket_id)["status"] == "pending"
