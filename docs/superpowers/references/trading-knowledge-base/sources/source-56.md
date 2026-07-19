[← Knowledge Base index](../README.md)

## Source 56 — "Introduction To Forex" (Mark McRae, Sure-Fire Forex Trading, 25pp) — ⛔ EXCLUDED (riba), logged

> **Zero agent-actionable content, but the clearest first-hand description of the riba mechanics we
> exclude — which is why it is logged rather than silently dropped** (same disposition as §18 carry
> and §53 warrants).
>
> This is a pure **market-mechanics primer** for retail FX: history, interbank, quote conventions,
> pips, pip-value arithmetic, lots, **leverage/margin**, **rollovers**, accounts, statements, the
> main players, and **four full pages of a central-bank URL directory** (pp.20–23). There is **no
> strategy content whatsoever** — no entries, no exits, no stops, no indicators, no rules. The
> closing "What Next" page is a two-paragraph Dow-Theory sketch (market discounts everything /
> prices move in trends / history repeats), long since saturated (§1, §23, §54).
>
> Mechanics are already covered by **§04** (terminology) and **§37** (Stanzione, "How to Trade
> Forex"). Nothing here is adopted.

---

### 56.1 ⭐ "Spot forex" is not spot in the shariah sense — a sharper statement of §30.1

The book opens by scoping itself to *"the main market, sometimes referred to as the **Spot or Cash**
market"* — and then describes a product that is none of those things. Its own account, p.12–13:

- Spot FX deals are *"nearly always due for settlement **two business days later**… the value date or
  delivery date."*
- Any position open at 21:59 London is *"automatically **rolled over** to the next business day"* —
  and the stated purpose is explicit: *"**This is necessary to avoid the actual delivery of the
  currency.**"*
- The roll is executed by closing and instantly reopening at an adjusted rate: *"The broker will
  normally charge you the **interest differential** between the two currencies if you rollover your
  position"* (worked example: a 1-pip premium on a long-EUR/short-USD position).
- And *"most leveraged accounts are **unable to actually deliver** the currency as there will be
  insufficient capital there to cover the transaction."*

→ **This is the cleanest negative exemplar in the KB for §30.1.** Riba al-fadl / al-nasee'ah require a
currency exchange to be **simultaneous, hand-to-hand**; deferment converts it to riba. Retail "spot"
FX is **T+2, perpetually rolled to avoid delivery, on capital that could not deliver anyway, with an
explicit interest charge for the deferral**. Every element §30.1 prohibits is present, and stated by a
proponent rather than a critic.

→ **Contrast, and why our own posture is sound:** Coinbase spot settles **immediately** and we take
actual ownership — the exact distinction §30.1 draws between a permissible BTC/USD spot trade and a
deferred one. Worth quoting in `CompliancePolicy` documentation: *the word "spot" on a product is not
evidence of spot settlement.*

### 56.2 The rollover/tom.next mechanism, described from the inside → reinforces §18

§18 excluded carry/rollover trading wholesale as riba. This source describes the **same mechanism from
the broker's side** and names it: **tom.next** ("tomorrow and the next day"). The rule it states is the
carry trade in one sentence: *"If you are long a currency and that currency has a **higher overnight
interest rate**, you will gain. If you are short the currency with a higher overnight interest rate,
then you will lose the difference."*

→ No change to the exclusion — this is corroboration, not new content. Logged because §18's file
records the *strategy*; this records the *plumbing* underneath it.

### 56.3 ⚠️ Interest on idle broker balances — a LIVE compliance touchpoint for us

p.16: *"**Just as with a bank you are entitled to interest on the money you have on deposit.** Some
brokers may stipulate that interest is only payable on accounts over a certain amount, but the trend
today is that you will earn interest on any amount that is not being used to cover your margin."*

→ **This one is not hypothetical for keel.** We hold a **USDC** quote balance on Coinbase (rail 13
routes buys through USDC), and Coinbase has historically offered **USDC rewards/yield on idle
balances**. That is riba accruing on our own cash, independent of anything the strategy does — a
compliance failure that no trading rail would catch, because it is an *account setting*, not an order.

→ **Action (new, account-level not strategy-level):** `CompliancePolicy` should carry an explicit
obligation to **ensure USDC rewards / interest-bearing features are disabled on the trading account**,
and ideally a documented operator checklist item. Note the §33.1 zakat-estimate report is the only
other account-level (non-order) compliance obligation in the KB — this is the second, and unlike zakat
it is *prohibitive* rather than positive.

### 56.4 ⛔ Excluded (the rest of the book)

- **Leverage / margin** (pp.10–12) — *"Leverage is **financed with credit**… The loan (leverage) in the
  margined account is collateralized by your initial margin."* 1% margin → $1,000 controls $100,000;
  margin calls; variation margin. Riba, already hard-excluded (§4.9, §10.10, §28.1).
- **Lots / contracts** ($100,000 standard, $10,000 mini) and the entire **pip-value arithmetic**
  (pp.6–9) — the canonical statement of the convention our adaptation lens *converts away*
  (pips → %/ticks/ATR; no lots, position sizing is risk-% of equity).
- **Currency-pair trading itself** — exchanging money for money with deferred settlement; see §56.1.
- **Shorting** — assumed throughout ("you sell to open a position"); long-only spot, always.
- **Speculation framing** — *"70%–90% of the FX market is speculative… the person or institution that
  bought or sold the currency has **no intention of actually taking delivery**."* The ownership /
  profit-loss-sharing principle (§28.1–28.2) is precisely what that sentence negates; also the
  low-turnover-as-compliance value (§28.3).
- **Hedge funds using "a much higher degree of leverage"** (p.18) — noted, excluded.

### 56.5 Discarded (no agent value)

Cover/branding and the "Sure-Fire Forex Trading" footer on all 25 pages; the **four-page central-bank
URL directory** (pp.20–23, ~180 links, a 2002-era reference list — many entries now defunct, e.g.
"National Bank of Yugoslavia"); broker-selection advice ("give a few of them a call… go spend the day
with him"); account-opening paperwork guidance; segregation-of-funds explanation (broker-solvency
topic, N/A — we self-custody via a regulated exchange API); the 1989–2001 BIS currency-turnover table;
account-statement layout example; the "What Next" fundamental-vs-technical sketch (saturated);
closing CTA and author contact.

### Net assessment (saturation-honest)

**Excluded wholesale; two things earned the file.** §56.1 gives us the sharpest available illustration
of why §30.1 demands *immediate* settlement — retail "spot" FX is T+2, rolled indefinitely to dodge
delivery, with interest charged for the deferral, described in the vendor's own words. §56.3 surfaces
a **genuinely new, live compliance obligation** that is account-level rather than strategy-level:
disable interest/rewards on idle USDC. Everything else is either already covered (§04, §37), converted
away by the adaptation lens (pips/lots), or hard-excluded (leverage, rollover, shorting, non-delivery).

**Recommendation:** add the USDC-rewards-disabled obligation to `CompliancePolicy` + an operator
checklist. **Do not feed more forex-mechanics primers** — this is the third pass over the same ground
(§04, §37, §56) and the yield is now purely negative-exemplar.
