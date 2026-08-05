# Coinbase's new asset classes — what keel can actually trade

**Date:** 2026-08-05
**KB basis:** §65.5 (gold futures forbidden outright), §65.11 (cash-settled futures = *maisir*;
short-selling), §65.4/§67.1/§71.5 (`qabd`, triply sourced — §66.2 logs digital custodial `qabd`
as an OPEN question, not as corroboration), §71.6 (equity-like ⇒ reject *by capability*),
§65.6 (speculation per se permissible), §29.2 (scholarly divergence), §73.3 (zero-trials
inheritance).
**Status:** feasibility only. **No code changed, no product admitted, allowlist unchanged.**
**Evidence:** live read-only probe of the production account's own CDP key
(`~/keel/.env:CDP_API_KEY`) on 2026-08-05. **No `create_order` call was made** — only
`preview_order`, which is non-binding, and it was refused before it could bind.

**Reproducing this.** Every empirical claim below comes from one committed, re-runnable script,
`docs/experiments/2026-08-05-coinbase-asset-class-probe.py`. It is read-only: products, candles,
book, quotes, trades, accounts, key permissions, and `preview_order` (which places nothing).

```
cd ~/keel && ./.venv/bin/python \
    ~/Development/work/CodeGate/keel/docs/experiments/2026-08-05-coinbase-asset-class-probe.py
```

Its output carries `===` section headers — `PRODUCT TYPE CENSUS`, `FX / FOREX`, `ACCOUNTS`,
`FUTURES TAXONOMY`, `MARKET DATA -- <label>`, `EQUITIES -- identity and classification`,
`EQUITIES -- pagination and stability`, `ORDER PATH` — and every citation of the form
`probe: SECTION` below points at one. Counts, prices and the equity page composition
have already moved since 2026-08-05 and will move again; the *structural* findings (which product
types exist, which endpoints answer, which refuse and with what error) are what this document
rests on, and those reproduce.

The probe selects **front-month contracts dynamically** (`front_month`: nearest `contract_expiry`
among the tradable contracts of a group), so it survives a roll. Every dated contract id quoted in
the prose below — `BIT-28AUG26-CDE`, `GOL-25NOV26-CDE`, `SLR-27AUG26-CDE`, `CU-27AUG26-CDE`,
`PT-28SEP26-CDE`, `NOL-19AUG26-CDE`, `NGS-26AUG26-CDE`, `MC-17SEP26-CDE` — is therefore
**illustrative as of 2026-08-05 and will have rolled** by the time this is re-run. The roots
(`BIT-`, `GOL-`, the `-CDE` suffix) persist; the dates do not. The `20DEC30` perp-style ids are the
long-dated exception and will outlast the rest.

## The question

Coinbase now lists stocks, futures, "perps", indices and commodities. Can keel trade all or some of
them? Below, "can" is decomposed into four independent gates — *does the product exist on this
account's API*, *is there market data*, *is there an order path*, *does keel's code accept it* —
because they fail for entirely different reasons and cost entirely different amounts to fix.

## Verdict

| Asset class | On this account's API? | Market data? | Order path? | keel-compatible today? | Verdict |
|---|:--:|:--:|:--:|:--:|---|
| **Spot crypto** (936 listed / 936 tradable, venue CBE) | yes | yes | yes | **yes** | what keel trades. Unchanged. |
| **Crypto dated futures** (nano BTC/ETH/SOL/…: 51 / 17) | yes | **yes, full OHLCV** | **no — CFM onboarding** | no | technically reachable; blocked on paperwork, a large build, and an unanswered ruling |
| **Crypto "perp-style" futures** (`*-20DEC30-CDE`: 24 / 24) | yes | yes | no — same gate | no | same, **plus hourly funding** — the sharpest halal problem in the set |
| **Commodity futures** (gold, silver, copper, Pt, oil, gas: 17 / 6) | yes | yes | no — same gate | no | gold/silver are **named prohibited** in the KB (§65.5). Others need a ruling. |
| **Index futures** (Mag7+Crypto, AI, China, Defense, Tech100: 7 / 5) | yes | yes | no — same gate | no | index basket + no delivery + no constituent screening. Worst case in the set. |
| **US equities** (venue CCM; a 1000-cap page over a several-thousand, non-enumerable universe) | yes | **NO — none** | **no — refused by design** | no | **not buildable at any price today.** The venue gate is absolute. |
| **FX / forex** | **does not exist** | — | — | — | **not a thing on Coinbase.** No product type, no products. |
| **Options** | **not a valid `product_type`** (HTTP 400) | — | — | — | absent from the API enum, and excluded by §65.11 anyway |

Counts are `listed / tradable` (`view_only: false`). The four futures rows partition
`product_type=FUTURE` exactly: 51 + 24 + 17 + 7 = **99 listed**, 17 + 24 + 6 + 5 = **52 tradable**
(`probe: PRODUCT TYPE CENSUS`, `FUTURES TAXONOMY`). The listed-vs-tradable gap is the dated
contracts: each dated root lists ~3 expiries and only the front month is tradable.

On the options row: `OPTION_GROUP` returning 0 proves nothing on its own — `FUTURE_GROUP` also
returns 0 while `FUTURE` returns 99, so a zero `*_GROUP` count is not evidence of absence. The
finding rests instead on `product_type="OPTION"` being rejected with HTTP 400
`parsing field "product_type": "OPTION" is not a valid value`: **`OPTION` is not a member of the
enum on this API version at all.** That is a stronger statement than "none listed".

**One-line answer: no — keel can trade none of them today, and only the crypto-futures family is
even a candidate for a future build.** Two of the four gates are open for futures (it exists, it
has data; no order path, no keel support) and only one is open for equities.

## What is actually there — precision on four commonly-confused points

### 1. FX does not exist on Coinbase

