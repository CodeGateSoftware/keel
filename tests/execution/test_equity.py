"""Tests for the equity/drawdown producer that rail 11 depends on.

Rail 11 shipped DORMANT: it reads `drawdown_total_pct`/`drawdown_weekly_pct` with a default of 0
and nothing ever wrote them, so the account-drawdown circuit breaker could not trip while reading
as enforced. The regression test at the bottom is the one that would have caught that.
"""

from __future__ import annotations

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
