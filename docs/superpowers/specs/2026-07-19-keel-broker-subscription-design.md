# keel — Per-Venue Subscription Tracking & Lapse Detection — Design Spec

**Date:** 2026-07-19
**Status:** Design approved, not yet implemented
**Relates to:** `2026-07-19-keel-broker-port-design.md` (resolves its §13 "Fee schedule" open question);
`2026-07-18-keel-monorepo-architecture-design.md` §8 (venue keying), §12 steps 4 and 6.

---

## 1. Purpose & scope

Each venue carries its own subscription — Coinbase One's tiers grant a monthly fee-free trading
volume in exchange for a monthly fee. The engine must track that per venue, keep it current, and
never authorise spend against an allowance the user no longer has.

**In scope:** the per-venue subscription record, deriving guards' rail 14 cap from it, the
`get_fee_summary` port method, fee-based lapse detection, the fail-closed policy, and periodic
re-attestation.

**Out of scope:** automatic subscription *purchase* or upgrade; multi-account-per-venue (one
subscription per venue is assumed); non-subscription fee-discount programmes such as Coinbase's
high-volume fee upgrade.

---

## 2. The defect this exposes

`free_volume_usd` — a tier's fee-free monthly trading volume — reaches **only** `sim/tiers.py`,
`sim/report.py`, and `cli.py`'s `simulate`. It never reaches `execution/guards.py`.

Meanwhile `Repository.set_subscription` (`repository.py:367`) persists only
`monthly_allowance_usd` and `pacing`. No tier, no free volume, no venue.

So the same concept is maintained in two disconnected places:

| Source | Value | Consumer |
|---|---|---|
| `SubscriptionConfig.monthly_allowance_usd` | default `500` | **rail 14 — gates live BUY orders** |
| `TierConfig("Basic").free_volume_usd` | `500` | `sim.tiers` — analysis only |

Identical number, no linkage. **Upgrading to Preferred leaves rail 14 capping at `500`** until
the user separately remembers `keel subscription set 10000`, and nothing detects the mismatch.
The simulator will recommend Preferred while the live engine refuses to trade like it.

**The fix is not only per-venue tracking. It is making rail 14's cap *derived* from the
subscription record rather than independently typed in.**

---

## 3. The subscription cannot be fetched

Checked against Coinbase's published API surface (July 2026). The Advanced Trade API documents 41
REST endpoints across accounts, orders, products, convert, portfolios, futures, perpetuals,
payment methods, and public data. **None exposes Coinbase One status, tier, free-volume
allowance, or remaining allowance.**

`GET /api/v3/brokerage/transaction_summary` — the closest candidate — returns `total_fees`,
`fee_tier`, `margin_rate`, `goods_and_services_tax`, `advanced_trade_only_volume`,
`advanced_trade_only_fees`, `coinbase_pro_volume`, `coinbase_pro_fees`, `total_balance`,
`volume_breakdown`, `has_cost_plus_commission`. No subscription field of any kind.

**Therefore the subscription is user-asserted.** Re-syncing is *re-attestation*, not a fetch.

But `transaction_summary` does expose what the venue actually *charged*, which is enough to
detect that an assertion has stopped being true. That is the basis of §6.

Sources: [Advanced Trade REST endpoints](https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/rest-api),
[Get Transaction Summary](https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/fees/get-transaction-summary).

---

## 4. The record

Replaces the singleton `state["subscription"]` row:

```sql
CREATE TABLE broker_subscriptions (
    venue                   TEXT PRIMARY KEY,
    tier_name               TEXT NOT NULL,   -- 'Basic'|'Preferred'|'Premium'|'none'
    free_volume_usd         TEXT,            -- NULL = unlimited (Premium)
    pacing                  TEXT NOT NULL,
    subscription_usd_month  TEXT NOT NULL,
    status                  TEXT NOT NULL,   -- 'active'|'suspect'|'lapsed'
    attested_at             INTEGER NOT NULL,
    attest_due_ts           INTEGER NOT NULL
);
```

Decimals are stored as exact `TEXT`, matching the existing `candles`/`orders` convention.

`tier_name='none'` with `free_volume_usd='0'` is the honest representation of "no subscription" —
not a missing row. A missing row means "never attested", which §7 treats as unknown and therefore
closed.

**Rail 14's cap is `free_volume_usd` from this row.** One value to change on upgrade; the
simulator and the live engine finally agree by construction.

`pacing` keeps its current meaning and moves to the record unchanged.

### 4.1 Migration

The existing singleton carries a `monthly_allowance_usd` the user has possibly tuned by hand. It
migrates to `venue='coinbase'` with that value as `free_volume_usd` and `tier_name='unknown'`,
`status='suspect'`, forcing one explicit attestation rather than silently guessing which tier the
number corresponded to. Guessing here would set a live spend cap from an inference.

---

## 5. Port addition

```python
@dataclass(frozen=True)
class FeeSummary:
    venue: str
    taker_rate: Decimal
    maker_rate: Decimal
    volume_usd: Decimal
    fees_usd: Decimal
    volume_window: str      # "trailing_30d" | "calendar_month" | "unknown"
    fetched_at: int
```

```python
class Broker(Protocol):
    def get_fee_summary(self) -> FeeSummary: ...
```

Gated by a new `BrokerCapabilities.supports_fee_summary`. An adapter that does not support it
raises; §6 then falls back to attestation alone for that venue.

This resolves the broker-port spec's §13 open question — `fee_schedule()` joins the port after
all, as `get_fee_summary`, now that it has a real consumer. It does not violate that spec's §4.1
rule ("a port method with no consumer is a guess") because reconciliation is that consumer.

