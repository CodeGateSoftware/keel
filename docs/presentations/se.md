# keel — architecture, stack, and a walk through the source

**Audience:** senior software engineers. Assumes you read code for a living and do not need
Python explained. Assumes no knowledge of trading or Islamic finance.

**Duration:** ~60 minutes, of which the source walkthrough (§6) is half. A 25-minute cut is
marked in the speaker notes.

---

## 1. What this is, in one slide

keel is an **auditable compliance engine for spot crypto trading**, with a reference trading
agent built on top of it.

The interesting part is not the trading. Plenty of people have a trading bot. The interesting
part is the machinery between a strategy's opinion and an order reaching a venue — a set of
deterministic, un-overridable checks, plus a rule that **unknown means refuse**.

**Three claims worth arguing with. The rest of this talk is evidence for them.**

1. **"Fail closed" is an architectural invariant, not a feature.** Not a flag you can turn off,
   and it applies to *missing data* as strictly as to a failed check.
2. **The engine never decides what is permissible.** The ruling is an *input*, recorded by a
   human with a source and an attributed name. The code is only the enforcer. Two operators
   following different schools of thought get different behaviour from identical code.
3. **The project publishes its own negative results.** No shipped strategy is net-positive after
   real fees. That is in the README before anything else.

### What it is not

- Not a fatwa engine. It derives no religious classification from anything.
- Not a black box. Every refusal is logged with a reason.
- Not, currently, profitable. See claim 3.

---

## 2. Stack

| Layer | Choice | Why it matters here |
|---|---|---|
| Language | **Python 3.14.4** pinned (`.python-version`), `requires-python >=3.11` | CI matrix runs 3.11 **and** 3.14 — the floor and the actual dev version |
| Packaging | **uv workspace**, editable path packages | One repo, seven distributions, no publish step to develop |
| CLI | **Click** | The CLI *is* the product surface; there is no server to run |
| Persistence | **SQLite (WAL)** via a hand-rolled `Repository` | Single-writer, file-backed, trivially backed up and diffed |
| Money | **`Decimal` only** | Floats never touch money or prices |
| Plugin discovery | **entry points** under `keel.brokers` | Venue adapters are installable plugins, not imports |
| Types | **mypy strict**, `py.typed` on every package | |
| Lint | **ruff**, configured in `ruff.toml` only | Two config homes would leave silently-dead settings |
| Tests | **pytest** — 766 test functions across 41 top-level files | Plus a broker **conformance suite** every adapter must pass |

**Deliberately absent:** no ORM, no DI framework, no async runtime, no message queue, no Docker
requirement for development. The engine is a synchronous library with a CLI on top. Every one of
those absences is a decision worth challenging in Q&A.

---

## 3. Repo topology

```
keel/                      the engine (distribution: keel-trader)
├── agent.py               the cycle: poll → evaluate → exits → entries
├── strategy/              Rule ABC + 4 rule kinds + backtest       (4,033 LOC)
├── execution/             guards (the rails), executor, sizing     (3,102)
├── compliance/            admission screen, income purification      (854)
├── data/                  Repository, market feed, venue client    (2,930)
├── sim/                   portfolio sim, metrics, reporting        (3,375)
├── analysis/              indicators                               (1,308)
├── research/              deflated Sharpe, factor collinearity     (1,658)
├── commands/              CLI implementations                     (23,188)
└── web/ · mcp/            read-only viewers                        (1,688)

packages/                  the uv workspace                          (7,190)
├── keel-core/             shared domain: config, types, products, logging, secrets
├── keel-broker-api/       ⭐ THE PORT — every adapter codes against this contract
├── keel-broker-coinbase/  adapter (the only one on the live path today)
├── keel-broker-alpaca/    adapter (equities)
├── keel-broker-robinhood/ adapter (optional)
├── keel-broker-kraken/    adapter
└── keel-broker-fake/      ⭐ adapter existing ONLY to exert design pressure on the port
```

**Two things to notice before we go further.**

`commands/` is 23,188 lines — larger than the engine it drives. That is deliberate rather than
accidental: reporting, rendering and operator-facing explanation are kept out of the compute
path. Whether the split is *right* is a fair question and it is on the open-problems list.

`keel-broker-fake` is a distribution whose only purpose is to prove the port is implementable by
more than one thing, and to keep plugin discovery honest in CI. It is dev-only and **must never
be installed in production**.

