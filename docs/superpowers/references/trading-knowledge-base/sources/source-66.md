[← Knowledge Base index](../README.md)

## Source 66 — Four trading-specific shariah papers (bay' al-sarf, qabd, and forex fiqh)

> **Four papers, one file**, all trading/exchange-specific rather than banking-specific — the
> sharpest-focused compliance material fed to this KB so far. In citation order below:
> - **[A]** Syed Faiq Najeeb, *"Trading in Islam: Shari'ah Rules and Contemporary Applications in
>   Islamic Financial Transactions"* (INCEIF, *Journal of Emerging Economies and Islamic Research*,
>   2014, 26pp) — a comprehensive academic survey of sale-of-goods and sale-of-debt contracts.
> - **[B]** Aminah Zuhria, *"Islamic Finance within Trading Framework: The Way to Legitimate
>   Profit"* (INCEIF coursework paper, CIFP part 1, 2011, 19pp) — a thinner student survey of the
>   same sale-contract taxonomy, business-ethics framed.
> - **[C]** Siti Endri Lutfiah, Sofian Al-Hakim & Cucu Susilawati, *"Sharia Economic Law Analysis of
>   Foreign Currency Buying and Selling in Forex Trading Transactions at PT. Didimax Berjangka"*
>   (*Jurnal Hukum Ekonomi Syariah AICONOMIA*, 2024, 13pp) — a field study of one Indonesian retail
>   forex/commodities broker, analyzed against DSN-MUI fatwas on *sharf* and hedging.
> - **[D]** Sharifah Fatimah Azzahra, Tengku Masturah Tg. Paris & Saim Kayadibi, *"Forex Trading:
>   Conventional and Islamic Perspective"* — Ch.3 of *The Principle of Currency Value System and
>   Islamic Banking and Finance* (IIUM Press, 2011). **The file is severely truncated: 5 pages total
>   — cover, title page, copyright, table of contents, and only the chapter's OPENING PARAGRAPH**
>   (p.55 of the book; the chapter itself runs to p.73 per the TOC). No shariah analysis is actually
>   present in the file. Logged for completeness only; see §66.8.
>
> **Net effect:** [A] and [C] are genuinely dense and on-point — [A] gives the fullest sale-contract
> taxonomy in the KB including an explicit **bay' sarf** entry and a **qabd-before-resale** hadith;
> [C] is the first source to analyze a *live* named forex broker's mechanism against *sharf* and
> hedging fatwas, directly engaging the §56.1 "spot"-label question. [B] mostly duplicates [A] at
> lower resolution. [D] contributes nothing extractable.

---

### 66.1 ⭐⭐ Bay' al-Sarf — the contract that actually governs USDC↔BTC, named and specified

**[A] Najeeb, Table 2 ("Types of Permissible Contracts")** gives the KB's first explicit named
entry for the *sarf* contract:

> **Bay Sarf** — "Sale of absolute price for absolute price or money exchange. For instance: a new
> 10 RM note must be exchanged with an equal value of an old 10RM note; money exchange e.g. where
> 1USD equals to 3.1RM according to market rate at that time." Prohibition: "No Prohibition **as
> long as the currencies are exchanged in the same session of contract (on-spot delivery and
> possession) and in equal quality and rate**."

**[C] Lutfiah et al.** independently states the identical conditions from the DSN-MUI fatwa
(No. 28/DSN-MUI/III/2002) framing:

> "...there is no speculation (luck), there is a need for transactions or just in case (savings),
> if transactions are carried out in similar currencies, then the value must be the same and in
> cash (*attaqabudh*), and if they are of different types, then they must be carried out with
> exchange rate in effect at the time the transaction is made **and in cash**."

