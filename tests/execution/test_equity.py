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
