[← Knowledge Base index](../README.md)

## Source 30 — "Understanding Riba in Islamic Finance" (Azzad Asset Management white paper, Joshua Brockwell, 3pp)

> Third compliance source (after [28](./source-28.md), [29](./source-29.md)). Very short, riba-focused.
> The compliance dimension is **deeply saturated**, but this one adds **one genuinely new, crypto-relevant
> nuance** — the **two-types-of-riba taxonomy**, especially **riba al-fadl** (§30.1), which Sources 28–29
> didn't distinguish and which bears directly on stablecoin funding and the spot-settlement requirement.
> Also re-confirms **AAOIFI** as the screening authority (§30.3). Everything else reinforces §28.

---

### 30.1 ⭐ The two types of riba — riba al-fadl adds a crypto-relevant nuance → `CompliancePolicy`
Almost all scholars split riba into two, and the second is new to our KB:
- **Riba al-nasee'ah** ("Qur'anic interest") — *"an increase in the amount of a commodity due to the mere
  passage of time"* = interest / compound interest on loans. → what we **already** exclude (leverage
  financing, swap/rollover, carry, bonds, lending-yield). No change.
- **Riba al-fadl** ("riba of surplus") — *"an immediate exchange of **unequal quantities of the same
  commodity**"* (gold-for-gold, silver-for-silver, wheat-for-wheat). The same-commodity exchange must be
  **equal in amount AND simultaneous (hand-to-hand / spot)**. → **New nuance.** Classical rule set: when
  exchanging the **same** ribawi commodity → must be **equal + spot**; when exchanging **different**
  commodities (e.g. gold-for-dollars) → amounts may differ but the exchange must still be **spot
  (simultaneous)**; deferment turns it into riba al-nasee'ah.

**Implications for our spot-crypto agent:**
- **Normal BTC/ETH ↔ USD spot trades are clearly fine:** different "commodities," unequal quantities are
  permitted, and Coinbase spot settlement is immediate (hand-to-hand) → satisfies the spot requirement.
- **Further grounds the SPOT / immediate-settlement mandate:** even a pure currency exchange must be
  simultaneous — **deferred settlement = riba**. This is an *independent* shariah reason (beyond gharar,
  §28.1) that **forwards/futures are excluded** and that we must trade **spot, not deferred**.
- **Stablecoin / same-money nuance (small, worth noting):** exchanging the **same monetary kind** should be
  **at parity + spot** — e.g. USDC↔USD or USDC↔USDT ≈ **1:1**, and any wrapped/underlying same-asset swap
  (wBTC↔BTC, wETH↔ETH) should be 1:1. Our **USDC-funding rail (rail 13)** already routes buys through USDC
  vs USD (effectively parity); we **don't do crypto↔crypto pairs** (already excluded, §27.4/§4.9), so
  riba al-fadl isn't triggered in practice — but a `CompliancePolicy` note should flag "no non-parity
  same-asset/stablecoin swaps" for completeness.

### 30.2 Reinforced (nothing new)
- **Riba = "any excess value in prohibited transactions,"** literally "increase/addition/growth"; interest
  (simple & compound) is the archetype → restates §28.1. **"Deceit in the marketplace is a type of riba"**
  → reinforces the honesty / no-wash-trade / bay'-al-inah point (§28.1, §29.3). Fiat fractional-reserve/
  inflation framed as riba-adjacent "devouring of wealth" → echoes the crypto/gold sound-money thesis
  (source-23 intro); context only, not actionable.

### 30.3 AAOIFI re-confirmed as the screening authority (2nd citation) → `CompliancePolicy` reference
Azzad "abides by **AAOIFI** guidelines when evaluating securities for investment," and cites AAOIFI as the
modern credentialed body applying timeless riba rules to new circumstances. → **Second independent source
naming AAOIFI** (after §29.1) as *the* authority — confirms it's the canonical reference for any formal
`CompliancePolicy` screening criteria.

### 30.4 Discarded (no agent value)
Azzad fund marketing (Azzad Wise Capital Fund, sukuk / Islamic bank deposits / ethical dividend stocks —
their products, out of scope); purchase/registration/broker CTAs & disclaimers; historical anecdotes
(Temple money-changers, Omar ibn al-Khattab); debt-repayment spiritual guidance; corporate boilerplate.

---

### Net assessment (saturation-honest)
- **Compliance dimension is SATURATED** — this is the third compliance source and adds only one nuance.
- **NEW:** the **riba al-fadl** distinction (§30.1) — mainly (a) an **independent shariah grounding for the
  spot/immediate-settlement mandate** (same-commodity/currency exchange must be simultaneous; deferred =
  riba → another reason forwards/futures are out) and (b) a small **stablecoin/same-asset "parity + spot"**
  note for `CompliancePolicy` (already satisfied by rail-13 USDC funding + no crypto-crypto pairs).
- **RE-CONFIRMS:** AAOIFI as the screening authority (§30.3); riba = the interest/excess archetype (§30.2).
- **No new rails or strategy.** Action: add a `CompliancePolicy` note "same-asset/stablecoin swaps only at
  parity + spot; all settlement spot/immediate" and cite the spot-settlement rationale in the design spec.
  **Recommend pausing the compliance-source stream** — 28/29/30 have covered riba/gharar/maisir/sector/
  settlement; further Islamic-finance papers will likely be pure reinforcement. See
  [[halal-cb-autotrade-project]], [[halal-cb-transcript-workflow]].