**This answers open question 1 directly.** *Sarf* is the contract for exchanging one monetary asset
for another (money-for-money), and its conditions are exactly what §30.1 (riba al-fadl) already
derived from Azzad's paper: same-type → equal + hand-to-hand; different-type → any agreed rate +
still hand-to-hand (spot). Two independent sources now name the contract and restate the same two
conditions — **this is now a triangulated, not single-sourced, rule.**

**Does *sarf* govern our USDC↔BTC swap?** Only partially, and the answer sharpens §30.1's note
rather than changing it:
- **USDC↔USD** (our funding rail, USD deposit → USDC) is squarely *sarf*: same monetary function,
  different instruments, parity expected, and Coinbase settles it immediately. This satisfies
  *sarf*'s spot condition cleanly.
- **USDC↔BTC/ETH/PAXG** (the actual trade) is a harder case: BTC/ETH are **not** widely
  classified as *thaman* (money) by these sources — none of the four papers rules on crypto as
  money (see §66.7) — so the trade is better modeled as an ordinary **bay' mutlaq** ("sale of goods
  for money" per Najeeb's Table 2, no prohibition provided the goods aren't haram-sector) than as a
  *sarf* exchange, UNLESS a scholar treats BTC itself as a monetary asset (a live, unresolved
  scholarly question these papers don't engage). **Practically this is moot for us either way**:
  bay' mutlaq permits unequal quantities/rates by design (it's a normal sale), and *sarf*, if it
  applied, would only additionally require equal value for same-type exchanges (N/A, USDC≠BTC) —
  **both readings converge on the same operative requirement that already governs us: the exchange
  must be spot/hand-to-hand, full stop.** The classification question is academically open; the
  compliance rule it would produce either way is one we already have.
- **PAXG** (gold-backed token) is the one edge case worth flagging: gold is a classical *ribawi*
  item under bay' al-sarf (see §66.4's Bay Muqayadah entry — "Barter of Ribawi items: Gold,
  Silver, Wheat, Barley, Dates, and Salt"). If PAXG↔USDC is treated as a gold-for-money exchange
  under *sarf*'s different-type branch, the rate-may-differ/must-be-spot rule applies — again spot
  settlement is the binding requirement, which Coinbase already provides. **No new restriction, but
  worth a `CompliancePolicy` comment**: PAXG trades are the one instrument in our allowlist where a
  *sarf*/ribawi-item classification is not just theoretical (gold is textbook *ribawi*), so if we
  ever consider gold-settled derivatives or PAXG-for-gold-token swaps, re-open this analysis —
  today's PAXG↔USDC spot buys/sells are unaffected.

**Module:** `CompliancePolicy` — add explicit *sarf* terminology (equal+spot for same-type,
any-rate+spot for different-type) as the named contract underlying our funding-rail exchange, and
the PAXG/ribawi-item flag above as a documentation note (no rule change).

### 66.2 ⭐⭐ Qabd (possession) as a precondition to resale — NEW, direct answer to open question 2

**[A] Najeeb, Table 3 ("Prohibited Sale Contracts")** contains the entry that answers this KB's
qabd question head-on:

> **"Sale of Food before Possession"** — "The Prophet (saw) forbade the resale of food items
> before taking actual possession of the items by the seller himself." Source: "In the hadith
> narrated by Ibn Umar, it is reported that Allah's Messenger also forbade sale of goods on the
> spot they are bought."

And earlier in the same section (§4.4 body text, quoting the same hadith more fully): *"Allah's
Apostle forbade the selling of foodstuff before its measuring and transferring into one's
possession. I asked Ibn 'Abbas, 'How is that?' Ibn 'Abbas replied, 'It will be just like selling
money for money, as the foodstuff has not been handed over to the first purchaser who is the
present seller.'"* (Sahih Bukhari, Vol.3, Bk.34, No.342.)

**What this establishes:** you cannot validly resell (or, by extension, exit a position in) an
asset you have not yet taken *qabd* (possession) of. Ibn 'Abbas's own gloss frames the rationale in
exactly *sarf* terms — an unsettled purchase behaves like "money for money" with one leg still
outstanding, which is the same deferred-settlement concern §30.1/§66.1 already flag.

