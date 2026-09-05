"""Storage for the per-currency available/total balance pair a cycle observed -- issue #719.

`equity_points` (#698) records the cycle's TOTAL cash and its two mark-to-market legs; it has
never recorded what the venue itself reports for any one currency's settled-versus-total split.
`cycle_balances` is that record: one row per currency per reading, deliberately separate from
`equity_points` because the split is a fact about ONE currency and `equity_points.cash` is
already a cross-currency sum (`agent._mark_to_market_parts`'s stated no-FX bound).

Same conventions as `equity_points` throughout: `mode` partitions paper from live, money is TEXT
holding `str(Decimal(...))`, and a bounded read keeps the MOST RECENT rows while still returning
them oldest first.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from keel_core.types import CycleBalance

from keel.data.db import connect, migrate
from keel.data.repository import Repository

NOW = 1_800_000_000
DAY = 86_400


@pytest.fixture()
def repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    return Repository(conn)


def _balance(
    ts: int = NOW,
    mode: str = "live",
    currency: str = "USD",
    available: str | None = "9000.25",
    total: str | None = "9500.50",
) -> CycleBalance:
    return CycleBalance(
        ts=ts,
        mode=mode,
        currency=currency,
        available=None if available is None else Decimal(available),
        total=None if total is None else Decimal(total),
    )


def test_the_schema_carries_the_table() -> None:
    conn = connect(":memory:")
    migrate(conn)
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "cycle_balances" in names


def test_a_fresh_database_carries_no_balances(repo: Repository) -> None:
    assert repo.get_cycle_balances() == []


def test_a_balance_round_trips_exactly(repo: Repository) -> None:
    repo.record_cycle_balance(_balance())
    assert repo.get_cycle_balances() == [_balance()]


def test_money_is_stored_as_exact_decimal_strings(repo: Repository) -> None:
    """The standing TEXT convention, checked at the storage layer rather than trusted."""
    repo.record_cycle_balance(_balance(available="9000.25", total="9500.50"))
    row = repo._conn.execute(  # noqa: SLF001
        "SELECT available, total FROM cycle_balances"
    ).fetchone()
    assert (row["available"], row["total"]) == ("9000.25", "9500.50")


def test_an_unobserved_total_round_trips_as_none_not_zero(repo: Repository) -> None:
    """NULL means NOT OBSERVED, never zero (the `cycle_balances` DDL comment, `db.py`). A venue
    that answered the available leg and not the total must not have a zero total invented for it."""
    repo.record_cycle_balance(_balance(total=None))
    got = repo.get_cycle_balances()[0]
    assert got.total is None
    assert got.available == Decimal("9000.25")


def test_an_unobserved_available_round_trips_as_none_not_zero(repo: Repository) -> None:
    """The symmetric case: a venue that answered the total and not the available-to-trade split."""
    repo.record_cycle_balance(_balance(available=None))
    got = repo.get_cycle_balances()[0]
    assert got.available is None
    assert got.total == Decimal("9500.50")


def test_balances_read_back_oldest_first(repo: Repository) -> None:
    for offset in (2 * DAY, 0, DAY):
        repo.record_cycle_balance(_balance(ts=NOW + offset))
    assert [b.ts for b in repo.get_cycle_balances()] == [NOW, NOW + DAY, NOW + 2 * DAY]


def test_a_mode_reads_back_only_its_own_balances(repo: Repository) -> None:
    repo.record_cycle_balance(_balance(ts=NOW, mode="paper", available="100"))
    repo.record_cycle_balance(_balance(ts=NOW + DAY, mode="live", available="250"))
    assert [b.available for b in repo.get_cycle_balances(mode="paper")] == [Decimal("100")]
    assert [b.available for b in repo.get_cycle_balances(mode="live")] == [Decimal("250")]
    assert len(repo.get_cycle_balances()) == 2


def test_a_currency_filter_reads_back_only_that_currency(repo: Repository) -> None:
    repo.record_cycle_balance(_balance(ts=NOW, currency="USD", available="100"))
    repo.record_cycle_balance(_balance(ts=NOW + DAY, currency="USDC", available="250"))
    assert [b.available for b in repo.get_cycle_balances(currency="USD")] == [Decimal("100")]
    assert [b.available for b in repo.get_cycle_balances(currency="USDC")] == [Decimal("250")]


def test_one_reading_writes_one_row_per_currency(repo: Repository) -> None:
    """The point of the per-currency shape: two currencies observed in the same cycle are two
    rows, `available` and `total` kept separate per currency."""
    repo.record_cycle_balance(_balance(ts=NOW, currency="USD", available="1000", total="1000"))
    repo.record_cycle_balance(_balance(ts=NOW, currency="USDC", available="7", total="9"))

    rows = {b.currency: b for b in repo.get_cycle_balances()}
    assert set(rows) == {"USD", "USDC"}
    assert rows["USD"].available == Decimal("1000")
    assert rows["USDC"].total == Decimal("9")


def test_a_since_window_is_bounded_by_epoch_order_not_string_order(repo: Repository) -> None:
    """Guards the `ts INTEGER` choice, mirroring `equity_points`' own regression."""
    repo.record_cycle_balance(_balance(ts=999_999_999))
    repo.record_cycle_balance(_balance(ts=NOW))
    assert [b.ts for b in repo.get_cycle_balances(since_ts=NOW)] == [NOW]


