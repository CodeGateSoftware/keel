# US equities — can keel trade stocks, from Coinbase or from anyone else?

**Date:** 2026-08-09
**KB basis:** **§71.6** is the centre of this document. Its screening axis is *what an instrument
legally REPRESENTS*, not only what it does: utility = `Haqq`, tradable if the project is compliant;
**equity/revenue-bearing instruments require share-style business AND FINANCIAL screening "we do
not perform" ⇒ REJECT BY CAPABILITY**; buy-back-dependent = hard reject
(`docs/superpowers/references/trading-knowledge-base/sources/source-71.md:385`ff, and the §71.6
rows in `.../README.md:126`). §65.10 supplies the other half and cuts the opposite way: it
reproduces the Dow Jones Islamic Market Index screen *in full* — halal core business, debt ÷
12-month avg market cap < 33%, (cash + interest-bearing securities) ÷ market cap < 33%,
receivables ÷ market cap < 33%, non-permissible income ≤ ~5% with the excess purified — and records
that keel's crypto screen is **stricter** than that standard, "a deliberate over-compliance". That
sentence is the seed of §3.1, the open question this document hands forward unresolved. ⚠️ §65.10's
DJIM description is faithful to Ayub but **no longer describes DJIM**, which retired two of those
three ratios in 2023 and now uses a 24-month window — one of two corrections §3 records against the
KB's only screening entry.
§29.1–29.2 name **AAOIFI** as the authoritative screening-standards reference and license a
conservative divergence from it; §71.4b/§71.5 already cite **AAOIFI Shari'ah Standard No. 18** §3/5
directly — which is why **Shari'ah Standard No. 21 (*Financial Paper — Shares and Bonds*) is
recorded in §3 as the decided methodology**, a sibling standard rather than a new authority.
§65.9 (income purification, report-only, `keel/compliance/purification.py`) is the machinery a
dividend-bearing instrument would need, and SS 21 makes purification an obligation rather than an
option. §65.6 (speculation per se is permissible) and §65.1 (riba strict-liability with **no de
minimis**, gharar a materiality threshold) keep this from collapsing into a reflexive rejection —
and §65.1 is also what makes §3.1 a genuine tension rather than a formality.
**Status:** feasibility only. **No code changed, no adapter added, no dependency introduced.**
This assessment is the entire deliverable — it is a settled decision record, not a plan handed to a
build. Nothing in §6 is proposed or scheduled; §7 states what would have to be true for the
question to be worth asking again.
**Evidence:** three kinds, deliberately not conflated — and §1 is a worked example of why that
separation earns its keep, because the three disagree there. (1) **Measured by probe**, and this
category now carries **two dates that must not be merged.** The 2026-08-05 measurements are
inherited from the committed, re-runnable
`docs/experiments/2026-08-05-coinbase-asset-class-probe.py` and the study it produced
(`docs/experiments/2026-08-05-coinbase-asset-class-feasibility.md`). **That same committed script
was then re-run against the production CDP key on 2026-08-09** — read-only and POST-guarded,
`create_order` never called — as part of an adversarial fact-check of this document. Every
2026-08-09 measurement below carries that date explicitly; where the two runs disagree, **both are
printed and the disagreement is itself the finding** (§1a). (2) **Verified against the working
tree** — every keel-side line/file citation below was executed or read on 2026-08-09; where
this document says a thing does not exist, that is a `grep` over the tree, stated as such. Four
citations inherited from the 2026-08-05 study had drifted and were corrected against the current
tree rather than copied forward. (3) **Read in vendor docs on 2026-08-09** — the external Coinbase
and broker claims, each carrying a URL and that read-date.

⚠️ **A note on how category (3) was disciplined, because it changed what this document is willing to
print.** The externally-sourced claims went through **three** passes: delegated research, then
direct verification of the load-bearing ones, then an adversarial fact-check that re-opened the
sources a fourth time and re-ran the probe. **No pass simply confirmed the one before it.** The
second refuted two claims the brief supplied and reduced several the first had asserted with
confident specificity — occurrence counts, an enum's value list, a regulator's file number, a press
quotation. The third then went the other way on two of those withdrawals: the Coinbase-for-Agents
roadmap wording and the equity trading-session enum both **turned out to be readable at source**,
and are restored below with their verbatim text and their URLs (§1d, §5/E3). Everything still
unconfirmed is **withdrawn and printed nowhere**, even where it is probably true, and the
withdrawals are itemised in the caveats. Where a source is silent this document says "docs silent";
where a claim could not be confirmed it says **"unverified"** or withdraws it outright; and where an
earlier pass asserted something that did not survive checking, the correction is made **in place and
attributed and dated**, never quietly dropped.

## The question

keel trades halal-screened spot crypto on Coinbase. The question asked of it is short: **can we add
US stocks — from Coinbase, and if not, from another broker?**

That decomposes into three, and they fail for entirely different reasons at entirely different
costs:

1. Does the venue keel already holds a key for sell stocks through an API? (**Yes — and the brief
   for this document said no.** The correction is in §1b. keel's own account measurably could not
   use it, and three documented equity rules conflict with keel's execution model, but "no
   securities endpoint" was simply wrong.)
2. Would keel's *charter* permit a stock even if a venue did? (**Not today** — and this is the
   finding that matters, because it is broker-independent and no probe can move it.)
3. If the charter gate were cleared, what would the engineering cost? (**Large but tractable** —
   and Coinbase would still probably not be the venue, for reasons that are now about fit rather
   than about existence.)

## Verdict

**No, keel cannot add US equities today — but the reason is not the one the Coinbase study
implies, and it is not the reason cTrader was refused. The binding gate is keel's own screening
CAPABILITY, not the instrument. Stocks are "not yet", where a CFD is "never".**

| Gate | What it asks | Result | Kind of blocker |
|---|---|---|---|
| **Coinbase venue** | is there an equities order path on Advanced Trade? | ⚠️ **YES — the brief said no and was wrong.** Equity orders route through the ordinary create-order endpoint, addressed by `product_id`, with `equity_order_metadata` carrying the session and time in force | corrected in §1b. **Not the blocker.** |
| **Coinbase, as measured** | could keel's own account actually use it? | **NO, and the 2026-08-09 re-probe made it worse, not better.** Preview refused 403 with a **product-class** message, and **zero market data on every surface tried** — no candles over a 30-day window, no closes on regular trading days, empty price strings on all 1000 products | **re-measured, no longer "stale". Independently decisive** |
| **Coinbase market data** | can an algo price an equity at any granularity? | **NO — the vacuum is total.** `get_candles` ONE_DAY over 30 days returns **n=0** across 21 trading days on both quote legs; `recent_trading_days` returns empty open/close for regular sessions; book and trades 500; **zero equities mentions in any WebSocket channel doc** (2026-08-09) | **not a rollout artifact and not a weekend artifact.** A second, self-standing refusal |
| **Coinbase, for keel specifically** | do the documented equity rules fit keel's execution model? | **NO — three conflicts.** No preview (fatal to `executor.py:445`), no attached orders (breaks `place_bracket`), no `quote_size` outside the normal session (breaks `MarketIOCByQuote`) | real, and survives however the row above resolves |
| **Charter — §71.6** | may keel hold an instrument representing equity in an issuer? | **NO — reject BY CAPABILITY.** Share-style *business and financial* screening is mandatory and keel performs none | **capability gap, not a prohibition.** The true blocker. Buildable. |
| **Screen schema** | can `asset_attestations` even express the answer? | **NO** — the row is `(asset, sector, backing ∈ {ayn,dayn,native}, pays_yield, source)`; there is no vocabulary for debt ÷ market cap | schema change; part of the capability gap |
| **Rail 19 (`spot_instrument`)** | is the id a spot `BASE-QUOTE` shape? | **NO for any equity id** — and there is deliberately **no config field to widen it** | keel's own charter code. Mandatory, safety-critical change. |
| **Rail 18 (`settlement_currency`)** | does the settlement leg parse to a configured currency? | depends on the adapter's id spelling — `AAPL-USD` would pass | not a defence here; rail 19 is |
| **Broker port on the live path** | can any adapter actually trade? | **NO** — `executor.py` types `broker: Any` and calls raw `CoinbaseClient` signatures; `_common.py` constructs `CoinbaseClient` directly and never calls `load_broker()`; no `broker:` key in any config | temporary, scheduled (Phase B). Two migrations, not one. |
| **Session calendar** | does keel know a market can be closed? | **NO** — no `is_market_open`, no exchange calendar anywhere in the tree. A closed market currently reads as a **stale feed** | new subsystem. Observability defect, not a safety hole |
| **Existing Robinhood adapter** | does keel's second adapter reach stocks? | **NO** — it is built against the Robinhood **Crypto** Trading API v2, and there is still no official Robinhood **REST** equities API. ⚠️ Robinhood *did* ship official MCP-based equities agentic trading on 2026-05-27 — a separate funded account and a tool surface, not a `Broker` (§4) | a common and expensive misconception, and the brief's version of it was also out of date |
| **Rail 13 (`usdc_funding`)** | settled quote balance ≥ notional, no ACH/margin | **PASSES, and is a point in keel's favour** — it already encodes a cash-account, no-margin posture | the one gate T+1 equities *fit* |

**One-line answer: the venue question turned out to be the easy one — Coinbase's API does support
equities, keel's account measurably could not use them, and a better venue exists anyway (Alpaca).
The thing actually standing between keel and a stock is a shariah financial-ratio screen nobody has
built, which no choice of broker fixes and no probe can measure.**

**Two independent refusals, and each is sufficient on its own. They should be read separately, not
stacked.**

1. **The charter gate (§2).** keel performs no share-style *financial* screening, §71.6 requires it,
   and that is a **capability keel does not have**. It is broker-independent, it is unaffected by
   anything a probe can return, and it is the reason the answer is no.
2. **The Coinbase surface is unusable for an algo, on market data alone (§1a, 2026-08-09).** There
   are no bars, no closes, no quotes and no stream — on any endpoint tried, for any of 1000
   products, over a 30-day window covering 21 trading days. Every rule in `keel/strategy/rules/`
   takes `candles_by_tf`. **A venue that cannot be priced is not a venue at any price**, regardless
   of what its order endpoint documents. This one is Coinbase-specific and would be answered by
   choosing Alpaca; it does not touch reason 1.

**The strongest evidence for that framing is that this document's own venue premise collapsed and
the verdict did not move.** The brief held that Coinbase exposes no securities endpoint; §1b
corrects that outright. Had the charter gate been a formality, that correction would have flipped
the answer. It changes nothing, because the binding constraint was never the venue — which is
exactly what makes §2, not §1, the finding worth keeping.

**Two things this document decides, and two it deliberately does not.** It decides that the answer
is *no* and that the answer is settled rather than pending (§7). It decides that **AAOIFI Shari'ah
Standard No. 21 is the methodology** if the screen is ever built, and quotes its criteria from the
standard's own text (§3). It does **not** decide whether keel would adopt that standard's ratio
tolerances as written, nor whether it would accept clause 3/4/6/1's sell-before-period-end
purification exemption — both would be places where keel's compliance posture became *less*
conservative than its own precedent, and both are stated in full and left open at **§3.1**.

## 1. Coinbase equities — what is actually there, and a correction to the brief

### (a) keel's own committed probe — measured 2026-08-05, **re-measured 2026-08-09**

Same script both times:
`docs/experiments/2026-08-05-coinbase-asset-class-probe.py` — read-only, POST-guarded,
`create_order` never called — run against the production account's own CDP key. The 2026-08-05 run
and the study it produced (`docs/experiments/2026-08-05-coinbase-asset-class-feasibility.md`) are
**inherited**; the 2026-08-09 run is **new to this document** and was performed by an adversarial
fact-check of an earlier draft. Both dates are printed. Where they disagree, the disagreement is the
finding.

**Inherited, 2026-08-05:**

- `product_type=EQUITY` returns products — **1000 of them, and that number is a cap, not a count.**
  The listing was measured as **not deterministically enumerable**: four identical calls differed
  from the first in 400/400/401 ids and unioned to **1868** distinct ids; `offset`+`limit` was
  unstable (261 of 500 differ); a cursor walk reached 13 089 distinct ids over 20 pages and repeat
  walks did not converge. ⚠️ **This no longer reproduces — see the 2026-08-09 correction below.**
- **Zero market data at any granularity.** `get_candles` returns `n=0` at ONE_HOUR *and* ONE_DAY,
  on both the USDC-quoted id and its `alias` USD-quoted twin. `get_best_bid_ask` returns
  `{"pricebooks": []}`. `price`, `best_bid_price`, `best_ask_price`, `mid_market_price` and
  `volume_24h` are **empty strings on all 1000 products**. Book and trades return HTTP 500 —
  corroborating only, since a fault can be fixed.
