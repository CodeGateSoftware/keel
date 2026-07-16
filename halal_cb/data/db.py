"""SQLite connection + schema management for halal_cb.

Standard-library `sqlite3` only (no ORM) per the design spec §6. `connect()` returns a
`sqlite3.Connection` configured with a `Row` factory (dict-like row access) and foreign keys
enabled. `migrate()` idempotently creates the eight §6 tables (`transactions`, `candles`,
`orders`, `rules`, `signals`, `backtests`, `pnl_daily`, `agent_state`, `journal`) plus their
indexes and a `schema_version` marker table.

Money and prices are stored as `TEXT` holding the exact `str(Decimal(...))` representation so
they round-trip without floating-point error; `repository.py` owns that conversion.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

# Creation order matters for readability (and for backends that validate FK targets eagerly);
# SQLite itself only checks FK targets at DML time, but we still declare referenced tables first.
_SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,
        params TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'candidate',
        created_at INTEGER,
        promoted_at INTEGER,
        demoted_at INTEGER
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_rules_status ON rules(status)",
    """
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mode TEXT NOT NULL,
        product_id TEXT NOT NULL,
        side TEXT NOT NULL,
        order_type TEXT,
        qty TEXT NOT NULL,
        limit_price TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        fee TEXT,
        expected_fill TEXT,
        actual_fill TEXT,
        raw_response TEXT,
        confirmation TEXT,
        rule_id INTEGER,
        created_at INTEGER,
        updated_at INTEGER,
        FOREIGN KEY (rule_id) REFERENCES rules(id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)",
    "CREATE INDEX IF NOT EXISTS idx_orders_rule_id ON orders(rule_id)",
    """
    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        coinbase_id TEXT UNIQUE,
        source TEXT NOT NULL,
        type TEXT NOT NULL,
        asset TEXT NOT NULL,
        ts INTEGER NOT NULL,
        qty TEXT NOT NULL,
        price TEXT,
        subtotal TEXT,
        total TEXT,
        fees TEXT,
        notes TEXT,
        rule_id INTEGER,
        order_id INTEGER,
        FOREIGN KEY (rule_id) REFERENCES rules(id),
        FOREIGN KEY (order_id) REFERENCES orders(id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_transactions_asset ON transactions(asset)",
    "CREATE INDEX IF NOT EXISTS idx_transactions_ts ON transactions(ts)",
    """
    CREATE TABLE IF NOT EXISTS candles (
        product_id TEXT NOT NULL,
        granularity TEXT NOT NULL,
        ts INTEGER NOT NULL,
        o TEXT NOT NULL,
        h TEXT NOT NULL,
        l TEXT NOT NULL,
        c TEXT NOT NULL,
        v TEXT NOT NULL,
        PRIMARY KEY (product_id, granularity, ts)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rule_id INTEGER,
        product_id TEXT NOT NULL,
        ts INTEGER NOT NULL,
        indicators TEXT,
        cts_score TEXT,
        fired INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY (rule_id) REFERENCES rules(id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_signals_rule_id ON signals(rule_id)",
    "CREATE INDEX IF NOT EXISTS idx_signals_product_ts ON signals(product_id, ts)",
    """
    CREATE TABLE IF NOT EXISTS backtests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rule_id INTEGER,
        n_trades INTEGER,
        win_rate TEXT,
        avg_win TEXT,
        avg_loss TEXT,
        expectancy TEXT,
        max_dd TEXT,
        max_losing_streak INTEGER,
        mfe TEXT,
        mae TEXT,
        period_start INTEGER,
        period_end INTEGER,
        FOREIGN KEY (rule_id) REFERENCES rules(id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_backtests_rule_id ON backtests(rule_id)",
    """
    CREATE TABLE IF NOT EXISTS pnl_daily (
        date TEXT NOT NULL,
        asset TEXT NOT NULL,
        qty TEXT NOT NULL,
        avg_cost TEXT NOT NULL,
        price TEXT NOT NULL,
        realized TEXT NOT NULL,
        unrealized TEXT NOT NULL,
        PRIMARY KEY (date, asset)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_pnl_daily_asset ON pnl_daily(asset)",
    """
    CREATE TABLE IF NOT EXISTS agent_state (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS journal (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER NOT NULL,
        emotion_score TEXT,
        rules_followed INTEGER,
        errors_made TEXT,
        dollar_impact TEXT,
        chart_note TEXT,
        screenshot_ref TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_journal_ts ON journal(ts)",
)


def connect(path: str | Path = "halal_cb.db") -> sqlite3.Connection:
    """Open a `sqlite3.Connection` to `path` (or an in-memory DB for `":memory:"`).

    Configures dict-like `Row` access and turns on foreign-key enforcement, which SQLite
    otherwise leaves off per-connection by default.
    """
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """Create all §6 tables + indexes if absent, and record `schema_version`.

    Safe to call repeatedly: every statement is `IF NOT EXISTS`, and the version row is only
    inserted the first time.
    """
    for statement in _SCHEMA_STATEMENTS:
        conn.execute(statement)

    version_row = conn.execute("SELECT version FROM schema_version").fetchone()
    if version_row is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))

    conn.commit()
