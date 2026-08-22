"""Tests for the exit-reconciliation pass -- the half of bracket handling that was missing.

A bracket rests at the exchange until price reaches the stop or the target. Nothing in a
placement response reveals that later fill, and `run_once` never re-read order status, so a
stop-out -- the dominant source of losses -- closed a position the agent never noticed: the DB
row stayed `pending`, `_held_position` still counted the position as held, `position_rule` was
never cleared, and no `trade_outcomes` row was written. Rails 11 and 16 were blind to the entire
category.
"""

from __future__ import annotations

import logging
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


class _RebracketingBroker(_Broker):
    """`_Broker` plus the order-placement surface `place_bracket` needs."""

    def __init__(self, orders: dict[str, dict[str, Any]] | None = None) -> None:
        super().__init__(orders)
        self.placed: list[dict[str, Any]] = []

    def get_accounts(self) -> list[dict[str, Any]]:
        return [{"currency": "USDC", "available_balance": Decimal("1000000")}]

    def preview_order(self, product_id: str, side: Any, order_configuration: dict) -> dict:
        return {"order_total": Decimal("50"), "commission_total": Decimal("0"),
                "errs": [], "warning": [],
                # Both book sides, as the real venue returns them: #350's spread gate
                # fails closed on a preview without them (reconcile places SELLs only,
                # which the gate never touches -- this keeps the fake honest anyway).
                "best_bid": Decimal("49990"), "best_ask": Decimal("50000")}

    def place_order(self, product_id: str, side: Any, order_configuration: dict) -> dict:
        self.placed.append({"product_id": product_id, "side": side,
                            "order_configuration": order_configuration})
        return {"success": True, "order_id": f"cb-re-{len(self.placed)}"}


def _allow_orders(repo: Repository) -> None:
    """Satisfy the rails that fail closed on unseeded state.

    Most tests in this module never place an order, so the fixture leaves these unset and the
    rails correctly veto. A test that expects reconcile to PLACE something must opt in.
    """
    repo.set_state("kill_switch", False)
    repo.set_state("last_feed_ts", NOW)


def _seed_bracket(
    repo: Repository,
    *,
    native_id: str = "cb-1",
    rule_name: str = "turtle_breakout",
    return_position: bool = False,
    with_ledger: bool = True,
) -> int:
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
    # `position_rule` is now ONLY the exit-rule ownership marker; the entry context a
    # `trade_outcomes` row needs lives in the `positions` ledger, one row per TRANCHE.
    repo.set_state(f"position_rule:{PRODUCT}", {
        "rule_name": rule_name, "opened_at": NOW - 1000,
    })
    position_id = None
    if with_ledger:
        position_id = repo.open_position(
            product_id=PRODUCT, rule_name=rule_name, opened_at=NOW - 1000,
            qty=Decimal("0.01"), entry_fill=Decimal("50000"), entry_fee=Decimal("3"),
            bracket_order_id=bracket_id,
        )
    repo.set_state(f"open_stop:{PRODUCT}", Decimal("49000"))
    return bracket_id if not return_position else position_id


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


def test_a_terminal_bracket_closes_its_tranche(repo):
    """A tranche left `open` after its bracket filled is a position the ledger still thinks we
    hold: the next exit would attribute P&L to it a second time, and `get_position_for_bracket`
    would keep answering for an order that is already gone."""
    position_id = _seed_bracket(repo, return_position=True)
    broker = _Broker({"cb-1": {
        "order_id": "cb-1", "status": "FILLED", "filled_size": Decimal("0.01"),
        "average_filled_price": Decimal("48900"), "total_fees": Decimal("2.93"),
    }})

    reconcile.reconcile_open_orders(broker, repo, _config(), now_ts=NOW)

    assert repo.get_open_positions(PRODUCT) == []
    assert repo.get_position_for_bracket(position_id) is None