- **Not a market-hours artifact.** The identical calls were re-run five minutes into the `NORMAL`
  session across `SPY`/`QQQ`/`AAPL`/`NVDA`/`TSLA`, on both quote legs, ten products: identical
  results, `trading_halted: false` throughout.
- **Preview is refused.** `preview_order` returns 403
  `"API order preview is not available for equities products"`. keel's
  executor calls `broker.preview_order` unconditionally and **re-raises on failure**
  (`keel/execution/executor.py:445`), and the Coinbase adapter declares
  `supports_native_preview=True, synthesizes_preview=False`, so there is no fallback.
- The equity `product_id` is an **opaque 64-char hex hash** with the ticker buried in
  `equity_product_details.ticker`.

#### ⚠️ Re-measured 2026-08-09 — three corrections and one decisive new finding

**1. The market-data vacuum is worse than 2026-08-05 recorded, and it is the decisive measurement in
this document.** The re-run was performed on a Sunday, so it was deliberately designed to be
**weekend-independent** — every probe below asks about *past trading days*, not about a live
session:

- `get_candles` at **ONE_DAY over a 30-day window** returns **n=0**. That window spans **21 regular
  trading days**. Zero bars. Run on both quote legs — the USDC-quoted id and its `alias`.
- `recent_trading_days` for **2026-08-05, 2026-08-06 and 2026-08-07**, each returned by Coinbase
  itself as `TRADE_DATE_TYPE_REGULAR`, carries `market_open_price: ''` and `market_close_price: ''`.
  **Coinbase's own product payload says those were regular US trading days and then declines to say
  what the stock opened or closed at.**
- `last_market_day_close_price`, `open_price`, `volume_today`, `price`, `best_bid_price`,
  `mid_market_price` and `volume_24h` are **empty strings on all 1000 products**.
- `get_product_book` and `get_market_trades` return **HTTP 500**; `get_best_bid_ask` returns
  `{"pricebooks": []}`.
- **Zero mentions of equities in any WebSocket channel documentation** — there is no streaming path
  either.

**There is no granularity, no historical depth, no snapshot and no stream. This is not a rollout
artifact, a weekend artifact or a fault: it is the absence of a market-data product.** Every rule in
`keel/strategy/rules/` takes `candles_by_tf`; with no bars there is no setup, no ATR, no stop, no
size and no mark. **This alone makes the Coinbase equity surface unbuildable for an algo, and it
stands entirely independently of the charter argument in §2.**

**2. ⚠️ Correction against the 2026-08-05 study: the "non-deterministically enumerable universe"
finding is GONE.** Re-measured 2026-08-09, the listing is **stable**: four identical calls produced
**zero drift**, and a clean cursor walk advanced **+1000 ids per page** to a terminating total of
**19 188 distinct ids**. Whatever produced the 2026-08-05 instability — a paginator bug, a rollout
in progress — has been fixed. **The 2026-08-05 study's enumeration finding should not be carried
forward by anyone, and the id-instability line in this document's own §1c is retired with it.** The
64-char-hash identity problem is unaffected and still stands.

**3. ⚠️ Correction against the 2026-08-05 study: "the order path is refused by design" was never
established.** That claim rested **solely on a preview 403**. `create_order` was never called — then
or now, deliberately. A refused *preview* is evidence about the preview endpoint; generalising it to
"the order path is refused" was an over-reach, and this document made the same over-reach in its own
first draft. **What is established is: preview is refused. What is unknown is whether `create_order`
would be.**

**4. The 403's wording is a product-class statement, not an entitlement one — and the contrast is
sharp.** The message is *"API order preview is not available for equities products"*. Compare how
the same API says "you are not onboarded" for futures: *"FCM preview orders are only enabled for
onboarded users."* Coinbase has a house phrasing for an entitlement gap, and **this is not it.** The
equities message names the *product class*, not the caller. That materially weakens the
"entitlement or rollout" hypothesis this document previously left open, and it points the same way
as finding 1: the surface is listed, not launched.

**5. ⚠️ And a finding that cuts the other way, recorded because suppressing it would be exactly the
failure this document is trying to avoid.** `equity_trading_flags`, read across all 1000 equity
products on 2026-08-09, returns **`tradable: true, buy_enabled: true, sell_enabled: true` on
998 of 1000**, with `view_only: false`, `trading_disabled: false`, `liquidate_only: false` and
`trading_halted: false`. **The products declare themselves tradable.** Taken with §1b's documented
order path, the honest reading is that the *order* half of this surface may well work and nobody has
called it; what is missing is not permission to trade but anything to trade *on*. That is a
different — and for keel, equally fatal — problem.

That study's verdict row read **"not buildable at any price today. The venue gate is absolute."**
⚠️ **The first half stands, and the 2026-08-09 re-probe strengthens it; the second half is too
strong, and §1b below is why.** "Absolute" was an inference about the venue drawn from an account's
behaviour, and the documented API contradicts it. **"Not buildable at any price" survives — but on
market data, not on the order path.** That correction is made here rather than left for a reader to
trip over.

What the study established that **is** unaffected, and that this document depends on: these are
**real US shares** — venue `CCM`, CIK numbers, `venue_id: "apex"` clearing — and not the
tokenized-stock product. Any reasoning imported from tokenized equities is wrong here (§1e).

⚠️ **On the brokerage launch itself — the substance behind venue `CCM` — this document deliberately
asserts very little, and the reason is methodological rather than incidental.** An earlier draft
carried a precise account: two-stage rollout with specific dates, a FINRA BrokerCheck firm number,
an SEC Form X-17A-5, an Apex press-release quote, and commission/settlement terms from Coinbase's
help pages. **None of it could be re-confirmed at write-up time** — `coinbase.com` and
`help.coinbase.com` return HTTP 403 to automated fetching (re-tested 2026-08-09), and the
non-Coinbase sources were not independently re-read. It is therefore **withdrawn rather than
printed**, since a decision record that cites a regulator's file number should have opened it.

What survives is what keel's own probe measured and what the KB already holds, which is enough for
every argument this document makes: the equity products carry **venue `CCM`**, **CIK numbers**, and
`venue_id: "apex"` on the trading-day block — an introducing-broker/clearing arrangement, i.e.
genuine US brokerage rails, not tokenized wrappers. **That these are real US shares cleared through
Apex is repo-sourced and solid.** The brokerage's launch chronology, its exact regulatory filings,
and whether the retail product is app-only or also web are **unverified here and not relied on
anywhere below** — the brief's specific "2026-01-22, app-only" framing should be treated as
unconfirmed rather than as corrected-to-something-else.

### (b) ⚠️ A correction to this document's own premise — read 2026-08-09

**This document was commissioned on the assumption that the Advanced Trade API exposes no
securities order path and that `product_type` routing covers SPOT and FUTURE only. That assumption
is wrong, and it is corrected here rather than quietly dropped.**

Verified against the authoritative OpenAPI specification
(https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/advanced-trade-spec.yaml)
and against the rendered create-order reference
(https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/orders/create-order), both
read 2026-08-09 — `docs.cdp.coinbase.com` is ungated, unlike `coinbase.com` itself, which returns
HTTP 403 to automated fetching. **Every finding below was opened directly at least twice, on
separate passes and by separate fetches.**

- **`EQUITY` is a member of the `ProductType` enum.** The full enum is
  `UNKNOWN_PRODUCT_TYPE, SPOT, FUTURE, EQUITY, OPTION_GROUP, FUTURE_GROUP`.
- ⚠️ **PRECISION, because an earlier draft of this document got it backwards and the error is
  load-bearing: `product_type` is a LISTING and FILTERING dimension, not an order-routing one.**
  `create_order` has **no `product_type` field at all** — an order is addressed by `product_id`.
  `product_type` is what you pass to *list products* to enumerate equities, what you set on *list
  orders* to filter to `EQUITY`, and what *get order* echoes back on the response. Saying "`EQUITY`
  is a valid `product_type` for order placement" is a category error and should not be repeated.
  What makes an order an equity order is the `product_id` you address and the
  `equity_order_metadata` you attach.
- **Equity orders go through the ordinary `POST /api/v3/brokerage/orders` endpoint** — there is no
  separate securities endpoint, and none is needed. The create-order reference carries its own
  equities paragraph, verbatim:

  > **Equities:** Use the canonical `product_id` returned by the Products API, not the display
  > ticker. Include `equity_order_metadata` to select the trading session and time in force. Use
  > `market_market_ioc` with `MARKET_GFD` for market orders, which are supported only during the
  > normal session. Use `limit_limit_gtc` with `LIMIT_GFD` or `LIMIT_GTC` for limit orders. In
  > pre-market, after-hours, overnight, or multi-session trading, specify a positive whole-share
  > `base_size`; `quote_size` and fractional sizing are not supported. Attached orders are not
  > supported for equities.

- **`equity_order_metadata` is a real request field on BOTH `create-order` and `preview-orders`**,
  carrying `equity_trading_session` and `displayed_order_config`. It is documented as *not* to be
  sent on cancel. The 2026-08-05 probe independently found `equity_product_details` on the product
  payload, with subtypes and a four-session `trading_day_info` block — so the equity surface is
  threaded through products, orders and fills rather than bolted to one endpoint.

⚠️ **One item withdrawn by an earlier pass is now RESTORED, and one stays withdrawn.** Restored: the
trading-session enum, which is directly readable on the create-order reference and is quoted with
its real spelling in §5/E3 below — the earlier draft's version of it was wrong in both its type name
and its value spellings, which is why it was pulled. Still withdrawn and printed nowhere: an
**occurrence count** for "equity" in the spec, and **named equity-specific error codes**. Neither
could be confirmed on any pass, and neither is load-bearing.

**What produced the wrong premise is worth recording, because it will mislead the next reader too —
and the honest reading of it has changed.** The API's own introduction page describes it as
*"Coinbase's programmatic interface for **spot crypto and derivatives**"*
(https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/introduction, read
2026-08-09), and its navigation offers exactly three families: **Spot & US Derivatives**, **US
Derivatives**, and **International Derivatives (INTX, deprecated)**. No equities section anywhere.

⚠️ **An earlier draft concluded from this that "the prose is out of date and the schema is not."
That is not established, and the documentation pattern points the other way.** Equity support
appears **only** inside auto-generated per-endpoint OpenAPI schema blocks — the ones carrying
internal protobuf names like `coinbase.public_api.authed.retail_brokerage_api.*`. The hand-written
surfaces are silent: the REST introduction, the API overview, the orders guide, the FAQ, and the
changelog, which contains **zero occurrences of the string "equit"**. So the split is not
prose-lagging-schema; it is **generated-artifacts-lagging-editorial-intent**, which is the opposite
inference. Taken with the live 403 and the market-data vacuum in §1a, **the introduction page
describes what is *usable* more accurately than the schema describes what is *shipped*.** Read both,
and treat the schema as evidence of what exists rather than of what works.

### (c) Reconciling (a) and (b) — documented is not shipped, and shipped is not priceable

Two true things that look contradictory: the API *documents* equity trading, and keel's own probe
*measured* that it could not do any of it. Both stand, on two dates now. The reconciliation matters
more than either.

| What | Status |
|---|---|
| Equity order path exists in the documented API | **yes** — spec + create-order reference, read 2026-08-09 |
| Equity products declare themselves tradable | **yes** — `equity_trading_flags` `tradable/buy_enabled/sell_enabled: true` on **998 of 1000**, measured 2026-08-09 |
| keel's account could preview an equity order | **no** — 403 `"API order preview is not available for equities products"`, measured 2026-08-05 **and again 2026-08-09** |
| Equity market data on this account | **none** — no daily bars over 21 trading days, no open/close on regular trading days, empty price strings on all 1000 products, no WebSocket channel. Measured 2026-08-05 and, more thoroughly, 2026-08-09 |
| **`create_order` on an equity** | **never called.** Deliberately, then and now |

So the accurate statement is **not** "Coinbase has no equities API," and it is no longer "we do not
know why it fails." It is: **the documented surface exists, the products advertise themselves as
tradable, preview is refused with a product-class message rather than an entitlement one, and there
is no market data of any kind on any endpoint. The order half is plausibly live and untested; the
data half is definitively absent.** For keel that ordering is irrelevant — the data half alone is
disqualifying — but it is the difference between "we are not entitled" and "it is not finished,"
and the 2026-08-09 evidence favours the second.

**Three documented constraints conflict directly with keel's execution model, and they survive
however that resolves:**

1. **No preview for equities.** keel's executor calls `broker.preview_order` **unconditionally and
   re-raises on failure** (`executor.py:445`), and the Coinbase adapter declares
   `supports_native_preview=True, synthesizes_preview=False` — there is no fallback. A 403 on
   preview is fatal to keel's order path *even if `create_order` would succeed*.
