"""What feed a cached series actually came from -- issue #696.

keel's liquidity statistic is `median(volume * close)` over cached candles, and it silently
assumed the venue's feed sees the whole market. That holds on a crypto exchange; it does not
hold on Alpaca's IEX feed, which reports one US equity exchange's own executions (IEX publishes
its overall share as roughly 3.8% for Q2 2026). The cost-fidelity measurement found MSFT cached
at $186M/day against a model anchored at $500M.

The half of that fixed here is the PROVENANCE, and it is the load-bearing half: before this,
"what feed produced these bars" was inferred from whatever config happened to be loaded at READ
time, so a database filled under IEX could be judged under a SIP setting and nothing would say
so. A gate built on an inferred value would encode the bug it was meant to fix, which is why
this lands before the gate does.

**Provenance is keyed on `(product_id, granularity, feed)`, not overwritten per series.** A
series fetched under both feeds records BOTH rows, because that is what happened. A single
mutable column would let the most recent fetch erase the fact that most of the bars came from
somewhere else -- and a mixed series is exactly the case a reader most needs to be warned about.
"""

from __future__ import annotations

import sqlite3
from decimal import Decimal

import pytest
from keel_core.types import Candle, Granularity

from keel.data.db import SCHEMA_VERSION, connect, migrate
from keel.data.repository import Repository


@pytest.fixture()
def repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    return Repository(conn)


def _candles(n: int = 3) -> list[Candle]:
    return [
        Candle(
            ts=i * 86400,
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100"),
            volume=Decimal("1000"),
        )
        for i in range(n)
    ]


def test_the_schema_carries_a_series_feed_table() -> None:
    conn = connect(":memory:")
    migrate(conn)
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "candle_series_feed" in names


def test_migrating_twice_is_a_no_op() -> None:
    conn = connect(":memory:")
    migrate(conn)
    migrate(conn)
    assert int(conn.execute("SELECT version FROM schema_version").fetchone()["version"]) == (
        SCHEMA_VERSION
    )


def test_an_existing_database_gains_the_table_on_migration() -> None:
    """The live deployments are at v16 with years of candles. The upgrade must add the table
    without touching a single cached bar."""
    conn = connect(":memory:")
    migrate(conn)
    conn.execute("UPDATE schema_version SET version = 16")
    conn.execute("DROP TABLE candle_series_feed")
    conn.commit()
    migrate(conn)
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "candle_series_feed" in names


def test_a_series_with_no_recorded_feed_reports_nothing(repo: Repository) -> None:
    """NOT "reports the default feed". Every bar cached before this existed has unknown
    provenance, and saying so is the only honest answer -- inventing one would put a
    fabricated claim into the table a gate is about to read."""
    repo.upsert_candles("MSFT-USD", Granularity.ONE_DAY, _candles())
    assert repo.get_series_feeds("MSFT-USD", Granularity.ONE_DAY) == ()


def test_upserting_with_a_feed_records_it(repo: Repository) -> None:
    repo.upsert_candles("MSFT-USD", Granularity.ONE_DAY, _candles(), feed="alpaca:iex")
    assert repo.get_series_feeds("MSFT-USD", Granularity.ONE_DAY) == ("alpaca:iex",)


def test_a_series_fetched_under_two_feeds_reports_both(repo: Repository) -> None:
    """The case a single mutable column would erase, and the one a reader most needs."""
    repo.upsert_candles("MSFT-USD", Granularity.ONE_DAY, _candles(), feed="alpaca:iex")
    repo.upsert_candles("MSFT-USD", Granularity.ONE_DAY, _candles(), feed="alpaca:sip")
    assert repo.get_series_feeds("MSFT-USD", Granularity.ONE_DAY) == ("alpaca:iex", "alpaca:sip")


def test_re_fetching_under_the_same_feed_does_not_duplicate(repo: Repository) -> None:
    repo.upsert_candles("MSFT-USD", Granularity.ONE_DAY, _candles(), feed="alpaca:iex")
    repo.upsert_candles("MSFT-USD", Granularity.ONE_DAY, _candles(), feed="alpaca:iex")
    assert repo.get_series_feeds("MSFT-USD", Granularity.ONE_DAY) == ("alpaca:iex",)


def test_provenance_is_per_granularity(repo: Repository) -> None:
    """A daily series and an hourly one can genuinely come from different feeds -- Alpaca mints
    hourly bars only inside a session -- so they are recorded separately."""
    repo.upsert_candles("MSFT-USD", Granularity.ONE_DAY, _candles(), feed="alpaca:sip")
    repo.upsert_candles("MSFT-USD", Granularity.ONE_HOUR, _candles(), feed="alpaca:iex")
    assert repo.get_series_feeds("MSFT-USD", Granularity.ONE_DAY) == ("alpaca:sip",)
    assert repo.get_series_feeds("MSFT-USD", Granularity.ONE_HOUR) == ("alpaca:iex",)