def test_a_limit_takes_the_MOST_RECENT_readings_still_oldest_first(repo: Repository) -> None:
    for offset in range(5):
        repo.record_cycle_balance(
            _balance(ts=NOW + offset * DAY, available=str(1000 + offset))
        )

    got = repo.get_cycle_balances(limit=2)

    assert [b.ts for b in got] == [NOW + 3 * DAY, NOW + 4 * DAY]
    assert [b.available for b in got] == [Decimal("1003"), Decimal("1004")]


def test_a_limit_composes_with_the_mode_and_currency_partition(repo: Repository) -> None:
    for offset in range(5):
        repo.record_cycle_balance(_balance(ts=NOW + offset * DAY, mode="paper", currency="USD"))
    for offset in range(5, 8):
        repo.record_cycle_balance(
            _balance(ts=NOW + offset * DAY, mode="live", currency="USD", available="250")
        )

    got = repo.get_cycle_balances(mode="live", currency="USD", limit=2)

    assert [b.ts for b in got] == [NOW + 6 * DAY, NOW + 7 * DAY]


def test_an_existing_database_gains_the_table_on_migration() -> None:
    conn = connect(":memory:")
    migrate(conn)
    conn.execute("UPDATE schema_version SET version = 19")
    conn.execute("DROP TABLE cycle_balances")
    conn.commit()
    migrate(conn)
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "cycle_balances" in names


def test_a_legacy_database_missing_the_table_does_not_break_reads() -> None:
    """Defensive, mirroring `get_series_feeds`'s own rescue: `mcp.tools._open_readonly_repo`
    deliberately never migrates, so a pre-v20 database handed to a shared read seam is a real
    shape a reader must tolerate. Missing table reads as "nothing recorded", which is true."""
    conn = connect(":memory:")
    migrate(conn)
    conn.execute("DROP TABLE cycle_balances")
    conn.commit()
    repo = Repository(conn)
    assert repo.get_cycle_balances() == []


def test_a_locked_database_is_raised_not_read_as_unrecorded(repo: Repository) -> None:
    """The missing-table rescue must catch ONLY a missing table, not a lock timeout -- the same
    property `get_series_feeds` pins for `candle_series_feed`."""
    import sqlite3

    class _Locked:
        row_factory = sqlite3.Row

        def execute(self, *_args: object, **_kwargs: object) -> object:
            raise sqlite3.OperationalError("database is locked")

    repo._conn = _Locked()  # type: ignore[assignment]  # noqa: SLF001
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        repo.get_cycle_balances()
