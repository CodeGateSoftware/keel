[← Knowledge Base index](../README.md)

## Source 65 — "Understanding Islamic Finance" (Muhammad Ayub, Wiley Finance, 2007, 544pp)

> **The canonical Islamic-finance textbook — and the compliance stream's new FOUNDATION source,
> displacing §28 (Jobst/IMF) in that role.** §28 is a 37pp IMF working paper that *summarises* the
> prohibitions in two pages; Ayub is a 544pp treatise that *derives* them from the primary texts,
> names the juristic tests, cites the OIC Fiqh Academy and AAOIFI resolutions, and — critically —
> states the **rules of exchange** (`bay' al-sarf`) and **possession** (`qabd`) that §28 never
> touches. Those two chapters are why this source matters to us at all.
>
> **The brief was: do not re-derive the exclusion set — stress-test it.** The result is unusually
> two-sided. This book **confirms** every rail we hold (§65.1, §65.11), but it also shows that
> **three of our stated positions are loose or wrong in their framing** (§65.2, §65.6, §65.10),
> answers the **`qabd`/exchange-custody question in our favour with conditions attached** (§65.4),
> and imposes a **genuinely stricter settlement regime on PAXG than on BTC** (§65.5). It also
> yields **one new account-level obligation on the §56.3 bar — income purification** (§65.9).
>
> **Use:** this is now the primary citable authority behind `keel`'s **CompliancePolicy**. Where
> §28 and §65 both cover a point, cite §65 — it is the more specific and more authoritative text.

---

### 65.0 Method — what was read densely, what was skipped, and why

544pp; ~70% of the book is Islamic *banking product* mechanics that a spot trading agent never
touches. Read **densely**:

| Chapter | Pages | Why |
|---|---|---|
| **Ch 3** — The Main Prohibitions (riba / gharar / maisir) | 43–71 | The grounding for every rail |
| **Ch 4** — Philosophy & Features, esp. **4.5 Exchange Rules**, 4.6 Time Value, **4.7 Money / Trading in Currencies** | 73–96 | `bay' al-sarf`, the `'illah` test, status of fiat as *thaman* |
| **Ch 5** — Islamic Law of Contracts, esp. 5.2 (*māl*/ownership), 5.4.2 (subject matter), **5.5.2–5.5.5** (validity rules) | 101–127 | Price-determinacy, contingent contracts, the speculation ruling |
| **Ch 6** — Trading in Islamic Commercial Law, esp. **6.5.1 (possession)**, 6.6 (riba in sales), 6.7 (gharar) | 129–152 | **`qabd` — the sharpest open question** |
| **Ch 8.8** — Islamic Financial Markets (8.8.2 stock screening, 8.8.4 tradability, 8.8.7 FX, **8.8.8 Derivatives**) | 199–211 | Screening criteria + the derivatives verdict |
| **Ch 14.4.5** — Forward Contracts & FX Dealings | 375–377 | **The five approved forms of constructive possession** |

**Skipped or skimmed for keyword hits only** (out of scope — institutional product machinery, see
§65.15): Ch 1–2 (Islamic economics as a system), Ch 7 (loans/debt — read only for the riba
definition), Ch 9 (Murabaha), Ch 10 (Salam/Istisna'a — read only where it rules on currencies and
gold), Ch 11 (Ijarah), Ch 12 (Musharakah/Mudarabah), Ch 13 (accessory contracts), Ch 14 (bank
financing practice), Ch 15–17 (sukuk, takaful, accounting/governance/regulation), Ch 18–19
(industry outlook). That is ~370 of the 544 pages. Nothing in them is a trading rule.

---

### 65.1 The prohibition set, canonically restated → CONFIRMS §28.1, and corrects its symmetry

Ayub's Ch 3 gives the same three prohibitions §28.1 gave us, with the primary-text derivations. On
the substance there is **no disagreement whatsoever** — every exclusion we enforce is textbook-
grounded, now from a source that spends 28 pages where §28 spent two:

- **Riba** — *"any increase over and above the principal amount payable in a contract obligation,
  **not covered by a corresponding increase in labour, commodity, risk or expertise**."* The
  liability-and-risk clause is what legitimises trading profit and excludes financing cost.
- **Gharar** — *"the uncertainty or hazard caused by lack of clarity regarding the subject matter or
  the price."*
