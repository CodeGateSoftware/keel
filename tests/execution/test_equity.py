"""Tests for the equity/drawdown producer that rail 11 depends on.

Rail 11 shipped DORMANT: it reads `drawdown_total_pct`/`drawdown_weekly_pct` with a default of 0
and nothing ever wrote them, so the account-drawdown circuit breaker could not trip while reading
as enforced. The regression test at the bottom is the one that would have caught that.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.execution import equity

NOW = 1_800_000_000
DAY = 86_400


def _repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    return Repository(conn)


def test_first_update_sets_the_high_water_mark_and_zero_drawdown() -> None:
    repo = _repo()
    equity.update_drawdown(repo, equity=Decimal("10000"), now_ts=NOW)
    assert repo.get_state("equity_high_water_mark") == Decimal("10000")
    assert repo.get_state("drawdown_total_pct") == Decimal("0")


def test_a_new_peak_raises_the_high_water_mark() -> None:
    repo = _repo()
    equity.update_drawdown(repo, equity=Decimal("10000"), now_ts=NOW)
    equity.update_drawdown(repo, equity=Decimal("12000"), now_ts=NOW + DAY)
    assert repo.get_state("equity_high_water_mark") == Decimal("12000")
    assert repo.get_state("drawdown_total_pct") == Decimal("0")


def test_drawdown_is_measured_from_the_peak_not_from_deposits() -> None:
    """Drawdown-from-deposit would read 0% forever on an account in profit."""
    repo = _repo()
    equity.update_drawdown(repo, equity=Decimal("10000"), now_ts=NOW)
    equity.update_drawdown(repo, equity=Decimal("12000"), now_ts=NOW + DAY)
    equity.update_drawdown(repo, equity=Decimal("9000"), now_ts=NOW + 2 * DAY)
    assert repo.get_state("drawdown_total_pct") == Decimal("0.25")   # 3000/12000


def test_the_high_water_mark_never_falls() -> None:
    repo = _repo()
    equity.update_drawdown(repo, equity=Decimal("12000"), now_ts=NOW)
    equity.update_drawdown(repo, equity=Decimal("9000"), now_ts=NOW + DAY)
    assert repo.get_state("equity_high_water_mark") == Decimal("12000")


def test_weekly_drawdown_uses_a_rolling_7_day_peak() -> None:
    """Rolling, not calendar: a calendar reset clears the breaker every Monday regardless of
    conditions."""
    repo = _repo()
    equity.update_drawdown(repo, equity=Decimal("10000"), now_ts=NOW)
    equity.update_drawdown(repo, equity=Decimal("8000"), now_ts=NOW + DAY)
    assert repo.get_state("drawdown_weekly_pct") == Decimal("0.2")


def test_a_peak_older_than_7_days_no_longer_binds_the_weekly_drawdown() -> None:
    """The negative for the test above -- proves the window actually rolls."""
    repo = _repo()
    equity.update_drawdown(repo, equity=Decimal("10000"), now_ts=NOW)
    equity.update_drawdown(repo, equity=Decimal("8000"), now_ts=NOW + 8 * DAY)
    assert repo.get_state("drawdown_weekly_pct") == Decimal("0")


def test_rail11_actually_trips_once_the_producer_runs() -> None:
    """THE REGRESSION TEST. Rail 11 was unfireable because these keys were never written.
    This fails if the producer stops writing them."""
    from keel.execution.guards import check

    repo = _repo()
    repo.set_state("kill_switch", False)
    repo.set_state("last_feed_ts", NOW)
    equity.update_drawdown(repo, equity=Decimal("10000"), now_ts=NOW)
    equity.update_drawdown(repo, equity=Decimal("7000"), now_ts=NOW + DAY)   # 30% > 20% cap

    from tests.execution.test_guards import _config, _intent, _keys

    result = check(_intent(), repo, _config(), NOW + DAY)
    assert "account_dd_breaker_total" in _keys(result)


# -- external cash flows: deposits and withdrawals are not P&L ---------------------------------


def test_a_withdrawal_lowers_the_high_water_mark_by_the_same_amount() -> None:
    """THE BUG THIS FIXES. Equity = cash + positions, so a deposit ratchets the monotonic HWM
    up and a later withdrawal then reads as a drawdown that never recovers.

    Deposit 5000 on a 10000 account (HWM -> 15000), withdraw it a week later, and without flow
    accounting the account reads 5000/15000 = 33% drawdown >= the 20% cap: rail 11 vetoes every
    entry, permanently, with zero trading losses.
    """
    repo = _repo()
    equity.update_drawdown(repo, equity=Decimal("10000"), now_ts=NOW)
    equity.record_external_flow(repo, amount=Decimal("5000"))          # deposit
    equity.update_drawdown(repo, equity=Decimal("15000"), now_ts=NOW + DAY)
    assert repo.get_state("equity_high_water_mark") == Decimal("15000")

    equity.record_external_flow(repo, amount=Decimal("-5000"))         # withdraw it again
    equity.update_drawdown(repo, equity=Decimal("10000"), now_ts=NOW + 2 * DAY)

    assert repo.get_state("equity_high_water_mark") == Decimal("10000")
    assert repo.get_state("drawdown_total_pct") == Decimal("0")


def test_a_deposit_does_not_mask_an_existing_trading_drawdown() -> None:
    """The other direction, and the one that matters for safety: depositing into a LOSING
    account must not shrink the measured drawdown. Down 20% on 10000 (equity 8000), deposit
    2000 -> equity 10000. Without adjusting the HWM that reads as a full recovery and disarms
    the breaker; the 20% trading loss has not gone anywhere.
    """
    repo = _repo()
    equity.update_drawdown(repo, equity=Decimal("10000"), now_ts=NOW)
    equity.update_drawdown(repo, equity=Decimal("8000"), now_ts=NOW + DAY)
    assert repo.get_state("drawdown_total_pct") == Decimal("0.2")

    equity.record_external_flow(repo, amount=Decimal("2000"))
    equity.update_drawdown(repo, equity=Decimal("10000"), now_ts=NOW + 2 * DAY)

    # HWM rebased 10000 -> 12000, so the 2000 trading loss is still visible rather than erased.
    # It reads 2000/12000 = 16.7%, not the original 20%: rebasing measures the loss against the
    # larger post-deposit base. That is the standard flow-adjusted treatment and, importantly,
    # it is the CONSERVATIVE half of the choice -- the unadjusted alternative reads 0%.
    assert repo.get_state("equity_high_water_mark") == Decimal("12000")
    assert repo.get_state("drawdown_total_pct") == Decimal("2000") / Decimal("12000")


def test_a_flow_also_rebases_the_rolling_weekly_peak() -> None:
    """`drawdown_weekly_pct` is measured against a 7-day peak held in `equity_history`. Leaving
    that unshifted would reintroduce the same phantom drawdown on the weekly rail for a week."""
    repo = _repo()
    equity.update_drawdown(repo, equity=Decimal("10000"), now_ts=NOW)

    equity.record_external_flow(repo, amount=Decimal("-3000"))
    equity.update_drawdown(repo, equity=Decimal("7000"), now_ts=NOW + DAY)

    assert repo.get_state("drawdown_weekly_pct") == Decimal("0")


def test_a_flow_before_the_first_cycle_is_a_no_op() -> None:
    """On a fresh DB there is no HWM to rebase -- the first cycle seeds it from observed equity,
    which already includes the deposit. Adjusting nothing is correct; inventing a HWM is not."""
    repo = _repo()
    equity.record_external_flow(repo, amount=Decimal("5000"))

    assert repo.get_state("equity_high_water_mark") is None

    equity.update_drawdown(repo, equity=Decimal("5000"), now_ts=NOW)
    assert repo.get_state("equity_high_water_mark") == Decimal("5000")
    assert repo.get_state("drawdown_total_pct") == Decimal("0")


def test_an_undeclared_equity_jump_is_warned_about_but_never_auto_adjusted(caplog) -> None:
    """Detection WARNS, it never adjusts. Inferring a withdrawal from a balance movement and
    lowering the HWM on that inference would silently mask a real trading drawdown -- the
    fail-open direction. A misread deposit is an annoyance; a misread withdrawal disarms the
    breaker. So the operator declares flows and the agent only ever complains.
    """
    repo = _repo()
    equity.update_drawdown(repo, equity=Decimal("10000"), now_ts=NOW)

    with caplog.at_level(logging.WARNING):
        equity.update_drawdown(repo, equity=Decimal("4000"), now_ts=NOW + DAY)

    assert "equity.unexplained_jump" in caplog.text
    # ...and the drawdown is still computed the conservative way, unadjusted
    assert repo.get_state("drawdown_total_pct") == Decimal("0.6")


# -- sizing equity vs pending purification (#490) ----------------------------------------------


def _reward_tx(coinbase_id: str, total: str, tx_type: str = "Reward Income") -> dict[str, object]:
    """A transactions-table row of the shape `Repository.upsert_transaction` writes -- the same
    shape `keel data import` produces from a Coinbase export, which is where reward income
    enters the repo (USDC Rewards accrue inside the trading account and reach the ledger, not
    the config)."""
    return {
        "coinbase_id": coinbase_id,
        "source": "coinbase",
        "type": tx_type,
        "asset": "USDC",
        "ts": 1_700_000_000,
        "qty": Decimal("1"),
        "price": Decimal("1"),
        "subtotal": Decimal(total),
        "total": Decimal(total),
        "fees": Decimal("0"),
    }


def test_sizing_equity_subtracts_pending_purification() -> None:
    """The #490 invariant: `sizing_equity == mark_to_market - pending_purification`. Interest
    left sitting in the balance would inflate the equity the sizing formula reads from
    (discussion #472) -- riba compounding into position size."""
    assert equity.sizing_equity(Decimal("42000"), Decimal("2000")) == Decimal("40000")


def test_sizing_equity_with_nothing_pending_is_the_mark_to_market() -> None:
    """A clean ledger (or a path with no reward accruals) must be unchanged -- purification
    subtracts only what actually accrued, never a default haircut."""
    assert equity.sizing_equity(Decimal("42000"), Decimal("0")) == Decimal("42000")


def test_sizing_equity_floors_at_zero_rather_than_going_negative() -> None:
    """Pending purification can exceed the mark-to-market read (a reward-heavy ledger against a
    mostly-withdrawn account). A negative equity base would size a NEGATIVE position; zero is
    the floor -- and `sizing.size` off zero risks zero, which is the correct no-trade answer."""
    assert equity.sizing_equity(Decimal("1500"), Decimal("2000")) == Decimal("0")


def test_pending_purification_usd_counts_only_non_compliant_income() -> None:
    """The purification input is `build_report(...).total_owed_usd` over the repo's imported
    transactions: non-compliant credits count, CLEAN trading activity does not, and `REVIEW`
    (unclassified) does not either -- over-purifying would misstate a religious obligation as
    fact (see `purification.classify`)."""
    repo = _repo()
    repo.upsert_transaction(_reward_tx("rx1", "2.50"))
    repo.upsert_transaction(_reward_tx("rx2", "1.25", tx_type="Incentives Rewards Payout"))
    repo.upsert_transaction(_reward_tx("cl1", "9999", tx_type="Buy"))
    repo.upsert_transaction(_reward_tx("rv1", "777", tx_type="Advanced Trade Fill"))

    assert equity.pending_purification_usd(repo) == Decimal("3.75")


def test_pending_purification_usd_is_zero_on_a_clean_ledger() -> None:
    repo = _repo()
    repo.upsert_transaction(_reward_tx("cl1", "500", tx_type="Buy"))

    assert equity.pending_purification_usd(repo) == Decimal("0")


# -- the persisted series (#698) ------------------------------------------------------------
#
# `equity_history` in `agent_state` is a 7-day window kept for the WEEKLY rail, and
# `record_external_flow` rewrites every point in it on a declared deposit. It is a rail's
# working set, so it cannot double as the record. These tests pin the durable one.


def test_a_cycle_appends_one_point_to_the_series() -> None:
    repo = _repo()
    repo.set_state("equity_state_mode", "paper")
    equity.update_drawdown(repo, equity=Decimal("10000"), now_ts=NOW)
    points = repo.get_equity_points()
    assert len(points) == 1
    assert points[0].ts == NOW
    assert points[0].equity == Decimal("10000")


def test_the_point_carries_the_high_water_mark_in_force_after_this_reading() -> None:
    """The chart's rail-11 overlay reads `hwm` off the row rather than recomputing a running
    maximum, because the two are not the same series: `record_external_flow` REBASES the HWM on
    a declared deposit, and a recomputed maximum would draw a ceiling the rail never used."""
    repo = _repo()
    repo.set_state("equity_state_mode", "paper")
    equity.update_drawdown(repo, equity=Decimal("12000"), now_ts=NOW)
    equity.update_drawdown(repo, equity=Decimal("9000"), now_ts=NOW + DAY)
    assert [p.hwm for p in repo.get_equity_points()] == [Decimal("12000"), Decimal("12000")]


def test_the_point_is_stamped_with_the_mode_that_produced_it() -> None:
    """`equity_state_mode` is the same stamp `_clear_live_mode_if_needed` reads before wiping
    the shared HWM on a flip. Deriving the mode a second way at the call site would let the two
    disagree, and a mislabelled row is worse than a missing one -- it lands in the wrong curve."""
    repo = _repo()
    repo.set_state("equity_state_mode", "live")
    equity.update_drawdown(repo, equity=Decimal("250"), now_ts=NOW)
    assert [p.mode for p in repo.get_equity_points()] == ["live"]


def test_a_mode_flip_leaves_two_series_not_one_blended_curve() -> None:
    """The whole reason for the `mode` column. Paper equity of $10k and live equity of $250 are
    two unrelated accounts; joined into one line the flip reads as a 97.5% drawdown that never
    happened."""
    repo = _repo()
    repo.set_state("equity_state_mode", "paper")
    equity.update_drawdown(repo, equity=Decimal("10000"), now_ts=NOW)
    # What the agent does on a flip: the shared scalars are cleared, then the stamp changes.
    repo.set_state("equity_high_water_mark", None)
    repo.set_state("equity_history", [])
    repo.set_state("equity_state_mode", "live")
    equity.update_drawdown(repo, equity=Decimal("250"), now_ts=NOW + DAY)

    assert [p.equity for p in repo.get_equity_points(mode="paper")] == [Decimal("10000")]
    assert [p.equity for p in repo.get_equity_points(mode="live")] == [Decimal("250")]


def test_the_split_is_recorded_when_the_caller_knows_it() -> None:
    repo = _repo()
    repo.set_state("equity_state_mode", "paper")
    equity.update_drawdown(
        repo,
        equity=Decimal("10000"),
        now_ts=NOW,
        cash=Decimal("9000"),
        unrealized=Decimal("-25.50"),
    )
    point = repo.get_equity_points()[0]
    assert point.cash == Decimal("9000")
    assert point.unrealized == Decimal("-25.50")


def test_an_unknown_split_is_recorded_as_none_not_zero() -> None:
    """A caller that knows only the total says so. Zero would assert a flat cash balance and a
    flat unrealized P&L, and nothing observed either."""
    repo = _repo()
    repo.set_state("equity_state_mode", "paper")
    equity.update_drawdown(repo, equity=Decimal("10000"), now_ts=NOW)
    point = repo.get_equity_points()[0]
    assert point.cash is None
    assert point.unrealized is None


def test_an_unstamped_mode_writes_no_point_and_still_updates_the_rail() -> None:
    """Rail 11 must never be held hostage to the chart. The agent stamps the mode before every
    `update_drawdown` (both branches do, unconditionally), so an unstamped call is not a real
    cycle -- and a row labelled with a guessed mode would land in the wrong curve, which is the
    one failure the partition exists to prevent. The scalars still advance."""
    repo = _repo()
    equity.update_drawdown(repo, equity=Decimal("10000"), now_ts=NOW)
    assert repo.get_equity_points() == []
    assert repo.get_state("equity_high_water_mark") == Decimal("10000")


# -- the persisted cycle_balances series (#719) -------------------------------------------------


def test_a_cycle_writes_one_cycle_balance_row_per_currency() -> None:
    """The write site's whole point: two currencies observed in the same cycle become two rows,
    `available` and `total` kept separate per currency -- never summed (that would cross the
    no-FX bound `agent._mark_to_market_parts` already states it will not cross)."""
    repo = _repo()
    repo.set_state("equity_state_mode", "live")
    equity.update_drawdown(
        repo,
        equity=Decimal("10000"),
        now_ts=NOW,
        cash=Decimal("9007"),
        balances=[
            ("USD", Decimal("1000"), Decimal("1000")),
            ("USDC", Decimal("7"), Decimal("9")),
        ],
    )
    rows = {b.currency: b for b in repo.get_cycle_balances()}
    assert set(rows) == {"USD", "USDC"}
    assert rows["USD"].available == Decimal("1000")
    assert rows["USD"].total == Decimal("1000")
    assert rows["USDC"].available == Decimal("7")
    assert rows["USDC"].total == Decimal("9")


def test_a_currency_with_no_observed_total_writes_it_as_none_not_zero() -> None:
    """NULL means NOT OBSERVED, never zero -- the `cycle_balances` DDL comment. A venue that
    answered `available` and not `total` must not have a zero total invented for it."""
    repo = _repo()
    repo.set_state("equity_state_mode", "live")
    equity.update_drawdown(
        repo,
        equity=Decimal("10000"),
        now_ts=NOW,
        balances=[("USD", Decimal("1000"), None)],
    )
    row = repo.get_cycle_balances()[0]
    assert row.available == Decimal("1000")
    assert row.total is None


def test_cycle_balances_carry_the_same_mode_stamp_as_the_equity_point() -> None:
    """Written under the SAME `equity_state_mode` read -- so one cycle cannot answer "which mode"
    two different ways."""
    repo = _repo()
    repo.set_state("equity_state_mode", "live")
    equity.update_drawdown(
        repo, equity=Decimal("10000"), now_ts=NOW, balances=[("USD", Decimal("1000"), None)]
    )
    point = repo.get_equity_points()[0]
    balance = repo.get_cycle_balances()[0]
    assert balance.mode == point.mode == "live"
    assert balance.ts == point.ts == NOW


def test_an_unstamped_mode_writes_no_cycle_balance_either() -> None:
    """Mirrors the equity-point rescue directly above: an unstamped call is not a real cycle, and
    a row labelled with a guessed mode would land in the wrong currency's curve."""
    repo = _repo()
    equity.update_drawdown(
        repo, equity=Decimal("10000"), now_ts=NOW, balances=[("USD", Decimal("1000"), None)]
    )
    assert repo.get_cycle_balances() == []


def test_no_balances_argument_writes_no_rows() -> None:
    """The paper branch never passes `balances=` at all (paper has no venue to observe) --
    proved here at the level `update_drawdown` itself can guarantee: the default writes nothing."""
    repo = _repo()
    repo.set_state("equity_state_mode", "paper")
    equity.update_drawdown(repo, equity=Decimal("10000"), now_ts=NOW)
    assert repo.get_cycle_balances() == []
