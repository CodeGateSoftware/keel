[← Knowledge Base index](../README.md)

## Source 67 — TWO PDFs: (A) "Handbook of Islamic Finance" (Dr. Mabid Ali Al-Jarhi, Dr. Abdulazeem Abuzaid, Dr. Adnan Oweida; ASBÜ Yayınları / Ankara Social Sciences University, April 2022, ISBN 978-605-71422-2-1, 322pp — filename `adnan-islami finans-41a_1.pdf`) and (B) "How to Trade Cryptocurrencies with Deriv" (Vince Stanzione, Deriv ebook, 56pp — filename `Deve.pdf`)

> **PDF A identified: an English-language Islamic-banking-products directory, NOT Turkish despite the
> filename/publisher.** The filename ("adnan-islami finans") and publisher (a Turkish university press)
> suggested a Turkish text; the book itself is written entirely in English by an international author
> team (Al-Jarhi is a well-known Islamic-economics scholar; Oweida is on the editorial board and an
> assistant professor at the publishing university). No translation-reliability caveat applies — every
> passage cited below was read directly, not machine-translated. It is a **product-by-product Shariah
> directory for Islamic banks** (Musharaka, Mudaraba, Muzara'a, Istisna', Salam, Murabaha, Ijarah,
> deposits, shares, sukuk, payment cards, derivatives) — most of it is banking/financing machinery this
> spot-only agent has no use for, per the README's own guidance that general Islamic-finance banking
> content is out of scope. But it was read **targeted** (TOC-driven, not linear) specifically against the
> two open questions flagged for this pass — **bay' al-sarf** and **qabd/constructive possession** — and
> both landed genuinely new, load-bearing answers (§67.1, §67.2), plus one sharpening of an already-
> actioned obligation (§67.4).
>
> **PDF B identified as a duplicate, not a new source.** "Deve.pdf" is an uninformatively-named copy of
> the exact same ebook already extracted in full as **Source 23** ("How to Trade Cryptocurrencies with
> Deriv", Vince Stanzione) — same author, same title, same 56pp, same TOC, same worked examples (LTC/USD
> Donchian breakout, Monero RSI walkthrough, the 20/20 SMA-in-channel variant). Confirmed by direct text
> diff, not just title match — see §67.6. **Not re-extracted; logged as duplicate only.**

---

### Reading method (PDF A — targeted, not linear)
Converted to text (`pdftotext -layout`) and grepped for `sarf`, `qabd`/`constructive possession`,
`ribawi`/`same genre`/`hand to hand`, `crypto`/`bitcoin`/`digital currency` (zero hits — the book predates
or simply never engages crypto), then read the TOC to route to on-topic chapters. **Read densely:**
Ch1 (Shariah justifications — riba fundamentals, for the classical six-ribawi-items hadith), Ch16/17
(Murabaha/Deferred Payment Sale — qabd doctrine, Sarf-contract carve-out for cash/currency/gold), Ch22
(Bank Deposits — current-account-as-qard classification + general qabd rule for fund transfers), Ch23
(Shares — the Ghalaba/majority tradability standard, which cites the same Sarf provision), Ch25 (Payment
Cards — the constructive-possession-via-electronic-recording resolution), Ch26/27 (Controversial Products
+ Derivatives). **Skipped as out-of-scope banking machinery** (skimmed TOC/openers only, no dense read):
Ch2–Ch15 (economic-advantages essay, Musharaka/Mudaraba/Muzara'a/land-reclamation/Muqharasa/Wakala
bel Istithmar/Istisna'/Salam — profit-sharing and manufacturing/agriculture financing contracts with no
spot-crypto analog), Ch18–Ch20 (Ijarah/leasing variants — N/A, we don't lease), Ch24 (Sukuk — bond-like
securitized debt instruments, riba/gharar-adjacent and already excluded via §18/§56), Appendices 1–4
(feasibility-study template, two verbatim OIC Fiqh Academy resolution reprints on leasing-bonds and
muqarada-bonds, a full Murabaha contract template, references) — administrative/templates, no rule
content.

