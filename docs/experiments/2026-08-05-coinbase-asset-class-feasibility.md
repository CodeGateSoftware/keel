# Coinbase's new asset classes — what keel can actually trade

**Date:** 2026-08-05
**KB basis:** §65.5 (gold futures forbidden outright), §65.11 (cash-settled futures = *maisir*;
short-selling), §65.4/§66.2/§67.1/§71.5 (`qabd`), §71.6 (equity-like ⇒ reject *by capability*),
§65.6 (speculation per se permissible), §29.2 (scholarly divergence), §73.3 (zero-trials
inheritance).
**Status:** feasibility only. **No code changed, no product admitted, allowlist unchanged.**
**Evidence:** live read-only probe of the production account's own CDP key
(`~/keel/.env:CDP_API_KEY`) on 2026-08-05. Raw output preserved at `scratchpad/out1.txt`
(product-type census, key permissions) and `scratchpad/out2.txt` (futures taxonomy, equity field
dump, candle probes, preview refusals). **No `create_order` call was made** — only
`preview_order`, which is non-binding, and it was refused before it could bind.

## The question

Coinbase now lists stocks, futures, "perps", indices and commodities. Can keel trade all or some of
them? Below, "can" is decomposed into four independent gates — *does the product exist on this
account's API*, *is there market data*, *is there an order path*, *does keel's code accept it* —
because they fail for entirely different reasons and cost entirely different amounts to fix.

## Verdict

| Asset class | On this account's API? | Market data? | Order path? | keel-compatible today? | Verdict |
|---|:--:|:--:|:--:|:--:|---|
| **Spot crypto** (936, venue CBE) | yes | yes | yes | **yes** | what keel trades. Unchanged. |
| **Crypto dated futures** (nano BTC/ETH/SOL/…) | yes, 99 total | **yes, full OHLCV** | **no — CFM onboarding** | no | technically reachable; blocked on paperwork, a large build, and an unanswered ruling |
| **Crypto "perp-style" futures** (`*-20DEC30-CDE`) | yes | yes | no — same gate | no | same, **plus hourly funding** — the sharpest halal problem in the set |
| **Commodity futures** (gold, silver, copper, Pt, oil, gas) | yes | yes | no — same gate | no | gold/silver are **named prohibited** in the KB (§65.5). Others need a ruling. |
| **Index futures** (Mag7+Crypto, AI, China, Defense, Tech100) | yes | yes | no — same gate | no | index basket + no delivery + no constituent screening. Worst case in the set. |
| **US equities** (1000-product page, venue CCM) | yes | **NO — none** | **no — refused by design** | no | **not buildable at any price today.** The venue gate is absolute. |
| **FX / forex** | **does not exist** | — | — | — | **not a thing on Coinbase.** No product type, no products. |
| **Options** | none listed (`OPTION_GROUP` = 0) | — | — | — | absent, and excluded by §65.11 anyway |

**One-line answer: no — keel can trade none of them today, and only the crypto-futures family is
even a candidate for a future build.** Three of the four gates are open for futures and only one
is open for equities.

## What is actually there — precision on four commonly-confused points

### 1. FX does not exist on Coinbase

`GET /api/v3/brokerage/products` accepts a `product_type` filter. The types that return anything
are `SPOT` (936), `FUTURE` (99) and `EQUITY` (1000, capped). `FUTURE_GROUP` and `OPTION_GROUP`
return zero. There is **no FX/forex product type and no FX product** (`out1.txt:44-57`). The
closest thing on the venue is spot `EURC`, which is a stablecoin — a `dayn` claim on an issuer, and
the 2026-07-20 candidate-universe doc already flagged it as such. There is nothing to evaluate.

### 2. "Stocks" here are REAL US equities, not the tokenized-stock product

This is the confusion most likely to mislead. The widely-reported Coinbase "tokenized equities"
launch is a **non-US** product issued on Base. That is **not** what this account sees. What the
Advanced Trade API returns is `product_type: EQUITY` on **venue `CCM`**, carrying
`equity_product_details` with a **CIK number**, an `equity_subtype`
(`COMMON_STOCK` / `ETF` / `ADR` / `PREFERRED_STOCK` / `SHARES_OF_BENEFICIAL_INTEREST`), and a
`trading_day_info.trading_sessions` block with `OVERNIGHT` / `PRE_MARKET` / `NORMAL` sessions
(`out2.txt:222-265`, the SPY record). The `venue_id` on the trading-day block is `"apex"` — an
introducing-broker/clearing arrangement, i.e. genuine US brokerage rails.

