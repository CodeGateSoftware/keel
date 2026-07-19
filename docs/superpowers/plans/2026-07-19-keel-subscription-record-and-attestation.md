# keel Subscription Record & Attestation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the singleton JSON subscription blob with a per-venue `broker_subscriptions` record, make rail 14 derive its spend cap from that record under a fail-closed policy, and add `keel subscription attest` as the only way to establish one.

**Architecture:** A new pure `keel_core.subscription` module holds the record and §7's status→cap policy as testable functions with no database. `db.py` gains minimal versioned-migration machinery and backfills the old blob. `repository.py` exposes typed get/upsert/list. Rail 14 then reads the record instead of a config number, and the CLI gains `attest`. The engine stays single-venue behind one named constant.

**Tech Stack:** Python 3.12, stdlib `sqlite3` (no ORM), `click`, `pytest`, `ruff`, `mypy`, `uv`.

## Global Constraints

- Python `>=3.12`; ruff `line-length = 100`, `select = ["E", "F", "I", "UP"]`, `ignore = ["UP042"]`.
- Money is `Decimal` in the Python API and exact `TEXT` in SQLite, matching the `candles`/`orders` convention. Never `float`.
- `keel_core` is stdlib-only and must stay dependency-free.
- `mypy` strict applies to `keel_broker_*` only; `keel.*`, `tests.*`, and `keel_core.*` remain `ignore_errors = true`. Do not tighten them here.
- `uv run pytest tests/baseline/ -v` (3 tests) must pass with `tests/fixtures/baseline_backtest.json` **byte-unchanged** at the end of every task. Backtesting never runs the rails, so any change here is a bug.
- Run everything through `uv run`. Never commit `keel.db`, `*.log`, or `transactions/`.
- After every task: `uv run pytest -q && uv run ruff check . && uv run mypy` must pass.

## Ordering constraint (do not resequence)

Tasks 1–3 are strictly additive. Task 4 changes config. Task 5 switches rail 14 onto the new record. Task 6 removes the old path.

**The old `repo.get_subscription`/`set_subscription` pair survives until Task 6.** Deleting it earlier breaks `guards.py` and `cli.py` mid-plan and leaves the suite red between tasks. Task 6 is the last consumer and does the removal.

---

### Task 1: The record and its policy

A pure module: a frozen dataclass and two methods. No database, no config, no I/O. §7's policy table becomes a function whose every row is a unit test.

**Files:**
- Create: `packages/keel-core/keel_core/subscription.py`
- Create: `tests/test_subscription_record.py`

**Interfaces:**
- Consumes: stdlib only.
- Produces: `keel_core.subscription.{SubscriptionStatus, BrokerSubscription}`. `BrokerSubscription` fields: `venue: str`, `tier_name: str`, `free_volume_usd: Decimal | None`, `pacing: str`, `subscription_usd_month: Decimal`, `status: SubscriptionStatus`, `attested_at: int`, `attest_due_ts: int`. Methods: `effective_status(now_ts: int) -> SubscriptionStatus`, `allowance_usd(now_ts: int, unsubscribed_allowance_usd: Decimal) -> Decimal | None`. Tasks 2, 3, 5, and 6 use these exact names.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_subscription_record.py`:

```python
"""Tests for the subscription record's policy -- the fail-closed table from the design spec §7.

These are pure functions: no database, no config object, no rail. Every row of that table is a
test here, so rail 14's own tests do not have to re-derive the policy.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from keel_core.subscription import BrokerSubscription, SubscriptionStatus

NOW = 1_800_000_000
UNSUBSCRIBED = Decimal("0")


def _record(
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE,
    free_volume_usd: Decimal | None = Decimal("10000"),
    attest_due_ts: int = NOW + 86_400,
) -> BrokerSubscription:
    return BrokerSubscription(
        venue="coinbase",
        tier_name="Preferred",
        free_volume_usd=free_volume_usd,
        pacing="opportunistic",
        subscription_usd_month=Decimal("29.99"),
        status=status,
        attested_at=NOW - 86_400,
        attest_due_ts=attest_due_ts,
    )


def test_active_allows_its_free_volume() -> None:
    assert _record().allowance_usd(NOW, UNSUBSCRIBED) == Decimal("10000")


def test_active_and_unlimited_returns_none() -> None:
    """Premium has no cap at all -- None, not a very large number."""
    assert _record(free_volume_usd=None).allowance_usd(NOW, UNSUBSCRIBED) is None


@pytest.mark.parametrize(
    "status", [SubscriptionStatus.SUSPECT, SubscriptionStatus.LAPSED]
)
def test_suspect_and_lapsed_fall_back_to_the_unsubscribed_allowance(
    status: SubscriptionStatus,
) -> None:
    assert _record(status=status).allowance_usd(NOW, Decimal("25")) == Decimal("25")


def test_unlimited_still_falls_back_when_suspect() -> None:
    """An unlimited allowance the user may no longer have is worth exactly nothing."""
    record = _record(status=SubscriptionStatus.SUSPECT, free_volume_usd=None)
    assert record.allowance_usd(NOW, Decimal("25")) == Decimal("25")


def test_overdue_attestation_overrides_a_stored_active_status() -> None:
    record = _record(attest_due_ts=NOW - 1)
    assert record.effective_status(NOW) is SubscriptionStatus.SUSPECT
    assert record.allowance_usd(NOW, Decimal("25")) == Decimal("25")


def test_due_exactly_now_is_already_overdue() -> None:
    """Boundary: due-at is the moment it expires, not one tick still-good.

    Matches `is_bypass_armed`'s strict `now_ts < armed_until` convention elsewhere.
    """
    assert _record(attest_due_ts=NOW).effective_status(NOW) is SubscriptionStatus.SUSPECT


def test_not_yet_due_keeps_the_stored_status() -> None:
    assert _record(attest_due_ts=NOW + 1).effective_status(NOW) is SubscriptionStatus.ACTIVE


def test_the_record_is_frozen() -> None:
    with pytest.raises(Exception):
        _record().status = SubscriptionStatus.LAPSED  # type: ignore[misc]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_subscription_record.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'keel_core.subscription'`

- [ ] **Step 3: Implement the module**

Create `packages/keel-core/keel_core/subscription.py`:

```python
"""The per-venue subscription record, and the policy turning it into a spend cap.

Coinbase exposes no subscription endpoint, so a subscription is *user-asserted* -- see the
broker-subscription design spec §3. This module holds what was asserted and decides what may be
spent against it.

The policy is deliberately here rather than in `execution/guards.py`: as a pure function of its
inputs it is testable with no database, no `OrderIntent`, and no rail around it, and rail 14 is
left with a single call. Every row of the spec's §7 table is a test in
`tests/test_subscription_record.py`.

Staleness is asymmetric, which is why the policy fails closed. An un-synced *upgrade*
under-permits: annoying, safe. An un-synced *downgrade or lapse* over-permits -- the engine
spends against an allowance the user does not have.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class SubscriptionStatus(str, Enum):
    """Whether the asserted subscription is still believed."""

    ACTIVE = "active"
    SUSPECT = "suspect"
    LAPSED = "lapsed"


@dataclass(frozen=True)
class BrokerSubscription:
    """One venue's subscription, as last attested by the user.

    `free_volume_usd is None` means an UNLIMITED fee-free allowance (Premium) -- there is no cap,
    which is not the same as a cap of zero.

    `tier_name` may be `'unknown'`, which `keel subscription set` produces: a raw cap the user
    asserted without naming a tier. It is visibly not an attestation.
    """

    venue: str
    tier_name: str
    free_volume_usd: Decimal | None
    pacing: str
    subscription_usd_month: Decimal
    status: SubscriptionStatus
    attested_at: int
    attest_due_ts: int

    def effective_status(self, now_ts: int) -> SubscriptionStatus:
        """The status actually in force, degrading an overdue attestation to `SUSPECT`.

        An attestation past its due date is an assertion nobody has re-confirmed, which is the
        same class of unknown as never having asserted at all. Due-at is the moment it expires,
        not one tick still-good.
        """
        if self.attest_due_ts <= now_ts:
            return SubscriptionStatus.SUSPECT
        return self.status

    def allowance_usd(
        self, now_ts: int, unsubscribed_allowance_usd: Decimal
    ) -> Decimal | None:
        """The spend cap rail 14 must enforce. `None` means unlimited (no cap at all).

        Anything other than an in-force `ACTIVE` falls back to `unsubscribed_allowance_usd` --
        including an unlimited tier, because an unlimited allowance the user may no longer hold
        is worth nothing.
        """
        if self.effective_status(now_ts) is SubscriptionStatus.ACTIVE:
            return self.free_volume_usd
        return unsubscribed_allowance_usd


