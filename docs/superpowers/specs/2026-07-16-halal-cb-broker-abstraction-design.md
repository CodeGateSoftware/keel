# halal-cb — Broker Abstraction, LLM Roles & Productization Principles — Design Spec

**Date:** 2026-07-16
**Status:** ⏳ **FUTURE ENHANCEMENT — design only; implement later.**
- The **broker-abstraction refactor** is deferred until after the core build (Phases 1–4); doing it now would
  churn the in-flight work.
- The **"Role of LLMs" asymmetry principle (§5)** is a **governing principle effective now** — it constrains
  the insights module, the event-blackout filter, and any future LLM use in *all* phases.
**Relates to:** main spec `2026-07-15-halal-cb-autotrade-design.md` (esp. §5 architecture, §6.4 no-oracle, §14
security). **Author:** Elmehdi Aitbrahim (with Claude).

---

## 1. Purpose & scope

Make the agent's broker access **pluggable** (Coinbase by default; any broker with an API swappable in), and
record the **governing principles** that keep the door open to a future multi-broker / multi-language / SaaS
product — **without building the SaaS** (that is gated on legal/licensing/broker-ToS realities, §7).

**In scope (design):** a `Broker` port + adapter pattern, a capability model, a **pluggable compliance policy**
(halal by default), concurrency owned by the port, an independent deterministic order-validator, and the
**Role-of-LLMs** principle. **Out of scope:** multi-tenancy, auth, billing, hosting, full i18n implementation
(captured only as principles in §6).

## 2. Broker abstraction — Ports & Adapters (hexagonal)

**2.1 The `Broker` port** (`halal_cb/brokers/base.py`) — a narrow interface every adapter implements, all I/O in
**canonical internal types** (`Candle`, `Decimal`, normalized `Product`/`Order`) so the engine never sees
broker-specific JSON or symbols:
- *Market data:* `get_candles(product, granularity, start, end)`, `get_spot(product)`.
- *Account:* `get_balances()`, `get_positions()`.
- *Orders:* `preview_order(...)`, `place_order(...)`, `cancel_order(id)`, `get_order(id)`.
- *Metadata:* `list_products()`, `fee_schedule()`, **`capabilities() -> BrokerCapabilities`**.

**2.2 Capability model** (`BrokerCapabilities`): declares `supports_spot`, `order_types`, `min_size`,
`supports_limit`, `max_leverage`, `asset_classes`, etc. The engine + compliance policy **check capabilities
before trading** and refuse cleanly when a broker can't do what a rule/policy requires (e.g. a broker offering
only CFDs/leverage).

**2.3 Symbol/product normalization:** each adapter maps the canonical `Product` ("BTC-USD") to/from the broker's
symbology (`BTCUSD`, `BTC/USDT`, …) so the rest of the system is symbol-agnostic.

**2.4 Adapters** (`brokers/coinbase.py` default; `brokers/binance_us.py`, `brokers/trade_nation.py` as
examples): each implements the port for one broker. **Per-adapter API-capability verification is required at
build time** (broker APIs differ widely; some may lack a suitable spot API — see §7).

**2.5 Selection & secrets:** config-driven (`broker: coinbase`) via a small factory; **per-broker credentials**
live in the encrypted secrets vault (main §14), keyed by broker id.

**2.6 Concurrency owned by the port** (resolves the "does the SDK support N threads?" concern): the adapter
**owns a single client instance** (or a tiny pool) and **serializes + rate-limits** all access behind the port
— thread-safety/rate-limits become an *adapter-internal* detail, invisible upstream. Design the port
**async-friendly**. **Heavy multi-threading is YAGNI** for the live agent (a minutes-cadence polling loop over a
few assets is correctly single-threaded); bounded parallelism is used only for **candle fetch across products**
and **offline backtests**, all funneled through the one owned client. (Real concurrency pressure only appears in
the SaaS future, which the async-friendly port keeps open.)

**2.7 Refactor path:** `data/cb_client.py` → `brokers/base.py` + `brokers/coinbase.py`; `market_feed`,
`executor`, etc. depend on the **`Broker` port**, not `CoinbaseClient`. Existing behavior/tests preserved
(Coinbase adapter behind the same contract).

## 3. Pluggable compliance policy (halal by default)

The halal rules (allowlist, no-leverage, no-shorting, no-carry, riba exclusions) move out of hardcoded
assumptions into a **`CompliancePolicy` interface** (`halal_cb/compliance/policy.py`), with **`HalalPolicy` as
the shipped default**. The engine asks the policy "is this product/action allowed?" before every setup/order.
This decouples *what's allowed* from *which broker* and is what lets "generic broker" coexist with "halal by
default": a **CFD/spread-betting broker (e.g. Trade Nation) is rejected by `HalalPolicy`** (leverage/CFDs = riba)
at the policy layer — no special-casing. The **halal agent** and a hypothetical **generic-policy agent** are
thus *two products sharing one core*.