2. **Attached orders are not supported for equities.** keel places protective legs via
   `place_bracket` (`executor.py:731`). A venue that refuses attached orders forces keel to manage
   stops as independent resting orders — a real change to the execution model, not a flag.
3. **`quote_size` and fractional sizing are rejected outside the normal session.** keel's entries
   are `MarketIOCByQuote` — "spend $N". Under this API keel's own entry model would work **only
   during the normal session**, and whole-share `base_size` would be mandatory otherwise, which is
   C6 (no lot rounding) becoming a blocking defect rather than a latent one.

Plus the identity problem the probe already found and the docs now confirm in their own words —
*"Use the canonical `product_id` returned by the Products API, not the display ticker"* — which is
the 64-char hash. ⚠️ **Earlier drafts added "on a listing that is not deterministically enumerable";
that half is retired.** The 2026-08-09 re-probe found the listing stable and cleanly walkable to
19 188 ids (§1a). The hash-not-ticker problem is real; the enumeration problem is not, any more.

**Zero market data remains the decisive keel-side fact, and the 2026-08-09 re-probe made it
stronger rather than weaker.** Every rule in `keel/strategy/rules/` takes `candles_by_tf`; with no
bars there is no setup, no ATR, no stop, no size and no mark. A documented order endpoint keel
cannot price into is not a capability — and this is the one Coinbase-specific conclusion that would
survive even if the charter question in §2 were answered tomorrow.

### (d) Coinbase's own agent product — ⚠️ **withdrawn by an earlier pass, and now RESTORED**

The brief for this document supplied what it called a decisive citation: that **"Coinbase for
Agents"** shipped with equities listed as *planned for the future*, which would have been Coinbase
confirming, in its own words, that no equities API existed.