def test_an_older_tranches_bracket_filling_attributes_to_THAT_tranche(repo):
    """The bug this whole task exists to kill. Tranche 1 at 50000, tranche 2 at 52000, then
    tranche 1's bracket fills at 49000. P&L must be computed against 50000 -- against 52000 it
    books a loss that never happened and feeds it to a live-money breaker.

    `position_rule` is deliberately seeded with the SECOND tranche's entry, because that is what
    the last-write-wins blob actually held after averaging up. Reading it yields -35.94; only
    per-tranche attribution yields -15.94. Seeding it with 50000 instead would make this test
    pass against the old code too, which is exactly how a green task hides a live bug.
    """
    for entry in (Decimal("50000"), Decimal("52000")):
        repo.insert_order(
            dict(mode="live", product_id=PRODUCT, side=Side.BUY.value, order_type="market",
                 qty=Decimal("0.01"), limit_price=entry, status="filled", fee=Decimal("3"),
                 expected_fill=entry, actual_fill=entry,
                 created_at=NOW - 1000, updated_at=NOW - 1000)
        )
    bracket_id = repo.insert_order(
        dict(mode="live", product_id=PRODUCT, side=Side.SELL.value, order_type="market",
             qty=Decimal("0.01"), limit_price=None, status="pending", fee=None,
             expected_fill=Decimal("49000"), actual_fill=None,
             raw_response='{"order_id": "cb-1"}', created_at=NOW - 1000, updated_at=NOW - 1000)
    )
    first = repo.open_position(product_id=PRODUCT, rule_name="turtle_breakout", opened_at=1_000,
                               qty=Decimal("0.01"), entry_fill=Decimal("50000"),
                               entry_fee=Decimal("3"), bracket_order_id=bracket_id)
    second = repo.open_position(product_id=PRODUCT, rule_name="turtle_breakout", opened_at=2_000,
                                qty=Decimal("0.01"), entry_fill=Decimal("52000"),
                                entry_fee=Decimal("3.1"))
    repo.set_state(f"position_rule:{PRODUCT}", {
        "rule_name": "turtle_breakout", "opened_at": 2_000,
        "entry_fill": Decimal("52000"), "qty": Decimal("0.01"), "entry_fee": Decimal("3.1"),
    })
    broker = _Broker({"cb-1": {
        "order_id": "cb-1", "status": "FILLED", "filled_size": Decimal("0.01"),
        "average_filled_price": Decimal("49000"), "total_fees": Decimal("2.94")}})

    reconcile.reconcile_open_orders(broker, repo, _config(), now_ts=NOW)

    outcome = repo.get_trade_outcomes()[0]
    assert outcome["entry_fill"] == Decimal("50000"), "attributed to the wrong tranche"
    # (49000 - 50000) * 0.01 - 2.94 exit - 3 entry.  Against tranche 2 this would be -35.94.
    assert outcome["pnl_net"] == Decimal("-15.94")
    # ONLY the filled tranche closes; the survivor must still be open and still be addressable.
    assert [r["id"] for r in repo.get_open_positions(PRODUCT)] == [second]
    assert repo.get_position_for_bracket(bracket_id) is None
    assert first != second


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


def test_a_partial_fill_is_recorded_as_its_own_non_terminal_state(repo):
    """A partially-filled bracket has NOT closed the position. Recording it as a full exit
    would book a P&L for size that never sold and release a position still partly held.

    But "leave the row untouched as `pending`" (the old behaviour) hides the fill from every
    reader: nothing distinguished an untouched order from one the venue had already begun
    executing, so the position basis and the bracket-sizing question were both blind to it
    (#446). The row now carries a DISTINCT non-terminal state plus the venue-observed filled
    quantity and running average price."""
    bracket_id = _seed_bracket(repo)
    broker = _Broker({"cb-1": {
        "order_id": "cb-1", "status": "OPEN", "filled_size": Decimal("0.004"),
        "average_filled_price": Decimal("48900"), "total_fees": Decimal("1.2"),
    }})

    changed = reconcile.reconcile_open_orders(broker, repo, _config(), now_ts=NOW)

    row = repo.get_order(bracket_id)
    assert changed == [bracket_id]                  # a state change, so it is reported as one
    assert row["status"] == "partially_filled"      # its own state, not "pending"
    assert row["filled_quantity"] == Decimal("0.004")
    assert row["actual_fill"] == Decimal("48900")   # the venue's running average across fills
    assert row["fee"] == Decimal("1.2")
    assert row["qty"] == Decimal("0.01")            # the ORDERED size stays the ordered size
    # ...and the terminal half is unchanged: no outcome, no release, the tranche is still open.
    assert repo.get_trade_outcomes() == []
    assert repo.get_open_positions(PRODUCT)


def test_partial_then_more_then_full_drives_the_states_and_the_weighted_average(repo):
    """The recognition state machine, driven by a fake venue through the whole life of one
    order. The average recorded at every step is the QUANTITY-WEIGHTED average of the fills
    observed so far -- exactly what rail 8 must later read as the basis (#446).

    The numbers are hand-computed so the arithmetic is pinned, not emergent:
      0.004 @ 48900                                        -> avg  48900.0
      +0.003 @ 50300 (0.004*48900 + 0.003*50300)/0.007     -> avg  49500.0
      +0.003 @ 51400 (...       + 0.003*51400)/0.01        -> avg  50070.0
    """
    bracket_id = _seed_bracket(repo)
    snapshots = [
        ("OPEN", Decimal("0.004"), Decimal("48900"), Decimal("1.17")),
        ("OPEN", Decimal("0.007"), Decimal("49500"), Decimal("2.05")),
        ("FILLED", Decimal("0.01"), Decimal("50070"), Decimal("2.93")),
    ]
    for status, filled, average, fees in snapshots:
        broker = _Broker({"cb-1": {
            "order_id": "cb-1", "status": status, "filled_size": filled,
            "average_filled_price": average, "total_fees": fees,
        }})
        reconcile.reconcile_open_orders(broker, repo, _config(), now_ts=NOW)
        row = repo.get_order(bracket_id)
        assert row["filled_quantity"] == filled
        assert row["actual_fill"] == average, f"weighted average wrong at {filled}"

    # Terminal: the outcome is booked ONCE, on the observed economics of the whole fill.
    row = repo.get_order(bracket_id)
    assert row["status"] == "filled"
    (outcome,) = repo.get_trade_outcomes()
    assert outcome["exit_fill"] == Decimal("50070")
    assert outcome["qty"] == Decimal("0.01")
    assert repo.get_open_positions(PRODUCT) == []


