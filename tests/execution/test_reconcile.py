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
                "errs": [], "warning": []}

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


def test_a_terminal_bracket_clears_the_bracket_order_key(repo):
    """A stale `bracket_order` would have a later roll or re-bracket cancel an order that is
    already gone -- and `_cancel_at_exchange` now RAISES on an unconfirmable cancel, so a stale
    key turns into a refused exit rather than a silent no-op."""
    _seed_bracket(repo)
    repo.set_state("bracket_order:BTC-USD", 2)
    broker = _Broker({"cb-1": {
        "order_id": "cb-1", "status": "FILLED", "filled_size": Decimal("0.01"),
        "average_filled_price": Decimal("48900"), "total_fees": Decimal("2.93"),
    }})

    reconcile.reconcile_open_orders(broker, repo, _config(), now_ts=NOW)

    assert repo.get_state("bracket_order:BTC-USD") is None


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
    _seed_bracket(repo)
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
    assert repo.get_state("bracket_order:BTC-USD") is not None


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
    _seed_bracket(repo)
    repo.set_state(f"position_rule:{PRODUCT}", {
        "rule_name": "dca", "opened_at": NOW - 1000,
        "entry_fill": Decimal("50000"), "qty": Decimal("0.01"), "entry_fee": Decimal("3"),
    })
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
    """The guard that stops a fabricated P&L. With no `position_rule` there is no observed entry
    price, and `record_closed_trade` refuses to guess one -- so must this path."""
    bracket_id = _seed_bracket(repo)
    repo.set_state(f"position_rule:{PRODUCT}", None)
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
