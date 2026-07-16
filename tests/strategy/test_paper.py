"""Tests for halal_cb.strategy.paper: the forward paper-trading simulator.

`PaperTrader` consumes `Signal`s (task 1's shared type) directly -- it does not
import `engine.py` (task 7 builds in parallel, per the Phase 2 wave-C split) -- and
candle data, simulates fills, and journals every paper fill to `orders(mode='paper')`
via `Repository`. All tests use an in-memory, migrated `Repository`, per the plan.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from halal_cb.data.db import connect, migrate
from halal_cb.data.repository import Repository
from halal_cb.strategy.backtest import BacktestResult
from halal_cb.strategy.paper import PaperTrader, track_record
from halal_cb.strategy.rules.base import Action, Setup, Signal
from halal_cb.types import Candle, Side

FEE_PCT = Decimal("0.006")
SLIPPAGE_PCT = Decimal("0.0005")


@pytest.fixture
def repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    return Repository(conn)


def _candle(ts: int, o: str, h: str, l: str, c: str) -> Candle:  # noqa: E741 - matches OHLC convention
    return Candle(
        ts=ts, open=Decimal(o), high=Decimal(h), low=Decimal(l), close=Decimal(c), volume=Decimal(0)
    )


def _setup(entry: str = "100", stop: str = "90", target: str = "120", ts: int = 1_000) -> Setup:
    return Setup(
        product_id="BTC-USD",
        direction="long",
        entry=Decimal(entry),
        stop=Decimal(stop),
        target=Decimal(target),
        context={},
        ts=ts,
    )


def _enter_signal(
    rule_name: str = "pullback_continuation",
    product_id: str = "BTC-USD",
    setup: Setup | None = None,
    ts: int = 1_000,
) -> Signal:
    return Signal(
        rule_name=rule_name,
        product_id=product_id,
        action=Action.ENTER,
        side=Side.BUY,
        setup=setup or _setup(ts=ts),
        cts_score=7,
        entry_technique="signal_candle",
        ts=ts,
    )


def _exit_signal(
    rule_name: str = "pullback_continuation", product_id: str = "BTC-USD", ts: int = 1_000
) -> Signal:
    return Signal(
        rule_name=rule_name,
        product_id=product_id,
        action=Action.EXIT,
        side=Side.SELL,
        setup=None,
        cts_score=0,
        entry_technique="confirm_3bar",
        ts=ts,
    )


class TestEnterThenTargetHit:
    """The plan's headline acceptance case: an ENTER signal, then price hitting
    target, writes entry+exit paper orders with correct pnl.
    """

    def test_entry_order_is_written_and_filled(self, repo: Repository) -> None:
        trader = PaperTrader(repo)
        entry_order_id = trader.on_signal(_enter_signal())

        assert entry_order_id is not None
        order = repo.get_order(entry_order_id)
        assert order["mode"] == "paper"
        assert order["product_id"] == "BTC-USD"
        assert order["side"] == "BUY"
        assert order["status"] == "filled"
        expected_entry_fill = Decimal("100") * (Decimal(1) + SLIPPAGE_PCT)
        assert order["actual_fill"] == expected_entry_fill

    def test_target_hit_writes_exit_order_with_correct_pnl(self, repo: Repository) -> None:
        trader = PaperTrader(repo)
        trader.on_signal(_enter_signal(ts=1_000))

        # runs cleanly to target (120), no stop touch first
        target_candle = _candle(1_060, "115", "121", "114", "120")
        exit_order_id = trader.on_candle("BTC-USD", target_candle)

        assert exit_order_id is not None
        order = repo.get_order(exit_order_id)
        assert order["mode"] == "paper"
        assert order["side"] == "SELL"
        assert order["status"] == "filled"

        entry_fill = Decimal("100") * (Decimal(1) + SLIPPAGE_PCT)
        exit_fill = Decimal("120") * (Decimal(1) - SLIPPAGE_PCT)
        entry_fee = entry_fill * FEE_PCT
        exit_fee = exit_fill * FEE_PCT
        expected_pnl = (exit_fill - entry_fill) - entry_fee - exit_fee

        assert order["actual_fill"] == exit_fill

        payload = json.loads(order["raw_response"])
        assert payload["role"] == "exit"
        assert Decimal(payload["pnl"]) == expected_pnl
        assert payload["outcome"] == "win"
        assert payload["entry_order_id"] is not None

    def test_no_touch_leaves_position_open_and_writes_no_exit_order(
        self, repo: Repository
    ) -> None:
        trader = PaperTrader(repo)
        trader.on_signal(_enter_signal(ts=1_000))

        holding_candle = _candle(1_060, "108", "109", "107", "108")
        exit_order_id = trader.on_candle("BTC-USD", holding_candle)

        assert exit_order_id is None
        assert trader.has_open_position("BTC-USD")


class TestMfeMae:
    def test_mfe_and_mae_recorded_on_close(self, repo: Repository) -> None:
        trader = PaperTrader(repo)
        trader.on_signal(_enter_signal(ts=1_000))

        entry_fill = Decimal("100") * (Decimal(1) + SLIPPAGE_PCT)

        # intermediate bar: no touch, but extends the running high/low
        trader.on_candle("BTC-USD", _candle(1_060, "104", "106", "97", "105"))
        # target-hit bar
        exit_order_id = trader.on_candle("BTC-USD", _candle(1_120, "110", "121", "109", "120"))

        order = repo.get_order(exit_order_id)
        payload = json.loads(order["raw_response"])
        expected_mfe = Decimal("121") - entry_fill
        expected_mae = entry_fill - Decimal("97")
        assert Decimal(payload["mfe"]) == expected_mfe
        assert Decimal(payload["mae"]) == expected_mae


class TestStopHit:
    def test_stop_touch_closes_at_stop_as_a_loss(self, repo: Repository) -> None:
        trader = PaperTrader(repo)
        trader.on_signal(_enter_signal(ts=1_000))

        stop_candle = _candle(1_060, "95", "96", "89", "90")
        exit_order_id = trader.on_candle("BTC-USD", stop_candle)

        assert exit_order_id is not None
        order = repo.get_order(exit_order_id)
        payload = json.loads(order["raw_response"])
        assert payload["outcome"] == "loss"
        entry_fill = Decimal("100") * (Decimal(1) + SLIPPAGE_PCT)
        exit_fill = Decimal("90") * (Decimal(1) - SLIPPAGE_PCT)
        assert Decimal(payload["exit"]) == exit_fill
        assert Decimal(payload["entry"]) == entry_fill


class TestExitSignal:
    def test_exit_signal_closes_open_position_at_candle_close(self, repo: Repository) -> None:
        trader = PaperTrader(repo)
        trader.on_signal(_enter_signal(ts=1_000))

        candle = _candle(1_060, "104", "106", "103", "105")
        exit_order_id = trader.on_signal(_exit_signal(ts=1_060), candle=candle)

        assert exit_order_id is not None
        assert not trader.has_open_position("BTC-USD")
        order = repo.get_order(exit_order_id)
        assert order["side"] == "SELL"
        exit_fill = Decimal("105") * (Decimal(1) - SLIPPAGE_PCT)
        assert order["actual_fill"] == exit_fill

    def test_exit_signal_without_open_position_is_a_noop(self, repo: Repository) -> None:
        trader = PaperTrader(repo)
        candle = _candle(1_000, "100", "101", "99", "100")
        assert trader.on_signal(_exit_signal(ts=1_000), candle=candle) is None


class TestNoOverlap:
    def test_second_enter_signal_for_open_instrument_is_ignored(self, repo: Repository) -> None:
        trader = PaperTrader(repo)
        first_id = trader.on_signal(_enter_signal(ts=1_000))
        second_id = trader.on_signal(_enter_signal(ts=1_060, setup=_setup(entry="105", ts=1_060)))

        assert first_id is not None
        assert second_id is None
        # only the first entry order was ever written
        assert repo.get_order(first_id) is not None

    def test_new_signal_accepted_after_position_closes(self, repo: Repository) -> None:
        trader = PaperTrader(repo)
        trader.on_signal(_enter_signal(ts=1_000))
        trader.on_candle("BTC-USD", _candle(1_060, "115", "121", "114", "120"))  # closes at target

        assert not trader.has_open_position("BTC-USD")
        second_id = trader.on_signal(_enter_signal(ts=1_120, setup=_setup(entry="121", ts=1_120)))
        assert second_id is not None

    def test_positions_are_tracked_independently_per_instrument(self, repo: Repository) -> None:
        trader = PaperTrader(repo)
        btc_id = trader.on_signal(_enter_signal(product_id="BTC-USD", ts=1_000))
        eth_id = trader.on_signal(_enter_signal(product_id="ETH-USD", ts=1_000))

        assert btc_id is not None
        assert eth_id is not None
        assert trader.has_open_position("BTC-USD")
        assert trader.has_open_position("ETH-USD")


class TestNoLiveOrders:
    def test_all_written_orders_are_mode_paper(self, repo: Repository) -> None:
        trader = PaperTrader(repo)
        entry_id = trader.on_signal(_enter_signal(ts=1_000))
        exit_id = trader.on_candle("BTC-USD", _candle(1_060, "115", "121", "114", "120"))

        assert repo.get_order(entry_id)["mode"] == "paper"
        assert repo.get_order(exit_id)["mode"] == "paper"


class TestTrackRecord:
    def _win(self, repo: Repository, rule_name: str, ts: int) -> None:
        trader = PaperTrader(repo)
        trader.on_signal(
            _enter_signal(rule_name=rule_name, setup=_setup(entry="100", stop="90", target="120", ts=ts))
        )
        trader.on_candle("BTC-USD", _candle(ts + 60, "115", "121", "114", "120"))

    def _loss(self, repo: Repository, rule_name: str, ts: int) -> None:
        trader = PaperTrader(repo)
        trader.on_signal(
            _enter_signal(rule_name=rule_name, setup=_setup(entry="100", stop="90", target="120", ts=ts))
        )
        trader.on_candle("BTC-USD", _candle(ts + 60, "95", "96", "89", "90"))

    def test_returns_backtest_result_shape(self, repo: Repository) -> None:
        self._win(repo, "rule_a", ts=1_000)

        result = track_record(repo, "rule_a")

        assert isinstance(result, BacktestResult)

    def test_aggregates_wins_and_losses(self, repo: Repository) -> None:
        self._win(repo, "rule_a", ts=1_000)
        self._loss(repo, "rule_a", ts=2_000)

        result = track_record(repo, "rule_a")

        assert result.n_trades == 2
        assert result.win_rate == 0.5
        assert len(result.trades) == 2
        outcomes = {t.outcome for t in result.trades}
        assert outcomes == {"win", "loss"}

    def test_filters_by_rule_name(self, repo: Repository) -> None:
        self._win(repo, "rule_a", ts=1_000)
        self._win(repo, "rule_b", ts=2_000)

        result = track_record(repo, "rule_a")

        assert result.n_trades == 1

    def test_open_position_included_in_trades_but_not_aggregate_stats(
        self, repo: Repository
    ) -> None:
        trader = PaperTrader(repo)
        trader.on_signal(_enter_signal(rule_name="rule_a", ts=1_000))
        # never closes

        result = track_record(repo, "rule_a")

        assert result.n_trades == 0
        assert len(result.trades) == 1
        assert result.trades[0].outcome == "open"

    def test_no_paper_trades_yields_empty_result(self, repo: Repository) -> None:
        result = track_record(repo, "unknown_rule")

        assert result.n_trades == 0
        assert result.trades == []
        assert result.win_rate == 0.0