These are real shares of real US issuers. Any reasoning imported from the tokenized-stock product
(wrapper tokens, on-chain settlement, a synthetic claim) is **wrong here** and would produce the
wrong halal analysis as well as the wrong technical one.

### 3. The US "perps" are EXPIRING contracts that pay funding — not perpetual swaps

Every one of the 99 futures products carries `contract_expiry_type: "EXPIRING"`
(`out1.txt:21`). There is not a single `PERPETUAL` contract on the venue. What Coinbase markets as
a perp is a **long-dated December-2030 contract with an hourly funding mechanism** bolted on:

```
BIP-20DEC30-CDE   display "BTC PERP"   contract_expiry 2030-12-20T16:00:00Z
                  funding_interval "3600s"   funding_rate 0.000007   open_interest 178182
```
(`out1.txt:22`; the tradable set with live funding rates is `out2.txt:115-166`.)

Two consequences the label hides:

- **They still expire and still need a roll.** Four and a half years out is long, but a position
  held to maturity has a settlement date, and any strategy holding one has an unavoidable roll
  obligation keel has no concept of. The dated contracts are far worse — ~3 monthly expiries per
  root, so `BIT-28AUG26-CDE` expires **23 days from this probe**.
- **They pay or receive funding every hour.** This is a periodic cash payment for holding a
  levered position, sized by a rate. Whether that is riba is not for this document to decide, but
  it is unambiguously the mechanic that most resembles it, and it is the *defining* feature of the
  "perp" variants and absent from the dated ones (`funding_interval: null`, `funding_rate: ""`).

### 4. Commodities and indices are futures wrappers, not spot

There is no spot gold, spot oil or spot index on this venue. Gold is `GOL-25NOV26-CDE`
(`contract_size: 1`, `non_crypto: true`, 24×7); silver `SLR-27AUG26-CDE` (size 50); copper
`CU-27AUG26-CDE` (size 2000, **not** 24×7); platinum `PT-28SEP26-CDE`; oil `NOL-19AUG26-CDE`;
natural gas `NGS-26AUG26-CDE` (`out2.txt:37-90`). Indices are the same shape: `MC-17SEP26-CDE`
(Mag7+Crypto, dated) plus perp-style `AIP-`/`CHN-`/`DEF-`/`TEK-19DEC30-CDE`, **none 24×7**.

keel already holds spot gold exposure via **PAXG**, which is a spot `'ayn`-candidate token, not a
contract. There is also a `PAU-20DEC30-CDE` "PAXG PERP". Substituting a funded, expiring, levered
contract for a spot holding of the same underlying is strictly worse on every axis this document
examines.

## Three kinds of blocker, and why the distinction matters

### (a) Account / paperwork gate — solvable by the operator in days, zero code

The key already has `can_trade: true` on the **Primary (DEFAULT) portfolio**
(`out1.txt:70`), which is the portfolio Coinbase requires for US futures. What is missing is the
CFM/futures **onboarding**: a separate application plus CFTC/NFA risk disclosures.

| Probe | Result |
|---|---|
| `preview_order` on `BIT-28AUG26-CDE` | HTTP 403 `PERMISSION_DENIED: "FCM preview orders are only enabled for onboarded users"` (`out2.txt:1`, `310`) |
| `GET /cfm/balance_summary` | `{"balance_summary": null}` (`out2.txt:320`) |
| `list_futures_positions` | `{"positions": []}` (`out1.txt:66`) |
| accounts census | 7 crypto + 1 fiat, all `ACCOUNT_PLATFORM_CONSUMER`; no futures currency (`out2.txt:325-327`) |

This gate is cheap to clear and **that is the danger**: it is the only blocker that can be removed
without touching code or answering the halal question, so it is the one most likely to be removed
first and by accident. See the "do not do" list.

### (b) Venue capability gap — NOT solvable by keel, at any budget

Futures are fine here. Equities are not, and it is not close.