def test_a_partially_filled_order_is_still_polled_next_cycle(repo):
    """`partially_filled` is NON-terminal: if the sweep only fetched `pending` rows, the very
    state it just wrote would take the order out of its own polling set and the remaining
    0.006 would never be observed -- the exact blindness this issue exists to end."""
    _seed_bracket(repo)
    partial = _Broker({"cb-1": {
        "order_id": "cb-1", "status": "OPEN", "filled_size": Decimal("0.004"),
        "average_filled_price": Decimal("48900"), "total_fees": Decimal("1.17"),
    }})
    reconcile.reconcile_open_orders(partial, repo, _config(), now_ts=NOW)

    terminal = _Broker({"cb-1": {
        "order_id": "cb-1", "status": "FILLED", "filled_size": Decimal("0.01"),
        "average_filled_price": Decimal("49500"), "total_fees": Decimal("2.93"),
    }})
    reconcile.reconcile_open_orders(terminal, repo, _config(), now_ts=NOW)

    assert len(repo.get_trade_outcomes()) == 1


def test_a_repeat_partial_observation_is_idempotent(repo, caplog):
    """The sweep runs every cycle; a resting partial that has not moved must not re-write the
    row or re-warn on every cycle -- that trains the alert to be ignored."""
    bracket_id = _seed_bracket(repo)
    snapshot = {"cb-1": {
        "order_id": "cb-1", "status": "OPEN", "filled_size": Decimal("0.004"),
        "average_filled_price": Decimal("48900"), "total_fees": Decimal("1.17"),
    }}
    broker = _Broker(snapshot)

    with caplog.at_level(logging.WARNING):
        reconcile.reconcile_open_orders(broker, repo, _config(), now_ts=NOW)
        first = repo.get_order(bracket_id)["updated_at"]
        assert caplog.text.count("reconcile.order_partially_filled") == 1

    reconcile.reconcile_open_orders(broker, repo, _config(), now_ts=NOW + 900)

    assert caplog.text.count("reconcile.order_partially_filled") == 1
    assert repo.get_order(bracket_id)["updated_at"] == first


def test_a_partially_filled_bracket_that_cancels_records_the_sold_part(repo):
    """partial -> CANCELLED. The dead-order branch already knew how to book `filled_size` of a
    cancelled order; what changes is that the row ARRIVES there from the `partially_filled`
    state rather than from `pending` -- the booked quantity must still be only what sold."""
    bracket_id = _seed_bracket(repo)
    partial = _Broker({"cb-1": {
        "order_id": "cb-1", "status": "OPEN", "filled_size": Decimal("0.004"),
        "average_filled_price": Decimal("48900"), "total_fees": Decimal("1.17"),
    }})
    reconcile.reconcile_open_orders(partial, repo, _config(), now_ts=NOW)

    dead = _Broker({"cb-1": {
        "order_id": "cb-1", "status": "CANCELLED", "filled_size": Decimal("0.004"),
        "average_filled_price": Decimal("48900"), "total_fees": Decimal("1.17"),
    }})
    reconcile.reconcile_open_orders(dead, repo, _config(), now_ts=NOW)

    row = repo.get_order(bracket_id)
    assert row["status"] == "filled"
    assert row["qty"] == Decimal("0.004")
    (outcome,) = repo.get_trade_outcomes()
    assert outcome["qty"] == Decimal("0.004")
    # The still-held remainder is not dropped out of the ledger.
    assert repo.get_open_positions(PRODUCT)


def test_a_full_fill_also_records_the_filled_quantity(repo):
    """`filled_quantity` is not a partial-only field: a fully-filled row carries it too, so a
    reader never has to special-case which statuses have an observed quantity."""
    bracket_id = _seed_bracket(repo)
    broker = _Broker({"cb-1": {
        "order_id": "cb-1", "status": "FILLED", "filled_size": Decimal("0.01"),
        "average_filled_price": Decimal("48900"), "total_fees": Decimal("2.93"),
    }})

    reconcile.reconcile_open_orders(broker, repo, _config(), now_ts=NOW)

    assert repo.get_order(bracket_id)["filled_quantity"] == Decimal("0.01")