__all__ = ["BrokerSubscription", "SubscriptionStatus"]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_subscription_record.py -v`
Expected: 9 passed

- [ ] **Step 5: Verify nothing else moved**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy`
Expected: all pass.

Run: `uv run pytest tests/baseline/ -v && git diff --stat tests/fixtures/baseline_backtest.json`
Expected: 3 passed; the `git diff --stat` prints nothing.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: BrokerSubscription record and its fail-closed allowance policy"
```

---

### Task 2: Schema, migration machinery, and the backfill

`db.py` today is create-only: every statement is `IF NOT EXISTS` and `SCHEMA_VERSION = 1` is written once and never read. This task adds the table and the smallest thing that makes an ordered, once-only data change expressible.

**Files:**
- Modify: `keel/data/db.py` (`SCHEMA_VERSION`, `_SCHEMA_STATEMENTS`, `migrate`)
- Create: `tests/data/test_migrations.py`

**Interfaces:**
- Consumes: nothing from Task 1 (the migration writes raw SQL, not record objects — it must keep working even if the record type later changes).
- Produces: the `broker_subscriptions` table; `keel.data.db.SCHEMA_VERSION == 2`.

- [ ] **Step 1: Write the failing tests**

Create `tests/data/test_migrations.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/data/test_migrations.py -v`
Expected: FAIL — `sqlite3.OperationalError: no such table: broker_subscriptions`

- [ ] **Step 3: Add the table to the schema**

In `keel/data/db.py`, change `SCHEMA_VERSION`:

```python
SCHEMA_VERSION = 2
```

Append this entry to `_SCHEMA_STATEMENTS`, immediately after the `agent_state` statement:

```python
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
```

Note `free_volume_usd` is the only nullable money column: `NULL` means unlimited (Premium), which is not the same as `'0'`.

- [ ] **Step 4: Implement the migration step and runner**

Add `import json` to the imports at the top of `keel/data/db.py`, and add before `connect()`:

```python
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
```

Add `import time` and `from collections.abc import Callable` and `from typing import Any` to the imports.

Replace the body of `migrate()`:

```python
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/data/test_migrations.py -v`
Expected: 10 passed (9 test functions; `test_backfill_accepts_both_decimal_encodings` is parametrized twice)

- [ ] **Step 6: Verify**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy`
Expected: all pass.

Run: `uv run pytest tests/baseline/ -v && git diff --stat tests/fixtures/baseline_backtest.json`
Expected: 3 passed; no diff output.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: broker_subscriptions table and versioned migration machinery"
```

---

### Task 3: Repository access

Typed read/write for the new table, added **alongside** the existing `get_subscription`/`set_subscription`. Task 6 removes the old pair once its last consumer is gone.

**Files:**
- Modify: `keel/data/repository.py` (add after the existing subscription section, around line 376)
- Create: `tests/data/test_repository_subscriptions.py`

**Interfaces:**
- Consumes: `keel_core.subscription.{BrokerSubscription, SubscriptionStatus}` (Task 1); the `broker_subscriptions` table (Task 2).
- Produces: `Repository.get_broker_subscription(venue: str) -> BrokerSubscription | None`, `Repository.upsert_broker_subscription(record: BrokerSubscription) -> None`, `Repository.list_broker_subscriptions() -> list[BrokerSubscription]`. Tasks 5 and 6 call these exact names.

- [ ] **Step 1: Write the failing tests**

Create `tests/data/test_repository_subscriptions.py`:

```python
"""Round-trip tests for the broker_subscriptions repository methods."""

from __future__ import annotations

from decimal import Decimal

from keel.data import db
from keel.data.repository import Repository
from keel_core.subscription import BrokerSubscription, SubscriptionStatus


def _repo() -> Repository:
    conn = db.connect(":memory:")
    db.migrate(conn)
    return Repository(conn)


def _record(venue: str = "coinbase", **overrides: object) -> BrokerSubscription:
    fields: dict[str, object] = {
        "venue": venue,
        "tier_name": "Preferred",
        "free_volume_usd": Decimal("10000"),
        "pacing": "opportunistic",
        "subscription_usd_month": Decimal("29.99"),
        "status": SubscriptionStatus.ACTIVE,
        "attested_at": 1_800_000_000,
        "attest_due_ts": 1_800_000_000 + 31_536_000,
    }
    fields.update(overrides)
    return BrokerSubscription(**fields)  # type: ignore[arg-type]


def test_missing_venue_returns_none() -> None:
    """No row means never attested, which the caller must treat as unknown and closed."""
    assert _repo().get_broker_subscription("coinbase") is None


def test_upsert_then_get_round_trips_exactly() -> None:
    repo = _repo()
    record = _record()
    repo.upsert_broker_subscription(record)
    assert repo.get_broker_subscription("coinbase") == record


def test_decimals_survive_the_round_trip_without_drift() -> None:
    repo = _repo()
    repo.upsert_broker_subscription(_record(free_volume_usd=Decimal("10000.123456789")))
    loaded = repo.get_broker_subscription("coinbase")
    assert loaded is not None
    assert loaded.free_volume_usd == Decimal("10000.123456789")


def test_unlimited_round_trips_as_none_not_zero() -> None:
    repo = _repo()
    repo.upsert_broker_subscription(_record(tier_name="Premium", free_volume_usd=None))
    loaded = repo.get_broker_subscription("coinbase")
    assert loaded is not None
    assert loaded.free_volume_usd is None


def test_upsert_replaces_in_place_keyed_on_venue() -> None:
    repo = _repo()
    repo.upsert_broker_subscription(_record())
    repo.upsert_broker_subscription(_record(tier_name="Basic", free_volume_usd=Decimal("500")))

    assert len(repo.list_broker_subscriptions()) == 1
    loaded = repo.get_broker_subscription("coinbase")
    assert loaded is not None
    assert loaded.tier_name == "Basic"


def test_venues_are_independent() -> None:
    repo = _repo()
    repo.upsert_broker_subscription(_record(venue="coinbase"))
    repo.upsert_broker_subscription(_record(venue="kraken", tier_name="none"))

    coinbase = repo.get_broker_subscription("coinbase")
    kraken = repo.get_broker_subscription("kraken")
    assert coinbase is not None and coinbase.tier_name == "Preferred"
    assert kraken is not None and kraken.tier_name == "none"


def test_list_is_sorted_by_venue() -> None:
    repo = _repo()
    repo.upsert_broker_subscription(_record(venue="kraken"))
    repo.upsert_broker_subscription(_record(venue="coinbase"))
    assert [r.venue for r in repo.list_broker_subscriptions()] == ["coinbase", "kraken"]