- **Maisir/Qimār** — *"wishing something valuable with ease and without paying an equivalent
  compensation ('Iwad) for it or without working for it, or without undertaking any liability
  against it, by way of a game of chance."*

⚠️ **But it corrects a framing we have carried since §28: the three are NOT symmetric.**

> *"**While the slightest involvement of Riba makes a transaction non-Sharī'ah-compliant**, some
> degree of Gharar in the sense of uncertainty is acceptable in the Islamic structure of business
> and finance."* Scholars distinguish **Gharar-e-Kathir** (excessive → prohibited) from **Gharar
> Qalil** (nominal → tolerated). *"Accordingly, Gharar is considered to be of less significance
> than Riba."*

→ **Riba is strict-liability; gharar is a threshold test.** Our README treats "riba/gharar/maisir"
as one undifferentiated trio. That is fine for the *outputs* (every instrument we exclude fails on
multiple grounds anyway), but it is the wrong mental model for **new** questions. The correct
posture, and what `CompliancePolicy` documentation should say:

- **Any** riba, however small, voids — so the USDC-rewards obligation (§56.3) admits **no** de
  minimis and neither does anything else riba-flavoured. This *hardens* §56.3.
- Gharar is judged by **materiality**. Ordinary market price risk is explicitly **not** gharar:
  *"The uncertainty leads to risk but **all risks are not Gharar, because business risk is not only
  a part of life but also a valid requirement for taking a return in exchanges**."* This is the
  textbook answer to "isn't a volatile crypto position gharar?" — **no**, and we should stop being
  defensive about it. Gharar attaches to *contractual* indeterminacy (unknown subject, unknown
  price, undeliverable object), not to price volatility.

Ayub also independently supplies §31's *al-ghunm bil ghurm* rationale (*"profit goes with loss"*)
and §28.2's ownership principle, so those are corroborated rather than new.

### 65.2 ⚠️ The `'illah` test — §30.1 is stated TOO LOOSELY, and here is the correct rule

**Our current statement (§30.1, carried into the README module map):** *"spot/immediate settlement
mandatory (deferred same-commodity/currency exchange = riba)."*

That is directionally right but juristically imprecise, and the imprecision matters. The actual
rule, from Imam Nawavi via Ayub (Ch 3 and again Ch 4.5) — a **three-branch test on the `'illah`**
(effective cause) of the two things being exchanged:

| Case | Excess/shortfall | Deferment |
|---|---|---|
| **`'Illah` differs** (e.g. gold for wheat, dollars for a car) | ✅ permitted | ✅ permitted |
| **Same genus** (gold↔gold, dollars↔dollars, wheat↔wheat) | ⛔ prohibited | ⛔ prohibited |
| **`'Illah` same, genus differs** (gold↔silver, USD↔JPY, wheat↔rice) | ✅ permitted | ⛔ **prohibited** |

> *"In the present scenario, the major 'Illah… on the basis of which one may extend the rules of Riba
> to other commodities by analogy is **their being used in lieu of money**. There is **consensus
> among scholars that the rules of Riba apply to anything that serves the function of money**. This
> may be gold, silver, any paper currency or IOUs."*

→ **What this changes for us.** The immediacy requirement is **not** a blanket rule over all our
trades — it is a rule that **fires when the `'illah` is monetary on both sides**. Restating §30.1
correctly:

- **USDC ↔ BTC.** If BTC bears *thamaniyyah* (monetary `'illah`), this is row 3: **excess allowed,
  deferment prohibited** — `bay' al-sarf` governs (§65.3). If BTC is treated as a commodity/asset,
  it is row 1 and *even deferment* would technically be permissible. **The conservative branch is
  row 3, and we should adopt it** — it is strictly the tighter constraint, and we already satisfy
  it. **Nothing to build; a documentation correction.**
- **USDC ↔ PAXG.** Row 3 with **no ambiguity at all**, because gold is a named *ribawi* commodity
  and explicitly *thaman*. See §65.5 — this is where a real new constraint lives.
- **USDC ↔ USD** (if we ever hold both legs). Row 2: **par only, and hand to hand**. Already
  satisfied by rail 13; §30.1's "stablecoin parity" note is confirmed exactly.

→ ⚠️ **A second looseness worth fixing:** the README says deferred exchange is riba *full stop*.
Ayub is more precise — deferment in a *same-'illah* exchange is **Riba Al-Nasiah**; unequal
quantity in a hand-to-hand *same-genus* exchange is **Riba Al-Fadl**. §30.1 introduced *al-fadl* to
us but attached it to the settlement question, where *al-nasee'ah* is actually the operative one.
Cosmetic for our behaviour, but the compliance doc should get the names right — a reviewer who
knows the field will notice.

### 65.3 `bay' al-sarf` — does it govern our USDC↔BTC swaps? → `CompliancePolicy`

**Yes, on the conservative reading, and we already comply.** Ayub, Ch 4.5:

> *"Gold, silver or any monetary units (Athman) are subject to the rules of **Bai' al Sarf**, i.e.
> **equal for equal and hand to hand** in the case of homogeneous currency, and **hand to hand** in
> the case of different units of currency being exchanged."*

And Ch 4.7.1, on fiat — the ruling that makes *sarf* reach USDC at all:

> *"Paper money is subject to all the tenets of Sharī'ah relating to Riba, debts, Zakat, etc. One
> cannot sell a 10 dollar bill for 11 dollars because the bill represents pure money and has no
> intrinsic value."* The Islamic Fiqh Council of the OIC (3rd session, 1986) *"resolved that paper
> money was **real money**, possessing all the characteristics of value, and subject to Sharī'ah
> rules governing gold and silver."*

**What `sarf` requires beyond what we already do — the honest answer: nothing operational.** The
three requirements are (i) simultaneity, (ii) equality *only* where the genus is identical, and
(iii) **no deferment clause** — *"The exchange would be simultaneous without any deferment clause
regarding the delivery of one or both counter values."* Coinbase spot fills settle both legs
atomically at fill; we hold no forwards, no margin, no settlement window. Row 3 is satisfied by
construction.

**Two things `sarf` does forbid, that we should record as standing exclusions** (neither is new
behaviour, both are now specifically named):

1. **No forward/date-fixed exchange, and no *promise* to exchange at a future fixed rate for
   speculative purposes.** OIC Fiqh Council, 11th session (1998): *"It is not permissible in
   Sharī'ah to sell currencies by deferred sale, and it is **not permissible, still, to fix a date
   for exchanging them**."* Forward cover is permitted only for *"genuine trade or payment
   transactions… supported by appropriate documents so as to prevent forward cover for speculative
   purposes"* (Ch 8.8.7). We have no such use case — **do not ever add one.**
2. **No `sarf` on a deferred-payment basis** — which is why *murabaha* on currencies/gold is
   invalid (Ch 9). Irrelevant to us, but it is the rule that kills any "buy now, settle later"
   product a broker might one day offer.

⚠️ **The one genuinely awkward passage** — Ayub Ch 4.7.2 says *"money (of the same denomination) is
not held to be the subject matter of trade, like other commodities. Its use has been restricted to
its basic purpose, i.e. to act as a medium of exchange and a measure of value."* Read maximally,
this could be turned into "buying BTC in order to sell it higher is trading *in money*, which is
not money's permitted purpose." **§65.6 resolves this** — and it resolves it in our favour. But
the tension is real and the resolution should be written down, not left implicit, because it is
exactly the objection a careful reviewer raises.

### 65.4 ⭐⭐ `qabd` / possession — exchange-held balances DO constitute valid possession, with conditions → `CompliancePolicy` + `execution/guards.py`

**This was the sharpest open question and the book answers it directly and favourably.** Two
independent passages, one juristic and one from a modern OIC/AAOIFI-derived rule set.

**(a) The juristic doctrine (Ch 6.5.1).** The prohibition *"do not sell what you do not possess"*
is **not** a physical-custody rule. Ayub: *"scholars contend that what is meant by possession here
is **the inability to deliver the goods**."*

> *"Many other jurists, including the Hanafis, have contended that for a lawful sale transaction, it
> is sufficient that the item of sale must be present and fully known… and that **physical
> possession is not a necessary condition of a valid sale**."*
>
> *"…delivery of the sale item on the part of the vendor is completed when **he sets it aside for
> the vendee and there is nothing to prevent the buyer from taking physical possession from the
> vendor whenever he desires**."*
>
> Worked example: a car left in a garage *"where A has free access and A is allowed to take
> delivery, real or constructive, from that place whenever he wishes, the car is in the
> **constructive possession** of A."* And: *"as the purchaser has taken the liability of the risk,
> he is considered the owner of the commodity, although the asset/commodity is still in the godown
> of the seller **or even in any other country**."*

**The two-part test for valid `qabd` is therefore: (i) the buyer bears the risk and reward, and
(ii) nothing prevents the buyer from taking delivery whenever he wishes.**

**(b) The modern rule set (Ch 14.4.5).** For *currency* specifically, Ayub reproduces the approved
forms of constructive possession. The very first one is decisive for us:

> *"Constructive possession of an amount of currency or an asset is deemed to have taken place by
> **the seller enabling the other party to take its delivery and dispose of it, even if there is no
> physical taking of possession**. Some of the forms of constructive possession that are approved
> by both the Sharī'ah and business norms are the following:*
> 1. ***Crediting a sum of money to the account of the customer** directly or through bank transfer.*
> 2. *A customer entering into a **spot contract of currency exchange** with the bank against
>    another currency **already deposited in his account**.*
> 3. *The bank debiting — by the order of the customer — a sum of money to the latter's account and
>    **crediting it to another account in a different currency**…"*

Item 2 **is our trade**, described almost literally: a spot exchange executed against a balance
already standing in the account. The OIC Fiqh Academy (9th session, 1995) resolution quoted in
Ch 4.5 is the same list.

→ **VERDICT: holding at Coinbase rather than self-custody does NOT defeat our spot-settlement
claim. `qabd` is satisfied. Self-custody is NOT required.** This closes the question, and it
closes it with a named, citable authority rather than an assumption.

→ ⭐ **BUT it generates a real, checkable operator obligation, because the test has a live
condition attached.** `qabd` holds *"provided that it does not exceed the usual period normally
allowed"* and only while *"there is nothing to prevent the buyer from taking physical possession…
whenever he desires."* Ayub is explicit that during any crediting delay *"the beneficiary of such
crediting **cannot deal in the currency** during the allowed period until the crediting takes its
full effect."*

> **NEW OBLIGATION (`CompliancePolicy`, operator-attested + partially machine-verifiable):**
> **withdrawal capability is a compliance precondition, not merely an operational nicety.**
> 1. **Operator-attested:** the account must be in a state where BTC/ETH/PAXG/USDC balances are
>    withdrawable on demand — full verification, no withdrawal hold, no restricted/frozen status.
>    Belongs on the same operator checklist as the §56.3 rewards obligation.
> 2. **Machine-verifiable:** the existing feed-health/stale-data guard should be **extended to treat
>    a broker-reported withdrawal suspension, account restriction, or asset-freeze as a
>    compliance-grade event**, not just an operational one. Under §65.4's test, an asset we cannot
>    withdraw is an asset we may not have validly *possessed* — and the correct response is to
>    **halt new entries** (existing holdings are already ours; forcing a sale would be worse).
>    ⚠️ It must gate **entries only**, exactly like the §57.1 breaker.
> 3. **A note we should write down honestly:** Ayub's constructive-possession forms all presuppose
>    a *credited account balance*. Where an exchange holds assets **omnibus and treats the customer
>    as an unsecured creditor**, the customer arguably holds a **debt (`dayn`), not the asset
>    (`'ayn`)** — and a debt is a different juristic object entirely (it cannot be sold at other
>    than face value, Ch 5.4.2/7.16). **Coinbase's segregated-custody terms are what make the
>    `'ayn` reading available to us.** That is a fact about our specific broker, not a general
>    truth about exchanges — so it belongs in the broker-abstraction spec's compliance surface as a
>    **per-broker attribute**, and it must be re-checked if we ever add a second venue.

### 65.5 ⭐⭐ PAXG / gold — a genuinely STRICTER constraint than BTC → `CompliancePolicy` + allowlist

**Yes, gold carries additional rules, and we hold PAXG.** Gold is one of the six named *ribawi*
commodities in the foundational hadith (*"Gold for gold, silver for silver, wheat for wheat, barley
for barley, dates for dates and salt for salt — like for like, equal for equal and **hand to
hand**; if the commodities differ, then you may sell as you wish, **provided that the exchange is
hand to hand**"*), and unlike BTC its *thamaniyyah* is **not arguable** — it is the paradigm case.

Consequences, all textbook-explicit:

1. **USDC↔PAXG is unambiguously `bay' al-sarf`, row 3** — deferment prohibited, no exceptions and
   no room for the "maybe it's a commodity" reading available for BTC. Whatever settlement latitude
   one might argue for BTC, **none exists for PAXG.**
2. **No forward or futures exposure to gold, ever.** *"As such, **futures trading in commodities
   like gold and silver that serve as Thaman is forbidden**."* (Ch 4.5.) Also: gold cannot be the
   subject of *murabaha* or *salam*. Already excluded for us, now named at the asset level.
3. ⭐ **A concrete settlement-latency tolerance exists, and it is our operational bound.** Ch 6.6:
   *"a normal time required for payment/settlement is allowed by the Sharī'ah scholars **provided
   that it does not become a condition of the exchange**."* The OIC Islamic Fiqh Academy and Al
   Baraka's Sharī'ah committee *"allow the use of an otherwise Sharī'ah-compliant credit card for
   the purchase of gold and silver, as **an unintentional delay of up to 72 hours** does not create
   a problem."*
   → **This is the actual, citable standard for "immediate enough."** It is generous relative to
   anything an exchange fill involves (sub-second), so **we clear it with enormous margin** — but
   it gives `CompliancePolicy` a real number instead of a vibe, and it is the number to check
   against if any settlement mechanism ever changes.
4. ⚠️ **The one thing to actually verify about PAXG, and it is not a settlement question at all.**
   The rules above concern *exchanging* gold. PAXG is a **token representing allocated gold held by
   a custodian** — so the prior question is whether holding PAXG is holding *gold* (`'ayn`, subject
   to §65.4's possession test at one further remove) or holding a **claim on a custodian** (`dayn`,
   which would be a different and much worse juristic object — a debt tradeable only at face
   value). Ayub gives the governing principle at Ch 8.8.4: instruments *"representing real physical
   assets… are negotiable at market prices"*, whereas those *"representing debts and money are
   subject for their negotiability to the rules of Hawalah and Bai' al Sarf."*
   → **NEW allowlist-admission check for any asset-backed token:** does the token confer
   **redeemable ownership of allocated, segregated metal**, or merely an unsecured claim on the
   issuer? Only the former reads as `'ayn`. **PAXG's redeemability and allocated-custody structure
   is what admits it** — and that is now a documented admission criterion, not an assumption.
   This is the *second* per-instrument compliance attribute this source surfaces (cf. §65.4's
   per-broker custody attribute), and both belong on the `CompliancePolicy` admission surface.

### 65.6 ⭐⭐ "Speculation per se is not prohibited" — §28.3 is OVERSTATED, and this is the bluntest correction in the file

**Our README currently says (from §28.3): "low-turnover as compliance value" / "high churn drifts
toward maisir", used to reinforce the anti-scalping rail as a *compliance* matter.**

Ayub rules on this directly, Ch 5.5.2:

> *"However, **speculation per se, which means sale/purchase keeping in mind possible change in
> prices in the future, is not prohibited**. It is only such sales that may involve the sale of
> nonexistent and not owned goods/shares and Maisir/Qimār that are prohibited."*

→ **Buying an owned, possessed asset in the expectation that its price will rise, and later selling
it, is a permissible trade — full stop.** What converts speculation into *maisir* is precisely and
only: **(a) not owning it, (b) not possessing it, (c) not taking/making delivery, or (d) settling
by price difference.** Ayub's own list of gharar/maisir-based invalid transactions makes the
boundary unmistakable: *"short-selling of shares… Futures sales of shares, in which delivery of the
shares is not given and taken and **only a difference in price is adjusted**… speculation in shares
and Forex business, **in which only the difference is netted and delivery does not take place**."*

**What this means for us — three things, and the third is uncomfortable:**

1. ✅ **It resolves §65.3's awkward passage.** Our BTC trading is not "trading in money for its own
   sake" in the prohibited sense: we take delivery, bear ownership risk, and never net differences.
2. ✅ **It is a stronger validation of the core mandate than §28.2 gave us.** §28.2 argued spot
   ownership is permissible; §65.6 goes further and says the *profit motive over price movement* is
   permissible too. That was never explicitly cleared before — it was assumed.
3. ⚠️ **§28.3's low-turnover claim is a soft preference dressed up as a rule, and we should
   downgrade it.** Ayub does share Jobst's *ethos* (Al-Ghazali on riba diverting people from real
   economic activity; the disapproving quotation of Gray on 95%-speculative FX volumes), but he
   **never converts it into a prohibition**, and his explicit ruling above cuts the other way. The
   honest statement: **the anti-scalping / min-move rail (§4.1) is a PRUDENTIAL rail (costs,
   noise-trading, edge decay — §28.3's own argument plus every strategy source in this KB), not a
   shariah requirement.** It should stay exactly as it is — it is well-supported on trading
   grounds — but `CompliancePolicy` should stop claiming compliance authority for it, because that
   claim will not survive contact with someone who has read Ayub.
   → Same demotion applies to the "favour real-utility over meme tokens" note (§28.3): a **defensible
   curation preference**, not a *haram_sector* rejection. The `haram_sector` screen (§28.4) is
   unaffected — an actual haram business line is an actual prohibition, and Ayub confirms it
   (§65.10).

### 65.7 Price must be determinate at contract execution → an independent shariah argument for the LIMIT order (converges with §58.1) → `execution/executor.py`

Ch 5.4.2, on the consideration:

> *"**The consideration of a contract or the price must be agreed and fixed at the time of executing
> the exchange transactions. If the price is uncertain, the contract is void.** For example, if the
> seller says to the buyer: 'Take this (asset) and I will charge you its price in the market, or I
> shall tell you the price later'… the transaction is not valid."*

And in the gharar list (Ch 3.2.2): *"**Selling goods without specifying the price, such as selling
at the 'market price'**"* is named as an example of gharar.

⚠️ **Read carelessly this looks like it bans market orders.** It does not — the *contract* is
concluded at the fill, at which instant the price is fully determinate and known to both sides;
the pre-fill order is an instruction, not a concluded sale. So a market order is **not** void.
**But the direction of preference is unmistakable**, and it lands somewhere useful:

→ ⭐ **The compliance-preferred order type and the empirically-best order type are the same one.**
§58.1 found the limit order was *"the single most important thing one can do to improve a system's
profitability"* across ~80 controlled tests, and that it is safe under the rails (an unfilled limit
is simply no trade) and cheaper on Coinbase (maker < taker). §65.7 adds that it is also the order
type that most cleanly satisfies the price-determinacy requirement and puts the most distance
between us and the *"selling at the market price"* example. **Three independent arguments —
performance, cost, compliance — now point at the same change.** Fold this into the §58.1 sweep as
a tiebreaker in favour of limit orders, not as a new rail.

### 65.8 Two mutually contingent contracts (5.5.5) → clears the OCO exit bracket, with one caveat → `execution/executor.py`

Timely, given the native exit bracket now shipping. Ch 5.5.5 prohibits **two mutually contingent
and inconsistent contracts**, specifically:

> *"1. The sale of two articles in such a way that one who intends to purchase an article is obliged
> to purchase the other also… 2. **The sale of a single article for two prices when one of the
> prices is not finally stipulated at the time of the execution of the sale.** 3. Contingent sale.
> 4. Combining sale and lending in one contract."*
> *"…jurists consider it preferable that a contract of sale must relate to only one transaction, and
> different contracts should not be mixed in such a way that the reward and liability of contracting
> parties involved in a transaction are not fully defined."*

**Does an OCO stop/limit bracket fall foul of this? No.** The prohibited form is a **concluded
sale** carrying an unresolved price, or two sales whose execution is conditioned on each other. An
OCO is **two conditional instructions, of which exactly one ever becomes a contract**, each with
its own fully stipulated price; the other is cancelled and no sale occurs. The gharar section's
matching phrasing is the clearest test — *"indicating more than one price or option in a contract
**unless one is specifically chosen**"* — and an OCO's defining property is that exactly one is
chosen, mechanically.

→ ✅ **No change required. Record the reasoning in `CompliancePolicy` documentation** — it is the
kind of question that will be asked about a bracket order, and having the answer pre-written with
a citation is worth more than the two minutes it costs.
→ ⚠️ **The one design constraint it does impose:** never build an order structure where **both**
legs can fill, or where one leg's fill *obliges* a second trade at a price not stipulated in
advance. Scale-in/scale-out ladders are fine (each rung is a separate, fully-priced contract);
anything that reads as "if this fills, you must also take that at a price TBD" is not.

### 65.9 ⭐ NEW account-level obligation — PURIFICATION of non-compliant income (the §56.3-bar find) → `CompliancePolicy`

§56.3 gave us a **prohibitive** account-level obligation (disable USDC rewards). §33.1 gave us a
**positive** one (zakat estimate). Ayub supplies the **third and missing category: what to do with
riba that has already accrued** — and it is a standing, well-established practice with a name.

Ch 8.8.1 (on Islamic funds) and Ch 5.5.3:

> *"Islamic asset management companies have to **purify their income by deducting from the returns
> on the investments the earnings emanating from any unacceptable source** from the Sharī'ah point
> of view. **It is obligatory to dole away the prohibited income** that is mixed up with the
> earnings… and this obligation is on the one who is **the owner of the shares** — the investor."*

Mechanically, Islamic institutions run a **Charity Account**: any income determined to be
non-compliant is segregated into it and given away, **never** recognised as profit. Ayub references
this account throughout (late-payment penalties, income from defective murabaha, income from
non-compliant rollovers — *"such rollovers must go to the Charity Account"*). Al-Meezan's method:
compute a per-holding *charity rate* = non-compliant income ÷ gross revenue, apply it to income
received, transfer the product out.

→ **Why this is a real find and not a curiosity.** §56.3's obligation is *preventive* and, as we
already recorded, **operator-attested and not machine-verifiable** — Advanced Trade exposes no
rewards/interest endpoint. Which means **we cannot guarantee zero riba accrual**; we can only
attest that the setting is off. §65.9 is the **remedy for the gap §56.3 leaves open**, and unlike
§56.3 it *is* machine-computable, because incoming interest/reward credits appear in the
transaction ledger:

> **OBLIGATION (`CompliancePolicy` + DB schema + reporting):** maintain a **purification ledger**.
> Any credit to the account that is not (a) sale proceeds, (b) our own deposit, or (c) an asset
> transfer — i.e. **any interest, reward, staking, rebate, airdrop-of-unknown-provenance, or
> promotional yield** — is flagged, **excluded from realised P&L and from every performance
> statistic**, and reported as an amount **owed to charity**. Report-only and operator-actioned
> (like §33.1's zakat estimate); the agent never disposes of funds.
>
> **Two consequences worth stating plainly:**
> - **P&L correctness is now a compliance concern, not just an accounting one.** If riba credits
>   were silently included in equity, they would inflate the equity base that fixed-fractional
>   sizing computes from — riba would be **compounding into position size**. Segregating them is
>   the fix. `analysis/pnl.py` and `strategy/money_mgmt.py` both touch this.
> - It composes cleanly with §33.1: **zakat is on purified wealth**; purification runs first.

**Also from Ch 5.5.3, a maxim worth quoting in the policy doc:** institutions *"must give special
consideration to avoiding Riba **lest their income might go to the Charity Account** due to
non-Sharī'ah compliance."* Prevention is preferred; purification is the fallback, not a licence.

### 65.10 Screening criteria and the tolerance question — ⚠️ we may be stricter than the standard, which is fine, but we should know it → `CompliancePolicy` / allowlist

Ayub reproduces the **Dow Jones Islamic Market Index criteria** and the general IFI screen (Ch
8.8.1 / 8.8.2) — this is what §29's "AAOIFI is the authoritative reference" pointer actually
resolves to in practice:

1. **The investee's basic business must be halal** — *"Trading in stocks of companies whose main
   purpose is a prohibited activity, such as transactions with Riba, production of, or dealing in,
   prohibited products **is prohibited**."* ✅ **This is §28.4's `haram_sector` screen, confirmed
   verbatim by the canonical text** — and note it explicitly names **riba-based financial
   institutions** as a prohibited business line, which is exactly the §41.1 Aave/Compound/Maker/
   yearn rejection. Our screen is textbook-correct.
2. Debt ÷ 12-month avg market cap **< 33%**.
3. (Cash + interest-bearing securities) ÷ market cap **< 33%**.
4. Receivables ÷ market cap **< 33%**.
5. **A de-minimis income tolerance** — *"Only a **negligible portion** of the income of an investee
   company is derived from interest… (Al Meezan: non-permissible income **should not exceed 5%** of
   total income)"*, with the excess purified per §65.9.

→ **Criteria 2–4 are financial-statement ratios and are structurally N/A to tokens** (same verdict
as §50 on equity fundamentals — no balance sheet exists). No action.

→ ⚠️ **Criterion 5 is the interesting one, and it cuts against our instincts.** The mainstream
standard is **not** zero-tolerance: a company with ≤5% incidental interest income is *investable*,
with the tainted fraction purified. Our posture is **binary rejection** on any riba-yield function
(§41.1). **That is stricter than the standard.**
- **This is a legitimate choice, and §29.2 already licenses it** ("keep policy pluggable + document
  our conservative interpretation"). Do not loosen it — a 5%-style tolerance would require
  computing a token's revenue mix, which is neither well-defined nor auditable for most tokens, and
  the whole point of a narrow BTC/ETH/PAXG allowlist is that we never have to make that call.
- **But it should be documented as a deliberate over-compliance, not presented as *the* standard.**
  Somebody will eventually ask "why is our screen harsher than DJIM?" and the answer — *because a
  5% test is unmeasurable for tokens, and our allowlist is small enough that binary rejection costs
  us nothing* — is a good one that we should have written down.
- Note also the mainstream terminology: this whole apparatus is the **"negative screen"** §32
  named. §65.10 is the concrete instantiation of it.

Ch 8.8.2 adds one direct corroboration of an existing rail: *"it is **not permissible to purchase a
share with an interest-bearing loan** offered to the purchaser by a broker… **Nor is it permissible
to sell a share that the seller does not possess**."* Margin buying and short-selling, excluded in
one sentence by the OIC Fiqh Academy.

### 65.11 Derivatives, options, futures, short-selling — CONFIRMS §28.1/§42–49/§53 with the most authoritative statements yet

No new ground; substantially better citations for ground we already hold. Consolidated:

- **Options** (Ch 8.8.8): *"the feature that an option contract **confers the right but not the
  obligation** to enter into an underlying contract of exchange at or before a specified future
  date makes the contract **non-Sharī'ah compliant**"* — because *"delivery has to be given and
  taken pursuant to the sale contracts **without regard to movement in prices**."* And decisively:
  *"As regards options relating to **currencies, interest rates and stock indices, all agree that
  these have no place in Islamic finance**."* → §42–§49 (the entire Swissquote options series) and
  §53 (warrants) are confirmed at the root by unanimous scholarly position.
- **Futures settled by difference** (Ch 3.2.3): *"Present futures and options contracts that are
  **settled through price differences only** are covered under **gambling**."* → the *maisir*
  grounding, distinct from the gharar one, for cash-settled derivatives. Note the operative
  criterion is **cash settlement / no delivery**, not futurity as such.
- **The general futures market** (Ch 10.1): *"The modern futures markets that deal in futures like
  options, derivatives, swaps, etc. **do not qualify** under these rules."*
- **Short-selling** (Ch 5.4.2): *"short-selling has been prohibited by **almost all scholars**"* —
  because the subject matter *"must be existing/existable… capable of ownership/title, capable of
  delivery/possession… and the seller must have its title and risk."* → the long-only rail, from
  the subject-matter conditions rather than from §28.2's ownership argument. Two independent
  derivations now.
- **Swaps, CFDs, hedging products** (Ch 8.8.8): excluded as *"derived from the expected future
  performance"* with gharar + riba; Ayub adds the pointed observation that hedging providers sell
  *"protection against a danger that never needed to exist in the first place"*.
- **Bay' al-'Inah / buy-back / wash trades** (Ch 6.11): prohibited by the majority; confirms §28.1's
  minor point and maps to a no-self-dealing / no-fabricated-round-trip posture.

### 65.12 Zakat — CONFIRMS §33.1 and adds the two missing parameters → `CompliancePolicy` (report-only)

§33.1 gave us "~2.5%/lunar year, report-only." Ayub confirms the rate and supplies what §33.1
omitted:

- **The base is NET wealth** — *"wealth in excess of his consumption needs"*, at *"generally 2.5%
  of net wealth"*. Not gross market value of holdings. Our report should say *estimate on net
  position value* and be explicit that liabilities and personal circumstances are the operator's
  to apply.
- **There is an exemption threshold — `nisab`** — *"over and above an exemption limit (Nisab)"*.
  Below it, nothing is due. The report should surface the raw figure and **state that `nisab` and
  the lunar-year anniversary are operator inputs**, not agent determinations.
- **Fiat/paper money is zakatable** on the same footing as gold and silver (Ch 4.7.1) → USDC
  balances belong in the base alongside BTC/ETH/PAXG.
- **Purification runs first** (§65.9): zakat is computed on purified wealth.

→ Still **report-only, still never actioned by the agent** — but the report is now parameterised
correctly instead of being a naive `0.025 × portfolio_value`, which would over-state.

### 65.13 Stablecoins and money-substitutes → CONFIRMS §30.1's parity note, and grounds §56.3 more sharply

Two things the book gives us on this:

1. **The `'illah` is *function*, not form** — *"the rules of Riba apply to **anything that serves
   the function of money**. This may be gold, silver, any paper currency **or IOUs**."* A
   fiat-referenced stablecoin is an IOU serving the function of money; it is *thaman*. → **USDC↔USD
   at par only, hand to hand** (§30.1 confirmed exactly), and any USDC-for-USDC exchange at
   non-parity would be textbook **Riba Al-Fadl**.
2. ⭐ **A sharper grounding for §56.3 than §56 itself gave.** In Islamic law a deposit balance
   owed back to you is a **loan (`qard`)**, and Ayub's rule is absolute: *"**all loans that seek
   benefit involve Riba**"* — with the corollary that even *indexation* of a loan for inflation
   *"leads to Riba"*. So the objection to Coinbase USDC rewards is not "it looks like interest";
   it is that **any** benefit accruing to us on a balance owed back to us is riba by definition,
   with **no de minimis** (§65.1). This upgrades §56.3 from a prudent precaution to a hard
   requirement — and is precisely why §65.9's purification ledger is needed as the backstop when
   the setting cannot be machine-verified.

### 65.14 Rewards / staking as riba — what the book does and does not say

**Honest answer: the book is from 2007 and rules on none of this directly.** It contains nothing on
staking, liquidity provision, or on-chain yield, and nothing on crypto at all. What it supplies is
the **test**, and the test is clear enough to apply:

- A **predetermined or guaranteed return on a balance** is riba — §28.1's definition, sharpened by
  §65.13's `qard` framing. → **Interest/rewards on idle USDC: riba, no tolerance.** Confirms §56.3.
- **Yield/lending tokens whose business function is riba** are excluded by the *business-line*
  screen, confirmed verbatim at §65.10 (*"interest-based financial institutions"*). Confirms §41.1.
- **Staking specifically is genuinely undecided by this source and we should say so.** The
  *analytical* question is whether a staking reward is (a) a return on a loan (riba), (b) a fee for
  a real service — validation work, with slashing risk borne (which would be `ju'alah`/`ujrah`-like
  and arguably permissible), or (c) profit-sharing on a deployed asset (`mudarabah`-like). Ayub's
  general principle — *"Any entitlement to profit or return comes from **value addition and bearing
  the business risk**"* — is what a real analysis would turn on, and it does **not** obviously
  resolve against staking.
  → **Our position is unchanged and stays conservative: we do not stake.** But state the reason
  accurately in `CompliancePolicy`: **not** "staking is settled riba" (it isn't), but *"the question
  is genuinely contested, we have no scholarly determination in hand, our mandate has no need of
  it, and §29.2 directs us to the conservative branch where scholars diverge."* That is a defensible
  position; claiming a settled ruling we do not have is not.

---

### 65.15 ⛔ Out of scope — Islamic banking products (the majority of the book)

**We are a spot trading agent, not a financial institution.** None of the following was extracted;
all of it is recorded here only so the boundary is explicit and nobody re-reads the book looking
for it:

- **Sale-based financing modes:** *Murabaha* / *Musawamah* (Ch 9, cost-plus and bargained sale,
  MPO structures, agency, rollover, defaults), *Salam* and *Istisna'a* (Ch 10, the two sanctioned
  forward-sale exceptions), *Bai' al-'Inah*, *Tawarruq*, *Bai' al-Istijrar* (Ch 13). Read **only**
  where they rule on currencies/gold (§65.5).
- **Leasing:** *Ijarah*, *Ijarah Muntahia-bi-Tamleek*, operating vs financial lease (Ch 11).
- **Participatory modes:** *Musharakah*, *Mudarabah*, *Diminishing Musharakah* (Ch 12) — §31 already
  established these are bank PLS-financing, not trading risk.
- **Accessory contracts:** *Wakalah*, *Ju'alah*, *Kafalah*, *Hawalah*, *Rahn* (Ch 7, 13).
- **Sukuk and securitization** (Ch 15) — same verdict as §28.5; tradability rules noted only insofar
  as they gave us §65.5's asset-backed-token principle.
- **Takaful** (Islamic insurance), **accounting/AAOIFI FAS standards, Sharī'ah governance boards,
  regulation and supervision** (Ch 16–17).
- **Bank operations:** deposit-pool management, financing tenor, working-capital/project/trade
  finance, liquidity management, inter-bank markets, central-bank refinancing, cards (Ch 8, 14).
- **Islamic monetary theory** — money creation, monetary policy, reserve regimes (Ch 4.7.3) — real
  economics, zero agent surface.

**One tangential note worth keeping (same spirit as §28.5's):** Ayub is markedly *more* critical
than §28 of the industry's tendency to replicate conventional payoffs by contract arrangement — on
*tawarruq*/commodity-murabaha he warns it *"should be used only in extreme cases… Widespread use of
such products is harmful to the Islamic banking industry in the long run"*, and on ruses (*hiyal*)
generally (Ch 6.11) he is dismissive. **This is not a licence for us to synthesize anything.** Our
mandate stays plain spot, which is unambiguously permissible under every reading in this book.

### 65.16 Discarded (no agent value)

Front matter, dedication, foreword, preface, acknowledgements, list of boxes/figures; Ch 1's
critique of neoclassical economics and conventional debt; Ch 2 entirely (*maqāsid al-sharī'ah*,
the role of Islamic economists, factors of production, liberalism vs state intervention); Ch 3.3
business ethics and norms (justice, covenants, mutual cooperation, *dharar*) — genuinely admirable,
zero mechanical content; the comparative-religion survey of usury prohibition and the Calvin/
Molinaeus/Encyclopedia-Britannica history; Pakistani Shariat Appellate Bench and Federal Shariat
Court procedural history; the lottery/prize-bond analysis (correct, and entirely about products we
would never touch); inflation-indexation-of-debt debates; 1990s–2000s Islamic-banking market-size
statistics, country studies (Malaysia/Pakistan/Gulf/Iran/Sudan) and institutional directories
(IFSB, IIFM, GCIBAFI, ARCIFI, IIRA, LMC); Ch 18–19 industry outlook and "way forward"; all
end-of-chapter exercises, the ~40pp glossary (mined for terms, not extracted), bibliography and
index.

---

### Net assessment

**The most consequential compliance source in the KB, and the first one that pushed back.** Sources
§29–§33 were pure reinforcement of §28; the README concluded, reasonably, that the Islamic-finance
stream was *"exhausted"*. **That conclusion was wrong** — it was reached by reading five short
papers that all summarised the same two prohibitions, and never a text that reached the **exchange
and possession rules**, which is exactly where a spot trading agent's actual compliance questions
live.

**CONFIRMS (now textbook-grounded, from the canonical treatise):** the whole exclusion set — riba
al-fadl *and* al-nasee'ah, gharar, maisir; long-only (from the subject-matter conditions, a second
independent derivation); no leverage/margin (OIC Fiqh Academy, one sentence); no derivatives, no
options (*"all agree that these have no place in Islamic finance"*), no cash-settled futures (as
*maisir*); the `haram_sector` screen **verbatim**, including riba-based financial institutions as a
named prohibited business line (§41.1 vindicated); spot ownership as the permissible baseline; the
stablecoin-parity rule; zakat; and — newly and importantly — **that trading for price appreciation
is itself permissible** (§65.6), which had only ever been assumed.

**COMPLICATES / STATED TOO LOOSELY (the important category):**
1. **§28.3's "low turnover = compliance value" is overstated** and should be demoted to a
   prudential rationale. *"Speculation per se… is not prohibited."* The anti-scalping rail keeps its
   trading justification and loses its shariah claim. (§65.6)
2. **§30.1's immediacy mandate is imprecise.** The rule is not "settlement must always be
   immediate"; it is the three-branch `'illah` test, and it fires only when both legs are monetary.
   We satisfy the strict branch, but the stated reasoning is wrong and the *al-fadl*/*al-nasee'ah*
   labels are attached to the wrong limb. (§65.2)
3. **Riba and gharar are not symmetric.** Riba is strict-liability with no de minimis; gharar is a
   materiality threshold, and **ordinary price volatility is explicitly not gharar**. Our
   undifferentiated trio is the wrong model for new questions. (§65.1)
4. **Our screen is stricter than the mainstream standard** (binary rejection vs DJIM/AAOIFI's ~5%
   incidental-income tolerance). Keep it — but document it as deliberate over-compliance rather
   than as *the* standard. (§65.10)
5. **We should stop implying staking is settled riba.** It is contested; our refusal rests on
   conservatism under §29.2, not on a ruling we possess. (§65.14)

**THE FOUR OPEN QUESTIONS, ANSWERED:** `sarf` **does** govern our swaps and we already comply, with
two standing exclusions named (§65.3). **`qabd` is satisfied by exchange-held balances** — the
approved forms of constructive possession describe our trade almost literally, **self-custody is not
required** — but the test has a live condition (withdrawability) that becomes an obligation
(§65.4). **PAXG is genuinely stricter than BTC**: unambiguous *sarf*, no futures ever, a concrete
72-hour settlement tolerance we clear by orders of magnitude, and a **new admission criterion for
asset-backed tokens** (allocated/redeemable = `'ayn`, not `dayn`) (§65.5). **Stablecoins are
*thaman* by function**, and the `qard` framing makes §56.3 a hard requirement with no de minimis
(§65.13).

**NEW obligations/rails on the §56.3 bar:**
- ⭐⭐ **§65.9 income purification** — a purification ledger segregating any interest/reward credit
  from realised P&L and from the equity base that drives position sizing, reported as owed to
  charity. This is the **remedy for the gap §56.3 leaves open** (that obligation is
  operator-attested and unverifiable), and unlike §56.3 it **is machine-computable**. It also makes
  P&L correctness a compliance concern.
- ⭐ **§65.4 withdrawal capability as a compliance precondition** — operator attestation plus an
  extension of the feed-health guard to treat withdrawal suspension/account restriction as a
  compliance-grade **entry-blocking** event (entries only, per §57.1's lesson).
- ⭐ **§65.5 asset-backed-token admission check** — allocated/redeemable ownership vs unsecured
  issuer claim. Together with §65.4's per-broker custody attribute, this means `CompliancePolicy`'s
  admission surface needs **per-instrument and per-broker attributes**, not just a per-order check.
- **§65.7** limit orders are compliance-preferred as well as (§58.1) empirically best — a tiebreaker
  in an existing sweep, not a new rail. **§65.8** clears the OCO bracket, with a design constraint
  on future ladder shapes.

**Recommendation:** wire §65.9 (purification ledger) and §65.4 (withdrawal attestation) into the
`CompliancePolicy` account-obligations surface designed for §56.3, and into `docs/operator-runbook.md`.
Correct the §28.3, §30.1 and riba/gharar-symmetry statements in the policy documentation. **On
further sources: the compliance stream is now genuinely well-served and can be closed** — but note
the earlier "exhausted at §32" call was premature, so close it for the right reason. The specific
remaining gap is not another textbook; it is a **contemporary scholarly determination on
cryptoassets** (are they *māl*? do they bear *thamaniyyah*?) — Ayub predates the question entirely
and merely gives us the framework to ask it. If any single further compliance source is fed, make
it an AAOIFI or OIC Fiqh Academy resolution on digital currencies, nothing else.