| Endpoint | Futures | Equities |
|---|---|---|
| `get_candles` ONE_HOUR | 72 candles, full OHLCV — verified on `BIT-28AUG26-CDE`, `BIP-20DEC30-CDE`, `GOL-25NOV26-CDE` (`out2.txt:277-290`) | **`{"candles": []}`** — empty at ONE_HOUR *and* ONE_DAY, on both the USDC id and its `alias` id (`out2.txt:292-295`) |
| `get_product_book` | live | **HTTP 500 INTERNAL** |
| `get_market_trades` | live | **HTTP 500 INTERNAL** |
| `get_best_bid_ask` | live | **`{"pricebooks": []}`** |
| product payload prices | `price`, `mid_market_price`, `volume_24h` all populated (`out2.txt:300`) | `price`, `best_bid_price`, `best_ask_price`, `mid_market_price`, `volume_24h` all **empty strings**; only `high_24h`/`low_24h` populated (`out2.txt:171-221`) |
| `preview_order` | 403, onboarding — a *gate* | 403 `"API order preview is not available for equities products"` — a *design decision* (`out2.txt:2`, `315`) |

**keel cannot trade an instrument it cannot get a candle for.** Every rule in
`keel/strategy/rules/` takes `candles_by_tf` (`keel/strategy/rules/base.py:120`); the CTS scorer,
the backtester, the ATR-based stop that fixed-fractional sizing divides by
(`keel/execution/sizing.py:30`) and the drawdown mark-to-market
(`keel/execution/equity.py:32-55`) all consume the same series. With zero bars there is no setup,
no stop, no size and no equity mark.

The preview refusal is independently fatal. `keel/execution/executor.py:427-433` calls
`broker.preview_order` **unconditionally and re-raises on failure** — before the confirm/autonomous
branch at line 444. It is not a veto path; the cycle raises. keel's Coinbase adapter declares
`supports_native_preview=True, synthesizes_preview=False`
(`packages/keel-broker-coinbase/keel_broker_coinbase/adapter.py:33-34`), so there is no fallback to
fall back to.

The only remaining route would be `create_order` with no preview and no price — placing real money
blind. That is not an option, and it is why "buildable later" is the right classification for
futures and "not buildable" is the right one for equities.

There is a second, smaller equity blocker worth recording because it would survive even if Coinbase
shipped market data tomorrow: **the equity `product_id` is an opaque 64-char hex hash**, e.g. SPY's
USDC-quoted id is `5b27e1b1…3227c` with `alias` `a4a29514…6162` for the USD-quoted twin
(`out2.txt:172`, `199`). The real ticker lives in `equity_product_details.ticker` and
`base_currency_id`. And the listing is **unstable**: `product_type=EQUITY` returns exactly 1000 —
the page cap — and the composition varies between calls (one call: 740 USDC / 260 USD
(`out2.txt:273`); a second call to the same endpoint: 746 / 254). Any allowlist keyed on
`product_id` would be keyed on a hash, and any discovery pass would see a different universe each
run.

### (c) keel architecture gap — solvable, expensive, and safety-critical

keel's instrument model is *a bare `product_id: str` shaped like `BASE-QUOTE`*. Nothing else. The
gap is not a missing feature; it is an assumption threaded through 341 `product_id` references.