def test_status_round_trips_as_an_enum_not_a_string() -> None:
    repo = _repo()
    repo.upsert_broker_subscription(_record(status=SubscriptionStatus.SUSPECT))
    loaded = repo.get_broker_subscription("coinbase")
    assert loaded is not None
    assert loaded.status is SubscriptionStatus.SUSPECT
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/data/test_repository_subscriptions.py -v`
Expected: FAIL — `AttributeError: 'Repository' object has no attribute 'get_broker_subscription'`

- [ ] **Step 3: Implement the repository methods**

Add `from keel_core.subscription import BrokerSubscription, SubscriptionStatus` to the imports in `keel/data/repository.py`.

Insert this section immediately after the existing `set_subscription` method (around line 376), keeping the old pair in place:

```python
    # -- broker subscriptions (per-venue, rail 14) -------------------------

    def get_broker_subscription(self, venue: str) -> BrokerSubscription | None:
        """Return `venue`'s attested subscription, or `None` if it has never been attested.

        `None` is meaningful, not an error: the design spec §7 treats a missing row as unknown
        and therefore closed. Callers must not substitute a default allowance of their own.
        """
        row = self._conn.execute(
            "SELECT * FROM broker_subscriptions WHERE venue = ?", (venue,)
        ).fetchone()
        return None if row is None else _subscription_from_row(row)

    def upsert_broker_subscription(self, record: BrokerSubscription) -> None:
        """Insert or replace `record`, keyed on venue. One subscription per venue."""
        self._conn.execute(
            """
            INSERT INTO broker_subscriptions (
                venue, tier_name, free_volume_usd, pacing,
                subscription_usd_month, status, attested_at, attest_due_ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(venue) DO UPDATE SET
                tier_name = excluded.tier_name,
                free_volume_usd = excluded.free_volume_usd,
                pacing = excluded.pacing,
                subscription_usd_month = excluded.subscription_usd_month,
                status = excluded.status,
                attested_at = excluded.attested_at,
                attest_due_ts = excluded.attest_due_ts
            """,
            (
                record.venue,
                record.tier_name,
                _dec_to_text(record.free_volume_usd),
                record.pacing,
                _dec_to_text(record.subscription_usd_month),
                record.status.value,
                record.attested_at,
                record.attest_due_ts,
            ),
        )
        self._conn.commit()

    def list_broker_subscriptions(self) -> list[BrokerSubscription]:
        """Every attested subscription, ordered by venue -- what `keel subscription show` renders."""
        rows = self._conn.execute(
            "SELECT * FROM broker_subscriptions ORDER BY venue"
        ).fetchall()
        return [_subscription_from_row(row) for row in rows]
