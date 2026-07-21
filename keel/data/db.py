"""SQLite connection + schema management for keel.

Standard-library `sqlite3` only (no ORM) per the design spec §6. `connect()` returns a
`sqlite3.Connection` configured with a `Row` factory (dict-like row access) and foreign keys
enabled. `migrate()` idempotently creates the §6 tables (`transactions`, `candles`,
`orders`, `rules`, `signals`, `backtests`, `pnl_daily`, `agent_state`, `broker_subscriptions`,
`trade_outcomes`, `positions`, `journal`) plus their indexes and a `schema_version` marker table.

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

SCHEMA_VERSION = 7

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
    # One row per TRANCHE, not per product. `agent_state["position_rule:<product>"]` was a single
    # JSON blob keyed by product, so a second entry overwrote the first's entry price and qty --
    # and a bracket from the FIRST tranche filling later computed its P&L against the SECOND
    # tranche's entry, feeding an inflated loss to `trade_outcomes` and rail 16's counter.
    # `bracket_order_id` is the ONE linkage direction: a position names its bracket, never the
    # reverse, so reconciliation starts from a filled order row and finds the tranche that owns it.
    """
    CREATE TABLE IF NOT EXISTS positions (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id        TEXT    NOT NULL,
        rule_name         TEXT    NOT NULL,
        opened_at         INTEGER NOT NULL,
        closed_at         INTEGER,
        qty               TEXT    NOT NULL,
        entry_fill        TEXT    NOT NULL,
        entry_fee         TEXT    NOT NULL,
        bracket_order_id  INTEGER,
        status            TEXT    NOT NULL DEFAULT 'open',
        FOREIGN KEY (bracket_order_id) REFERENCES orders(id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_positions_open ON positions (product_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_positions_bracket ON positions (bracket_order_id)",
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
    CREATE TABLE IF NOT EXISTS trade_outcomes (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id    TEXT NOT NULL,
        rule_name     TEXT,
        is_dca        INTEGER NOT NULL,
        opened_at     INTEGER NOT NULL,
        closed_at     INTEGER NOT NULL,
        qty           TEXT NOT NULL,
        entry_fill    TEXT NOT NULL,
        exit_fill     TEXT NOT NULL,
        fees          TEXT NOT NULL,
        pnl_net       TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_trade_outcomes_closed_at ON trade_outcomes(closed_at)",
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
    """
    CREATE TABLE IF NOT EXISTS candle_gap_probes (
        product      TEXT NOT NULL,
        granularity  TEXT NOT NULL,
        start_ts     INTEGER NOT NULL,
        end_ts       INTEGER NOT NULL,
        n_missing    INTEGER NOT NULL,
        probed_at    INTEGER NOT NULL,
        PRIMARY KEY (product, granularity, start_ts, end_ts)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_gap_probes_product ON candle_gap_probes(product, granularity)",
    """
    CREATE TABLE IF NOT EXISTS asset_attestations (
        asset        TEXT PRIMARY KEY,
        sector       TEXT NOT NULL,
        backing      TEXT NOT NULL,
        pays_yield   INTEGER NOT NULL,
        source       TEXT NOT NULL,
        attested_by  TEXT NOT NULL,
        attested_at  INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS profile (
        id          INTEGER PRIMARY KEY CHECK (id = 1),
        autonomous  INTEGER NOT NULL DEFAULT 0,
        updated_ts  INTEGER NOT NULL
    )
    """,
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
    # Venue-scoped, not table-wide: the engine is single-venue today so this is behaviourally
    # identical either way, but a table-wide guard would skip a *second* venue's backfill once
    # one exists, just because some other venue already has a row.
    already_migrated = conn.execute(
        "SELECT 1 FROM broker_subscriptions WHERE venue = 'coinbase' LIMIT 1"
    ).fetchone()
    if already_migrated is not None:
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


def _migrate_v3_trade_outcomes(conn: sqlite3.Connection) -> None:
    """v3 adds `trade_outcomes`. Table creation is handled by `_SCHEMA_STATEMENTS`; there is
    deliberately NO backfill.

    Historical `orders` rows cannot be reliably paired into round-trips (partial fills, scale-outs,
    positions opened before entry context was tracked). Rail 16's threshold is derived from streak
    statistics, so seeding it with guessed history would be worse than starting empty.
    """


def _migrate_v4_positions(conn: sqlite3.Connection) -> None:
    """v4 adds `positions`, the per-tranche ledger. Table creation is handled by
    `_SCHEMA_STATEMENTS`; there is deliberately NO backfill.

    The pre-v4 carrier was `agent_state["position_rule:<product>"]`, a last-write-wins blob that
    holds at most ONE tranche per product and, on a database that averaged up, holds the newest
    one's entry against the whole holding. Synthesising tranches from it would manufacture exactly
    the mis-attribution this table exists to end. An open position from before the upgrade simply
    has no ledger row: `_record_fill` then skips its outcome rather than inventing an entry price,
    the same standard `record_closed_trade` already applies.
    """


def _migrate_v5_candle_gap_probes(conn: sqlite3.Connection) -> None:
    """v5 adds `candle_gap_probes`. Table creation is handled by `_SCHEMA_STATEMENTS`; there is
    deliberately NO backfill.

    A row here asserts "we asked the venue for this window and it had nothing", which is a
    claim about an observation we have not made for any pre-existing gap. Seeding it would
    silently suppress holes that were never probed -- the one failure mode this table exists to
    prevent. An empty table simply means every gap is still unproven, which is true.
    """


def _migrate_v6_asset_attestations(conn: sqlite3.Connection) -> None:
    """v6 adds `asset_attestations`. Table creation is handled by `_SCHEMA_STATEMENTS`; there is
    deliberately NO backfill.

    A row asserts that a human established this asset's sector and backing against a named
    source. Seeding one for BTC/ETH/PAXG because they happen to be in the current allowlist would
    fabricate exactly the attestation the screen exists to demand -- and would do it for the three
    assets the project is most likely to stop questioning. An empty table correctly says nothing
    has been attested yet.
    """


def _migrate_v7_profile(conn: sqlite3.Connection) -> None:
    """v7 adds `profile`, which carries the user's autonomy choice. Table creation is handled by
    `_SCHEMA_STATEMENTS`; there is deliberately NO backfill.

    No row means `get_profile()` reports `autonomous=False`, which is the correct and safe
    reading of an upgraded database: the user has never opted into unattended trading, so we must
    not infer that they did. Seeding a row here -- even an explicitly `autonomous=0` one -- would
    only manufacture a consent record that no human gave.
    """


_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    2: _migrate_v2_broker_subscriptions,
    3: _migrate_v3_trade_outcomes,
    4: _migrate_v4_positions,
    5: _migrate_v5_candle_gap_probes,
    6: _migrate_v6_asset_attestations,
    7: _migrate_v7_profile,
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

    Requires `conn.row_factory` to be `sqlite3.Row` (as `connect()` returns) -- this function
    subscripts rows by column name (`row["value"]`, `version_row["version"]`).
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
