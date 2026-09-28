"""Tests for the closed-trade producer and the streak counters it maintains.

The counter is the producer's private state; rail 16 reads only `streak_halt_until`. Keeping the
threshold decision in one place is deliberate -- if the rail also evaluated the counter, the two
could disagree about whether the breaker is tripped.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

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
        exit_fill=Decimal("100.10"),  # +0.10 gross
        exit_qty=Decimal("1"),
        fees=Decimal("0.25"),  # -0.15 net
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
    assert len(repo.get_trade_outcomes()) == 1  # recorded
    assert repo.get_state("consecutive_losses", default=0) == 0  # but not counted
    assert repo.get_state("streak_halt_until", default=0) == 0  # and never halts


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
        position={
            "rule_name": "turtle_breakout",
            "opened_at": None,
            "entry_fill": None,
            "qty": None,
        },
        exit_fill=Decimal("100"),
        exit_qty=Decimal("1"),
        fees=Decimal("0"),
        is_dca=False,
        now_ts=NOW,
    )
    assert repo.get_trade_outcomes() == []
    assert repo.get_state("consecutive_losses", default=0) == 0


def test_pnl_net_subtracts_the_entry_fee_as_well_as_the_exit_fee() -> None:
    """BOTH legs' fees, matching `SimAccount.close`, which nets `entry_fee` and `exit_fee`.

    Rail 16's threshold is meant to be set from a `keel simulate` sweep. If live subtracted only
    the exit leg, live's loss definition would be strictly looser than the sim's, and a
    threshold tuned on sim streaks would be systematically loose in production -- the breaker
    would fire later than the sweep predicted, on real money.
    """
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
            "entry_fee": Decimal("0.30"),
        },
        exit_fill=Decimal("100.50"),  # +0.50 gross
        exit_qty=Decimal("1"),
        fees=Decimal("0.30"),  # -0.10 net once BOTH legs are counted
        is_dca=False,
        now_ts=NOW,
    )
    assert repo.get_trade_outcomes()[0]["pnl_net"] == Decimal("-0.10")
    assert repo.get_state("consecutive_losses") == 1


def test_a_position_without_entry_fee_context_still_records() -> None:
    """Legacy/degraded positions carry no `entry_fee`. Treat it as 0 rather than skipping the
    whole outcome: unlike a missing entry PRICE (which would fabricate the P&L's sign), a
    missing fee only understates the cost, and dropping the record would hide the trade from
    rail 16 entirely."""
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
        exit_fill=Decimal("99"),
        exit_qty=Decimal("1"),
        fees=Decimal("0"),
        is_dca=False,
        now_ts=NOW,
    )
    assert repo.get_trade_outcomes()[0]["pnl_net"] == Decimal("-1")


# --- book_exit(is_dca=None): each FIFO leg by its own tranche (#857, #860; plan R9) ----------


def _mixed_paxg(repo: Repository) -> None:
    """PAXG's real shape: turtle tranche 3 is the OLDEST row, a DCA tranche sits behind it."""
    repo.open_position(
        product_id="PAXG-USD",
        rule_name="turtle_breakout",
        opened_at=1,
        qty=Decimal("0.0132"),
        entry_fill=Decimal("4673.23"),
        entry_fee=Decimal("0.73"),
    )
    repo.open_position(
        product_id="PAXG-USD",
        rule_name="dca",
        opened_at=2,
        qty=Decimal("0.01"),
        entry_fill=Decimal("4400"),
        entry_fee=Decimal("0.40"),
    )


_PAXG_EXIT = {"id": 99, "actual_fill": Decimal("4300"), "fee": Decimal("0.20")}


def test_a_mixed_paxg_sale_books_each_leg_by_its_own_tranche() -> None:
    """#860 / spec Q10: FIFO reaches turtle tranche 3 first. With `is_dca=None` the turtle leg
    is a RULE outcome (it counts toward rail 16) and the DCA leg is not. Both legs lose here, so
    a single falsy flag would count two losses, not one."""
    repo, config = _repo(), _config()
    repo.set_state("consecutive_losses", 0)
    _mixed_paxg(repo)

    streak.book_exit(
        repo,
        config,
        product_id="PAXG-USD",
        exit_order=_PAXG_EXIT,
        sold_qty=None,
        is_dca=None,
        now_ts=NOW,
    )

    outcomes = repo.get_trade_outcomes()
    assert [(o["rule_name"], o["is_dca"]) for o in outcomes] == [
        ("turtle_breakout", False),
        ("dca", True),
    ]
    assert all(o["pnl_net"] < 0 for o in outcomes), "both legs lose; only one may count"
    assert repo.get_state("consecutive_losses") == 1, "only the turtle loss counts"


def test_a_partial_none_sale_stopping_inside_the_dca_tranche_books_only_the_turtle_leg() -> None:
    """The leg the sale stops INSIDE is carried, not booked -- whatever its derived flag."""
    repo, config = _repo(), _config()
    repo.set_state("consecutive_losses", 0)
    _mixed_paxg(repo)

    streak.book_exit(
        repo,
        config,
        product_id="PAXG-USD",
        exit_order=_PAXG_EXIT,
        sold_qty=Decimal("0.015"),
        is_dca=None,
        now_ts=NOW,
    )

    assert [(o["rule_name"], o["is_dca"]) for o in repo.get_trade_outcomes()] == [
        ("turtle_breakout", False)
    ]
    [left] = repo.get_open_positions("PAXG-USD")
    assert (left["rule_name"], left["qty"]) == ("dca", Decimal("0.0082"))
    assert repo.get_state("consecutive_losses") == 1


@pytest.mark.parametrize("flag", [True, False])
def test_a_bool_flag_still_books_every_leg_with_that_flag(flag: bool) -> None:
    """R9: existing callers pass a bool and see today's behaviour exactly -- the caller's flag
    on every leg, whatever each tranche's own `rule_name` says."""
    repo, config = _repo(), _config()
    repo.set_state("consecutive_losses", 0)
    _mixed_paxg(repo)

    streak.book_exit(
        repo,
        config,
        product_id="PAXG-USD",
        exit_order=_PAXG_EXIT,
        sold_qty=None,
        is_dca=flag,
        now_ts=NOW,
    )

    outcomes = repo.get_trade_outcomes()
    assert [(o["rule_name"], o["is_dca"]) for o in outcomes] == [
        ("turtle_breakout", flag),
        ("dca", flag),
    ]
    assert repo.get_state("consecutive_losses") == (0 if flag else 2)
