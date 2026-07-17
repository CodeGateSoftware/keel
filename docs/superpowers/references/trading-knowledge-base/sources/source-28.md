[← Knowledge Base index](../README.md)

## Source 28 — "The Economics of Islamic Finance and Securitization" (Andreas A. Jobst, IMF Working Paper 07/117, 2007, 37pp)

> **A different KIND of source** — the first **halal-compliance foundation**, not a trading strategy.
> An academic IMF paper. Most of it (Islamic securitization, sukuk, murabaha/ijarah/mudharaba financing
> structures, put-call-parity valuation models) is **out of scope** — our agent does spot crypto, not
> structured finance. But its **Section II definition of Islamic finance** is the authoritative grounding
> for the project's whole halal posture: it **defines and validates every exclusion rail we already have**
> (riba/leverage/derivatives/options), explains **why spot ownership is permissible while CFDs are not**,
> and adds **one genuinely new screening dimension** — haram business-line/sector screening (§28.4).
>
> **Use:** this is the citable authority behind `keel`'s **HalalPolicy / CompliancePolicy** and the
> "Explicit exclusions" rail-set. See [[halal-cb-autotrade-project]] (halal spot-crypto, long-only,
> no-leverage mandate; HalalPolicy as pluggable CompliancePolicy in the broker-abstraction spec).

---

