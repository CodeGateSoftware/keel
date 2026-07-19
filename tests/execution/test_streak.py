"""Tests for the closed-trade producer and the streak counters it maintains.

The counter is the producer's private state; rail 16 reads only `streak_halt_until`. Keeping the
threshold decision in one place is deliberate -- if the rail also evaluated the counter, the two
could disagree about whether the breaker is tripped.
"""

from __future__ import annotations

from decimal import Decimal

from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.execution import streak

NOW = 1_800_000_000
DAY = 86_400


def _repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    return Repository(conn)


def _config(max_consecutive_losses: int = 3, streak_cooloff_days: int = 2):
    # NOTE: `caps` and `market_data` have NO defaults on `Config` -- omitting them raises
    # `TypeError: missing 2 required positional arguments`. Verified against config.py:206.
    from keel.config import Caps, Config, MarketDataConfig, MoneyMgmtConfig

    return Config(
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
        money_mgmt=MoneyMgmtConfig(
            max_consecutive_losses=max_consecutive_losses,
            streak_cooloff_days=streak_cooloff_days,
        ),
    )


def _close(repo, config, *, pnl: str, is_dca: bool = False, now_ts: int = NOW) -> None:
    """Close one trade with a given net P&L."""
    entry = Decimal("100")
    qty = Decimal("1")
    exit_fill = entry + Decimal(pnl)
    streak.record_closed_trade(
        repo,
        config,
        product_id="BTC-USD",
        position={
            "rule_name": None if is_dca else "turtle_breakout",
            "opened_at": now_ts - DAY,
            "entry_fill": entry,
            "qty": qty,
        },
        exit_fill=exit_fill,
        exit_qty=qty,
        fees=Decimal("0"),
        is_dca=is_dca,
        now_ts=now_ts,
    )


def test_a_closed_trade_appends_exactly_one_outcome_row() -> None:
    repo = _repo()
    _close(repo, _config(), pnl="5")
    assert len(repo.get_trade_outcomes()) == 1


def test_a_losing_trade_increments_the_counter() -> None:
    repo = _repo()
    _close(repo, _config(), pnl="-5")
    assert repo.get_state("consecutive_losses") == 1


def test_a_winning_trade_resets_the_counter_to_zero() -> None:
    """The counter resets on ANY win -- that is normal operation, no halt involved."""
    repo = _repo()
    config = _config()
    _close(repo, config, pnl="-5")
    _close(repo, config, pnl="-5")
    assert repo.get_state("consecutive_losses") == 2
    _close(repo, config, pnl="+5")
    assert repo.get_state("consecutive_losses") == 0


def test_fees_can_turn_a_gross_winner_into_a_counted_loss() -> None:
    """Rail 7 exists because fees dominate small moves; the streak must agree with that."""
    repo = _repo()
    streak.record_closed_trade(
        repo,
        _config(),
        product_id="BTC-USD",
        position={
            "rule_name": "turtle_breakout",
            "opened_at": NOW - DAY,
            "entry_fill": Decimal("100"),
            "qty": Decimal("1"),
        },
        exit_fill=Decimal("100.10"),   # +0.10 gross
        exit_qty=Decimal("1"),
        fees=Decimal("0.25"),          # -0.15 net
        is_dca=False,
        now_ts=NOW,
    )
    assert repo.get_trade_outcomes()[0]["pnl_net"] == Decimal("-0.15")
    assert repo.get_state("consecutive_losses") == 1


def test_reaching_the_threshold_sets_the_halt() -> None:
    repo = _repo()
    config = _config(max_consecutive_losses=3, streak_cooloff_days=2)
    for _ in range(3):
        _close(repo, config, pnl="-5")
    assert repo.get_state("streak_halt_until") == NOW + 2 * DAY


def test_below_the_threshold_sets_no_halt() -> None:
    """The negative for the test above: two losses with a threshold of three must NOT halt."""
    repo = _repo()
    config = _config(max_consecutive_losses=3)
    for _ in range(2):
        _close(repo, config, pnl="-5")
    assert repo.get_state("streak_halt_until", default=0) == 0


def test_a_dca_loss_records_an_outcome_but_never_moves_the_streak() -> None:
    """DCA is designed to buy through drawdowns (§12.6) -- counting it would trip the breaker
    during exactly the accumulation it exists to perform."""
    repo = _repo()
    config = _config(max_consecutive_losses=1)
    _close(repo, config, pnl="-5", is_dca=True)
    assert len(repo.get_trade_outcomes()) == 1        # recorded
    assert repo.get_state("consecutive_losses", default=0) == 0   # but not counted
    assert repo.get_state("streak_halt_until", default=0) == 0    # and never halts


def test_the_rail_is_inert_when_disabled() -> None:
    """max_consecutive_losses = 0 is the shipped default and must never halt."""
    repo = _repo()
    config = _config(max_consecutive_losses=0)
    for _ in range(10):
        _close(repo, config, pnl="-5")
    assert repo.get_state("streak_halt_until", default=0) == 0


def test_a_position_with_no_entry_context_is_skipped_not_guessed() -> None:
    """Legacy bare-string state (Task 1) yields entry_fill=None. Inventing a price would
    fabricate a P&L and could trip a live-money breaker on a number nobody observed."""
    repo = _repo()
    streak.record_closed_trade(
        repo,
        _config(),
        product_id="BTC-USD",
        position={"rule_name": "turtle_breakout", "opened_at": None,
                  "entry_fill": None, "qty": None},
        exit_fill=Decimal("100"),
        exit_qty=Decimal("1"),
        fees=Decimal("0"),
        is_dca=False,
        now_ts=NOW,
    )
    assert repo.get_trade_outcomes() == []
    assert repo.get_state("consecutive_losses", default=0) == 0