---

### 67.1 ⭐⭐ Constructive possession (qabd) via electronic recording IS sufficient — direct answer to the open `qabd` question

> *"It is not required for the possession to be by hand or to transfer the goods to the buyer's
> warehouses. Rather, the mere enabling of the buyer by the seller to take delivery of the asset is
> considered a possession taking, which is known as constructive possession. Likewise, Fiqh Academy has
> issued a resolution accepting the constructive possession and considering the prevailing custom as a
> reference in this regard."* (Ch16, Murabaha)

And, more directly on point — a dedicated passage on buying gold/silver/currency **electronically**:

> *"Shariah requires that both counter values be handed over when buying gold, silver, or currencies with
> cash. However, **constructive possession through electronic recording of the transaction for the
> account of the recipient is sufficient in this regard**, as per the resolution of Fiqh Academy [Resolution
> No. 53 (4/6)]. Based on this, the permissibility of purchasing gold, silver, or currency with cards should
> depend on the seller receiving the money in his account."* (Ch25, Payment Cards)

The same chapter goes on to flag the genuinely hard part of this question, which is directly analogous to
our situation: card-based settlement of a gold/silver/currency purchase typically takes **2–7 days** to
actually land in the seller's account, and the author explicitly debates whether that delay is compatible
with "spot." Their conclusion: **the controlling question is not physical possession or which account the
money is deducted from, but whether the counter-value has actually reached the recipient's account** —
*"the difference between a debit and credit card... has no Shariah bearing... it relates to whether or not
the money has entered the seller's account."*

**Why this matters for `keel` — direct answer, not an inference:** we hold BTC/ETH/PAXG in custody at
Coinbase rather than in a self-custody wallet. That is exactly the fact pattern this passage addresses —
constructive possession via an electronic account record, not physical/hand-to-hand delivery. The
authority cited (OIC Fiqh Academy, the same body §29/§30 already treat as authoritative for this KB) holds
that **electronic constructive possession satisfies qabd**, provided the counter-value has genuinely and
promptly landed in the account of the party entitled to it. Coinbase settles spot trades **instantly**
(the underlying asset shows in the account the moment the trade executes) — which is a *stronger* case
than the 2–7 day card-settlement scenario this text treats as the live edge case, and comfortably clears
the bar this source actually sets. **This resolves the qabd open question in `keel`'s favor**, with the
caveat that the resolution is scoped to *prompt, genuine* account crediting — a custodian that delayed or
merely promised future crediting would not qualify, which is a useful test to keep in mind if custody
arrangements ever change (e.g., a broker showing a balance without actually holding/crediting the asset).

→ **Action:** `CompliancePolicy` design-spec documentation can now cite this affirmatively (OIC Fiqh
Academy Resolution No. 53 (4/6), as reproduced in this Handbook) as the qabd/constructive-possession
grounding for custodial spot holding at a regulated exchange, alongside the existing §30.1 spot-settlement
grounding. No rule/rail change — this is a documentation-and-confidence item, closing a previously open
judgment call.

### 67.2 ⭐ Sarf contract explicitly covers GOLD, not just currency — extends §30.1 to PAXG

Two passages independently confirm that gold/silver sales are governed by the same Sarf provisions as
currency exchange (spot + equal value if same genre):

> *"It is not permissible to sell cash, currencies, gold, or silver on deferred payment basis, because
> these items are subject to the provisions of Sarf contract; i.e. they have to be exchanged on the spot
> unless they are sold against other items, such as wheat, as in selling gold for wheat."* (Ch17, Deferred
> Payment Sale)

> *"The primary activity of the company must not be money exchanges or sale of gold and silver, because
> these sales are subject to the provisions of Sarf in Islamic law, where spot payment of both counter
> values is required, in addition to equality of their values when they are of the same genre."* (Ch23,
> Shares — the stock-screening chapter)