| # | Area | Where it breaks | Evidence |
|---|---|---|---|
| C1 | **Instrument identity** | `quote_currency_of` does `rpartition("-")`; `guards._asset` does `split("-")[0]` | `packages/keel-core/keel_core/products.py:22`, `keel/execution/guards.py:158-159` |
| C2 | **Id construction** | the only construction path is `f"{asset}-{quote}"` — it cannot express an expiry or a hash | `keel/commands/_products.py:23` |
| C3 | **Position model** | positions are reconstructed from keel's own SQLite audit log, never fetched from the venue; the port has **no `get_positions`** | `keel/data/db.py:73-89`, `keel/execution/guards.py:179-189`, `packages/keel-broker-api/keel_broker_api/port.py:23-47` |
| C4 | **Balance model** | `Balance{currency, available, total}` is a spot-wallet shape — no margin, no unrealized P&L, no maintenance requirement | `packages/keel-broker-api/keel_broker_api/results.py:17-23` |
| C5 | **Order model** | 4 variants, none carrying leverage, reduce-only, post-only or a contract count | `packages/keel-broker-api/keel_broker_api/orders.py:26-105`, `…/keel_broker_coinbase/translate.py:21-53` |
| C6 | **Lot sizing** | sizing emits fractional `Decimal` and never rounds to a venue lot; futures need **whole contracts** (`base_increment: "1"`, `base_min_size: "1"`) | `keel/execution/sizing.py:22-51` vs `out2.txt:300` |
| C7 | **Notional ≠ cash** | rails 2/3/4/6 compare `qty × price` against USD caps; a futures notional is `contracts × contract_size × price` and the cash at stake is the margin, not the notional | `keel/execution/guards.py:279-344` |
| C8 | **Settled-funds rail** | rail 13 requires a settled quote-currency balance ≥ notional — it structurally rejects margin buying power | `keel/execution/guards.py:415-444` |
| C9 | **Direction model** | `Side` is BUY/SELL; `Setup.direction` is `Literal["long"]`; rail 10 assumes every SELL reduces an existing long | `packages/keel-core/keel_core/types.py:25-29`, `keel/strategy/rules/base.py:39`, `keel/execution/guards.py:383-388` |
| C10 | **Correlation model** | `UNCORRELATED_ASSETS = frozenset({"PAXG"})` — hardcoded, single-member, and says nothing about oil vs gas or Tech100 vs Mag7 | `keel/execution/guards.py:98` |
| C11 | **24/7 assumption** | rail 12 treats any feed older than `interval_sec × 3` as stale — it would misfire every weekend and overnight on anything not `twenty_four_by_seven`; `YZ_PERIODS_PER_YEAR = 365` is the same assumption in the vol estimator | `keel/execution/guards.py:405-413`, `keel/analysis/indicators.py:377` |
| C12 | **Expiry / roll** | no concept exists anywhere in the codebase | — |
| C13 | **Capability gate is dead** | `BrokerCapabilities.asset_classes` is declared `{"spot"}` by both adapters and **read by nothing** — a stub, not a gate | `packages/keel-broker-api/keel_broker_api/capabilities.py:20`, `…/keel_broker_coinbase/adapter.py:37`, `…/keel_broker_fake/adapter.py:50` |

None of this is a surprise to the design: the broker-abstraction spec listed `max_leverage` and
`asset_classes` as *future* capability fields and the whole document is marked
"⏳ **FUTURE ENHANCEMENT — design only; implement later**"
(`docs/superpowers/specs/2026-07-16-keel-broker-abstraction-design.md:35-36`, `:4`).

### ⚠️ A live fragility this probe surfaced, worth fixing regardless of every decision below

keel refuses these products today **by accident, not by design**, and one leg of the accident has
already partly failed.

Verified by executing the two parsers against real probe ids:

```
'SOL-28AUG26-CDE'  -> _asset='SOL'   quote_currency_of='CDE'
'XLM-28AUG26-CDE'  -> _asset='XLM'   quote_currency_of='CDE'
'ADA-28AUG26-CDE'  -> _asset='ADA'   quote_currency_of='CDE'
'GOL-25NOV26-CDE'  -> _asset='GOL'   quote_currency_of='CDE'
'ac568fb9…c3b05e'  -> _asset='ac568fb9…c3b05e'   quote_currency_of=None
```

**Every one of those base codes is allowlisted on a running deployment**: `ADA` and `XLM` on the
live money path (`~/keel/config.live-sandbox.yaml:7-12`, allowlist `BTC ETH PAXG ADA XLM`), and
`SOL` additionally on the paper-forward path (`~/keel/config.yaml:11`). So rail 1 —
the halal allowlist, the un-overridable §14 gate — **passes an `ADA-28AUG26-CDE` futures contract
on the live config today**, because it only ever sees the string `ADA`. What
stops a live BUY is rail 13, incidentally: `quote_currency_of` returns `"CDE"`, the executor asks
the broker for a `CDE` balance (`keel/execution/executor.py:336`), gets nothing, and the rail fails
closed on an unknown balance. That is a single, unintended last line of defence, and it has two
holes: **rail 13 is exempt for SELL** (`guards.py:418`, `is_buy`) and **skipped entirely in paper
mode** (`guards.py:145`, `:267`, `:418`).

What actually prevents this in practice is that keel never *names* a futures product —
`_products.py:23` can only build `BASE-QUOTE`, `cb_client.list_products` defaults to
`product_type="SPOT"` (`keel/data/cb_client.py:169`) and `cli.py:715` calls it with no argument, and
`discover_candidates` drops every futures/equity product incidentally because their `status` is
`""` rather than `"online"` (`keel/compliance/screen.py:265`, cf. `out2.txt:272`, `out1.txt:22`).

