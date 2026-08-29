"""Tests for keel.data.db: connect() and migrate()."""

from __future__ import annotations

import sqlite3

import pytest

from keel.data.db import connect, migrate

EXPECTED_TABLES = {
    "transactions",
    "candles",
    "orders",
    "rules",
    "signals",
    "backtests",
    "pnl_daily",
    "agent_state",
    "journal",
    "schema_version",
}


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {row["name"] for row in rows}


def test_connect_returns_row_factory_connection_with_foreign_keys_on():
    conn = connect(":memory:")
    try:
        assert conn.row_factory is sqlite3.Row
        fk_status = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk_status == 1
    finally:
        conn.close()


def test_migrate_creates_all_schema_tables():
    conn = connect(":memory:")

    migrate(conn)

    assert EXPECTED_TABLES <= _table_names(conn)


def test_migrate_is_idempotent():
    conn = connect(":memory:")

    migrate(conn)
    migrate(conn)  # must not raise, must not duplicate schema_version rows

    version_rows = conn.execute("SELECT version FROM schema_version").fetchall()
    assert len(version_rows) == 1


def test_migrate_creates_expected_indexes():
    conn = connect(":memory:")

    migrate(conn)

    index_rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL"
    ).fetchall()
    index_names = {row["name"] for row in index_rows}
    assert any(name.startswith("idx_") for name in index_names)


def test_candles_table_has_composite_primary_key():
    conn = connect(":memory:")

    migrate(conn)

    columns = conn.execute("PRAGMA table_info(candles)").fetchall()
    pk_columns = {row["name"] for row in columns if row["pk"] > 0}
    assert pk_columns == {"product_id", "granularity", "ts"}


def test_transactions_table_enforces_unique_coinbase_id():
    conn = connect(":memory:")
    migrate(conn)
    conn.execute(
        "INSERT INTO transactions (coinbase_id, source, type, asset, ts, qty) "
        "VALUES ('tx-1', 'csv_import', 'buy', 'BTC', 1700000000, '1')"
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO transactions (coinbase_id, source, type, asset, ts, qty) "
            "VALUES ('tx-1', 'csv_import', 'buy', 'BTC', 1700000001, '2')"
        )


def test_agent_state_table_has_key_primary_key():
    conn = connect(":memory:")

    migrate(conn)

    columns = conn.execute("PRAGMA table_info(agent_state)").fetchall()
    pk_columns = {row["name"] for row in columns if row["pk"] > 0}
    assert pk_columns == {"key"}


def test_schema_version_is_13():
    """Deliberate tripwire: bump this literal consciously on every schema change."""
    from keel.data.db import SCHEMA_VERSION

    assert SCHEMA_VERSION == 14


def test_a_v6_database_migrates_up_and_gains_the_profile_table(tmp_path):
    from keel.data.db import SCHEMA_VERSION, connect, migrate

    conn = connect(str(tmp_path / "old.db"))
    migrate(conn)
    conn.execute("UPDATE schema_version SET version = 6")
    conn.commit()

    migrate(conn)
    assert int(conn.execute("SELECT version FROM schema_version").fetchone()["version"]) == (
        SCHEMA_VERSION
    )
    named = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='profile'"
    ).fetchone()
    assert named is not None, "v7 must add the profile table"


def test_a_v7_database_without_the_expiry_column_gains_it(tmp_path):
    """Regression: `autonomous_until` was added to v7's CREATE TABLE, but a database already
    stamped 7 runs no migration step and IF NOT EXISTS is a no-op -- so it kept the old table
    forever, and `keel autonomy off` (the DE-RISKING command) died on a missing column."""
    import sqlite3

    from keel.data.db import SCHEMA_VERSION, connect, migrate

    path = str(tmp_path / "v7.db")
    conn = connect(path)
    migrate(conn)
    # Recreate the pre-fix v7 shape: profile without the expiry column, stamped at 7.
    conn.execute("DROP TABLE profile")
    conn.execute(
        "CREATE TABLE profile (id INTEGER PRIMARY KEY CHECK (id = 1), "
        "autonomous INTEGER NOT NULL DEFAULT 0, updated_ts INTEGER NOT NULL)"
    )
    conn.execute("INSERT INTO profile (id, autonomous, updated_ts) VALUES (1, 1, 5)")
    conn.execute("UPDATE schema_version SET version = 7")
    conn.commit()

    migrate(conn)

    cols = [r["name"] for r in conn.execute("PRAGMA table_info(profile)")]
    assert "autonomous_until" in cols, "v8 must add the expiry column to an existing v7 table"
    assert int(conn.execute("SELECT version FROM schema_version").fetchone()["version"]) == (
        SCHEMA_VERSION
    )
    # the recorded choice survives, and writing to it no longer raises
    from keel.data.repository import Repository

    repo = Repository(conn)
    assert repo.get_profile().autonomous is True
    repo.set_autonomous(False, now_ts=9)  # must not raise sqlite3.OperationalError
    assert repo.get_profile().autonomous is False
    assert isinstance(conn, sqlite3.Connection)


