"""Tests for versioned schema migration, and the §4.1 subscription backfill.

The backfill moves a hand-tuned allowance onto a live spend cap, so it is tested for exactness
(the value survives), idempotency (re-running changes nothing), and honesty (the migrated row is
`suspect`, not a guessed tier).
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from keel.data import db


def _v1_database() -> sqlite3.Connection:
    """A database as it existed before this change: schema at v1, subscription in agent_state."""
    conn = db.connect(":memory:")
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version (version) VALUES (1)")
    conn.execute("CREATE TABLE IF NOT EXISTS agent_state (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute(
        "INSERT INTO agent_state (key, value) VALUES (?, ?)",
        (
            "subscription",
            json.dumps(
                {
                    "monthly_allowance_usd": {"__decimal__": "750.25"},
                    "pacing": "even_daily",
                    "updated_at": 1_700_000_000,
                }
            ),
        ),
    )
    conn.commit()
    return conn


def _subscription_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM broker_subscriptions").fetchall())


def test_fresh_database_is_stamped_at_the_current_version() -> None:
    conn = db.connect(":memory:")
    db.migrate(conn)
    version = conn.execute("SELECT version FROM schema_version").fetchone()["version"]
    assert version == db.SCHEMA_VERSION == 12


def test_fresh_database_gets_no_subscription_row() -> None:
    """keel ships inert: no attested record means no live BUY, by design."""
    conn = db.connect(":memory:")
    db.migrate(conn)
    assert _subscription_rows(conn) == []


def test_v1_database_backfills_the_singleton_subscription() -> None:
    conn = _v1_database()
    db.migrate(conn)

    rows = _subscription_rows(conn)
    assert len(rows) == 1
    row = rows[0]
    assert row["venue"] == "coinbase"
    assert row["free_volume_usd"] == "750.25"
    assert row["pacing"] == "even_daily"


def test_the_migrated_row_forces_an_explicit_attestation() -> None:
    """Guessing which tier a hand-tuned number meant would set a live cap from an inference."""
    conn = _v1_database()
    db.migrate(conn)

    row = _subscription_rows(conn)[0]
    assert row["tier_name"] == "unknown"
    assert row["status"] == "suspect"
    assert row["attest_due_ts"] == row["attested_at"]


def test_migration_bumps_the_stored_version() -> None:
    conn = _v1_database()
    db.migrate(conn)
    stamped = conn.execute("SELECT version FROM schema_version").fetchone()["version"]
    assert stamped == db.SCHEMA_VERSION


def test_migration_is_idempotent() -> None:
    conn = _v1_database()
    db.migrate(conn)
    db.migrate(conn)
    db.migrate(conn)
    assert len(_subscription_rows(conn)) == 1


def test_migration_does_not_overwrite_an_existing_row() -> None:
    """A user who already attested must not be silently reset by a re-run."""
    conn = _v1_database()
    db.migrate(conn)
    conn.execute(
        "UPDATE broker_subscriptions SET tier_name = 'Preferred', status = 'active'"
    )
    conn.commit()

    db.migrate(conn)

    row = _subscription_rows(conn)[0]
    assert row["tier_name"] == "Preferred"
    assert row["status"] == "active"


def test_the_old_agent_state_row_is_left_in_place() -> None:
    """It costs nothing and is the only copy of the pre-migration value."""
    conn = _v1_database()
    db.migrate(conn)
    assert (
        conn.execute("SELECT value FROM agent_state WHERE key = 'subscription'").fetchone()
        is not None
    )


def test_v1_database_without_a_subscription_migrates_to_no_row() -> None:
    conn = _v1_database()
    conn.execute("DELETE FROM agent_state WHERE key = 'subscription'")
    conn.commit()

    db.migrate(conn)

    assert _subscription_rows(conn) == []
    stamped = conn.execute("SELECT version FROM schema_version").fetchone()["version"]
    assert stamped == db.SCHEMA_VERSION


@pytest.mark.parametrize("stored", ["750.25", 750.25])
def test_backfill_accepts_both_decimal_encodings(stored: object) -> None:
    """Older rows may hold a bare number rather than the tagged-Decimal form."""
    conn = _v1_database()
    conn.execute(
        "UPDATE agent_state SET value = ? WHERE key = 'subscription'",
        (json.dumps({"monthly_allowance_usd": stored, "pacing": "opportunistic"}),),
    )
    conn.commit()

    db.migrate(conn)

    assert _subscription_rows(conn)[0]["free_volume_usd"] == "750.25"


def test_migration_to_v4_creates_the_positions_table() -> None:
    """The per-tranche ledger. Asserted against the `schema_version` TABLE, not
    `PRAGMA user_version` -- keel never writes the pragma, so a pragma assertion would read 0
    forever and pass/fail independently of the migration it claims to check."""
    conn = db.connect(":memory:")
    db.migrate(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(positions)")}
    assert cols >= {
        "id", "product_id", "rule_name", "opened_at", "closed_at",
        "qty", "entry_fill", "entry_fee", "bracket_order_id", "status",
    }
    stamped = conn.execute("SELECT version FROM schema_version").fetchone()["version"]
    assert stamped == db.SCHEMA_VERSION


def test_an_existing_v1_database_picks_up_the_positions_table() -> None:
    """The additive-DDL path: `_SCHEMA_STATEMENTS` runs BEFORE the version check, so an
    already-stamped database gets the new table from `CREATE TABLE IF NOT EXISTS` and the
    migration step only advances the stamp. Same pattern `trade_outcomes` used at v3."""
    conn = _v1_database()
    db.migrate(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(positions)")}
    assert "bracket_order_id" in cols
    stamped = conn.execute("SELECT version FROM schema_version").fetchone()["version"]
    assert stamped == db.SCHEMA_VERSION


def test_migration_to_v5_creates_the_candle_gap_probes_table() -> None:
    conn = db.connect(":memory:")
    db.migrate(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(candle_gap_probes)")}
    assert cols >= {"product", "granularity", "start_ts", "end_ts", "n_missing", "probed_at"}


def test_an_existing_v1_database_picks_up_the_gap_probes_table_EMPTY() -> None:
    """Additive DDL, and deliberately NO backfill.

    A row in this table asserts "we asked the venue for this window and it had nothing" -- an
    observation nobody has made for a pre-existing gap. Seeding it would silently suppress
    holes that were never probed, which is the single failure mode the table exists to prevent.
    """
    conn = _v1_database()
    db.migrate(conn)
    (count,) = conn.execute("SELECT COUNT(*) FROM candle_gap_probes").fetchone()
    assert count == 0
    stamped = conn.execute("SELECT version FROM schema_version").fetchone()["version"]
    assert stamped == db.SCHEMA_VERSION


def test_migration_to_v6_creates_the_asset_attestations_table_EMPTY() -> None:
    """Additive DDL, and deliberately NO backfill.

    A row asserts a human established this asset's sector and backing against a named source.
    Seeding one for the current allowlist would fabricate exactly the attestation the screen
    exists to demand -- for the three assets the project is most likely to stop questioning.
    """
    conn = _v1_database()
    db.migrate(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(asset_attestations)")}
    assert cols >= {"asset", "sector", "backing", "pays_yield", "source", "attested_by"}
    (count,) = conn.execute("SELECT COUNT(*) FROM asset_attestations").fetchone()
    assert count == 0


def test_migration_to_v9_creates_the_screen_exceptions_table_EMPTY() -> None:
    """Additive DDL, and deliberately NO backfill.

    A row asserts a human documented a waiver for one asset/criterion pair. Seeding one would
    fabricate an exception nobody granted; an empty table correctly says nothing has been
    excepted yet.
    """
    conn = _v1_database()
    db.migrate(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(screen_exceptions)")}
    assert cols >= {"asset", "criterion", "rationale", "granted_by", "granted_at"}
    (count,) = conn.execute("SELECT COUNT(*) FROM screen_exceptions").fetchone()
    assert count == 0
    stamped = conn.execute("SELECT version FROM schema_version").fetchone()["version"]
    assert stamped == db.SCHEMA_VERSION


def test_migration_to_v11_adds_orders_filled_quantity() -> None:
    """The column reconciliation records the venue-observed fill on (#446). `qty` keeps meaning
    the ORDERED size; `filled_quantity` is what the venue actually executed, NULL on every row
    written before the column existed."""
    conn = db.connect(":memory:")
    db.migrate(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(orders)")}
    assert "filled_quantity" in cols


def test_an_existing_orders_table_gains_filled_quantity_by_ALTER() -> None:
    """A `CREATE TABLE IF NOT EXISTS` addition is invisible to an already-stamped database
    (the v8 lesson: `profile.autonomous_until` silently never appeared). The v11 step must
    ALTER the live table, and existing rows must read back with `filled_quantity IS NULL` --
    "not observed", not zero, so a partial-fill reader can tell them apart."""
    conn = db.connect(":memory:")
    # A v10 database: the `orders` table exactly as v10 shipped it (no `filled_quantity`).
    conn.execute(
        """
        CREATE TABLE orders (
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
            updated_at INTEGER
        )
        """
    )
    conn.execute(
        """
        INSERT INTO orders (mode, product_id, side, qty, status, created_at, updated_at)
        VALUES ('live', 'BTC-USD', 'BUY', '0.01', 'filled', 1, 1)
        """
    )
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version (version) VALUES (10)")
    # v2's step reads `agent_state`; an empty one means "nothing to migrate", which is all it
    # needs from this fixture.
    conn.execute("CREATE TABLE agent_state (key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()

    db.migrate(conn)

    cols = {r["name"] for r in conn.execute("PRAGMA table_info(orders)")}
    assert "filled_quantity" in cols
    row = conn.execute("SELECT qty, status, filled_quantity FROM orders").fetchone()
    assert row["filled_quantity"] is None
    stamped = conn.execute("SELECT version FROM schema_version").fetchone()["version"]
    assert stamped == db.SCHEMA_VERSION
