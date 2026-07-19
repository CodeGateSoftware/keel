# keel — Broker Port & Adapter Plugins — Design Spec

**Date:** 2026-07-19
**Status:** Design approved, not yet implemented
**Implements:** `2026-07-18-keel-monorepo-architecture-design.md` §7 and §12 step 3.
**Depends on:** `2026-07-16-keel-broker-abstraction-design.md` §2 (ports-and-adapters, capability model) — adopted and made concrete here.

---

## 1. Purpose & scope

The engine must be broker-agnostic: adding a venue should be installing a package, not editing
the engine. Users should install only the adapters they use. Today there is one adapter and its
schema leaks into the engine's own control flow.

**In scope:** the `Broker` port and its domain types, the order model, the capability model and
where it is enforced, the preview/confirm safety rule, the Coinbase adapter, a deliberately
divergent fake adapter, the conformance suite, entry-point plugin discovery, and adding `mypy`.

**Out of scope:** the `venue` column and `keel-data`/`keel-security` extraction (monorepo spec
§12 step 4); any second *real* brokerage integration.

---

## 2. Current coupling

The broker surface is small — four methods across the entire codebase:

```
broker.get_candles()   broker.get_accounts()   broker.preview_order()   broker.place_order()
```

But the coupling is deeper than a signature. `executor.py` does not pass Coinbase payloads
through; it **constructs** them:

| Site | Emits |
|---|---|
| `executor.py:410` `_order_configuration` | `{"market_market_ioc": {"quote_size": ...}}` |
| `executor.py:423` `_stop_leg_order_configuration` | `{"stop_limit_stop_limit_gtc": {...}}` |
| `executor.py:434` `_target_leg_order_configuration` | `{"limit_limit_gtc": {...}}` |
| `executor.py:416` `_initial_status` | derives order status by **string-matching the Coinbase config key** |

So Coinbase's order-type vocabulary and time-in-force semantics are engine knowledge, and one
piece of control flow reads the native schema's shape. Two further raw-`dict` leaks:
`get_accounts() -> list[dict]` (probed at `executor.py:168`) and `place_order`'s return
(`place_result.get("success")`, `executor.py:345`). Finally `agent.py:292` types the broker as
`Any` — duck typing, not a contract.

None of this is hard to fix with one adapter. All of it gets harder with each one added.

---

## 3. Package layout

```
packages/
├── keel-broker-api/        # port, domain types, capabilities, registry, conformance suite
├── keel-broker-coinbase/   # owns coinbase-advanced-py
└── keel-broker-fake/       # test-only; deliberately disagrees with Coinbase
```

`keel-broker-api` depends only on `keel-core` and stdlib. The engine depends on
`keel-broker-api` and never on an adapter.

**`coinbase-advanced-py` leaves the root dependency set.** Today every backtest run installs a
Coinbase SDK it never calls.

---

## 4. The port

```python
class Broker(Protocol):
    def capabilities(self) -> BrokerCapabilities: ...
    def get_candles(self, product_id: str, granularity: Granularity,
                    start_ts: int, end_ts: int) -> list[Candle]: ...
    def get_balances(self) -> list[Balance]: ...
    def preview_order(self, spec: OrderSpec) -> Preview: ...
    def place_order(self, spec: OrderSpec) -> PlaceResult: ...
```

**No broker-native type and no raw `dict` crosses this boundary.** `Candle`, `Side`, and
`Granularity` continue to come from `keel-core`.

Domain types replacing today's dicts:

```python
@dataclass(frozen=True)
class Balance:      currency: str; available: Decimal; total: Decimal

@dataclass(frozen=True)
class PlaceResult:  success: bool; broker_order_id: str | None; reason: str | None

@dataclass(frozen=True)
class Preview:
    product_id: str; side: Side
    est_base_size: Decimal; est_quote_size: Decimal; est_fee: Decimal
    synthetic: bool          # True => an estimate, not the broker's own quote
    detail: Mapping[str, str]
```