```

Add this module-level helper next to `_text_to_dec` (around line 66):

```python
def _subscription_from_row(row: sqlite3.Row) -> BrokerSubscription:
    """Map a `broker_subscriptions` row to the domain record.

    `free_volume_usd` is the one nullable money column: NULL means unlimited (Premium), which is
    deliberately not the same as `'0'`.
    """
    return BrokerSubscription(
        venue=row["venue"],
        tier_name=row["tier_name"],
        free_volume_usd=_text_to_dec(row["free_volume_usd"]),
        pacing=row["pacing"],
        subscription_usd_month=Decimal(row["subscription_usd_month"]),
        status=SubscriptionStatus(row["status"]),
        attested_at=int(row["attested_at"]),
        attest_due_ts=int(row["attest_due_ts"]),
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/data/test_repository_subscriptions.py -v`
Expected: 8 passed

- [ ] **Step 5: Verify**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy`
Expected: all pass. The old `get_subscription`/`set_subscription` still exist, so nothing else moved.

Run: `uv run pytest tests/baseline/ -v && git diff --stat tests/fixtures/baseline_backtest.json`
Expected: 3 passed; no diff output.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: typed repository access for broker_subscriptions"
```

---

### Task 4: Config — rename the simulator's allowance, add the unsubscribed cap

`monthly_allowance_usd` currently serves two unrelated roles: rail 14's live cap and the simulator's assumed allowance. Rail 14 stops reading it in Task 5, so it is renamed to say what it actually is — the simulator's assumption.

**Read the spec's §6.1 before starting.** The rename exists because `keel/sim/account.py:167` is a second implementation of rail 14 held in explicit parity with `guards.check`, and deleting the field would strand it.

**Files:**
- Modify: `packages/keel-core/keel_core/config.py` (`SubscriptionConfig` around line 108; parsing around lines 387-393 and 440-446)
- Modify: `keel/sim/account.py:168`
- Modify: `config.yaml:64-66`
- Modify: `tests/conftest.py` (`VALID_CONFIG_YAML`, line 59)
- Modify: `tests/test_config.py` (subscription assertions)
- Modify: `tests/sim/test_account.py`, `tests/sim/test_portfolio_sim.py` (config construction)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `SubscriptionConfig.assumed_free_volume_usd: Decimal` (default `Decimal("500")`) and `SubscriptionConfig.unsubscribed_allowance_usd: Decimal` (default `Decimal("0")`). `SubscriptionConfig.monthly_allowance_usd` no longer exists. Task 5 reads `unsubscribed_allowance_usd`; Task 6 reads `pacing`.

- [ ] **Step 1: Find every reference before changing anything**

Run: `uv run grep -rn "monthly_allowance_usd" keel/ tests/ config.yaml packages/ --include="*.py" --include="*.yaml" | grep -v __pycache__`

Expected: hits in `packages/keel-core/keel_core/config.py`, `keel/sim/account.py`, `keel/data/repository.py`, `keel/execution/guards.py`, `keel/cli.py`, `config.yaml`, `tests/conftest.py`, `tests/test_config.py`, `tests/sim/test_account.py`, `tests/sim/test_portfolio_sim.py`, `tests/data/test_repository.py`, `tests/execution/test_guards.py`, `tests/test_cli.py`.

**Only the `config.subscription.monthly_allowance_usd` references change in this task.** Hits in `repository.py`, `guards.py`, `cli.py`, `tests/data/test_repository.py`, `tests/execution/test_guards.py`, and `tests/test_cli.py` are the *DB blob's* dict key, which Tasks 5 and 6 handle. Do not touch them here.

- [ ] **Step 2: Write the failing config tests**

Add to `tests/test_config.py`:

```python
def test_assumed_free_volume_usd_parses(write_config) -> None:
    from tests.conftest import VALID_CONFIG_YAML

    path = write_config(
        VALID_CONFIG_YAML.replace(
            "assumed_free_volume_usd: 500", "assumed_free_volume_usd: 1234.5"
        )
    )
    config = load_config(path)
    assert config.subscription.assumed_free_volume_usd == Decimal("1234.5")


def test_unsubscribed_allowance_defaults_to_zero(valid_config_path) -> None:
    """Fail-closed: an unattested venue may spend nothing unless the user says otherwise."""
    assert load_config(valid_config_path).subscription.unsubscribed_allowance_usd == Decimal("0")


def test_unsubscribed_allowance_parses(write_config) -> None:
    from tests.conftest import VALID_CONFIG_YAML

    path = write_config(
        VALID_CONFIG_YAML.replace(
            "pacing: opportunistic", "pacing: opportunistic\n  unsubscribed_allowance_usd: 25"
        )
    )
    assert load_config(path).subscription.unsubscribed_allowance_usd == Decimal("25")


def test_the_old_monthly_allowance_key_is_rejected(write_config) -> None:
    """Silently ignoring a hand-set spend number is the exact defect this work fixes."""
    from tests.conftest import VALID_CONFIG_YAML

    path = write_config(
        VALID_CONFIG_YAML.replace("assumed_free_volume_usd: 500", "monthly_allowance_usd: 500")
    )
    with pytest.raises(ConfigError, match="assumed_free_volume_usd"):
        load_config(path)


def test_the_rejection_message_points_at_attest(write_config) -> None:
    from tests.conftest import VALID_CONFIG_YAML

    path = write_config(
        VALID_CONFIG_YAML.replace("assumed_free_volume_usd: 500", "monthly_allowance_usd: 500")
    )
    with pytest.raises(ConfigError, match="subscription attest"):
        load_config(path)
```

Ensure `tests/test_config.py` imports `ConfigError` and `Decimal` if it does not already.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_config.py -k "assumed or unsubscribed or monthly_allowance" -v`
Expected: FAIL — `AttributeError: 'SubscriptionConfig' object has no attribute 'assumed_free_volume_usd'`

- [ ] **Step 4: Update `SubscriptionConfig`**

Replace `SubscriptionConfig` in `packages/keel-core/keel_core/config.py`:

```python
@dataclass(frozen=True)
class SubscriptionConfig:
    """Subscription-related settings. Three fields, three distinct roles — do not conflate them.

    `assumed_free_volume_usd` is the **simulator's** assumed fee-free monthly volume
    (`sim/account.py`'s `_monthly_allowance_cap`). It is a pinned, reproducible assumption for
    backtests. It is NOT the live cap: rail 14 derives that from the attested
    `broker_subscriptions` record, so that upgrading a tier changes one place. This field was
    called `monthly_allowance_usd` when it meant both, which is exactly the defect the
    subscription design spec §2 describes.

    `unsubscribed_allowance_usd` is what rail 14 permits on a venue whose subscription is
    unattested, suspect, lapsed, or overdue. The default `0` stops trading on that venue. A user
    content to pay fees may raise it deliberately — the point is that continuing to spend is an
    explicit choice rather than the consequence of a stale row.

    `pacing="opportunistic"` (default) enforces only the flat monthly cap. `pacing="even_daily"`
    additionally caps cumulative month-to-date spend to
    `allowance / business_days_in_month * business_days_elapsed`, so the allowance cannot be
    blown in one burst early in the month. It is the default pacing for new attestations, and
    the simulator reads it directly.
    """

    assumed_free_volume_usd: Decimal = Decimal("500")
    unsubscribed_allowance_usd: Decimal = Decimal("0")
    pacing: str = "opportunistic"
```

- [ ] **Step 5: Update config parsing**

In `packages/keel-core/keel_core/config.py`, after the existing `pacing` validation (around line 389), add the rejection:

```python
    if "monthly_allowance_usd" in subscription_raw:
        raise ConfigError(
            "subscription.monthly_allowance_usd was renamed to "
            "subscription.assumed_free_volume_usd, which is now the SIMULATOR's assumed "
            "allowance only. The live rail-14 cap comes from the attested subscription record "
            "-- set it with `keel subscription attest --venue coinbase --tier <tier>`."
        )
```

Replace the `subscription=SubscriptionConfig(...)` block (around line 440):

```python
        subscription=SubscriptionConfig(
            assumed_free_volume_usd=_non_negative_decimal(
                subscription_raw.get("assumed_free_volume_usd", "500"),
                "subscription.assumed_free_volume_usd",
            ),
            unsubscribed_allowance_usd=_non_negative_decimal(
                subscription_raw.get("unsubscribed_allowance_usd", "0"),
                "subscription.unsubscribed_allowance_usd",
            ),
            pacing=pacing,
        ),
```

- [ ] **Step 6: Update the simulator**

In `keel/sim/account.py`, change line 168 inside `_monthly_allowance_cap`:

```python
        allowance = config.subscription.assumed_free_volume_usd
```

Add to that method's docstring (or immediately above it) a one-line note:

```python
        # The SIMULATOR's assumed allowance, deliberately from config and never from the
        # attested `broker_subscriptions` record: reading live state here would make backtests
        # non-reproducible. See the subscription design spec §6.1.
```

- [ ] **Step 7: Update `config.yaml` and the shared test fixture**

In `config.yaml`, replace lines 64-66:

```yaml
subscription:
  # The SIMULATOR's assumed fee-free monthly volume. The LIVE rail-14 cap is not set here --
  # it comes from the attested record: `keel subscription attest --venue coinbase --tier <t>`.
  assumed_free_volume_usd: 500
  # What rail 14 permits on a venue that is unattested, suspect, lapsed, or overdue.
  # 0 means such a venue cannot buy at all until it is attested.
  unsubscribed_allowance_usd: 0
  pacing: opportunistic  # opportunistic (monthly cap only) | even_daily (also paces per business day)
```

In `tests/conftest.py`, replace the `subscription:` block inside `VALID_CONFIG_YAML`:

```yaml
subscription:
  assumed_free_volume_usd: 500
  unsubscribed_allowance_usd: 0
  pacing: opportunistic
```

- [ ] **Step 8: Fix the remaining call sites**

Run: `uv run pytest -q 2>&1 | tail -40`

Update every failure caused by `config.subscription.monthly_allowance_usd` — expected in `tests/test_config.py`, `tests/sim/test_account.py`, and `tests/sim/test_portfolio_sim.py` — by renaming the attribute or the `SubscriptionConfig(...)` keyword to `assumed_free_volume_usd`. These are mechanical renames; do not change any test's assertions or numbers.

If a failure is *not* an attribute rename, stop and re-read Step 1 — you may be editing a DB-blob reference that belongs to Task 5 or 6.

- [ ] **Step 9: Run the tests to verify they pass**

Run: `uv run pytest tests/test_config.py tests/sim/ -v`
Expected: all pass, including the five new config tests.

- [ ] **Step 10: Verify**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy`
Expected: all pass.

Run: `uv run pytest tests/baseline/ -v && git diff --stat tests/fixtures/baseline_backtest.json`
Expected: 3 passed; no diff output. The simulator's number is unchanged, only its name.

- [ ] **Step 11: Commit**

```bash
git add -A
git commit -m "refactor: rename monthly_allowance_usd to assumed_free_volume_usd, add unsubscribed cap"
```

---

### Task 5: Rail 14 derives its cap from the record

The point of the whole change. Rail 14 stops reading a config number and reads the attested record, under the §7 fail-closed policy.

**Files:**
- Modify: `keel/execution/guards.py` (module docstring's rail 14 paragraph; rail 14 body around lines 366-398; add `DEFAULT_VENUE` near the other rail constants around line 80)
- Modify: `tests/execution/test_guards.py` (rail 14 tests)
- Modify: `tests/execution/test_executor.py` (subscription setup)
- Modify: `tests/sim/test_account.py` (parity test setup)

**Interfaces:**
- Consumes: `Repository.get_broker_subscription` (Task 3); `BrokerSubscription.allowance_usd` and `SubscriptionStatus` (Task 1); `config.subscription.unsubscribed_allowance_usd` (Task 4).
- Produces: `keel.execution.guards.DEFAULT_VENUE = "coinbase"`. Task 6's CLI defaults `--venue` to it.

- [ ] **Step 1: Update the shared `repo` fixture — do this first**

`tests/execution/test_guards.py:35`'s `repo` fixture currently seeds a large allowance through the old API:

```python
    r.set_subscription(_LARGE_ALLOWANCE, "opportunistic", now_ts=NOW_TS)
```

Its docstring explains why: pre-existing rail tests that don't exercise rail 14 must not be incidentally tripped by it. **After this task rail 14 no longer reads that blob**, so leaving this line makes every BUY test in the file fail closed. Replace it:

```python
    r.upsert_broker_subscription(
        BrokerSubscription(
            venue="coinbase",
            tier_name="Preferred",
            free_volume_usd=_LARGE_ALLOWANCE,
            pacing="opportunistic",
            subscription_usd_month=Decimal("29.99"),
            status=SubscriptionStatus.ACTIVE,
            attested_at=NOW_TS,
            attest_due_ts=NOW_TS + 31_536_000,
        )
    )
```

Update the fixture docstring's last sentence to say rail-14-specific tests override it with `_attest(...)` rather than `repo.set_subscription(...)`.

Add to the module's imports:

```python
from keel_core.subscription import BrokerSubscription, SubscriptionStatus
```

- [ ] **Step 2: Add an `unsubscribed_allowance_usd` knob to `_config`**

`_config(...)` at `tests/execution/test_guards.py:52` takes keyword-only overrides and builds `Config(...)` without passing `subscription=` at all. Add a parameter and wire it:

```python
def _config(
    *,
    allowlist: tuple[str, ...] = ("BTC", "ETH", "PAXG"),
    max_per_order_usd: Decimal = Decimal("100"),
    max_per_day_usd: Decimal = Decimal("300"),
    max_exposure_usd: Decimal = Decimal("1000"),
    max_per_asset_pct: Decimal = Decimal("0.5"),
    max_total_dd_pct: Decimal = Decimal("0.20"),
    max_weekly_dd_pct: Decimal = Decimal("0.08"),
    interval_sec: int = 900,
    unsubscribed_allowance_usd: Decimal = Decimal("0"),
) -> Config:
```

and add to the `Config(...)` call:

```python
        subscription=SubscriptionConfig(
            unsubscribed_allowance_usd=unsubscribed_allowance_usd
        ),
```

Import `SubscriptionConfig` alongside the other config types the module already imports.

- [ ] **Step 3: Write the failing rail-14 tests**

Append to `tests/execution/test_guards.py`, next to the existing `test_rail14_*` tests (which start at line 521). These use the file's real conventions: the `repo` fixture, `NOW_TS`, `_intent(**overrides)`, `_config(...)`, and `_keys(result)`.

```python
def _attest(
    repo: Repository,
    *,
    free_volume_usd: Decimal | None,
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE,
    pacing: str = "opportunistic",
    attested_at: int = NOW_TS,
    attest_due_ts: int | None = None,
) -> None:
    """Attest a coinbase subscription -- the setup every rail-14 test now needs."""
    repo.upsert_broker_subscription(
        BrokerSubscription(
            venue="coinbase",
            tier_name="Preferred",
            free_volume_usd=free_volume_usd,
            pacing=pacing,
            subscription_usd_month=Decimal("29.99"),
            status=status,
            attested_at=attested_at,
            attest_due_ts=(
                attest_due_ts if attest_due_ts is not None else attested_at + 31_536_000
            ),
        )
    )


def _unattested_repo() -> Repository:
    """A compliant repo with NO subscription -- a fresh install, before any attestation."""
    conn = connect(":memory:")
    migrate(conn)
    r = Repository(conn)
    r.set_state("kill_switch", False)
    r.set_state("last_feed_ts", NOW_TS)
    return r


def test_rail14_refuses_a_buy_when_nothing_has_been_attested() -> None:
    """keel ships inert: no record means no live BUY."""
    result = guards.check(_intent(), _unattested_repo(), _roomy_config(), NOW_TS)
    assert not result.ok
    assert "subscription_unattested" in _keys(result)


def test_rail14_inert_message_tells_the_user_to_attest() -> None:
    """A bare "0 exceeds cap 0" is arithmetically true and practically useless."""
    result = guards.check(_intent(), _unattested_repo(), _roomy_config(), NOW_TS)
    violation = next(v for v in result.violations if v.startswith("subscription_unattested"))
    assert "attest" in violation
    assert "coinbase" in violation


def test_rail14_allows_a_buy_inside_an_attested_allowance(repo: Repository) -> None:
    _attest(repo, free_volume_usd=Decimal("10000"))
    result = guards.check(_intent(notional=Decimal("50")), repo, _roomy_config(), NOW_TS)
    assert result.ok


def test_rail14_still_caps_at_the_attested_allowance(repo: Repository) -> None:
    _attest(repo, free_volume_usd=Decimal("40"))
    result = guards.check(_intent(notional=Decimal("50")), repo, _roomy_config(), NOW_TS)
    assert "monthly_subscription_allowance" in _keys(result)


def test_rail14_passes_unconditionally_for_an_unlimited_tier(repo: Repository) -> None:
    """Premium has no cap, and pacing a cap that does not exist is meaningless."""
    _attest(repo, free_volume_usd=None, pacing="even_daily")
    result = guards.check(_intent(notional=Decimal("50")), repo, _roomy_config(), NOW_TS)
    assert "monthly_subscription_allowance" not in _keys(result)
    assert "subscription_unattested" not in _keys(result)


@pytest.mark.parametrize("status", [SubscriptionStatus.SUSPECT, SubscriptionStatus.LAPSED])
def test_rail14_fails_closed_on_a_degraded_subscription(
    repo: Repository, status: SubscriptionStatus
) -> None:
    _attest(repo, free_volume_usd=Decimal("10000"), status=status)
    result = guards.check(_intent(notional=Decimal("50")), repo, _roomy_config(), NOW_TS)
    assert "subscription_unattested" in _keys(result)


def test_rail14_fails_closed_on_an_overdue_attestation(repo: Repository) -> None:
    _attest(
        repo,
        free_volume_usd=Decimal("10000"),
        attested_at=NOW_TS - 40_000_000,
        attest_due_ts=NOW_TS - 1,
    )
    result = guards.check(_intent(notional=Decimal("50")), repo, _roomy_config(), NOW_TS)
    violation = next(v for v in result.violations if v.startswith("subscription_unattested"))
    assert "overdue" in violation


def test_rail14_honours_a_raised_unsubscribed_allowance() -> None:
    """A user content to pay fees may raise it -- deliberately, not by accident."""
    config = _config(
        max_per_order_usd=Decimal("10000"),
        max_per_day_usd=Decimal("10000"),
        max_exposure_usd=Decimal("100000"),
        unsubscribed_allowance_usd=Decimal("200"),
    )
    result = guards.check(_intent(notional=Decimal("50")), _unattested_repo(), config, NOW_TS)
    assert "subscription_unattested" not in _keys(result)


def test_rail14_reads_pacing_from_the_record_not_config(repo: Repository) -> None:
    """even_daily paces the attested allowance across elapsed business days."""
    _attest(repo, free_volume_usd=Decimal("10000"), pacing="even_daily")
    result = guards.check(_intent(notional=Decimal("9000")), repo, _roomy_config(), NOW_TS)
    violation = next(
        v for v in result.violations if v.startswith("monthly_subscription_allowance")
    )
    assert "even_daily pacing" in violation


def test_rail14_does_not_gate_sells() -> None:
    """SELL produces quote currency; the rail exists to cap spend, so it must not fire."""
    result = guards.check(
        _intent(side=Side.SELL), _unattested_repo(), _roomy_config(), NOW_TS
    )
    assert "subscription_unattested" not in _keys(result)
    assert "monthly_subscription_allowance" not in _keys(result)
```

`_roomy_config()` already exists at line 511 and is what the surrounding rail-14 tests use to keep the other spend caps out of the way. `_unattested_repo()` mirrors the `repo` fixture minus the attestation — it cannot use the fixture, since the fixture's whole job is to have a subscription.

If `test_rail14_reads_pacing_from_the_record_not_config`'s notional trips a different cap first, raise `_roomy_config()`'s ceilings via `_config(...)` rather than lowering the notional — the test needs a value inside the flat cap but outside the paced one.

- [ ] **Step 4: Run the tests to verify they fail**

Run: `uv run pytest tests/execution/test_guards.py -k rail14 -v`
Expected: FAIL. Rail 14 still reads the old blob, so `test_rail14_refuses_a_buy_when_nothing_has_been_attested` and its siblings fail — an unattested repo currently yields the *default* `SubscriptionConfig` allowance rather than a veto, and no violation key `subscription_unattested` exists yet.

- [ ] **Step 5: Add the venue constant**

In `keel/execution/guards.py`, next to the other rail constants (around line 80):

```python
# Rail 14: the engine is single-venue until the broker port lands. `OrderIntent` carries no
# venue to key on, and inventing one before then would be a guess -- but `broker_subscriptions`
# is venue-keyed from birth because that costs nothing and is the right shape. This constant is
# the one line the multi-venue migration deletes (monorepo design spec §8).
DEFAULT_VENUE = "coinbase"
```

- [ ] **Step 6: Rewrite rail 14**

Add to `keel/execution/guards.py`'s imports:

```python
from keel_core.subscription import SubscriptionStatus
```

Replace the rail 14 block (the `if is_buy:` clause beginning `subscription = repo.get_subscription()`):

```python
    # 14. Monthly subscription-allowance — month-to-date live BUY spend + this order must not
    #     exceed the allowance derived from the venue's *attested* subscription record
    #     (`repo.get_broker_subscription`), read fresh on every call so an attestation takes
    #     effect on the very next order. Fails closed: unattested, suspect, lapsed, or overdue
    #     all fall back to `unsubscribed_allowance_usd` (default 0). DCA is NOT exempt -- it is
    #     exactly the recurring spend this rail exists to cap (Issue #59).
    if is_buy:
        record = repo.get_broker_subscription(DEFAULT_VENUE)
        unsubscribed = config.subscription.unsubscribed_allowance_usd

        if record is None:
            allowance: Decimal | None = unsubscribed
            degraded_reason = "no subscription has been attested"
            pacing = "opportunistic"
        else:
            allowance = record.allowance_usd(now_ts, unsubscribed)
            pacing = record.pacing
            effective = record.effective_status(now_ts)
            if effective is SubscriptionStatus.ACTIVE:
                degraded_reason = ""
            elif record.attest_due_ts <= now_ts:
                degraded_reason = "its attestation is overdue"
                log_event(
                    logger,
                    logging.WARNING,
                    "subscription.attestation_overdue",
                    venue=DEFAULT_VENUE,
                    attested_at=record.attested_at,
                    attest_due_ts=record.attest_due_ts,
                )
            else:
                degraded_reason = f"its subscription is {effective.value}"

        # An unlimited allowance (Premium, in force) has no cap to exceed, and pacing a cap that
        # does not exist is meaningless -- the rail simply does not apply.
        if allowance is not None:
            monthly_spend = _monthly_buy_spend_usd(repo, now_ts)
            projected_monthly = monthly_spend + intent.notional

            effective_cap = allowance
            pacing_note = ""
            if pacing == "even_daily":
                dt = datetime.fromtimestamp(now_ts, tz=UTC)
                biz_days_in_month = _business_days_in_month(dt.year, dt.month)
                biz_days_elapsed = _business_days_elapsed(dt.year, dt.month, dt.day)
                if biz_days_in_month > 0:
                    paced_cap = (allowance / biz_days_in_month) * biz_days_elapsed
                    if paced_cap < effective_cap:
                        effective_cap = paced_cap
                        pacing_note = (
                            f" (even_daily pacing: {biz_days_elapsed}/{biz_days_in_month} "
                            f"business days elapsed -> paced cap {paced_cap})"
                        )

            if projected_monthly > effective_cap:
                if degraded_reason:
                    # A user in this state is not over budget -- they have no budget. Telling
                    # them "0 exceeds 0" would be true and useless.
                    violations.append(
                        f"subscription_unattested: {DEFAULT_VENUE} cannot spend because "
                        f"{degraded_reason}, so its allowance is the unsubscribed default "
                        f"{unsubscribed}. Run `keel subscription attest --venue "
                        f"{DEFAULT_VENUE} --tier <tier>` to restore it."
                    )
                else:
                    remaining = max(effective_cap - monthly_spend, Decimal("0"))
                    violations.append(
                        "monthly_subscription_allowance: month-to-date BUY spend "
                        f"{monthly_spend} + {intent.notional} = {projected_monthly} exceeds the "
                        f"allowance cap {effective_cap}{pacing_note} -- remaining allowance "
                        f"{remaining}"
                    )
```

- [ ] **Step 7: Update the module docstring**

In `keel/execution/guards.py`, replace the rail 14 bullet in the module docstring (the paragraph beginning "Rail 14 (monthly subscription-allowance) caps this calendar month's live BUY notional"):

```
- Rail 14 (monthly subscription-allowance) caps this calendar month's live BUY notional (own
  spend, from the orders audit log, `_monthly_buy_spend_usd`) plus this order's notional against
  the allowance derived from the venue's **attested subscription record**
  (`repo.get_broker_subscription`, `data/repository.py`) -- read fresh on every `check()` call,
  never cached, so `keel subscription attest` takes effect on the very next order. The cap is
  `free_volume_usd` from that record, so upgrading a tier changes exactly one place; it is NOT
  typed into config. **Fails closed** like rails 12/13: an unattested venue, a `suspect` or
  `lapsed` record, or one whose `attest_due_ts` has passed all fall back to
  `config.subscription.unsubscribed_allowance_usd` (default 0, i.e. no spending) -- silence is
  not consent to spend. A record with `free_volume_usd IS NULL` (Premium, unlimited and in
  force) has no cap and the rail does not apply. Optional `pacing="even_daily"`, read from the
  record, additionally caps cumulative month-to-date spend to
  `allowance / business_days_in_month * business_days_elapsed` (Mon-Fri, no holiday calendar);
  `pacing="opportunistic"` (default) skips that extra check.
