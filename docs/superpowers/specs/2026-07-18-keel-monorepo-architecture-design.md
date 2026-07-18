# keel — Monorepo Architecture — Design Spec

**Date:** 2026-07-18
**Status:** Design approved, not yet implemented
**Supersedes:** nothing. **Depends on:** `2026-07-16-keel-broker-abstraction-design.md` (§2 broker port and capability model are adopted wholesale here and given a package home).

---

## 1. Purpose & scope

Today `keel` is a single Python package (~11.4k LOC) built and installed as one unit. The
roadmap needs several independently buildable, independently deployable apps — engine, data
ingest, simulation, an LLM service, a Textual TUI, and later web and mobile clients — plus
broker adapters that users install à la carte so no one pays for code they do not run.

**In scope:** the workspace layout, the package/app boundaries, the dependency rules and their
CI enforcement, the client seam that permits a later local→remote transition, the broker plugin
mechanism, the venue-aware data model, and the observability model across processes.

**Out of scope (deliberately deferred):** the HTTP API wire format, authentication and tenancy,
the web and mobile apps, and any hosted infrastructure. This spec's job is to ensure none of
those are *foreclosed*, not to build them.

**Non-goal:** microservices. Most of what follows is about separating *build and deploy units*,
not about introducing network hops. See §3.

---

## 2. Principles

1. **Libraries never import apps. Apps never import each other.** All sharing goes through
   `packages/`.
2. **A network boundary only where a process boundary genuinely exists** — different machine,
   different lifecycle, or different language.
3. **`keel-strategy` is I/O-free.** No database, no HTTP, no clock, no filesystem. Candles in,
   signals out.
4. **Build what the local system needs; do not foreclose the remote one.** Where a seam is cheap
   now and expensive later, build the seam now and the simplest implementation behind it.
5. **Refuse rather than degrade.** When a broker lacks a capability or data is stale, stop. Never
   silently substitute a different behaviour on the live-money path.
6. **`Repository` is the only SQL-aware module.**

---

## 3. Why separate builds, not separate services

"Separately deployable" and "separate service" are distinct, and conflating them is the main
failure mode this spec guards against.

Simulation and the engine share `strategy/rules/*`, `strategy/engine.py`, and `analysis/*`. A
backtest *is* the live strategy code run over historical candles — if the two diverged, backtests
would no longer predict live behaviour. So sim must **import** the strategy library, not call the
engine over a network. Serialising candles over HTTP to ask "what would this rule say?" would mean
thousands of round-trips per backtest, coupled to a running server, for no benefit.

Each app therefore gets its own build artifact, container, and release cadence, while talking to
shared code in-process.

Genuine network boundaries in the target state: **TUI ↔ engine** (different machines once the
engine is hosted) and **engine ↔ LLM service** (different scaling and failure characteristics; an
LLM stall must never block the trade loop). That is all. `sim` is a batch job invoked by CLI, not
a server — it is embarrassingly parallel with no session state, and a server wrapper would add ops
burden until there is a "run backtest" button, at which point the need is a job queue rather than
an HTTP service.

---

## 4. Layout

```
keel/
├── pyproject.toml              # workspace root, no code
├── packages/
│   ├── keel-core/              # types, config, telemetry, logging
│   ├── keel-strategy/          # rules, engine, indicators, analysis  (I/O-free)
│   ├── keel-data/              # db, repository, market_feed, history, csv_import
│   ├── keel-broker-api/        # Broker port, domain types, capabilities, registry, conformance suite
│   ├── keel-broker-coinbase/   # Coinbase adapter  (owns coinbase-advanced-py)
│   ├── keel-security/          # secrets, authz
│   └── keel-client/            # EngineClient protocol + Local/Http implementations
├── apps/
│   ├── engine/                 # agent loop, execution, guards, sizing
│   ├── ingest/                 # scheduled polling, history backfill, CSV import
│   ├── sim/                    # backtest, portfolio sim, paper, metrics, reports
│   ├── cli/                    # `keel` command — thin dispatcher
│   └── tui/                    # Textual client            (new)
├── docs/
└── tests/                      # cross-cutting only; unit tests live with their package
```