`Preview` earns a real type more than anything else here: it is the object the human approves at
the confirm gate (`executor.py:311`, `confirm_fn(preview)`), not merely diagnostic output.

`get_accounts` is renamed `get_balances` because it now returns balances rather than account
records — the old name described Coinbase's endpoint, not the port's meaning.

### 4.1 Deliberately not on the port

The broker-abstraction spec (`2026-07-16`, §2.33) listed `list_products()` and `fee_schedule()`
as port metadata. Both are omitted here, along with `get_spot`:

| Method | Status | Reasoning |
|---|---|---|
| `get_spot(product_id)` | Stays on the Coinbase adapter, off the port | Implemented and tested (`cb_client.py:148`), but has **zero production callers** — only `tests/data/test_cb_client.py` exercises it. §7's synthesised preview would need it, but no adapter synthesises yet. |
| `list_products()` | Not implemented | Products come from `config.yaml`'s allowlist, not from the venue. Nothing needs venue-side discovery. |
| `fee_schedule()` | Not implemented | `config.FeesConfig` currently supplies fees. See §13. |

The rule applied: **a port method with no consumer is a guess about a future caller's needs.**
Each of these is trivial to add when something actually needs it, and each would otherwise be an
unverified constraint every future adapter must satisfy. `get_spot` in particular is likely to
return in step 5 or later — that is the right time, when the synthesising adapter defines what it
actually needs.

---

## 5. Order model

A sum type, one frozen dataclass per shape. Every variant carries exactly the fields it needs:

```python
@dataclass(frozen=True)
class MarketIOCByQuote:
    kind: ClassVar[str] = "market_ioc_quote"
    product_id: str; side: Side; quote_size: Decimal

@dataclass(frozen=True)
class MarketIOCByBase:
    kind: ClassVar[str] = "market_ioc_base"
    product_id: str; side: Side; base_size: Decimal

@dataclass(frozen=True)
class LimitGTC:
    kind: ClassVar[str] = "limit_gtc"
    product_id: str; side: Side; base_size: Decimal; limit_price: Decimal

@dataclass(frozen=True)
class StopLimitGTC:
    kind: ClassVar[str] = "stop_limit_gtc"
    product_id: str; side: Side; base_size: Decimal; stop_price: Decimal; limit_price: Decimal

OrderSpec = MarketIOCByQuote | MarketIOCByBase | LimitGTC | StopLimitGTC
```

**Why a sum type rather than one flat dataclass with an `order_type` enum.** A flat shape makes
nonsense representable — a market order carrying a `limit_price`, a stop-limit with
`stop_price=None`. On the live-money path a malformed order is not a crash you notice; it can be
a *filled* order you did not intend. Making bad states unexpressible is worth more here than
anywhere else in the codebase.

**Why market orders split by sizing basis.** Coinbase accepts `quote_size` on market buys; many
venues take base size only and expect the caller to compute quantity. That is a genuine
capability difference, and splitting the variants makes it declarable and checkable instead of
invisible.

Each variant exposes an `initial_status` property. This retires `executor.py:416`
`_initial_status`, the last engine control flow keyed off Coinbase's schema shape.

**Exhaustiveness.** Python does not enforce it. Every `match` over `OrderSpec` ends with
`case _: assert_never(spec)`, and §9's conformance suite requires each adapter to handle every
variant or declare it unsupported. `mypy` (§10) makes `assert_never` a real check rather than a
comment.

---

## 6. Capabilities

```python
@dataclass(frozen=True)
class BrokerCapabilities:
    venue: str
    supported_orders: frozenset[str]        # OrderSpec.kind values
    supports_native_preview: bool
    synthesizes_preview: bool
    quote_currencies: frozenset[str]
    asset_classes: frozenset[str]
```

`supported_orders` holds `kind` strings rather than types: serialisable, loggable, and
debuggable in a way `frozenset[type]` is not.

### 6.1 Checked at rule-evaluation time, not at the executor