`GET /api/v3/brokerage/products` accepts a `product_type` filter. The types that return anything
are `SPOT` (936), `FUTURE` (99) and `EQUITY` (1000, capped). `FUTURE_GROUP` and `OPTION_GROUP`
return zero. `product_type="FX"` and `product_type="FOREX"` are rejected outright with HTTP 400
`parsing field "product_type": … is not a valid value` — not members of the enum
(`probe: PRODUCT TYPE CENSUS`, which prints the type census and then, under *"Values the document
claims are not enum members at all"*, the `OPTION`/`FX`/`FOREX` 400s). Asking for the pairs
themselves fails too: `EUR-USD`, `GBP-USD`, `USD-JPY` and `AUD-USD` each return HTTP 404
`… is not supported` (`probe: FX / FOREX`). There is **no FX/forex product type and no FX
product**.

**What does exist, and must not be mistaken for it: fiat-*quoted* crypto spot.** 67 spot ids carry
a major-currency leg (`USDC-EUR`, `BTC-EUR`, `USDT-EUR`, `SOL-EUR`, `BTC-GBP`, `USDC-INR`, …), and
the probe measures that two independent ways which **agree exactly**: its nine-marker id sweep
(`EUR GBP JPY CHF AUD CAD NZD INR SGD`) returns 67, and a straight count of products whose
`quote_currency_id` is one of those markers also returns 67 — EUR 35, GBP 25, INR 4, SGD 1, CAD 1,
AUD 1 (`probe: FX / FOREX`). The marker list covers every fiat quote currency the venue actually
uses and finds nothing outside it. There are also stablecoin-against-stablecoin
pairs that *look* like crosses, `EURC-USDC` and `TGBP-USDC`. **All of these are crypto spot**: one
leg is a coin or a token, the settlement is the venue's ordinary spot settlement, and none of them
is a currency-against-currency contract. `EURC`/`TGBP` are stablecoins — a `dayn` claim on an
issuer, which the 2026-07-20 candidate-universe doc already flagged. Buying `BTC-EUR` is buying
BTC, not trading EUR. There is no FX offering here to evaluate.

One false positive worth naming so nobody re-derives it: the equity page contains ticker `NOK` —
Nokia's ADR, not the Norwegian krone. Any FX sweep keyed on ISO currency codes will hit it
(`probe: FX / FOREX`, the closing note).

### 2. "Stocks" here are REAL US equities, not the tokenized-stock product

This is the confusion most likely to mislead. The widely-reported Coinbase "tokenized equities"
launch is a **non-US** product issued on Base. That is **not** what this account sees. What the
Advanced Trade API returns is `product_type: EQUITY` on **venue `CCM`**, carrying
`equity_product_details` with a **CIK number**, an `equity_subtype`
(`COMMON_STOCK` / `ETF` / `ADR` / `SHARES_OF_BENEFICIAL_INTEREST`, with `PREFERRED_STOCK` and
`PREFERRED_ADR` present on some pages and absent from others), and a
`trading_day_info.trading_sessions` block with four sessions — `OVERNIGHT` / `PRE_MARKET` /
`NORMAL` / `AFTER_HOURS`. The `venue_id` on the trading-day block is
`"apex"` — an introducing-broker/clearing arrangement, i.e. genuine US brokerage rails
(`probe: EQUITIES -- identity and classification`, which prints the subtype census and then
`ticker=… fractionable=… cik=… venue_id=…` plus the session list for a sample product).

These are real shares of real US issuers. Any reasoning imported from the tokenized-stock product
(wrapper tokens, on-chain settlement, a synthetic claim) is **wrong here** and would produce the
wrong halal analysis as well as the wrong technical one.

### 3. The US "perps" are EXPIRING contracts that pay funding — not perpetual swaps

Every one of the 99 futures products carries `contract_expiry_type: "EXPIRING"`
(`probe: FUTURES TAXONOMY`, which prints the census as `{'EXPIRING': 99}`). There is not a single
`PERPETUAL` contract on the venue. What Coinbase markets as
a perp is a **long-dated December-2030 contract with an hourly funding mechanism** bolted on:

```
BIP-20DEC30-CDE   display "BTC PERP"   contract_expiry 2030-12-20T16:00:00Z
                  funding_interval "3600s"   funding_rate 0.000007   open_interest 178182
```
(`probe: FUTURES TAXONOMY` — first the group census keyed by `funding_interval`/`contract_size`,
then a **per-contract block** printed for every contract carrying a `funding_interval`, giving
`expiry`, `funding_rate`, `oi`, `quote`, `base_increment` and `risk` on one line each.)

Two consequences the label hides:

- **They still expire and still need a roll.** Just over four years out (4.4 years from this
  probe) is long, but a position
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
natural gas `NGS-26AUG26-CDE` (`probe: FUTURES TAXONOMY`, the `non_crypto=True` groups). Indices
are the same shape: `MC-17SEP26-CDE`
(Mag7+Crypto, dated) plus perp-style `AIP-`/`CHN-`/`DEF-`/`TEK-19DEC30-CDE`, **none 24×7**.

keel already holds spot gold exposure via **PAXG**, which is a spot `'ayn`-candidate token, not a
contract. There is also a `PAU-20DEC30-CDE` "PAXG PERP". Substituting a funded, expiring, levered
contract for a spot holding of the same underlying is strictly worse on every axis this document
examines.

## Three kinds of blocker, and why the distinction matters

### (a) Account / paperwork gate — zero code, and its cost to clear is UNVERIFIED

The key already has `can_trade: true` on the **Primary (DEFAULT) portfolio**
(`probe: ORDER PATH`, `api key permissions`), which is the portfolio Coinbase requires for US
futures. What is missing is the CFM/futures **onboarding**: a separate application plus CFTC/NFA
risk disclosures. The first three rows below come from `probe: ORDER PATH`; the accounts row from
`probe: ACCOUNTS`.

| Probe | Result |
|---|---|
| `preview_order` on `BIT-28AUG26-CDE` | HTTP 403 `PERMISSION_DENIED: "FCM preview orders are only enabled for onboarded users"` |
| `GET /cfm/balance_summary` | `{"balance_summary": null}` |
| `list_futures_positions` | `{"positions": []}` |
| accounts census | 7 crypto + 1 fiat, all `ACCOUNT_PLATFORM_CONSUMER`; no futures currency |

**Whether this gate is actually cheap to clear is UNVERIFIED.** Nothing in the probe speaks to CFM
eligibility, entity type, jurisdiction, or how long an application takes; "a separate application
plus CFTC/NFA risk disclosures" describes the *shape* of the requirement, not its cost. This matters
more than it looks: the whole "futures reachable, equities not" split in the verdict table rests on
this gate being the cheap kind of blocker, and that has been asserted, not measured.

What *is* established is the asymmetry, and **that is the danger**: it is the only blocker that can
be removed without touching code or answering the halal question, so it is the one most likely to
be removed first and by accident. See the "do not do" list.

### (b) Venue capability gap — NOT solvable by keel, at any budget

Futures are fine here. Equities are not, and it is not close.

Both columns come from `probe: MARKET DATA -- <label>`, which runs the identical five calls against
three front-month futures ids (dated crypto, perp-style, gold) and **two** equity ids — the sample
equity's quoted id *and its `alias`*, i.e. the same share's other quote leg, so a null result
cannot be blamed on picking the wrong one of the pair. The 1000-product `price`-field census is
`probe: EQUITIES -- identity and classification`; the preview row is `probe: ORDER PATH`.

| Endpoint | Futures | Equities |
|---|---|---|
| `get_candles` ONE_HOUR | 72 candles, full OHLCV — verified on `BIT-28AUG26-CDE`, `BIP-20DEC30-CDE`, `GOL-25NOV26-CDE` | **`n=0`** — empty at ONE_HOUR *and* ONE_DAY, on both the USDC id and its `alias` id |
| `get_product_book` | live | **HTTP 500 INTERNAL** † |
| `get_market_trades` | live | **HTTP 500 INTERNAL** † |
| `get_best_bid_ask` | live | **`{"pricebooks": []}`** |
| product payload prices | `price`, `mid_market_price`, `volume_24h` all populated (`probe: MARKET DATA -- <label>`, "price fields on the product itself") | `price`, `best_bid_price`, `best_ask_price`, `mid_market_price`, `volume_24h` all **empty strings** on all 1000 products — `probe: EQUITIES -- identity and classification` prints `price field populated: {False: 1000}`; only `high_24h`/`low_24h` populated |
| `preview_order` | 403, onboarding — a *gate* | 403 `"API order preview is not available for equities products"` — a *design decision* |

**† on the two 500s.** `HTTP 500 INTERNAL` is a *server error*, not a documented refusal, and a
fault can be fixed. Those two rows are corroborating, not load-bearing. The conclusion rests on the
aggregate of the rest: zero candles at **both** granularities on **both** quote legs, an empty
`pricebooks` array, every price field empty on all 1000 products, and an **explicit** 403 stating
that preview "is not available for equities products" — that last is a stated policy and needs no
such caveat.

**Not a market-hours artifact — checked.** Equity products carry four sessions
(`OVERNIGHT` / `PRE_MARKET` / `NORMAL` / `AFTER_HOURS`, `probe: EQUITIES -- identity and
classification`), so the obvious objection is that the probe simply ran while the market was shut
and the endpoints go live at the opening bell. It does not. The runs above land in `PRE_MARKET`;
the identical five calls were re-run at **13:35 UTC on 2026-08-05, five minutes into the `NORMAL`
session** (13:30–20:00 UTC), across `SPY`, `QQQ`, `AAPL`, `NVDA`, `TSLA` — each on **both** its
USDC-quoted and USD-quoted id, ten products in total. Every result was identical to the pre-market
one: `candles=0`, `pricebooks=[]`, HTTP 500 on book and trades, `price` and `mid_market_price`
empty, `trading_halted: false` throughout. The data is absent in-session, not merely out of hours.

**keel cannot trade an instrument it cannot get a candle for.** Every rule in
`keel/strategy/rules/` takes `candles_by_tf` (`keel/strategy/rules/base.py:120`), as do the CTS
scorer and the backtester. Sizing and equity marking do not read candles themselves — they read
values *derived* from them: `sizing.size` divides by the ATR-derived stop distance
(`keel/execution/sizing.py:34`, taking `entry`/`stop` as bare `Decimal`s) and
`equity.mark_positions` takes a `price_by_product` dict (`keel/execution/equity.py:32-55`). The
dependency is one step removed but not weaker: with zero bars there is no setup, no ATR, no stop,
no size and no mark.

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
(`probe: EQUITIES -- identity and classification`, the identity-shape dump). The real ticker lives in
`equity_product_details.ticker` and `base_currency_id`, so any allowlist keyed on `product_id`
would be keyed on a hash.

**And the listing cannot be enumerated deterministically.** This was probed three ways
(`probe: EQUITIES -- pagination and stability` for all four rows):

| Access pattern | Result |
|---|---|
| naive `product_type=EQUITY` | hard-capped at **1000** regardless of `limit` — `limit=250` → 250, but `limit=1000` and `limit=5000` both → 1000 |
| **four** identical naive calls (`STABILITY_CALLS = 4`) | calls 2/3/4 differ from call 1 in **400, 400 and 401** of 1000 ids; the **union across the four is 1868 distinct ids** — nearly double a single page, and impossible if the universe served were stable at 1000. Earlier sessions, on pairs: `\|A∩B\|=584`, then `665` |
| `offset=0, limit=500`, twice | also unstable — **261 of 500 ids differ** (an earlier session: 233) |
| cursor walk on `pagination.next_cursor` | walks well past the cap: **13 089** distinct ids across 20 pages (the probe's page cap). New-items-per-page is **non-monotonic** — +1000, +937, +592, +959, … +175, … +118, … +69, +120, +88, +653, +879 — i.e. pages re-serve ids already seen. Earlier walks reached 7 546 over 38 pages and 3 673 over 17, overlapping on only 1 477; **repeat walks do not converge on the same total** |

**Why four calls, not two.** A single back-to-back *pair* can come back **identical** — that has
been observed on a re-run — so a two-call check is not a test, it is a coin flip, and a re-runner
who happened to draw a stable pair would conclude this document was wrong. The probe therefore
makes `STABILITY_CALLS = 4` identical calls and reports both the per-call drift and the union
across all four. The **union** is the decisive figure, because exceeding the 1000-row cap cannot be
explained by a stable universe however the individual pairs land. **N≥4 before concluding
anything about stability here.**

So the universe is **several thousand products, and partially enumerable via the cursor — but not
deterministically enumerable**. Consequence for any future equity discovery or allowlist
mechanism: it cannot obtain a clean, reproducible snapshot from this endpoint. Two runs disagree
about which products exist, so "the candidate set" is not a well-defined object, a discovery diff
would report churn that is an artefact of the endpoint, and an allowlist admitted from one run
could not be re-derived from the next.

**Judgement, flagged as such:** the probe establishes the *behaviour*, not the *mechanism*. No
vendor statement is cited and none was sought. A sharded backend serving an unordered page per
shard would produce exactly this pattern — and would also be fixable, which would falsify any claim
that it is permanent. Read it as a **property measured repeatedly, across sessions and across three
different access patterns**, not as a stated design guarantee. Plan against it; do not assume it
cannot change.

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
| C6 | **Lot sizing** | sizing emits fractional `Decimal` and never rounds to a venue lot; futures need **whole contracts** (`base_increment: "1"`) | `keel/execution/sizing.py:22-51` vs `probe: FUTURES TAXONOMY`, the per-contract block |
| C7 | **Notional ≠ cash** | rails 2/3/4/6 compare `qty × price` against USD caps; a futures notional is `contracts × contract_size × price` and the cash at stake is the margin, not the notional | `keel/execution/guards.py:279-344` |
| C8 | **Settled-funds rail** | rail 13 requires a settled quote-currency balance ≥ notional — it structurally rejects margin buying power | `keel/execution/guards.py:415-444` |
| C9 | **Direction model** | `Side` is BUY/SELL and `Setup.direction` is `Literal["long"]` — long-only by type. The *assumption that a SELL reduces an existing long* is not rail 10 (which only checks a SELL cites a `rule_kind`); it lives in `_open_exposure_by_asset`, where SELL subtracts from a per-asset exposure that can never go negative, and in rail 13's SELL exemption ("it produces quote currency, it doesn't consume it") | `packages/keel-core/keel_core/types.py:25-29`, `keel/strategy/rules/base.py:39`, `keel/execution/guards.py:179-189`, `keel/execution/guards.py:418` |
| C10 | **Correlation model** | `UNCORRELATED_ASSETS = frozenset({"PAXG"})` — hardcoded, single-member, and says nothing about oil vs gas or Tech100 vs Mag7 | `keel/execution/guards.py:98` |
| C11 | **24/7 assumption** | **not a hard-rail defect** — see the note below. The real assumption is `market_feed.is_fresh`, which compares the newest stored candle to now against `interval_sec × 3`; a product whose market is closed fails it and is **skipped for the cycle** | `keel/data/market_feed.py:160-176`, called from `keel/agent.py:853` with the max age computed at `keel/agent.py:754` |
| C12 | **Expiry / roll** | no concept exists anywhere in the codebase | — |
| C13 | **Capability gate is dead** | `BrokerCapabilities.asset_classes` is declared `{"spot"}` by both adapters and **read by nothing** — a stub, not a gate | `packages/keel-broker-api/keel_broker_api/capabilities.py:20`, `…/keel_broker_coinbase/adapter.py:37`, `…/keel_broker_fake/adapter.py:50` |

None of this is a surprise to the design: the broker-abstraction spec listed `max_leverage` and
`asset_classes` as *future* capability fields and the whole document is marked
"⏳ **FUTURE ENHANCEMENT — design only; implement later**"
(`docs/superpowers/specs/2026-07-16-keel-broker-abstraction-design.md:35-36`, `:4`).

**On C11, precisely — rail 12 is not the problem.** It is worth stating what rail 12 actually
does, because the obvious reading of it is wrong. Rail 12 reads the `agent_state` key
`last_feed_ts` (`guards.py:405-413`), and `agent.py:740` stamps that key with wall-clock `now_ts`
after **every** poll, whether or not any new candle came back. It is an *agent-loop heartbeat*:
it detects that keel stopped polling, not that a market stopped printing. A closed market does
not trip it, and the code says so (`agent.py:705-706`).

The 24/7 assumption lives one layer down, in `market_feed.is_fresh`
(`market_feed.py:160-176`), which compares the **newest stored candle's timestamp** to now against
`interval_sec × FEED_STALENESS_CYCLES` (`agent.py:754`) and is called per product at
`agent.py:853`. On a market that closes, every product would fail that test for the whole of every
close. The consequence is a **per-product skip for that cycle** (`stale_products`,
`agent.feed_stale`) — the cycle still runs for everything else, and no order is placed on
stale data. That is arguably the *right* behaviour, not a defect: refusing to act on a stale
series is what the check exists for. What is genuinely missing is a session calendar, so keel
would log "stale feed" through every weekend when the true cause is "market closed", and would
never distinguish the two. That is a clarity and observability gap in a non-24/7 build, not a
safety hole.

`YZ_PERIODS_PER_YEAR = 365` (`keel/analysis/indicators.py:377`) is a real 24/7 assumption, and thin
as a blocker: it is a **default** on a keyword argument that both callers already expose
(`keel/analysis/indicators.py:381`, `:446`), so a 261-day instrument is a call-site change, not a
rewrite — and Yang-Zhang is recorded in the KB as "Built, wired into nothing"
(`docs/superpowers/references/trading-knowledge-base/README.md:11`, §79.9), so nothing in the
live path reads it today.

### ⚠️ A live fragility this probe surfaced, worth fixing regardless of every decision below

keel refuses these products today **by accident, not by design** — and the accident is thinner
than it looks. Both legs of it were probed: the rails let a live SELL through outright, and the
"keel can never name one" leg holds only for the paths keel drives itself.

Verified by executing the two parsers against real probe ids:

```
'SOL-28AUG26-CDE'  -> _asset='SOL'   quote_currency_of='CDE'
'XLM-28AUG26-CDE'  -> _asset='XLM'   quote_currency_of='CDE'
'ADA-28AUG26-CDE'  -> _asset='ADA'   quote_currency_of='CDE'
'GOL-25NOV26-CDE'  -> _asset='GOL'   quote_currency_of='CDE'
'ac568fb9…c3b05e'  -> _asset='ac568fb9…c3b05e'   quote_currency_of=None
```

(`CDE` is not even the venue's own quote currency for these — every futures product reports
`quote_currency_id: "USD"` (`probe: FUTURES TAXONOMY`, the per-contract block, which prints
`quote=USD` on each). `CDE` is a venue suffix that `rpartition("-")` mistakes for a settlement leg.)

**Every one of those base codes is allowlisted on a running deployment**: `ADA` and `XLM` on the
live money path (`~/keel/config.live-sandbox.yaml:14-19`, allowlist `BTC ETH PAXG ADA XLM`), and
`SOL` additionally on the paper-forward path (`~/keel/config.paperforward.yaml:11` — the config
`paperforward-run.sh:52` actually runs, *not* `~/keel/config.yaml`). So rail 1 —
the halal allowlist, the un-overridable §14 gate — **passes an `ADA-28AUG26-CDE` futures contract
on the live config today**, because it only ever sees the string `ADA`.

**A SELL of `ADA-28AUG26-CDE` passes every single rail on the real live config.** Verified by
executing `guards.check` against `~/keel/config.live-sandbox.yaml` and a copy of the live
`keel-live.db`, with the kill-switch off and the feed fresh (i.e. the normal operating state):

```
ADA-28AUG26-CDE  SELL  offline=False  ok=True   violations=[]  skipped=[]
XLM-28AUG26-CDE  SELL  offline=False  ok=True   violations=[]  skipped=[]
ADA-28AUG26-CDE  BUY   offline=False  ok=False  violations=['usdc_funding: available CDE balance
                                                 is unknown/unavailable -- failing closed']
ADA-28AUG26-CDE  BUY   offline=True   ok=True   violations=[]  skipped=['usdc_funding',
                                                 'withdrawal_capability']
GOL-25NOV26-CDE  SELL  offline=False  ok=False  violations=['halal_allowlist: GOL ...']
```

So the last line of defence is narrower than it looks. Only a **live BUY** is stopped, and only
incidentally: `quote_currency_of` returns `"CDE"`, the executor asks the broker for a `CDE`
balance (`keel/execution/executor.py:336`), gets nothing, and rail 13 fails closed on an unknown
balance. That single unintended rail has two holes — it is **exempt for SELL** (`guards.py:418`,
`is_buy`) and **skipped entirely in paper mode** alongside rail 17 (`guards.py:145`, `:267`,
`:418`) — and the run above shows both: a live SELL clears with an empty `violations` *and* an
empty `skipped`, and a paper BUY clears too. `GOL-*` is stopped only because `GOL` happens not to
be on the allowlist.

**Two things stand between that passing rail check and an actual live short today — and both are
account state, not instrument design.** Stating them is not a softening; it is what makes the
argument in the "do not do" list precise.

1. **keel's executor cannot *construct* that SELL out of nothing.** A SELL intent is only ever
   built on the EXIT branch, and only when `_held_position(repo, product_id) > 0`
   (`keel/execution/executor.py:355-357`), which nets **filled `live` BUYs minus SELLs of that exact
   `product_id`** out of keel's own audit log (`:373-391`). Every other SELL site —
   `place_bracket` (`executor.py:737`), `scale_out` (`:800`), `_roll_stop` (`:878`) — is reached
   only downstream of a filled BUY (`executor.py:162`), and the agent's exit path gates on the same
   `_held_position` (`keel/agent.py:505-507`). So a live futures SELL requires a **prior filled live
   futures BUY** — the one thing rail 13 vetoes. Paper fills do not help: `_held_position` filters
   `mode="live"`. The live DB confirms the state: **0 rows in `orders`**.
2. **Even a rail-passing SELL dies before it reaches the venue.** `_run_order` calls
   `broker.preview_order` immediately after `guards.check` and **re-raises on failure**
   (`executor.py:407`, `:427-433`), and preview on a futures product is refused with
   `403 PERMISSION_DENIED: "FCM preview orders are only enabled for onboarded users"`
   (`probe: ORDER PATH`; the probe previewed a BUY, and the refusal is phrased in terms of the
   *user's onboarding state*, not the side).

Neither is a designed instrument gate, and note exactly what removes each. **CFM onboarding removes
(2) outright** — it is precisely what the 403 names as missing. It does not remove (1): rail 13
would still find no `CDE` balance to report. What removes (1) is a **funded `CDE`-denominated
balance**. So the honest statement is not "a live short is one command away" — it is that
**two account-state changes, neither of them a code change and neither of them recorded anywhere
in keel, are the entire remaining distance**: complete CFM onboarding, and let a `CDE` balance
exist. And **after the first of those, the rails are the only thing left** — the rails that pass
this SELL with an empty `violations` list. That is the "do not onboard" argument at its sharpest,
not a weakening of it; it is also why the two additions to the "do not do" list below are the two
that matter.

**What actually prevents this in practice — with an important exception.** keel's *default* and
*discovery* paths cannot name a futures product:

- `_products.py:23` is the only id-**construction** path and can only emit `f"{asset}-{quote}"`;
- `cb_client.list_products` defaults to `product_type="SPOT"` (`keel/data/cb_client.py:169`) and
  `cli.py:715` calls it with no argument;
- `discover_candidates` drops every futures/equity product incidentally, because their `status` is
  `""` rather than `"online"` (`keel/compliance/screen.py:265`). `status` is `""` on all 99 futures
  and all 1000 equities, against `{'online': 928, 'delisted': 8}` on spot — read from the same
  product payloads `probe: PRODUCT TYPE CENSUS` fetches, though that section prints the **venue**
  census (`SPOT {'CBE': 936}`, `FUTURE {'FCM': 99}`, `EQUITY {'CCM': 1000}`) rather than the status
  one.

Those three are individually exact but they cover only what keel picks **by itself**. They do not
cover what an operator can **type**. `--products` takes a raw comma-separated string and validates
nothing — `_parse_products_option` (`cli.py:1253-1256`) just splits and strips, backing the flag on
`cli.py:342`, `cli.py:762` and `cli.py:1446`; `keel rules seed` repeats the same two-line splitter
inline (`keel/commands/rules.py:283`, flag at `:228`) and writes the operator's raw string straight
into `rules.params.product_id` (`rules.py:311-319`). The agent then polls exactly those strings
(`agent.py:723-725`). Verified by running it against a scratch DB:

```
$ keel --config ~/keel/config.live-sandbox.yaml --db scratch.db rules seed \
      --products XLM-28AUG26-CDE --kinds turtle_breakout --status live
seeded=1 skipped=0 status=live
  seeded: turtle_breakout:XLM-28AUG26-CDE
$ keel ... rules list
[1] turtle_breakout status=live params={... 'product_id': 'XLM-28AUG26-CDE'}
```

`--status live` is the seed flag whose own help text says it is "for the supervised live-order
test only" (`rules.py:243-244`), i.e. an operator-facing path, not a test fixture. Without it the
row lands as `candidate` and the agent — which loads only `paper`/`live` rules (`agent.py:723-724`)
— ignores it, so this needs one deliberate flag or one `rules promote`. That is a small speed bump,
not a gate. **keel can name a futures product today.**

**The defence is "keel does not name one by default, and the account is not in a state that would
let one through", not "the rails reject the class."** Every part of that is contingent — on an
operator not typing an id, and on two pieces of account state. The class rejection is the thing
that is missing, and it is cheap to add. See recommendations R1 and R2.

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
| **Equity-like instruments** | §71.6: equity/revenue-representing instruments need **share-style business *and financial* screening we do not perform** ⇒ **reject by capability**. ⚠️ §71.6 is a *token*-screening section; applying it to real US shares is an **inference**, not a direct citation — though a strong one, since it demands share-style screening precisely by analogy to shares | **the entire equity page** — every subtype on it (common stock, ETF, ADR, shares-of-beneficial-interest, preferred), plus the index futures. A given page runs roughly 565–590 common / 355–390 ETF / 36–44 ADR / 7–8 SBI, with the preferred subtypes present on some pages and absent from others — the mix moves between calls, so no count here is a fact about the universe. The *class* is what is rejected, not a count |
| **`qabd` / possession** | **triply** sourced on the operative test — possession = *ability to dispose*, exchange custody suffices, **but nothing may prevent taking delivery when desired** (§71.5 itself: *"Three sources now converge"* — §65.4 Ayub · §67.1 OIC 53/4-6 · §71.5 AAOIFI SS 18 3/5). §66.2 supplies the underlying no-resale-before-possession hadith but **explicitly declines to settle this**: *"none of these four papers addresses digital custodial qabd or crypto qabd at all… Log as an open question."* It is not corroboration | a futures position is not a holding of the underlying and cannot be withdrawn at all — rail 17 (`guards.py:547-569`) is the code expression of this test and has no meaning for a contract |

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

**The halal screen is strict — but it is an ADMISSION gate, not an order-path gate.** Everything
it does, it does strictly: `keel/compliance/screen.py:191-202` makes `backing == "dayn"` an
**unwaivable** rejection; `WAIVABLE_CRITERIA` contains exactly `{"history"}` (`screen.py:57`) and
is filtered once, up front, so no future edit can route a waiver into a shariah branch
(`screen.py:142`). Sector (`HARAM_SECTORS`, `screen.py:30-41`) and `pays_yield`
(`screen.py:185-189`) are hard failures, and a missing attestation fails closed (`screen.py:174-180`).

**But `screen_asset` never sees an order.** Its only call site is `cli.py:543`, inside the
`_screen_product` helper, which is itself reached only from operator commands — `keel assets
holdings` (`cli.py:649`), `keel assets screen` (`cli.py:778`) and the proposal report
(`cli.py:829`). Nothing in `agent.py`, `executor.py` or `guards.py` calls it, directly or
transitively. What it gates is **what a human puts on the allowlist**; what gates an order is rail
1, and rail 1 compares `product_id.split("-")[0]` against that allowlist. This is the same fact as
the ⚠️ section above, stated from the other side: the screen's strictness is real and is entirely
upstream of the order path, which is exactly why `ADA-28AUG26-CDE` reaches the rails with `ADA`'s
attestation standing behind it.

The screen also has **no vocabulary for a contract** — it screens *assets*, with
`backing ∈ {ayn, dayn, native}`. A futures contract is none of the three. Admitting one would
require extending the attestation model itself, not just filling in a row.

## What each viable path costs

Estimates are engineer-days for a single engineer, and cover design + implementation + tests to
this repo's standard. They exclude the ruling and the onboarding.

### Path A — crypto futures (dated and/or perp-style). **~35–65 days.**

| Work | Files | Days |
|---|---|---:|
| A1 Typed `Instrument` (asset, quote, class, expiry, contract size) replacing the bare `str` | **11 parse sites**: 7 literal splitters (`keel_core/products.py:22` `rpartition`; `guards.py:159`, `screen.py:272`, `cli.py:500/526/777/1260` `split("-")[0]`) + 4 `quote_currency_of` call sites (`agent.py:307`, `cli.py:512`, `guards.py:423`, `executor.py:336`). Also `commands/_products.py:23` — a *construction* site, not a parse site — and `proposer.py:68`, which documents the same coupling in a comment. 341 `product_id` references overall | 5–9 |
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

### Path B — US equities. **Not buildable today, at any price.**

No candles, no book, no quotes, no preview (§(b) above). Everything downstream is moot, so **today
this path has no cost, because it has no start** — the number below is not a quote, it is the cost
of the *hypothetical* successor to a venue change that has not happened and may never.

*If* Coinbase shipped equity market data and API preview, the *additional* keel work on top of
Path A's A1/A6 would be roughly **10–20 days**: opaque, non-deterministic instrument identity (the
64-char hash *and* the unstable listing above — an equity allowlist would need a locally pinned id
set, since the venue cannot re-serve one); the **four**-session trading calendar
(`OVERNIGHT`/`PRE_MARKET`/`NORMAL`/`AFTER_HOURS`) with per-session
`support_fractional`/`limit_only` flags; `trading_halted` handling; `liquidate_only` products; and
a session-aware replacement for the market-closed-looks-like-stale-feed behaviour in
`market_feed.is_fresh` (see C11 — a clarity fix, not a rail fix; `keel/analysis/indicators.py:377` is a
one-argument default and barely counts). **Plus** a business-and-financial screening capability
keel does not have and §71.6 (by inference) says is mandatory for equity-like instruments — that
one is not day-estimable at all. Re-probe before spending anything here; the venue side is the
whole story and it may change.

### Path C — commodity and index futures. **Path A + ~8–15 days, and the worst halal position.**

Superset of Path A (same order/margin/roll machinery) plus session handling for the non-24×7
products (copper, platinum, oil, gas, and **all** the index products) plus a correlation model that
means something for oil/gas or Tech100/Mag7 (`guards.py:98` currently knows one fact: PAXG is not
crypto). Gold and silver are named prohibited in the KB. Do not start here.

### Path D — do nothing. **0 days.** Everything keel trades today keeps working.

## Recommendation

**Ranked.**

1. **R1 — Reject any intent whose settlement leg the adapter does not declare. (~1 hour. Do this
   first.)**
   The whole class closes today, with no new instrument model, using machinery the rails already
   trust. `guards.check` already calls `quote_currency_of(intent.product_id)` at `guards.py:423`;
   the Coinbase adapter already declares
   `quote_currencies=frozenset({"USD", "USDC"})` (`packages/keel-broker-coinbase/keel_broker_coinbase/adapter.py:36`).
   Veto — on **both** sides, not only BUY — any intent whose `quote_currency_of(product_id)` is not
   in that declared set. Verified by executing `quote_currency_of` over every real product id the
   venue returns:

   | product type | `quote_currency_of` | in the declared `{USD, USDC}` |
   |---|---|:--:|
   | `SPOT` (936) | `USD` 406, `USDC` 410, `EUR` 35, `GBP` 25, `USDT` 23, `BTC` 24, `ETH` 6, `INR` 4, `SGD`/`CAD`/`AUD` 1 each | 816 / 936 |
   | `FUTURE` (99) | **`CDE` on all 99** — the venue suffix, mis-parsed as a settlement leg | **0 / 99** |
   | `EQUITY` (1000) | **`None` on all 1000** (a 64-char hash has no separator), and `None` on all 813 `alias` ids too | **0 / 1000** |

   One check, both sides: every futures id and every equity id gone. **Where it goes matters:**
   `guards.check(intent, repo, config, now_ts)` has no broker handle, so either `_run_order` reads
   `broker.capabilities().quote_currencies` and vetoes before `guards.check`
   (`executor.py:407`) — the cheaper wiring — or the declared set is threaded into `guards.check`
   as an argument. Pick one deliberately; do not hardcode `{"USD", "USDC"}` a second time in
   `guards.py`, or the adapter's declaration stops being the source of truth. It also tightens spot — the
   120 non-`USD`/`USDC`-quoted pairs would be rejected too, which is exactly what the adapter's own
   declaration already says should happen, and none of them is reachable today: all three
   deployment configs set `quote_currency: USD`, so `_history_product` can only derive `-USD` ids.
   *Blast radius today is nil, and that is what makes it safe to ship in an hour:* the live DB holds
   **6 rules, every `product_id` of the form `BASE-USD`** (`BTC/ETH/PAXG/ADA/XLM-USD`, plus the BTC
   DCA rule) and **0 rows in `orders`**; all 936 spot ids have **exactly one hyphen**.
   **Ship the regression test with it, not after:** a guard test asserting that a SELL of
   `ADA-28AUG26-CDE` is vetoed (`tests/execution/test_guards.py`). That test is half the
   deliverable — without it this silently regresses the next time the rails are edited, and the
   failure mode is a live short.
2. **R2 — Then make the spot-only assumption structural. (~2–4 days.)**
   R1 is a settlement-leg check, not an instrument model; it would not stop a hypothetical future
   product that happens to settle in USD. Turn `BrokerCapabilities.asset_classes`
   (`capabilities.py:20`) from a dead stub into a real gate — and price it honestly, because the
   obvious phrasing hides the cost: **`OrderIntent` carries no instrument class at all** (C1/C2 —
   the id is a bare `str` and the only construction path is `f"{asset}-{quote}"`), so "reject an
   intent whose class is not in the adapter's declared set" silently requires inventing an
   id→class classifier first. That classifier is a slice of **A1**, not of A6. Scope it in
   deliberately or do not start.
   Two further pieces:
   - **`guards._asset` must return a *violation*, never raise**, on an id that is not `BASE-QUOTE`.
     `_asset` is not called only on the intent: `_open_exposure_by_asset` runs it over **every
     historical filled order** (`guards.py:183`). If a malformed id raises there, one bad audit row
     turns a veto into a crashed agent cycle — strictly worse than the hole being closed. Validate
     the intent's own id into `violations`; on history, skip-or-flag the row and keep going.
   - **`--products` / `rules seed` should reject an inadmissible id where the operator types it**
     (`cli.py:1253-1256`, `rules.py:283`), not where the agent trades it.
   *On the estimate:* this is the `asset_classes`-gate half of **A6** (3–5 days), plus the A1 slice
   above and the CLI-entry rejection, minus A6's `AssetAttestation`-to-instruments extension. It is
   not additive on top of A6; if Path A is ever built, A6 subsumes it.
3. **R3 — Answer question 1 (cash-settled or delivered?) before spending anything else.**
   It is a documentation lookup plus, if needed, one email to Coinbase support. If CDE contracts
   settle in cash, §65.11 closes the entire futures family and Paths A and C are dead for ~zero
   cost. **Do this before onboarding, not after.**
4. **R4 — If and only if R3 comes back favourably: commission the scholarly ruling** on questions
   2–4 (funding, margin, effective non-delivery). Written, sourced, and recorded via
   `keel assets attest`-equivalent machinery — the screen's own standard is that an unsourced
   claim is not evidence (`screen.py:210`).
5. **R5 — Only after R3 and R4 both clear: build Path A**, dated contracts before perp-style
   (dated ones have no funding mechanic, so they carry strictly fewer open questions), and gate it
   behind the R2 capability check from day one.
6. **R6 — Re-probe equities in ~6 months.** The venue gate is Coinbase's to remove and there is no
   signal it is imminent. Re-running `docs/experiments/2026-08-05-coinbase-asset-class-probe.py`
   (command at the top of this document; ~1 minute) answers it: if the `MARKET DATA -- equity …`
   sections return candles instead of `n=0`, and `ORDER PATH` stops returning
   `"API order preview is not available for equities products"`, the situation has changed.

**Do not do:**

- ❌ **Do not complete CFM futures onboarding "just to have the option."** It is the one blocker
  removable without code or a ruling, which makes it the one most likely to be removed
  prematurely. It converts a hard 403 into an open order path while R1's and R2's gates do not yet
  exist.
- ❌ **Do not fund or create a `CDE`-denominated balance on the account.** Rail 13's veto of a live
  futures BUY is **entirely** an artefact of `quote_currency_of` returning `"CDE"` and the broker
  having no such balance to report (`executor.py:336`, `guards.py:423`). A positive `CDE` balance
  would satisfy rail 13 on its own terms and silently disarm the only rail standing between the
  live config and a futures entry — no code change, no config change, and nothing in the logs
  saying a defence was removed. Pair this with the onboarding warning above: those two account
  actions are the whole distance between today and a live futures position.
- ❌ **Do not add any futures product id to the `allowlist` of any config.** `guards._asset`
  reduces `SOL-28AUG26-CDE` to `SOL` and `GOL-25NOV26-CDE` to `GOL`; the allowlist cannot
  distinguish a contract from a coin.
- ❌ **Do not promote a rule whose `product_id` you did not construct via `_history_product`.**
  `keel/commands/_products.py:14-23` is the only id-construction path in the codebase and can emit
  only `f"{asset}-{quote.upper()}"`. Any `product_id` that reached the `rules` table by another
  route — a hand-typed `--products`, a hand-edited row — has been validated by nothing at all, and
  `rules promote` is the step that hands it to the agent.
- ❌ **Do not pass a futures or equity id to `--products`, on any command.** This is the live hole,
  not a theoretical one: `--products` is unvalidated (`cli.py:1253-1256`, `rules.py:283`), and
  `keel rules seed --products <futures-id> --status live` puts that id in the `rules` table where
  the agent will poll it. The allowlist warning above is necessary but **not sufficient** — it
  covers what an operator writes in a config file, not what an operator types at a prompt.
- ❌ **Do not read "PERP" as "perpetual."** Every contract expires — `contract_expiry_type` is
  `EXPIRING` on all 99 (`probe: FUTURES TAXONOMY`). A strategy
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
  `future_product_details`, and decisive for the halal question. R3.
- **The cost of clearing CFM onboarding — UNVERIFIED.** Nothing in the probe speaks to eligibility,
  entity type, jurisdiction or elapsed time; "solvable by the operator" is a judgement. It is load
  bearing: the "futures reachable, equities not" split in the verdict table depends on it. See §(a).
- **`create_order` was never called** on any futures or equity product, deliberately. The order
  path is therefore verified *refused* for both classes but never verified *working* for either.
- **The EQUITY listing is capped at 1000 and non-deterministic — this is now MEASURED, not
  unknown.** See §(b): the naive call is hard-capped at 1000 for any `limit`; four identical calls
  differ from the first by 400/400/401 ids and union to 1868; `offset`+`limit` is unstable too
  (261 of 500 differed); and a cursor walk reaches 13 089 distinct ids over 20 pages, with earlier
  walks at 7 546 over 38 and 3 673 over 17 overlapping on only 1 477, new-items-per-page
  non-monotonic throughout. **The universe is several thousand and partially enumerable, but not
  deterministically enumerable** — its exact size remains UNVERIFIED and, on this endpoint's
  behaviour, may not be a well-defined quantity. **A back-to-back pair can come back identical**;
  use N≥4 calls, and read the union, not the pairwise diff. Any subtype or ticker count
  (587 common / 368 ETF / 38 ADR / 7 SBI and no preferred on one call; 574 / 375 / 44 / 7 on
  another; 565 / 390 / 36 / 8 on a third) describes *a* page, not the universe.
- **Candle probes covered 72 hourly bars on three futures products**, not deep history. Whether
  multi-year continuous futures history is retrievable at all is **UNVERIFIED** and is a direct
  input to A7's cost.
- **Day estimates are judgement, not measurement.** They assume this repo's existing standard
  (conformance suite, guard tests, docstring discipline) and no scope growth. A7 in particular
  could be much larger, or could be correctly refused.
- **The probe reflects one account on one day.** Product availability, `view_only` flags (52 of 99
  futures were tradable) and onboarding state are all account- and time-specific. The script is
  committed precisely so the next reader re-measures rather than trusts these numbers.
