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
- No custody, no SaaS, no key handling changes — keys stay local to the operator deployment.
- No Robinhood live-path work (that is #198, separate).
- No strategy tuning for equities — the promotion gauntlet applies unmodified; a new venue is
  a new measurement, not a fresh start for unproven rules.

## 3. Background: what the port already provides

- `packages/keel-broker-api` — the contract every adapter codes against.
- `packages/keel-broker-coinbase` — the production adapter (the reference implementation).
- `packages/keel-broker-fake` — deliberately divergent venue + the **conformance suite** that
  runs against every adapter.
- `packages/keel-broker-robinhood` — the optional second venue (not part of a deployment).
- Capability-based venue visibility (#233, open) — an adapter declares what it can do; the
  engine must not infer capability from key presence. The Alpaca adapter should be the first
  consumer of whatever #233 lands, and its design must assume capability declarations.

## 4. Functional requirements

- **FR-1 Package.** `packages/keel-broker-alpaca` with pyproject pinned `==` to the workspace
  version, registered under the `keel.brokers` entry point, shipping `py.typed`.
- **FR-2 Venue identity.** `venue = "alpaca"`, quote currency USD, asset class US equities
  (spot only). Attestations are keyed `(venue, product_id)` — equity instruments attest under
  their own venue namespace, never reused from Coinbase rows.
- **FR-3 Orders.** Market and limit BUY/SELL for supported instruments, fractional quantities
  included, routed through the same intent → guards → preview → confirm/place pipeline as
  Coinbase.
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

## 5. Compliance requirements (the part keel exists for)

- **Per-instrument attestation.** Equity screening criteria (business-activity screens,
  leverage ratios, purification) are operator-supplied classifications from attributed
  sources — the AAOIFI/IFSB standards watch from the fiqh source review applies directly.
  The engine computes market facts; it never infers a classification.
- **Rail 17 (withdrawal capability) semantics for equities.** "Can this asset leave this
  venue?" maps to transfer-out capability (e.g. ACATS). The attestation flow needs an
  equities-specific operator note in the runbook.
- **Cash-account discipline.** The adapter documents and the config enforces cash-only
  accounts: no margin borrowing (riba), which also sidesteps the Pattern Day Trader rule's
  $25k margin-account threshold — but **T+1 settlement** then constrains churn, which must be
  documented because it interacts with cadence and the feed-staleness rails.

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

## 8. Risks

- **Data licensing/fidelity** (IEX vs SIP) silently degrading candle quality → mitigate with
  capability declaration + the 15-minute data-health screen pattern from #351.
- **Regulatory drift** (fee schedules, settlement cycles) → the cost model is versioned and
  re-measured, not assumed.
- **Contributor dependency** — the volunteering contributor may drift; Phase A is scoped so
  the port contract and suite, not any one person, carry the correctness bar.
- **Scope creep toward live trading** → the non-objectives section is the contract; the
  promotion gauntlet applies unmodified.

## 9. Issues

Tracked in milestone *Phase 12 — Stocks via Alpaca*: adapter + conformance (Phase A),
equities paper profile (Phase B), cost fidelity + benchmark (Phase C), and the cash-account/
regulatory posture document.