**`volume_window` is explicit because Coinbase's window could not be determined from the docs.**
Requiring the adapter to declare it means the engine can never silently compare a
trailing-30-day figure against a calendar-month cap. `"unknown"` is a legal value, and §6 then
ignores `volume_usd` entirely — using only `fees_usd`, which is window-independent for the test
that matters.

### 5.1 Secondary benefit: real fee rates

`FeesConfig` currently hardcodes `taker_pct=0.012` / `maker_pct=0.006`, documented as *"the
`<$1k-30d-volume` account tier's published rate"* — a guess baked into config that every backtest
and tier simulation uses. `FeeSummary.taker_rate`/`maker_rate` are the account's actual rates.
Feeding them into the simulator is a straightforward accuracy win, tracked as an open question in
§10 rather than assumed here.

---

## 6. Lapse detection

The signal is simple: **the user claims a fee-free allowance, and the venue charged a fee
anyway.**

```
if fees_usd > 0 and month_to_date_volume < free_volume_usd:
    status = 'suspect'
```

**`month_to_date_volume` comes from the local `orders` table, not the API.** That is the same
figure rail 14 already computes (`guards._monthly_buy_spend_usd`), it is calendar-month by
construction, and it sidesteps the `volume_window` ambiguity entirely.

The API's `volume_usd` is used only as a secondary reconciliation signal, and only when
`volume_window == "calendar_month"`. A disagreement between local and venue volume is logged, not
acted on — it usually means fills the engine did not place (manual trading), which is not a
subscription problem.

Unlimited tiers (`free_volume_usd IS NULL`, Premium) still detect a lapse: any nonzero
`fees_usd` contradicts an unlimited fee-free allowance.

**Why this is worth building.** Without it, a downgrade or lapse goes undetected until the next
attestation — up to a year — while rail 14 keeps authorising spend against an allowance that no
longer exists. With it, the contradiction surfaces on the first fee-bearing trade.

---

## 7. Fail-closed policy

Staleness is asymmetric. An un-synced *upgrade* under-permits: annoying, safe. An un-synced
*downgrade or lapse* over-permits: the engine spends against an allowance the user does not have.
The design fails closed on the second.

| State | Rail 14 cap |
|---|---|
| `active` | `free_volume_usd` (unlimited if NULL) |
| `suspect` | `unsubscribed_allowance_usd` — **new config, default `0`** |
| `lapsed` | `unsubscribed_allowance_usd` |
| no row for the venue | `unsubscribed_allowance_usd` |
| `attest_due_ts` in the past | treated as `suspect` regardless of stored status |

Default `0` stops trading on that venue. A user content to pay fees can raise
`unsubscribed_allowance_usd` deliberately — the point is that continuing to spend becomes an
explicit choice rather than the consequence of a stale row.

This matches how rails 12–14 already treat unknowns: `guards.py` documents "silence is not
consent to spend" for an unset kill-switch, a never-recorded feed timestamp, and an unknown quote
balance. An unverifiable subscription is the same class of unknown.

Every transition emits a structured event (`subscription.lapse_suspected`,
`subscription.attestation_overdue`), and the TUI surfaces venue subscription status alongside
ingest lag.

---

## 8. Attestation

```
keel subscription attest --venue coinbase --tier Preferred
```

Sets `tier_name`, derives `free_volume_usd` and `subscription_usd_month` from `config.tiers`,
stamps `attested_at=now`, sets `attest_due_ts = now + 365d`, and returns status to `active`.

**Only an explicit attestation clears `suspect`.** Detection must not be self-clearing: a
subsequent fee-free month proves nothing, since it is equally consistent with simply having
traded less.

`keel subscription set` is retained for the raw cap but is now venue-scoped, and using it leaves
`tier_name='unknown'` — an escape hatch that is visibly not an attestation.

---

## 9. Sequencing

Slotted into existing plans rather than run ahead of them:

| Piece | Lands in |
|---|---|
| `FeeSummary`, `get_fee_summary`, `supports_fee_summary` | Broker-port Phase A, Task 1 |
| Coinbase `transaction_summary` implementation | Broker-port Phase A, Task 3 |
| Fake adapter declares `supports_fee_summary=False` | Broker-port Phase A, Task 4 |
| Conformance: capability matches behaviour | Broker-port Phase A, Task 5 |
| `broker_subscriptions` table, venue keying, migration | Monorepo spec §12 step 4 |
| `subscription attest` CLI | with the table |
| Rail 14 derives its cap from the record | Broker-port Phase B |
| Reconciliation job and `suspect` transitions | Monorepo spec §12 step 6 (`ingest`) |

Only the first four touch work currently in flight. Rail 14 changes live order gating and belongs
with the other Phase B order-path work, behind the same review scrutiny.

---

## 10. Open questions

- **`advanced_trade_only_volume`'s window.** Trailing-30-day or calendar-month could not be
  determined from the documentation. §6 is designed not to need the answer. Confirm against a
  live account before anything depends on it; if it proves calendar-month, a second independent
  reconciliation check becomes available for free.
- **Feeding real fee rates into the simulator.** `FeeSummary` exposes the account's actual
  taker/maker rates, which are more accurate than `FeesConfig`'s hardcoded guess. Whether the
  simulator should use live rates (accurate but non-reproducible across runs) or keep pinned
  config values (reproducible but wrong) is a real trade-off for backtest stability and is not
  settled here.
- **Multiple accounts per venue.** The record assumes one subscription per venue. A user running
  two Coinbase accounts breaks that assumption; deferred until it exists.
- **Mid-month tier changes.** An upgrade partway through a month leaves rail 14 comparing
  month-to-date spend against the new, larger allowance. Whether to prorate is unresolved;
  the conservative reading is not to, which under-permits and is therefore safe.
