# The evolution plan — Jesse's skeleton, keel's soul

**Status:** plan of record. Owner-approved across the 2026-08-28→31 delivery sessions.
Amended 2026-08-31 after external review: ceremony card & idempotency binding (§5 Phase B),
screening data source (§5 Phase C), Fee Reality flagship (§5 Phase E), hosted-confirm
signer spike hypothesis (§5 Phase F).
**Companion decisions:** ADR 0004 (monetisation, pending #603), ADR 0005 (options, pending #637).
**Rule of application:** every item in this plan passes the constitution (§4) or it does not ship. The plan is subordinate to the constitution, always.

---

## 1. Context — what Jesse is, measured

Jesse (jesse.trade) is the category incumbent: an open-source, self-hosted Python crypto
framework (public 2020), monetised through a **closed-source live-trade plugin that runs on
the user's own machine** — lifetime $899 coexisting with Free/Basic/Pro/Enterprise
subscriptions, paid roadmap votes, feature sponsorship, and exchange affiliate links.

Their answers to the hard problems:

- **Privacy/custody:** solved structurally — they host nothing; keys never leave the user's
  machine. Their ToS even forbids using the package to run a competing cloud service.
- **Mobile/anywhere:** unsolved in eight years. No app, no PWA story; the de-facto answer is
  "run it on a VPS yourself."
- **Monetisation:** mature mechanics — lifetime → subscriptions with grandfathering → paid
  votes — but every layer landed *after* the community existed.
- **Free tier:** crippled by design (no paper trading, testnet-only live, CPU-capped
  research). Free is a demo.
- **Intelligence:** JesseGPT, a hosted chatbot that drafts strategies — a black box.

## 2. The synthesis rule — take / refuse

| Jesse's solution | Verdict | keel's version |
|---|---|---|
| Never host keys; paid part runs on the user's machine | **TAKE** | Zero-custody architecture; monetize trust artifacts, not hosting |
| Notifications free on every tier | **TAKE** | Web Push direct from the user's own engine; free forever |
| "…or your AI assistant — all self-hosted" + MCP headline | **TAKE** (deferred) | #600 / site #118 / #119 — parked in the Deferred milestone until there are users |
| Lifetime → subscriptions with grandfathering | **TAKE the loyalty, skip the whiplash** | When the trigger fires: early adopters grandfathered permanently, announced as a promise |
| Deep docs/guides as growth engine | **TAKE** | Site guides grow with the research front door |
| Public roadmap | **TAKE, minus the paywall** | Site #117, generated from milestones, free |
| ToS reserving hosted-service rights | **TAKE the decision, consciously** | Fold into Phase 6's licence choice |
| Paid paper trading, testnet-only free, CPU-capped research | **REFUSE** | Paper is the strongest safety default in the category — free and unlimited, forever |
| Selling "Premium Strategies" | **REFUSE** | keel's central measured finding is that no shipped rule family is net-positive at taker fees; selling strategies would sell an edge keel itself measured as absent |
| Exchange affiliate links | **REFUSE (permanent)** | Fees are the binding constraint; revenue scaling with venue routing is the exact incentive that finding warns against |
| Hosted GPT | **REFUSE, replace** | Point-your-own-assistant (MCP) — better, and costs nothing to run |
| Perpetual futures, margin, shorting | **REFUSE** | Spot-only, cash-account posture is a category with no competitor |

## 3. The three moats

1. **The honesty moat.** The evidence stack (#601's thirteen modules), the Strathern rail,
   published negative results. Jesse markets "is your edge real?" — keel measures it, at the
   fee actually paid, and can say no. This is a discipline, not a feature; it cannot be
   copied quickly.
2. **The anywhere/mobile moat.** Eight years and Jesse still answers "how do I use it from
   my phone?" with "run a VPS." keel can own this surface outright (Phase B).
3. **The intelligence moat.** keel + Ibn-Khaldun-Analyser: multi-lens historical divergence
   analysis wired into a compliance-gated engine *without* surrendering the decision
   (Phase D). Jesse's answer to intelligence is a strategy-drafting chatbot — the black box
   keel exists to argue against.

## 4. The constitution — every feature passes these or doesn't ship

1. A tool that cannot say no is a flattery tool.
2. Scores report and gate; they never rank (the Strathern rail).
3. Fees are priced at what was actually paid.
4. Paper is free and unlimited, forever.
5. Every attestation is human-sourced, or refused.
6. The free engine is never a demo.
7. The operator's servers never hold venue keys.
8. Negative results are published.

Every Jesse mechanic adopted in §2 passed a specific line here. Every refused one failed one.

## 5. The roadmap

### Phase A — active queue (filed, ordered)

#595 (docs truth) → #601 (`keel research` front door; adopts the in-flight console) →
#437 (first-run wizard) → #602 (chart workspace) → #603 (ADR 0004, now carrying the tier
table). Site: #117 (roadmap page), #120 (ADR docs sync).

### Phase B — the mobile wedge (to file; new "Anywhere access" milestone)

- **`keel link`** — one command: WireGuard mesh (Tailscale/Headscale) or Cloudflare Tunnel +
  Access, ACL pinned to the user's devices, QR pairing to the phone. The engine never
  changes; one-time session tokens stay.
- **Mobile-first PWA pass** — responsive redesign (bottom nav, ≥44px targets), install
  prompt, offline shell. The phone's honest role is command surface — above all
  **approve/deny in confirm mode**: the ceremony's remote desk.
- **Web Push from the engine** — VAPID, direct. Greenfield, not groundwork (corrected
  2026-08-31): the service worker ships for offline/manifest only — it has no push
  handler, no subscription endpoint, no payload encryption (RFC 8291), and the engine has
  no VAPID key management. All of that is new work. Prerequisite: the remote-exposure
  security pass (#648) — nothing remote ships before it.
- **The one-thumb ceremony card** — confirm-mode approval as a dedicated card, not a
  scaled-down table. v1 fields are only what exists today (fee basis at the order's clip
  size, purification liability, trigger rationale); a spread vital sign arrives only with
  #626's data capture — a number invented for the UI would be a constitution violation.
  Confirmations carry `idempotency_key` + `proposal_hash`, both; a mismatch is refused and
  proposals expire — so a retried approval over a flapping tunnel deduplicates cleanly,
  and a replayed key can never approve a regenerated proposal.
- **The price-drift gate** — an async approval is not a fresh quote: the envelope also
  carries `price_at_proposal` and a per-product drift tolerance. **Entries** exceeding it
  are refused with a named code and re-proposed at the next cycle; **exits are never
  walled off** — they degrade to the typed-phrase friction path, the same principle
  `_ask_to_place` already documents (trapping a position behind a gate is a worse money
  outcome than a warned order). The tolerance is derived — per-product, from measured
  spread once #626's capture lands — never a flat constant: a chosen number is the #523
  mistake in miniature.

Why this and not a hosted app: a hosted executor can never honestly say "we can't see your
data" — the operator ships the code that runs beside the plaintext keys. The only
architectures that keep the promise are the ones where the keys never reach our servers.

### Phase C — asset-class breadth (elevated)

- **Equities via Alpaca** — finish #370/#371. The completion bar now explicitly includes
  equity screening + purification extension (debt ratios, impermissible-revenue shares,
  human-sourced attestations) and cost fidelity *before* the live paper flip (#571's
  lesson applied to a new asset class). Screening measures from **SEC XBRL
  `companyfacts`** — AAOIFI 33/33/5 thresholds, a pinned quarterly snapshot under the
  #523 pin/re-pin discipline; the module measures, the human attests (recorded on #370).
  No third-party SaaS screening black box. Being first with honestly-costed,
  compliance-gated stocks next to crypto is a differentiator Jesse structurally cannot copy.
- **Phase 13 Binance** — #564–#568, after Alpaca proves the pattern.
- **Options** — a constitution decision, not a feature. Three collisions: the shipped
  `CashAccountRequired` rail (multiplier == 1, #372), the spot-only/no-leverage stance, the
  default-refused screen. If amended, the narrowest defensible slice is cash-secured only
  (covered calls, cash-secured puts), exception-gated unscreened book, `posture:
  exceptions` in every report, its own ADR (0005) with a reopen trigger. Sequenced after
  #370 lands and #636's venue-reality findings are in. Default posture until then:
  cash-spot only.

### Phase D — Ibn-Khaldun-Analyser integration (#570–#578)

```
IKA (Go, pgvector, four lenses)  ──SSE──▶  B1: bounded stdlib client (not a daemon)
     signal block (assessment)                    │
                                                 ▼
                                      B2: frame→Candidate adapter (pure, strictly
                                          narrower than the payload)
                                                 │
                                                 ▼
                                      B3: `keel analysis fetch` → shortlist that the
                                          EXISTING `assets propose` reads
                                                 │
                                                 ▼
                                       A HUMAN attests — or doesn't
```

- The rail extends to the feed (C1's AST pins): no IKA field may ever become a ranking
  key, an auto-admission, or an order input.
- C2's operator guide carries the sentence this implies: **you must not attest out of
  persuasion** — an attestation sourced "the feed said so" is refused exactly like an
  unsourced one.
- A1 before money: measure crypto relevance before buying the Desk tier. IKA reads
  macro/geopol; its crypto bearing must be measured, not assumed.
- B4: an entitlement lapse reads as "feed silent," never "market quiet."
- C3: the keep-paying decision, measured and published either way.
- Config is `analysis_feed:`, never `subscription:` (rail 14).

### Phase E — open source & discoverability (Phase 6 objective)

Licence choice (permissive + trademark posture + the reserve-hosted-rights decision),
README written for the GitHub audience, awesome-lists (site #14) and handles (site #15).
Jesse's eight-year GitHub presence is their entire funnel; keel's is unstarted. The
cheapest growth lever on the board. Its flagship is the **Fee Reality benchmark** (#646 +
site #136): the negative result, reproducible from recorded runs, as the README's opening
hook — every number rendered from the ledger or it does not ship, and no competitor named
in the asset.

### Phase F — monetisation (trigger-gated; recorded in ADR 0004)

| Tier | Price (USD-eq, crypto-only) | What it buys | The promise |
|---|---|---|---|
| **Free** | $0, forever | The full engine. Never a demo. | We see nothing |
| **Pro — "your box, anywhere"** | $14/mo · $140/yr prepay | managed `keel link`, signed+notarized installers (the #438 certs become Pro value), managed updates, Web Push plumbing, priority support | We can't see your data |
| **Founder — lifetime** | $399, first 200, then gone | Everything in Pro, forever, founder badge | Grandfathering is a promise, not a promo |
| **Hosted-confirm — "our brain, your keys"** | $49/mo · $450/yr (built last) | Full hosted engine; your device holds the keys and approves every order | We can see, we can't act |

- **Payment is crypto-only**, via self-hosted non-custodial BTCPay (#638): USD-priced at
  spot, prepaid credit (no auto-charge exists on-chain), Lightning for small recurring
  amounts, the invoice ledger as the audit trail. Chosen because it coheres with promises
  already made: no interest-bearing rails, no processor that sees the payer, no chargeback
  asymmetry. US reality stated plainly: crypto-only removes the processor, not the tax —
  receipt is ordinary income at fair-market value.
- **Hosted-confirm's signer** — one substantial new artifact, in either shape: (a) a small
  native signer app (Swift Keychain / Android Keystore, biometric-gated), or (b) the
  2026-08-31 hypothesis — venue keys held in the PWA as **non-extractable WebCrypto keys**,
  passkey-gated (the biometric unlocks the signing call; a passkey authenticates, it
  cannot sign an order), with the hosted engine as a **dumb relay** for fully-signed
  requests: browsers cannot call venue APIs directly (origin blocks), so the engine
  forwards bytes it can withhold but never forge. Caveats named honestly — IndexedDB is
  not a Keychain; venue signing algorithms must exist in WebCrypto; replay protection
  needs timestamps/nonces; and a malicious engine can still *propose* differently:
  socialize, not steal. Shape (b) is settled by a validation spike before any commitment,
  not by decision.
- **No classic SaaS tier.** If full-autonomy hosting is ever demanded, it ships labeled
  with Jesse's privacy tradeoff, not with a promise keel cannot make.
- The reopen trigger is unchanged and recorded in ADR 0004: **users, not revenue**.

## 6. Standing decisions already made on the way here

- **MCP surfacing deferred** (#600 → Deferred milestone) until there are users to serve.
- **Desktop signing stays off** (Option D on #438): unsigned at $0; activation is a
  secrets-drop; if only one platform is bought, macOS first. The certs are Pro-tier value.
- **Repo boundary:** app issues live in CodeGateSoftware/keel (project *keel*); site-only
  issues live in CodeGateSoftware/keeltrading.com (project *KeelTrading*). No issue carries
  another repo's work in its checklist.

## 7. The thesis

Jesse built the category's best *terminal* and monetized it by walling off capability.
keel becomes better by being the category's honest *instrument* — evidence that can say
no, compliance gates that stay in human hands, access from any device without custody, and
intelligence from IKA that informs without instructing — and monetizes trust itself:
signing, updates, access, support. Jesse's product asks to be trusted with your money;
keel's is designed so it never has to be.

## 8. Appendix — the honest cockpit: the Alpaca audit (2026-09)

A page-by-page audit of Alpaca's dashboard (overview, plans, connect, and the account
section: configure, balances, activities, orders, positions) was run against keel's
verified web surface. Alpaca's information architecture is worth copying in three places;
almost everything inside the skeleton is a growth funnel. The result is milestone
**28 — The honest cockpit (dashboard)**, and one addition to the thesis: the dashboard
itself is a moat, because every competitor's UI depends on hiding what keel shows.

### What the audit took

- **The account switcher as session identity** — paper/live visible on every page, not
  buried in settings (#704). Keel's chip displays and navigates; it never mutates. The
  paper banner carries **no CTA, ever** — Alpaca's carries "Open Live Account".
- **The account section's four-table split** — Balances / Activities / Orders / Positions
  (#700, #701, #702, #703). The engine already records everything these views need
  (per-order fees and expected-vs-actual fills, per-tranche brackets and stops, settled
  cash, the purification ledger); none of it was surfaced.
- **Status tabs, type chips, CSV export** — adopted (#703), with the export upgraded from
  spreadsheet to audit record: trials-ledger row hashes and attestation sources ride along.

### What the audit refused

- Any live-mode CTA, any one-click order entry, any one-click position close. Exits
  degrade to typed friction; a panic tap is never the last line of defense.
- Market movers and watchlist feeds inside the trading loop — engagement bait, not
  judgment support. Screening stays a separate honest tool (#370) that gates, never ranks.
- The Connect marketplace's credential handoff to third-party code. Keel's Connect page
  (growing the existing Venues view) shows adapters, declared capability gaps, and
  credential fingerprints — including what each key cannot do.
- Feature-gating inside the engine. The Plans page ships **inverted** (#706): a
  transparency artifact stating what is free forever and why — not a paywall matrix.

### What keel does beyond parity

- **The cancel asymmetry** (#707): cancelling an entry is refusing risk — frictionless,
  one-click even from the web. Cancelling an exit or protective bracket is removing
  protection — walled off with a typed `EXIT_PROTECTION_LOCKED` refusal naming the
  terminal command. No broker on earth makes this distinction; keel's constitution hands
  us the design.
- **Per-order honesty receipts** (#700): expected vs actual fill, fee as actually charged,
  quote provenance — execution drag in plain sight on every order row.
- **A mark-to-market equity series** (#698): the audit's one genuine engine addition —
  a mode-partitioned `equity_points` table replacing the 7-day rolling window, making the
  chart time-axised with the rail-11 and drawdown ceilings as overlays.
- **The Research Hub** (#708): trials ledger, evidence matrix, promotion gauntlet, and
  slippage universe in the navigation — all read-only surfaces over machinery that
  already exists and is already public. No broker, SaaS platform, or open-source
  competitor shows their trials and negative results; their business models depend on
  trading looking easy. PR #686's null — 0 of 120 cells clear PF 1.0 at per-product
  cost; the corpus null stands at 0 of 138 — is the headline exhibit, not a footnote.
  The Strathern rail holds in the UI: nothing in `/research` sorts or ranks by profit
  factor; views group by rule family and report pass/fail gates.

### Sequencing and guardrails

Sprint 1 — the cockpit backbone (#698 → #700 → #701 → #702); Sprint 2 — context and
history (#703 → #704 → #705, wiring the dead `journal` table as a human-sourced
attestation); Sprint 3 — action and transparency (#707 → #706, the cancel action last
because it opens the first non-setup write surface and carries its own security pass);
#708 runs in parallel after Sprint 1 — it is read-only and shares no code with #707.

Everything here is loopback-safe and waits on nothing: no view depends on #648, and the
async ceremony card remains Phase B exactly as planned. Money crosses the wire as
presentation-ready `Field`s, never JSON numbers; there is zero client-side math; and
every claim on every new page traces to recorded data — the dashboard holds itself to
the same standard as the engine.