```

- [ ] **Step 8: Fix the existing rail-14 and executor tests**

Run: `uv run pytest tests/execution/ -q 2>&1 | tail -40`

Existing tests that relied on a defaulted allowance now fail closed. For each, add an `_attest(...)` (or equivalent `upsert_broker_subscription`) setup step with the allowance that test intends. **Do not weaken an assertion to make a test pass** — if a test asserted a BUY succeeds, attest an allowance large enough; the test's intent is preserved by giving it the subscription it always implicitly assumed.

Do the same for `tests/execution/test_executor.py`, whose fixtures set a subscription through the old API.

- [ ] **Step 9: Fix the sim parity tests**

Run: `uv run pytest tests/sim/test_account.py -q 2>&1 | tail -30`

`test_parity_with_guards_check_*` builds equivalent `Repository`/`Config` state and asserts `SimAccount.can_open` agrees with `guards.check`. It must now also attest a record whose `free_volume_usd` equals `config.subscription.assumed_free_volume_usd`, making the parity an explicit setup step rather than an ambient coincidence. Add a comment saying exactly that.

- [ ] **Step 10: Run the tests to verify they pass**

Run: `uv run pytest tests/execution/ tests/sim/ -v`
Expected: all pass, including the ten new rail-14 tests.

- [ ] **Step 11: Verify**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy`
Expected: all pass.