A rule whose execution plan the venue cannot support is skipped **before it is ever sized**,
with a structured event recording why. `strategy/engine.py::evaluate` gains a
`capabilities: BrokerCapabilities` parameter.

This does not violate monorepo spec §2.3 (`keel-strategy` is I/O-free): `BrokerCapabilities` is
a frozen dataclass passed in as data, exactly as `now_ts` already is. The engine never calls a
broker.

Rules declare their requirement:

```python
class Rule(ABC):
    required_order_kinds: ClassVar[frozenset[str]] = frozenset(
        {"market_ioc_quote", "stop_limit_gtc", "limit_gtc"}
    )
```

The default follows from `Setup`, whose `entry`/`stop`/`target` are all non-optional — any rule
producing one implies an entry, a protective stop, and a take-profit leg.

**`dca.py` overrides it to `frozenset({"market_ioc_quote"})`.** Its own comment
(`dca.py:112-116`) states the reason: "DCA is accumulation, not a risk-defined trade -- there is
no stop-loss or take-profit," and it passes sentinel `stop=0`/`target=entry` purely to satisfy
`Setup.rr`. So DCA can trade venues that cannot place stop-limits, where a breakout rule cannot.

### 6.2 No executor-side rail

An earlier draft added a fifteenth guard rail. It is unnecessary: the adapter's `place_order`
receives a typed variant and raises on an unsupported `kind`. That is already defence in depth at
the last gate before money moves, with no duplicated logic and no new rail. Guards keep their
existing fourteen and their existing no-broker-access property.

---

## 7. Preview and the confirm gate

`preview` is not diagnostic output — it is what `confirm_fn` shows the human, so a confirm gate
without one is theatre.

**In `confirm` mode the executor refuses unless the adapter reports `supports_native_preview` or
`synthesizes_preview`.** `bypass` mode is unaffected; unattended execution is already accepted
there.

An adapter lacking a native endpoint **may** opt in to synthesising a preview from spot price and
fee schedule. Such a `Preview` sets `synthetic=True`, and every renderer must surface that, so
approving an estimate never looks identical to approving a broker's own quote.

The default is refusal rather than estimation because a synthesised preview is least accurate
exactly when accuracy matters most — wide spreads, thin books, fast markets — which is precisely
when the confirm gate should be telling the truth.

---

## 8. Adapters

Both register an entry point and are discovered at runtime:

```toml
[project.entry-points."keel.brokers"]
coinbase = "keel_broker_coinbase:CoinbaseAdapter"
```

`importlib.metadata.entry_points(group="keel.brokers")` resolves them. Installing a venue is
`uv add keel-broker-<venue>` — no core change, no rebuild. Third parties can ship adapters
without touching this repository.

### 8.1 `keel-broker-coinbase`

Today's `data/cb_client.py`, rewritten against the port. All translation to
`{"market_market_ioc": {...}}` and its siblings lives here, in one module. Retains the existing
`Transport` Protocol so it stays testable against canned JSON fixtures.

Capabilities: all four order kinds, `supports_native_preview=True`.

### 8.2 `keel-broker-fake`

Test-only, and deliberately **disagreeing** with Coinbase:

| Disagreement | What it flushes out |
|---|---|
| No native preview | The §7 confirm-mode gate |
| Base-size only — omits `market_ioc_quote` | Capability refusal at rule evaluation (§6.1) |
| Models stops as separate order objects, translating `StopLimitGTC` internally | Whether the port leaks Coinbase's "a stop is an order type" assumption |
| Different granularity set and page size | Coinbase-shaped pagination assumptions in `data/history.py` |

It is a **separate distribution**, not a fixture inside the test suite, because it is the only
thing that exercises entry-point discovery with two plugins installed. A port with one adapter is
a guess; a plugin mechanism with one plugin is equally a guess.

---

## 9. Conformance suite

Shipped from `keel-broker-api` so any adapter — including third-party — can run it:

- Every `kind` in `supported_orders` round-trips through `place_order`; every `kind` **not** in it
  raises rather than silently substituting.
- `capabilities()` matches observed behaviour — a declaration cannot lie.
- `get_balances` returns `Balance` objects; no `dict` crosses the port.
- Broker-native errors surface as port-level exceptions.
- `preview_order` sets `synthetic=True` **iff** the adapter declares `synthesizes_preview`.

`keel-broker-fake` gives the suite teeth. Without a non-Coinbase implementation, the suite only
ever asserts that Coinbase matches Coinbase.

---

## 10. Type checking

`mypy` is added in this step, rolled out per-module rather than repo-wide:

```toml
[tool.mypy]
python_version = "3.12"

[[tool.mypy.overrides]]
module = ["keel_core.*", "keel_broker_api.*", "keel_broker_coinbase.*", "keel_broker_fake.*"]
strict = true

[[tool.mypy.overrides]]
module = "keel.*"
ignore_errors = true
```

New packages are strict from birth; `keel/` tightens as modules move out in steps 4–7. Enabling
strict mode across 11.4k lines at once would produce a wall of errors that stalls the actual
work — "fix 400 type errors" is not a deliverable of this step.

`mypy` over `pyright` for CI ubiquity and documentation, accepting that pyright narrows unions
better. Reversible if the `OrderSpec` union proves awkward in practice.

---

## 11. Trust boundary

Entry points execute arbitrary code. When engines run server-side (the SaaS phase), **only
first-party adapters are loaded there.** Community adapters remain a local-machine capability.
Not enforced in this step — recorded so the plugin mechanism is not mistaken for a security
boundary.

---

## 12. Sequencing

Each step leaves the system working and the monorepo spec's baseline green.

**Phase A — build and validate the port (purely additive; nothing consumes it yet).**

1. `keel-broker-api`: domain types, `OrderSpec` variants, `BrokerCapabilities`, the `Broker`
   Protocol.
2. Add `mypy` with the §10 configuration; make `keel-broker-api` pass strict.
3. `keel-broker-coinbase`: port today's `cb_client.py`, own the translation, register the entry
   point. Drop `coinbase-advanced-py` from root dependencies.
4. `keel-broker-fake` as a second distribution; verify two-plugin entry-point discovery.
5. The conformance suite; both adapters pass it.

**Phase B — migrate the engine onto the validated port (carries the behavioural risk).**

6. Retire the leaks in `executor.py`: `_order_configuration` and the two leg builders emit
   `OrderSpec` variants; `_initial_status` becomes a variant property; `get_balances`/`PlaceResult`
   replace dict probing. Type `agent.py`'s broker parameter as `Broker`.
7. Capability gating in `engine.evaluate` + `Rule.required_order_kinds` + the `dca.py` override.
8. The §7 preview/confirm gate.
9. Carried work from monorepo spec §14: item 1 (prune redundant root deps) and item 2 (normalise
   `cb_client` failure-event granularity).

**The phase boundary is load-bearing.** The fake adapter and conformance suite exist to prove the
port is not secretly Coinbase-shaped. If they landed *after* the engine was rewritten against the
port, discovering a Coinbase-ism would mean redoing the engine work — so all validation completes
before anything consumes the port.

Phase A cannot change behaviour: no existing code path calls into it. Phase B step 6 is the first
change on the live order path and warrants the closest review.

---

## 13. Open questions

- **Fee schedule.** `Preview.est_fee` needs one, and `config.FeesConfig` currently hardcodes
  Coinbase's `<$1k-30d-volume` tier. Whether `fee_schedule()` joins the port or stays
  configuration is deferred until the fake adapter forces the question in step 5.
- **Synthesised preview accuracy.** No adapter synthesises one yet. When the first does, it needs
  a stated accuracy bound and a rule for when the estimate is too stale to approve.
- **Multi-venue rule routing.** With one venue, "which venue does this rule trade?" is not a
  question. It becomes one alongside the `venue` column in step 4.
