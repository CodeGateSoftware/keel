# PRD — `keel-broker-alpaca`: US equities via the broker port

**Status:** proposal (captured 2026-08-18 from an operator conversation; not scheduled) ·
**Lineage:** Phase 5 — Broker port & second venue (milestone #8; #233 and #198 remain open there) ·
**Tracking:** milestone *Phase 12 — Stocks via Alpaca* · **Author:** the operator, with the orchestrator

## 1. Problem / opportunity

keel's production deployment is single-venue: Coinbase spot crypto. The engine's value —
attested fails-closed screening, un-overridable rails, the promotion gauntlet, honest
measurement — is venue-agnostic by construction, but it is only exercised on one asset class.
US equities are the natural second class: deeply liquid, cheap to trade, and the compliance
questions (equity screening criteria) are the oldest and best-established territory in Islamic
finance. Friends and potential contributors have independently asked for exactly this ("I don't
really deal with crypto — Alpaca offers paper trading for stocks"), and one experienced
developer has volunteered for the build.

[Alpaca](https://alpaca.markets) exposes a modern REST/WebSocket Trading API for US equities
with **first-class paper trading** — which slots directly into keel's proving discipline.

## 2. Objectives

- **O1** — A `keel-broker-alpaca` package implementing the `keel-broker-api` port, plugging in
  under the `keel.brokers` entry point with **zero changes to the `keel/` core**.
- **O2** — The port's **conformance suite passes** against it (the deliberately-divergent fake
  venue exists precisely to keep adapters honest; a green suite is the acceptance bar).
- **O3** — An **equities paper profile** runs end-to-end: signals → rails → preview → paper
  fill → hash-chained evidence accrual, benchmarked against DCA on the same tickers.
- **O4** — **Cost fidelity first**: Alpaca's real cost structure (commission-free is not
  cost-free — SEC/FINRA regulatory fees on sells, spread, SIP-vs-IEX data tiers) is measured
  and documented before any strategy evaluation is believed.

## Non-objectives

- No margin, no shorting, no options, no derivatives — the engine's Global Constraints
  (long-only spot, cash-based sizing) already prohibit these; the adapter must not open a path
  around them. **Cash accounts only.**
- **No stock lending** — Alpaca offers fully-paid securities lending (income for lending out
  held shares). Lending out shares conflicts with *qabd* (possession is the engine's
  possession rail) and the income is interest-like. The account-level opt-out status is an
  **operator-verified obligation** documented in the runbook, and the adapter surfaces
  lent-share state as a rail input when the venue exposes it.
- **No high-yield cash sweep** — interest on uninvested USD is riba; opted out and recorded
  the same way.
- **No Alpaca crypto, tokenized equities ("Instant Tokenization Network"), or overnight
  session trading** — crypto stays on Coinbase; tokenized equities raise the exact
  per-instrument-classification questions the fiqh source review (#367 taxonomy) flags for
  careful study; overnight sessions are off under FR-9.
- **No OAuth Connect, no FIX, no MCP/"natural-language" trading** — key/secret REST (+
  optional WebSocket reads) only, and no AI/LLM anywhere in the loop: the engine is
  deterministic by design and advertises it.
- **No Broker API** — Alpaca's embed-brokerage product is the natural surface *if* keel ever
  becomes a compliance-SaaS (the subscription, never trade-commission, model from the
  2026-08-18 business-model discussion). Recorded here so the idea has a home; building it is
  not this milestone's work and would follow, not precede, scholarly review.
- No custody, no SaaS, no key handling changes — keys stay local to the operator deployment.
- No Robinhood live-path work (that is #198, separate).
- No strategy tuning for equities — the promotion gauntlet applies unmodified; a new venue is
  a new measurement, not a fresh start for unproven rules.

## 3. Background: what the port already provides, and one reference implementation

- `packages/keel-broker-api` — the contract every adapter codes against.
- `packages/keel-broker-coinbase` — the production adapter (the reference implementation).
- `packages/keel-broker-fake` — deliberately divergent venue + the **conformance suite** that
  runs against every adapter.
- `packages/keel-broker-robinhood` — the optional second venue (not part of a deployment).
- Capability-based venue visibility (#233, open) — an adapter declares what it can do; the
  engine must not infer capability from key presence. The Alpaca adapter should be the first
  consumer of whatever #233 lands, and its design must assume capability declarations.
- **Reference implementation reviewed:** [QuantConnect's LEAN Alpaca
  brokerage](https://github.com/QuantConnect/Lean.Brokerages.Alpaca) (Apache-2.0) — studied
  for its order-type matrix (market/limit/stop-market/stop-limit; `MarketOnOpen`/
  `MarketOnClose` equity-only), its settlement modeling, and its daily cash-sync pattern. Its
  cash/margin/PDT *simulation* modeling is out of keel's scope (keel enforces real
  constraints rather than simulating portfolio effects), but its capability enumeration is a
  useful checklist. Alpaca's own product surface (alpaca.markets) drives the session,
  corporate-action, and exclusion requirements below.

## 4. Functional requirements

- **FR-1 Package.** `packages/keel-broker-alpaca` with pyproject pinned `==` to the workspace
  version, registered under the `keel.brokers` entry point, shipping `py.typed`.
- **FR-2 Venue identity.** `venue = "alpaca"`, quote currency USD, asset class US equities
  (spot only). Attestations are keyed `(venue, product_id)` — equity instruments attest under
  their own venue namespace, never reused from Coinbase rows.
- **FR-3 Orders.** Market, limit, stop-market, and stop-limit BUY/SELL for supported
  instruments, fractional **and notional** quantities included (keel sizes by risk → USD
  notional → fractional shares; Alpaca's notional market orders map directly), routed
  through the same intent → guards → preview → confirm/place pipeline as Coinbase. Native
  bracket/OCO (`order_class: bracket`) maps keel's stop-loss + take-profit exit brackets —
  one venue-side atomic bracket instead of two legs where the API allows it. `MarketOnOpen`/
  `MarketOnClose` are declared as available-but-unused unless the exit machinery later wants
  them (exits must always execute; MOC is a candidate for guaranteed exit sessions).
- **FR-4 Preview with a book.** `preview_order` must surface best bid/ask where the venue
  provides them, feeding the #332 entry-override warning and the #350 max-spread entry gate
  unchanged. Where the venue cannot provide a book at preview time, the gate's documented
  fail-closed semantics apply.
- **FR-5 Market data.** Candle history mapped onto `Granularity` (Alpaca's `15Min`, `1Hour`,
  `1Day` cover keel's confirmation/trading/bias series). Data-tier differences (IEX vs SIP)
  must be declared as a capability and their cost/fidelity implications documented — candles
  drive rails (feed-staleness) and rules alike.
- **FR-6 Balances / positions.** Cash and position reads for the paper and live accounts,
  with settlement state (T+1) surfaced honestly where it affects spendable cash.
- **FR-7 Fees.** A cost model reflecting Alpaca's actual structure: $0 commission, but
  regulatory fees on sells (SEC Section 31 + FINRA TAF, passed through) and spread. This
  belongs beside the per-venue fee honesty the crypto side already practices.
- **FR-8 Conformance.** The adapter passes the port conformance suite; divergences the suite
  exposes are either fixed or documented as declared, deliberate capability gaps.
- **FR-9 Session & calendar awareness.** Equities are not 24/7: the adapter declares regular
  session (9:30–16:00 ET, holidays and half-days included) as the default posture, sourced
  from the venue's clock/calendar endpoints — not a locally maintained calendar that drifts.
  The agent's cycle scheduling and the feed-staleness rails must be **session-aware**: a
  weekend or market holiday is "market closed," never "feed stale" (the crypto staleness
  semantics would false-positive), and extended/overnight sessions (Alpaca trades 24/5 with
  session-aware routing) are explicitly OFF by default — overnight liquidity is thinner and
  the #350 spread gate would bind constantly. Session posture is config, validated at load.
- **FR-10 Corporate actions.** Splits, dividends, and ticker changes are first-class events,
  not edge cases: candle policy must state adjusted vs raw (backtests on split-adjusted
  series; the cache records which), held-position quantities reconcile through split events,
  and dividend events surface as **recorded events with a purification obligation** (see §5).
  The adapter consumes the venue's corporate-actions announcements; an action that cannot be
  reconciled halts that instrument's entries (fail-closed) rather than trading through a
  mis-sized position.
- **FR-11 Rate limits & endpoints.** Alpaca's trading endpoints are rate-limited (~200
  requests/min on the basic tier) with separate paper and live hosts (`paper-api` vs `api`) —
  keel's cycle cadence (a handful of requests per cycle) sits far below any limit, but the
  adapter honors venue 429/backoff semantics and the paper/live host selection is part of the
  #233 capability declaration (a paper key must never be mistaken for a live one).

## 5. Compliance requirements (the part keel exists for)

- **Per-instrument attestation.** Equity screening criteria (business-activity screens,
  leverage ratios, purification) are operator-supplied classifications from attributed
  sources — the AAOIFI/IFSB standards watch from the fiqh source review applies directly.
  The engine computes market facts; it never infers a classification.
- **Rail 17 (withdrawal capability) semantics for equities.** "Can this asset leave this
  venue?" maps to transfer-out capability (e.g. ACATS). The attestation flow needs an
  equities-specific operator note in the runbook.
- **Dividend purification (new, FR-10-adjacent).** Equities pay dividends; a screened
  stock's dividend may include income from non-compliant activity, and purification
  (disbursing the impure fraction) is the operator's compliance obligation. keel's role is
  determinism and record: dividend events are recorded, the purification calculation is
  recorded against an operator-documented policy (inputs: the attestation's purification
  ratio; outputs: amount and disposition), and the runbook walks the operator through it.
  keel computes and records; it never rules.
- **Cash-account discipline.** The adapter documents and the config enforces cash-only
  accounts: no margin borrowing (riba), which also sidesteps the Pattern Day Trader rule's
  $25k margin-account threshold — but **settlement** (T+1 for equities) then constrains
  spendable cash between trade and settlement, which must be surfaced (FR-6) because it
  interacts with cadence and the spend rails.

## 6. Success criteria

1. Conformance suite green against `keel-broker-alpaca` in CI.
2. An equities paper profile accrues hash-chained evidence end-to-end on Alpaca's paper API.
3. A cost-fidelity document (fees + regulatory costs + spread, by data tier) exists and the
   DCA benchmark runs on the same tickers — no strategy claim is believed before it.
4. Runbook section covering profile bootstrap, attestation semantics, PDT/T+1 posture, and
   the cash-account constraint.

## 7. Phasing

- **Phase A — Adapter.** Package scaffold, port implementation, conformance suite green
  (FR-1–FR-8). *The advertised "dream first contribution": well-scoped, contract-documented,
  suite-verified.*
- **Phase B — Paper profile.** Config template, seeding, runbook docs, end-to-end paper
  accrual (O3, §5, §6.2/6.4).
- **Phase C — Cost fidelity & benchmark.** Measurement doc, DCA restatement for equities
  (O4, §6.3). Nothing in any phase touches the live path; live is a later, separately-gated
  conversation with the same refusal posture as crypto.

## Trademark and originality posture

Alpaca is a trademark of its owner; keel is not affiliated with, endorsed by, or sponsored by
Alpaca (nor by Coinbase or Robinhood). Venue names appear in this repo solely as nominative
use — to identify what an adapter talks to — and never in branding, logos, or any phrasing
that implies partnership. The adapter is an **original implementation against Alpaca's
publicly documented API**, not a derivative of Alpaca's products. Reference to third-party
open source (such as QuantConnect's Apache-2.0 LEAN adapter) is for requirements
study only; if any third-party code is ever ported into this repo it must carry its license
and attribution in the contributing PR, and original implementations are always preferred.
The README carries the standing disclaimer.

## 8. Risks

- **Data licensing/fidelity** (IEX vs SIP) silently degrading candle quality → mitigate with
  capability declaration + the 15-minute data-health screen pattern from #351.
- **Corporate-action mishandling** (a missed split mis-sizing a position; an unadjusted
  candle series corrupting a backtest) → FR-10's fail-closed halt plus a recorded
  adjusted/raw policy per cached series; Phase C re-measures across a known action date.
- **Session-boundary staleness false-positives** (weekends/holidays read as feed failures)
  → FR-9 makes session state a first-class input to the staleness rails, tested against a
  holiday calendar fixture.
- **Extended-hours drift** (someone enabling overnight sessions for "more signals") →
  posture is config-validated and documented against FR-9's liquidity rationale; the #350
  spread gate remains the backstop.
- **Regulatory drift** (fee schedules, settlement cycles, rate limits) → the cost model is
  versioned and re-measured, not assumed.
- **Contributor dependency** — the volunteering contributor may drift; Phase A is scoped so
  the port contract and suite, not any one person, carry the correctness bar.
- **Scope creep toward live trading** → the non-objectives section is the contract; the
  promotion gauntlet applies unmodified.

## 9. Issues

Tracked in milestone *Phase 12 — Stocks via Alpaca*: adapter + conformance (Phase A),
equities paper profile (Phase B), cost fidelity + benchmark (Phase C), and the cash-account/
regulatory posture document.