Run: `uv run pytest tests/baseline/ -v && git diff --stat tests/fixtures/baseline_backtest.json`
Expected: 3 passed; no diff output. Backtesting never runs the rails, so any change here is a bug — stop and investigate rather than regenerating the golden file.

- [ ] **Step 12: Commit**

```bash
git add -A
git commit -m "feat: rail 14 derives its cap from the attested subscription record"
```

---

### Task 6: The attest CLI, and retiring the old path

`attest` is the only way to establish a subscription. This task also removes the seeding path and the old repository pair, whose last consumers disappear here.

**Files:**
- Modify: `keel/cli.py` (delete `_ensure_subscription_seeded` at line 170 and its call at line 360; rewrite the `subscription` group at lines 1098-1170)
- Modify: `keel/data/repository.py` (delete `get_subscription`/`set_subscription`, lines 348-376)
- Modify: `tests/test_cli.py`, `tests/data/test_repository.py`
- Create: `tests/test_cli_subscription.py`

**Interfaces:**
- Consumes: `Repository.{get_broker_subscription, upsert_broker_subscription, list_broker_subscriptions}` (Task 3); `BrokerSubscription`/`SubscriptionStatus` (Task 1); `guards.DEFAULT_VENUE` (Task 5); `config.tiers` and `config.subscription.pacing` (Task 4).
- Produces: `keel subscription attest|set|show`.

- [ ] **Step 1: Write the failing CLI tests**

Create `tests/test_cli_subscription.py`, following `tests/test_cli.py`'s conventions: an inline `CliRunner()` per test, a `_repo_at(db_path)` helper opening a migrated `Repository`, and `cli` invoked as `["--db", str(db_path), ...]`. There is no shared `cli_env` fixture in that file and this plan does not add one.

The `--config` flag is passed explicitly so the tier definitions come from the shared `VALID_CONFIG_YAML` fixture (`tests/conftest.py`), which carries Basic/Preferred/Premium.