**Does an exchange-held Coinbase balance satisfy qabd?** Neither this paper nor any of the other
three rules on *constructive possession* (qabd hukmi) for custodial/book-entry assets specifically
— this is a genuine gap in the four sources, not a settled answer, and should be logged as such
rather than overstated. What the sources DO establish, combined:
- The hadith's concern is about **reselling before settlement completes**, not about *where* the
  asset is subsequently held. Once a buy order fills and the balance posts to our Coinbase account,
  we have full, immediate, unconditional disposal rights over it (we can sell, withdraw, or
  transfer it at will) — this is the functional core of what classical qabd requires (the classical
  problem case is a debtor/seller who does NOT yet have the goods and is trying to sell them out
  from under an unsettled first transaction).
- Custodial exchange holding is a *different* question — whether Coinbase's custody model
  satisfies qabd in the sense of "the buyer, not a third party, controls the asset" — and **none of
  these four papers addresses digital custodial qabd or crypto qabd at all.** This is consistent
  with §66.7: the crypto-specific fiqh question these sources would need to answer simply isn't in
  scope for any of them.
- **The concrete, actionable rule that DOES follow cleanly:** the agent must not treat a BUY order
  as settled — and therefore must not allow an exit/sell order against that tranche — until the fill
  is confirmed and the balance change is reflected in our own ledger, not merely "order accepted."
  This is a *sarf*/qabd-grounded justification (not just an execution-hygiene one) for a rule the
  per-tranche position ledger work already appears to be building toward (per the recent commits on
  this branch — "per-tranche position ledger," "resting bracket's order id," dead-bracket
  reconciliation). **Recommend the compliance doc cite this hadith as the shariah rationale**
  for requiring confirmed-fill-before-exit-eligible, alongside whatever execution-correctness
  reasons already motivate it.
- **What we do NOT get from these sources:** a ruling that self-custody is required, or that
  Coinbase custody specifically fails qabd. That would need a source that rules on custodial/digital
  possession directly — none of these four do. **Log as an open question for a future source**,
  not as a settled requirement. Document our working position conservatively (per §29.2's
  scholarly-divergence caveat): we treat "unconditional, immediate right of disposal over a
  settled exchange balance" as satisfying qabd for the purpose of this agent's operation, pending
  a source that addresses custodial qabd directly.

**Module:** `CompliancePolicy` — new documented interpretive position (not a new rail): qabd is
satisfied by a *settled, disposal-ready* exchange balance; a BUY tranche must show as settled in
our own ledger before it is exit-eligible. Flag custodial-qabd-versus-self-custody as an
**open scholarly question, unresolved by any source fed so far** — worth a dedicated future source
if one specifically addressing digital-asset/exchange custody qabd can be found.

### 66.3 ⭐ The Didimax case study — refines, doesn't contradict, §56.1's negative exemplar

**[C]** is the most valuable single find in this batch: a live field study of an actual Indonesian
retail forex/commodities broker (PT. Didimax Berjangka — currency, gold, silver, multilateral
commodities), analyzed against two competing regulatory answers.

**66.3a — The T+2 "spot" window is not automatically the §56.1 violation.** Citing the DSN-MUI
fatwa concept directly: *sharf* transactions "must be carried out in cash (spot)... which must be
**handed over at the same time (over the counter) or settlement no later than 2 (two) days**."
This is the same T+2 window §56.1 flagged as damning in *conventional* retail FX — but here a
named Shariah body explicitly treats a T+2 settlement cycle as compatible with "spot," **provided
delivery/transfer of ownership actually occurs** at the end of that window. This **refines rather
than contradicts §56.1**: the problem §56.1 identified was never really the *two days* per se — it
was that the position is **perpetually rolled specifically to avoid delivery ever happening**, on
capital that could never cover actual delivery in the first place. A T+2 settlement cycle that
concludes in real delivery is a different animal from a T+2 cycle that never concludes because it
is rolled indefinitely. **§56.1's core point stands, sharpened:** the defect is *non-delivery by
design*, not the existence of any settlement lag at all. (Not that this matters operationally for
us — Coinbase settles same-transaction, well inside any T+2 allowance — but it corrects a
too-loose reading of §56.1 that would treat ANY T+1/T+2 lag as automatically riba.)