def test_an_old_row_without_filled_quantity_still_reconciles(repo):
    """Back-compat: every order written before this change has NULL `filled_quantity`, and a
    NULL must read as "not observed" -- the row behaves exactly as it did before."""
    bracket_id = _seed_bracket(repo)
    assert repo.get_order(bracket_id)["filled_quantity"] is None

    broker = _Broker({"cb-1": {
        "order_id": "cb-1", "status": "FILLED", "filled_size": Decimal("0.01"),
        "average_filled_price": Decimal("48900"), "total_fees": Decimal("2.93"),
    }})
    reconcile.reconcile_open_orders(broker, repo, _config(), now_ts=NOW)

    row = repo.get_order(bracket_id)
    assert row["status"] == "filled"
    assert row["filled_quantity"] == Decimal("0.01")


def test_a_partially_filled_then_cancelled_bracket_records_the_part_that_sold(repo):
    """Coinbase returns CANCELLED with `filled_size > 0` for an order that partly filled before
    being cancelled (thin book, self-trade prevention). The dead-order branch marked the row
    canceled without reading `filled_size`, so:

      - `_held_position` (which sums only `filled` rows) still reported the FULL position held,
        so the next exit would size a sell for coins we no longer own -- rejected, forever;
      - the realized loss on the sold portion never reached `trade_outcomes`, so rails 11 and 16
        never saw it.

    Observed fill quantity must never be dropped on the floor.
    """
    bracket_id = _seed_bracket(repo)
    broker = _Broker({"cb-1": {
        "order_id": "cb-1", "status": "CANCELLED", "filled_size": Decimal("0.006"),
        "average_filled_price": Decimal("48900"), "total_fees": Decimal("1.76"),
    }})

    reconcile.reconcile_open_orders(broker, repo, _config(), now_ts=NOW)

    row = repo.get_order(bracket_id)
    assert row["status"] == "filled"          # it DID sell -- partially
    assert row["qty"] == Decimal("0.006")     # only what actually sold
    assert row["actual_fill"] == Decimal("48900")
    assert row["fee"] == Decimal("1.76")

    outcomes = repo.get_trade_outcomes()
    assert len(outcomes) == 1
    assert outcomes[0]["qty"] == Decimal("0.006")


def test_a_cancelled_bracket_with_no_fill_still_records_nothing(repo):
    """Negative control for the above: filled_size == 0 means nothing sold, so recording a P&L
    would fabricate a trade."""
    bracket_id = _seed_bracket(repo)
    broker = _Broker({"cb-1": {
        "order_id": "cb-1", "status": "CANCELLED", "filled_size": Decimal("0"),
        "average_filled_price": Decimal("0"), "total_fees": Decimal("0"),
    }})

    reconcile.reconcile_open_orders(broker, repo, _config(), now_ts=NOW)

    assert repo.get_order(bracket_id)["status"] == "canceled"
    assert repo.get_trade_outcomes() == []


def test_a_filled_order_with_no_observed_price_is_not_turned_into_a_phantom_loss(repo):
    """`get_order` defaults a missing `average_filled_price` to 0. Feeding that to the producer
    computes (0 - entry) * qty -- a full-notional phantom loss written to trade_outcomes, which
    could trip rail 16 on a number nobody observed. `record_closed_trade` already refuses to
    guess a missing ENTRY price; the exit side must be just as strict."""
    bracket_id = _seed_bracket(repo)
    broker = _Broker({"cb-1": {
        "order_id": "cb-1", "status": "FILLED", "filled_size": Decimal("0.01"),
        "average_filled_price": Decimal("0"), "total_fees": Decimal("0"),
    }})

    reconcile.reconcile_open_orders(broker, repo, _config(), now_ts=NOW)

    assert repo.get_order(bracket_id)["status"] == "filled"   # it did fill
    assert repo.get_trade_outcomes() == []                    # but we will not invent a P&L
    assert repo.get_state("consecutive_losses", default=0) == 0


def test_a_failure_recording_one_outcome_does_not_abandon_the_other_orders(repo):
    """`_record_fill` sat outside the per-order try/except, so a raise there propagated out of
    `run_once` and left every remaining pending order unreconciled -- defeating the stated
    'one bad order must not blind the agent' contract, which was only tested for `get_order`."""
    _seed_bracket(repo, native_id="cb-1")
    second = repo.insert_order(
        dict(mode="live", product_id="ETH-USD", side=Side.SELL.value, order_type="market",
             qty=Decimal("1"), limit_price=None, status="pending", fee=None,
             expected_fill=Decimal("3000"), actual_fill=None,
             raw_response='{"order_id": "cb-2"}', created_at=NOW, updated_at=NOW)
    )
    broker = _Broker({
        "cb-1": {"order_id": "cb-1", "status": "FILLED", "filled_size": Decimal("0.01"),
                 "average_filled_price": Decimal("48900"), "total_fees": Decimal("2.93")},
        "cb-2": {"order_id": "cb-2", "status": "FILLED", "filled_size": Decimal("1"),
                 "average_filled_price": Decimal("2900"), "total_fees": Decimal("1")},
    })

    def _boom(*a, **k):
        raise RuntimeError("db is locked")

    repo.insert_trade_outcome = _boom   # type: ignore[method-assign]

    reconcile.reconcile_open_orders(broker, repo, _config(), now_ts=NOW)

    # the second order still got reconciled despite the first one blowing up
    assert repo.get_order(second)["status"] == "filled"