---

## 4. Architecture — ports and adapters, with a hard dependency rule

```
       ┌────────────────────────────────────────────────┐
       │  keel/  (the engine)                           │
       │   agent → strategy → sizing → guards → executor│
       └─────────────────────────────────────┬──────────┘
                                             │ depends on
                                             ▼
                            ┌────────────────────────────┐
                            │ keel-broker-api  (THE PORT)│
                            │  Protocol Broker           │
                            │  BrokerCapabilities        │
                            │  order sum type            │
                            │  registry (entry points)   │
                            └────────────┬───────────────┘
                                         │ implemented by
              ┌──────────┬───────────────┼───────────────┬──────────┐
              ▼          ▼               ▼               ▼          ▼
          coinbase    alpaca         robinhood        kraken      fake
```

**The rule: adapters depend on the port; the engine depends on the port; nothing depends on an
adapter.** Adapters are discovered at runtime:

```python
# packages/keel-broker-api/keel_broker_api/registry.py
def discover_brokers() -> dict[str, Any]:
    """Map venue name to the adapter class registered under `keel.brokers`."""
```

```toml
# packages/keel-broker-coinbase/pyproject.toml
[project.entry-points."keel.brokers"]
coinbase = "keel_broker_coinbase:CoinbaseAdapter"
```

**Honest caveat — the migration is unfinished.** `keel/commands/_common.py` still constructs
`CoinbaseClient` directly, so the live path cannot yet reach a non-Coinbase adapter. The port,
the registry and four adapters exist and are conformance-tested in CI; the engine has not
finished being rewired. This is the largest piece of outstanding architectural work, and it is
why `keel-broker-robinhood` is a dev dependency rather than a runtime one.

### The port's shape

`Broker` is a **`Protocol`, not an ABC** — adapters are structurally typed and carry no runtime
import of the port.

```python
class Broker(Protocol):
    def capabilities(self) -> BrokerCapabilities: ...
    def market_clock(self) -> SessionState: ...
    def market_schedule(self) -> MarketSchedule: ...
    def get_candles(...) -> ...
    def get_balances(self) -> list[Balance]: ...
    def preview_order(self, spec: OrderSpec) -> Preview: ...
    def place_order(self, spec: OrderSpec, *, idempotency_key: str | None = None) -> PlaceResult: ...
    def get_fee_summary(self) -> FeeSummary: ...
    def get_order(self, order_id: str) -> OrderStatus: ...
    def cancel_order(self, order_id: str) -> CancelOutcome: ...
```

**`BrokerCapabilities` is the interesting piece.** Rather than assuming every venue does
everything, an adapter *declares* what it supports and the engine adapts. A venue that cannot
preview an order says so, and confirm-mode changes shape rather than crashing.

**Orders are a sum type, not one dataclass with an `order_type` enum:**

```python
# packages/keel-broker-api/keel_broker_api/orders.py
@dataclass(frozen=True)
class MarketIOCByQuote: ...
@dataclass(frozen=True)
class MarketIOCByBase: ...
```

The module docstring gives the reason: a flat shape makes invalid combinations representable.
Make-illegal-states-unrepresentable, applied to order construction.

---

## 5. The three invariants

### 5.1 Fail closed — unknown is a rejection, not a default pass

The one to internalise. It recurs at every layer.

```python
# keel/compliance/screen.py
"""
Absent attestation FAILS CLOSED. An unattested asset is not "probably fine" — it is unknown,
and unknown is a rejection.
"""
```

| Situation | Naive behaviour | keel's behaviour |
|---|---|---|
| No shariah classification recorded | assume permissible | **reject the asset** |
| Broker balance unreadable (`None`) | retry, or assume funded | **veto the BUY** |
| Kill-switch state never written | assume not engaged | **treat as engaged** |
| Market-data timestamp never recorded | assume fresh | **treat as stale** |
| Venue listing type unattested | infer from the product id | **reject** — a CFD can spell itself `BTC-USD` |

> "Silence is not consent to spend." — `guards.py`

### 5.2 The ruling is data; the code is only the enforcer

`compliance/screen.py` splits the world by **what is knowable**:

- **Market facts are COMPUTED** — history depth, liquidity, settlement currency. Recomputed
  freely from cached candles. No judgement.
