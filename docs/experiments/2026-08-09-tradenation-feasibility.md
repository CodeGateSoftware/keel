# Trade Nation — can keel trade through it?

**Date:** 2026-08-09
**KB basis:** §28.1's third prohibition names the exclusion at the root — gharar covers *"all
financial derivative instruments, forwarding contracts, and future agreements"*, and the section
spells out the consequence itself: this *"grounds exclusion of **CFDs**, futures, forwards, options,
all derivatives"*. §28.1's second prohibition, maisir, is the one that catches the *other* half of
Trade Nation's product range: *"the speculative trade or exchange of money for debt **without an
underlying asset transfer**"* — a financial spread bet is the paradigm case, not an edge one.
§28.2 supplies the positive statement the refusal is measured against: *"Spot crypto = you OWN the
asset … **CFD/future/option = you own nothing**, you hold a bet on price with a counterparty →
gharar + no ownership → excluded"*, and derives long-only from the same root. §65.11 sharpens
maisir into an operative criterion: contracts *"settled through price differences only are covered
under gambling"*, with the note that *"the operative criterion is **cash settlement / no delivery**,
not futurity as such"* — which is what makes it bite on a CFD that has no expiry at all; the same
section excludes *"Swaps, CFDs, hedging products"* by name and records that *"short-selling has been
prohibited by almost all scholars"*. §56.1, as corrected by §66.3, governs the FX half: retail
"spot" FX is *"automatically rolled over to the next business day … necessary to avoid the actual
delivery of the currency"* with the
*"interest differential"* charged for the deferral, and §66.3 sharpens the defect from "T+2 ⇒ riba"
(too blunt) to **non-delivery by design**, supplying the litmus test this document applies
throughout — the Malaysian Perlis fatwa's condemnation of *"no transfer of property by both
parties, either substantively or constructively"*. §65.4/§67.1/§71.5 triangulate `qabd` across
three independent sources onto one operative test — possession is the ability to dispose, *"nothing
to prevent the buyer from taking physical possession … whenever he desires"* — which is the direct
grounding for rail 17. §71.5 also records the one attribute Trade Nation genuinely satisfies:
*"registered/licensed with a recognised regulator"* as a per-broker compliance property, re-checked
per venue. §65.5 governs their gold and silver: *"futures trading in commodities like gold and
silver that serve as Thaman is forbidden"*, with the 72-hour settlement bound as the citable
outer limit. §71.6 grounds the stock-CFD point: a claim-bearing instrument requires share-style
business *and* financial screening keel does not have, so it is rejected **by capability**, not
merely by sector. §66.7's crypto gap — since **partially** closed by §71/§72/§85 — is not
load-bearing here in either direction: their crypto products are refused as CFDs, not as crypto.
**Status:** feasibility only. **No code changed, no adapter added, no dependency introduced.**
**Evidence:** public documentation review, not a live probe, and this document does not dress it up
as one. **No Trade Nation account was opened**, no demo registered, no MT4 terminal connected, no
order placed or previewed. Every claim about the product range, the platforms and the regulators is
sourced to a `tradenation.com` / `support.tradenation.com` page or a named third party, read on
2026-08-09 and cited inline. The negative claim — that no public trading API exists — is
established by **enumeration of their own published sitemaps**, not by a failed search; the method
and the counts are given in full below so the claim can be re-run and falsified rather than taken
on trust. `api.tradenation.com` exists and resolves, but it is a private backend for their own
apps: it was checked for existence and **deliberately not probed, enumerated or reverse-engineered**
beyond an unauthenticated request to `/`, per issue #204's own instruction. Where the public record
is silent, this document says so rather than guessing.

## The question

Issue #204 asks the same question of Trade Nation that #201 asked of cTrader, and it deserves an
answer that does not simply reuse the previous one. The two cases look alike — a retail
CFD/spread-betting house surfaced as a candidate execution venue — but they fail in structurally
different places, and conflating them would teach the wrong lesson to whoever reads this next.

cTrader's assessment had two things to lean on: a fully documented protocol whose own message model
made leverage, swap and short-symmetry unavoidable, and a Python SDK that had not shipped in two
years. Both are absent here, in opposite directions. Trade Nation publishes **no** protocol
documentation at all, so there is no message model to read — and yet a **technically usable,
self-serve, broker-advertised Python order-placement path nevertheless exists**, because Trade
Nation offers MetaTrader 4 and explicitly promotes Expert Advisors as its automation route. That
inversion is what makes this document worth writing separately: the refusal here cannot rest on
"the tooling is unmaintained" or "the transport is awkward". It has to rest entirely on **what is
being traded**, and it does.

## Verdict

