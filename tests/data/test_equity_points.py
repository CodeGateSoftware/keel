"""Storage for the mark-to-market equity series -- issue #698.

The agent has always computed mark-to-market equity every cycle and always thrown it away:
`agent_state["equity_history"]` is a 7-day rolling window kept for the weekly drawdown rail, and
nothing else persisted. This table is the long-term record, and the tests below pin the two
properties that make it usable as one.

`mode` is the load-bearing partition. The DB is already one-per-profile (ADR 0002), but paper and
live flip WITHIN one database, and `_clear_live_mode_if_needed` wipes the shared HWM on every
flip precisely because one mode's equity is not the other's. A series that blended them would
draw a cliff between two unrelated accounts and call it a drawdown.

Money is TEXT holding `str(Decimal(...))`, the standing convention (`db.py` module docstring):
these rows feed a chart whose whole claim is that the numbers are the ones the engine acted on.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from keel_core.types import EquityReading

from keel.data.db import connect, migrate
from keel.data.repository import Repository

NOW = 1_800_000_000
DAY = 86_400


@pytest.fixture()
def repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    return Repository(conn)


def _point(
    ts: int = NOW,
    mode: str = "paper",
    equity: str = "10000.55",
    cash: str | None = "9000.25",
    unrealized: str | None = "-12.30",
    hwm: str = "10500.00",
) -> EquityReading:
    return EquityReading(
        ts=ts,
        mode=mode,
        equity=Decimal(equity),
        cash=None if cash is None else Decimal(cash),
        unrealized=None if unrealized is None else Decimal(unrealized),
        hwm=Decimal(hwm),
    )


def test_the_schema_carries_the_table() -> None:
    conn = connect(":memory:")
    migrate(conn)
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "equity_points" in names


def test_a_fresh_database_carries_no_points(repo: Repository) -> None:
    """No backfill, by design (`_migrate_v19_equity_points`): the 7-day window is a rail's
    working set that `record_external_flow` rewrites, and replaying it would publish equities
    the account never had."""
    assert repo.get_equity_points() == []


def test_a_point_round_trips_exactly(repo: Repository) -> None:
    repo.record_equity_point(_point())
    assert repo.get_equity_points() == [_point()]


def test_money_is_stored_as_exact_decimal_strings(repo: Repository) -> None:
    """The standing TEXT convention, checked at the storage layer rather than trusted: a float
    column would round 10000.55 and the chart's claim is that these are the numbers the engine
    acted on."""
    repo.record_equity_point(_point(equity="10000.55", cash="9000.25", unrealized="-12.30"))
    row = repo._conn.execute("SELECT equity, cash, unrealized, hwm FROM equity_points").fetchone()
    assert (row["equity"], row["cash"], row["unrealized"], row["hwm"]) == (
        "10000.55",
        "9000.25",
        "-12.30",
        "10500.00",
    )


def test_an_unrecorded_split_round_trips_as_none_not_zero(repo: Repository) -> None:
    """`None` means the cycle knew its total but not the split. Zero would state a flat cash
    position and a flat unrealized P&L, neither of which was observed."""
    repo.record_equity_point(_point(cash=None, unrealized=None))
    got = repo.get_equity_points()[0]
    assert got.cash is None
    assert got.unrealized is None
    assert got.equity == Decimal("10000.55")


def test_points_read_back_oldest_first(repo: Repository) -> None:
    """A chart draws left to right; ordering at the read keeps every caller from re-sorting."""
    for offset in (2 * DAY, 0, DAY):
        repo.record_equity_point(_point(ts=NOW + offset))
    assert [p.ts for p in repo.get_equity_points()] == [NOW, NOW + DAY, NOW + 2 * DAY]


def test_a_mode_reads_back_only_its_own_points(repo: Repository) -> None:
    """The partition that matters: paper and live share one database and flip within it, and
    `_clear_live_mode_if_needed` wipes the shared HWM on every flip for this reason."""
    repo.record_equity_point(_point(ts=NOW, mode="paper", equity="10000"))
    repo.record_equity_point(_point(ts=NOW + DAY, mode="live", equity="250"))
    assert [p.equity for p in repo.get_equity_points(mode="paper")] == [Decimal("10000")]
    assert [p.equity for p in repo.get_equity_points(mode="live")] == [Decimal("250")]
    assert len(repo.get_equity_points()) == 2


def test_a_since_window_is_bounded_by_epoch_order_not_string_order(repo: Repository) -> None:
    """Guards the `ts INTEGER` choice. As TEXT, "1800086400" < "1800000000" is false but
    "999999999" > "1800000000" is true -- a window straddling a digit-count change would drop
    or admit the wrong rows, silently."""
    repo.record_equity_point(_point(ts=999_999_999))
    repo.record_equity_point(_point(ts=NOW))
    assert [p.ts for p in repo.get_equity_points(since_ts=NOW)] == [NOW]


def test_a_limit_takes_the_MOST_RECENT_readings_still_oldest_first(repo: Repository) -> None:
    """A bounded read has to keep the END of the series, not the beginning. The chart's subject
    is where the account is now; the first N rows ever written are the least interesting answer
    to that, and on a long-running deployment they are also the ones furthest from the truth."""
    for offset in range(5):
        repo.record_equity_point(_point(ts=NOW + offset * DAY, equity=str(1000 + offset)))

    got = repo.get_equity_points(limit=2)

    assert [p.ts for p in got] == [NOW + 3 * DAY, NOW + 4 * DAY]
    assert [p.equity for p in got] == [Decimal("1003"), Decimal("1004")]


def test_a_limit_larger_than_the_table_returns_everything(repo: Repository) -> None:
    repo.record_equity_point(_point(ts=NOW))
    assert len(repo.get_equity_points(limit=500)) == 1


def test_a_limit_composes_with_the_mode_partition(repo: Repository) -> None:
    """The limit must be applied WITHIN the mode, not to a blended read that is then filtered --
    otherwise asking for the last two live readings on a paper-heavy database returns nothing."""
    for offset in range(5):
        repo.record_equity_point(_point(ts=NOW + offset * DAY, mode="paper"))
    for offset in range(5, 8):
        repo.record_equity_point(_point(ts=NOW + offset * DAY, mode="live", equity="250"))

    got = repo.get_equity_points(mode="live", limit=2)

    assert [p.ts for p in got] == [NOW + 6 * DAY, NOW + 7 * DAY]


def test_the_recorded_count_is_readable_without_reading_the_rows(repo: Repository) -> None:
    """What lets a bounded chart say how much it is NOT showing. A count is the whole reason the
    truncation can be stated honestly instead of the series quietly starting wherever the cap
    happened to fall."""
    for offset in range(7):
        repo.record_equity_point(_point(ts=NOW + offset * DAY))

    assert repo.count_equity_points() == 7
    assert repo.count_equity_points(mode="live") == 0


def test_an_existing_database_gains_the_table_on_migration() -> None:
    conn = connect(":memory:")
    migrate(conn)
    # Literal 18, not `SCHEMA_VERSION - 1`: every version pin in tests/data/ is a literal
    # precisely so a bump is acknowledged rather than silently absorbed.
    conn.execute("UPDATE schema_version SET version = 18")
    conn.execute("DROP TABLE equity_points")
    conn.commit()
    migrate(conn)
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "equity_points" in names