```python
"""Tests for `keel subscription attest|set|show`.

Attestation is the only thing that establishes a live spend cap, so these tests pin what each
command writes, not merely that it exits zero. Everything runs through `CliRunner` -- no live
network, no live broker, matching `tests/test_cli.py`.
"""

from __future__ import annotations

import dataclasses
from decimal import Decimal
from pathlib import Path

import pytest
from click.testing import CliRunner
from keel.cli import cli
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel_core.subscription import SubscriptionStatus

ONE_YEAR = 31_536_000


def _repo_at(db_path: Path) -> Repository:
    conn = connect(str(db_path))
    migrate(conn)
    return Repository(conn)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "keel.db"


def _run(db_path: Path, config_path: Path, *args: str):
    return CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(config_path), *args]
    )


def test_attest_writes_the_tiers_values(db_path: Path, valid_config_path: Path) -> None:
    result = _run(
        db_path, valid_config_path,
        "subscription", "attest", "--venue", "coinbase", "--tier", "Preferred",
    )
    assert result.exit_code == 0, result.output

    record = _repo_at(db_path).get_broker_subscription("coinbase")
    assert record is not None
    assert record.tier_name == "Preferred"
    assert record.free_volume_usd == Decimal("10000")
    assert record.subscription_usd_month == Decimal("29.99")
    assert record.status is SubscriptionStatus.ACTIVE


def test_attest_sets_a_one_year_due_date(db_path: Path, valid_config_path: Path) -> None:
    _run(db_path, valid_config_path,
         "subscription", "attest", "--venue", "coinbase", "--tier", "Basic")
    record = _repo_at(db_path).get_broker_subscription("coinbase")
    assert record is not None
    assert record.attest_due_ts == record.attested_at + ONE_YEAR


def test_attest_stores_unlimited_as_null_for_premium(
    db_path: Path, valid_config_path: Path
) -> None:
    _run(db_path, valid_config_path,
         "subscription", "attest", "--venue", "coinbase", "--tier", "Premium")
    record = _repo_at(db_path).get_broker_subscription("coinbase")
    assert record is not None
    assert record.free_volume_usd is None


def test_attest_clears_a_suspect_status(db_path: Path, valid_config_path: Path) -> None:
    """Only an explicit attestation clears suspect -- detection must not be self-clearing."""
    _run(db_path, valid_config_path,
         "subscription", "set", "--venue", "coinbase", "--free-volume-usd", "500")

    repo = _repo_at(db_path)
    stored = repo.get_broker_subscription("coinbase")
    assert stored is not None
    repo.upsert_broker_subscription(
        dataclasses.replace(stored, status=SubscriptionStatus.SUSPECT)
    )

    _run(db_path, valid_config_path,
         "subscription", "attest", "--venue", "coinbase", "--tier", "Preferred")

    record = _repo_at(db_path).get_broker_subscription("coinbase")
    assert record is not None
    assert record.status is SubscriptionStatus.ACTIVE


def test_attest_rejects_an_unknown_tier_and_lists_the_valid_ones(
    db_path: Path, valid_config_path: Path
) -> None:
    result = _run(db_path, valid_config_path,
                  "subscription", "attest", "--venue", "coinbase", "--tier", "Gold")
    assert result.exit_code != 0
    assert "Basic" in result.output
    assert "Preferred" in result.output
    assert "Premium" in result.output
    assert _repo_at(db_path).get_broker_subscription("coinbase") is None


def test_attest_keeps_an_existing_pacing_choice(
    db_path: Path, valid_config_path: Path
) -> None:
    """Re-attesting must not silently reset a pacing the user set earlier."""
    _run(db_path, valid_config_path,
         "subscription", "attest", "--venue", "coinbase", "--tier", "Basic",
         "--pacing", "even_daily")
    _run(db_path, valid_config_path,
         "subscription", "attest", "--venue", "coinbase", "--tier", "Preferred")

    record = _repo_at(db_path).get_broker_subscription("coinbase")
    assert record is not None
    assert record.pacing == "even_daily"


def test_set_leaves_the_tier_unknown(db_path: Path, valid_config_path: Path) -> None:
    """The escape hatch must be visibly not an attestation."""
    _run(db_path, valid_config_path,
         "subscription", "set", "--venue", "coinbase", "--free-volume-usd", "750")

    record = _repo_at(db_path).get_broker_subscription("coinbase")
    assert record is not None
    assert record.tier_name == "unknown"
    assert record.free_volume_usd == Decimal("750")
    assert record.status is SubscriptionStatus.ACTIVE


def test_set_rejects_a_negative_allowance(db_path: Path, valid_config_path: Path) -> None:
    result = _run(db_path, valid_config_path,
                  "subscription", "set", "--venue", "coinbase", "--free-volume-usd", "-1")
    assert result.exit_code != 0


def test_show_reports_nothing_attested_on_a_fresh_database(
    db_path: Path, valid_config_path: Path
) -> None:
    result = _run(db_path, valid_config_path, "subscription", "show")
    assert result.exit_code == 0
    assert "no subscription" in result.output.lower()


def test_show_surfaces_effective_status_and_cap(
    db_path: Path, valid_config_path: Path
) -> None:
    _run(db_path, valid_config_path,
         "subscription", "attest", "--venue", "coinbase", "--tier", "Preferred")

    result = _run(db_path, valid_config_path, "subscription", "show")
    assert "coinbase" in result.output
    assert "Preferred" in result.output
    assert "10000" in result.output
    assert "effective_status=active" in result.output


def test_show_reports_an_overdue_record_as_suspect(
    db_path: Path, valid_config_path: Path
) -> None:
    """Effective status is what a user needs and is not a stored column."""
    _run(db_path, valid_config_path,
         "subscription", "attest", "--venue", "coinbase", "--tier", "Preferred")

    repo = _repo_at(db_path)
    stored = repo.get_broker_subscription("coinbase")
    assert stored is not None
    repo.upsert_broker_subscription(dataclasses.replace(stored, attest_due_ts=1))

    result = _run(db_path, valid_config_path, "subscription", "show")
    assert "effective_status=suspect" in result.output
```

`valid_config_path` is the existing fixture from `tests/conftest.py`. States no command can produce — a `suspect` status, an expired due date — are reached with `dataclasses.replace` on a stored record rather than a bespoke helper.

`--db` and `--config` are both global options on the `cli` group (`keel/cli.py:200-209`).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli_subscription.py -v`
Expected: FAIL — `Error: No such command 'attest'`

- [ ] **Step 3: Rewrite the subscription CLI group**

In `keel/cli.py`, replace the entire `subscription` group (the `subscription_group`, `subscription_show`, and `subscription_set` definitions):

```python
# -- subscription (rail 14, per-venue attested allowance) ----------------------------------------

ATTESTATION_PERIOD_SEC = 365 * 24 * 3600


@cli.group("subscription")
def subscription_group() -> None:
    """View or attest a venue's subscription (the allowance execution.guards rail 14 enforces).

    Coinbase exposes no subscription endpoint, so a subscription is *asserted* by the user, not
    fetched. `attest` is that assertion. Rail 14 reads the resulting record fresh on every order,
    so an attestation takes effect on the very next one, with no restart.

    Until a venue is attested, rail 14 caps it at `subscription.unsubscribed_allowance_usd`
    (default 0) -- keel ships unable to place a live BUY, deliberately.
    """


def _resolve_pacing(
    repo: Repository, config: Config, venue: str, pacing: str | None
) -> str:
    """Explicit `--pacing` wins; otherwise keep the venue's existing choice, else config's.

    Re-attesting must not silently reset a pacing mode the user set earlier.
    """
    if pacing is not None:
        return pacing
    existing = repo.get_broker_subscription(venue)
    return existing.pacing if existing is not None else config.subscription.pacing


@subscription_group.command("attest")
@click.option("--venue", default=DEFAULT_VENUE, show_default=True, help="Venue to attest.")
@click.option("--tier", "tier_name", required=True, help="Tier name from config.yaml's `tiers`.")
@click.option(
    "--pacing",
    type=click.Choice(["opportunistic", "even_daily"]),
    default=None,
    help="Pacing mode (default: keep the venue's current value).",
)
@click.pass_context
@with_disclaimer
def subscription_attest(
    ctx: click.Context, venue: str, tier_name: str, pacing: str | None
) -> None:
    """Assert which subscription tier this venue is on -- the only thing that clears `suspect`."""
    repo = _open_repo(ctx)
    config = _load_cfg(ctx)

    tier = next((t for t in config.tiers if t.name == tier_name), None)
    if tier is None:
        valid = ", ".join(t.name for t in config.tiers)
        click.echo(
            f"Error: unknown tier {tier_name!r}. Configured tiers: {valid}",
            err=True,
        )
        ctx.exit(1)
        return

    now_ts = int(time.time())
    repo.upsert_broker_subscription(
        BrokerSubscription(
            venue=venue,
            tier_name=tier.name,
            free_volume_usd=tier.free_volume_usd,
            pacing=_resolve_pacing(repo, config, venue, pacing),
            subscription_usd_month=tier.subscription_usd_month,
            status=SubscriptionStatus.ACTIVE,
            attested_at=now_ts,
            attest_due_ts=now_ts + ATTESTATION_PERIOD_SEC,
        )
    )
    volume = "unlimited" if tier.free_volume_usd is None else str(tier.free_volume_usd)
    click.echo(
        f"attested {venue}: tier={tier.name} free_volume_usd={volume} "
        f"status=active due in 365 days"
    )