def test_a_dead_bracket_on_a_still_held_position_escalates_loudly(repo, caplog):
    """A cancelled bracket on a position we still hold means the stop is GONE.

    Reconcile now tries to RE-PLACE the bracket rather than only warn, so this covers the first
    fallback: `_seed_bracket` records an `open_stop` but no `open_target`, and a bracket needs
    both prices. Re-placing on an invented target would silently re-risk the position on a level
    no rule produced, so it escalates instead. INFO is not the right level for "your stop is
    gone". The vetoed-placement fallback is covered separately below.
    """
    _seed_bracket(repo)
    broker = _Broker({"cb-1": {
        "order_id": "cb-1", "status": "CANCELLED", "filled_size": Decimal("0"),
        "average_filled_price": Decimal("0"), "total_fees": Decimal("0"),
    }})

    with caplog.at_level(logging.CRITICAL):
        reconcile.reconcile_open_orders(broker, repo, _config(), now_ts=NOW)

    assert "reconcile.position_unprotected" in caplog.text


def test_a_dead_bracket_on_a_held_position_is_replaced(repo):
    """Coinbase cancels resting orders for reasons outside our control -- product status
    changes, self-trade prevention, an operator tapping cancel in the mobile app. Logging
    CRITICAL and leaving the position naked is not a resting state a trading agent should sit in
    for an unbounded time. Re-place from the recorded stop/target."""
    position_id = _seed_bracket(repo, return_position=True)
    repo.set_state("open_target:BTC-USD", Decimal("53000"))
    # The replacement runs through `guards.check` like any other order (un-overridable), so the
    # rails that fail closed on unseeded state -- kill-switch and feed staleness -- have to be
    # satisfied here or the re-bracket is vetoed and we only ever see the escalation.
    _allow_orders(repo)
    broker = _RebracketingBroker({"cb-1": {
        "order_id": "cb-1", "status": "CANCELLED", "filled_size": Decimal("0"),
        "average_filled_price": Decimal("0"), "total_fees": Decimal("0"),
    }})

    reconcile.reconcile_open_orders(broker, repo, _config(), now_ts=NOW)

    assert broker.placed, "no replacement bracket was placed"
    leg = broker.placed[-1]["order_configuration"]["trigger_bracket_gtc"]
    assert leg["stop_trigger_price"] == "49000"
    assert leg["limit_price"] == "53000"
    # The tranche must now name the REPLACEMENT. Leaving it on the dead order is silent data
    # loss: the replacement's eventual fill would resolve to no tranche, take the
    # "exit without position context" skip, and close the position with no `trade_outcomes` row.
    replacement_id = repo.get_orders(mode="live", product_id=PRODUCT, status="pending")[-1]["id"]
    owner = repo.get_position_for_bracket(replacement_id)
    assert owner is not None, "the tranche still points at the dead bracket"
    assert owner["id"] == position_id


def test_a_vetoed_replacement_bracket_escalates_instead_of_going_quiet(repo, caplog):
    """The second fallback, and the one that must never be silent: both prices ARE recorded, so
    re-placing is attempted, but `guards.check` vetoes it (here: the kill-switch). Guards stay
    un-overridable even for a protective order, so the position is left naked -- and a naked
    position that nobody is told about is strictly worse than the old warn-only behaviour."""
    _seed_bracket(repo)
    repo.set_state("open_target:BTC-USD", Decimal("53000"))
    _allow_orders(repo)
    repo.set_state("kill_switch", True)
    broker = _RebracketingBroker({"cb-1": {
        "order_id": "cb-1", "status": "CANCELLED", "filled_size": Decimal("0"),
        "average_filled_price": Decimal("0"), "total_fees": Decimal("0"),
    }})

    with caplog.at_level(logging.CRITICAL):
        reconcile.reconcile_open_orders(broker, repo, _config(), now_ts=NOW)

    assert not broker.placed, "a kill-switched agent must not place a replacement"
    assert "reconcile.position_unprotected" in caplog.text