**66.3b — The forward/hedging wa'ad mechanism: a genuine complication, correctly excluded by our
scope, not by oversight.** Didimax's actual mechanism, per the paper: *"the type of transaction
used is a Forward transaction, whose value is determined at present and applied for the future,
between 2×24 hours to one year"* — justified not as an ordinary *sharf* sale but under a **separate
DSN-MUI fatwa on Sharia Hedging (No. 96/DSN-MUI/IV/2015)**, which frames the arrangement as a
**mutual promise (*wa'ad*/*muwa'adah*)** to transact at a future date at an agreed rate, rather
than a binding forward sale contract. The stated rationale is that a *promise* to exchange, unlike
a *sale* of a not-yet-existing exchange, does not itself violate *sarf*'s spot requirement, because
no sale has actually been concluded yet — only a commitment to conclude one later.

→ **This is real fiqh engineering to solve a hedging problem for institutions carrying genuine FX
risk** (the paper is explicit that AAOIFI-style hedging fatwas exist "to increase accelerated
competitiveness in global competition" for Islamic financial institutions needing FX risk
management). **It is not license for us.** Per the task's own framing: a paper concluding
"Islamic forex hedging via *wa'ad* is permissible" is not permission for leverage or
overnight-rollover mechanics, and this construction exists *specifically* to accommodate deferred,
future-dated settlement — precisely what we exclude by design (spot only, no rollover, no forward
positions, no hedging overlay). **Out of scope for `keel`, logged not adopted** — see §66.9.

**66.3c — The competing Malaysian ruling: sharpens the "what does 'no real delivery' actually look
like" question.** The paper also cites Malaysia's Perlis State Fatwa Authority (2016), which
declared *online* retail forex trading wholesale **haram**, for reasons distinct from and sharper
than the rollover/interest critique in §56.1:
1. **"No transfer of property by both parties, either substantively or constructively"** — i.e.,
   the retail platform never actually delivers anything; positions are pure price-difference bets.
2. **Riba** — the platform charges additional fees for cash-loan/margin facilities.
3. **Gharar** — the identity/counterparty of the platform provider is not knowable/verifiable.
4. **Maisir** — traders face profit/loss purely from predicting price direction, no underlying
   economic activity.

→ **This is the sharpest available contrast for validating our own posture.** Coinbase spot trades
DO involve actual transfer of property — our USDC balance decreases, our BTC/ETH/PAXG balance
increases, on a regulated exchange whose counterparty identity is fully known and contractually
defined. The Malaysian fatwa's fatal defect ("no transfer of property... substantively or
constructively") is precisely the qabd/constructive-possession bar discussed in §66.2, and
precisely the bar Coinbase spot execution clears while the retail-CFD-style forex product it
condemns does not. **This is now the KB's clearest documented boundary case for "what would make
our own spot execution fail" — worth citing directly in `CompliancePolicy` docs as the litmus test:
does the trade result in an actual, verifiable transfer of the asset onto our balance sheet, or
merely a cash-settled price difference? If ever the answer becomes the latter (e.g., a
derivative-wrapped "spot-tracking" product), it fails this test regardless of its marketing label.**

**Module:** `CompliancePolicy` — (a) refine the §56.1 "T+2 = automatically riba" framing to the more
precise "T+2-with-real-delivery is a different case from perpetual-rollover-to-avoid-delivery";
(b) log the *wa'ad*-forward hedging construction as an out-of-scope instrument (§66.9); (c) adopt
the Malaysian "substantive-or-constructive transfer of property" test as the operative litmus test
for what "spot" must mean for us, citing it directly.

