"""Tests for versioned schema migration, and the §4.1 subscription backfill.

The backfill moves a hand-tuned allowance onto a live spend cap, so it is tested for exactness
(the value survives), idempotency (re-running changes nothing), and honesty (the migrated row is
`suspect`, not a guessed tier).
"""

from __future__ import annotations

import json
import sqlite3
import time
from decimal import Decimal

import pytest

from keel.data import db
from keel.data.repository import Repository


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
    assert version == db.SCHEMA_VERSION == 14


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


def test_migration_to_v13_adds_the_partial_exit_accumulators() -> None:
    """The three columns a tranche closed in PIECES needs (#502): what has been sold, what it
    fetched gross, and the exit-leg fees already charged. `positions.qty` becomes what is STILL
    HELD; these carry the legs behind it until the one `trade_outcomes` row is written."""
    conn = db.connect(":memory:")
    db.migrate(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(positions)")}
    assert {"realized_qty", "realized_proceeds", "realized_fees"} <= cols


def test_an_existing_positions_table_gains_the_accumulators_by_ALTER() -> None:
    """The v8 lesson again: a `CREATE TABLE IF NOT EXISTS` addition is invisible to a database
    already stamped past v4, so the v13 step must ALTER the live table. Existing rows read back
    NULL, which the repository decodes to ZERO -- unlike `initial_stop`, because there is no
    difference between "never partially exited" and "partially exited nothing"."""
    conn = db.connect(":memory:")
    # A v12 database: `positions` exactly as v12 shipped it.
    conn.execute(
        """
        CREATE TABLE positions (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id        TEXT    NOT NULL,
            rule_name         TEXT    NOT NULL,
            opened_at         INTEGER NOT NULL,
            closed_at         INTEGER,
            qty               TEXT    NOT NULL,
            entry_fill        TEXT    NOT NULL,
            entry_fee         TEXT    NOT NULL,
            initial_stop      TEXT,
            bracket_order_id  INTEGER,
            status            TEXT    NOT NULL DEFAULT 'open'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO positions (product_id, rule_name, opened_at, qty, entry_fill, entry_fee)
        VALUES ('BTC-USD', 'r', 1, '0.1', '50000', '1')
        """
    )
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version (version) VALUES (12)")
    conn.execute("CREATE TABLE agent_state (key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()

    db.migrate(conn)

    cols = {r["name"] for r in conn.execute("PRAGMA table_info(positions)")}
    assert {"realized_qty", "realized_proceeds", "realized_fees"} <= cols
    row = conn.execute(
        "SELECT realized_qty, realized_proceeds, realized_fees FROM positions"
    ).fetchone()
    assert row["realized_qty"] is None
    assert row["realized_proceeds"] is None
    assert row["realized_fees"] is None
    assert Repository(conn).get_open_positions("BTC-USD")[0]["realized_qty"] == Decimal("0")
    stamped = conn.execute("SELECT version FROM schema_version").fetchone()["version"]
    assert stamped == db.SCHEMA_VERSION


# -- v14: venue_trade_scopes, and the #233 coinbase backfill -----------------


def _v12_database() -> sqlite3.Connection:
    """A database stamped at v12, with the `orders` table exactly as today's schema (unchanged
    since v11) so rows inserted here are what the v14 migration's backfill reads."""
    conn = db.connect(":memory:")
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version (version) VALUES (12)")
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
            filled_quantity TEXT,
            raw_response TEXT,
            confirmation TEXT,
            rule_id INTEGER,
            created_at INTEGER,
            updated_at INTEGER
        )
        """
    )
    conn.commit()
    return conn


def _insert_order(
    conn: sqlite3.Connection,
    *,
    mode: str,
    status: str,
    created_at: int | None,
    product_id: str = "BTC-USD",
) -> None:
    conn.execute(
        "INSERT INTO orders (mode, product_id, side, qty, status, created_at, updated_at) "
        "VALUES (?, ?, 'BUY', '0.01', ?, ?, ?)",
        (mode, product_id, status, created_at, created_at),
    )
    conn.commit()


def _trade_scope_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM venue_trade_scopes").fetchall())


def _create_venue_trade_scopes_table(conn: sqlite3.Connection) -> None:
    """Calling `_migrate_v14_venue_trade_scopes` directly (to reach its own early-return guard,
    bypassing `db.migrate()`'s version gate) skips the `_SCHEMA_STATEMENTS` pass that normally
    creates this table first -- so direct-call tests must create it themselves."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS venue_trade_scopes (
            venue           TEXT PRIMARY KEY,
            state           TEXT NOT NULL,
            attested_scope  TEXT,
            attested_ts     INTEGER,
            confirmed_ts    INTEGER,
            refuted_ts      INTEGER,
            refuted_reason  TEXT
        )
        """
    )
    conn.commit()


def test_v14_backfill_keeps_a_live_coinbase_deployment_trading() -> None:
    """THE test that matters most here. A running Coinbase deployment that has already placed a
    live order must come out of this migration still able to trade -- if it does not, rail 20's
    next cycle vetoes a live ENTRY on a venue that has been working the whole time, on an
    unattended deployment that trades daily. That is the production incident this backfill
    exists to prevent."""
    conn = _v12_database()
    _insert_order(conn, mode="live", status="filled", created_at=1_700_000_000)

    db.migrate(conn)

    scope = Repository(conn).get_venue_trade_scope("coinbase")
    assert scope is not None, (
        "no venue_trade_scopes row after upgrading a database with a live accepted order -- "
        "rail 20 will fail closed on the next live ENTRY and veto a venue that already works"
    )
    assert scope.may_place_live_entry() is True, (
        "backfilled coinbase record must permit a live entry, or upgrading breaks a venue that "
        "was already trading unattended"
    )


def test_v14_creates_the_venue_trade_scopes_table() -> None:
    conn = db.connect(":memory:")
    db.migrate(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(venue_trade_scopes)")}
    assert cols >= {
        "venue", "state", "attested_scope", "attested_ts", "confirmed_ts", "refuted_ts",
        "refuted_reason",
    }


def test_v14_fresh_database_gets_no_trade_scope_row() -> None:
    """keel ships inert: no order history means no backfill evidence, by design."""
    conn = db.connect(":memory:")
    db.migrate(conn)
    assert _trade_scope_rows(conn) == []


def test_v14_backfill_is_confirmed_not_attested() -> None:
    """The venue proved this, nobody attested it: attested_scope/attested_ts stay NULL."""
    conn = _v12_database()
    _insert_order(conn, mode="live", status="filled", created_at=1_700_000_000)
    db.migrate(conn)

    row = _trade_scope_rows(conn)[0]
    assert row["venue"] == "coinbase"
    assert row["state"] == "confirmed"
    assert row["attested_scope"] is None
    assert row["attested_ts"] is None
    assert row["confirmed_ts"] == 1_700_000_000
    assert row["refuted_ts"] is None


def test_v14_backfill_uses_the_most_recent_qualifying_order() -> None:
    conn = _v12_database()
    _insert_order(conn, mode="live", status="filled", created_at=1_600_000_000)
    _insert_order(conn, mode="live", status="canceled", created_at=1_700_000_000)
    db.migrate(conn)

    row = _trade_scope_rows(conn)[0]
    assert row["confirmed_ts"] == 1_700_000_000


def test_v14_backfill_falls_back_to_now_when_created_at_is_null() -> None:
    """Old rows may have no `created_at` at all; the fallback must still produce a usable
    timestamp rather than NULL."""
    conn = _v12_database()
    _insert_order(conn, mode="live", status="filled", created_at=None)
    before = int(time.time())

    db.migrate(conn)

    row = _trade_scope_rows(conn)[0]
    assert row["confirmed_ts"] is not None
    assert row["confirmed_ts"] >= before


def test_v14_paper_only_database_gets_no_backfill() -> None:
    """Mutation-relevant: the `mode = 'live'` filter must exclude paper orders, or a
    paper-trading-only database would be wrongly marked as having a confirmed live venue."""
    conn = _v12_database()
    _insert_order(conn, mode="paper", status="filled", created_at=1_700_000_000)
    db.migrate(conn)
    assert _trade_scope_rows(conn) == []


def test_v14_rejected_only_database_gets_no_backfill() -> None:
    """Mutation-relevant: a venue whose only live order was REFUSED must not be marked
    confirmed -- `rejected` means the broker declined the placement, not accepted it."""
    conn = _v12_database()
    _insert_order(conn, mode="live", status="rejected", created_at=1_700_000_000)
    db.migrate(conn)
    assert _trade_scope_rows(conn) == []


def test_v14_migration_is_idempotent() -> None:
    conn = _v12_database()
    _insert_order(conn, mode="live", status="filled", created_at=1_700_000_000)
    db.migrate(conn)
    db.migrate(conn)
    db.migrate(conn)
    assert len(_trade_scope_rows(conn)) == 1


def test_v14_migration_does_not_overwrite_an_existing_attestation() -> None:
    """`db.migrate()` itself is idempotent across repeated calls (it never re-invokes a step
    once the stored version reaches that step's target), which this also pins. It is NOT,
    however, a test of the migration function's own early-return guard -- see the direct-call
    test below for that."""
    conn = _v12_database()
    _insert_order(conn, mode="live", status="filled", created_at=1_700_000_000)
    db.migrate(conn)
    conn.execute(
        "UPDATE venue_trade_scopes SET state = 'attested', attested_scope = 'trading', "
        "attested_ts = 1800000000 WHERE venue = 'coinbase'"
    )
    conn.commit()

    db.migrate(conn)

    row = _trade_scope_rows(conn)[0]
    assert row["state"] == "attested"
    assert row["attested_scope"] == "trading"


def test_v14_migration_step_does_not_overwrite_an_existing_attestation_when_rerun() -> None:
    """Exercises `_migrate_v14_venue_trade_scopes`'s own early-return guard directly.

    `db.migrate()`'s version gate (`if current < target`) means this step is never invoked a
    second time through the public API once a database is stamped at 14 -- so a test that only
    calls `db.migrate()` twice (the test above) would stay green even if the guard were deleted
    outright. Calling the migration function itself, twice, is the only way to actually reach it.
    """
    conn = _v12_database()
    _create_venue_trade_scopes_table(conn)
    _insert_order(conn, mode="live", status="filled", created_at=1_700_000_000)
    db._migrate_v14_venue_trade_scopes(conn)
    conn.commit()
    conn.execute(
        "UPDATE venue_trade_scopes SET state = 'attested', attested_scope = 'trading', "
        "attested_ts = 1800000000 WHERE venue = 'coinbase'"
    )
    conn.commit()

    db._migrate_v14_venue_trade_scopes(conn)
    conn.commit()

    row = _trade_scope_rows(conn)[0]
    assert row["state"] == "attested"
    assert row["attested_scope"] == "trading"


def test_v14_migration_bumps_the_stored_version() -> None:
    conn = _v12_database()
    db.migrate(conn)
    stamped = conn.execute("SELECT version FROM schema_version").fetchone()["version"]
    assert stamped == db.SCHEMA_VERSION == 14


def test_v14_migration_step_is_not_blocked_by_another_venues_existing_row() -> None:
    """The guard is scoped to `venue = 'coinbase'`, not the whole table: a pre-existing row for
    some OTHER venue must not suppress coinbase's own backfill. Calls the migration function
    directly, for the same reason as the guard test above -- there is no present-day path that
    writes a non-coinbase row before v14 runs through `db.migrate()`."""
    conn = _v12_database()
    _create_venue_trade_scopes_table(conn)
    _insert_order(conn, mode="live", status="filled", created_at=1_700_000_000)
    conn.execute("INSERT INTO venue_trade_scopes (venue, state) VALUES ('kraken', 'unverified')")
    conn.commit()

    db._migrate_v14_venue_trade_scopes(conn)
    conn.commit()

    rows = {r["venue"]: r["state"] for r in _trade_scope_rows(conn)}
    assert rows == {"coinbase": "confirmed", "kraken": "unverified"}


def test_v14_backfill_runs_on_a_database_already_stamped_at_v13() -> None:
    """The REAL upgrade path after the renumber, and the one the live deployment now takes.

    #502's `_migrate_v13_positions_realized_legs` reached `main` first, so #233's backfill moved
    from v13 to v14. A deployment that already upgraded to #502 is stamped at 13, and the
    version loop must therefore still run 14 on it. Had the renumber been done the other way --
    keeping #233 at v13 and moving the LANDED migration -- this database would skip the backfill
    entirely (`if current < target` is false for 13), rail 20 would find no record for coinbase,
    and the next live ENTRY on a venue that has been trading all along would be vetoed.
    """
    conn = _v12_database()
    _insert_order(conn, mode="live", status="filled", created_at=1_700_000_000)
    conn.execute("UPDATE schema_version SET version = 13")
    conn.commit()

    db.migrate(conn)

    scope = Repository(conn).get_venue_trade_scope("coinbase")
    assert scope is not None, (
        "a database already stamped at v13 by #502 got no #233 backfill -- the renumber left "
        "the live deployment with no trade-scope record and rail 20 will veto its next entry"
    )
    assert scope.may_place_live_entry() is True
