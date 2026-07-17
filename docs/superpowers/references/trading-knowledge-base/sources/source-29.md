[← Knowledge Base index](../README.md)

## Source 29 — "Islamic Banking Processes and Products: Key Regional Variations" (Oracle white paper, Sept 2017, 14pp)

> Second compliance-adjacent source (after [Source 28](./source-28.md)). An **industry white paper on
> Islamic retail/corporate BANKING** (deposits, financing, treasury, trade finance across Middle East /
> North Africa / Malaysia / Indonesia) — **almost entirely out of scope** for a spot-crypto trading agent,
> and its compliance principles are a **subset of Source 28**.
>
> **Saturation-honest: thin.** No trading mechanics; the riba/gharar/profit-loss-sharing tenets just
> restate §28.1. **Three small takeaways only** (§29.1–29.3); everything else is discarded.

---

### 29.1 AAOIFI — the authoritative shariah-compliance standards body → reference for `CompliancePolicy`
The paper flags **AAOIFI (Accounting and Auditing Organization for Islamic Financial Institutions)** as the
body whose standards are adopted "on a mandatory basis or as guidance" by many central banks. → **Useful
reference pointer:** if the project ever wants *formal, citable* shariah-screening criteria for
`CompliancePolicy` (beyond our own conservative rules), **AAOIFI standards are the canonical source** (and
AAOIFI has published guidance on financial-market/equity screening). Recorded as the go-to authority to
consult; no change to current rules required.

### 29.2 ⚠️ Scholarly divergence / regional variation → validates a *pluggable, conservative* CompliancePolicy
The paper stresses there is **"a difference in opinion on Sharia interpretations among Sharia scholars,"**
notably Middle East vs Malaysia, and that frameworks "can often be contradictory." Concrete examples:
**Tawarruq** is popular in Malaysia but **prohibited** by scholars in ME/NA/Indonesia; **pawn-broking
(Al-Rahnu)** is prohibited in ME/NA but popular in Malaysia/Indonesia. → **Important caveat for us:**
halal-screening is **not monolithic** — our `HalalPolicy` encodes **one deliberately conservative
interpretation** (spot-only, no-leverage, no-derivatives, no-options). This (a) **validates keeping
`CompliancePolicy` pluggable/configurable** (already the design in the broker-abstraction spec — HalalPolicy
as a swappable policy) and (b) argues for **documenting explicitly that we follow a conservative reading**,
so the choice is transparent rather than presented as the single "correct" ruling.

### 29.3 Industry-practice reinforcement of our exclusions (all four regions)
Confirms our gharar/derivative exclusions from a real-world-practice angle:
- **Forex "forward deals are NOT allowed"** — in every region → reinforces exclusion of **forwards/futures**
  (gharar), §28.1/§27.4.
- **Derivatives:** "very limited … due to strict ruling by Sharia scholars," "Not Yet Available," "No
  derivative-based product" → reinforces the **no-derivatives** mandate.
- **Bay' al inah:** Salam annexure notes "it is prohibited to sell the commodity to the original party" →
  reinforces the no-sham-round-trip / no-self-dealing point (§28.1) — loosely, a wash-trade prohibition.
- **Profit-and-loss sharing / real-asset backing** (riba → PLS; Sukuk "must be linked to an underlying
  asset") → restates §28.2–28.3, nothing new.
- (Minor context: **penalty income is given to charity** — impermissible income is "purified" via charity;
  not applicable to our agent, which charges no penalties.)

### 29.4 Out of scope / discarded (the bulk of the paper)
All banking-product mechanics and regional comparison tables — **Deposits** (Wadiah/Mudharabah/Qard/
commodity-Murabaha/Wakalah), **Financing** (Murabaha/Ijarah/Diminishing-Musharakah/Istisna/Tawarruq/
Salam), **Treasury**, **Trade Finance** (LC/bills/guarantees); the **Annexure glossary** of these structures
(all already contextualized in §28.5); market-size stats, growth drivers, geographic-expansion strategy,
customer-centricity / cross-sell / ROE commentary, Oracle product-direction & corporate boilerplate.

---

### Net assessment (saturation-honest)
- **Compliance dimension has SATURATED** — this paper reinforces Source 28 without adding principles.
- **Only three takeaways:** **AAOIFI** as the authoritative screening-standards reference (§29.1); the
  **scholarly-divergence caveat** validating a pluggable + explicitly-conservative `CompliancePolicy`
  (§29.2); and **industry-practice confirmation** that forwards/derivatives are broadly prohibited (§29.3).
- **No new rails, no strategy content, no allowlist changes.** Action: note AAOIFI + the conservative-
  interpretation disclaimer in the design spec's compliance section; otherwise nothing to build. See
  [[halal-cb-autotrade-project]], [[halal-cb-transcript-workflow]].