**Why this is new, not just reinforcement:** §30.1 (Azzad riba paper, already in the KB) grounds our
spot-settlement mandate in riba al-fadl/al-nasee'ah for **currency** exchange. Nothing prior in the KB
states — as explicitly as this — that **gold sales sit under the identical Sarf umbrella**. That matters
concretely because `keel` trades **PAXG** (gold-backed token), not just BTC/ETH. This passage means the
USDC→PAXG (and PAXG→USDC) leg is, in classical terms, a Sarf-type transaction requiring spot settlement —
which Coinbase's instant execution already satisfies — rather than an ordinary commodity sale where
deferred payment would be unremarkable. Combined with §67.1 (electronic constructive possession
suffices), this gives PAXG the **same clean compliance grounding BTC/ETH already had under §30.1**,
closing a gap the KB had never explicitly addressed for the one asset in the allowlist that is not a pure
cryptocurrency.

→ **Action:** none required (Coinbase spot execution already satisfies both prongs) — but worth adding to
`CompliancePolicy` documentation as the explicit PAXG-specific citation, since §30.1's original wording is
currency-only and a reviewer could otherwise ask "does the currency-exchange grounding even apply to a
gold token?"

### 67.3 The Ghalaba (majority/51%) standard for mixed-asset tradability — logged as a reference, not adopted

Ch23 (Shares) and the Sukuk chapter both describe a **numeric screening standard** for when a security
backed by a mix of cash/debt/tangible assets may be freely traded: the **Ghalaba (majority) standard**,
which Fiqh Academy adopts over rival 10%/30% thresholds used by some other bodies —

> *"the underlying non-negotiable assets of a stock can be treated similar to the tradable assets if...
> the percentage of the negotiable [tangible] assets is no less than 51%... below 50%... has always been
> deemed a subordinate and never given an independent status."*

This is a genuinely different kind of Shariah tool than anything else in the KB — a **quantitative
composition test** applied to a security's underlying assets, rather than a binary sector/activity screen
(our `haram_sector` allowlist test, §28.4/§41.1). It doesn't map onto anything `keel` needs today: BTC and
ETH are not asset-backed securities with a mixed balance sheet, and PAXG's backing is (by design) ~100%
allocated physical gold, not a mixed pool. **Not adopted as a rule.** Logging it only because it is the
closest thing in the KB to a template should a future allowlist candidate ever be a **reserve-backed
token whose backing composition is mixed or opaque** (e.g., a stablecoin whose reserve includes
interest-bearing instruments) — the Ghalaba 51% test is the citable precedent for "how much haram
backing is enough to taint the whole instrument," which our current binary `haram_sector` screen doesn't
address. Flag as a **future consideration**, not a current gap: `keel` doesn't currently apply the
allowlist screen to stablecoin reserve composition at all (USDC is treated as the riba-free base currency
by construction), and this source doesn't give us enough to decide whether it should.

### 67.4 ⭐ Sharpens §56.3 (idle-USDC-interest obligation) — explicit criteria for gift vs. disguised riba

Ch22 (Bank Deposits) classifies a bank current account as a **loan (qard)** from depositor to bank
(because the bank guarantees repayment) and then states the exact test for when a balance-linked payment
crosses from permissible gift into riba:

> *"The borrowing bank cannot commit to paying a return on these deposits, because a stipulated return on
> a loan is nothing but Riba. The bank may grant current account holders cash increases or in-kind gifts
> as long as these are donations and are not guaranteed... However, it is recommended **not to distribute
> these donations regularly, fix them in value, or link them to the volume of the amounts deposited.**"*

§56.3 (Source 56) already identified that Coinbase's USDC rewards on idle balances are a **live, account-
level riba risk** for `keel` and actioned disabling them. This source sharpens *why* that call is correct
rather than a conservative-but-debatable stance: our USDC balance on Coinbase is functionally a current
account (a guaranteed-repayment loan to the custodian), and the Handbook's own three-part test for
disguised riba — **regular** payment, **fixed** value/rate, **linked to balance volume** — is precisely
how APY-style USDC rewards are structured (a recurring, rate-proportional-to-balance payment), not the
occasional, non-guaranteed, non-formulaic "gift" the Shariah carve-out actually permits. This closes the
one soft spot in §56.3's reasoning (it argued from a forex-book author's plain-language account of
"interest on idle balances"; this source gives the same conclusion from an authoritative Islamic-finance
directory with an explicit legal test).

