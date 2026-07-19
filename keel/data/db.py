"""SQLite connection + schema management for keel.

Standard-library `sqlite3` only (no ORM) per the design spec §6. `connect()` returns a
`sqlite3.Connection` configured with a `Row` factory (dict-like row access) and foreign keys
enabled. `migrate()` idempotently creates the eight §6 tables (`transactions`, `candles`,
`orders`, `rules`, `signals`, `backtests`, `pnl_daily`, `agent_state`, `journal`) plus their
indexes and a `schema_version` marker table.

Money and prices are stored as `TEXT` holding the exact `str(Decimal(...))` representation so
they round-trip without floating-point error; `repository.py` owns that conversion.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2

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
    CREATE TABLE IF NOT EXISTS broker_subscriptions (
        venue                   TEXT PRIMARY KEY,
        tier_name               TEXT NOT NULL,
        free_volume_usd         TEXT,
        pacing                  TEXT NOT NULL,
        subscription_usd_month  TEXT NOT NULL,
        status                  TEXT NOT NULL,
        attested_at             INTEGER NOT NULL,
        attest_due_ts           INTEGER NOT NULL
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


def _decode_stored_decimal(value: Any) -> str | None:
    """Read a money value out of an `agent_state` JSON blob as an exact string.

    `repository.py` encodes `Decimal` as `{"__decimal__": "..."}`; older rows may hold a bare
    number. Decoded here rather than imported from `repository` so the migration keeps working
    regardless of what that module does later -- a migration must be frozen against the shape of
    the data it was written for.
    """
    if value is None:
        return None
    if isinstance(value, dict):
        tagged = value.get("__decimal__")
        return None if tagged is None else str(tagged)
    return str(value)


def _migrate_v2_broker_subscriptions(conn: sqlite3.Connection) -> None:
    """Move the singleton `agent_state['subscription']` onto a venue-keyed row.

    The old blob carries a `monthly_allowance_usd` the user has possibly tuned by hand. It
    becomes `venue='coinbase'`'s `free_volume_usd` with `tier_name='unknown'` and
    `status='suspect'`, forcing one explicit attestation rather than silently guessing which
    tier the number corresponded to -- guessing here would set a live spend cap from an
    inference.

    Idempotent by construction: it skips when a row already exists, so a user who has since
    attested is never reset. The `agent_state` row is deliberately left in place as the only
    copy of the pre-migration value.
    """
    if conn.execute("SELECT 1 FROM broker_subscriptions LIMIT 1").fetchone() is not None:
        return

    row = conn.execute(
        "SELECT value FROM agent_state WHERE key = 'subscription'"
    ).fetchone()
    if row is None:
        return

    stored = json.loads(row["value"])
    free_volume = _decode_stored_decimal(stored.get("monthly_allowance_usd"))
    if free_volume is None:
        return

    attested_at = int(stored.get("updated_at") or int(time.time()))
    conn.execute(
        """
        INSERT INTO broker_subscriptions (
            venue, tier_name, free_volume_usd, pacing,
            subscription_usd_month, status, attested_at, attest_due_ts
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "coinbase",
            "unknown",
            free_volume,
            stored.get("pacing") or "opportunistic",
            "0",
            "suspect",
            attested_at,
            attested_at,  # already due: forces an attestation before the next live BUY
        ),
    )


_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    2: _migrate_v2_broker_subscriptions,
}


def connect(path: str | Path = "keel.db") -> sqlite3.Connection:
    """Open a `sqlite3.Connection` to `path` (or an in-memory DB for `":memory:"`).

    Configures dict-like `Row` access and turns on foreign-key enforcement, which SQLite
    otherwise leaves off per-connection by default.
    """
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """Create all tables + indexes if absent, then run any outstanding migration steps.

    Safe to call repeatedly: every DDL statement is `IF NOT EXISTS`, and each migration step
    runs only while the stored version is below its target. A fresh database is stamped at
    `SCHEMA_VERSION` and runs no steps -- correct, since it has nothing to migrate.
    """
    for statement in _SCHEMA_STATEMENTS:
        conn.execute(statement)

    version_row = conn.execute("SELECT version FROM schema_version").fetchone()
    if version_row is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
        conn.commit()
        return

    current = int(version_row["version"])
    for target in sorted(_MIGRATIONS):
        if current < target:
            _MIGRATIONS[target](conn)
            conn.execute("UPDATE schema_version SET version = ?", (target,))
            current = target
    conn.commit()