**The defence is "keel never says the word", not "the rails reject the class."** That is the
correct thing to fix, and it is cheap. See recommendation R1.

## The halal question

This is keel's founding constraint, so it is stated precisely and **not ruled on here**. Note two
KB corrections that cut *against* reflexive rejection and must not be discarded: §65.6 —
*"speculation per se… is not prohibited"*, so price-appreciation trading is permissible and
frequency is not the criterion; and §65.1 — riba is strict-liability with no de minimis while
gharar is a **materiality threshold**, and ordinary price volatility is explicitly not gharar.
The trio riba/gharar/maisir is not one undifferentiated objection.

**Where the KB already speaks, and it is not ambiguous:**

| Mechanic | KB position | Applies to |
|---|---|---|
| Futures **settled by price difference only** | *"covered under gambling"* — *maisir*, and the operative criterion is **cash settlement / no delivery, not futurity as such**​ | every CDE contract, if none delivers |
| **Gold/silver futures** | *"futures trading in commodities like gold and silver that serve as Thaman is **forbidden**"* (Ayub Ch 4.5, §65.5) — named at the asset level | `GOL-*`, `SLR-*`, and `PAU-*` (PAXG perp) |
| **Margin/leverage** | excluded (OIC Fiqh Academy), and the KB's own framing is "a spot-only, no-leverage agent" | every futures product — all are `risk_managed_by: MANAGED_BY_FCM` |
| **Short-selling** | *"prohibited by almost all scholars"* (§65.11) — subject matter must be existing, ownable, deliverable, and the seller must hold title and risk | not a new exposure if keel stays long-only, but the *ability* to go short is what a futures build unlocks |
| **Equity-like instruments** | §71.6: equity/revenue-representing instruments need **share-style business *and financial* screening we do not perform** ⇒ **reject by capability** | the 597 common stocks, 37 ADRs, and the index futures |
| **`qabd` / possession** | §65.4/§66.2/§67.1/§71.5, quadruply sourced: possession = *ability to dispose*, exchange custody suffices, **but nothing may prevent taking delivery when desired** | a futures position is not a holding of the underlying and cannot be withdrawn at all — rail 17 (`guards.py:547-569`) is the code expression of this test and has no meaning for a contract |

**What would need a scholarly ruling before any of this could be built** — stated as questions, not
verdicts:

1. **Does a Coinbase CDE crypto future ever deliver?** The *maisir* finding in §65.11 turns on
   cash settlement, not futurity. `future_product_details` does not expose a settlement type, and
   the probe could not determine it. **UNVERIFIED — and it is the single highest-value unknown in
   this document.** If these are cash-settled, §65.11 disposes of the entire class without further
   analysis.
2. **Is the hourly funding payment riba?** `funding_interval: "3600s"` with a signed `funding_rate`
   is a periodic payment, rate-determined, attached to holding a levered position. Riba is
   strict-liability with no de minimis (§65.1), so smallness is not a defence.
3. **Does FCM-held margin constitute the impermissible borrowing, or is a fully-collateralised
   futures position a different case?** The KB's exclusion is stated at the level of "no margin",
   not per-mechanism.
4. **Is a long-dated 20DEC30 contract with funding materially "perpetual rollover so delivery never
   happens"** — the exact defect §66.3 identified as the real problem with retail FX (correcting
   §56.1's blunter "T+2 ⇒ riba")? A 2030 expiry on a position never intended to be held to it is
   structurally close.
5. **Index futures:** an unscreened basket, no delivery, no constituent business/financial
   screening. §71.6's reject-by-capability applies most strongly here.
6. **Commodity futures other than gold/silver** (oil, gas, copper, platinum): the §65.5 naming is
   specific to metals that serve as *thaman*. Whether the *qabd*/delivery objection independently
   disposes of the others is a separate question.

**This gate is enforced in code today, and enforced strictly.** `keel/compliance/screen.py:191-202`
makes `backing == "dayn"` an **unwaivable** rejection; `WAIVABLE_CRITERIA` contains exactly
`{"history"}` (`screen.py:57`) and is filtered once, up front, so no future edit can route a waiver
into a shariah branch (`screen.py:142`). Sector (`HARAM_SECTORS`, `screen.py:30-41`) and `pays_yield`
(`screen.py:185-189`) are hard failures, and a missing attestation fails closed (`screen.py:174-180`).
The screen has **no vocabulary for a contract** — it screens *assets*, with `backing ∈ {ayn, dayn,
native}`. A futures contract is none of the three. Admitting one would require extending the
attestation model itself, not just filling in a row.

