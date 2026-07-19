# keel — Subscription Record, Attestation, and Rail 14 Derivation — Design Spec

**Date:** 2026-07-19
**Status:** Design approved, not yet implemented
**Implements:** `2026-07-19-keel-broker-subscription-design.md` §4, §4.1, §7, §8 — the storage,
attestation, and enforcement half of that spec.
**Relates to:** `2026-07-18-keel-monorepo-architecture-design.md` §8 (venue keying), §12 step 4.

---

## 1. Purpose & scope

The subscription design spec establishes *what* must be tracked per venue and *why*. This spec
settles *how* it lands in this codebase, against the code as it actually stands.

**In scope:** the `broker_subscriptions` table, versioned migration machinery, the record type
and its policy methods, rail 14 deriving its cap from the record, the `subscription attest` CLI,
and the config changes that follow.

**Out of scope:** the reconciliation job and `suspect` transitions driven by `get_fee_summary`
(monorepo spec §12 step 6); `subscription.lapse_suspected`; automatic subscription purchase.

### 1.1 Deviation from the parent spec's sequencing

The parent spec's §9 places "rail 14 derives its cap from the record" in broker-port Phase B,
reasoning that *"rail 14 changes live order gating and belongs with the other Phase B order-path
work, behind the same review scrutiny."*

**That deferral is dropped here, deliberately.** Its premise was risk to a running engine, and
the engine is not yet trading live. The cost of deferring is real: §2 of the parent spec is
explicit that the defect is not the missing table but the *underived* cap, so landing storage
without derivation would leave `subscription attest --tier Preferred` updating a record while
rail 14 kept capping at its own independently-typed-in number — the exact two-sources-of-truth
problem the spec opens with, just with better storage. It would also require a compatibility
shim written now and unwound later.

Discovering that `status='suspect'` halts buying costs nothing while offline and costs real
money to discover later.

---

## 2. What the code actually looks like today

Three findings drove the design, each verified against the tree:

1. **The subscription is not a row.** It is a JSON blob in `agent_state` under key
   `"subscription"` (`keel/data/repository.py:350-376`), carrying `monthly_allowance_usd`,
   `pacing`, and `updated_at`. §4's table is therefore both a new table and a data move.

2. **There is no migration machinery.** `keel/data/db.py:178` `migrate()` is create-only: every
   statement is `IF NOT EXISTS`, and `SCHEMA_VERSION = 1` is written once and never read to
   drive an upgrade. §4.1's migration has nothing to run on.

3. **`venue` does not exist anywhere in `keel/`.** `OrderIntent` (`guards.py:90`) has no venue
   field, and a repository-wide search returns no hits. The engine is implicitly single-venue.

A fourth finding scoped the blast radius: **`guards.check` has exactly one caller**
(`keel/execution/executor.py:273`), and `keel/sim/`, `keel/analysis/`, and `keel/strategy/`
import neither `guards` nor `executor`. Backtesting and simulation never run the rails, so a
fail-closed rail 14 cannot break them. No mode special-casing is needed.

---

## 3. The record and its policy

New module `packages/keel-core/keel_core/subscription.py`:

```python
class SubscriptionStatus(str, Enum):
    ACTIVE = "active"
    SUSPECT = "suspect"
    LAPSED = "lapsed"


@dataclass(frozen=True)
class BrokerSubscription:
    venue: str
    tier_name: str                  # 'Basic'|'Preferred'|'Premium'|'none'|'unknown'
    free_volume_usd: Decimal | None # None = unlimited (Premium)
    pacing: str
    subscription_usd_month: Decimal
    status: SubscriptionStatus
    attested_at: int
    attest_due_ts: int

    def effective_status(self, now_ts: int) -> SubscriptionStatus: ...
    def allowance_usd(
        self, now_ts: int, unsubscribed_allowance_usd: Decimal
    ) -> Decimal | None: ...
```

`effective_status` returns `SUSPECT` when `attest_due_ts <= now_ts`, regardless of stored status;
otherwise the stored status.

`allowance_usd` is §7's table as a function. `None` means unlimited.

| `effective_status` | returns |
|---|---|
| `ACTIVE` | `free_volume_usd` (`None` if unlimited) |
| `SUSPECT` | `unsubscribed_allowance_usd` |
| `LAPSED` | `unsubscribed_allowance_usd` |

The caller handles "no record at all" — there is no object on which to call a method — by
substituting `unsubscribed_allowance_usd` directly.

**Why the policy lives on the record rather than in `guards.py`:** it makes §7's table a pure
function of its inputs, testable with no database, no `OrderIntent`, and no rail around it.
Every row above becomes a direct unit test. Rail 14 keeps one call.