@subscription_group.command("set")
@click.option("--venue", default=DEFAULT_VENUE, show_default=True, help="Venue to update.")
@click.option(
    "--free-volume-usd",
    "free_volume_raw",
    required=True,
    help="Raw fee-free monthly volume in USD, e.g. 500.",
)
@click.option(
    "--pacing",
    type=click.Choice(["opportunistic", "even_daily"]),
    default=None,
    help="Pacing mode (default: keep the venue's current value).",
)
@click.pass_context
@with_disclaimer
def subscription_set(
    ctx: click.Context, venue: str, free_volume_raw: str, pacing: str | None
) -> None:
    """Set a raw allowance without naming a tier -- an escape hatch, not an attestation.

    Leaves `tier_name='unknown'`, which `show` surfaces: the record is visibly a hand-set number
    rather than a stated tier. Prefer `attest`.
    """
    repo = _open_repo(ctx)
    config = _load_cfg(ctx)

    try:
        free_volume_usd = Decimal(free_volume_raw)
    except InvalidOperation:
        click.echo(
            f"Error: --free-volume-usd must be a number, got {free_volume_raw!r}", err=True
        )
        ctx.exit(1)
        return
    if free_volume_usd < 0:
        click.echo("Error: --free-volume-usd must be non-negative", err=True)
        ctx.exit(1)
        return

    now_ts = int(time.time())
    repo.upsert_broker_subscription(
        BrokerSubscription(
            venue=venue,
            tier_name="unknown",
            free_volume_usd=free_volume_usd,
            pacing=_resolve_pacing(repo, config, venue, pacing),
            subscription_usd_month=Decimal("0"),
            status=SubscriptionStatus.ACTIVE,
            attested_at=now_ts,
            attest_due_ts=now_ts + ATTESTATION_PERIOD_SEC,
        )
    )
    click.echo(
        f"set {venue}: free_volume_usd={free_volume_usd} tier=unknown "
        f"(not an attestation -- prefer `subscription attest`)"
    )


@subscription_group.command("show")
@click.pass_context
@with_disclaimer
def subscription_show(ctx: click.Context) -> None:
    """Show every venue's subscription, with the status and cap actually in force."""
    repo = _open_repo(ctx)
    config = _load_cfg(ctx)
    records = repo.list_broker_subscriptions()

    if not records:
        click.echo(
            "no subscription attested for any venue -- rail 14 caps live BUYs at the "
            f"unsubscribed allowance {config.subscription.unsubscribed_allowance_usd}. "
            "Run `keel subscription attest --venue coinbase --tier <tier>`."
        )
        return

    now_ts = int(time.time())
    unsubscribed = config.subscription.unsubscribed_allowance_usd
    for record in records:
        allowance = record.allowance_usd(now_ts, unsubscribed)
        cap = "unlimited" if allowance is None else str(allowance)
        volume = (
            "unlimited" if record.free_volume_usd is None else str(record.free_volume_usd)
        )
        click.echo(
            f"{record.venue}: tier={record.tier_name} free_volume_usd={volume} "
            f"pacing={record.pacing} stored_status={record.status.value} "
            f"effective_status={record.effective_status(now_ts).value} "
            f"effective_cap={cap} attested_at={record.attested_at} "
            f"attest_due_ts={record.attest_due_ts}"
        )
```

Add to `keel/cli.py`'s imports:

```python
from keel_core.subscription import BrokerSubscription, SubscriptionStatus

from keel.execution.guards import DEFAULT_VENUE
```

- [ ] **Step 4: Delete the seeding path**

In `keel/cli.py`, delete the `_ensure_subscription_seeded` function (around line 170) and its call site (around line 360).

Seeding a live spend cap from `config.yaml` is the defect, not a convenience: it produced a cap nobody attested to. Its removal is what makes keel ship inert.

- [ ] **Step 5: Delete the old repository pair**

In `keel/data/repository.py`, delete `get_subscription` and `set_subscription` (the `# -- subscription (rail 14, monthly-allowance)` section, lines 348-376) along with that section header.

Removed rather than aliased: their dict return shape is exactly what is being replaced, and leaving them would leave a second way to read a live spend cap.

- [ ] **Step 6: Fix the remaining tests**

Run: `uv run pytest -q 2>&1 | tail -40`

- `tests/data/test_repository.py`: delete the `get_subscription`/`set_subscription` tests. That behaviour is now covered by `tests/data/test_repository_subscriptions.py`.
- `tests/test_cli.py`: update or remove the old `subscription show|set` tests superseded by `tests/test_cli_subscription.py`. Any test asserting seeding-on-first-use is testing deleted behaviour and should go.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli_subscription.py -v`
Expected: 11 passed

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 8: Verify the whole feature end to end**

Run:

```bash
DB=$(mktemp -u /tmp/keel-attest-check-XXXX.db)
uv run keel --db "$DB" subscription show
uv run keel --db "$DB" subscription attest --venue coinbase --tier Preferred
uv run keel --db "$DB" subscription show
rm -f "$DB"
```

Expected: the first `show` reports nothing attested and names the unsubscribed allowance; `attest` confirms `tier=Preferred free_volume_usd=10000`; the second `show` reports `effective_status=active effective_cap=10000`.

This uses the repo's real `config.yaml` for tier definitions, so it also confirms Task 4's edit to that file is consistent with what `attest` expects.

- [ ] **Step 9: Verify**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy`
Expected: all pass.

Run: `uv run pytest tests/baseline/ -v && git diff --stat tests/fixtures/baseline_backtest.json`
Expected: 3 passed; no diff output.

Run: `uv run grep -rn "get_subscription\|set_subscription\|_ensure_subscription_seeded" keel/ tests/ --include="*.py" | grep -v __pycache__ | grep -v broker_subscription`
Expected: no output — the old path is fully gone.

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "feat: keel subscription attest, and retire the seeded singleton allowance"
```

---

## Done criteria

- `uv run pytest -q`, `uv run ruff check .`, and `uv run mypy` all pass.
- `tests/fixtures/baseline_backtest.json` is byte-identical to its original generation.
- A v1 database with a hand-tuned allowance migrates to a `suspect`, `tier_name='unknown'` row preserving that number exactly, and re-running `migrate()` never overwrites it.
- A fresh database has no subscription row, and rail 14 refuses live BUYs with a message naming `subscription attest`.
- `keel subscription attest --tier Preferred` makes rail 14 cap at `10000` on the very next order, with no restart.
- `config.subscription.monthly_allowance_usd` raises a `ConfigError` naming `assumed_free_volume_usd`.
- No reference to `get_subscription`, `set_subscription`, or `_ensure_subscription_seeded` remains.
- `tests/sim/test_account.py::test_parity_with_guards_check_*` still passes, with the parity record attested explicitly in setup.

## Follow-on

The reconciliation job (parent spec §6) is deliberately absent: it consumes `Broker.get_fee_summary`, which exists but has no engine-side caller until the broker port migration (Phase B) lands. It belongs with monorepo spec §12 step 6, and it is what makes `suspect` reachable from anything other than the migration — until then, only the §4.1 backfill produces that status.

`subscription.lapse_suspected` is likewise not emitted here; nothing can detect a lapse yet, and a dead event path is worse than none.