**No. Trade Nation is refused, and the refusal is structural — it rests on the instrument, not on
the transport.** Nothing in Trade Nation's product range is unleveraged spot ownership. Every
tradeable thing they offer is a contract for difference, or in some jurisdictions a financial
spread bet on the same underlying: forex, indices, commodities, individual stocks, bonds and
cryptocurrencies, all as CFDs, with no share ownership and no spot crypto anywhere
(https://tradenation.com/markets, https://tradenation.com/our-platforms/) — TN Trader carries named
bond futures (`BUND - Future (Sep)`, `BOBL - Future (Sep)`) and differential spreads (`Gold / Silver
Diff`, `Germany 40 / UK 100 - Rolling Future Diff`) alongside the more familiar range, which only
widens how much of it is CFD-only. keel's charter is spot-only, and several independent gates already
enforce that charter against any venue without needing to know Trade Nation exists.

| Gate | What it checks | Trade Nation's shape | Result |
|---|---|---|---|
| The product itself | is anything here unleveraged spot ownership? | CFDs and spread bets only — no share custody, no spot crypto, no delivery of anything | **the decisive failure.** Nothing to build an adapter *for* |
| Rail 19 (`spot_instrument`) | product id must be `BASE-QUOTE`, uppercase, exactly one hyphen (`guards.py:750-756`) | `EUR/USD`, `XAUUSD`, `US500` (MT4), `Germany 40`, `UK 100` (TN Trader) — a slash, spaces or nothing, but never the single hyphen the grammar requires | veto on every intent; and the rail's comment says there is **no config field to widen** it (`guards.py:741-742`) |
| Rail 18 (`settlement_currency`) | last segment vs `config.settlement_currencies` (`guards.py:696-708`) | `quote_currency_of("EUR/USD")` is `None` — no separator (`products.py:57-62`) | fails **closed**, independently of rail 19 |
| Rail 17 (`withdrawal_capability`) | `qabd`: is the account in a state where the asset could be withdrawn on demand? (`guards.py:644-666`) | a CFD has **no underlying to withdraw, ever** — the question has no true answer | fails closed on every LIVE BUY; but see the honest caveat below — this is an *operator*-honesty gate |
| Rail 13 (USDC-funding) | settled quote balance ≥ full order notional (`guards.py:512-541`) | margin: the whole point is to post a fraction of notional | structurally anti-margin — 100% cash cover or no BUY |
| Rail 1 (halal allowlist) | base asset ∈ `config.allowlist` (`guards.py:368-373`) | `EUR`, `US500`, `XAUUSD`, `AAPL` are not on it, and could not be added honestly | veto, both sides, every mode |
| Curation screen (`screen.py`) | `spot_instrument` grammar, then `backing` ∈ {ayn, dayn, native}, then `pays_yield` | a CFD is a bilateral contract — none of the three backings; the id fails the grammar one gate earlier | refused at admission, *provided the operator attests the contract rather than its underlying* — see #202 |
| `BrokerCapabilities.asset_classes` | vocabulary: `{"spot", "futures", "equity"}` (`capabilities.py:21`) | no `cfd` / `margin` / `derivative` exists to declare | cannot be honestly declared — but **inert on the live path today**, by the class's own admission (`capabilities.py:28-40`) |
| `Side` / strategy layer | `Side` is BUY/SELL only (`types.py:25-29`); `Setup.direction: Literal["long"]` (`base.py:39`) | CFD SELL opens a short, not a disposal | no vocabulary exists to express it |

Instrument naming is not even consistent across Trade Nation's own two platforms — TN Trader spells
these `EUR/USD`, `Gold (per 0.1)`, `US 500 (Per 1.0)`, `UK 100`, `Germany 40`; MT4 spells them
`EUR/USD`, `XAUUSD`, `US500`, `UK100`, `DE40` — but every variant on both platforms fails rail 19's
grammar the same way, for the same reason: none of them is `BASE-QUOTE` with exactly one hyphen.

**One-line answer: Trade Nation is not a spot venue with a CFD wrapper bolted on — the CFD *is* the
product, there is no unleveraged, delivery-settled instrument anywhere in the range, and the
existence of a working automation path through MetaTrader 4 does not change what that path would be
automating.**

## What Trade Nation actually is

Trade Nation is a retail CFD and financial-spread-betting broker with a proprietary web/mobile
platform (TN Trader) and a MetaTrader 4 offering alongside it. Its published range is
*"1,000+ local and global markets, such as Forex, indices, commodities, and stocks"*
(https://tradenation.com/markets), plus cryptocurrencies and, on TN Trader specifically, bond
futures (`BUND - Future (Sep)`, `BOBL - Future (Sep)`) and cross-instrument differential spreads
(`Gold / Silver Diff`, `Germany 40 / UK 100 - Rolling Future Diff`) — every one of them as a contract
for difference, and in the jurisdictions where the tax treatment applies, as a financial spread bet
on the same underlying. Bonds are named explicitly, alongside forex, among the design spec's own
exclusions —
*"Bonds, forex carry, derivatives/futures/perps"*
(`docs/superpowers/specs/2026-07-15-keel-autotrade-design.md:39`) — so Trade Nation's bond products
hit a hard exclusion this document has already invoked for its forex products, not a new one. There
is no share dealing, no custody, no spot crypto, and no delivery
mechanism of any kind — their own UK Client Agreement states the no-ownership fact as a contractual
term, not merely a marketing description: *"You will not have any rights of ownership or otherwise
in any Instrument as a result of a Transaction with us"* (clause 3.5), quoted in full where it does
the decisive work below. Their headline commercial proposition is **fixed spreads**, which is itself
a tell about the product: a fixed spread is something a dealer quotes, not something an order book
produces.

The entity is regulated in six jurisdictions, per the footer of https://tradenation.com/our-platforms/
— Seychelles FSA (SD150), UK FCA (525164), Australia ASIC (AFSL 422661), Bahamas SCB (SIA-F216),
South Africa FSCA (49846), and Portugal/EU CMVM (601) — and does not accept US residents. This is
worth stating plainly and without hedging, because it is the one compliance attribute in §71.5's
per-broker list that Trade Nation genuinely satisfies: they are *"registered/licensed"* with
recognised regulators, several of them tier-one. **The refusal below is not a claim that Trade
Nation is disreputable, unregulated or shady.** It is a claim about what a CFD *is*, and it would
apply identically to a flawlessly run venue. Regulatory standing answers §71.5's venue question and
leaves §28.1's instrument question completely untouched.

They also operate a "Pro Accounts Programme" — FCA elective-professional status, applied for via
`support.uk@tradenation.com`. It is worth naming only to close it: it concerns higher leverage and
the surrender of retail client protections. It has nothing to do with connectivity, and nothing in
it converts a CFD into an owned asset. It is the opposite of what would be needed.

## The transport question — established by enumeration, not by a failed search

A negative claim about a public API is easy to make carelessly and hard to make well. "I searched
and found nothing" is not evidence; it is the absence of evidence, and it is exactly how a
feasibility document acquires a claim that a later reader quietly discovers is wrong. So the claim
here is made by enumerating what Trade Nation itself publishes, with counts, so it can be re-run.

Their two sitemaps — `tradenation.com/sitemap.xml` (675 URLs) and `tradenation.com/en-gb/sitemap.xml`
(748 URLs) — enumerate **1,423 URLs** between them (counts as of 2026-08-09; they drift as articles
are published, which is why the matched-set result rather than the total is the finding). Matching
that set against `api|institutional|professional|developer|liquidity|white-label|b2b` returns
**zero** connectivity pages. The only URLs containing the substring "api" (case-insensitively) are a
news article about the American Petroleum Institute and a page whose slug contains the word
"capital" (`https://tradenation.com/zest-capital-traders/`); neither is a connectivity page. The
support knowledge base, `support.tradenation.com/sitemap.xml` (160 URLs), matched against
`api|automat|algo|expert|robot|institution`, likewise returns zero. Direct requests to
`/institutional/`, `/professional/`, `/developers/`, `/liquidity/`, `/white-label/` and `/b2b/` all
return 404; `/api/` returns a 302 trailing-slash redirect to `/api`, which is also 404. The word
"API" appears **zero times** in the rendered text of the home page, `/partners/`, `/about-us/`,
`/partner-faq/`, `/tradingview/` and `/metatrader-4/`.

The partner programme is affiliate and Introducing-Broker only (https://tradenation.com/partners/) —
revenue share for referred clients. There is no connectivity tier, no liquidity tier, no technology
tier, and no institutional desk surfaced by that sweep. Beyond their own site,
there is no PyPI package, no GitHub SDK, and no public teardown: searching GitHub for
`api.tradenation.com`, `tradenation trading api`, and `TradeNation-LiveBravo` returns zero results
for all three. `api.tradenation.com` does resolve — to CloudFront, returning 403 at the root — and
that is the entire extent to which it was touched. It is a private backend for their own
applications. Per #204's explicit instruction it was **not** enumerated, authenticated against, or
reverse-engineered, and this document draws no inference from it beyond "it exists and is closed".

### The white-label hypothesis is dead, and that is a real finding

The most promising lead was that Trade Nation might be running someone else's platform, in which
case the vendor's API documentation would be the thing to read and the transport question would
have a different answer. Their Android package id is `com.finsa.tradenation`, which reads like a
vendor namespace. It is not.

`FINSA EUROPE LTD` **is** `TRADE NATION FINANCIAL UK LTD` — the same company, Companies House
number **07073413**, renamed on 23 February 2022 (and before that, `THE TRADER MANAGEMENT COMPANY
LTD` from 2009 to 2014). `com.finsa.` is a legacy in-group namespace surviving a rename, not
evidence of a third-party platform vendor. A deliberate sweep for the usual suppliers —
Devexperts/DXtrade, TradeLocker, Match-Trader, Spotware/cTrader, oneZero, PrimeXM, Gain/StoneX,
Gold-i, Your Bourse, B2Broker, Leverate — found no connection to any of them.

The positive evidence points the other way: they build it themselves. Their own recruitment copy
describes *"the next generation of **TN Trader — a proprietary, React-based trading platform**
designed for the needs of global CFD and spread betting traders"*
(https://apply.workable.com/j/EFF9D2D56F), and a second listing enumerates the engineering surface
as *"Execution & Trading - pricing engines, liquidity-provider integrations, risk monitoring and
trade-execution logic"* (https://apply.workable.com/j/DA8D46C30B). The only bought-in components
identifiable are ChartIQ for charting and TradingView. **So there is no vendor API document to go
and read.** That route is closed, and closing it properly is what turns "we could not find an API"
into "there is not one".

### Competitor context — so the absence reads as normal, not as a red flag

Retail CFD houses split cleanly on this question, and the split tracks size and platform strategy
rather than integrity. Self-serve public retail APIs exist at IG (`labs.ig.com`), Capital.com
(`open-api.capital.com`), OANDA (v20), Saxo (`developer.saxo`) and Interactive Brokers.
Application-gated ones exist at City Index / StoneX (`ciapi.cityindex.com` is live but not
self-serve). And there are none at all at CMC Markets, Spreadex, Plus500 — or Trade Nation.
Proprietary-platform CFD houses of Trade Nation's size push retail automation into MetaTrader
instead of building and supporting a public API, and Trade Nation matches that pattern exactly.

One distinction is worth drawing, because it would be unfair to blur it: Trade Nation is **not**
automation-hostile in the way Plus500 is, whose terms prohibit use of any *"automated data entry
system"*. Trade Nation actively advertises Expert Advisors. The absence of an API here is a product
decision, not a policy against algorithmic clients. Third-party review coverage reaches the same
reading: *"the broker does not currently provide direct API access such as FIX or REST connections,
[but] its MT4 integration still accommodates most retail-level automation needs"*
(https://www.tradomatix.com/broker-reviews/trade-nation/).

## The MT4 / Expert Advisor route — a real path, closed on purpose

This is the intellectual centre of the assessment, and the section that most distinguishes it from
the cTrader one. It would be easy, and dishonest, to write "no API, therefore no" and stop. A
working programmatic order-placement path to Trade Nation **does** exist, it is self-serve, and the
broker advertises it. Any future reader who does thirty minutes of research will find it, and if
this document has not already dealt with it, the whole refusal will look like it rested on a
transport gap that turned out not to be one.

So, stated at full strength before it is answered. Trade Nation's platforms page lists
*"MetaTrader 4 — … **Automate using Expert Advisors (EAs)**"* alongside *"CFD trading on forex,
indices, and more"* (https://tradenation.com/our-platforms/), and their MT4 page is explicit about
the coding route: *"Trading robots, or Expert Advisors (EA), automate the trading process. Have
coding experience? You can create expert advisors using **MetaQuotes Language 4**"*
(https://tradenation.com/metatrader-4/). The live server name is published in their own help centre
— `TradeNation-LiveBravo` (https://support.tradenation.com/connecting-mt4-accounts). An MT4 account
is self-serve: no application, no professional gate, no minimum deposit, no manual review of the
kind Spotware imposes on a cTrader app. And MQL4↔Python bridges are a solved, open-source problem —
Darwinex's DWX ZeroMQ Connector (https://github.com/darwinex/dwx-zeromq-connector) is the canonical
one. A determined engineer could have Python placing live orders at Trade Nation inside a weekend.

There is a second broker-advertised automation route, and it should be named rather than left for a
later reader to discover: Trade Nation runs its own copy-trading app, TradeCopier
(https://tradenation.com/tradecopier/, https://tradenation.com/tradecopier-faq/), which links to an
MT4 account and mirrors trades onto it. It is worth flagging precisely because the enumeration above
would not have surfaced it on its own — the `api|institutional|professional|developer|liquidity|
white-label|b2b` regex run against the two main-site sitemaps has no term that matches "tradecopier",
and the broader `api|automat|algo|expert|robot|institution` regex was run only against the support
sitemap, not the main site. It changes nothing about the verdict: TradeCopier is a mobile
copy-trading client, not a Python path, and whatever it mirrors trades onto is the same MT4 CFD
account already addressed above.

**And it changes nothing, because the thing at the far end of that bridge is still a CFD.** This is
the whole point. Every gate in the verdict table above is a gate on *what is traded*, not on *how
the order got there*. Rail 19 asks what shape the instrument id is. Rail 18 asks what it settles in.
Rail 17 asks whether the acquired thing can be withdrawn. Rail 1 asks whether the base asset is on
the halal allowlist. The curation screen asks what backs it. Not one of them takes a transport as
an argument, and none of them would notice or care that the fill arrived via ZeroMQ from a Windows
terminal rather than over HTTPS from a REST endpoint. §28.1's gharar exclusion and §65.11's
cash-settlement criterion are properties of the contract; a bridge does not launder them.

Because the route is closed on the instrument, the practical objections to it are *secondary* — and
they are recorded here only so that no one mistakes them for the reason, and so that no one later
proposes "MT4 but better" as though the reason had been ergonomic:

- **MT4 is CFD-only, not spread betting**, per Trade Nation's own bullet — so the route does not even
  reach their full range, and the part it reaches is the part §28.1 excludes as gharar rather than
  the part it excludes as maisir. Cold comfort.
- **Fixed spreads do not apply on MT4**; it is variable-spread, which discards the one pricing
  proposition that made Trade Nation distinctive in the first place.
- **No equities on MT4** — those are TN Trader-only — so even the instrument set narrows.
- **No MT5.** Their help centre's own article slug is `do-you-offer-mt5`, and the answer is no. That
  matters concretely: the official `MetaTrader5` PyPI package, the only first-party Python
  integration MetaQuotes ships, is therefore unavailable. Every Python path here is third-party.
- **MT4 is a stateful Windows GUI terminal** requiring an always-on VPS plus a bridge EA holding a
  socket open — a transport shape wholly unlike every broker keel has: a stateless, request/response
  HTTPS client behind the `Broker` port. Adopting it would mean an availability model and a failure
  model the engine has no vocabulary for.
- **Hedging is advertised, not restricted.** A deal-ticket hedging toggle is a named term in Trade
  Nation's Client Agreement and a bullet on their MT4 page — this is not a route Trade Nation is
  trying to close off. The one formal restriction on the books is narrower than "no scalping": a UK
  Scalping Policy aimed specifically at latency and stale-price arbitrage, not at fast trading as
  such, and the UK Client Agreement's clause 12(k) constrains automated devices only by a good-faith
  test. That supports, rather than complicates, the point made above: Trade Nation is not
  automation-hostile the way Plus500 is.

**The MT4/EA route is therefore refused for the same reason the venue is: it places CFD orders.
Re-proposing it with a better bridge, a managed VPS, or an MT5 migration does not move any of the
gates, because none of the gates is about the bridge.**

## The decisive section — instrument semantics

Everything above is context. This is the finding.

A Trade Nation position is a bilateral contract with Trade Nation, settled in cash against the
movement of a reference price. Buying their `XAUUSD` product does not put gold anywhere; buying
their `AAPL` product confers no share and no vote, and the dividend adjustment it credits
(https://tradenation.com/dividend-projections/) is a cash payment from the dealer, not a
distribution from the issuer — which strengthens the point rather than weakening it: it is exactly
§28.2's *"you own nothing"* fact pattern, restated in the one place a naive reader might expect
ownership to leak through. Buying their `Bitcoin` product leaves no coin in any wallet. There is no
custody leg, no delivery leg, no settlement of the underlying, and no mechanism anywhere in the
product range by which one could come to exist. That is not a defect in their implementation — it is
the definition of the instrument they sell, and their own UK Client Agreement says so in terms that
leave nothing to marketing-copy interpretation: *"You will not have any rights of ownership or
otherwise in any Instrument as a result of a Transaction with us. We will not transfer any Instrument
or the rights in such Instrument (such as voting rights) to you"* (clause 3.5).

Applied to the KB's own tests, the answers are unanimous and they are not close:

**§66.3's litmus test — the one the Coinbase study adopted as operative — asks whether the trade
results in transfer of property *"substantively or constructively"*.** For a CFD the answer is never,
by construction, on the venue's own description of its product. The Perlis fatwa §66.3 reproduces
condemns online retail forex on precisely four grounds, and a Trade Nation forex CFD satisfies all
four rather than some of them: no transfer of property either substantively or constructively; riba
in the financing of the margin facility; gharar; and maisir, because *"traders face profit/loss
purely from predicting price direction, no underlying economic activity"*. §66.3's closing
instruction is written as though for this case — *"if ever the answer becomes the latter (e.g., a
derivative-wrapped 'spot-tracking' product), it fails this test regardless of its marketing label."*

**§65.11 supplies the maisir grounding independently of the gharar one**, and its operative criterion
is the sharp one here: contracts *"settled through price differences only are covered under
gambling"*, where *"the operative criterion is cash settlement / no delivery, not futurity as such"*.
A CFD has no expiry at all, so an argument that leans on futurity would miss it entirely; an
argument that leans on cash settlement lands squarely. The same section excludes *"Swaps, CFDs,
hedging products"* by name.

**Trade Nation's spread-betting product is worse, not better.** It is the jurisdictional variant
sold on its tax treatment, and its literal form is a wager staked per point of price movement.
§28.1's second prohibition is maisir, defined as *"gambling/speculation/betting"*; there is no
interpretive distance to travel to reach a product whose own name is "spread **betting**".

**Short symmetry.** A CFD SELL opens a short position; it is not the disposal of a holding, because
there is no holding. §65.11 records that *"short-selling has been prohibited by almost all
scholars"*, and §28.2 derives long-only from the ownership argument independently — *"short = sell
what you don't possess"*. keel's design spec names the same exclusion at
`docs/superpowers/specs/2026-07-15-keel-autotrade-design.md:37`.

**The commodity products are the worst case, not the best one.** The instinct that gold "feels
closer to spot" than an index is exactly backwards. §65.5 makes gold and silver *stricter* than
crypto, not looser: they are named *ribawi* commodities whose *thamaniyyah* is *"not arguable — it
is the paradigm case"*, deferment is prohibited *"no exceptions"*, and the section states outright
that *"futures trading in commodities like gold and silver that serve as Thaman is forbidden"*. The
citable outer bound on settlement latency is 72 hours — reproduced in keel's own code at
`keel/compliance/screen.py:244-248`, which warns that for gold or silver backing *"§65.5's stricter
bay' al-sarf regime applies -- no deferment, 72h settlement bound"*. A CFD does not settle the
underlying in 72 hours, or in 72 years. It never settles the underlying at all.

**The forex products** hit the exclusion the design spec names directly — *"Bonds, forex carry,
derivatives/futures/perps"*
(`docs/superpowers/specs/2026-07-15-keel-autotrade-design.md:39`) — and §56.1's negative exemplar
describes the mechanism from the inside: rolled nightly *"to avoid the actual delivery of the
currency"*, with the *"interest differential"* charged for the privilege.

**The index products** are what the Coinbase study called *"index basket + no delivery + no
constituent screening. Worst case in the set."*
(`docs/experiments/2026-08-05-coinbase-asset-class-feasibility.md:54`). Trade Nation's `US500`,
`UK 100` and `Germany 40` are the same object with the futures wrapper swapped for a CFD one, which
removes the expiry and changes nothing else that matters.

**The stock products fail by capability, which is a different and stronger failure.** §71.6's rule
is that any claim-bearing instrument requires share-style screening of *"core business activity AND
financials"* — a two-part test keel performs neither half of, because its allowlist deliberately
contains no issuer-claim instruments. The KB's recorded conclusion is that *"admitting any
equity/revenue/claim-bearing token would require a screening capability we do not have"*. A stock
CFD is worse still: it is a claim on Trade Nation about the price of a claim on the issuer. Even if
keel had the share screen, the CFD wrapper would remain.

**Crypto, honestly.** §66.7 recorded that none of its four sources addressed crypto directly. That
gap is now **partially** closed — §71.1's IIFA Resolution 237 is a deliberate non-ruling, §72 maps
a split and thin literature, and §85 is the first in-depth treatment by a qualified jurist. None of
it bears on this question in either direction, because Trade Nation's crypto products are refused
**as CFDs**, on §28.1 and §65.11, exactly as their gold and index products are. The refusal is on
the wrapper, not the underlying, so no crypto-specific ruling is extended or relied on here.

## The keel-side gates — verified against the working tree

The instrument section above is sufficient on its own. What follows maps the refusal onto keel's
actual code, in the order an adapter attempt would hit it, so that the verdict is traceable rather
than asserted. Every line reference below was read in the working tree on 2026-08-09.

**1. Rail 19, spot instrument shape.** `keel/execution/guards.py:750-756` vetoes any
`OrderIntent.product_id` for which `parse_spot_product_id` returns `None` — *"not a well-formed spot
product id (BASE-QUOTE, uppercase, exactly one hyphen)"*. Here, unlike in the cTrader case, the
grammar **does** fail the venue on its real symbols, and the naming is platform-specific to boot:
TN Trader has `EUR/USD`, `Gold (per 0.1)`, `US 500 (Per 1.0)`, `Germany 40`, `UK 100`; MT4 has
`EUR/USD`, `XAUUSD`, `US500`, `DE40`, `UK100` — a slash, spaces or nothing on either platform, but
never the single hyphen `_SPOT_PRODUCT_ID_RE` requires (`packages/keel-core/keel_core/products.py:46`),
and `parse_spot_product_id` (`products.py:86-117`), which applies that regex, rejects every variant
of both. The rail
is un-skippable by construction: its own comment at `guards.py:737-739` reads *"BOTH SIDES, EVERY
MODE, DCA INCLUDED — deliberately not in `LIVE_STATE_RAILS`"*, so paper trading runs it too, and
*"a rehearsal cannot build a track record on trades live trading would veto."* The load-bearing
sentence, though, is the charter statement two lines further down (`guards.py:741-742`):

> Spot-only is this agent's CHARTER, not an operator preference, so there is no config field here
> to widen (unlike rail 18's `settlement_currencies`).

The module docstring says the same thing twice more, at `guards.py:1` — *"THE HARD RAILS (§14) —
enforced before every order, un-overridable"* — and at `guards.py:93-94`: *"no config field to widen --
spot-only is this agent's charter, not an operator preference."*

**2. Rail 18, settlement currency.** `guards.py:696-708` resolves the settlement leg with
`quote_currency_of` and compares it to `config.settlement_currencies`. Because `quote_currency_of`
returns `None` for any id without a separator (`products.py:57-62`), `EUR/USD` produces
*"cannot resolve a settlement currency … failing closed"* — a second, independent veto reached by a
different route than rail 19's. This rail *is* operator-configurable — the field defaults to
`{USD, USDC}` (`packages/keel-core/keel_core/config.py:319`, `config.yaml:100-102`) and
`guards.py:689-690` calls it *"the escape hatch, which is why the set is not hardcoded here"* — but
widening a currency set does nothing for an id that has no currency leg to widen *to*.

**3. Rail 17, withdrawal capability (`qabd`).** `guards.py:644-666`. The rail's comment states the
juristic basis: possession holds only while *"there is nothing to prevent the buyer from taking
physical possession whenever he desires"*, and *"an asset we cannot withdraw is an asset we may not
validly POSSESS — so acquiring more of it is the thing to stop."* It fails closed on `None`
(`guards.py:652-659`), and the attestation feeding it is read live on every intent with a 7-day TTL
(`keel/execution/executor.py:231-257`, whose docstring is explicit that it is *"never cached"* and
that an expired attestation is treated as UNKNOWN rather than as `False`).

For a CFD the input to this rail has no honest value at all: there is no underlying, so
"withdrawable" is not false, it is undefined. But the honest caveat from the cTrader assessment
applies unchanged and must not be quietly dropped here: `withdrawals_enabled` is the **operator's**
attestation, not an adapter-reported fact, and the rail inspects no venue. It is entries-only, it is
one of exactly two rails `offline=True` skips (`guards.py:167`), so paper never runs it — and an
operator who ran `keel withdrawals attest --enabled` against a CFD venue would clear it. Rail 17 is
an operator-honesty gate. It is a real gate; it is not a gate the adapter itself could not get past.

**4. Rail 13, USDC funding.** `guards.py:512-541` vetoes a BUY unless the settled quote-currency
balance covers the **entire** order notional; an unknown balance fails closed, and SELL is exempt
because *"it produces quote currency, it doesn't consume it"* (`guards.py:513-514`). This rail is
worth naming separately because it is *structurally* anti-margin rather than incidentally so: the
entire commercial proposition of a CFD account is posting a fraction of notional. A venue whose
product only makes sense at less than 100% cash cover meets a rail that only passes at 100% or more.
The same principle is stated at the design level — *"all sizing uses actual cash only"*
(`docs/superpowers/specs/2026-07-15-keel-autotrade-design.md:35`) — and implemented that way:
`keel/execution/sizing.py:22-34` computes size purely as risk-capital over stop distance, with no
margin, leverage or multiplier term anywhere in it.

**5. Rail 1, the halal allowlist.** `guards.py:368-373` vetoes any intent whose base asset is not in
`config.allowlist`, in every mode and on both sides. `EUR`, `US500`, `XAUUSD` and `AAPL` are not on it
— and the path by which they *could* be added runs through the curation screen below, which is the
point.

**6. The curation screen.** `keel/compliance/screen.py` gates admission to the allowlist, and it
refuses a Trade Nation instrument twice. First on shape: `screen.py:205-212` runs **rail 19's own
grammar**, imported rather than restated so the two cannot drift, and the comment at
`screen.py:196-200` explains that there is deliberately no `ScreenPolicy` knob for it because
*"spot-only is this agent's charter. A knob whose only safe value is its default is a liability."*
Second on substance: `screen.py:232-243` requires `backing` ∈ `{ayn, dayn, native}`
(`screen.py:48-51`) and rejects `dayn` as *"a debt claim on an issuer, not an owned thing"*. A CFD
is neither an owned thing nor a debt claim — it is a bilateral price-difference contract — so an
honest attestation lands on the unknown-backing branch at `screen.py:233-237` and is refused for not
being classifiable at all. `riba_yield` is separately a listed haram sector (`screen.py:42`) and
`pays_yield` fires at `screen.py:226-230`, which is what an honestly attested overnight-financing
instrument would trip.

None of this is waivable. `WAIVABLE_CRITERIA = frozenset({"history"})` (`screen.py:60`), and the
comment above it states that the shariah criteria and `settlement` *"can NEVER be waived"* and that
expanding the set is *"a deliberate future decision, not a default -- do not add to it to make a
test pass."* That is enforced three ways: filtering at `screen.py:158` before any branch runs, the
CLI's `click.Choice(sorted(screen_mod.WAIVABLE_CRITERIA))` on both the grant and revoke commands
(`keel/cli.py:974`, `keel/cli.py:1023`), and the repository's own note that *"only criteria in
`WAIVABLE_CRITERIA` are ever honoured, so a row here can never bypass the shariah core"*
(`keel/data/repository.py:746-748`).

**7. `BrokerCapabilities.asset_classes` — real, but inert, and this document will not overclaim it.**
`packages/keel-broker-api/keel_broker_api/capabilities.py:21` fixes the vocabulary at
`frozenset({"spot", "futures", "equity"})`; `__post_init__` raises on anything outside it
(`capabilities.py:55-57`), and the conformance suite forbids the empty-set dodge
(`conformance/suite.py:89-98`: *"An adapter that declares nothing declares nothing checkable."*)
There is no `cfd`, `margin` or `derivative` word to declare, so an honest Trade Nation declaration
cannot be constructed. But the class's own docstring is unambiguous that this is not a live defence
(`capabilities.py:28-40`): *"`asset_classes` is **not** what keeps keel spot-only today, and no
engine code reads it … a gate built on this field would be dead code on every real path while
reading as a defence."* It is a declaration-integrity check. It belongs in the table above because it
is the first wall an adapter author hits, not because it is the wall that holds.

**8. The type system, one level up.** Even before the rails, the strategy layer has no words for
this product. `keel/strategy/rules/base.py:7-9` states the contract in the module docstring —
*"Long-only spot, no leverage: `Setup.direction` is pinned to `"long"` for v1; bearish setups are
exit/don't-buy filters, not shorts"* — and `base.py:39` pins it in the type:
`direction: Literal["long"]`. `Side` is BUY/SELL only
(`packages/keel-core/keel_core/types.py:25-29`), and SELL is understood everywhere as reducing a
long: `guards._open_exposure_by_asset` (`guards.py:281-285`) adds BUY notional and subtracts SELL
notional from a per-asset exposure that is then filtered to strictly positive values. A CFD SELL —
which opens a short — has no representation in that model. The README says the same to a human
reader: *"An offline-first, halal (long-only, no-leverage) auto-trading agent"* (`README.md:3`) and
*"Long-only spot only — no leverage, shorting, or derivatives; sizing uses actual cash, so no riba"*
(`README.md:58-59`), with *"Nothing overrides a rail — not even autonomy"* at `README.md:44-45`.

### Where the rails' limits actually are — stated plainly

There is a sharp point here that this document would be dishonest to leave out, because a reader who
finds it later will reasonably conclude the rest was oversold.

Rails 18 and 19 are **string-shape checks**. They are defeated by renaming — but not symmetrically
across the range, and the asymmetry is worth stating precisely rather than glossed over.

For gold: if an adapter presented Trade Nation's `XAUUSD` CFD as `GOLD-USD`, `parse_spot_product_id`
would return `("GOLD", "USD")` and rail 19 would pass; `quote_currency_of` would return `"USD"`,
which is in the default `settlement_currencies`, so rail 18 would pass too. This is the same residual
`guards.py:725-735` already documents against itself in its own words — `BTC-PERP` *"PASSES this
grammar"* because the grammar *"cannot know which four-letter tokens are currencies without carrying
a currency table it deliberately does not carry."* But `GOLD-USD` is a fabrication: no Trade Nation
platform names the instrument `GOLD` anywhere. Getting past rails 18 and 19 for gold costs a false
ticker on top of everything else.

For forex it costs less, and this is the sharper case. `EUR/USD` → `EUR-USD` is not a fabrication —
it is a faithful transliteration. EUR really is the base leg and USD really is the quote leg; nothing
is invented, only the separator is normalised. `EUR-USD` passes rail 19 (well-formed `BASE-QUOTE`)
and rail 18 (`USD` is in the default `settlement_currencies`) on that one honest rename alone. So for
the FX half of the range, the string-shape gates are defeated by **one** lie, not two, and only rail 1
(`EUR` is not on the allowlist) plus the curation screen (which cannot classify a CFD's backing)
stand between the renamed adapter and an order — see #202 below for why the screen's refusal is
itself conditional. Gold needed a fabricated symbol before those gates even engaged; forex does not.

What stops the renamed CFD, on both instruments, is the remaining gates: rail 1, because neither
`GOLD`/`XAUUSD` nor `EUR` is on the allowlist; the curation screen, which would have to admit it first
and cannot classify its backing; rail 13, because a margin product cannot post full cash cover; and
rail 17, which fails closed absent an attestation the operator would have to make falsely. So the
defence holds — but note precisely *how* it holds, and that it holds by a different margin depending
on the instrument. **The gold path only "works" through a false product name and a false
attestation; the forex path needs only the false attestation, since the renamed id is not itself a
lie.** Rails 18 and 19 stop an accident and an ordinary mistake, not a determined operator who has
decided to misrepresent the venue to their own agent — and for forex specifically, it is rail 1 and
the curation screen doing that work alone, not the string-shape rails.

The one genuinely missing piece is already recorded as an open issue rather than papered over here:
**#202 — "`AssetAttestation` cannot express an instrument wrapper, so the curation screen cannot see
a CFD."** `AssetAttestation` (`screen.py:76-86`) is keyed on `asset`, a base-leg symbol, with no
field for the wrapper the asset is traded through. An operator attesting the *underlying* of a Trade
Nation Bitcoin CFD would record `sector=crypto, backing=native, pays_yield=False` — BTC's existing,
already-admitted spot attestation — and the screen would admit it, because swap cost, leverage and
counterparty exposure are properties of the contract and the schema has no way to name the contract.
The screen's refusal described in gate 6 above is therefore conditional on the operator attesting
the *instrument* rather than its underlying, and nothing in the schema forces that. That is exactly
#202's subject, and it is the reason this document does not present the curation screen as an
unconditional third defence.

## What would have to be true for this to change

Not effort, and not the broker-port migration. Two things could change the verdict, and only two:

**(a) Trade Nation offering genuine unleveraged spot ownership with delivery and withdrawal** —
share dealing with real custody, or spot crypto in a wallet, or allocated metal — reachable
programmatically. Nothing in their published range is close to this. They are a CFD and spread-betting
house by licence, by platform design, by pricing model and by their own recruitment copy; their
engineering roadmap describes *"pricing engines, liquidity-provider integrations, risk monitoring"*,
which is the architecture of a dealer, not a custodian. This is not a roadmap item anyone on keel's
side can influence.

**(b) keel abandoning spot-only.** That is the charter, not a default. Rail 19's comment says there
is no config field for it because there should not be one (`guards.py:741-742`), and the design spec
heads the section *"Governing constraint — halal (non-negotiable)"*
(`docs/superpowers/specs/2026-07-15-keel-autotrade-design.md:30`), hard-excluding leverage/margin/
borrowing, carry/rollover/funding, short selling and derivatives/futures/perps among the bullets at
`:35-39`, against a purpose line that reads *"riba-free (interest-free), long-only,
spot-crypto"* (`:13`).

Note specifically what does **not** get to (a). An elective-professional "Pro" account does not — it
raises leverage. An MT4 account does not — it trades the same CFDs through a different terminal. A
future Trade Nation REST API would not — it would place the same orders over a nicer transport. Each
of these answers a question this document is not asking.

Nor does a **swap-free ("Islamic") account**, and this is worth pre-empting explicitly, because it
is the most predictable challenge to this document and the one most likely to be raised as though it
were a fix. Swap-free accounts are near-universal in retail CFD — they exist precisely so a broker
can offer the product to clients who object to riba on the overnight rollover. What a swap-free
account removes is the interest differential §56.1 and §66.3 condemn on the *financing* of the
position. What it does not touch is §28.1's gharar or §65.11's cash-settlement-no-delivery criterion,
because both are properties of the **contract** — no underlying transfer, settlement through price
differences only — not properties of how the position is financed overnight. A swap-free CFD is
still a CFD: no ownership, no delivery, still refused on the same instrument-level grounds this
document rests on. We could not confirm Trade Nation offers one — no live page surfaced in either
sitemap, and guessed slugs for one 404 — so this paragraph pre-empts the challenge rather than
reports a fact about Trade Nation's actual offering.

### (c) A market-data-only integration — answered directly, because it will be asked

The cTrader assessment took this variant seriously and concluded "not barred, but not worth it".
Trade Nation's case is **weaker on both halves**, and it is worth being clear about why rather than
copying the previous conclusion across.

On the "not barred" half, the architecture is the same: every gate the refusal rests on is
execution-side. Rail 17 gates `is_buy`; rails 18 and 19 gate `OrderIntent.product_id` at order-intent
time; rail 13 gates a BUY's funding; the screen gates admission of a *tradeable* asset. A read-only
price feed touches none of them, and would not go through the `Broker` port at all —
`keel/data/market_feed.py` types its entry points on `CoinbaseClient` directly rather than on any
port interface (`market_feed.py:20` the import, `:74` and `:120` the two signatures), so a
market-data source is already architecturally a different kind of object in this codebase than a
`Broker` is. One gate does reach it: `MarketFacts.product_id` runs rail 19's grammar
at admission (`screen.py:205-212`), and Trade Nation's un-hyphenated symbols fail it — so even a pure
data feed's candidate ids would have to be renamed before they were screenable, which is the same
cosmetic-rename problem as above wearing different clothes.

On the "worth it" half, the case is materially worse than cTrader's on two counts.

First, **the marks are worse**. A CFD price is a dealer's quote, not a settlement print — and Trade
Nation's headline proposition is *fixed* spreads, meaning the quote is a commercial construction
rather than a passive reflection of an order book. cTrader at least surfaced Level II depth from
liquidity providers. And the instruments quoted are ones keel does not and cannot trade: `US500`,
`Germany 40`, `EUR/USD`, `XAUUSD`. keel's live universe is Coinbase USD spot, so this is not even
cross-venue basis on an asset keel holds — it is reference data for a universe keel has no exposure
to.

Second, and decisively, **there is nothing to read it from**. cTrader's market-data case rested on a
documented protocol with published message types, granularities and rate limits; the argument against
it was cost/benefit. Trade Nation has no public data API at all. The only ways to obtain their marks
are a private backend #204 forbids probing, or scraping a React application, or an MT4 terminal
bridge — i.e. the same VPS-plus-EA machinery described above, stood up permanently to harvest
dealer quotes for instruments keel does not trade.

**Conclusion: no. Unlike cTrader, where declining the data was a judgment call worth revisiting if a
cross-venue FX reference need ever arose, here there is no viable read path to decline in the first
place.** If keel ever needs index or FX reference data, the venues to look at are the ones that
publish a documented data API — several are named in the competitor section above — not this one.

## The alternative, recommended honestly

The question behind #204 is presumably not "Trade Nation specifically" but "should keel have a
second execution venue". That is a reasonable thing to want, and refusing this candidate should not
be read as refusing the goal.

The shape that fits is another **spot** venue: real ownership of the asset, a withdrawal path that
gives rail 17 a truthful input, `BASE-QUOTE` instrument ids that rails 18 and 19 can parse without a
rename, and an asset class that can be declared in the port's existing vocabulary without inventing a
word. That work is already underway in this tree —
`packages/keel-broker-robinhood/keel_broker_robinhood/adapter.py:89` declares
`asset_classes=frozenset({"spot"})`, with `quote_currencies=frozenset({"USD"})` at `:88` and a
`supported_orders` set that deliberately omits `market_ioc_quote` rather than declaring a capability
the venue does not honestly have (`:68-78`). That adapter is the right template for a second venue,
and the useful next step for anyone who arrived here wanting one is to finish the broker-port
migration behind it — `keel/commands/_common.py:137-156` still constructs `CoinbaseClient`
directly — rather than to look for a way to make a CFD house fit.

## What was deliberately NOT built

No `packages/keel-broker-tradenation/` package. No dependency added anywhere in the workspace — no
MQL bridge, no ZeroMQ client, no MT4 tooling. No entry point registered under `keel.brokers`. No
conformance test written against a Trade Nation fake. No `pyproject.toml` touched. This document is
the only file this commit adds.

The reasoning is the one the cTrader assessment set out, and it applies here with more force rather
than less. This codebase already deleted one piece of speculative machinery for a closely related
reason: R1's guard rail consulting `BrokerCapabilities.asset_classes`, built and then removed before
merge because nothing on the live path calls `capabilities()`, so it would have been dead code
reading as a **defence** (`docs/experiments/2026-08-05-coinbase-asset-class-feasibility.md`; the
pointer survives in `guards.py:743-747`). An adapter is the mirror image: dead code reading as a
**capability**.

keel holds no general rule against unwired adapters — `packages/keel-broker-robinhood/` is a live
counter-example in this same tree, built and tested deliberately ahead of the broker-port migration
that will use it. The rule is against dead code that misrepresents itself, and the distinction is
exactly the one this document has been drawing throughout. An unwired Robinhood adapter is a spot
venue waiting on plumbing; it will become tradeable when the migration lands, and its presence in the
tree tells the truth about that. An unwired Trade Nation adapter would sit beside it implying the
same thing — a live option pending only wiring — when the actual state is that every instrument it
could ever place an order for is refused on charter grounds that no amount of wiring, and no
migration on any roadmap, will move.

The MT4 route makes this trap sharper here than it was for cTrader, and that is the note to end on.
Because a working Python path genuinely exists, a Trade Nation adapter would not merely *look*
plausible in the tree — it would actually run, and place real orders, against a live self-serve
account, today. That is precisely why it must not exist. The only thing standing between this
codebase and executing leveraged CFDs is that nobody wrote the adapter, and the right way to keep it
that way is to leave the reasoning here in prose and the package directory empty.
