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
