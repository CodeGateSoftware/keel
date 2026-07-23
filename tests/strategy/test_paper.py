"""Tests for keel.strategy.paper: the forward paper-trading simulator.

`PaperTrader` consumes `Signal`s (task 1's shared type) directly -- it does not
import `engine.py` (task 7 builds in parallel, per the Phase 2 wave-C split) -- and
candle data, simulates fills, and journals every paper fill to `orders(mode='paper')`
via `Repository`. All tests use an in-memory, migrated `Repository`, per the plan.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.strategy.backtest import BacktestResult
from keel.strategy.paper import PaperTrader, track_record
from keel.strategy.rules.base import Action, Setup, Signal
from keel.types import Candle, Side

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

    def test_paper_entry_records_supplied_qty(self, repo: Repository) -> None:
        trader = PaperTrader(repo)
        signal = _enter_signal(
            setup=Setup(
                product_id="BTC-USD",
                direction="long",
                entry=Decimal("100"),
                stop=Decimal("90"),
                target=Decimal("130"),
                context={},
                ts=1_000,
            )
        )
        order_id = trader.on_signal(signal, qty=Decimal("3"))

        assert order_id is not None
        order = repo.get_order(order_id)
        assert order["qty"] == Decimal("3")
        assert json.loads(order["raw_response"])["qty"] == "3"


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
        setup = _setup(entry="100", stop="90", target="120", ts=ts)
        trader.on_signal(_enter_signal(rule_name=rule_name, setup=setup))
        trader.on_candle("BTC-USD", _candle(ts + 60, "115", "121", "114", "120"))

    def _loss(self, repo: Repository, rule_name: str, ts: int) -> None:
        trader = PaperTrader(repo)
        setup = _setup(entry="100", stop="90", target="120", ts=ts)
        trader.on_signal(_enter_signal(rule_name=rule_name, setup=setup))
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


# -- rehydration (required for scheduled operation) ----------------------------


def test_open_positions_survive_a_new_PaperTrader_over_the_same_repo(repo):
    """⚠️ The bug this prevents: a per-cycle agent constructs a fresh trader each run.

    Without rehydration it would forget every open position -- never exiting them, and
    re-entering the same instrument on the next signal. The paper track record, which the
    promotion gate is scored on, would silently fill with unclosed entries.
    """
    first = PaperTrader(repo)
    first.on_signal(_enter_signal())
    assert first.has_open_position("BTC-USD")

    second = PaperTrader(repo)
    assert second.has_open_position("BTC-USD") is True


def test_a_closed_position_does_NOT_reopen_on_rehydration(repo):
    first = PaperTrader(repo)
    first.on_signal(_enter_signal())
    first.on_candle("BTC-USD", _candle(2_000, "110", "125", "108", "122"))  # contains target 120
    assert first.has_open_position("BTC-USD") is False

    assert PaperTrader(repo).has_open_position("BTC-USD") is False


def test_a_rehydrated_position_still_exits_on_its_original_stop(repo):
    """The reconstructed setup must carry the real stop/target, not defaults."""
    PaperTrader(repo).on_signal(_enter_signal())

    resumed = PaperTrader(repo)
    exit_id = resumed.on_candle("BTC-USD", _candle(2_000, "100", "101", "89", "90"))
    assert exit_id is not None
    assert resumed.has_open_position("BTC-USD") is False


def test_rehydration_on_an_empty_repo_is_a_no_op(repo):
    assert PaperTrader(repo).has_open_position("BTC-USD") is False


# -- synthetic cash, funding check, equity, epoch cutoff -----------------------


def test_paper_equity_seed_and_mark(repo):
    trader = PaperTrader(repo)
    assert trader.equity({"BTC-USD": Decimal("100")}) is None  # unseeded

    trader.seed_cash(Decimal("30000"), now_ts=1_700_000_000)
    assert trader.equity({}) == Decimal("30000")  # all cash, no positions

    # open a position and re-mark
    sig = _enter_signal(setup=_setup(entry="100", stop="90", target="130"))
    trader.on_signal(sig, qty=Decimal("5"))
    eq = trader.equity({"BTC-USD": Decimal("120")})
    # cash was debited by fill+fee; positions valued at 5*120
    assert eq < Decimal("30000") + Decimal("5") * Decimal("120")  # fee/slippage drag
    assert eq > Decimal("29000")


def test_paper_funding_check_rejects_when_cash_insufficient(repo):
    trader = PaperTrader(repo)
    trader.seed_cash(Decimal("50"), now_ts=1_700_000_000)
    sig = _enter_signal(setup=_setup(entry="100", stop="90", target="130"))
    # notional 5*100 = 500 >> 50 cash -> no fill
    assert trader.on_signal(sig, qty=Decimal("5")) is None
    assert trader.get_cash() == Decimal("50")  # unchanged
    assert not trader.has_open_position("BTC-USD")
    assert repo.get_orders(mode="paper") == []


def test_paper_funding_check_allows_when_cash_unseeded(repo):
    """Unseeded (`None`) cash must not block fills -- the funding check only applies
    once the operator has opted into a synthetic account via `seed_cash`.
    """
    trader = PaperTrader(repo)
    sig = _enter_signal(setup=_setup(entry="100", stop="90", target="130"))
    assert trader.on_signal(sig, qty=Decimal("5")) is not None


def test_seed_cash_only_sets_ledger_start_ts_once(repo):
    trader = PaperTrader(repo)
    trader.seed_cash(Decimal("1000"), now_ts=1_700_000_000)
    trader.seed_cash(Decimal("2000"), now_ts=1_800_000_000)
    assert repo.get_state("paper_ledger_start_ts") == 1_700_000_000
    assert trader.get_cash() == Decimal("2000")


def test_deposit_adds_to_cash(repo):
    trader = PaperTrader(repo)
    trader.seed_cash(Decimal("1000"), now_ts=1_700_000_000)
    trader.deposit(Decimal("500"))
    assert trader.get_cash() == Decimal("1500")
    assert repo.get_state("paper_cash_usdc") == Decimal("1500")


def test_deposit_is_a_noop_when_cash_unseeded(repo):
    trader = PaperTrader(repo)
    trader.deposit(Decimal("500"))
    assert trader.get_cash() is None


def test_cash_debited_on_entry_and_credited_on_exit(repo):
    trader = PaperTrader(repo)
    trader.seed_cash(Decimal("1000"), now_ts=1_000)
    trader.on_signal(_enter_signal(ts=1_000))  # qty defaults to 1, entry 100

    entry_fill = Decimal("100") * (Decimal(1) + SLIPPAGE_PCT)
    fee = entry_fill * FEE_PCT
    expected_after_entry = Decimal("1000") - entry_fill - fee
    assert trader.get_cash() == expected_after_entry

    trader.on_candle("BTC-USD", _candle(1_060, "115", "121", "114", "120"))  # closes at target
    exit_fill = Decimal("120") * (Decimal(1) - SLIPPAGE_PCT)
    exit_fee = exit_fill * FEE_PCT
    expected_after_exit = expected_after_entry + exit_fill - exit_fee
    assert trader.get_cash() == expected_after_exit


def test_pre_epoch_orders_are_skipped_on_rehydration(repo):
    """Legacy 1-unit orders written before `paper_ledger_start_ts` was seeded must
    never rehydrate into the synthetic account.
    """
    legacy = PaperTrader(repo)
    legacy.on_signal(_enter_signal(ts=1_000))
    assert legacy.has_open_position("BTC-USD")

    # operator now seeds a synthetic ledger epoch AFTER the legacy order was written
    legacy.seed_cash(Decimal("30000"), now_ts=2_000)

    resumed = PaperTrader(repo)
    assert resumed.has_open_position("BTC-USD") is False


def test_position_opened_before_seeding_is_never_costed(repo):
    """A position opened while cash is unseeded, later seeded mid-flight, must not
    desync the ledger: no debit happened at open, so no credit may happen at close,
    and it must never inflate `equity()` -- otherwise seeding after the fact would
    silently manufacture free equity out of an uncosted position.
    """
    trader = PaperTrader(repo)
    trader.on_signal(_enter_signal(setup=_setup(entry="100", stop="90", target="130"), ts=1_000))
    assert trader.has_open_position("BTC-USD")
    assert trader.get_cash() is None  # unseeded -- no debit was possible

    trader.seed_cash(Decimal("30000"), now_ts=1_700_000_000)

    # equity must be exactly cash -- the uncosted open position contributes nothing
    assert trader.equity({"BTC-USD": Decimal("120")}) == Decimal("30000")

    # close it via a stop touch
    trader.on_candle("BTC-USD", _candle(2_000, "89", "91", "88", "90"))
    assert not trader.has_open_position("BTC-USD")

    # no credit for an uncosted position -- cash stays exactly what was seeded
    assert trader.get_cash() == Decimal("30000")


def test_funding_check_rejects_at_boundary_between_notional_and_actual_fill_cost(repo):
    """The funding check must gate on the ACTUAL debit (fill + slippage + fee), not
    the coarser intent notional (entry * qty) -- otherwise cash can go negative for
    any seed strictly between the two, defeating the check's stated purpose.
    """
    trader = PaperTrader(repo)
    entry = Decimal("100")
    qty = Decimal("5")
    notional = entry * qty
    entry_fill = entry * (Decimal(1) + SLIPPAGE_PCT)
    fee = entry_fill * qty * FEE_PCT
    fill_cost = entry_fill * qty + fee
    assert notional < fill_cost  # slippage+fee always push the real cost higher

    seed = (notional + fill_cost) / 2  # strictly between the two
    trader.seed_cash(seed, now_ts=1_700_000_000)

    sig = _enter_signal(setup=_setup(entry="100", stop="90", target="130"))
    assert trader.on_signal(sig, qty=qty) is None
    assert trader.get_cash() == seed  # unchanged
    assert not trader.has_open_position("BTC-USD")
    assert repo.get_orders(mode="paper") == []
