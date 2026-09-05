"""SQLite connection + schema management for keel.

Standard-library `sqlite3` only (no ORM) per the design spec §6. `connect()` returns a
`sqlite3.Connection` configured with a `Row` factory (dict-like row access) and foreign keys
enabled. `migrate()` idempotently creates the §6 tables (`transactions`, `candles`,
`orders`, `rules`, `signals`, `backtests`, `pnl_daily`, `agent_state`, `broker_subscriptions`,
`trade_outcomes`, `positions`, `journal`, `venue_trade_scopes`) plus their indexes and a
`schema_version` marker table.

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

SCHEMA_VERSION = 20

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
        -- What the venue has actually executed on this order (#446). `qty` stays the ORDERED
        -- size; this is the observed fill, so a partial is two numbers, not one rewritten.
        -- NULL on every row written before v11: "not observed", not zero.
        filled_quantity TEXT,
        -- THE VENUE'S OWN BOOK AT THE MOMENT THIS ORDER WAS SUBMITTED (#626), as the venue
        -- gave it: the raw pair, never a derived spread. At this deployment's clip sizes
        -- (`max_per_order_usd: 100`; the three real live fills were $50.00, $50.00, $61.71)
        -- square-root-law impact is under 5bp for every product in the corpus while the
        -- backtest charges 50bp, so essentially the whole modelled cost is SPREAD -- and
        -- before these two columns keel stored nothing that measured it. #523's merged
        -- measurement reports every participation arm as a LOWER BOUND on cost for exactly
        -- this reason.
        --
        -- The pair, not `(ask - bid) / mid`, for the same reason `expected_fill` and
        -- `actual_fill` are two columns rather than one delta: a derivation cannot be
        -- re-derived differently later, and half-spread-from-mid, half-spread-from-the-side
        -- crossed, and relative spread are three different questions off one pair.
        --
        -- NULL means NOT OBSERVED, never zero: a preview that carried no readable book, and
        -- every `mode='paper'` row (paper fills synthetically, with no venue preview at all --
        -- a fabricated book sharing a column name with a real one would poison the very
        -- measurement these columns exist for).
        --
        -- AT SUBMIT, and for a resting order that is NOT the book it eventually fills in. A
        -- market IOC crosses this book; a `BracketGTC` records the book it was submitted into
        -- and then waits, sometimes for days. `order_type` CANNOT separate the two -- the
        -- executor writes `'market'` on every row -- so a reader measuring realised spread
        -- cost excludes the bracket legs by their id in `positions.bracket_order_id`, the one
        -- linkage direction that exists.
        submit_best_bid TEXT,
        submit_best_ask TEXT,
        -- How the price on THIS order was arrived at (v20, #715):
        -- one of `keel_core.quote_provenance`'s four tokens, written by
        -- `executor._order_row` from the same `Preview` the confirmation gate reads (#715).
        -- A property of the PREVIEW, so it is recorded in autonomous mode too.
        --
        -- NULL on every row written before v20, and on every `mode='paper'` row: paper fills
        -- synthetically with no venue preview at all, and a fabricated provenance sharing a
        -- column with a real one would poison the measurement this column exists for -- the
        -- same posture `submit_best_bid`/`submit_best_ask` take above. Never a provenance
        -- inferred after the fact from `limit_price` or the submit book.
        quote_provenance TEXT,
        -- The id the adapter actually SENT the venue for this order (v20, #715), as distinct
        -- from `id` (this table's own PK) and from a broker-returned `raw_response`/
        -- `confirmation` id that only exists after acceptance. `resolve_client_order_id`
        -- (`keel_broker_api.port`) mints one per placement attempt, and since #715 `PlaceResult`
        -- reports the id the adapter ACTUALLY sent so the executor can write it here.
        --
        -- Recording only: nothing passes `idempotency_key` into `place_order`, because a
        -- per-attempt id is a deliberate default and changing it would change venue-side
        -- deduplication -- a behaviour change wearing a recording change's clothes. NULL on
        -- every row written before v20, and on every row whose adapter reported no id.
        client_order_id TEXT,
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
        initial_stop      TEXT,
        -- Partial-exit accumulators (#502). `qty` is the quantity STILL HELD, and it is now
        -- mutable: `scale_out` sells a fraction of a tranche and leaves the rest running, so
        -- the legs of one trade land at different prices and different times. These three
        -- carry the legs already sold -- quantity, gross proceeds, and exit-leg fees -- so the
        -- ONE `trade_outcomes` row this tranche finally produces sums them all.
        --
        -- One row per TRADE, not per leg, is the approved definition (§2 of the trade-outcomes
        -- design: "a half-off-at-target that later stops out at breakeven is ONE trade, not
        -- two. Its P&L is the sum across all partial exits"). Booking each leg separately
        -- would hand rail 16 a fee-sized loss for every runner that ends at break-even, and a
        -- consecutive-loss breaker fed one loss per profitable scale-out is a breaker that
        -- trips on a working strategy.
        realized_qty      TEXT,
        realized_proceeds TEXT,
        realized_fees     TEXT,
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
    CREATE TABLE IF NOT EXISTS candle_series_feed (
        product_id TEXT NOT NULL,
        granularity TEXT NOT NULL,
        feed TEXT NOT NULL,
        first_seen_ts INTEGER NOT NULL,
        last_seen_ts INTEGER NOT NULL,
        PRIMARY KEY (product_id, granularity, feed)
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
    CREATE TABLE IF NOT EXISTS venue_trade_scopes (
        venue                   TEXT PRIMARY KEY,
        state                   TEXT NOT NULL,
        attested_scope          TEXT,
        attested_ts             INTEGER,
        confirmed_ts            INTEGER,
        refuted_ts              INTEGER,
        refuted_reason          TEXT,
        -- Non-reversible fingerprint of the credential IDENTIFIER this evidence was collected
        -- under (#633), NULL for every row written before v15 -- including a v14-backfilled
        -- `confirmed` coinbase row, which by construction has no fingerprint to give it, and
        -- every row an operator attested before this column existed. NULL means "recorded
        -- before fingerprinting existed" and the read path (`VenueTradeScope.
        -- credential_evidence`/`may_place_live_entry`) treats it as MATCHING, never as a
        -- mismatch -- see `_migrate_v15_trade_scope_credential_fingerprint`'s docstring for why
        -- getting that backwards would make this migration itself the outage.
        credential_fingerprint  TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS venue_cash_postures (
        venue                   TEXT PRIMARY KEY,
        state                   TEXT NOT NULL,
        -- `spot_cash` | `margin_enabled` | NULL. NULL only alongside state `unverified`.
        attested_posture        TEXT,
        attested_ts             INTEGER,
        -- When the attestation expires. Deliberately stored rather than derived, so changing
        -- `ATTESTATION_TTL_SEC` cannot retroactively expire (or extend) a claim a human made
        -- under the window that was in force when they made it.
        attest_due_ts           INTEGER,
        -- Set when venue evidence CONTRADICTS the claim -- an INTX portfolio is the only such
        -- evidence Stage 1 can produce. The attestation columns are preserved alongside it: a
        -- report has to be able to say what was claimed and when, not just that it was refuted.
        refuted_ts              INTEGER,
        refuted_reason          TEXT,
        -- Non-reversible fingerprint of the credential IDENTIFIER the claim was made under
        -- (#633). NULL means "recorded without fingerprinting" and reads as MATCHING, never as a
        -- mismatch -- the same fail-safe direction as `venue_trade_scopes`.
        credential_fingerprint  TEXT
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
        attested_at  INTEGER NOT NULL,
        -- When this attestation's window closes (v20, #718), the same nullable shape as
        -- `venue_cash_postures.attest_due_ts` -- unlike `broker_subscriptions.attest_due_ts`,
        -- which is required because every insert there sets one. Schema only: nothing yet
        -- writes or reads this column, so it is NULL for every row, existing or new, until a
        -- later change wires an actual expiry policy through it.
        attest_due_ts INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS instrument_attestations (
        venue        TEXT NOT NULL,
        product_id   TEXT NOT NULL,
        wrapper      TEXT NOT NULL,
        source       TEXT NOT NULL,
        attested_by  TEXT NOT NULL,
        attested_at  INTEGER NOT NULL,
        -- Same column, same meaning, same "schema only, always NULL for now" caveat as
        -- `asset_attestations.attest_due_ts` above (v20, #718).
        attest_due_ts INTEGER,
        PRIMARY KEY (venue, product_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS screen_exceptions (
        asset       TEXT NOT NULL,
        criterion   TEXT NOT NULL,
        rationale   TEXT NOT NULL,
        granted_by  TEXT NOT NULL,
        granted_at  INTEGER NOT NULL,
        PRIMARY KEY (asset, criterion)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS profile (
        id          INTEGER PRIMARY KEY CHECK (id = 1),
        autonomous  INTEGER NOT NULL DEFAULT 0,
        -- NULL = no expiry (a durable choice). A timestamp makes autonomy LAPSE on its own,
        -- restoring the time bound the removed bypass-arm token used to provide.
        autonomous_until INTEGER,
        updated_ts  INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS equity_points (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        -- Epoch seconds, INTEGER, like every other timestamp in this schema (`positions.
        -- opened_at`, `asset_attestations.attested_at`) and like the `now_ts` the agent passes
        -- down its whole cycle. TEXT would order an epoch LEXICALLY, so a `ts >= ?` window --
        -- which is how the chart reads this table -- would silently return the wrong rows.
        ts          INTEGER NOT NULL,
        -- 'paper' | 'live'. NO `profile` column: the database is already one-per-profile
        -- (ADR 0002), while paper and live flip WITHIN one database -- so mode is the
        -- partition that actually needs storing, and the one a reader must never blend.
        mode        TEXT NOT NULL,
        equity      TEXT NOT NULL,
        -- NULL is "not recorded", never zero: a cycle can know its total while the split is
        -- unavailable (`orders.filled_quantity`'s convention, and for the same reason).
        cash        TEXT,
        unrealized  TEXT,
        -- The high-water mark AFTER this reading, so the row carries the rail-11 ceiling that
        -- was in force rather than one a reader recomputes -- `record_external_flow` rebases
        -- the HWM on a declared deposit, and a recomputed maximum would miss that.
        hwm         TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_equity_points_mode_ts ON equity_points(mode, ts)",
    # One row per currency per RECORDED READING -- the settled/available/total pair a cycle
    # observed (v20, #719). Per CURRENCY and not one scalar pair, because folding several
    # currencies into one figure would add them 1:1, which is the no-FX bound
    # `agent._mark_to_market_parts` already states it will not cross.
    #
    # Deliberately NOT unique on (mode, currency, ts), and the wording above is careful because
    # of it: there is no cycle identifier here, only a stamp, so the schema cannot express "one
    # row per cycle" and a comment claiming it would be describing a constraint that does not
    # exist. `equity_points` beside it takes the same shape for the same reason -- an append-only
    # series of readings, deduplicated by nobody. A reader wanting the current balance takes the
    # newest row per (mode, currency), exactly as `gather_balances` already does for equity.
    #
    # Schema only in this release: nothing writes these rows yet, and
    # `commands/balances.py` still reports the split as unrecorded.
    """
    CREATE TABLE IF NOT EXISTS cycle_balances (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        ts          INTEGER NOT NULL,
        mode        TEXT NOT NULL,
        currency    TEXT NOT NULL,
        -- Decimal as TEXT, the same convention as every money column in this schema (`orders.
        -- qty`, `equity_points.equity`). NULL means NOT OBSERVED, never zero: a cycle that could
        -- read the total but not the available-to-trade split (or vice versa) must be able to
        -- leave the unread side NULL rather than have a writer invent a number for it.
        available   TEXT,
        total       TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_cycle_balances_mode_currency_ts "
    "ON cycle_balances(mode, currency, ts)",
    """
    CREATE TABLE IF NOT EXISTS audit_events (
        seq_id        INTEGER PRIMARY KEY AUTOINCREMENT,
        -- Epoch seconds, INTEGER -- deliberately NOT `ts TEXT` as issue #721's comment specifies.
        -- Every other timestamp in this schema is INTEGER (`positions.opened_at`,
        -- `asset_attestations.attested_at`, and `equity_points.ts` above, which spells out why).
        --
        -- TEXT orders an epoch LEXICALLY. Run it: a TEXT column holding '9999999999' and
        -- '10000000000' returns them from `ORDER BY ts` as ['10000000000', '9999999999'] --
        -- the REVERSE of the numeric truth, because '1' sorts before '9'. So a `ts >= ?` range
        -- read, which is exactly how an audit trail gets queried, would silently return the
        -- wrong rows the moment the epoch gains a digit. That is the bug `equity_points.ts`
        -- exists to document, and this table would have walked into it by following the issue
        -- text literally.
        --
        -- Pinned by `test_audit_events_stores_its_timestamp_as_an_integer`: without a test the
        -- deviation was free -- flipping this column back to TEXT passed the entire suite, and
        -- a deviation from a written decision that nothing pins is one the next reader undoes
        -- in good faith.
        ts            INTEGER NOT NULL,
        event_type    TEXT NOT NULL,
        entity_id     TEXT NOT NULL,
        payload_json  TEXT NOT NULL,
        -- Hash-chain fields: each row commits to the previous row's `row_hash`, so the chain
        -- (not any single row) is what a reader verifies. Schema only here -- no writer computes
        -- either hash yet; that is a later change's job.
        prev_hash     TEXT NOT NULL,
        row_hash      TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_audit_events_ts ON audit_events(ts)",
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

    row = conn.execute("SELECT value FROM agent_state WHERE key = 'subscription'").fetchone()
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


def _migrate_v8_autonomy_expiry(conn: sqlite3.Connection) -> None:
    """v8 adds `profile.autonomous_until` (NULL = the choice never lapses).

    This exists because the column was first added to v7's `CREATE TABLE IF NOT EXISTS`, which
    silently does nothing for a database already stamped at 7 -- it kept the old three-column
    table forever, so a recorded choice read back as "off" and `keel autonomy off` died on a
    missing column. Only ever reachable on a developer database built from the unmerged branch,
    but the de-risking command must never be the one that crashes.

    Idempotent: a database that got the column from the DDL (fresh, or upgraded from <=v6) is
    left alone rather than hitting "duplicate column name".
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(profile)")}
    if "autonomous_until" not in columns:
        conn.execute("ALTER TABLE profile ADD COLUMN autonomous_until INTEGER")


def _migrate_v9_screen_exceptions(conn: sqlite3.Connection) -> None:
    """v9 adds `screen_exceptions`. Table creation is handled by `_SCHEMA_STATEMENTS`; there is
    deliberately NO backfill.

    A row asserts a human documented a waiver for one asset/criterion pair, with a rationale and
    who granted it. Seeding one would fabricate an exception nobody granted; an empty table
    correctly says nothing has been excepted yet.

    Unlike v8's `profile.autonomous_until` ADD COLUMN, this is a genuine no-op: `migrate()` runs
    every `_SCHEMA_STATEMENTS` statement (all `IF NOT EXISTS`) before the version loop below, so
    a database already stamped at v8 picks up the new table from that pass alone. This step only
    exists to advance the stamp.
    """


def _migrate_v10_instrument_attestations(conn: sqlite3.Connection) -> None:
    """v10 adds `instrument_attestations`. Table creation is handled by `_SCHEMA_STATEMENTS`;
    there is deliberately NO backfill.

    A row asserts a human established what CONTRACT a given venue listing actually is (spot,
    CFD, perpetual, ...) against a named source. Seeding `spot` rows for the currently-allowlisted
    products would fabricate exactly the claim this gap exists to demand -- and would do it for
    the products the project is most likely to stop questioning.

    Like v9, this is a genuine no-op migration: `migrate()` runs every `_SCHEMA_STATEMENTS`
    statement (all `IF NOT EXISTS`) before the version loop below, so a database already stamped
    at v9 picks the table up from that pass alone. This step only exists to advance the stamp.

    An empty table correctly says nothing has been attested yet -- which, because the screen
    fails closed on a missing instrument attestation, means every product reports REJECT until
    the operator runs `keel assets attest-instrument`. That is the intended fail-closed default,
    not a regression.
    """


def _migrate_v11_orders_filled_quantity(conn: sqlite3.Connection) -> None:
    """v11 adds `orders.filled_quantity` -- the venue-observed fill quantity (#446).

    A partial fill is two numbers, not one: the ORDERED size (`qty`) and what the venue has
    actually executed. Rewriting `qty` on a partial would destroy the second half of the
    comparison (`filled < ordered`) that makes the state recognizable at all.

    Idempotent by the v8 pattern (`PRAGMA table_info` guard) rather than the v9/v10 no-op
    pattern: a database already stamped at v10 got its `orders` table from v10's DDL, which has
    no such column, and `CREATE TABLE IF NOT EXISTS` never adds one -- the exact way v8's
    `profile.autonomous_until` went missing. There is deliberately NO backfill: the honest
    value for every pre-v11 row is NULL, "not observed", which readers treat as "use `qty`" --
    the behaviour those rows had before the column existed.
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(orders)")}
    if "filled_quantity" not in columns:
        conn.execute("ALTER TABLE orders ADD COLUMN filled_quantity TEXT")


def _migrate_v12_positions_initial_stop(conn: sqlite3.Connection) -> None:
    """v12 adds `positions.initial_stop` -- the stop the tranche was SIZED against (#520).

    The break-even arm of `exit_policy.next_stop` computes its threshold from the trade's
    ORIGINAL per-unit risk: `entry + be_roll_rr * (entry - initial_stop)`. Live state carries
    `entry_fill` (this ledger) and `open_stop:<product_id>` (the CURRENT, already-ratcheted stop)
    and nothing else -- so the number the threshold is most sensitive to was simply absent.

    Substituting the current stop is not an approximation, it is a DIFFERENT POLICY: the current
    stop rises on every ratchet, shrinking `(entry - stop)` so the threshold creeps toward entry
    and the arm fires earlier each time, drifting further from the measured policy the longer a
    trade runs. Live and sim would then encode two different break-even rules while appearing to
    share `exit_policy`'s functions -- the exact failure sharing them was meant to prevent.

    Idempotent by the v8/v11 `PRAGMA table_info` guard: a database already stamped at v11 got
    `positions` from v4's DDL, which has no such column, and `CREATE TABLE IF NOT EXISTS` never
    adds one.

    **NO BACKFILL, deliberately.** The honest value for every pre-v12 tranche is NULL -- nobody
    recorded it — and readers must treat NULL as "unknown" and disable the break-even arm for
    that tranche rather than guess. Inventing a value here would fabricate the one input the
    policy is most sensitive to. The trailing arm is unaffected: it needs no original risk, so an
    old tranche keeps trailing and simply never break-even-rolls.
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(positions)")}
    if "initial_stop" not in columns:
        conn.execute("ALTER TABLE positions ADD COLUMN initial_stop TEXT")



def _migrate_v13_positions_realized_legs(conn: sqlite3.Connection) -> None:
    """v13 adds the partial-exit accumulators to `positions` (#502).

    Until now a tranche closed in ONE piece: `_close_tranches` booked `exit_qty=position.qty`
    and called `close_position`, and `positions.qty` had no UPDATE anywhere in the codebase.
    `scale_out` breaks that assumption by design -- it sells a fraction and leaves the rest
    running -- and #446's partially-filled market exit breaks it by accident, booking the whole
    tranche against a sale that only partly happened.

    Both need the same two things: a mutable `qty` (what is still held) and somewhere to keep
    the legs already sold until the tranche finally closes. `realized_qty`, `realized_proceeds`
    (gross, price x quantity) and `realized_fees` (exit-leg only -- the entry fee belongs to the
    whole tranche and is charged once, on the closing row) are that somewhere.

    Idempotent by the v8/v11/v12 `PRAGMA table_info` guard: a database stamped at v12 got
    `positions` from v4's DDL, and `CREATE TABLE IF NOT EXISTS` never adds a column.

    **NO BACKFILL, and none is needed.** Every pre-v13 tranche closed whole or is still whole,
    so the honest value is zero realized -- which is exactly what NULL decodes to here. That is
    deliberately UNLIKE `initial_stop`, where NULL means "nobody recorded it" and readers must
    disable a policy arm rather than substitute a number: there is no difference between "never
    partially exited" and "partially exited nothing", so decoding NULL to zero invents nothing.
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(positions)")}
    for column in ("realized_qty", "realized_proceeds", "realized_fees"):
        if column not in columns:
            conn.execute(f"ALTER TABLE positions ADD COLUMN {column} TEXT")

def _migrate_v14_venue_trade_scopes(conn: sqlite3.Connection) -> None:
    """v14 adds `venue_trade_scopes` (#233). Table creation is handled by `_SCHEMA_STATEMENTS`;
    this step backfills `'coinbase'` from order history so an upgrade does not retroactively
    de-authorise a venue that has been trading live all along.

    **The evidence.** `orders` carries no venue column, but `executor.py` writes
    `status = 'rejected'` only when the broker REFUSED the placement -- every other status means
    the venue ACCEPTED it -- and `reconcile.py` never writes `rejected` onto a row that was
    accepted (it only ever arrives there from `_initial_status`, at insert time). A vetoed intent
    never becomes a row at all: the insert happens after the guard gate. So:

        SELECT 1 FROM orders WHERE mode = 'live' AND status <> 'rejected' LIMIT 1

    is exactly "has this deployment ever had a live placement the venue accepted" -- proof the
    credential can trade, supplied by the venue itself, which is why the backfilled record is
    `CONFIRMED` rather than `ATTESTED`.

    **Why `'coinbase'`, when the row carries no venue.** Follows the v2 `broker_subscriptions`
    precedent: attribute the evidence to `'coinbase'`, today's only broker. This cannot
    over-permit any OTHER venue, because the record is read by venue key. A deployment configured
    for a different venue looks up ITS OWN row, finds none, and fails closed exactly as a fresh
    install does -- a `coinbase` row is only ever read by a deployment whose `broker.name` is
    `coinbase`, which is the deployment whose history wrote those `orders` rows in the first
    place.

    **Why this matters.** Without the backfill, the user's live Coinbase deployment -- trading
    unattended, daily -- would upgrade into a database with no `venue_trade_scopes` row, rail 20's
    predicate would fail closed on the missing record, and the very next live ENTRY would be
    vetoed: a self-inflicted incident on a venue that has been working the whole time. The design
    intends "the running deployment sees zero behaviour change"; this backfill is what delivers
    that for an already-live venue, exactly as v2's backfill did for the subscription cap.

    **Numbered v14, not v13.** This migration was written as v13 and renumbered when
    #502's `_migrate_v13_positions_realized_legs` reached `main` first. A migration that
    has LANDED is never renumbered -- databases in the field are already stamped against
    it, and moving its number would either re-run it or skip it. The unmerged one moves,
    every time.

    Idempotent the v2 way, venue-scoped rather than table-wide: skip once a `'coinbase'` row
    exists, so an operator who has since attested (e.g. after rotating the credential) is never
    reset. A table-wide guard would be wrong for the same reason v2's comment gives -- it would
    skip a SECOND venue's backfill just because `coinbase` already has a row.
    """
    already_migrated = conn.execute(
        "SELECT 1 FROM venue_trade_scopes WHERE venue = 'coinbase' LIMIT 1"
    ).fetchone()
    if already_migrated is not None:
        return

    row = conn.execute(
        "SELECT created_at FROM orders WHERE mode = 'live' AND status <> 'rejected' "
        "ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return

    confirmed_ts = int(row["created_at"]) if row["created_at"] is not None else int(time.time())
    conn.execute(
        """
        INSERT INTO venue_trade_scopes (
            venue, state, attested_scope, attested_ts, confirmed_ts, refuted_ts, refuted_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("coinbase", "confirmed", None, None, confirmed_ts, None, None),
    )


def _migrate_v15_trade_scope_credential_fingerprint(conn: sqlite3.Connection) -> None:
    """v15 adds `venue_trade_scopes.credential_fingerprint` (#633).

    `venue_trade_scopes` (#233) recorded evidence about a CREDENTIAL but was keyed only by
    VENUE, so nothing noticed when the credential underneath a venue changed -- `keel
    credentials forget CDP_API_KEY` followed by `keel credentials set CDP_API_KEY` (a different
    key) left the venue's `confirmed` record standing, and rail 20 would permit a live entry on
    the new key on the strength of the old key's evidence. This column is a non-reversible
    fingerprint of the credential IDENTIFIER (never the signing secret --
    `keel_core.credential_identity` carries the full argument) the evidence was collected under,
    so the read path can compare it against the CURRENT credential instead of trusting the venue
    key alone.

    **NULL is the correct value for every existing row, including the live deployment's
    backfilled `confirmed` one, and the read path MUST treat NULL as matching.** This is not an
    incidental migration detail -- get it backwards and this migration IS the outage it exists
    to prevent. The live deployment's database was verified at schema version 12 before this PR
    (`venue_trade_scopes` did not exist yet), which means the v14 backfill and this v15 column
    both run in the SAME `migrate()` pass on the next upgrade: `_SCHEMA_STATEMENTS` creates
    `venue_trade_scopes` WITH `credential_fingerprint` already present, v14's backfill inserts a
    `confirmed` coinbase row with no fingerprint (nothing observed which credential placed a
    2026-07 order, and none can be reconstructed retroactively), and this step then finds nothing
    to do to that row -- it already has the column, NULL, exactly the value a pre-#633 row is
    supposed to carry. `VenueTradeScope.credential_evidence` reads that NULL as
    `UNFINGERPRINTED`, and `may_place_live_entry` treats `UNFINGERPRINTED` as matching. Getting
    that read-side decision backwards (withholding permission on a NULL fingerprint) would veto
    the very next live entry on a healthy Coinbase deployment that has been trading unattended
    the whole time -- the identical shape of incident the v14 backfill itself exists to prevent,
    just one migration later.

    **Why the `PRAGMA table_info` guard, unlike a bare `ALTER TABLE`.** On a fresh install, or on
    the 12 -> 15 chain described above, `_SCHEMA_STATEMENTS` runs BEFORE any numbered migration
    step (`migrate()`'s own ordering) and creates `venue_trade_scopes` with this column already
    present. A bare `ALTER TABLE venue_trade_scopes ADD COLUMN credential_fingerprint TEXT` on
    that database raises sqlite's own `duplicate column name: credential_fingerprint` and the
    whole migration chain fails -- which would mean EVERY upgrade landing on this release breaks,
    not just the ones that happen to skip through v14 on the way. Idempotent by the same
    `PRAGMA table_info` pattern `_migrate_v11_orders_filled_quantity`/
    `_migrate_v12_positions_initial_stop`/`_migrate_v13_positions_realized_legs` already use: add
    the column only when it is not already there, so this step is a genuine no-op on a database
    that got it from `_SCHEMA_STATEMENTS` and a real `ALTER TABLE` on one that got the v14 shape
    of the table from an earlier release.
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(venue_trade_scopes)")}
    if "credential_fingerprint" not in columns:
        conn.execute("ALTER TABLE venue_trade_scopes ADD COLUMN credential_fingerprint TEXT")


def _migrate_v16_orders_submit_book(conn: sqlite3.Connection) -> None:
    """v16 adds `orders.submit_best_bid` / `orders.submit_best_ask` -- the venue's own book at
    the moment the order was submitted (#626, option 1).

    keel already FETCHES this on every live order and has since #350: `_run_order` previews
    before it places, and the routing-time max-entry-spread gate reads `best_bid`/`best_ask`
    out of that preview to decide whether to enter at all. It then threw the numbers away.
    These two columns are pure persistence of a value already in hand -- no new port method,
    no extra venue call, no added latency on the order path.

    Why it matters is `config.live-sandbox.yaml`'s `max_per_order_usd: 100`. At the $50-ish
    clips this deployment actually fills, square-root-law market impact is under 5bp for every
    product in the corpus while `slippage_for_quote_volume` charges 50bp, so the modelled cost
    is essentially ALL spread -- and nothing recorded the spread. #523 cannot resolve its
    participation-rate option without these rows, which is why its measurement
    (`docs/experiments/2026-08-30-slippage-cap-options.md`) reports every arm it has as a
    lower bound on cost.

    Idempotent by the `PRAGMA table_info` pattern of `_migrate_v11_orders_filled_quantity` /
    `_migrate_v15_trade_scope_credential_fingerprint`, and for the same two reasons. A database
    already stamped at v15 got its `orders` table from an older `CREATE TABLE IF NOT EXISTS`,
    which never adds a column, so it needs a real `ALTER TABLE` -- the v8 lesson, where
    `profile.autonomous_until` silently never appeared. A database arriving from below v15 gets
    `orders` fresh from `_SCHEMA_STATEMENTS`, which runs BEFORE any numbered step and already
    carries both columns, so a bare `ALTER TABLE` there would raise sqlite's own "duplicate
    column name" and break every upgrade landing on this release.

    Each column is guarded INDEPENDENTLY rather than the pair being guarded on one of them: a
    database interrupted between the two ALTERs (or hand-patched with one of them) must be able
    to complete, and a guard that inferred the second from the first would leave it stuck.

    NO BACKFILL, deliberately. The honest value for every row written before this column is
    NULL -- "not observed". The four `orders` rows in the live deployment's database were placed
    against books nobody wrote down, and no book can be reconstructed for a 2026-07/08 order
    after the fact. Inventing one -- from a daily candle, from a current quote -- would put
    fabricated spread observations into the exact table #523 will read to decide a cost model.
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(orders)")}
    if "submit_best_bid" not in columns:
        conn.execute("ALTER TABLE orders ADD COLUMN submit_best_bid TEXT")
    if "submit_best_ask" not in columns:
        conn.execute("ALTER TABLE orders ADD COLUMN submit_best_ask TEXT")


def _migrate_v17_candle_series_feed(conn: sqlite3.Connection) -> None:
    """v17 adds `candle_series_feed`. Table creation is handled by `_SCHEMA_STATEMENTS`; there is
    deliberately NO backfill, and the reason is the whole point of the table (#696).

    A row here asserts "these bars were written while THIS feed was in use". For every candle
    already cached, nobody wrote that down -- the feed was inferred at read time from whatever
    config happened to be loaded, which is precisely the bug. Seeding rows from the CURRENT
    `broker.data_feed` would manufacture exactly the claim this table exists to make checkable,
    and would do it for years of bars that may well have come from somewhere else.

    An empty table means "provenance unrecorded", which is true of every pre-existing series and
    is a different statement from "consolidated". Callers must keep those apart: the first is
    the absence of evidence, the second is evidence.
    """


def _migrate_v18_venue_cash_postures(conn: sqlite3.Connection) -> None:
    """v18 adds `venue_cash_postures`. Table creation is handled by `_SCHEMA_STATEMENTS`; there is
    deliberately NO backfill, and here that is close to the entire point of the feature.

    A row asserts that a HUMAN examined their own venue account and stated its cash-versus-margin
    posture. Nothing else can produce that claim -- Coinbase exposes no such field for spot
    (#666), which is why the attestation exists at all. Seeding a row for the live deployment
    because its portfolios happen to look cash-only today would fabricate the one thing the record
    is for, and would do it in the table a rail reads before placing a live entry.

    An empty table means every venue is UNVERIFIED, which is true, and unverified vetoes new
    entries and nothing else.
    """


def _migrate_v19_equity_points(conn: sqlite3.Connection) -> None:
    """v19 adds `equity_points`. Table creation is handled by `_SCHEMA_STATEMENTS`; there is
    deliberately NO backfill, and what could be backfilled is exactly what must not be.

    Two sources look like history. `agent_state["equity_history"]` holds at most 7 days, and it
    is a RAIL's working set, not a record: `record_external_flow` shifts every point in it by a
    declared deposit so the weekly drawdown keeps measuring trading performance. Replaying those
    shifted numbers as observations would publish equities the account never had. The `orders`
    ledger could reconstruct a curve, but only for closed trades, only at trade resolution, and
    with no cash leg -- a different quantity wearing this table's name.

    An empty table means "not observed before v19", which is true: nothing wrote it down. The
    chart starts at the first cycle after the upgrade and says so, rather than opening on a
    fabricated past.
    """


def _migrate_v20_provenance_and_attest_windows(conn: sqlite3.Connection) -> None:
    """v20 adds four columns and two tables (#721): `orders.quote_provenance`,
    `orders.client_order_id`, `asset_attestations.attest_due_ts`,
    `instrument_attestations.attest_due_ts`, plus `cycle_balances` and `audit_events` --
    #715 (the two order columns), #718 (the two windows), #719 (`cycle_balances`) and #721
    (`audit_events`), batched into one bump so a database is atomically v19 or v20.

    SCHEMA ONLY AS SHIPPED: this migration makes the columns and tables available and changes
    nothing any existing writer or rail does. The writers arrive in follow-up work, issue by
    issue -- #715's two order columns are written by `executor._order_row` and the placement
    `update_order` as of that PR; `attest_due_ts`, `cycle_balances` and `audit_events` still
    have none, and `commands/balances.py` and `commands/timeline.py` go on reporting their
    fields as unrecorded until they do.

    See the DDL comments beside each column in `_SCHEMA_STATEMENTS` for what each one is for,
    and why `audit_events.ts` is INTEGER rather than the `TEXT` #721's own comment specifies.

    `cycle_balances` and `audit_events` are brand new, so their creation is a genuine no-op here
    -- the `_migrate_v9_screen_exceptions`/`_migrate_v19_equity_points` pattern: `migrate()` runs
    every `_SCHEMA_STATEMENTS` statement (all `IF NOT EXISTS`) before the version loop below, so
    a database already stamped below v20 picks both tables up from that pass alone.

    The four columns are a different story, because `orders`, `asset_attestations` and
    `instrument_attestations` all PREDATE this migration: a database arriving from anywhere
    between v1 and v19 already has these three tables from an EARLIER `CREATE TABLE IF NOT
    EXISTS`, which never adds a column to a table that already exists -- the v8 lesson
    (`profile.autonomous_until`), repeated at v11, v12, v13, v15 and v16 for exactly this reason.
    So each column needs a real `ALTER TABLE`, guarded the v16 way: a `PRAGMA table_info` check
    INDEPENDENTLY per column, because a database arriving fresh (or from v12 straight through)
    gets all three tables from THIS release's `_SCHEMA_STATEMENTS`, which already carries all
    four columns -- a bare `ALTER TABLE` there would raise sqlite's own "duplicate column name"
    and break every upgrade landing on this release. Guarding each column on its own, rather than
    inferring one from another, also means a database hand-patched with only some of the four
    can still pick up the rest instead of getting stuck.

    **NO BACKFILL, deliberately, for all four.** The honest value for every row written before
    v20 is NULL -- "not recorded" -- exactly the convention `filled_quantity` (v11) and
    `submit_best_bid`/`submit_best_ask` (v16) already established for this same `orders` table.
    Nothing in this database can tell you what priced a 2026-07 order, what id the adapter sent
    for it, or when an attestation window closes for an attestation nobody dated with one.
    Inventing values for any of them would be worse than the honest NULL this migration leaves
    behind.
    """
    order_columns = {row["name"] for row in conn.execute("PRAGMA table_info(orders)")}
    if "quote_provenance" not in order_columns:
        conn.execute("ALTER TABLE orders ADD COLUMN quote_provenance TEXT")
    if "client_order_id" not in order_columns:
        conn.execute("ALTER TABLE orders ADD COLUMN client_order_id TEXT")

    asset_columns = {row["name"] for row in conn.execute("PRAGMA table_info(asset_attestations)")}
    if "attest_due_ts" not in asset_columns:
        conn.execute("ALTER TABLE asset_attestations ADD COLUMN attest_due_ts INTEGER")

    instrument_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(instrument_attestations)")
    }
    if "attest_due_ts" not in instrument_columns:
        conn.execute("ALTER TABLE instrument_attestations ADD COLUMN attest_due_ts INTEGER")


_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    2: _migrate_v2_broker_subscriptions,
    3: _migrate_v3_trade_outcomes,
    4: _migrate_v4_positions,
    5: _migrate_v5_candle_gap_probes,
    6: _migrate_v6_asset_attestations,
    7: _migrate_v7_profile,
    8: _migrate_v8_autonomy_expiry,
    9: _migrate_v9_screen_exceptions,
    10: _migrate_v10_instrument_attestations,
    11: _migrate_v11_orders_filled_quantity,
    12: _migrate_v12_positions_initial_stop,
    13: _migrate_v13_positions_realized_legs,
    14: _migrate_v14_venue_trade_scopes,
    15: _migrate_v15_trade_scope_credential_fingerprint,
    16: _migrate_v16_orders_submit_book,
    17: _migrate_v17_candle_series_feed,
    18: _migrate_v18_venue_cash_postures,
    19: _migrate_v19_equity_points,
    20: _migrate_v20_provenance_and_attest_windows,
}


#: How long a connection waits for a lock before giving up. SQLite's default is ZERO -- it raises
#: immediately -- which is the wrong default for a process that now reads and writes this file at
#: the same time. Five seconds is far longer than any contention here lasts and far shorter than
#: a person's patience.
BUSY_TIMEOUT_MS = 5_000


def connect(path: str | Path = "keel.db") -> sqlite3.Connection:
    """Open a `sqlite3.Connection` to `path` (or an in-memory DB for `":memory:"`).

    Configures dict-like `Row` access, foreign-key enforcement (which SQLite otherwise leaves off
    per-connection), a busy timeout, and WAL.

    **WAL, and why it is not a tuning preference.** In the default rollback journal a writer takes
    an EXCLUSIVE lock and readers take SHARED ones, so a reader and a writer cannot coexist. That
    was survivable while one process used this file at a time. It stopped being survivable when
    `keel serve` began polling the database every few seconds to render a page while a background
    fetch wrote to it (#437): the fetch DIED, and it died on the path most likely to be taken,
    because the page invites the operator to watch it. Measured, on the same fetch:

        page polling every 5s, rollback  -> FAILED at 45s, 31,709 candles ("disk I/O error")
        nobody polling,        rollback  -> ran 150s, 108,202 candles
        polling every 0.2s,    WAL       -> ran 150s, 108,501 candles, 694 clean reads

    In WAL, readers never block the writer and the writer never blocks readers. The agent writing
    a cycle while a dashboard refreshes is the same shape and was the same hazard.

    Journal mode is a property OF THE FILE, not of the connection: the first connection converts
    it and every later one inherits it, so this is a no-op on an already-converted database.

    Two consequences worth knowing. WAL adds `-wal` and `-shm` sidecar files beside the database
    -- a deployment folder now has three files where it had one. And `keel update`'s backups are
    unaffected: it uses SQLite's own online-backup API, precisely because "a plain file copy of a
    database with a live rollback journal is not a snapshot", and that API reads committed WAL
    content too.

    The standing decision this serves -- SQLite, one writer per file, and the four triggers that
    would reopen the question -- is `docs/decisions/0002-sqlite-persistence.md` (#526).
    """
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    # `:memory:` has no file to journal, and asking for WAL there is refused; a shared in-memory
    # database is also single-connection by nature, so there is nothing to protect.
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
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