### 66.4 Contract taxonomy — corroborates existing exclusions, no new rails

**[A] and [B] both survey the same core taxonomy** (permissible: bay muqayadah/barter, bay mutlaq,
bay sarf, bay salam, bay istisna, murabahah; disputed: bay al-inah, bay al-wafa, tawarruq, bay
al-arbun; debt-sale: bay al-dayn, dha' wa ta'ajjal). None of this is new to the KB's compliance
exclusion set (§28–§30 already ground riba/gharar/maisir and the spot mandate), but it is useful as
independent, contract-named corroboration:

- **Bay Salam / Bay Istisna** (deferred-delivery-for-advance-payment / manufacture-to-order) —
  explicitly permitted *as an exception* to the general spot-delivery rule, but conditioned on
  advance full payment and a fixed delivery date for a *tangible, to-be-manufactured or
  agriculturally-deferred* good. **N/A to us**: we trade already-existing, exchange-listed spot
  assets, never commissioned/future goods. No rule change; logged as a confirmed non-analog.
- **Murabahah / BBA (Bay Bithaman Ajil)** — cost-plus-profit sale, often on deferred/installment
  payment. **[A] gives a detailed critique of Islamic banks' actual practice** (banks frequently
  don't take real ownership risk before reselling at markup — "form over substance" per [A]'s own
  §6 critical-issues section) — this is a **banking-product critique, not a trading rule**, out of
  scope for us (we never finance/resell on markup; we buy and hold spot). Logged, not adopted.
- **Bay al-Inah / Tawarruq / Bay al-Wafa** — all disputed liquidity-generating sale-and-buyback
  structures (majority view: impermissible ruse to circumvent riba; some jurisdictions permit).
  **Out of scope**: none of these describe anything our agent does (we never sell-then-repurchase
  the same asset from the same counterparty as a financing device). Logged as reinforcement that
  the "form over substance" scrutiny [A] applies to banks is exactly the scrutiny our own spot
  trades should always be able to pass trivially (real asset, real counterparty, real settlement).
- **Bay al-Dayn / dha' wa ta'ajjal (debt-discounting)** — sale-of-debt-instrument mechanics, the
  underlying fiqh machinery for sukuk. Already out of scope per §28's securitization exclusion.
  No new content.

**Module:** `CompliancePolicy` (reinforcement only, no rule change) — the "form over substance"
critique in [A]'s §6 is worth one documentation line: our spot trades should always be able to
demonstrate real transfer of ownership/risk, the same standard [A] finds many Islamic banking
products failing.

### 66.5 General mu'amalat principles — full reinforcement, zero new content