## 4. Independent deterministic order-validator (the "reviewer")

Before any live order, a **second, independently-implemented code path** re-derives the proposed order
(entry/stop/target/rr, and re-checks every rail) from the raw candles and **must agree** with the engine's
output, else the order is vetoed and flagged (a deterministic **N-version / dual-computation** check). This is
the safe form of the "reviewer/validator sub-agent" idea — **fully deterministic, reproducible**, adding
high-assurance redundancy to the money path without any LLM. Complements (does not replace) `guards.py`.

## 5. Role of LLMs in the system — GOVERNING PRINCIPLE (effective now)

**5.1 The asymmetry principle.** A fuzzy / LLM-derived input may **directly *reduce* risk** (pause, veto, flag,
size-down — fail-safe). It may only **cause *increased* activity** (a new rule, a buy, larger size) **after
passing the deterministic backtest → paper → rails gate.** Fuzzy inputs may stop trading; they may never start it.

**5.2 Sanctioned LLM roles** (all off the live decision path):
1. **Strategy/parameter *proposer* (Opus) → then backtested.** LLM hypothesizes rules/params; the output becomes
   a *fixed deterministic rule* fed to backtest → paper gate → promotion. Rejected if it doesn't beat the floor
   out-of-sample. Expands the search space; the deterministic machinery still decides what's real.
2. **Insights / post-hoc analysis (Opus + Haiku), offline, human-facing.** DB mining, edge-decay, seasonality,
   quarterly review (main §15). Never a live order.
3. **Explainer.** Narrate a deterministic rule's confirm-mode setup in plain language so the *human* decides faster.
4. **Research (Haiku web-search) → *veto* side only.** Assemble a scheduled high-impact-events calendar
   (FOMC/CPI/halving/regulatory) that populates the **event-blackout filter** (main §3.6), which *pauses* trading
   around events. Reviewed + cached; never a live buy trigger (fail-safe direction only).
5. **Anomaly flagging** → notify the human. Surfacing, not deciding.

**5.3 The hard line (never):** No LLM in the **live trade decision** (whether/what/how-much) or in the **rails**.
Reasons (from main §6.4 + the knowledge base): LLM decisions are **non-reproducible → not backtestable**, which
voids the expectancy/paper/promotion proof that justifies risking real money; they reintroduce the
**prediction-oracle**; live web-search is exposed to **prompt-injection / market manipulation**, latency, and
cost. "Sentiment as a factor" is allowed only as a **quantifiable, backtestable feature** (LLM may help *design*
it offline; it then ships as deterministic, gated code) — never live LLM web-search in the loop.

## 6. SaaS + i18n — architecture principles only (NOT built)

Constraints the design honors so nothing precludes the future; **no multi-tenancy/auth/billing/hosting code now:**
- **Stateless, config-driven core:** a "tenant" is just *broker + compliance policy + secrets + locale + config*.
  No global singletons; everything injectable.
- **Pluggable compliance policy** (§3) already generalizes beyond halal.
- **Externalized user-facing strings** behind a tiny message catalog + a `locale` config (English default), so
  i18n is a later drop-in — never hardcode English (or halal) into logic.
- **Note (honest):** classic user **authentication** — YAGNI for the local single-user agent (main §14) —
  becomes **genuinely required** in the SaaS future (multi-tenant). The local design's "no auth" is correct for
  local; the SaaS door stays open via the stateless/injectable core, but SaaS itself is a separate program (§7).

## 7. Honest caveats & regulatory reality

- **A trading SaaS is a regulated financial activity, not just software.** Auto-trading others' money / holding
  their broker keys implicates securities & financial regulation, licensing (money-transmitter / investment-
  adviser by jurisdiction), KYC/AML, custody rules. **Gated on legal/business decisions, not engineering.**
- **Broker ToS:** many broker APIs restrict third-party/SaaS use of a user's API keys — a hard constraint on any
  multi-tenant offering; must be checked per broker.
- **Capability gaps:** spot-crypto exchange vs CFD vs stock broker differ hugely; the engine must gate on
  `capabilities()` + policy. Some named brokers (Trade Nation) may not expose a suitable spot API at all.
- **Two-products reality:** halal-default vs generic-policy are related-but-distinct products sharing the core.

## 8. Status & sequencing (deferred)

- **§5 (Role of LLMs)** — governing principle, effective immediately across phases.
- **§2–4, §6 (broker abstraction, validator, principles)** — implement **after Phases 1–4**, as their own
  milestone with issues for: `brokers/base.py` + capability model, the Coinbase adapter refactor, the
  `CompliancePolicy` extraction (HalalPolicy), the independent order-validator, per-broker secrets, and the
  i18n message-catalog seam. Each adapter (Binance.US, etc.) is a separate, capability-verified issue. **SaaS
  productization is explicitly not scheduled** here.

---

*Informational, not financial, legal, or religious advice. A trading SaaS requires independent legal/regulatory
review before any launch.*