## What each viable path costs

Estimates are engineer-days for a single engineer, and cover design + implementation + tests to
this repo's standard. They exclude the ruling and the onboarding.

### Path A — crypto futures (dated and/or perp-style). **~35–65 days.**

| Work | Files | Days |
|---|---|---:|
| A1 Typed `Instrument` (asset, quote, class, expiry, contract size) replacing the bare `str` | `keel_core/products.py`, `guards.py:158`, `commands/_products.py`, `cli.py:500/512/526/777/1260`, `screen.py:272`, `agent.py:307`, `guards.py:423`, `executor.py:336` — 10 parse sites, 341 `product_id` references | 5–9 |
| A2 Position + margin model: add `get_positions` to the port, an FCM balance shape, unrealized P&L | `port.py`, `results.py`, both adapters, conformance suite, `guards.py:179-189`, `execution/equity.py` | 6–11 |
| A3 Order model: contract counts, lot rounding to `base_increment`, notional = contracts × size × price | `orders.py`, `translate.py`, `sizing.py` | 4–6 |
| A4 Guard recalibration for notional-vs-margin across rails 2/3/4/5/6/13 — **safety-critical** | `guards.py` | 6–11 |
| A5 Expiry + roll: detect approaching expiry, close or roll, ledger the roll as its own order class | new module, `db.py` schema, `executor.py`, `agent.py` | 6–11 |
| A6 Compliance surface: extend `AssetAttestation` to instruments; make `asset_classes` a live gate | `screen.py`, `capabilities.py`, both adapters, `executor.py` | 3–5 |
| A7 Continuous back-adjusted series for validation (see below) | `data/`, `sim/` | 5–12 |

**A7 is not merely expensive, it is a research-integrity problem.** The Turtle's promotion rests on
§73.3's zero-trials inheritance — the rule is applied *unchanged* across assets, which is the entire
reason expansion did not cost a fresh parameter sweep (`docs/experiments/2026-07-20-candidate-universe.md:85-92`).
A dated contract listed weeks ago has no multi-year history; validating on futures means
synthesising a back-adjusted continuous series, and back-adjustment method is a *choice*. Making
that choice on data you have already seen is a trial, and it forfeits the inheritance. **The
honest cheap alternative is to trade futures on the spot-derived signal and never re-fit — which
raises the obvious question of why not just hold the spot.**

### Path B — US equities. **Not buildable. Cost is undefined, not large.**

No candles, no book, no quotes, no preview (§(b) above). Everything downstream is moot. Were
Coinbase to ship equity market data and API preview, the *additional* keel work on top of Path A's
A1/A6 would be roughly 10–20 days (opaque-hash instrument identity, the three-session trading
calendar with per-session `support_fractional`/`limit_only` flags, `trading_halted` handling,
`liquidate_only` products, the 24/7 assumptions at `guards.py:405-413` and `indicators.py:377`) —
**plus** a business-and-financial screening capability keel does not have and §71.6 says is
mandatory for equity-like instruments. Re-probe before spending anything here; the venue side is
the whole story and it may change.

### Path C — commodity and index futures. **Path A + ~8–15 days, and the worst halal position.**

Superset of Path A (same order/margin/roll machinery) plus session handling for the non-24×7
products (copper, platinum, oil, gas, and **all** the index products) plus a correlation model that
means something for oil/gas or Tech100/Mag7 (`guards.py:98` currently knows one fact: PAXG is not
crypto). Gold and silver are named prohibited in the KB. Do not start here.

### Path D — do nothing. **0 days.** Everything keel trades today keeps working.

## Recommendation

**Ranked.**

1. **R1 — Do Path D, plus one small defensive change. (recommended, ~1–2 days.)**
   Make the spot-only assumption **explicit and enforced** rather than accidental. Turn
   `BrokerCapabilities.asset_classes` (`capabilities.py:20`) from a dead stub into a real gate:
   have the executor reject an intent whose instrument class is not in the adapter's declared set,
   and have `guards._asset` refuse to parse an id that is not `BASE-QUOTE`. Today rail 1 passes
   `SOL-28AUG26-CDE` because "SOL" is allowlisted, and only rail 13 — BUY-only, paper-exempt —
   catches it. That is worth closing whether or not anything below is ever built, and it is the
   only code change this document recommends.