- **Shariah classifications are ATTESTED, never inferred** — sector, backing (`'ayn` / `dayn` /
  `native`), yield. Questions of fact-plus-scholarship about the world. The module cannot derive
  them from candles and does not pretend to.

An attestation carries a **source** and an **attributed human name**. Provenance as a
first-class data requirement is what makes the engine auditable rather than merely configurable.

### 5.3 Decimal only

```python
# keel/execution/sizing.py
def size(equity: Decimal, risk_pct: Decimal, entry: Decimal, stop: Decimal) -> Decimal:
    """qty = (equity * risk_pct) / abs(entry - stop)"""
```

Note the second-order consequence, from `compliance/purification.py`: interest credits left in
the balance would inflate the equity base this formula reads — **riba compounding into position
size**. Segregating them is a correctness fix as much as a religious one. A domain constraint
and an engineering constraint turning out to be the same constraint.

---

## 6. Walkthrough — one order's journey

**The core of the talk.** We follow a single hypothetical BUY from bar to venue. Seven files, in
order.

### 6.1 `keel/agent.py::run_once` — the cycle

```
(kill-switch / market-session gates) → poll → evaluate → exits → entries
```

Read the docstring before the code. Two details worth pausing on:

- **The kill-switch is checked before any network call** — `repo.get_state("kill_switch",
  default=True)`. Note the default.
- **Reconciliation runs FIRST**, before equity is computed and before any entry. A bracket that
  filled since the last cycle must be resolved before anything reads position state.

### 6.2 `keel/data/market_feed.py::poll_once` — getting bars

Candles cached in SQLite; only the delta is fetched. The engine is offline-first, which is what
lets the backtest and the live path share code.

### 6.3 `keel/strategy/rules/*.py` — the strategy layer

`Rule` is an ABC with a deliberately tiny surface:

```python
class Rule(ABC):
    def detect(self, candles_by_tf: dict[Granularity, list[Candle]]) -> Setup | None: ...
    def exit_signal(self, held, candles_by_tf) -> bool: ...
    def describe(self) -> dict: ...
```

Four registered kinds — `turtle_breakout`, `dca`, `pullback_continuation`, `rsi_meanrev`. The
registry is **closed**: `agent.py` maps exactly these to classes and raises `ValueError` on
anything else. Adding a rule kind is a reviewed change to the trading core, by design.

`detect()` returns a `Setup` (entry, stop, target) or `None`. **It has no access to the account,
the balance, or the venue.** A rule cannot know how much money exists and therefore cannot size
a position. That separation is what makes rules testable as pure functions over candles.

### 6.4 `keel/execution/sizing.py` — Setup to quantity

Two regimes, and the difference is instructive:

- `size()` — fixed-fractional risk, sized off the **stop distance**. For rules with a defined
  invalidation price.
- `dca_size()` — a fixed budget converted to quantity at price, for the DCA rule which has **no
  stop by design**.

`size()` raises `ValueError` on a zero stop distance rather than returning infinity.

### 6.5 `keel/execution/guards.py::check` — the rails ⭐

**If you read one file, read this one.**

```python
def check(intent: OrderIntent, repo: Repository, config: Config,
          now_ts: int, offline: bool = False) -> GuardResult:
    """Run all eighteen §14 hard rails against `intent`. Never short-circuits."""
```

Four design decisions worth discussing:

1. **A pure checker with no broker access, by design.** It cannot fetch a balance; the executor
   fetches it live and hands it in via `OrderIntent.available_quote`. Consequence: the entire
   rail suite is testable with no network, no mock venue, no fixture server.
2. **It never short-circuits.** All rails run, all failures collected. A bad order yields every
   reason it was bad, not the first.
3. **`offline=True` skips exactly two rails**, named explicitly: `LIVE_STATE_RAILS =
   ("usdc_funding", "withdrawal_capability")`. Every other rail still runs in paper mode,
   because the promotion gate is scored on the paper record — a rehearsal that skipped rails
   would promote a strategy on evidence live trading would have vetoed.
4. **Un-overridable.** No bypass parameter, no admin flag, no `force=True`.