def test_a_broker_error_while_re_bracketing_does_not_abandon_the_rest_of_the_pass(repo, caplog):
    """Re-bracketing reaches the network, and reconcile runs at the TOP of `run_once`. An
    exception escaping here aborted the whole cycle over one unreachable product -- every other
    pending order left unreconciled -- and swallowed the escalation too, leaving the position
    naked and silent. Same isolation contract the status fetch and `_record_fill` already have.
    """
    _seed_bracket(repo)
    repo.set_state("open_target:BTC-USD", Decimal("53000"))
    _allow_orders(repo)

    class _ExplodingBroker(_RebracketingBroker):
        def preview_order(self, product_id: str, side: Any, order_configuration: dict) -> dict:
            raise RuntimeError("exchange unreachable")

    broker = _ExplodingBroker({"cb-1": {
        "order_id": "cb-1", "status": "CANCELLED", "filled_size": Decimal("0"),
        "average_filled_price": Decimal("0"), "total_fees": Decimal("0"),
    }})

    with caplog.at_level(logging.CRITICAL):
        changed = reconcile.reconcile_open_orders(broker, repo, _config(), now_ts=NOW)

    assert changed, "the dead order was still reconciled"
    assert "reconcile.position_unprotected" in caplog.text


def test_a_dead_bracket_on_a_position_already_gone_does_not_escalate(repo, caplog):
    """Negative control: no holding means nothing to protect, so this must stay quiet."""
    bracket_id = repo.insert_order(
        dict(mode="live", product_id=PRODUCT, side=Side.SELL.value, order_type="market",
             qty=Decimal("0.01"), limit_price=None, status="pending", fee=None,
             expected_fill=Decimal("49000"), actual_fill=None,
             raw_response='{"order_id": "cb-9"}', created_at=NOW, updated_at=NOW)
    )
    broker = _Broker({"cb-9": {
        "order_id": "cb-9", "status": "CANCELLED", "filled_size": Decimal("0"),
        "average_filled_price": Decimal("0"), "total_fees": Decimal("0"),
    }})

    with caplog.at_level(logging.CRITICAL):
        reconcile.reconcile_open_orders(broker, repo, _config(), now_ts=NOW)

    assert "reconcile.position_unprotected" not in caplog.text
    assert repo.get_order(bracket_id)["status"] == "canceled"


def test_a_dca_owned_position_stopping_out_stays_exempt_from_the_streak(repo):
    """§12.6 exempts DCA from the consecutive-loss streak. The VOLUNTARY exit path derives
    `is_dca` from the owning rule and has a test; the reconcile path derived it too but nothing
    held it, so a DCA position stopping out would have counted toward a live-money breaker."""
    _seed_bracket(repo, rule_name="dca")
    broker = _Broker({"cb-1": {
        "order_id": "cb-1", "status": "FILLED", "filled_size": Decimal("0.01"),
        "average_filled_price": Decimal("48900"), "total_fees": Decimal("2.93"),
    }})

    reconcile.reconcile_open_orders(broker, repo, _config(), now_ts=NOW)

    assert repo.get_trade_outcomes()[0]["is_dca"] is True
    assert repo.get_state("consecutive_losses", default=0) == 0


def test_a_reconciled_BUY_never_records_an_outcome_or_releases_the_position(repo):
    """Only a SELL closes a position. A filled BUY reconciled here would otherwise book a
    `trade_outcomes` row, feed the streak, and clear `position_rule` -- releasing the very
    position it just opened."""
    repo.set_state(f"position_rule:{PRODUCT}", {
        "rule_name": "turtle_breakout", "opened_at": NOW - 1000,
        "entry_fill": Decimal("50000"), "qty": Decimal("0.01"), "entry_fee": Decimal("3"),
    })
    buy_id = repo.insert_order(
        dict(mode="live", product_id=PRODUCT, side=Side.BUY.value, order_type="limit",
             qty=Decimal("0.01"), limit_price=Decimal("50000"), status="pending", fee=None,
             expected_fill=Decimal("50000"), actual_fill=None,
             raw_response='{"order_id": "cb-buy"}', created_at=NOW, updated_at=NOW)
    )
    broker = _Broker({"cb-buy": {
        "order_id": "cb-buy", "status": "FILLED", "filled_size": Decimal("0.01"),
        "average_filled_price": Decimal("50010"), "total_fees": Decimal("3.1"),
    }})

    reconcile.reconcile_open_orders(broker, repo, _config(), now_ts=NOW)

    assert repo.get_order(buy_id)["status"] == "filled"      # economics still corrected
    assert repo.get_order(buy_id)["actual_fill"] == Decimal("50010")
    assert repo.get_trade_outcomes() == []                   # ...but no trade closed
    assert repo.get_state(f"position_rule:{PRODUCT}") is not None


def test_an_exit_with_no_entry_context_is_skipped_rather_than_invented(repo, caplog):
    """The guard that stops a fabricated P&L. With no ledger tranche owning this bracket there is
    no observed entry price, and `record_closed_trade` refuses to guess one -- so must this path.
    This is also the resting state for a position opened before the v4 ledger existed."""
    bracket_id = _seed_bracket(repo, with_ledger=False)
    broker = _Broker({"cb-1": {
        "order_id": "cb-1", "status": "FILLED", "filled_size": Decimal("0.01"),
        "average_filled_price": Decimal("48900"), "total_fees": Decimal("2.93"),
    }})

    with caplog.at_level(logging.WARNING):
        reconcile.reconcile_open_orders(broker, repo, _config(), now_ts=NOW)

    assert repo.get_order(bracket_id)["status"] == "filled"
    assert repo.get_trade_outcomes() == []
    assert repo.get_state("consecutive_losses", default=0) == 0
    # Assert the SKIP, not just the absence of an outcome. Without the guard the producer is
    # called with position=None and raises, which `_try_record_fill`'s isolation swallows --
    # producing an identical empty-outcome result. Only the log distinguishes deliberate skip
    # from swallowed crash.
    assert "reconcile.exit_without_position_context" in caplog.text
    assert "reconcile.record_fill_failed" not in caplog.text