2. **R2 — Answer question 1 (cash-settled or delivered?) before spending anything else.**
   It is a documentation lookup plus, if needed, one email to Coinbase support. If CDE contracts
   settle in cash, §65.11 closes the entire futures family and Paths A and C are dead for ~zero
   cost. **Do this before onboarding, not after.**
3. **R3 — If and only if R2 comes back favourably: commission the scholarly ruling** on questions
   2–4 (funding, margin, effective non-delivery). Written, sourced, and recorded via
   `keel assets attest`-equivalent machinery — the screen's own standard is that an unsourced
   claim is not evidence (`screen.py:210`).
4. **R4 — Only after R2 and R3 both clear: build Path A**, dated contracts before perp-style
   (dated ones have no funding mechanic, so they carry strictly fewer open questions), and gate it
   behind the R1 capability check from day one.
5. **R5 — Re-probe equities in ~6 months.** The venue gate is Coinbase's to remove and there is no
   signal it is imminent. A 10-minute re-run of `scratchpad/probe2.py` answers it.

**Do not do:**

- ❌ **Do not complete CFM futures onboarding "just to have the option."** It is the one blocker
  removable without code or a ruling, which makes it the one most likely to be removed
  prematurely. It converts a hard 403 into an open order path while R1's gate does not yet exist.
- ❌ **Do not add any futures product id to `config.yaml:allowlist`.** `guards._asset` reduces
  `SOL-28AUG26-CDE` to `SOL` and `GOL-25NOV26-CDE` to `GOL`; the allowlist cannot distinguish a
  contract from a coin.
- ❌ **Do not read "PERP" as "perpetual."** Every contract expires (`out1.txt:21`). A strategy
  written against a no-expiry assumption would be wrong by construction.
- ❌ **Do not trade equities via this API by any route**, including a hand-wired `create_order`.
  There is no price, no book and no preview — it is a blind market order with real money.
- ❌ **Do not substitute `PAU-20DEC30-CDE` (PAXG perp) for spot PAXG.** It adds leverage, funding,
  an expiry and a *qabd* problem to an exposure keel already holds cleanly.
- ❌ **Do not re-fit the Turtle on futures series.** §73.3's zero-trials inheritance is the whole
  reason the expansion was free; re-fitting converts it into a fresh sweep.
- ❌ **Do not widen `WAIVABLE_CRITERIA` (`screen.py:57`) to make a futures instrument pass.** The
  comment there anticipates exactly this pressure: expanding it is a deliberate future decision,
  not a way to get a test green.
- ❌ **Do not reason about these equities from tokenized-stock coverage.** Venue CCM, CIK numbers
  and Apex clearing are real US shares; the on-chain product is a different, non-US thing.

## Caveats and what is UNVERIFIED

- **Settlement type of CDE contracts (cash vs physical) — UNVERIFIED.** Not exposed in
  `future_product_details`, and decisive for the halal question. R2.
- **`create_order` was never called** on any futures or equity product, deliberately. The order
  path is therefore verified *refused* for both classes but never verified *working* for either.
- **The EQUITY listing is a capped, unstable page.** 1000 is exactly the cap, and composition
  varies between calls (740/260 vs 746/254 USDC/USD on two calls). Per-ticker counts (816 distinct
  tickers; 597 common stock / 356 ETF / 37 ADR / 1 preferred / 8 SBI / 1 preferred ADR) describe
  *a* page, not the universe. The true equity universe size is **UNVERIFIED**.
- **Candle probes covered 72 hourly bars on three futures products**, not deep history. Whether
  multi-year continuous futures history is retrievable at all is **UNVERIFIED** and is a direct
  input to A7's cost.
- **Day estimates are judgement, not measurement.** They assume this repo's existing standard
  (conformance suite, guard tests, docstring discipline) and no scope growth. A7 in particular
  could be much larger, or could be correctly refused.
- **The probe reflects one account on one day.** Product availability, `view_only` flags (52 of 99
  futures were tradable) and onboarding state are all account- and time-specific.