→ **Action:** none beyond what §56.3 already actioned (`CompliancePolicy` account-obligations surface,
operator-attested since Advanced Trade exposes no rewards/interest endpoint) — this is corroboration that
sharpens the audit-trail rationale, not a new obligation.

### 67.5 Derivatives (Ch26–27) — full reinforcement, no new rule; useful legal texture on WHY

Ch26 ("Controversial Financial Products") and Ch27 ("Issues in Justifying Derivatives") give a detailed
Shariah appraisal of futures/options/swaps, concluding all three are impermissible: deferred counter-
values with uncertainty (gharar) in one or both, riba if the counter-values are currencies, and — for
options specifically — selling a bare right/commitment that isn't itself a tradable property under
Shariah. One sharpening worth noting: the authors explicitly reject the idea that the problem is merely
*settlement-failure risk* (which modern clearing houses have engineered away) — *"the big problem is in
the content of these transactions that may not differ from betting and gambling."* i.e. **gharar/maisir
here is about the economic substance (zero-sum price speculation on a right, not an asset), not
counterparty risk** — a cleaner statement of why "but the exchange guarantees settlement" is not a
rebuttal. Also covered: several Islamized-derivative workarounds (Tawarruq/Eina-based swaps, binding
Promise as a contract-substitute, 'Urbun-as-option) are each individually rejected — logged only as
evidence the KB's existing derivatives exclusion (§28.1–28.2, §42–49) is not a shortcut the industry
itself has found a way around either.

→ No action — §28/§42–49 already hard-exclude derivatives; this adds legal depth, not a new rail.

### 67.6 PDF B — "How to Trade Cryptocurrencies with Deriv" is a duplicate of Source 23, confirmed by text diff

`Deve.pdf` converts to the identical book already fully extracted as **Source 23**: same author (Vince
Stanzione), same title, same 56pp, same table of contents (Introduction → Types of cryptocurrency →
Basics of trading cryptocurrency → CFD order types → How to evaluate a cryptocurrency → Final note →
Appendices → Glossary), and — checked directly, not just inferred from matching titles — the **same
worked examples**: the Litecoin/USD 20-minute Donchian-channel breakout system, the Monero (XMR/USD) RSI
walkthrough, the ETH/USD 20-day SMA example, and the identical "20/20" Donchian-plus-SMA variant Source
23 logged at §23.1. The copyright/publication stamp on this copy reads 2026, but that is consistent with
Deriv's PDF generator re-stamping a current date on each download rather than evidence of a substantively
revised edition — the content, structure, and even the specific illustrative assets/prices match Source
23's extraction exactly.

**Not re-extracted.** See [source-23](./source-23.md) for the full extraction (§23.1 Donchian breakout —
the KB's canonical crypto-native trend-following lead; §23.2 channel-low trail; §23.3 RSI param sweep;
§23.4–23.7 reinforcements; §23.8 CFD/leverage/short/swap exclusions).

---

### Halal / out-of-scope exclusions (PDF A)

- **All profit-sharing and financing-contract chapters** (Musharaka, Mudaraba, Muzara'a, land-reclamation/
  Muqharasa, Wakala bel Istithmar, Istisna', Salam, Murabaha, Deferred Payment Sale, Ijarah in all its
  forms) — these are **bank financing/leasing instruments**. `keel` neither borrows, lends, leases, nor
  manufactures-to-order; it buys and holds spot assets with its own cash. N/A by instrument, not by riba
  (most of these are themselves the *halal alternative* to conventional lending — just not a shape our
  agent needs).
- **Sukuk (Ch24)** — asset-backed/asset-based securitized paper. Bond-like in tradability mechanics
  (subject to the same debt-trading restrictions as conventional bonds when the underlying is
  predominantly cash/debt); already excluded in spirit by the existing bonds/fixed-income exclusion
  (README "Explicit exclusions" section). Not a `keel` instrument.
- **Payment-card mechanics generally** (fee structures, credit-card Tawarruq/Eina prohibition, Islamic
  pawnbroking) — retail-banking product design, N/A to a trading agent that doesn't issue cards or extend
  credit.
- **Eina and Tawarruq financing** (Ch26) — riba-circumvention structures used to disguise cash lending as
  a pair of sales. Genuinely interesting as *the* canonical "sneaky riba" pattern in Islamic banking
  literature, but N/A: `keel` never does deferred-sale-based financing of any kind, so there is no
  transaction shape here for the pattern to apply to. Logged as excluded-by-instrument, not extracted
  further.
- **Derivatives (Ch26–27)** — see §67.5; reinforces the existing hard exclusion, no new rule.

### Discarded (no agent value)

**PDF A:** cover page, publisher/ISBN/editorial-board front matter, acknowledgments; the Introduction's
methodology statement; Ch1's classical riba-is-a-major-sin Quran/hadith citations (already fully
established at §28–§30, this source adds nothing beyond the same six-ribawi-items hadith already on file);
Ch2 (Economic Advantages of Islamic Finance — a general macro/development essay, no agent-actionable
content); the detailed procedural/documentary mechanics of Murabaha implementation (LPO documents, mortgage
registration, installment-rescheduling paperwork) — bank back-office process, not a Shariah rule; Ch25's
non-gold-related payment-card provisions (subscription fees, card-blocking for unlawful merchants, default
insurance); all four Appendices (feasibility-study template, two verbatim OIC Fiqh Academy resolution
reprints on leasing-bonds/muqarada-bonds, a full Murabaha contract template) — legal/administrative
templates with no rule content; the References list.