def test_the_outcome_uses_the_OBSERVED_filled_size_not_the_orders_original_qty(repo):
    """`exit_qty` drives `pnl_net`. Every other fixture sets `filled_size == row qty`, which
    makes the distinction untestable -- so a variant reading the order's original size would
    pass. Here they DIFFER, which pins which one is used."""
    _seed_bracket(repo)   # row qty is 0.01
    broker = _Broker({"cb-1": {
        "order_id": "cb-1", "status": "FILLED", "filled_size": Decimal("0.008"),
        "average_filled_price": Decimal("48900"), "total_fees": Decimal("2.34"),
    }})

    reconcile.reconcile_open_orders(broker, repo, _config(), now_ts=NOW)

    outcome = repo.get_trade_outcomes()[0]
    assert outcome["qty"] == Decimal("0.008")
    # (48900 - 50000) * 0.008 - 2.34 exit - 3 entry
    assert outcome["pnl_net"] == Decimal("-14.14")


# -- the never-placed bracket (issue #195) ----------------------------------------------------
#
# `_rebracket_or_escalate` above heals a bracket that WAS accepted and later died: it is reached
# from the `_DEAD` branch of `reconcile_open_orders`, which only ever iterates `status="pending"`
# rows. A bracket that was never placed at all has no such row -- a broker rejection writes
# `rejected`, and a rails veto writes nothing, because the insert happens after the guard gate.
# Neither is `pending`, so nothing revisited the tranche and the position stayed naked for a full
# cycle (~24h, one per UTC day) behind a WARNING. These cover the ledger-driven sweep that closes
# that hole.


class _RejectingRebracketBroker(_RebracketingBroker):
    """Placement reaches the exchange and comes back refused -- min-size, precision, a venue
    error. The reachable cause of a never-placed bracket, and the one no rail can prevent."""

    def place_order(self, product_id: str, side: Any, order_configuration: dict) -> dict:
        self.placed.append({"product_id": product_id, "side": side,
                            "order_configuration": order_configuration})
        return {"success": False, "error": "PREVIEW_INVALID_BASE_SIZE"}


def _seed_unbracketed_tranche(
    repo: Repository,
    *,
    rule_name: str = "turtle_breakout",
    with_intent: bool = True,
    bracket_order_id: int | None = None,
) -> int:
    """A filled entry whose protective bracket never made it to the exchange.

    The entry BUY is `filled` (so `_held_position` sees inventory) but the tranche names no
    resting bracket. `with_intent=False` models a DCA tranche, which has no stop by design and
    must never be swept.
    """
    repo.insert_order(
        dict(mode="live", product_id=PRODUCT, side=Side.BUY.value, order_type="market",
             qty=Decimal("0.01"), limit_price=Decimal("50000"), status="filled",
             fee=Decimal("3"), expected_fill=Decimal("50000"), actual_fill=Decimal("50000"),
             created_at=NOW - 1000, updated_at=NOW - 1000)
    )
    repo.set_state(f"position_rule:{PRODUCT}", {
        "rule_name": rule_name, "opened_at": NOW - 1000,
    })
    position_id = repo.open_position(
        product_id=PRODUCT, rule_name=rule_name, opened_at=NOW - 1000,
        qty=Decimal("0.01"), entry_fill=Decimal("50000"), entry_fee=Decimal("3"),
        bracket_order_id=bracket_order_id,
    )
    if with_intent:
        repo.set_state(f"unbracketed:{PRODUCT}", {
            "stop": Decimal("49000"), "target": Decimal("53000"), "qty": Decimal("0.01"),
        })
    return position_id


def test_a_tranche_whose_bracket_was_never_placed_is_bracketed_next_cycle(repo):
    """The hole this closes. Nothing reached this tranche before: it owns no `pending` row, so
    `reconcile_open_orders` never looked at it, and no other pass scans held positions."""
    position_id = _seed_unbracketed_tranche(repo)
    _allow_orders(repo)
    broker = _RebracketingBroker()

    reconcile.reconcile_unbracketed_positions(broker, repo, _config(), now_ts=NOW)

    assert broker.placed, "the unprotected tranche was left without a bracket"
    leg = broker.placed[-1]["order_configuration"]["trigger_bracket_gtc"]
    # The levels the ORIGINAL trade was risk-sized against, not invented ones.
    assert leg["stop_trigger_price"] == "49000"
    assert leg["limit_price"] == "53000"

    placed_id = repo.get_orders(mode="live", product_id=PRODUCT, status="pending")[-1]["id"]
    owner = repo.get_position_for_bracket(placed_id)
    assert owner is not None and owner["id"] == position_id


