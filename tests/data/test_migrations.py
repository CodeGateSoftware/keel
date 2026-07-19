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
    assert version == db.SCHEMA_VERSION == 2


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
    assert conn.execute("SELECT version FROM schema_version").fetchone()["version"] == 2


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
    assert conn.execute("SELECT version FROM schema_version").fetchone()["version"] == 2


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