### 28.1 The five prohibitions (authoritative definitions) → grounds `guards.py` exclusions + CompliancePolicy
Islamic finance forbids financial relationships involving (paraphrasing the paper's Section II, p.4):
1. **Riba** (interest/usury) — *"any unjustifiable increase of capital, whether through loans or sales …
   any positive, fixed, predetermined rate of return **guaranteed regardless of the performance of an
   investment**."* → grounds our exclusion of **leverage/margin financing, swap/rollover charges, carry,
   bonds** (all riba). Confirms §4.9 / §18 / §23.8 / §27.4.
2. **Maisir** (gambling/speculation/betting) — *including "the speculative trade or exchange of money for
   debt **without an underlying asset transfer**."* → grounds exclusion of **binary/digital/barrier
   options** (fixed-odds bets) and pure speculation. **Validates the source-27 call** (§27.4) that digital
   options carry maisir.
3. **Gharar** (preventable/excessive uncertainty) — *"such as **all financial derivative instruments,
   forwarding contracts, and future agreements**."* → grounds exclusion of **CFDs, futures, forwards,
   options, all derivatives**. This is the **single strongest authority for our no-derivatives mandate** —
   the entire Stanzione-ebook premise (CFDs) is gharar-excluded at the root.
4. **Haram business lines** — direct/indirect association with **alcohol, pork, firearms, tobacco, adult
   entertainment** (also hoarding, miserliness, extravagance). → **NEW screening dimension**, see §28.4.
5. **Bay' al inah** — trading the same object between buyer and seller (sham sale-repurchase). → niche;
   loosely maps to a **wash-trade prohibition** (don't self-deal / no fabricated round-trips). Minor.

### 28.2 ⭐ Why SPOT ownership is halal but CFDs/derivatives are not → validates the core mandate
The paper's central tenet: *income must be derived from **shared business risk and ownership**, not
guaranteed return; profits accrue only if the underlying investment yields income; investors must hold
**"clearly identifiable rights and obligations"** and **"a sufficient element of ownership."*** This is the
principled reason the project's design is correct:
- **Spot crypto = you OWN the asset** and bear its real price risk → permissible (this is the "halal spot
  long-only" baseline). **CFD/future/option = you own nothing**, you hold a bet on price with a
  counterparty → **gharar + no ownership → excluded.** The distinction we've drawn empirically across
  Sources 18/23–27 is exactly the distinction Islamic jurisprudence draws.
- **No guaranteed return / profit-loss sharing** → grounds why **leverage** (guaranteed financing cost
  regardless of outcome = riba) and **fixed-odds options** (predetermined payoff = maisir) are out.
- **Long-only fits too:** profit from owning a productive/appreciating asset, not from a pure directional
  wager on decline you don't own (short = sell what you don't possess). Reinforces the long-only rail.

### 28.3 Asset-backing / real-economic-activity + low-turnover → reinforces anti-scalping + asset selection
- *Gains "must involve the funding or production of **real assets** rather than the purchase of financial
  securities, which would amount to second-order financing … the subsequent gearing being speculative."*
  → For crypto this is the crux of the broader **"is this token halal"** question: favor assets with
  **genuine underlying utility/economic activity** over pure-speculation/meme tokens. Feeds allowlist
  curation (a qualitative CompliancePolicy factor), not a hard numeric gate. (The paper predates Bitcoin;
  it does **not** rule on crypto — the project has **already committed** to spot-crypto-as-permissible
  baseline; this is context, not a re-litigation.)
- *"The underlying assets … must not be employed for **speculative purposes, and turnover should be kept
  low**."* → a principled reinforcement of our **anti-scalping / min-move rail (§4.1)** and the
  **longer-term-holding / trend-following bias**: **high-frequency churn drifts toward maisir**, so our
  low-turnover, hold-the-trend posture is not just better strategy (§23–27) but better *compliance*.

### 28.4 ⭐ NEW screening dimension — haram business-line/sector screen → `CompliancePolicy` / allowlist
Beyond *trade mechanics* (riba/gharar/maisir, which we already enforce), the paper adds a **subject-matter
screen**: an asset is impermissible if its **underlying business/purpose is haram** (alcohol, pork,
firearms, tobacco, adult entertainment, gambling). We had focused almost entirely on *how* we trade (spot,
no-leverage, no-shorts); this adds a check on *what* we trade:
- **Actionable for crypto:** screen OUT tokens whose core purpose is a haram sector — e.g. **gambling/
  casino dapp tokens, adult-content tokens, tokens for prohibited goods**, and (arguably) pure interest-
  bearing "yield"/lending tokens (riba) and some staking-yield instruments (riba-like guaranteed return).
- → Add a **`haram_sector` screen to CompliancePolicy** applied at **allowlist admission** (a token's
  sector/utility is a listing criterion, checked once when curating the allowlist, not per-trade). Keeps
  the hard rails mechanical while adding this as a **curation gate** on what may enter the allowlist.
  Complements the existing custody/liquidity/5yr-data/backtestable admission criteria.

### 28.5 Out of scope (financing machinery — not what a spot agent does)
The bulk of the paper: **sukuk** (Islamic investment certificates / ABS), the three financing forms
(**murabaha** cost-plus sale, **ijarah** lease/sale-leaseback, **mudharaba** profit-sharing) and variants
(salam, istisna, BBA, quard al-hasan, musawama); **put-call-parity replication** and asset-pricing models
of Islamic contracts; SPV/true-sale structuring; **takaful** (Islamic insurance); Malaysia/Gulf market
development; legal-uncertainty and enforceability discussion. All are **structured-finance mechanics our
spot agent never touches** — recorded as context only. (One tangential note: the paper shows Islamic
finance often *replicates* conventional payoffs via asset arrangements — **not a licence for us to
synthesize leverage/derivatives**; our mandate stays plain spot, which is unambiguously permissible.)

### 28.6 Discarded (no agent value)
IMF disclaimers, JEL codes, abstract/keywords; references; figure/box captions (CARAVAN I SPV,
Malaysia pacemaker); the entire valuation-model math (put-call parity pay-off profiles); market-size
statistics circa 2007.

---

### Net assessment
- **This is a compliance-FOUNDATION source, not a strategy source** — the first of its kind in the KB.
- **VALIDATES (with authority) every exclusion we already enforce:** riba → leverage/swap/carry/bonds;
  gharar → CFDs/futures/forwards/**options**; maisir → binary/digital options + speculation; and the
  ownership/profit-loss-sharing principle that makes **spot long-only** permissible while derivatives are
  not. Confirms the source-27 digital-options (maisir/gharar) call.
- **NEW & actionable:** a **haram business-line/sector screen** for `CompliancePolicy` at **allowlist
  admission** (§28.4) — screen out gambling/adult/prohibited-sector tokens and riba-yield instruments; and
  a principled **low-turnover/anti-speculation** reinforcement (§28.3) backing the anti-scalping rail +
  trend-following hold bias.
- **CONTEXT (not re-litigated):** the asset-backing / "is a given token halal" question — favor real-
  utility assets in allowlist curation; project already committed to spot-crypto baseline.
- **OUT OF SCOPE:** all sukuk/murabaha/ijarah/mudharaba/takaful securitization machinery.
- **Action:** wire a `haram_sector` curation gate into the allowlist/CompliancePolicy; otherwise this
  source mainly hardens the *rationale* behind the existing rails (cite it in the design spec's compliance
  section). See [[halal-cb-autotrade-project]], [[halal-cb-transcript-workflow]].