---

## 4. Schema and migration

### 4.1 The table

`SCHEMA_VERSION` becomes `2`. Added to `_SCHEMA_STATEMENTS` in `keel/data/db.py`:

```sql
CREATE TABLE IF NOT EXISTS broker_subscriptions (
    venue                   TEXT PRIMARY KEY,
    tier_name               TEXT NOT NULL,
    free_volume_usd         TEXT,            -- NULL = unlimited (Premium)
    pacing                  TEXT NOT NULL,
    subscription_usd_month  TEXT NOT NULL,
    status                  TEXT NOT NULL,
    attested_at             INTEGER NOT NULL,
    attest_due_ts           INTEGER NOT NULL
)
```

Decimals are exact `TEXT`, matching the `candles`/`orders` convention.

### 4.2 Migration machinery

`migrate()` gains a minimal versioned runner: a `{target_version: callable}` map executed after
the create statements, each step running only when the stored version is below it, with the
version row updated afterwards inside the same transaction.

This is deliberately not a migration framework. It is the smallest thing that makes an ordered,
once-only data change expressible, and this is the second schema change with more to come.

A fresh database is stamped at `SCHEMA_VERSION` on creation and therefore runs no migration
steps — correct, because it has nothing to migrate.

### 4.3 The §4.1 backfill (migration step 2)

Reads `agent_state['subscription']` and, if present and no `coinbase` row exists, inserts:

| Column | Value |
|---|---|
| `venue` | `'coinbase'` |
| `free_volume_usd` | the old `monthly_allowance_usd` |
| `pacing` | the old `pacing` |
| `tier_name` | `'unknown'` |
| `subscription_usd_month` | `'0'` |
| `status` | `'suspect'` |
| `attested_at` | the old `updated_at`, or the migration time if null |
| `attest_due_ts` | `attested_at` (immediately due) |

`tier_name='unknown'` and `status='suspect'` force one explicit attestation rather than guessing
which tier a hand-tuned number corresponded to. Guessing would set a live spend cap from an
inference.

The step is idempotent by construction — it skips when a row already exists — so it is safe
independently of the version machinery.

**The `agent_state` row is left in place, not deleted.** It costs nothing, and it is the only
copy of the pre-migration value if the backfill is ever found to have been wrong.

---

## 5. Rail 14

`keel/execution/guards.py` rail 14 becomes:

1. Read `repo.get_broker_subscription(DEFAULT_VENUE)`.
2. Compute the allowance: `record.allowance_usd(now_ts, config.subscription.unsubscribed_allowance_usd)`,
   or `unsubscribed_allowance_usd` directly when there is no record.
3. `None` (unlimited) → the rail passes unconditionally; pacing is meaningless without a cap.
4. Otherwise apply the existing `even_daily` pacing arithmetic and month-to-date comparison
   unchanged, with `pacing` now read from the record.

`_monthly_buy_spend_usd` is untouched.

### 5.1 Two violation messages, not one

The inert state and the over-budget state are different problems and must read differently. A
new user told *"month-to-date BUY spend 0 + 100 = 100 exceeds the allowance cap 0"* has been
given an arithmetically true and practically useless message.

- **No record, or a record degraded to `suspect`/`lapsed`:** the message names the venue, the
  reason (never attested / attestation overdue / lapsed), and the remedy — `keel subscription
  attest --venue <v> --tier <t>`.
- **`active` with a real cap:** today's message, unchanged.

### 5.2 The single-venue assumption

Named once, as `DEFAULT_VENUE = "coinbase"` in `guards.py`, with a comment pointing at monorepo
spec §8. The table is venue-keyed from birth because that costs nothing and is the right shape;
the engine stays single-venue because `OrderIntent` has no venue to key on and inventing one
before the broker port lands would be a guess. This constant is the one line Phase B deletes.

### 5.3 Events

Rail 14 emits `subscription.attestation_overdue` when it degrades a record because
`attest_due_ts` has passed.

`subscription.lapse_suspected` is **not** emitted. Nothing can detect a lapse until the
reconciliation job lands (monorepo §12 step 6); emitting it now would be a dead code path.

---

## 6. Config

`keel_core.config.SubscriptionConfig`:

- **Adds** `unsubscribed_allowance_usd: Decimal = Decimal("0")`.
- **Keeps** `pacing`, now serving as the default pacing for new attestations.
- **Removes** `monthly_allowance_usd`.