def test_a_bracketed_tranche_clears_the_unprotected_record(repo):
    """The record is the retry trigger. Left behind after a successful placement it would make
    every later cycle re-place a bracket the position already has."""
    _seed_unbracketed_tranche(repo)
    _allow_orders(repo)

    reconcile.reconcile_unbracketed_positions(
        _RebracketingBroker(), repo, _config(), now_ts=NOW
    )

    assert repo.get_state(f"unbracketed:{PRODUCT}") is None


def test_a_dca_tranche_with_no_bracket_is_left_alone(repo, caplog):
    """DCA carries no stop by design (`agent.py`: "`None` for DCA (no stop, so no bracket)"), so
    a NULL bracket is its correct resting state. Sweeping it would invent a stop no rule
    produced, and escalating it would cry wolf on every DCA tranche every cycle."""
    _seed_unbracketed_tranche(repo, rule_name="dca", with_intent=False)
    _allow_orders(repo)
    broker = _RebracketingBroker()

    with caplog.at_level(logging.CRITICAL):
        reconcile.reconcile_unbracketed_positions(broker, repo, _config(), now_ts=NOW)

    assert not broker.placed, "a DCA tranche was given a bracket it should never have"
    assert "position_unprotected" not in caplog.text


def test_a_tranche_that_still_cannot_be_bracketed_escalates_loudly(repo, caplog):
    """Second placement attempt, second refusal. The position is genuinely naked and a human has
    to know -- this is the one state the whole pass exists to make impossible to sit in quietly."""
    _seed_unbracketed_tranche(repo)
    _allow_orders(repo)

    with caplog.at_level(logging.CRITICAL):
        reconcile.reconcile_unbracketed_positions(
            _RejectingRebracketBroker(), repo, _config(), now_ts=NOW
        )

    assert "reconcile.position_unprotected" in caplog.text


def test_a_tranche_that_could_not_be_bracketed_keeps_its_record_for_the_next_cycle(repo):
    """A failed retry must stay retryable. Clearing the record on failure would strand the
    position permanently -- exactly the resting-state bug this pass was written to end."""
    _seed_unbracketed_tranche(repo)
    _allow_orders(repo)

    reconcile.reconcile_unbracketed_positions(
        _RejectingRebracketBroker(), repo, _config(), now_ts=NOW
    )

    assert repo.get_state(f"unbracketed:{PRODUCT}") is not None


def test_a_tranche_with_a_resting_bracket_is_left_alone(repo):
    """Already protected. Re-placing would commit inventory the resting bracket already holds
    and be refused for insufficient funds -- turning a healthy position into a naked one."""
    _seed_bracket(repo)
    _allow_orders(repo)
    broker = _RebracketingBroker()

    reconcile.reconcile_unbracketed_positions(broker, repo, _config(), now_ts=NOW)

    assert not broker.placed


def test_a_tranche_whose_bracket_was_REJECTED_is_swept_too(repo):
    """A broker rejection DOES write an order row, just not a `pending` one. Keying the sweep on
    the row's existence rather than its status would skip exactly the reachable case."""
    rejected_id = repo.insert_order(
        dict(mode="live", product_id=PRODUCT, side=Side.SELL.value, order_type="market",
             qty=Decimal("0.01"), limit_price=None, status="rejected",
             fee=None, expected_fill=Decimal("49000"), actual_fill=None,
             created_at=NOW - 1000, updated_at=NOW - 1000)
    )
    _seed_unbracketed_tranche(repo, bracket_order_id=rejected_id)
    _allow_orders(repo)
    broker = _RebracketingBroker()

    reconcile.reconcile_unbracketed_positions(broker, repo, _config(), now_ts=NOW)

    assert broker.placed, "a tranche whose bracket was rejected was treated as protected"


def test_a_sold_out_product_is_not_re_bracketed(repo):
    """No inventory left means the tranche is stale ledger state, not an exposure. Placing a
    protective SELL here would be a naked short -- structurally impossible for keel to hold."""
    _seed_unbracketed_tranche(repo)
    repo.insert_order(
        dict(mode="live", product_id=PRODUCT, side=Side.SELL.value, order_type="market",
             qty=Decimal("0.01"), limit_price=None, status="filled",
             fee=Decimal("2"), expected_fill=Decimal("49000"), actual_fill=Decimal("49000"),
             created_at=NOW - 500, updated_at=NOW - 500)
    )
    _allow_orders(repo)
    broker = _RebracketingBroker()

    reconcile.reconcile_unbracketed_positions(broker, repo, _config(), now_ts=NOW)

    assert not broker.placed