**An earlier pass withdrew that citation on the grounds that `coinbase.com` returns HTTP 403 to
automated fetching. That withdrawal was wrong — it looked at the wrong host.** The Coinbase for
Agents documentation lives on the ungated `docs.cdp.coinbase.com`, and it says the thing verbatim
(https://docs.cdp.coinbase.com/coinbase-for-agents, read 2026-08-09):

> **Coming soon:** x402 payments for agent-consumed services (paywalled research, data APIs,
> compute), **equities**, prediction markets, and additional asset classes. If it's on Coinbase,
> your agent will be able to trade it.

**Coinbase uses the word "equities" under an explicit "Coming soon" header, in its own developer
documentation.** ⚠️ **This also refutes a claim made in the brief and repeated in an earlier draft:
that the "planned for the future" line was a journalist's paraphrase rather than Coinbase's own
wording.** Only the exact *sentence* that appeared in the CoinDesk coverage was the journalist's
construction; **the substance is Coinbase's published roadmap**, and it is quotable at source. No
press citation is needed or printed for it.

**What it does and does not establish, because the two are easy to run together.** It is a statement
about the *agent product's* roadmap, not about the REST surface — §1b shows the REST surface already
carries equities in its schema. Read alongside §1a's measured vacuum, the two agree with each other:
Coinbase's own editorial voice places equities in the future tense, its agent product does not have
them, its REST introduction does not list them, and its market-data endpoints return nothing for
them. **The schema is the outlier, not the roadmap.** That is a materially different picture from
"the docs are stale," which is what an earlier draft of §1b concluded.

### (e) Tokenized equities — a different product, and not for a US person

⚠️ **Provenance, stated plainly: the specifics below are inherited from the 2026-08-05 study and
from general knowledge, not from sources re-read for this document.** The press citations an earlier
draft carried — announcement date, a CoinDesk URL, a "no-action relief" characterisation — were not
re-confirmed and are not printed.

What is solid, because it is repo-sourced, is the 2026-08-05 study's own finding, restated here
unchanged: **the widely-reported Coinbase "tokenized equities" launch is a non-US product issued on
Base, and it is NOT what this account's API returns.** What the Advanced Trade API returns is
`product_type: EQUITY` on venue **`CCM`** with CIK numbers and Apex clearing — real US shares.

**Keep the two straight, because confusing them produces both the wrong technical answer and the
wrong halal one.** "Coinbase equities" means either **CCM/Apex brokerage shares** — real shares, on
the Advanced Trade API — or **Base tokenized equities** — on-chain, and *not* on that API. Whether
tokenized equities remain unavailable to a US person, and on what regulatory basis, is
**unverified here** and nothing below depends on it.

### What this means together

The brief's framing — "Coinbase equities: NO, confirmed twice, independently" — **was right for the
wrong reasons, and the reasons matter more than the answer.** Its API leg was **refuted**: the
documented order path exists (§1b). Its Coinbase-for-Agents leg was **withdrawn as unverifiable by
one pass and then restored verbatim by the next** (§1d), which is its own lesson about how a
withdrawal can be as unfounded as an assertion. What survives is narrower, better sourced, and still
sufficient:

- **The documented API supports equities**, and the products advertise themselves as tradable
  (998/1000, 2026-08-09). The brief's premise to the contrary was wrong, and is corrected in place.
- **There is no market data at any granularity, on any endpoint, over any window** — re-measured and
  widened on 2026-08-09, deliberately weekend-independent. This is the Coinbase-side finding that
  actually decides anything.
- **Preview is refused with a product-class message**, not an entitlement one, on both probe dates.
- **Three documented equity constraints conflict with keel's execution model** — no preview, no
  attached orders, no quote-sized entries outside the normal session — and the first is
  independently fatal to `executor.py`'s unconditional preview call.
- **None of that is the reason the answer is no.** §2 is. The market-data vacuum is why Coinbase in
  particular would not be the venue *even if* §2 were cleared; §2 is why no venue would be.

**Consequence for the 2026-08-05 study's R6 ("re-probe equities in ~6 months"): it has now been
done, on 2026-08-09, and it answered all three of the questions an earlier draft of this section
listed as live.** Has market data appeared? No — and the vacuum is deeper than 2026-08-05 recorded.
Does the preview refusal still stand? Yes, in the same words. Is it an entitlement gap? The wording
says product class, and `equity_trading_flags` says the products are tradable, so probably not.
**R6 should be closed rather than rescheduled.** The remaining unanswered question is whether
`create_order` works, and that one is not answerable by a read-only probe and will not be attempted:
it would place a real order, in a real brokerage account, for an instrument keel's charter forbids
it to hold. **Every one of these was, and remains, downstream of a charter gate no probe can move.**

## 2. The real gate is the charter, not the venue — and it is broker-independent

This is the most important finding in this document, and it is the one that does not change if a
better venue is chosen tomorrow. §1 is the demonstration, twice over: the venue premise this
document started from was wrong in keel's favour — the order path exists — and then the 2026-08-09
re-probe moved the venue evidence hard in the other direction — there is no market data at all —
**and the answer did not move by a single step in either case.** That is the test of a
broker-independent gate, and this one passes it. Everything below is what a probe cannot measure and
a broker cannot supply.

**Keep this separate from the market-data finding in §1a.** They are both refusals, they are both
firm, and they are not the same refusal. The data vacuum is a fact about Coinbase that Alpaca would
answer tomorrow. §71.6's screening requirement is a fact about keel that no venue answers ever.
**Fixing the first would leave the verdict exactly where it is.**

### What §71.6 actually says

The Shariyah Review Bureau material behind §71.6 classifies instruments by **what they legally
represent**, and rules per type
(`docs/superpowers/references/trading-knowledge-base/sources/source-71.md:385`ff):

| Type | What it represents | Ruling |
|---|---|---|
| Utility tokens | rights to services | `Huquq` — *"permissible to trade such tokens on a secondary market **provided that the project is Shariah compliant and has passed the Shariah screening for ICOs**."* |
| Revenue instruments | participation in future revenues | possible if genuine equity + risk-sharing; **"requires screening of core business activity AND financials, 'like the screening methodology of shares'"** |
| **Equity instruments** | **equity in the issuer — votes, dividends, beneficial interest** | ***"similar to purchasing shares"*; requires the same share-screening methodology** |
| Buy-back-dependent | appreciation backed by issuer repurchase-and-destroy | ⚠️ contract combination — hard reject |

And the rule the KB extracted from it, in its own words:

> **⭐ Function screening is not sufficient for *any* token carrying a claim on an issuer.** SRB
> requires equity and revenue tokens to pass **share-style screening of the issuer's core business
> activity AND its financials** — a two-part test our binary sector screen does not perform. This
> is the same standard §65.10 noted we already exceed on the *sector* half (binary rejection vs
> AAOIFI/DJIM's ~5% tolerance) — but **we perform no financial-ratio screen at all**, because our
> allowlist contains no issuer-claim tokens. **The rule to record: admitting any equity/revenue/
> claim-bearing token would require a screening capability we do not have.**

The README states the same conclusion in four words: **"equity/revenue ⇒ REJECT BY CAPABILITY"**.

### One inference, named as such

§71.6 is a **token**-screening section. Applying it to a real US common share is an **inference**,
not a direct citation — the 2026-08-05 study flagged this in the same terms and it has not been
closed since. It is, however, an unusually strong inference: the section's ruling for equity tokens
is *"similar to purchasing shares… requires the same share-screening methodology."* It reaches
shares by analogy **to shares**. Applying it to the thing the analogy points at is the one
direction the inference cannot be wrong in.

### The distinction that this whole document turns on

**"Reject by capability" is not "reject".** It is the KB saying: *the test exists, it is
well-specified, and we do not run it.*

Compare the two refusals this repo has now recorded:

| | cTrader (2026-08-09) | US equities (this document) |
|---|---|---|
| What is wrong | leverage, short-symmetry, financing and cash-only P&L are **load-bearing in every core message type**; no message anywhere expresses an unleveraged spot holding | **nothing about a share is structurally defective.** A common share is an ownership stake, risk-sharing by construction, deliverable, and holdable |
| How it could change | Spotware or a licensee inventing an account type and a set of messages that do not exist | **keel building a screen that Islamic finance already specifies in full** |
| Who can act | not keel | **keel** |
| Verdict word | **never** | **not yet** |

§65.10 is the proof of the second column. It reproduces the DJIM screen in full — the halal-core-
business test, the three sub-33% balance-sheet ratios, the ~5% non-permissible income tolerance
with purification of the excess — from Ayub, verbatim, as *"what §29's 'AAOIFI is the authoritative
reference' pointer actually resolves to in practice."* Equities are not an unscreened frontier in
Islamic finance; they are the **original** subject of the screening literature, and crypto is the
part that had to be reasoned to by analogy. The canonical treatment is **AAOIFI Shari'ah Standard
No. 21, *Financial Paper (Shares and Bonds)*, and it is the methodology this document records as
decided** should the screen ever be built — see §3, where its criteria are quoted from the standard
text, and §3.1, which names the two places that choice would cut against keel's existing posture.
⚠️ **Distinguish two senses of "read": this document has read the standard (§3 quotes it from
AAOIFI's own published text), but SS 21 has *not* been read into the knowledge base as a source
file, and §71.4b/§71.5's SS 18 remains the only AAOIFI standard in it.** That authoring step
follows the §3.1 decisions rather than preceding them (§7 item 2).

§65.10 also records the direction of keel's existing divergence, which matters for the design:

> Our posture is **binary rejection** on any riba-yield function (§41.1). **That is stricter than
> the standard.** … it should be documented as a deliberate over-compliance, not presented as *the*
> standard.

keel is already *more* conservative than DJIM on the sector half. A keel equity screen inheriting
that posture would be tighter than an off-the-shelf Islamic index, not looser. **The capability gap
is real; the compliance instinct behind it is already calibrated.**

### The schema cannot express the answer — verified

`asset_attestations` (`keel/data/db.py:231-240`) is:

```sql
CREATE TABLE IF NOT EXISTS asset_attestations (
    asset        TEXT PRIMARY KEY,
    sector       TEXT NOT NULL,
    backing      TEXT NOT NULL,
    pays_yield   INTEGER NOT NULL,
    source       TEXT NOT NULL,
    attested_by  TEXT NOT NULL,
    attested_at  INTEGER NOT NULL
)
```

mirrored by `AssetAttestation` (`keel/compliance/screen.py:76-86`). `sector` is free text matched
against `HARAM_SECTORS` (`screen.py:33-44`: gambling, casino, adult, alcohol, pork, tobacco,
firearms, riba_yield). `backing` is one of `{'ayn, 'dayn, native}`. `pays_yield` is a boolean.

**None of those is a vocabulary for a company's debt-to-market-cap ratio, its interest income as a
fraction of revenue, or its receivables.** There is no numeric field on the row at all. A DJIM
screen is four ratios and a threshold; this schema can record neither a ratio nor a threshold. The
gap is not "fill in a column" — it is a second attestation kind that does not exist. Note also that
`backing` would be genuinely awkward: a share is not `'ayn` (it is not a specific identified
object), not `'dayn` (it is not a debt claim), and not `native`. **Which value a share takes is
itself an open question this document does not answer — unverified, and it needs a ruling, not a
probe.**

### The screening work is genuinely recurring, unlike a crypto attestation

This is the cost that is easiest to underestimate. A crypto attestation is a **one-time** judgement
about what a token is: BTC's `backing=native, sector=crypto, pays_yield=False` does not change
because a quarter closed. A financial-ratio screen is a **per-reporting-period** judgement about
numbers that move every period — debt is refinanced, cash balances swing, and a company can cross a
threshold without doing anything a headline would report.

**And under the standard keel has chosen it is worse than per-period, for a reason specific to that
standard: SS 21's denominator is market capitalisation** (§3), so the *denominator moves every time
the price does. A company's compliance can change on a price move alone, with an unchanged balance
sheet.** That is not true of MSCI's total-assets basis, and it is the sharpest recurring-cost
consequence of the §3 decision. (SS 21 mitigates it slightly by specifying no averaging window at
all — clause 3/4/5's *"last budget or verified financial position"* — so the ratio is struck
point-in-time rather than tracked continuously; but *when* it is struck then becomes a policy
choice keel must make and defend.)

Consequences, stated plainly because they are design constraints and not caveats:

- An admitted stock can become **inadmissible while held** — through a filing, or (per the above)
  through a price move alone. The screen needs an *exit* semantics the crypto screen has never
  needed. **SS 21 clause 3/4/8 supplies the requirement** — *"it is obligatory to give up such
  investment"* — so this is no longer an open question of policy, only of mechanism (§3 item 4).
- `attested_at` is currently a timestamp nothing expires on. A ratio screen needs a **staleness
  policy**, and "fails closed on a stale attestation" is the only safe default — which means the
  screen must know each issuer's fiscal calendar.
- Somebody has to read filings. keel has no ingestion path for XBRL, 10-Qs, or any fundamentals
  vendor, and `screen.py:251`'s standard is that *"an unsourced claim is not evidence"*.

**This recurring cost is the single most under-priced item in this document**, and it is an
operating cost, not a build cost — it does not end when the code ships.

## 3. What an equity shariah screen would actually require

### The standard is decided: AAOIFI Shari'ah Standard No. 21

**Should the equity screen ever be built, the methodology is AAOIFI Shari'ah Standard No. 21,
*Financial Paper (Shares and Bonds)*.** This is recorded here as a decision, not offered as an
option, so that the question does not have to be re-argued if the subject is ever re-opened.

The grounding is that SS 21 is not a new authority being imported — it is **the consistent sibling
of a standard this KB already cites directly**:

- §29.1–29.2 name **AAOIFI as the authoritative screening-standards reference**, and license
  documenting a conservative divergence from it.
- §71.4b/§71.5 cite **AAOIFI Shari'ah Standard No. 18 §3/5** directly, for the two-condition
  `'ayn`-vs-`dayn` possession test, and it is the KB's first AAOIFI standard applied to a digital
  asset. §71.5's *"Three sources now converge"* on `qabd` rests on it.
- SS 21 is the same body's standard governing dealing in shares — the instrument class §71.6 sends
  an equity to be screened *like*. Choosing anything else would mean screening keel's possession
  test by AAOIFI and its admission test by somebody else.

**DJIM, MSCI Islamic and S&P Shariah are the comparison set SS 21 was chosen over, not alternatives
left open.** They matter to this document for exactly one reason: §65.10's record of keel's current
posture is stated *against DJIM's* numbers, so the comparison is how the tension in §3.1 below
becomes visible at all. The three commercial index families differ from AAOIFI and from each other
on the ratio denominators and thresholds — a difference that is a compliance decision rather than an
implementation detail, and one this document deliberately does not resolve.

#### What SS 21 actually says — read from the standard text, 2026-08-09

**AAOIFI publishes the full English text of SS 21 on its own site**, so the following is quoted from
the standard rather than from a secondary summary:
https://aaoifi.com/wp-content/uploads/2020/08/SS-21-Financial-Paper-Shares-and-Bonds.pdf (a 28-page
extract of the compiled *Shari'ah Standards* volume, book pagination 557–583, AAOIFI-hosted since
August 2020; catalogue page https://aaoifi.com/ss-21-financial-paper-shares-and-bonds/?lang=en).
Issued **20 May 2004** (30 Rabi' I 1425 A.H.), adopted at Shari'ah Board meeting No. 12,
Al-Madinah Al-Munawwarah. Scope covers shares and interest-bearing bonds, and **excludes**
investment Sukuk (SS 17).

**⚠️ The framing is the most important thing in the standard, and it is not what a reader expecting
an index methodology would predict.** Clause **3/4** introduces the ratios like this:

> "Participation or trading (for investment and trading) in the shares of corporations whose primary
> activity is permissible, but they make deposits or borrow on the basis of interest — **The
> fundamental rule is that of prohibition** of acquiring shares of and transactions (investment and
> trading) in the shares of corporations that sometimes undertake transactions in Riba and other
> prohibited things **even when their primary activity is permissible**, but from this rule
> subscription and transactions (investment or trading) are **exempted** with the following
> conditions:"

**The ratios are a narrow exemption from a baseline prohibition, not a permission with limits.**
The Appendix grounds the exemption in *"removal of hardship and acknowledging of general need,
widespread practice."* This materially changes §3.1 and is picked up there.

**The tests, verbatim, with their exact denominators:**

| Clause | Test | Threshold | Denominator — exact wording |
|---|---|---|---|
| **3/4/2** | interest-bearing debt, long- or short-term | **30%** | *"of the **market capitalization** of the corporation"* |
| **3/4/3** | **interest-taking deposits** | **30%** | *"of the **market capitalization of total equity**"* |
| **3/4/4** | income from a prohibited component | **5%** | *"of the **total income** of the corporation"* |
| **3/19** | tangible assets, benefits and rights | **≥ 30%** | *"of the **total assets** value of the corporation"* |
| — | accounts receivable | **no such test exists in SS 21** | — |

The only percentages anywhere in the standard are 30% (three times) and 5% (once). **There is no
33% and no receivables screen.**

**Four details that a DJIM-shaped implementation would get wrong:**

1. **The deposits numerator is *deposits*, not cash.** 3/4/3 counts *"interest-taking deposits"* —
   AAOIFI does **not** put a company's ordinary cash in the numerator, where DJIM/MSCI/S&P use
   `cash + interest-bearing securities`. A keel implementation reusing the commercial-index
   numerator would be screening something AAOIFI did not ask for.
2. **The income denominator is *total income*, not revenue.** AAOIFI says *income*; DJIM and S&P
   both say *Total Revenue*. Not interchangeable.
3. **There is no averaging window at all.** Clause **3/4/5**: *"For the determination of these
   percentages, recourse is to be had to the **last budget or verified financial position**."*
   Point-in-time, off the latest verified financials. Every commercial index uses a trailing average
   (DJIM 24-month, MSCI M-Series and S&P 36-month); **AAOIFI uses none**, and anyone citing "AAOIFI's
   12-month average" is importing it from elsewhere. ⚠️ **This corrects a claim made earlier in this
   document's own §2**, where the recurring-cost argument leaned on a continuously-moving average
   denominator — see the correction there.
4. **⚠️ SS 21 contradicts itself on the 3/19 floor.** Clause 3/19 says **30%**, but footnote (1) to
   3/1 says *"should not be less than **one-third**"* and the Appendix says *"less than a third."*
   The footnote even carries AAOIFI's own hedge: *"(This explanatory note is intended to complete
   the text of the Standard for implementing subsequent amending procedures, God willing)."*
   **Do not present that floor as unambiguous.**

**Ongoing obligation — and it answers a question this document raised as open.** Clause **3/4/8**:

> "It is necessary to observe these rules **throughout the period** of participation or trading. If
> the rules cannot be applied, **it is obligatory to give up such investment**."

That is the admitted-then-failed disposal semantics named as undecided earlier in §3. **SS 21
decides it: forced disposal.** It is not left to the implementer.

**Purification — clause 3/4/6, and it has a sharp edge for a trading agent.** The obligation is
*"to eliminate prohibited income specific to the share that is mixed up with the earnings of the
corporations"*, and:

- **3/4/6/1** — the obligation falls on whoever holds the share **at the end of the financial
  period**. *"Accordingly, elimination is not obligatory for one who sells the shares before the end
  of the financial period."*
- **3/4/6/2** — it applies *"whether or not the profits have been distributed and whether or not the
  corporation has declared a profit or suffered a loss."* **Purification is therefore not a
  dividend-only concern** — a non-dividend-paying holding can still carry the obligation.
- **3/4/6/4** — the formula: total prohibited income ÷ number of shares, × shares held.
- **3/4/6/5** — *"It is **not permissible to utilise the prohibited component in any way whatsoever**
  nor is any legal fiction to be created to do so **even if this is through the payment of taxes**."*

⚠️ **The 3/4/6/1 carve-out is a live hazard, not a convenience.** keel's mean hold is ~24 days, so
keel would frequently sell before a period end and owe nothing under the letter of the rule. **An
agent that satisfies a purification obligation by holding briefly is gaming a timing rule**, and
§65.9's posture — segregate what accrued, report it, never adjust — is the more conservative
reading. This document does not resolve it, but it is a second place (alongside §3.1) where SS 21
as written is *looser* than keel's existing instincts, and it should not be discovered during
implementation.

**Other SS 21 prohibitions keel already satisfies by construction**, worth recording because they
confirm the fit: **3/5** no margin or interest-financed purchase and no pledging shares for such a
loan (this is rail 13's posture, and §65.10 quotes the same rule from Ayub); **3/6** no short
selling; **3/9** no share lending; **3/11** no Salam; **3/12** no futures; **3/13** no options;
**3/14** no swaps; **3/15** no renting of shares. keel is long-only spot with settled cash — it
clears all eight without a line of code.

**How SS 21 compares to the index families it was chosen over** (all methodologies read 2026-08-09):

| | Debt | Cash / deposits | Receivables | Impermissible income | Denominator | Window |
|---|---|---|---|---|---|---|
| **AAOIFI SS 21** | **30%** | **30%** (deposits) | **none** | **5% of total income** | **market cap** | **none** |
| DJIM (May 2025) | 33% | **screen retired 09/2023** | **screen retired 03/2023** | 5% of total revenue | 24-mo avg market cap | 24-mo |
| MSCI Islamic | 33.33% | 33.33% | 33.33% | 5% of revenue | **total assets** | latest report |
| S&P Shariah | 33% | 33% | 49% | 5% of revenue | market value of equity | 36-mo |

⚠️ **Two corrections this table forces on §65.10's material, which is the KB's only screening
reference today.** First, **§65.10's three-ratio DJIM description is out of date**: S&P DJI retired
the receivables screen effective 2023-03-17 and the cash/interest-bearing-securities screen
effective 2023-09-15, leaving DJIM with a **single** accounting screen. A keel screen built against
§65.10's DJIM form would be implementing a methodology its own author retired three years ago.
Second, **§65.10 records the DJIM denominator as a 12-month average; current DJIM uses 24 months** —
Ayub's book predates the change. Neither correction affects the *verdict*, and neither is a defect
in §65.10, which faithfully records what its source said; both are reasons the KB entry needs a
refresh whenever SS 21 is read in.

**MSCI is the real outlier and the comparison worth keeping:** a **total-assets** denominator is
price-insensitive, where AAOIFI's **market-cap** denominator means a company can fail the debt
screen on a price drawdown alone, with an unchanged balance sheet. **For a trading agent that is a
genuinely awkward interaction** — the moment a holding's price falls hardest is the moment it is
most likely to breach a market-cap-denominated leverage screen, which is also the moment keel's
stop is closest. A forced-disposal obligation (3/4/8) and a stop-loss exit would fire together, for
unrelated reasons, and the audit trail would need to say which one caused the sale.

### 3.1 ⚠️ The open question the owner will face: adopting SS 21 would make keel *less* conservative

This is named, not resolved. It is the first place in keel's history where following a standard
would loosen the project's posture rather than tighten it, and whoever builds the screen will meet
it on day one.

**⚠️ Read the standard's own framing first, because it narrows this tension substantially — and
this subsection was drafted before the text was available, on the assumption that it would not.**
SS 21 does not present its ratios as neutral thresholds the way an index methodology does. Clause
3/4 states that **"the fundamental rule is that of prohibition"**, that this holds **"even when
their primary activity is permissible"**, and that the ratios are an **exemption** from that rule,
grounded in the Appendix on *"removal of hardship and acknowledging of general need, widespread
practice."* Each ratio clause then repeats the point against itself: 3/4/2 adds *"knowingly that
raising loans on interest is prohibited whatsoever the amount is"*, and 3/4/3 *"knowingly that
interest-taking deposits are prohibited whatsoever the collective amount is."*

**That is much closer to keel's instinct than DJIM's numbers are, and it changes the shape of the
question.** AAOIFI is not saying a 4% riba income is fine; it is saying it remains prohibited and is
tolerated under necessity, with mandatory purification attached. keel's binary rejection and
AAOIFI's exemption-from-prohibition are the *same posture* at different points on a hardship
argument — where DJIM's threshold, read cold, genuinely is a de minimis test.

**The tension is therefore real but narrower than it first appears, and it reduces to one question:
does the hardship rationale that justifies AAOIFI's exemption apply to keel at all?** The exemption
exists because a Muslim investor who refused every company with incidental interest exposure could
barely participate in modern equity markets. **keel is not in that position.** It is a discretionary
agent with a five-asset allowlist and no mandate to hold equities whatsoever, so "removal of
hardship" is a weak argument in its case — which is an argument *for* keel keeping its stricter
line, and it is an argument the standard's own reasoning supplies rather than one keel would be
inventing.

With that framing established, the original tension still stands on the numbers:

> ⚠️ **Criterion 5 is the interesting one, and it cuts against our instincts.** The mainstream
> standard is **not** zero-tolerance: a company with ≤5% incidental interest income is *investable*,
> with the tainted fraction purified. Our posture is **binary rejection** on any riba-yield function
> (§41.1). **That is stricter than the standard.**
> … **it should be documented as a deliberate over-compliance, not presented as *the* standard.**

Every precedent in this repo runs the same way. keel's screen rejects a `riba_yield` sector
outright where AAOIFI/DJIM tolerate an incidental fraction. `backing == "dayn"` is an **unwaivable**
rejection and `WAIVABLE_CRITERIA` is exactly `{"history"}` (`screen.py:60`). §65.14 refuses staking
on §29.2 conservatism rather than on a ruling keel holds. Rail 19 carries no config field because
*"spot-only is this agent's CHARTER"*. **The pattern is: where the standard permits a tolerance,
keel has declined to use it.**

Adopting SS 21 as written would invert that, and in the most conspicuous possible place — an
impermissible-income tolerance is, by construction, a rule that admits an instrument keel *knows*
carries some riba, on the grounds that the amount is small and the remainder can be purified. That
is a coherent and mainstream position, held by every major Islamic index. It is also the exact
shape of reasoning §65.1 warns about: **riba is strict-liability with no de minimis**, where gharar
is a materiality threshold. A percentage tolerance is a de minimis test.

The two defensible resolutions pull in opposite directions, and both have KB support:

- **Adopt SS 21 as written.** The tolerance is not keel's invention to reject — it is the standard's
  considered position, paired with a mandatory purification obligation that keel already has
  machinery for (§65.9, `keel/compliance/purification.py`). §65.10's own reasoning for keeping the
  stricter crypto line was that *"a 5% test is unmeasurable for tokens"* — a company's financial
  statements make it perfectly measurable, so the stated reason for the divergence **does not
  transfer to equities**. This is the strongest argument, and it is worth noticing that it is an
  argument §65.10 supplies against itself.
- **Adopt SS 21's structure with keel's stricter thresholds**, recorded as deliberate
  over-compliance under §29.2 exactly as §65.10 asks. Consistent with every precedent above, at the
  cost of a smaller investable universe and of keel holding a position no standard body holds.
  **The standard's own hardship rationale is the strongest support for this option** — see the
  framing above: an exemption granted to relieve hardship is weakest where there is no hardship, and
  a five-asset discretionary agent has none.

**And a second, smaller instance of the same tension, which the standard text surfaced and nobody
would have predicted: the purification timing rule.** Clause 3/4/6/1 places the obligation on
whoever holds the share **at the end of the financial period** and states outright that
*"elimination is not obligatory for one who sells the shares before the end of the financial
period."* keel's ~24-day mean hold means it would routinely fall on the exempt side. **A system that
discharges a purification obligation by not being there on the measurement date has satisfied the
letter and hollowed out the intent** — and §65.9's posture (segregate what accrued, report it,
adjust nothing) is the stricter and more coherent reading. Same shape as the ratio question, same
answer available, and worth deciding at the same time rather than separately.

**This document does not choose, on either.** Both readings are defensible; the choice is a
compliance decision for the repo owner, it should be made explicitly and written into the KB rather
than falling out of an implementation, and it should be made **before** any screen is built rather
than discovered while calibrating one. Naming it is the deliverable here.

### The rest of what a screen would need

Sketched, not designed. Enumerated so the cost estimate below has something to point at.

1. **SS 21 read into the KB as a source**, in the KB's normal form, with the §3.1 decisions recorded
   and keel's deviations stated. The criteria are quoted above and the standard is freely available
   from AAOIFI, so this is a KB-authoring task rather than a research one — **and it should carry
   the two §65.10 corrections this document surfaced** (DJIM's retired screens, the 12- vs 24-month
   window), since §65.10 is the KB's only screening entry today.
2. **A second attestation kind** — **issuer-keyed, not asset-keyed**, which is itself a schema
   departure — carrying: core business line; **interest-bearing debt ÷ market cap (3/4/2, 30%)**;
   **interest-taking deposits ÷ market cap (3/4/3, 30%)** — note the numerator is *deposits*, not
   cash; **prohibited income ÷ total income (3/4/4, 5%)**; **tangible assets ÷ total assets (3/19,
   ≥30%, ⚠️ stated inconsistently in the standard)**; plus the verified financial position each was
   taken from (3/4/5 — point-in-time, no averaging) and the filing it came from. **No receivables
   field: SS 21 has no such test.**
3. **A thresholds policy object**, versioned, with keel's divergences from SS 21 recorded the way
   §65.10 asks — because after §3.1 the interesting question is not "does keel implement AAOIFI"
   but "where does keel deviate, in which direction, and why".
4. **Per-period re-screening with fail-closed staleness** — mandated, not optional: clause **3/4/8**
   requires the rules be observed *"throughout the period"*. **The action on a later failure is no
   longer an open design question**: 3/4/8 specifies *"it is obligatory to give up such
   investment"* — forced disposal. What is still keel's to decide is the *mechanism*, and it is
   awkward: a compliance-driven forced sale is an exit no rule generated, and `guards.py`'s
   rail 16 comment warns that a breaker blocking exits "would trap capital" — so this exit must be
   able to fire even when other machinery is halting. **A compliance exit is a new order class**,
   not a reuse of the stop path.
5. **A purification path — and it is broader than dividends.** Clause 3/4/6/2 is explicit that the
   obligation applies *"whether or not the profits have been distributed and whether or not the
   corporation has declared a profit or suffered a loss"*, so a non-dividend-paying holding still
   carries it; the 3/4/6/4 formula needs the issuer's total prohibited income and share count, not
   keel's own cash ledger. §65.9 already has the machinery and the right posture — report-only
   segregation, `keel/compliance/purification.py`, ⛔ **"REPORT-ONLY. The agent never disposes of
   funds."** — but note it currently computes from **keel's transaction ledger**, and this
   obligation is computed from **the issuer's financials**. That is a new input, not a new report.
   The §3.1 timing hazard (3/4/6/1's sell-before-period-end exemption) is a policy decision that
   belongs here.
6. **A fundamentals data source**, with cost and licensing. Unverified — no vendor was priced for
   this document.

**Item 6 and the recurring cost in §2 are why this is not day-estimable with confidence.** The
estimate below gives a range and says so.

## 4. If not Coinbase, who? — broker survey

**Every row here is documentation and pricing-page reading on 2026-08-09. No account was opened, no
key issued, no sandbox exercised** — the single exception is an unauthenticated 401 handshake
against Robinhood's MCP endpoint, which needed no account and is flagged where it is used. Sandbox
fidelity, fill behaviour, rate limits and corporate-action reporting are therefore all
**unmeasured**, and the recommendation below is a paper judgement. Some vendor doc hosts
(`docs.alpaca.markets`, `docs.tradier.com`) block automated fetching; where a finding rests on
search-indexed official documentation rather than a directly loaded page, it is flagged in the
prose. ⚠️ **Correction: an earlier draft listed `developer.schwab.com` among the blocked hosts. It
is not** — its terms-and-conditions page loads fine and was read directly (see the Schwab note
below); what is gated is the Individual Developer Agreement behind login.

| Venue | Official equities API | Auth | Free paper sandbox | Fractional | Market data | Official Python SDK | Fit for keel |
|---|---|---|---|---|---|---|---|
| **Alpaca** | yes — REST + WebSocket | key + secret | **yes, self-serve** | **yes**, $1 notional | free IEX real-time / **$99·mo⁻¹** full SIP | **`alpaca-py`** ✓ | **best fit** |
| **Interactive Brokers** | yes | local gateway login | paper needs a funded live account first | broker yes, **API rounds down** | free non-consolidated; ~$4.50·mo⁻¹ NBBO | `ibapi` (in the TWS zip) | most capable, highest cost |
| **Tradier** | yes — REST | bearer token | yes, free, 15-min delayed | **RIA-only** | included, $0 | **none** | **disqualified — see below** |
| **Tastytrade** | yes | **OAuth2 only** | yes, resets q24h | yes, notional | included | none (unofficial `tastytrade`) | viable second choice |
| **Schwab** | yes | OAuth2, **7-day refresh** | **no** | unverified | included | none | **unsuited to unattended use** |
| **Robinhood** | **no REST** — MCP only | **OAuth PKCE, browser consent, no `client_credentials`** | no | n/a | n/a | none | **disqualified — cannot run unattended.** See the correction below |
| **Coinbase** | **yes** (§1b) — but no preview, no attached orders | existing CDP key | no | normal session only | **none — measured absent 2026-08-05 and again 2026-08-09** | `keel-broker-coinbase` (crypto) | **unusable for keel today** (§1a/§1c) |

### Why Alpaca, on keel's specific constraints rather than on vendor marketing

Three of keel's own defects decide this, and they point the same way.

- **C6 (no lot rounding) makes fractional support load-bearing, not a nicety.**
  `keel/execution/sizing.py` emits fractional `Decimal`s and rounds to nothing. A venue that
  requires whole shares turns C6 from a latent gap into a blocking one. **This is what disqualifies
  Tradier and damages IBKR**: Tradier's own disclosure restricts fractional shares to *"a Registered
  Investment Advisor"* — a self-directed retail developer cannot place notional equity orders at all
  — and IBKR documents that *"Stock orders submitted using Cash Quantity field through the API will
  round down to the nearest whole share."*
- **keel's entry model is quote-sized.** Entries are `MarketIOCByQuote` — "spend $N" — which is
  exactly Alpaca's notional-order shape ($1 minimum, market orders only, long only). keel is
  long-only by type (`Setup.direction: Literal["long"]`), so every one of those restrictions is
  already keel's own posture. **The one adapter constraint that broke Robinhood-for-crypto — no
  quote-sized market orders, so it "cannot open positions under keel's current entry model"
  (`packages/keel-broker-robinhood/README.md`) — does not recur here.**
- **Unattended operation is non-negotiable.** keel runs from launchd with no human in the loop.
  That is what rules out Schwab, and for a more mundane reason than any policy: **its refresh token
  hard-expires at 7 days with no renewal mechanism**, so unattended operation would require a human
  redoing a browser OAuth flow every week. Combined with the absence of any paper-trading simulator
  — Schwab's "sandbox" is an API-call harness returning engineered datasets, not a fill simulator —
  it is the worst fit in the table for this workload.

Alpaca is also the closest **DX shape** to Advanced Trade — REST + WebSocket, key/secret, a
self-serve paper account that needs no funded live account first — which matters because it is the
shape `packages/keel-broker-coinbase` is already written against. `alpaca-py` is unambiguously the
current official SDK (v0.43.5, 2026-07-02); its predecessor `alpaca-trade-api-python` is
**archived and formally deprecated**, and Alpaca's own marketing page still shows sample code
importing the dead one — a trap worth recording before anyone copies it.

The honest cost note: the **free data tier is IEX-only for real-time**, which is a fraction of
consolidated volume and a poor basis for a daily-candle strategy's marks. Full SIP is **$99·mo⁻¹**
(https://alpaca.markets/data, read 2026-08-09), which would be keel's first recurring market-data
bill — Coinbase candles are free today. The free tier does serve 15-minute-delayed SIP and full
historical SIP older than 15 minutes, so a **daily-bar** strategy might survive on it; whether it
does is **unverified — needs a probe**, and it is a real input to any cost decision.

### ⚠️ Correction to a premise this survey was started with: Robinhood

The brief for this document held that Robinhood offers no sanctioned programmatic equities access.
**That is now out of date, and the correction matters enough to state plainly rather than bury.**

What remains true: **there is still no official public REST equities API.** `docs.robinhood.com`
ships one product — the **Crypto** Trading API — and that is what
`packages/keel-broker-robinhood` is written against (its own README: *"implemented against the
Robinhood Crypto Trading API v2"*). **keel's existing Robinhood adapter does not extend to stocks,
and no amount of work on it would make it.** The unofficial libraries (`robin_stocks` still active,
`pyrh` in maintenance mode) still hit undocumented private endpoints with real ToS and
account-suspension risk, and should not be used.

What is new: on **2026-05-27** Robinhood launched **Agentic Trading**
(https://robinhood.com/us/en/newsroom/robinhood-is-now-open-to-agents/, read 2026-08-09), which is
official, first-party, OAuth-authenticated programmatic equities access — *"Agentic Trading is
launching in beta with support for equities only out of the gate."* It is delivered as a **hosted
MCP server**, with read access across accounts but **write access only within a dedicated,
separately-funded Agentic account.**

**The endpoint is real and live**, confirmed 2026-08-09 without an account:
`POST https://agent.robinhood.com/mcp/trading` returns **HTTP 401** with
`www-authenticate: Bearer resource_metadata=…/mcp/trading` — a correctly-formed MCP authorization
challenge, not a 404 and not a marketing page.

**Four reasons this does not change the verdict, and one reason it is still worth recording:**

1. **It does not touch the charter gate.** §2 is broker-independent. An easier venue does not
   screen a company's balance sheet.
2. **⚠️ Its auth model is disqualifying for keel specifically, and this is the concrete blocker
   rather than a stylistic objection.** Authorization is **OAuth PKCE requiring a one-time desktop
   browser consent**, and the advertised grant set carries **no `client_credentials`** — there is no
   machine-to-machine path. **keel runs headless, from launchd, with no human in the loop**
   (`com.keel.live.plist`, 24 invocations a day). An auth flow whose first step is "open a browser
   and click approve" cannot be automated by keel and must not be worked around. This is the same
   defect that rules out Schwab, arriving by a different route.
3. **An MCP server is not a `Broker`.** keel's port is a typed Python `Protocol` whose
   implementations make direct HTTP calls; an MCP endpoint is a tool surface designed to be driven
   by an LLM agent. Wiring it in would make keel an MCP *client* and put a model in the order path —
   architecturally alien to a deterministic rule engine whose entire audit story is that
   `guards.check` ran over a typed `OrderIntent`. That is not an adapter; it is a different agent.
4. **It is a separate funded account**, and its scope model is coarse in a way that forecloses the
   obvious fallback. `scopes_supported` is the single value `["internal"]` — **there is no read-only
   variant**, so keel could not take Robinhood's equity market data without simultaneously granting
   order-placement authority over the Agentic account. "Use it for data only" is not available here
   the way it was for cTrader.
5. **But it is a clear signal that programmatic equities access is arriving from several directions
   at once** — Robinhood shipped an agentic equities path in May 2026, and Coinbase's REST surface
   already carries equities in its schema (§1b). The venue question is getting easier on its own.
   **The screening question is not**, and that asymmetry is the whole finding.

### One claim from the brief that did not survive checking — and the rule it produced

The brief asserted that Schwab's commercial-approval review *"explicitly scrutinizes automated or
AI-driven functionality"* — a real risk for an unattended bot, if true. **It is not Schwab's
wording, and it is not printed here as a finding.** Schwab's publicly-readable developer terms
(https://developer.schwab.com/terms-and-conditions, read 2026-08-09 — 16.6 KB of real contract text,
not a login wall) contain **zero matches** for `AI`, `artificial intelligence`, `machine learning`,
or `advisory`. The app registration form collects four fields and an order limit, with no automation
or AI attestation anywhere.

**The wording traces to five interlinked articles on a single domain, `mylinedchart.com`, whose
registration record dates to 2026-04-20** and whose own disclaimer states it is *"not affiliated
with or endorsed by Charles Schwab."* The articles paraphrase rather than quote, cite no Schwab
document, and describe the *Commercial* track rather than the Individual one — and they now rank
highly enough that AI search summaries restate them as policy. **A five-page cross-linked cluster on
one four-month-old domain reads as corroboration and is a single source.**

⚠️ **Caveat on that refutation, preserved deliberately:** Schwab's **Individual Developer Agreement**
is gated and could not be read. So this is refuted **across every publicly-readable Schwab page**,
not disproven absolutely. The documented gate is mundane: *"Most requests are reviewed within two
business days"*, individuals are limited to one app, and the dashboard shows `Approved - Pending`
before an app is actually usable (`Ready For Use` is the state to wait for). **Schwab is ruled out
below on its 7-day refresh token, which is a documented mechanism, not on any AI-policy claim.**

This is recorded at length for one reason: it is a live example of the failure mode this repo's
documents are written to avoid. **A plausible, specific, useful-sounding claim, repeated by search
summaries, with no primary source under it.** It was in the brief for this document and would have
been printed as fact.

## 5. The engineering gap, if the charter gate were cleared

The 2026-08-05 study numbered keel's instrument-model defects C1–C13. Those findings are cited, not
restated. What follows is which of them equities hit, plus the ones that are new to equities and do
not appear in that list at all.

### Inherited from C1–C13 (see the 2026-08-05 study for the evidence)

| # | Area | How equities hit it |
|---|---|---|
| **C1/C2** | instrument identity and construction | `quote_currency_of` does `rpartition("-")`, `guards._asset` does `split("-")[0]`, and `keel/commands/_products.py` can only construct `f"{asset}-{quote}"`. An `AAPL-USD` id from a sane adapter survives all three — **equities are much kinder here than futures were.** Coinbase's 64-char hash does not, and its docs now confirm the hash is mandatory (*"not the display ticker"*, §1b) — one more reason §4 lands on a different venue. |
| **C3** | position model | positions are reconstructed from keel's own SQLite audit log and never fetched from the venue; the port still has **no `get_positions`** (`packages/keel-broker-api/keel_broker_api/port.py:23-47` — verified 2026-08-09: the Protocol is exactly eight methods, `capabilities`/`get_candles`/`get_balances`/`preview_order`/`place_order`/`get_fee_summary`/`get_order`/`cancel_order`, and none of them is one). Corporate actions make this worse: see below. |
| **C4** | balance model | `Balance(currency, available, total)` has no notion of **settled vs unsettled** cash. T+1 makes that distinction real. |
| **C6** | lot sizing | `keel/execution/sizing.py` emits fractional `Decimal`s and never rounds to a venue lot (`size`, `dca_size`, `spend` — the whole module, verified 2026-08-09). Fractional-share brokers make this survivable; a whole-share venue does not. |
| **C11** | 24/7 assumption | the live one. Expanded below. |
| **C13** | capability gate is dead | `BrokerCapabilities.asset_classes` already contains `"equity"` in its vocabulary (`packages/keel-broker-api/keel_broker_api/capabilities.py:21`, `ASSET_CLASSES = frozenset({"spot", "futures", "equity"})`) and **is read by nothing** — the field's own docstring says so: *"`asset_classes` is **not** what keeps keel spot-only today, and no engine code reads it."* All three shipped adapters declare `frozenset({"spot"})`. **Declared, not wired.** |

### New to equities, and not in C1–C13

**E1. The broker port is not on the live path — so equities sit behind TWO migrations.**
Verified 2026-08-09:

- `keel/execution/executor.py` types the broker as bare `Any` at every site (`:110`, `:312`,
  `:416`, `:731`, `:801`, `:841`, `:946`, `:965`), and calls the **pre-port raw `CoinbaseClient`
  signatures**, not the port's: `broker.preview_order(intent.product_id, intent.side,
  order_configuration)` (`executor.py:445`) and `broker.place_order(...)` (`:486`) against
  `keel/data/cb_client.py:223,247` — where the port's shape is `preview_order(spec: OrderSpec)`
  (`port.py:32`). `broker.get_accounts()` (`executor.py:282`) is not on the port at all; the port
  has `get_balances()` (`port.py:30`).
- `keel/commands/_common.py:137-156` (`_build_broker`) constructs `CoinbaseClient` directly, from
  `coinbase.rest.RESTClient` and `keel.config.load_secrets`. It **never calls `load_broker()`** —
  `packages/keel-broker-api/keel_broker_api/registry.py:23` is reachable only from
  `discover_brokers`, and nothing on the order path calls either.
- There is **no `broker:` key in any config** — `grep broker config.yaml config.live-sandbox.yaml
  config.paperforward.yaml` returns nothing.
- Three adapters register under the `keel.brokers` entry-point group (coinbase, robinhood, fake —
  `packages/*/pyproject.toml`) and all pass the conformance suite
  (`packages/keel-broker-api/keel_broker_api/conformance/suite.py`). Installing one makes it
  **discoverable and conformance-checkable, not tradeable.**

That migration is Phase B, already scoped:
*"Phase B (spec steps 6–9) migrates the engine onto the validated port: retiring `executor.py`'s
three `_*_order_configuration` builders and `_initial_status`, typing `agent.py`'s broker parameter
as `Broker`, deleting `keel/data/cb_client.py` … It carries all of this work's behavioural risk and
warrants its own plan."* (`docs/superpowers/plans/2026-07-19-keel-broker-port-phase-a.md:1159`).
**An equities adapter written before Phase B lands is dead code by construction** — exactly the
state `packages/keel-broker-robinhood/README.md` documents for the adapter already in the tree.

**E2. Rails 18 and 19 veto every equity id, and rail 19 has no widening knob.**
`keel/execution/guards.py:668-757`, read 2026-08-09. Rail 19's check is:

```python
if parse_spot_product_id(intent.product_id) is None:
    violations.append(
        f"spot_instrument: {intent.product_id!r} is not a well-formed spot product id "
        f"(BASE-QUOTE, uppercase, exactly one hyphen). keel is spot-only: futures "
        f"(BASE-DDMMMYY-CDE), equities (an opaque 64-char hash) and any other instrument "
        f"shape are refused here regardless of what they settle in."
    )
```

Two things to be precise about, because the naive reading is wrong in both directions:

- The error message **names equities explicitly**, but what it actually rejects is the *Coinbase*
  equity id shape (a hash with no hyphen). A well-behaved adapter emitting `AAPL-USD` **passes both
  rails** — the grammar checks shape, not what backs the instrument, which is the same residual
  `guards.py:726-731` records for `BTC-PERP` (Coinbase International's actual perpetual format,
  which passes this grammar and is stopped only by rail 18). So rail 19 is not a semantic equity
  gate and must not be described as one.
- What *is* load-bearing is the comment, and it is un-negotiable by design:

  > Spot-only is this agent's CHARTER, not an operator preference, so there is no config field here
  > to widen (unlike rail 18's `settlement_currencies`).

  Rails 18 and 19 both run on **both sides, in every mode, DCA included** — deliberately not in
  `LIVE_STATE_RAILS` (`guards.py:167`), so paper cannot skip them.

**Widening rail 19 is mandatory for equities, and it is the single most safety-critical change in
this document.** Whatever replaces "is this a spot pair" has to keep refusing everything the
current grammar refuses — the `CDE` futures family, the `BASE-PERP-QUOTE` residual, the hash — while
admitting exactly one new shape. It is also the change where a mistake is silent: rail 19 is the
thing standing between keel and a derivative, and there is no second rail behind it for a
three-segment id. Any equities work must ship this with its own tests before an adapter, not after.

**E3. No market-hours or session model exists anywhere. Verified by grep, 2026-08-09.**
`grep -rn "is_market_open|market_hours|exchange_calendar|trading_session" --include="*.py"` over the
tree returns exactly one hit, and it is in the 2026-08-05 probe script
(`docs/experiments/2026-08-05-coinbase-asset-class-probe.py:268`, printing Coinbase's
`session_type` list). **Nothing in `keel/` knows a market can be closed.**

What happens instead is C11: a closed market fails
`keel/data/market_feed.py:160-176`'s `is_fresh`, which compares the newest stored candle to
`now_ts` against `config.auto_trade.interval_sec * FEED_STALENESS_CYCLES` (`keel/agent.py:856`).
The product is skipped for the cycle and logged as `feed_stale`. **That behaviour is safe — keel
refuses to act on a stale series, which is what the check is for — but it is indistinguishable in
the logs from an outage.** Every weekend, every holiday, and every overnight would emit the same
event a broken feed emits. Over a 252-day calendar that is the majority of wall-clock hours.

The deployment cadence says the same thing from the other side: `com.keel.live.plist` fires
`StartCalendarInterval` **24 times a day**, hourly at :20, on a UTC daily-candle cadence. That is a
crypto calendar. It is not wrong for equities so much as meaningless — 14 of those 24 cycles would
have nothing to do.

**And a session model is not optional at the venue either, which §1b makes concrete.**
⚠️ **This paragraph previously named a type `EquityTradingSession` with five bare values; that
spelling was invented and is corrected here against the source.** The real field is
`equity_order_metadata.equity_trading_session`, and its enum, read directly from the create-order
reference on 2026-08-09
(https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/orders/create-order), is
**six** prefixed values:

```
UNKNOWN_EQUITY_TRADING_SESSION      (the documented default value)
EQUITY_TRADING_SESSION_NORMAL
EQUITY_TRADING_SESSION_PRE_MARKET
EQUITY_TRADING_SESSION_AFTER_HOURS
EQUITY_TRADING_SESSION_OVERNIGHT
EQUITY_TRADING_SESSION_MULTI_SESSION
```

**A second correction in the same paragraph: the field is optional, not required.** Coinbase's own
description reads *"Defaults to `EQUITY_TRADING_SESSION_NORMAL` when omitted. Market orders are
supported only in the normal session. Any non-normal session requires a limit order with a positive
whole-share `base_size`; `quote_size` and fractional sizing are not supported."*

So the accurate statement is weaker than the earlier one and still sufficient: **the session is not
a required field, but it is a field whose default silently confines keel to one session, and whose
non-default values change both the permitted order type and the sizing basis.** A crypto-shaped
`OrderIntent` carries no concept that could populate it, and an agent that never sets it is an agent
that can only trade 09:30–16:00 ET and does not know that about itself. That keeps E3 a hard
prerequisite for this venue, on a smaller claim, correctly spelled.

**E4. No table records asset class.** `keel/data/db.py`, `SCHEMA_VERSION = 9` (`db.py:22`).
`orders`, `positions`, `candles` and `transactions` all key on an opaque `product_id`/`asset`
string; `asset_attestations` keys on `asset`. **Nothing anywhere in the schema records what kind of
thing a row is about.** A mixed crypto/equity ledger would be indistinguishable per-row, which
matters for P&L attribution, for the exposure walk in `_open_exposure_by_asset`, and for
`UNCORRELATED_ASSETS` (`guards.py:120` — the 2026-08-05 study's C10 cites `:98`; the line has moved
since, the fact has not) — a hardcoded single-member set `frozenset({"PAXG"})` that says nothing
about whether AAPL is correlated with BTC.

**E5. T+1 settlement and settled-vs-unsettled cash — and the one place keel is already right.**
This is a genuinely new accounting concern: `Balance(currency, available, total)` cannot express
"sold yesterday, cash not yet settled", and a good-faith-violation / free-riding rule has no
representation anywhere.

**But rail 13 is well-aligned, and this deserves to be said plainly rather than buried.**
`guards.py:512-541`:

> **13. USDC-funding** — a BUY may only spend an **already-settled** quote-currency balance, never
> a linked bank/ACH source. Fails closed: an unknown balance (`None`) vetoes the BUY.

That is, almost word for word, the posture a **cash account with no margin** requires — which is
also the only equities posture §65.10 permits (*"it is not permissible to purchase a share with an
interest-bearing loan offered to the purchaser by a broker"*). keel did not build rail 13 for
equities and it lands on the right side anyway. The honest caveats: rail 13 is **BUY-only** (SELL is
exempt — it produces quote currency) and **skipped when `offline=True`** (`LIVE_STATE_RAILS`,
`guards.py:167`), so it is not a defence a paper rehearsal exercises.

**E6. The Pattern Day Trader rule — eliminated, and it barely mattered to keel anyway.**
The $25,000 pattern-day-trader minimum-equity requirement is the constraint most people expect to
bind an automated equities strategy. **It no longer exists.** FINRA **Regulatory Notice 26-10**,
*"FINRA Adopts New Intraday Margin Standards to Replace the Day Trading Margin Requirements"*
(https://www.finra.org/rules-guidance/notices/26-10, read 2026-08-09), states that the amendments
replace *"the day trade count requirements for designating a customer as a 'pattern day trader' and
the $25,000 pattern day trader minimum equity requirement."* FINRA's investor page puts it plainly:
*"no 'pattern day trader' designation based on counting trades"* and *"no $25,000 minimum equity
requirement for day trading"* (https://www.finra.org/investors/insights/intraday-margin-requirements).

Effective **2026-06-04**, replaced by a risk-based intraday margin framework, with an **18-month
phase-in ending 2027-10-20**: *"Members that need more time to implement the rule change will be
permitted to phase in their implementation over a period of 18 months, until October 20, 2027."*
The SEC approved the proposal on **2026-04-14** (Exchange Act Release No. 105226, 91 FR 20731,
file SR-FINRA-2025-017 — citation chain per FINRA's own notice; sec.gov and federalregister.gov
both refuse automated fetching, so the release number was not read at source). Independently
corroborated by WilmerHale, 2026-04-23.

**Two caveats and one reason this is a footnote rather than a finding.** The new regime applies
*regardless of whether the customer day trades* — firms must monitor intraday margin deficits in
all margin accounts — so it is not simply a deregulation. And during the phase-in window an
individual broker may still be running the old rules, so "the PDT rule is gone" is not yet true of
every venue. **But keel holds positions for ~24 days on average
(`.../sources/source-71.md:758`, §71.8's substantive-defence line: *"real asset, real ownership,
real delivery, ~24-day holds, rule-driven exits"*) and is long-only with no margin: it was never a
pattern day trader, and rail 13's settled-cash requirement means it could not use margin buying
power even if offered.** The rule change removes an obstacle keel did
not have. It is recorded because its *absence* would otherwise be the first thing a reader checks,
and because a cash-account posture is now the only relevant one.

**E7. Corporate actions — a class of position mutation keel has never modelled.**
A split changes the quantity of a held position without any order; a dividend credits cash without
any order; a spin-off creates a position that was never bought. keel's positions are reconstructed
**from keel's own audit log of its own orders** (C3, `db.py:74-89`), so every one of those is a
silent divergence between keel's books and the broker's. Worse, `_warn_on_unexplained_jump`
(`keel/execution/equity.py:133`) exists precisely to detect equity that moved for reasons keel
cannot explain, and is deliberately **detection-only** — its docstring: *"it NEVER adjusts
anything… flows are declared by the operator (`keel record-flow`) and this is only here to make a
forgotten one loud."* A dividend season would fire it routinely with nothing for the operator to
record, **training them to ignore the one alarm that guards rail 11's high-water mark.** That is a
worse outcome than the missing feature itself.

The dividend half has a compliance dimension that is, unusually, already solved in outline:
§65.9's purification report (`keel/compliance/purification.py`) segregates non-compliant credits
from the transaction ledger and is **report-only by design** — *"The agent never disposes of funds.
This computes an amount owed and says so; moving it is the operator's act."* Extending it to the
non-permissible fraction of a dividend is an extension, not an invention.

## 6. What it would cost

**This is a magnitude, not a plan.** Nothing here is proposed, scheduled, or recommended — the
decision this document records is *no*, and the numbers exist so that the *no* is an informed one
rather than a shrug. A reader should be able to tell from this section whether equities are a
quarter of work or a year of it, and nothing more should be inferred from it.

**These are estimates, not measurements.** They are engineer-days for a single engineer, covering
design + implementation + tests to this repo's standard, and they assume no scope growth. They
exclude the standards and ruling work in §7 items 1–2, which is not engineer-days at all. The
2026-08-05 study's own caveat applies unchanged: *"Day estimates are judgement, not measurement."*

Ordered by dependency, and by which is the true blocker.

| Phase | Work | Days | Unlocks |
|---|---|---:|---|
| **P0 — Broker-port migration to the live path (Phase B)** | type `executor.py`/`agent.py` on `Broker`, retire the raw `CoinbaseClient` call shapes, delete `cb_client.py`, add a `broker:` config key routed through `load_broker()`, load-time capability reconciliation | **15–30** | **everything.** Not equity-specific — it is owed anyway, and both existing non-Coinbase adapters are inert until it lands |
| **P1 — The equity shariah screen** | second attestation kind, ratio fields, thresholds policy, per-period re-screen with fail-closed staleness, admitted-then-failed disposal semantics, dividend purification extension, fundamentals ingestion | **20–40**, plus an **unbounded** standards/ruling item and a **recurring** per-period operating cost | **the actual gate.** Nothing else matters until this exists |
| **P2 — Instrument model + rail 19** | an id→class notion `OrderIntent` does not carry today (the A1 slice the 2026-08-05 study priced), widening rail 19 without loosening what it refuses, wiring `asset_classes` from a declaration into a gate | **8–15** | makes spot-vs-equity a thing the rails can *say*, rather than a shape they infer. **Safety-critical** |
| **P3 — Session calendar** | exchange calendar, session state, a market-closed signal distinct from a stale feed, holiday handling, a non-24×7 scheduler to replace the 24×/day plist | **6–12** | correctness of the feed-freshness signal and of any time-based rule. Without it every close reads as an outage |
| **P4 — Equities adapter** | `packages/keel-broker-alpaca/`, conformance suite, paper-account integration tests | **8–15** | the venue. Cheap *only because* P0–P3 did the hard parts |
| **P5 — T+1, settled cash, corporate actions** | settled-vs-unsettled on `Balance`, a corporate-actions ingestion path, position reconciliation against the broker rather than the audit log, dividend purification wiring | **10–20** | correctness of the books. Deferrable at first, not indefinitely |

**Total: roughly 65–130 engineer-days**, plus the unbounded standards item and the recurring
screening cost. Call it **four to seven months of one engineer**, and treat the low end as
optimistic — the range is wide because P1's true size depends on a fundamentals data decision
nobody has made and a standards read nobody has done.

**Three properties of that ordering matter more than the totals, and they are the part of this
section worth remembering:**

- **P0 unlocks the most and is the one item on the list that is owed whether or not equities are
  ever built.** It is what makes the Robinhood adapter already in the tree do anything, and it is
  what makes any future venue a package rather than a rewrite. It is on this list only because
  equities would need it too — it is not equity work, and it should not be counted as the cost of
  a decision this document is answering *no*.
- **P1 is the true blocker, and it is the phase nobody would build first.** That asymmetry is the
  risk this document exists to record. P0/P2/P3/P4 are legible engineering with visible progress;
  P1 is reading a standard and designing an attestation schema, and it gates all of them. Anyone
  approaching equities from the engineering side would produce a working adapter that the charter
  forbids using — the exact "dead code that reads as a capability" failure the cTrader study
  declined to commit.
- **P2 could not follow P4.** Rail 19 is the charter expressed in code. Widening it under schedule
  pressure from a finished-but-unusable adapter is precisely how a spot-only agent would stop being
  one, and the ordering above exists to make that sequence visible before anyone is standing in it.

## 7. What would have to be true to re-open this

**The answer recorded here is no, and it is a settled answer, not a deferral pending someone's
time.** This section exists so that a future reader can tell, quickly, whether the thing that
changed is one of the things that matters. Most changes are not. In particular, **a new venue
appearing is not a reason to re-open this** — the gate is not the venue and never was.

The preconditions are ordered, and the ordering is the point: **the first two are not engineering,
and until both hold, nothing below them is worth costing.**

**The gate — before the question is worth asking again:**

1. **A ruling, or a documented interpretive position under §29.2, that a common share is admissible
   in principle for this agent.** §71.6's application to real US shares is an *inference* (§2), and
   this document has been explicit that it is one. It is a strong inference, but the KB's own
   standard is that an unsourced claim is not evidence (`screen.py:251`), and the whole verdict
   rests on this single step. Confirming or replacing it costs a reading and a write-up, not a
   build — **which makes it both the cheapest item on this list and the only one that is strictly
   required before any other has meaning.**
2. **The two §3.1 decisions taken and written down** — whether keel adopts SS 21's ratio tolerances
   as written or keeps its stricter line under §29.2, and whether it accepts clause 3/4/6/1's
   sell-before-period-end purification exemption. **This is now the live item, and it is cheaper
   than it was:** the standard has been read (§3), its criteria are quoted, and AAOIFI's own
   hardship rationale is on the record — so what remains is a judgement, not research. Formally
   reading SS 21 into the KB follows the decision rather than preceding it, and should carry the two
   §65.10 corrections this document surfaced.
3. **A fundamentals data source, priced and licensed.** Unverified — none was evaluated here, and
   it is the largest single unknown in the estimate. Note SS 21 makes this harder than an index
   methodology would: a **market-cap** denominator (3/4/2, 3/4/3) needs price data alongside
   filings, and clause 3/4/6/4's purification formula needs the **issuer's** total prohibited income
   and share count — a datum most fundamentals feeds do not carry. Without a source, item 4 has no
   inputs and the recurring cost in §2 has no upper bound.
4. **The equity screen actually built** — the per-period re-screen (3/4/8's *"throughout the
   period"*), the fail-closed staleness policy, the **forced-disposal exit 3/4/8 mandates** as its
   own order class, and the issuer-sourced purification path. **This is the gate.** Everything after
   it is ordinary engineering; this is the part that decides whether keel may hold a share at all,
   and it is the part no choice of broker, and no amount of adapter work, moves by a single day.

**Only then, and only in this order, the engineering preconditions:** the broker-port migration on
the live path (P0, owed independently of all of this); an instrument class the rails can name, with
rail 19 widened to admit exactly one new shape and its tests shipped alongside rather than after
(P2); a session calendar, so that "market closed" and "feed broken" stop being the same log line
(P3); an adapter against a venue whose equity rules fit keel's execution model — **Alpaca on
current evidence, and not Coinbase**, which has the documented API but refuses preview, refuses
attached orders, refuses quote-sized entries outside the normal session, and — measured twice —
**publishes no market data of any kind for its equities** (P4); and T+1
settlement, settled-vs-unsettled cash and corporate actions,
before a position is ever held across a dividend or a split rather than after the first one
silently breaks the books (P5).

**Two changes would materially move this document, and one that looks like it would does not:**

- ✅ **A ruling on item 1, or a decision to read SS 21 in.** This is the only cheap change that
  reaches the actual gate. It is also the only one keel can make unilaterally, today, without
  writing code.
- ✅ **keel deciding it is a crypto-only agent as a matter of charter rather than of capability.**
  This is a legitimate answer and, stated plainly, it is *cheaper and more honest than the current
  state* — which is a charter that permits shares in principle and a codebase that cannot screen
  one. If that is the answer, it should be written down the way spot-only is written down at
  `guards.py:741` ("this agent's CHARTER, not an operator preference"), and this document becomes
  the record of why. **That would close the question rather than defer it, and closing it is worth
  more than leaving it ambiguous.**
- ❌ **Coinbase's equities surface becoming usable on keel's account.** This is the change most
  likely to be mistaken for a reason to re-open, and it is not one — **and it has already partly
  happened without moving anything.** The order path exists today (§1b); the brief for this
  document assumed it did not; correcting that changed no part of the verdict. The probe was then
  re-run on 2026-08-09 and found the surface *less* usable than recorded, not more — no market data
  on any endpoint — and that changed no part of the verdict either. **Two measurements in opposite
  directions, neither of which moved anything, is the cleanest available demonstration that this
  gate is not the venue.** If data appears and the preview refusal lifts tomorrow, items 1–4 are
  untouched, because they are not about venues. **The 2026-08-05 study's R6 is answered and should
  be closed rather than rescheduled** (§1's closing note).

## What was deliberately NOT done

No Alpaca, Tradier, Tastytrade, Schwab, Robinhood or IBKR account was opened, no key was issued,
and no paper-trading sandbox was exercised — **every broker claim in §4 is documentation and
pricing-page reading, not measurement** (the one exception is the unauthenticated 401 handshake
against Robinhood's MCP endpoint, which required no account), and the DX judgements are inferences
from those pages. No `packages/keel-broker-alpaca/` package. No dependency added to the workspace.
No entry point registered under `keel.brokers`. No change to `capabilities.py`, `guards.py`,
`screen.py`, or `db.py`.

**⚠️ One line in this section has been retracted.** An earlier draft recorded that the committed
Coinbase probe was *deliberately not re-run*, and defended that choice on scope. **It has since been
re-run, on 2026-08-09, read-only and POST-guarded**, and the results are in §1a — including a
market-data finding that is now one of the two legs the verdict stands on, and two corrections
against the 2026-08-05 study that nobody would have found by argument. The earlier defence of the
omission was coherent and wrong: re-running it did produce an identical verdict, but it also
produced two retractions and one decisive new fact. **A one-minute measurement that is cheap enough
to decline is cheap enough to run.**

**`create_order` was still never called**, on any date, by any pass. That is the one measurement
this document declines on purpose and will keep declining: it would place a real order in a real
brokerage account for an instrument keel's charter forbids it to hold, and no amount of curiosity
about whether the endpoint works justifies it.

The unbuilt guard rail is deliberate in the specific way this repo has twice recorded. `"equity"` is
already in `ASSET_CLASSES` (`capabilities.py:21`) and is read by nothing; the temptation on writing
a document like this is to make it *mean* something. It must not be made to mean something yet, for
the reason `capabilities.py`'s own docstring gives — a gate on a field no live path reads *"would
be dead code on every real path while reading as a defence"*, and that exact pattern was built and
deleted once already (R1). The same argument forbids the adapter: an equities adapter in this tree
would imply stocks are a live option pending only wiring, when the actual state is that the screen
which would clear one has not been designed.

## The research-integrity lesson

Three research passes went into §1 and §4. **Each one reduced the pass before it, and the second one
over-corrected.** The first produced confident, well-formed, specific claims — an occurrence count,
an enum with named values, a regulator's file number, a press quotation — that direct verification
could not support, and they were withdrawn. The second withdrew two things it should have kept: the
Coinbase-for-Agents roadmap wording, which was sitting readable on `docs.cdp.coinbase.com` while the
pass was checking `coinbase.com` and getting a 403, and the trading-session enum, which is printed
in full on the create-order reference. **It also left a fabricated version of that enum standing in
§5/E3 while announcing its withdrawal in §1b** — a retraction that did not survive a `grep` of the
document making it. The third pass re-opened every source and re-ran the probe, and turned up the
single most decisive fact in the document, which no amount of reasoning would have produced: there
is no equity market data.

Three rules fall out of that, and they are cheap:

1. **A claim is only as good as the source someone actually opened.** Not searched, not summarised,
   not inferred from a schema — opened. This is the same rule §4 arrives at independently from the
   Schwab case, where five cross-linked pages on one four-month-old domain read as corroboration and
   were a single source.
2. **A withdrawal is a claim too, and needs the same standard.** "Could not be confirmed" often means
   "was looked for in the wrong place." Two of this document's withdrawals were wrong.
3. **A retraction is not done until the retracted text is gone.** Announce it in the caveats *and*
   `grep` for it in the body. The confidence of a claim is uncorrelated with its truth, and a
   document that says so about its sources owes the same scepticism to itself.

## Caveats and what is UNVERIFIED

- **The probe was re-run on 2026-08-09** (read-only, POST-guarded, `create_order` never called), so
  the Coinbase measurements are current rather than four days stale. **This removes what an earlier
  draft called the document's weakest point** and replaces it with a narrower one: the re-run
  measured the *product surface*, not the order endpoint, which remains untested by design.
- ⚠️ **Two claims from this document's brief were REFUTED and are corrected in place, not silently:**
  that Advanced Trade exposes no securities endpoint (§1b), and that Schwab's approval review
  scrutinizes AI-driven functionality (§4 — traced to five interlinked pages on one non-affiliated
  domain registered 2026-04-20). A third, that Robinhood offers no sanctioned programmatic equities
  access, is materially out of date (§4).
- ⚠️ **Two corrections run against the 2026-08-05 study itself, and are attributed and dated rather
  than quietly fixed** (§1a, both from the 2026-08-09 re-run): its **"non-deterministically
  enumerable universe"** finding no longer reproduces — the listing is now stable, zero drift across
  four calls, a clean cursor walk to 19 188 ids — and its **"the order path is refused by design"**
  conclusion was never established, because it rested solely on a preview 403 and `create_order` was
  never called. Neither is a defect in that study's method; the first is a venue that changed, the
  second is an over-generalisation this document repeated before catching it.
- ⚠️ **Two claims withdrawn by an earlier pass have been RESTORED, and the withdrawals were the
  error:** the **"Coinbase for Agents" roadmap wording**, which is quotable verbatim from
  `docs.cdp.coinbase.com` and uses the word "equities" under an explicit "Coming soon" header (§1d —
  the earlier pass checked the 403-gated `coinbase.com` instead), and the **equity trading-session
  enum**, which is printed in full on the create-order reference and is quoted with its real
  six-value spelling in §5/E3. ⚠️ **The related brief claim that the "planned for the future" line
  was CoinDesk's paraphrase rather than Coinbase's own wording is REFUTED**: only the exact CoinDesk
  sentence was the journalist's construction.
- ⚠️ **Still withdrawn and printed nowhere, because no pass could confirm them:** the brokerage
  launch chronology and its FINRA/SEC/Apex filing citations (§1a — `coinbase.com` and
  `help.coinbase.com` return HTTP 403 to automated fetching), the tokenized-equity press citations
  (§1e), an **occurrence count** for "equity" in the spec, and **named equity-specific error codes**.
  **An earlier draft printed several of these as verified.** None is load-bearing: §1b settles the
  venue question from a directly-readable source, and the "real US shares on venue CCM with Apex
  clearing" finding those citations were decorating is independently established by keel's own
  committed probe.
- ⚠️ **A finding that cuts against this document's conclusion, recorded rather than buried:**
  `equity_trading_flags` returns `tradable/buy_enabled/sell_enabled: true` on **998 of 1000** equity
  products, with no halt, view-only or liquidate-only state (2026-08-09). The products declare
  themselves tradable. It does not change the verdict — §2 is broker-independent and the market-data
  vacuum is separately fatal — but anyone arguing the surface is dead should know it is the products
  themselves saying otherwise.
- **`create_order` has still never been called on a Coinbase equity**, on any date, by any pass, and
  it will not be. The order path is documented as existing, measured as un-previewable, and has
  never been verified *working*.
- ⚠️ **"The introduction page is merely stale" is NOT established, and an earlier draft asserted it.**
  Equity support appears only inside auto-generated per-endpoint OpenAPI schema blocks carrying
  internal protobuf names (`coinbase.public_api.authed.retail_brokerage_api.*`). The hand-written
  surfaces — REST introduction, overview, orders guide, FAQ, changelog (**zero occurrences of
  "equit"**) — are silent. Given the live 403 and the data vacuum, **the introduction describes what
  is usable more accurately than the schema describes what is shipped** (§1b).
- **§71.6 applied to real US shares is an INFERENCE**, not a direct citation. Stated above; restated
  here because the whole verdict rests on it.
- **Which `backing` value a common share takes (`'ayn` / `'dayn` / neither) is UNVERIFIED** and is
  a ruling question, not a probe question.
- **No fundamentals data vendor was priced or licence-checked.** It is a direct input to P1's range
  and is the largest single source of uncertainty in the estimate.
- **Every broker in §4 is documentation-read, never exercised.** Sandbox quality, fill behaviour,
  rate limits, corporate-action reporting and data-feed reliability are all unmeasured. The
  recommendation of Alpaca is a paper judgement.
- **AAOIFI SS 21's criteria are VERIFIED from the standard's own text**, which AAOIFI publishes
  openly (§3). All four thresholds (30% / 30% / 5% / ≥30%), their exact denominators, the absence of
  any receivables test, and the absence of any averaging window are read from that document, not
  from a secondary summary. Two items remain open: **the edition is UNVERIFIED** (the AAOIFI-hosted
  extract carries no edition year; issuance is confirmed as 2004-05-20), and **whether any post-2004
  revision exists is UNVERIFIED** — none was found, none was ruled out, since AAOIFI's e-standards
  portal is paywalled. Neither affects the verdict; both should be checked before the standard is
  written into the KB.
- ⚠️ **SS 21 contradicts itself on the clause 3/19 tangible-assets floor** — 30% in the clause,
  one-third in footnote (1) to 3/1 and in the Appendix, with AAOIFI's own note that the footnote
  anticipates *"subsequent amending procedures"*. Recorded, not resolved.
- ⚠️ **Two claims in §65.10 are now out of date, through no fault of the entry**, which faithfully
  records its source: DJIM retired its receivables screen (2023-03-17) and its cash screen
  (2023-09-15) and now has a **single** accounting ratio, and its window is **24 months**, not the
  12 §65.10 records. A screen built against §65.10's DJIM description would implement a retired
  methodology. This matters only because §65.10 is the KB's sole screening entry today.
- **The current S&P Shariah (2026) and DJIM (2026) methodologies were not read directly** —
  spglobal.com refuses automated fetching. The verified editions are DJIM May 2025 and S&P Shariah
  January 2015; DJIM's own change log shows no accounting-screen change after 2023-09-15, so the
  comparison table is high-confidence but not certified current.
- **Day estimates are judgement, not measurement**, and the range is deliberately wide. P1 in
  particular could be much larger — or could be correctly refused.
- **The recurring per-period screening cost is an operating cost that is not in the day estimate
  at all**, because it does not end.