**PDF B:** none beyond what Source 23 already logged at §23.9 (Deriv/MT5 platform promos, affiliate
pitch, glossary, news-source list, Stock-to-Flow mention, synthetic-indices cross-sell) — this is the same
document.

---

### Net assessment (saturation-honest)

**PDF A earned its place narrowly but genuinely** — of 322pp, roughly 15 were read densely and they
delivered on the two specific open questions this pass was tasked with: **§67.1 answers qabd**
(electronic constructive possession, via a custodial account that promptly and genuinely credits the
asset, satisfies possession — Coinbase's instant spot settlement clears this comfortably) and **§67.2
extends §30.1's Sarf/spot-settlement grounding explicitly to gold**, closing a citation gap for PAXG
specifically. §67.4 sharpens (does not change) the already-actioned §56.3 idle-USDC obligation with an
explicit gift-vs-riba test. §67.3 (the Ghalaba majority standard) is logged as a reference for a
hypothetical future question (mixed-backing token screening) we don't currently face. Everything else —
the bulk of the book — is banking-product machinery with no spot-crypto agent surface, exactly as the
README's out-of-scope guidance for general Islamic-finance texts predicts.

**PDF B is not a new source** — it is a duplicate download of Source 23, confirmed by matching worked
examples, not just title. No re-extraction performed.

**Recommendation:** the compliance stream (§28–§33, §56, now §67) has answered every open judgment call
this KB had flagged for it (bay' al-sarf for currency **and** gold; qabd for custodial holding). Recommend
treating Islamic-finance compliance grounding as **closed** absent a genuinely new instrument-type question
(e.g., staking, DeFi yield, or a new allowlist asset with non-obvious backing). Do not feed further general
Islamic-banking-products texts — this source already demonstrates steeply diminishing returns (307 of
322pp skipped as out-of-scope). Do not feed further copies of Stanzione/Deriv titles — verify by content,
not just filename, before spending a read on an "unidentified" PDF that turns out to be a re-download.