def test_migrating_a_v6_database_twice_is_idempotent(tmp_path):
    """v8's ALTER must not fire twice on a DB that already got the column from the DDL."""
    from keel.data.db import SCHEMA_VERSION, connect, migrate

    conn = connect(str(tmp_path / "v6.db"))
    migrate(conn)
    conn.execute("UPDATE schema_version SET version = 6")
    conn.commit()
    migrate(conn)
    migrate(conn)  # must not raise "duplicate column name"
    assert int(conn.execute("SELECT version FROM schema_version").fetchone()["version"]) == (
        SCHEMA_VERSION
    )


def test_a_v8_database_migrates_up_and_gains_the_screen_exceptions_table(tmp_path):
    """v9 is a documented no-op migration: `_SCHEMA_STATEMENTS` (IF NOT EXISTS) already creates
    the new table on an existing v8 DB before the version loop runs, so the migration step itself
    has nothing to do -- unlike v8's ADD COLUMN case, which needed real work."""
    from keel.data.db import SCHEMA_VERSION, connect, migrate

    conn = connect(str(tmp_path / "v8.db"))
    migrate(conn)
    conn.execute("UPDATE schema_version SET version = 8")
    conn.commit()

    migrate(conn)

    assert int(conn.execute("SELECT version FROM schema_version").fetchone()["version"]) == (
        SCHEMA_VERSION
    )
    named = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='screen_exceptions'"
    ).fetchone()
    assert named is not None, "v9 must add the screen_exceptions table"


def test_migrating_a_v8_database_twice_is_idempotent(tmp_path):
    from keel.data.db import SCHEMA_VERSION, connect, migrate

    conn = connect(str(tmp_path / "v8_twice.db"))
    migrate(conn)
    conn.execute("UPDATE schema_version SET version = 8")
    conn.commit()
    migrate(conn)
    migrate(conn)  # must not raise
    assert int(conn.execute("SELECT version FROM schema_version").fetchone()["version"]) == (
        SCHEMA_VERSION
    )


def test_a_fresh_database_has_the_instrument_attestations_table():
    conn = connect(":memory:")

    migrate(conn)

    named = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='instrument_attestations'"
    ).fetchone()
    assert named is not None


def test_a_v9_database_migrates_up_and_gains_the_instrument_attestations_table(tmp_path):
    """v10 is a documented no-op migration, same shape as v9: `_SCHEMA_STATEMENTS` (IF NOT
    EXISTS) already creates the new table on an existing v9 DB before the version loop runs, so
    the migration step itself has nothing to do."""
    from keel.data.db import SCHEMA_VERSION, connect, migrate

    conn = connect(str(tmp_path / "v9.db"))
    migrate(conn)
    conn.execute("UPDATE schema_version SET version = 9")
    conn.commit()

    migrate(conn)

    assert int(conn.execute("SELECT version FROM schema_version").fetchone()["version"]) == (
        SCHEMA_VERSION
    )
    named = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='instrument_attestations'"
    ).fetchone()
    assert named is not None, "v10 must add the instrument_attestations table"
    (count,) = conn.execute("SELECT COUNT(*) FROM instrument_attestations").fetchone()
    assert count == 0, "no backfill: seeding rows would fabricate an attestation nobody made"


def test_migrating_a_v9_database_twice_is_idempotent(tmp_path):
    from keel.data.db import SCHEMA_VERSION, connect, migrate

    conn = connect(str(tmp_path / "v9_twice.db"))
    migrate(conn)
    conn.execute("UPDATE schema_version SET version = 9")
    conn.commit()
    migrate(conn)
    migrate(conn)  # must not raise
    assert int(conn.execute("SELECT version FROM schema_version").fetchone()["version"]) == (
        SCHEMA_VERSION
    )