**[A]'s Table 1** ("General Principles of Mu'amalat Transactions") restates, with fresh Quran/hadith
citations, principles already fully established in §28.1: free mutual consent, prohibition of
gharar (cites the *bay' al-hasat*/pebble-sale and unripe-fruit-sale hadiths), prohibition of riba
(cites Qur'an 2:278–279, 3:130 and the "cursed lender/borrower/scribe/witness" hadith), prohibition
of qimar/maisir, prohibition of khilabah/ghishsh (fraud/deception), prohibition of two mutually
inconsistent contracts in one sale, conformity with maqasid al-shari'ah, and "profits with
liability" (*al-kharaj bi al-daman* — profit is legitimized by bearing the asset's risk, echoing
§31's ghorm-vs-gharar nugget). **[B]'s "Things forbidden in business"** section (haram goods,
gharar, hoarding, fraud, prohibited-use sales, riba) is the same list at lower resolution with a
generic-website citation rather than primary sources. **Fully saturated — no action beyond noting
the extra primary-source citations are available if `CompliancePolicy` documentation ever wants a
denser citation trail.**

### 66.6 Scholarly divergence and regulatory fragmentation — reinforces §29.2, sharpened by a live example

**[A]'s §6** ("Critical Issues and Challenges") explicitly flags the same scholarly-divergence
caveat §29.2 already logged — Islamic finance lacks a unified regulatory/fatwa authority, and the
paper recommends AAOIFI/OIC Fiqh Academy move toward "broader consensus... in order to avoid
inconsistencies in industry practices." **[C] hands this an unusually concrete, live example**: the
*exact same* retail forex product (online currency trading) is ruled **haram** by Malaysia's Perlis
authority and **halal** (via the hedging/*wa'ad* construction) by Indonesia's DSN-MUI, for the
*same underlying activity*. This is the sharpest documented instance of jurisdictional
scholarly divergence in the whole KB so far — a direct, concrete instantiation of the abstract
caveat §29.2 logged from Oracle's white paper. **Reinforces (does not change) the existing
decision to keep `CompliancePolicy` pluggable and document our interpretation as one conservative
reading among plausible others** — now with a citable real-world example of exactly how divergent
two major national authorities can be on materially the same product.

### 66.7 ⭐ No source addresses cryptocurrency directly — a clean negative finding

**None of the four papers mentions Bitcoin, cryptocurrency, blockchain, digital assets, or
stablecoins anywhere.** [A] is 2014, [B] is 2011, [C] is 2024 but scoped entirely to fiat forex
(USD/EUR/GBP/AUD/CHF/JPY/NZD) at a conventional futures/commodities broker, [D]'s surviving content
is one paragraph of introduction. This directly answers open question 3: **these four sources are
silent on crypto-as-*mal*/property and on stablecoin classification.** The KB's existing crypto
compliance reasoning (§30.1's stablecoin-parity note, the `haram_sector` screen) remains
un-supplemented by anything here — it is neither corroborated nor contradicted, simply not
addressed. **Recommend not assuming silence implies permissibility; the crypto-as-mal question
remains genuinely open and would need a source that engages it directly** (e.g., an AAOIFI or OIC
Fiqh Academy resolution on digital assets specifically, several of which exist publicly and could
be a good future single-topic source).

### 66.8 [D] Forex Trading: Conventional and Islamic Perspective — file is broken/truncated

The PDF at `Forex_trading_conventional_and_Islamic_p.pdf` is verified 5 pages total (confirmed via
file metadata): book cover, title page, copyright page, full table of contents (12 chapters of the
2011 IIUM anthology *The Principle of Currency Value System and Islamic Banking and Finance*), and
then **only the opening paragraph of Chapter 3** (the chapter itself is pp.55–73 per the TOC; the
file cuts off after the first paragraph). The one salvageable sentence: forex trading's popularity
is attributed to *"diversification, hedging and leveraged returns"* — a one-line, incidental
confirmation that mainstream retail forex is marketed on exactly the leverage/hedging/rollover
axis this KB already excludes wholesale (§4.9, §10.10, §18, §28.1). **No new content. Do not
re-request this file** — if the actual chapter content (pp.56–73) is wanted, a different/complete
copy of the source PDF would be needed; the file as supplied cannot yield more.

### 66.9 Out of scope

- **The Didimax *wa'ad*-forward hedging mechanism** (§66.3b) — a fiqh-engineered permissibility for
  institutional FX hedging via mutual promise, explicitly not a general endorsement of forward/
  rollover forex trading. We do not hedge, do not take forward positions, and do not use promise-
  based deferred-settlement instruments. Logged as understood, not adopted.
- **Murabahah / BBA / diminishing-partnership (Musharakah Mutanaqisah) home financing** ([A] §4.5,
  [B] §9.1–9.2) — bank financing products, N/A to a spot trading agent.
- **Bay al-Inah / Bay al-Wafa / Tawarruq / Bay al-Arbun** ([A] §4.6, [B] §9.3–9.4) — disputed
  liquidity/financing structures, N/A (we never sell-then-repurchase or use down-payment-forfeiture
  structures).
- **Bay al-Dayn / sukuk debt-securitization / dha' wa ta'ajjal debt-discounting** ([A] §5) — already
  out of scope per §28's securitization exclusion; restated here, not re-adopted.
- **Ijarah / Ujr / Salam / Istisna as financing instruments** ([B] §9.5–9.8) — lease/wage/
  manufacture-order contracts; N/A to spot crypto trading (no leasing, no wage contracts, no
  commissioned-manufacture goods).
- **PT. Didimax's broker operations, licensing, training-program marketing, BAPPEBTI regulatory
  detail** ([C]) — Indonesian futures-broker business detail, N/A to our Coinbase-based operation.

### 66.10 Discarded (no agent value)

Book cover/title/copyright/TOC pages and reference lists from all four PDFs (citation
bibliographies, none independently useful beyond what's already cited above); [A]'s §6 "Education
and Awareness," "Regulatory Frameworks," "Appropriate Benchmarks," and "Human Resources" subsections
(industry-development commentary, no agent-actionable content beyond the §66.6 divergence point
already extracted); [A]'s acknowledgements and full reference list (66 works cited, classical fiqh
texts — useful only if a future source needs a specific primary citation, not independently
extractable); [B]'s "Business Ethics in Islam" preamble (trust/justice/honesty/mutual-respect
platitudes with generic-website citations, restates nothing beyond [A]'s Table 1 at far lower
resolution); [B]'s "Concept of Shariah" and "School of Islamic Law" historical sections (Hanafi/
Maliki/Shafi'i/Hanbali founding dates — general-knowledge background, no rule content); [C]'s
interview-methodology section, PT. Didimax's award history and branch-expansion plans, and the
"General Overview of Forex Trading Implementation Flow" registration/deposit/trade diagram
(broker-operations marketing, N/A); [C]'s full reference list (mostly Indonesian-language theses
on unrelated fintech/accounting topics); [D]'s cover/title/copyright/TOC (all that survives of the
file besides one paragraph, see §66.8).

---

### Net assessment (saturation-honest)

**Compliance dimension advances meaningfully, does not merely restate.** Unlike some prior
compliance sources that were pure reinforcement (§31, §32), this batch answers two of this KB's
genuinely open questions with real content: **bay' al-sarf is now a named, triangulated contract**
(§66.1) governing our funding-rail exchange and clarifying (without changing) how PAXG's
gold-*ribawi* status should be watched; and **qabd is now grounded in an explicit hadith**
(§66.2) that justifies — on shariah rather than purely operational grounds — treating a
BUY tranche as exit-ineligible until settlement is confirmed in our own ledger. The Didimax case
study (§66.3) is the single most valuable item: it refines §56.1's T+2 framing (delivery-concluding
lag ≠ perpetual non-delivery rollover), gives a citable litmus test for "real spot" (substantive-or-
constructive transfer of property, per the Malaysian fatwa's own condemnation of the alternative),
and shows a live case of the exact fiqh-engineering move (*wa'ad*-forward hedging) that a scope
statement must explicitly exclude rather than accidentally inherit. **Crypto remains genuinely
unaddressed** by all four sources (§66.7) — this is a documented gap, not a resolved question, and
is worth a dedicated future source (an AAOIFI/OIC resolution on digital assets specifically) rather
than assuming silence is permission. [D] is a dead end (§66.8); do not re-request.

**Recommendation:** the compliance stream can reasonably pause again after this batch on
*general* Islamic-finance grounds (contract taxonomy is now thoroughly saturated across five
sources: §28–30, this one), but **a single, targeted source on crypto-as-mal / digital-asset qabd
would close the one real gap this batch surfaced** and should be prioritized over further general
trading-in-Islam surveys.