### 4.1 Packages

| Package | Contents (from today's tree) | Depends on |
|---|---|---|
| `keel-core` | `types.py`, `config.py`, `logging_setup.py`, new `telemetry.py` | — |
| `keel-strategy` | `strategy/{engine,rules/*,indicators_cts,stats}.py`, `analysis/*`, promotion **predicates** | core |
| `keel-data` | `data/{db,repository,market_feed,history,csv_import}.py` | core, broker-api |
| `keel-broker-api` | `Broker` protocol, `BrokerCapabilities`, order/balance domain types, registry, conformance tests | core |
| `keel-broker-coinbase` | today's `data/cb_client.py`, rewritten against the port | core, broker-api |
| `keel-security` | `security/{secrets,authz}.py` | core |
| `keel-client` | `EngineClient` protocol, `LocalEngineClient`, later `HttpEngineClient` | core |

### 4.2 Apps

| App | Contents | Runtime shape |
|---|---|---|
| `engine` | `agent.py`, `execution/{executor,guards,sizing}.py` | long-running loop |
| `ingest` | drives `market_feed.poll_once`, `history.ensure_history`, `csv_import` | scheduled job / worker |
| `sim` | `sim/*`, `strategy/backtest.py`, `strategy/paper.py`, promotion **transitions** | batch, CLI-invoked |
| `cli` | today's `cli.py`, dispatching into the others | user-invoked |
| `tui` | Textual UI, depends **only** on `keel-client` | user-invoked |

### 4.3 Resolved placements

- **`promotion.py` splits.** It is imported by `cli.py:76` and `sim/report.py:67`, never by
  `agent.py` or `execution/` — so it is not live-path. But it writes through `Repository`, which
  would violate §2.3. Pure predicates (`can_promote`, `should_demote`, `promotion_class_of`) move
  to `keel-strategy`; the write-through `transition()` moves to `apps/sim`.
- **`paper.py`** → `apps/sim`. It returns a `BacktestResult` and performs no execution.
- **`halal_cb/`** is deleted. It is a leftover from the rename and contains only `__pycache__`.

### 4.4 The three strategy→data imports

`keel/strategy` imports `keel/data` in exactly three places, all of which must go for §2.3 to hold:

| Site | Resolution |
|---|---|
| `promotion.py:30` | `transition()` moves to `apps/sim` (§4.3); predicates keep no repo access |
| `paper.py:30` | whole module moves to `apps/sim` (§4.3) |
| `engine.py:49` | **newly identified.** A `TYPE_CHECKING` import of `Repository` for annotations. Replace with a narrow local `Protocol` declaring only the methods the engine actually calls. |

The third is not merely a lint concern. Annotating against the concrete `Repository` couples the
engine to the full persistence surface, so the type is wider than the need. The codebase already
uses the correct pattern — `data/history.py:54` defines a local `_Repo(Protocol)` with exactly the
two methods it needs, and `data/cb_client.py:45` does the same with `Transport`. Applying it in
`engine.py` follows existing convention rather than introducing a new one.

`import-linter` must be configured to include `TYPE_CHECKING` imports, or this class of coupling
passes CI unnoticed.

---

## 5. Dependency enforcement

Boundaries that are not enforced erode. `keel-strategy` staying I/O-free is the invariant that
makes backtest/live parity structural rather than aspirational, and it is precisely the one that
decays first under deadline pressure.

Add `import-linter` to CI with, at minimum:

```toml
[tool.importlinter]
root_packages = ["keel_core", "keel_strategy", "keel_data", "keel_broker_api", "keel_security"]

[[tool.importlinter.contracts]]
name = "Strategy and data are independent"
type = "independence"
modules = ["keel_strategy", "keel_data"]

[[tool.importlinter.contracts]]
name = "Strategy is I/O-free"
type = "forbidden"
source_modules = ["keel_strategy"]
forbidden_modules = ["keel_data", "sqlite3", "requests", "httpx", "time", "datetime"]

[[tool.importlinter.contracts]]
name = "TUI reaches the engine only through the client"
type = "forbidden"
source_modules = ["keel_tui"]
forbidden_modules = ["keel_data", "keel_engine", "keel_broker_coinbase"]
```

`time` and `datetime` are forbidden in strategy deliberately: every rule already receives
`now_ts` explicitly, and keeping it that way is what makes backtests deterministic.

---

## 6. The client seam

The TUI ships to end users via a package manager and connects to an engine that is local today
and hosted later. If TUI screens read SQLite directly, that transition is a rewrite — each screen
grows its own data access, and there is no single place to change.

`packages/keel-client` defines one protocol:

```python
class EngineClient(Protocol):
    def status(self) -> EngineStatus: ...
    def positions(self) -> list[Position]: ...
    def health(self) -> list[AppHealth]: ...
    def arm_bypass(self, passphrase: str) -> None: ...
    def stream_events(self, since: int) -> Iterator[Event]: ...
```

- **`LocalEngineClient`** — today. Reads the shared SQLite database and calls the engine in-process.
- **`HttpEngineClient`** — later. Same protocol, carries an auth token.

**The TUI imports `keel-client` and nothing else.** Cost now is a protocol definition and a thin
local implementation. This protocol is also the eventual REST/gRPC surface, which web and mobile
inherit for free.

This is an abstraction earned by a known requirement, not a speculative one — the remote engine
is a stated goal, which is exactly the condition under which building the seam early is correct.

---

## 7. Broker plugins

Adopts §2 of the broker-abstraction spec. This section gives it a package home and a lean-install
mechanism.

### 7.1 Current coupling

The SDK import is already confined to `cli.py:186` (factory) and `cb_client.py:10` (under
`TYPE_CHECKING`), and a `Transport` Protocol exists — so the engine hard-imports nothing from
Coinbase. Two real leaks remain:

1. `agent.py:292` takes `broker: Any` — duck typing, not a contract.
2. Coinbase's native schema crosses the interface: `preview_order(..., order_configuration: dict)`
   and `get_accounts() -> list[dict]`. A second adapter would have to imitate Coinbase's order
   shape or the signature breaks.

Both are cheap to fix while there is exactly one adapter, and get progressively harder.

### 7.2 Port and anti-corruption

`keel-broker-api` owns the `Broker` protocol and the domain types crossing it — `OrderSpec`,
`Fill`, `Balance`, `ProductInfo`, `FeeSchedule`, `BrokerCapabilities`. **No broker-native type or
raw `dict` crosses the port.** Each adapter translates at its own edge; `agent.py` and
`executor.py` are typed against `Broker`, never `Any`.

### 7.3 Discovery and lean installs

Each adapter is a separate distribution registering an entry point:

```toml
[project.entry-points."keel.brokers"]
coinbase = "keel_broker_coinbase:CoinbaseAdapter"
```

The engine resolves adapters at runtime via
`importlib.metadata.entry_points(group="keel.brokers")`. Installing a broker is
`uv add keel-broker-kraken` — no core change, no rebuild. Third parties can ship adapters without
touching this repository.

**Immediate win:** `coinbase-advanced-py` is currently a top-level dependency, so every backtest
run installs a Coinbase SDK it never calls. Moving it behind the adapter removes it from `sim`,
`tui`, and `keel-strategy` entirely.

### 7.4 Capabilities: refuse, do not degrade

Adapters declare `capabilities()`. `guards.check` consults it **before** constructing an order,
and an unsupported capability is a **refusal**. Substituting a market order because a broker lacks
limit orders is the class of bug that costs real money.

### 7.5 Conformance suite

`keel-broker-api` ships an executable contract every adapter must pass: declared capabilities
match observed behaviour, order round-trips against the broker's sandbox, error mapping, and
pagination and rate-limit handling. With one adapter, correctness is self-evident; with five, a
shared suite is the only thing keeping them honest, and it is what makes a third-party adapter
trustworthy enough to run.

### 7.6 Trust boundary (SaaS phase)

Entry points execute arbitrary code. When engines run server-side, **only first-party adapters are
loaded there.** Community adapters remain a local-machine capability.

---

## 8. Data model

### 8.1 Venue is part of the key

BTC-USD on Coinbase and BTC-USD on Kraken are different data — different prices, spreads, fees,
and liquidity. `venue` therefore joins the primary key of `candles`, `orders`, `fills`, and
`positions`. This is not optional and is independent of physical storage layout.

### 8.2 One database, not one per broker

Separate database files per broker were considered and rejected:

- The portfolio spans brokers; separate files make a single P&L a cross-file join.
- Sim's most valuable question is "same strategy, which venue?" — trivial with a column, painful
  across files.
- One schema and one migration path, rather than N copies that drift.

The isolation instinct is correct but aimed at the wrong axis: **separate databases are for tenant
isolation in the SaaS phase, not venue isolation.** Spending the complexity on venue now would not
remove the later need for it on tenants. Because `Repository` is the only SQL-aware module (§2.6),
per-tenant sharding remains a configuration change.

### 8.3 Storage evolution

SQLite stays for the local phase. Two processes now write it — `ingest` writes candles, `engine`
writes orders and state — which requires WAL mode and a busy timeout. This is the first thing that
breaks when the engine moves to a server, and candles (append-only time series, heavy range scans)
are the first data to outgrow SQLite. Confining SQL to `Repository` keeps the eventual
Postgres/TimescaleDB move to one package.

**Migration required:** existing `candles`, `orders`, and `transactions` rows predate `venue` and
must be backfilled as `coinbase` during the schema change.

---

## 9. Ingest extraction

Today `run_once()` calls `market_feed.poll_once` inline (`agent.py:313`), and
`history.ensure_history` is reachable only from the CLI (`cli.py:984`). Ingestion is thus half
inside the trade loop and half a manual command. `apps/ingest` takes both. It touches no strategy
code, making it the cleanest boundary in the codebase and the right first extraction.

**Consequence — the failure mode inverts.** Once the engine no longer fetches, it cannot recover
from a data gap on its own. The `is_fresh` guard at `agent.py:338` becomes the only thing between
a stalled ingest job and trading on stale candles. It is already written correctly (it skips the
affected product rather than aborting the cycle), so the code change is small — but a fetch
failure changes from loud and inline to silent staleness in another process.

Therefore ingest-lag alerting and per-product last-successful-poll in the TUI are **part of this
extraction, not a follow-up.**

Upside: engine, sim, and TUI read one candle store owned by one writer — a single source of truth
for history, which is what backtest/live parity requires.

---

## 10. Observability

`logging_setup.py` today attaches one rotating file handler with a plaintext format, at `ERROR`
unless `--verbose`. Correct for one process; it breaks at five — separate files, no correlation,
and a format nothing can aggregate.

### 10.1 Health is a staleness check

An `app_health` table: `app`, `instance`, `venue`, `last_beat_ts`, `status`, `last_error_ts`,
`last_error_msg`, `meta`. Every app upserts a heartbeat each cycle — engine per loop, ingest per
poll, sim at start and end.

Health is then `now - last_beat_ts > k × expected_interval → unhealthy`.

This inversion is the core of the design. Error aggregation only reports on apps healthy enough to
report; a hung or crashed ingest job emits nothing, and silent staleness is precisely the
dangerous failure mode here because the engine keeps trading on old candles. **Absence of signal
is the signal.** These are the semantics of Prometheus `up{}` plus alerting rules, on a smaller
substrate — so the model composes upward rather than being thrown away.

The TUI is the dashboard, via `EngineClient.health()`.

### 10.2 Structured logging is the retrofit-expensive part

`keel_core.telemetry` replaces the plaintext formatter with JSON lines carrying stable fields:
`ts, level, app, instance, event, venue, product, rule, cycle_id`. `event` is a stable identifier,
not an interpolated sentence — f-string messages cannot be grouped or queried, and fixing that
later means rewriting every call site.

A `cycle_id` is generated per engine loop and propagated into every event it emits. Once engine,
ingest, and LLM are separate processes, that is already the trace ID.

Records at `ERROR` and above are additionally written to a bounded, rolling `events` table so the
TUI can surface them without reading log files.

### 10.3 Telemetry is not the audit trail

Observability data is lossy, sampled, and rotates. Trades, orders, and state transitions require
complete durable records in their own tables. The `events` table is for operations; the
orders/fills tables are the record of truth. Conflating them is very hard to undo.

### 10.4 Signals defined now

Ingest lag per `(venue, product, granularity)` — the most important one — plus engine
last-cycle-ts and consecutive-error count, broker auth failures and rate-limit headroom, and sim
last-run status.

### 10.5 Later

OpenTelemetry as the instrumentation API, Sentry for error aggregation (grouping and deduplication
are genuinely hard; buy that), and Grafana Cloud or Better Stack for metrics and logs. Because
events are already structured with stable names, this is an exporter change rather than a code
change.

---

## 11. Tooling

**`uv` workspaces.** The project already uses `uv` and `uv_build`; workspaces are native, give one
lockfile with consistent resolution, and support `uv run --package <app>` and per-app builds. No
additional task runner is warranted.

Root:

```toml
[tool.uv.workspace]
members = ["packages/*", "apps/*"]
```

Member:

```toml
[project]
name = "keel-engine"
dependencies = ["keel-core", "keel-strategy", "keel-data", "keel-broker-api"]

[tool.uv.sources]
keel-core = { workspace = true }
keel-strategy = { workspace = true }
keel-data = { workspace = true }
keel-broker-api = { workspace = true }
```

**Turborepo and Nx were considered and rejected for now.** Both are aimed at JavaScript build
graphs; neither understands `uv` resolution, and both would sit as a second orchestration layer
over a Python workspace that does not need one.

**When web and mobile arrive:** add a `pnpm` workspace under `apps/` alongside the Python one — the
two coexist without interference. Introduce a cross-language task runner (Turborepo, or `just`)
only when the build graph genuinely hurts, which is a decision better made against a real graph
than predicted now.

**CI:** per-package test jobs keyed on changed paths, plus `import-linter`, `ruff`, and the broker
conformance suite as gates.

---

## 12. Sequencing

Ordered by risk retired per unit of effort. Each step leaves the system working.

1. Delete `halal_cb/`. Create the workspace skeleton with the existing code as a single member.
2. Extract `keel-core`. Add `telemetry.py`; convert logging to JSON lines with stable event names
   and introduce `cycle_id`. **Do this before the app split** — it touches every file, and doing it
   while files are moving would be needlessly painful.
3. Extract `keel-broker-api` and `keel-broker-coinbase`. Fix the `broker: Any` and
   `order_configuration: dict` leaks. Write the conformance suite. Drop `coinbase-advanced-py` from
   the root dependencies.
4. Extract `keel-data` and `keel-security`. Add the `venue` column and the backfill migration; set
   WAL mode.
5. Extract `keel-strategy`: split `promotion.py`, move `paper.py` to `apps/sim`, and replace
   `engine.py`'s `Repository` annotation with a narrow Protocol (§4.4). Add the `import-linter`
   contracts.
6. Split `apps/ingest` out of the engine loop, with the `app_health` table, heartbeats, and
   ingest-lag alerting.
7. Split `apps/engine` and `apps/sim`; make `apps/cli` a thin dispatcher.
8. Add `keel-client` with `LocalEngineClient`, then `apps/tui` against it.
9. `apps/llm`, per §5 of the broker-abstraction spec.

Steps 1–5 are refactors under existing tests with no behaviour change and should be verifiable by
the current suite plus backtest-output equality against a pinned baseline. Step 6 is the first with
real behavioural risk and deserves its own plan.

---

## 13. Open questions

- **Baseline equality harness.** Steps 1–7 must not change strategy output. A pinned
  backtest-output fixture compared before and after each step is the cheapest guard, but it does
  not yet exist and should be built as part of step 1.
- **`apps/cli` versus `apps/tui` convergence.** The CLI is today's entry point and the TUI is
  intended to become it. Whether the CLI survives long-term as a scripting surface or is absorbed
  is deferred until the TUI exists.
- **Sim invocation from the web app.** Noted in §3 as a job queue rather than an HTTP service;
  the queue technology is out of scope until the web app is specified.