| Rail | Enforces |
|---|---|
| 1 | Allowlist — mechanical, per-trade, every intent |
| 9 | No stop-loss widening — a stop may tighten, never loosen |
| 11 | Drawdown circuit breaker |
| 12 | Kill switch — unset treated as engaged |
| 13 | Funding source — a BUY may only spend settled balance, never a linked bank/ACH source |
| 16 | Consecutive-loss circuit breaker |
| 17 | **`qabd` / possession** — classical fiqh as an executable check |
| 18/19 | Settlement currency and spot-instrument shape — no perps, futures or CFDs |

**Rail 17 is the one to dwell on.** Constructive possession — "the ability to dispose, not
physical custody" — is tested by asking whether the asset can be withdrawn from the venue. If it
cannot, you do not effectively possess it, so acquiring more is stopped. The attestation expires
after 7 days (`WITHDRAWAL_ATTESTATION_TTL_SEC`) because "a stale attestation is no better than
none". A classical contract rule, expressed as a TTL.

### 6.6 `keel/execution/executor.py` — placing it

The same pipeline for every order shape: **guards → preview → place → log**.

- Entries are **market orders** (`order_type="market"`, `limit_price=None`). This matters
  enormously for backtest fidelity — see §7.
- Protective orders go out as an exchange-side **bracket** (take-profit limit + stop trigger).
  The exchange owns it, so an agent that dies does not leave a naked position.
- `scale_out()` exists for partial exits and runs the full guard pipeline — **but no shipped
  rule calls it.** Plumbing without a caller; say so if asked.

### 6.7 `keel/data/repository.py` — persistence

Plain SQLite, plain SQL, hand-written mapping, no ORM. Every order, veto and state transition is
written. The audit trail is not bolted on — it is the same rows the engine reads to make its
next decision.

---

## 7. The research side, and why it is separate

`sim/` and `strategy/backtest.py` answer "does this rule have an edge?", deliberately apart from
the live path.

**The decision with the largest blast radius:** `backtest()` fills an entry at the **next bar's
open plus slippage**, because that is what the executor places live. It does *not* rest an order
and wait for a level. This makes the backtest honest and makes several classical strategies
unimplementable as written — `pullback_continuation` demands follow-through above a level, and a
market fill takes the trades it meant to decline.

Cost modelling is `floor × sqrt(anchor / median_daily_quote_volume)` clamped to [5bp, 50bp] — a
square-root market-impact model, with every parameter printed beside every result table so a
number's assumptions can be recovered.

**The result, plainly:** no shipped rule family is net-positive at the ~1.2% taker fee this venue
charges. Round-trip friction is ~2.5% of notional — the same order of magnitude as the per-trade
edge of everything measured. Cost is the binding constraint, not signal quality.

---

## 8. Testing

- **766 test functions across 41 top-level files**, plus package-level suites; parametrisation
  expands the executed count well beyond that.
- **A broker conformance suite every adapter must pass**, including the fake. That is what makes
  the port a contract rather than a naming convention.
- **Rails are tested for behaviour under *missing* input**, not only wrong input — fail-closed is
  the property under test.

**The cautionary tale, from the repo's own record:** two engine defects produced plausible,
internally consistent, wrong output for the life of the project while 2,712 tests passed.
Plausible output is not evidence of a working engine. Ask what your numbers *cannot* distinguish.

---

## 9. Open problems — bring your opinions

1. **The broker-port migration is unfinished.** `_common.py` still builds `CoinbaseClient`
   directly. Safest sequencing to finish it without a flag day?
2. **`commands/` is 23,188 lines** — bigger than the engine. Healthy separation, or a symptom?
3. **The slippage cap flattens a whole cohort.** The model degenerates to a flat fee for every
   asset under $5M/day median volume — most of the realistic universe.
4. **Partial exits are plumbed but unused.** `scale_out()` has no caller.
5. **`Rule` cannot express a conditional entry**, so any strategy needing a resting order is
   mis-modelled by construction.
6. **SQLite single-writer** — where does that stop being enough?

---

## 10. Questions I would ask if I were you

- **Why not async?** One account, one venue, one cycle per interval. Concurrency buys nothing and
  costs testability.
- **Why no ORM?** The audit trail is the schema. Hand-written SQL keeps it inspectable.
- **Why ship a fake broker?** To make the port's second implementation exist before the second
  real venue does.
- **Why `Protocol` rather than ABC?** Adapters carry no runtime import of the port.
- **Won't "fail closed" block everything?** Yes, frequently. That is intended — the engine
  currently refuses to trade at all.