Removal is enforced, not silent: parsing a `subscription:` block containing
`monthly_allowance_usd` raises, with a message pointing at `keel subscription attest`. A number
the user set by hand that silently stops having any effect is precisely the disconnect §2 of the
parent spec exists to fix — reproducing it in the fix would be perverse.

`config.yaml`'s `subscription:` block is updated accordingly. The `tiers:` block is unchanged and
becomes the source `attest` derives from.

---

## 7. CLI

`_ensure_subscription_seeded` (`keel/cli.py:170`) is **deleted**. Seeding a live spend cap from
config is the defect, not a convenience.

**keel ships inert:** with no attested record, rail 14 caps at `unsubscribed_allowance_usd`
(default `0`) and live BUYs are refused with the §5.1 message. This is the same "silence is not
consent to spend" treatment rails 12–14 already give an unset kill-switch, a never-recorded feed
timestamp, and an unknown quote balance. Simulation and backtesting are unaffected (§2).

### 7.1 `subscription attest --venue <v> --tier <t> [--pacing <p>]`

Looks `<t>` up in `config.tiers`, rejecting an unknown name with the valid list. Derives
`free_volume_usd` and `subscription_usd_month` from that tier, sets `attested_at=now`,
`attest_due_ts=now+365d`, and `status=active`.

`--pacing` is optional. Omitted, it keeps the existing record's pacing, falling back to
`config.subscription.pacing` when there is no record yet. Re-attesting must not silently reset a
pacing choice the user made earlier.

Only an explicit attestation clears `suspect`. Detection must not be self-clearing.

### 7.2 `subscription set --venue <v> --free-volume-usd <n> [--pacing <p>]`

The raw-cap escape hatch, now venue-scoped. Sets `tier_name='unknown'` and stamps the attestation
timestamps as `attest` does, with `status=active`.

**`set` yields `active`, not `suspect`.** It is still an explicit user assertion about their own
subscription, and treating a deliberate statement as suspect would make the status meaningless.
The visible tell that it was not a real attestation is `tier_name='unknown'`, which `show`
surfaces.

### 7.3 `subscription show`

A per-venue table: venue, tier, free volume, stored status, **effective** status, attested-at,
due-at, and the effective cap rail 14 would currently apply. Effective status and effective cap
are the two things a user actually needs and neither is a stored column.

---

## 8. Repository

`keel/data/repository.py` replaces `get_subscription`/`set_subscription` with:

```python
def get_broker_subscription(self, venue: str) -> BrokerSubscription | None: ...
def upsert_broker_subscription(self, record: BrokerSubscription) -> None: ...
def list_broker_subscriptions(self) -> list[BrokerSubscription]: ...
```

The old pair is deleted rather than kept as aliases: their dict return shape is the thing being
replaced, and leaving them would leave a second way to read a live spend cap.

---

## 9. Testing

- **Policy table (§3).** Every row of `allowance_usd`, plus unlimited, plus overdue-overrides-
  stored-status. Pure functions, no database.
- **Migration (§4).** A v1 database carrying an `agent_state` subscription migrates to the
  expected row; running `migrate()` twice changes nothing; a fresh database gets no row and is
  stamped at version 2.
- **Rail 14 (§5).** No record → refused with the attest message. `active` with a cap → today's
  behaviour, including `even_daily` pacing. Unlimited → passes. `suspect`, `lapsed`, and overdue
  → refused. Both violation messages asserted distinctly.
- **CLI (§7).** `attest` derives tier fields and clears `suspect`; an unknown tier is rejected
  listing valid names; `set` leaves `tier_name='unknown'`; `show` renders effective status and
  cap.
- **Config (§6).** `monthly_allowance_usd` in a config file raises with a useful message.
- **Regression.** `tests/baseline/` stays green with `baseline_backtest.json` byte-unchanged —
  which §2's finding predicts, since backtesting never runs the rails.

---

## 10. Open questions

- **Attestation period.** 365 days is the parent spec's number and is carried unchanged. Whether
  a year is the right re-attestation interval for a value gating live spend is untested; it is
  long enough that fee-based reconciliation (§12 step 6) is doing the real detection work.
- **Per-venue `unsubscribed_allowance_usd`.** It is global config here. A user deliberately
  paying fees on one venue but not another would need it per-venue; deferred until that exists.
- **`tier_name='none'`.** The parent spec's §4 describes `'none'` with `free_volume_usd='0'` as
  the honest representation of "no subscription", but no CLI path produces it — `attest` requires
  a configured tier. Either `attest --tier none` should be accepted or the value should be
  dropped from the vocabulary; resolved when a user without Coinbase One actually runs this.