def test_provenance_is_per_product(repo: Repository) -> None:
    repo.upsert_candles("MSFT-USD", Granularity.ONE_DAY, _candles(), feed="alpaca:iex")
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _candles(), feed="coinbase")
    assert repo.get_series_feeds("BTC-USD", Granularity.ONE_DAY) == ("coinbase",)


def test_the_window_each_feed_contributed_is_recorded(repo: Repository) -> None:
    """First and last time this feed was seen writing this series. `doctor` needs it to say
    "the IEX rows stopped in March" rather than only "this series is mixed"."""
    repo.upsert_candles("MSFT-USD", Granularity.ONE_DAY, _candles(), feed="alpaca:iex", now_ts=100)
    repo.upsert_candles("MSFT-USD", Granularity.ONE_DAY, _candles(), feed="alpaca:iex", now_ts=500)
    row = repo.get_series_feed_window("MSFT-USD", Granularity.ONE_DAY, "alpaca:iex")
    assert row == (100, 500)


def test_an_unknown_feed_window_is_none(repo: Repository) -> None:
    assert repo.get_series_feed_window("MSFT-USD", Granularity.ONE_DAY, "alpaca:iex") is None


def test_writing_no_candles_records_no_provenance(repo: Repository) -> None:
    """A fetch that returned nothing is not evidence that this feed served this series."""
    repo.upsert_candles("MSFT-USD", Granularity.ONE_DAY, [], feed="alpaca:iex")
    assert repo.get_series_feeds("MSFT-USD", Granularity.ONE_DAY) == ()


def test_an_empty_feed_string_is_refused(repo: Repository) -> None:
    """`feed=""` is a caller bug that would otherwise record provenance meaning nothing, and
    would then read back as a feed whose scope cannot be looked up."""
    with pytest.raises(ValueError, match="feed"):
        repo.upsert_candles("MSFT-USD", Granularity.ONE_DAY, _candles(), feed="")


def test_the_candles_themselves_are_untouched_by_provenance(repo: Repository) -> None:
    repo.upsert_candles("MSFT-USD", Granularity.ONE_DAY, _candles(3), feed="alpaca:iex")
    assert len(repo.get_candles("MSFT-USD", Granularity.ONE_DAY)) == 3
    cols = {
        r["name"]
        for r in repo._conn.execute("PRAGMA table_info(candles)")  # noqa: SLF001
    }
    assert cols == {"product_id", "granularity", "ts", "o", "h", "l", "c", "v"}


def test_feeds_are_returned_in_a_stable_order(repo: Repository) -> None:
    """Sorted, so a caller rendering "iex, sip" cannot produce a different string on a
    different day for the same database."""
    repo.upsert_candles("X-USD", Granularity.ONE_DAY, _candles(), feed="zzz")
    repo.upsert_candles("X-USD", Granularity.ONE_DAY, _candles(), feed="aaa")
    assert repo.get_series_feeds("X-USD", Granularity.ONE_DAY) == ("aaa", "zzz")


def test_a_legacy_database_missing_the_table_does_not_break_reads() -> None:
    """Defensive: a hand-patched or partially-migrated file must not turn a liquidity read into
    a crash. Missing table reads as "no provenance recorded", which is true."""
    conn = connect(":memory:")
    migrate(conn)
    conn.execute("DROP TABLE candle_series_feed")
    conn.commit()
    repo = Repository(conn)
    assert repo.get_series_feeds("MSFT-USD", Granularity.ONE_DAY) == ()
    assert repo.get_series_feed_window("MSFT-USD", Granularity.ONE_DAY, "alpaca:iex") is None


def test_a_locked_database_is_raised_not_read_as_unrecorded(repo: Repository) -> None:
    """The missing-table rescue must catch ONLY a missing table.

    `sqlite3.OperationalError` is also what a lock timeout raises, and keel reads and writes this
    file from more than one process (see `db.connect`'s WAL note). Swallowing a lock would report
    "scope unrecorded" for a series whose scope is sitting on disk -- turning a retryable error
    into the `None` verdict that `feed_scope` reserves for the absence of evidence, and doing it
    silently. A caught mutant is what put this test here: the rescue started life as a bare
    `except`, and nothing failed.
    """

    class _Locked:
        row_factory = sqlite3.Row

        def execute(self, *_args: object, **_kwargs: object) -> object:
            raise sqlite3.OperationalError("database is locked")

    repo._conn = _Locked()  # type: ignore[assignment]  # noqa: SLF001
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        repo.get_series_feeds("MSFT-USD", Granularity.ONE_DAY)
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        repo.get_series_feed_window("MSFT-USD", Granularity.ONE_DAY, "alpaca:iex")
