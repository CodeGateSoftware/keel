# cTrader Open API — can keel trade through it?

**Date:** 2026-08-09
**KB basis:** §56.1 as corrected by §66.3 — retail "spot" FX settles T+2, but is *"perpetually
rolled to avoid the actual delivery of the currency,"* with the interest differential charged for
the deferral, so a "spot" label on a product is not evidence of spot settlement. §66.3 sharpens
the defect from "T+2 ⇒ riba" (too blunt) to *non-delivery by design*, and supplies the litmus test
this doc leans on: does the trade involve *"transfer of property, substantively or constructively"*
(the Malaysian fatwa's own condemnation of retail FX) — precisely cTrader's product. §28.1–28.2
name **CFDs** explicitly in the gharar exclusion set, alongside futures/forwards/options, and
ground why spot ownership is halal while a CFD is a bet on price with no ownership. §65.11 confirms
cash-settled-by-price-difference futures are maisir and that short selling is excluded by "almost
all scholars." §65.4/§66.2/§67.1/§71.5 triangulate `qabd` across three independent sources to
*"possession is the ability to dispose, not physical custody"* — the direct grounding for rail 17
below. §71.6 grounds the equity-CFD point later in this doc: a claim-bearing instrument requires
share-style screening keel does not have, so it is rejected **by capability**, not merely by
sector. §66.7 records that no compliance source in this KB addresses crypto directly; nothing here
extends any ruling to a crypto-specific question.
**Status:** feasibility only. **No code changed, no adapter added, no dependency introduced.**
**Evidence:** documentation review, not a live probe. No cTrader account was opened, no
application registered, no connection made. Every claim about the protocol is sourced to a
`help.ctrader.com/open-api/*` page read on 2026-08-09, cited inline; the SDK-maintenance claims
below are sourced to PyPI and GitHub instead, also read and cited on that date. Where the docs are
silent, this document says "docs silent" rather than guessing. Unlike the Coinbase asset-class
study (`docs/experiments/2026-08-05-coinbase-asset-class-feasibility.md`), there is no committed,
re-runnable probe script here — there is nothing to probe against without an account, and none was
opened for this review.

## The question

Coinbase's asset-class study asked "can keel trade what's newly listed on a venue it already
holds a key for." This one asks a cheaper question first: is cTrader — a retail FX/CFD API
surfaced repeatedly as a candidate venue — even worth opening an account for. The answer turns
out not to need an account. It turns on what the protocol's own message model represents, which
is fully documented without one, and on gates keel's own code already enforces regardless of
venue.

## Verdict

**No. cTrader Open API is refused, and the refusal is structural, not a matter of missing an
adapter.** Every account the API can express is a leveraged, short-permitting, financing-bearing
CFD/FX account — there is no message anywhere in the protocol for an unleveraged spot holding.
keel's charter is spot-only, and multiple independent rails already enforce that charter against
any venue, cTrader included, without needing to know cTrader exists.

| Gate | What it checks | cTrader's shape | Result |
|---|---|---|---|
| `BrokerCapabilities.asset_classes` | vocabulary for the adapter's declared instrument classes | none of `spot`/`futures`/`equity` describes a leveraged CFD | cannot be honestly declared (inert on the live path today — see below) |
| `OrderSpec` sum type | BUY/SELL sized in base or quote units against a holding | cTrader SELL opens a short, not a disposal | no vocabulary exists to express it |
| `Broker.get_balances()` | per-currency `Balance(currency, available, total)` | only a deposit-currency cash balance plus position P&L | nothing truthful to report for "a base-asset holding" |
| Rail 17 (`withdrawal_capability`) | `withdrawals_enabled` on entry, BUY-only, skipped in paper/offline | a CFD has no underlying to ever withdraw | `None`/`False` — fails closed on every LIVE BUY |
| Rail 19 (`spot_instrument`) | product id must be a spot shape | irrelevant — the underlying contract is leveraged regardless of id shape | the charter comment says there is no config field to widen this |
| Curation screen (`screen.py`) | `pays_yield`, `backing`, keyed on `asset` not on instrument wrapper | a CFD's *underlying* attests the same as spot BTC/ETH — the schema cannot name the contract | **not a reliable gate here** — a real modelling gap, see below |

**One-line answer: cTrader is not a spot venue with a leverage option bolted on — leverage,
margin, short-symmetry and cash-only P&L are load-bearing in every core message type, and there
is no configuration of the account or the API that removes all four at once.**

## What cTrader Open API actually is

### Protocol and transport

cTrader Open API is Protobuf over TCP+SSL as the primary transport, with a parallel WebSocket
transport on the same host/port and a separate JSON transport on its own port
(`help.ctrader.com/open-api/connection`). Live and demo are wholly separate connections:
`live.ctraderapi.com:5035` (protobuf) / `:5036` (JSON), and `demo.ctraderapi.com:5035` / `:5036`
(`help.ctrader.com/open-api/proxies-endpoints`). The connection is long-lived and requires a
`ProtoHeartbeatEvent` **every 10 seconds**, or the server disconnects
(`help.ctrader.com/open-api/connection`, `/open-api/faq`).

The official SDK is `ctrader-open-api` on PyPI (`import ctrader_open_api`), source at
`github.com/spotware/OpenApiPy`, built on Twisted. Latest release is **0.9.3, dated 2024-08-06**
(PyPI); the last push to the GitHub repo was 2024-08-07, and the repo is not archived. **No
release or commit in roughly two years** as of 2026-08-09 — this reads as a maintenance-mode
project, not an actively developed one. (Star/open-issue counts are omitted deliberately: they
move by the week and would read as false precision a year after this document is written; the
dates are the load-bearing fact and are stable.) It is TCP-only; the WebSocket transport is
documented as .NET-SDK-only, so a Python integration would be building against the older, less
maintained side of the protocol.

### Auth

OAuth2 authorization-code flow. An app is registered at `openapi.ctrader.com/apps` under a cTID.
Registration is open to any individual — it is **not** gated on being a broker or a partner —
but every new app starts in a "submitted" state and is **manually reviewed by Spotware** before
it can be used outside the built-in Playground. Scopes are `accounts` (view-only) and `trading`. An
authorization code lives 1 minute; an access token lives roughly 30 days (2,628,000 seconds); a
refresh token has **no expiry** and rotates on use. Before any trading message is accepted, the
client must complete `ProtoOAApplicationAuthReq` → `ProtoOAGetAccountListByAccessTokenReq` →
`ProtoOAAccountAuthReq` per account (`help.ctrader.com/open-api`).

### Account model

cTrader Open API is not a brokerage in its own right — it is the API surface of the white-label
cTrader platform that retail FX/CFD brokers license. A usable account means an account at a
cTrader-affiliated broker; the docs do not enumerate which brokers those are (docs silent). The
demo environment is first-class and is the documented recommended path for development.
`ProtoOAAccountType` is `HEDGED` / `NETTED` / `SPREAD_BETTING`
(`help.ctrader.com/open-api/model-messages`). That `SPREAD_BETTING` is a first-class,
equally-weighted account type alongside the other two is itself telling about what kind of
product this is.

## The decisive section — instrument semantics

This is the core finding, and it comes entirely from the Protobuf model reference
(`help.ctrader.com/open-api/model-messages`, `/open-api/messages`).

**Leverage is not opt-in — it is intrinsic to how every account is modeled.**
`ProtoOATrader` carries `leverageInCents` and `maxLeverage` as core, always-present account
attributes. `ProtoOAPosition` carries `usedMargin`, `marginRate`, and `swap` as baseline fields on
**every** open position, not as extensions for a "margin mode." `ProtoOASymbol` defines
`swapLong`/`swapShort` per symbol, plus `swapRollover3Days` (the triple-swap-Wednesday
convention), `swapCalculationType`, `swapPeriod`, `swapTime`, and `chargeSwapAtWeekends`.
**Overnight financing is the default, per symbol.**

**There is no configuration in which holding a position overnight is cost-free.** The only
swap-free path is a broker-configured Shariah-compliant account
(`ProtoOATrader.swapFree = true`) — and it is not actually free of financing cost. It substitutes
a `rolloverCommission`, a daily per-lot administrative fee, expressed through
`ProtoOAChangeBalanceType.BALANCE_WITHDRAW_ROLLOVER` ("Charge of rollover fee for Shariah
compliant accounts"). This is the point a reader will most want to disbelieve, because
`swapFree` looks superficially like the answer: it renames the charge, it does not remove it.

This is not merely an operational observation — it is the KB's own halal argument against this
venue class, and it is the sharpest one available (§56.1, corrected by §66.3). A "spot" FX
position that is rolled nightly specifically to avoid delivery, with an interest differential
(or, on a `swapFree` account, a `rolloverCommission`) charged for the deferral, is exactly the
pattern §56.1 names as riba al-fadl/al-nasee'ah — and §66.3's Malaysian-fatwa litmus test is the
one to apply here directly: does the position ever result in *"transfer of property, substantively
or constructively"*? `ProtoOAPosition`'s only exit is `ProtoOAClosePositionDetail`, a cash P&L
against the deposit currency (below) — never a change in an asset-denominated holding — which is
the "never" answer to that test, on the protocol's own terms, independent of anything keel's rails
additionally veto.

**No 1:1 / unleveraged mode exists anywhere in the protocol.**
`ProtoOATotalMarginCalculationType` (`MAX`/`SUM`/`NET`), `ProtoOAExpectedMarginReq`,
`ProtoOAMarginCall`, `ProtoOAStopOutStrategy`, and `ProtoOADynamicLeverage` are woven through the
trading path, not isolated to an opt-in corner of it. No field on `ProtoOANewOrderReq`,
`ProtoOASymbol`, or `ProtoOATrader` flags an order as unleveraged, fully collateralized, or
delivery-settled. The vocabulary for that state does not exist.

**Short selling is intrinsic, not additive.** `ProtoOATradeSide` is a plain BUY/SELL enum used
identically by orders, positions, and deals: SELL opens a short exactly as BUY opens a long, and
there is no "sell what you own" path distinct from it. Confirming this by omission:
`ProtoOASymbol.enableShortSelling` is a per-symbol toggle to **disable** short selling — the
default is on, and brokers restrict it rather than add it — with a dedicated
`SHORT_SELLING_NOT_ALLOWED` error code for when a broker has turned it off.

**No cash-spot-balance-in-asset concept exists.** The only monetary state on the account is
`ProtoOATrader.balance`, a single number in the account's `depositAssetId` currency, plus
per-position `usedMargin` and unrealized P&L (`ProtoOAGetPositionUnrealizedPnLReq` returns
`grossUnrealizedPnL`/`netUnrealizedPnL`, both "denoted in the account deposit currency"). Closing
a position yields `ProtoOAClosePositionDetail` with `grossProfit`/`swap`/`commission`/`balance` —
cash P&L measured against the deposit currency, never a change in an asset-denominated holding.
There is no message anywhere representing "you hold 0.1 BTC."

Asset classes themselves are broker-defined and fetched dynamically
(`ProtoOAAssetClassListReq`, `ProtoOASymbolsListReq`), but `ProtoOACommissionType` documents the
intended universe: `PERCENTAGE_OF_VALUE` is "usually used for Equities"; `USD_PER_LOT` /
`QUOTE_CCY_PER_LOT` are "usually used for CFDs and futures for commodities, and indices." Since
those "equities" sit inside the same margin/swap/short position model, with **no settlement,
delivery, or custody concept anywhere in the API**, they are equity CFDs, not share custody — the
same distinction the Coinbase study drew between tokenized equities and real Apex-cleared shares,
resolved the opposite way here: nothing on this API is ever a real share.

Order types on `ProtoOANewOrderReq` are MARKET, LIMIT, STOP, STOP_LIMIT, MARKET_RANGE — a sixth
enum value, `STOP_LOSS_TAKE_PROFIT`, is internal to protective legs rather than a placeable order
type. Time-in-force is GOOD_TILL_DATE, GOOD_TILL_CANCEL, IMMEDIATE_OR_CANCEL, FILL_OR_KILL,
MARKET_ON_OPEN.

**Preview covers margin only.** `ProtoOAExpectedMarginReq` is documented as usable "before sending
a new order request," but it estimates margin — there is **no** dry-run for expected fill price,
fee, or slippage. For keel specifically: even setting the charter question aside, a `preview_order`
implementation against this API could only ever be `synthetic=True`, and what it would be
previewing is a margin requirement, not the fill-price/fee economics keel's Preview DTO actually
describes.

## Market data — genuinely good, worth saying plainly

This is the one area where cTrader is not a weak fit. `ProtoOAGetTrendbarsReq` covers
M1/M2/M3/M4/M5/M10/M15/M30, H1/H4/H12, D1, W1, MN1. The docs state that per-period maximum
from/to distance constraints exist but **do not publish the numbers** — docs silent on the actual
values. `ProtoOAGetTickDataReq` allows up to a 1-week window (604,800,000 ms), bid/ask selectable,
with a broker-side chunk cap signalled by `hasMore`. Live data is push-based over the persistent
connection: `ProtoOASubscribeSpotsReq` → `ProtoOASpotEvent` for tick-by-tick quotes, live
trendbars via `ProtoOASubscribeLiveTrendbarReq` (which requires an active spot subscription), and
Level II depth via `ProtoOASubscribeDepthQuotesReq` / `ProtoOADepthEvent`. None of this changes
the verdict — the instrument model does — but it means the refusal below is not a data-quality
argument, and should not be read as one.

## Rate limits

50 requests/second for non-historical calls, 5/second for historical calls, per connection
(`help.ctrader.com/open-api`). Unrealized-P&L polling is recommended at 2–3 second intervals.
Breaching the limit returns HTTP 429 or `BLOCKED_PAYLOAD_TYPE` with a `retryAfter` value. A
`CONNECTIONS_LIMIT_EXCEEDED` error code exists; the numeric connection cap is not published.

## The keel-side gates — verified against the working tree

The instrument-semantics section above is sufficient on its own to explain why cTrader does not
fit. What follows is the gate-by-gate mapping onto keel's actual code, in the order an adapter
attempt would hit them, so the refusal is traceable rather than asserted.

**1. `BrokerCapabilities.asset_classes`.**
`packages/keel-broker-api/keel_broker_api/capabilities.py:21` defines
`ASSET_CLASSES: frozenset[str] = frozenset({"spot", "futures", "equity"})`, and `__post_init__`
(`capabilities.py:51-58`) raises `ValueError` on anything outside that set — but *only* outside
it: `__post_init__` checks `self.asset_classes - ASSET_CLASSES`, so `asset_classes=frozenset()`
raises nothing at construction. The thing that actually forbids that empty-set dodge is the
conformance suite, not `__post_init__`:
`packages/keel-broker-api/keel_broker_api/conformance/suite.py:89-98`,
`test_asset_classes_is_non_empty_and_drawn_from_the_known_vocabulary`, whose docstring states the
point directly — *"An adapter that declares nothing declares nothing checkable."* Either way,
there is no `margin`, `cfd`, or `short` in the vocabulary — an honest, non-empty cTrader
declaration cannot be constructed, because none of the three permitted words describes a
leveraged, short-permitting CFD account, and an empty one fails conformance instead. That said,
this is a statement of vocabulary, not the operative defence: the class's own docstring says so
directly —

> `asset_classes` is **not** what keeps keel spot-only today, and no engine code reads it. The
> spot gate on the live path is **rail 19 (`spot_instrument`)** in `keel/execution/guards.py`...
> a gate built on this field would be dead code on every real path while reading as a defence.

So the first gate hit is real but inert; it is included here because it is the first thing an
adapter author would confront, not because it is what actually stops the class.

**2. The `OrderSpec` sum type.**
`packages/keel-broker-api/keel_broker_api/orders.py:92` — `MarketIOCByQuote | MarketIOCByBase |
LimitGTC | StopLimitGTC`, each sized in base or quote units against a BUY/SELL `Side`. There is no
vocabulary for leverage, margin, position-open/close, or stop-out anywhere in that sum type.
cTrader's model cannot be expressed through this port without lying about what the order does.
The specific mismatch: in keel, SELL means "dispose of a base-asset holding you have." In
cTrader, SELL means "open a short." Same two-letter enum value, opposite contract underneath it.

**3. `Broker.get_balances() -> list[Balance]`.**
`Balance(currency, available, total)` (`packages/keel-broker-api/keel_broker_api/results.py:18`,
`port.py:30`). cTrader has only a deposit-currency cash balance plus position P&L — there is no
base-asset holding, ever, per the instrument-semantics section above. An adapter would have
nothing truthful to report for the field this DTO exists to carry.

**4. Rail 17, withdrawal capability (`qabd`).**
`keel/execution/guards.py:644-651`, the rail's own comment:

> 17. Withdrawal capability — a COMPLIANCE rail, not an operational one (§65.4). Ayub's
> constructive-possession test (`qabd`) has a live condition attached: possession holds only
> while "there is nothing to prevent the buyer from taking physical possession whenever he
> desires". An asset we cannot withdraw is an asset we may not validly POSSESS — so acquiring more
> of it is the thing to stop.
> ENTRIES ONLY, exactly like rails 11/16: existing holdings are already ours, and forcing a sale
> to "fix" a withdrawal freeze would be strictly worse than holding through it. Fails CLOSED on
> None, like rails 12/13 — silence is not evidence of possession.

That "ENTRIES ONLY" line matters for how this rail is described. Rail 17 is gated
`if is_buy and not offline` (`guards.py:652`), and `withdrawal_capability` is one of exactly two
rails in `LIVE_STATE_RAILS` (`guards.py:167`) that `offline=True` skips outright — paper trading,
the mode a new adapter is normally exercised in first, never runs this check at all. The accurate
claim is: rail 17 vetoes every **LIVE BUY**, is **entries-only** by design, and **does not run in
paper**. Not "unconditionally, forever."

`withdrawals_enabled` is also not adapter-reported — a CFD having no underlying to withdraw is
true, but it is not what makes the input to this rail honest. `withdrawals_enabled` is the
**operator's own attestation**, read live on every intent by `_withdrawals_enabled`
(`keel/execution/executor.py:234-257`, whose docstring states "Read LIVE from the operator's
attestation on every intent -- never cached"), and set by `keel withdrawals attest`
(`keel/commands/withdrawals.py:56`). The rail does fail closed **by default**: with no attestation
on file, `_withdrawals_enabled` returns `None`, which produces `withdrawal_capability: UNKNOWN`
and vetoes the BUY. But that makes rail 17 an **operator-honesty gate, not an adapter-honesty
one** — nothing in the rail inspects the venue at all. An operator who ran
`keel withdrawals attest --enabled` would clear rail 17 against any venue, cTrader included,
because the attestation is a flat boolean with no venue awareness behind it. That is a real
weakness in how much this rail actually defends, not a hypothetical one, and worth stating plainly
rather than overclaiming rail 17 as a check the adapter itself cannot get past.

**5. Rail 19, spot instrument shape.**
`keel/execution/guards.py:750`:

```python
if parse_spot_product_id(intent.product_id) is None:
    violations.append(
        f"spot_instrument: {intent.product_id!r} is not a well-formed spot product id "
        f"(BASE-QUOTE, uppercase, exactly one hyphen). keel is spot-only: futures "
        f"(BASE-DDMMMYY-CDE), equities (an opaque 64-char hash) and any other instrument "
        f"shape are refused here regardless of what they settle in."
    )
```

This check does **not** fail cTrader. An adapter emitting ids shaped `EUR-USD` or `BTC-USD` — the
obvious choice, since cTrader's own symbol names are plain currency/asset pairs — passes
`parse_spot_product_id` cleanly: the grammar checks *shape*, not what backs the instrument, and a
CFD's id is indistinguishable in shape from a genuine spot pair. This is exactly the residual
`guards.py:725-736` documents in its own words: `BTC-PERP` — Coinbase International's actual
perpetual-futures format, not a hypothetical — "PASSES this grammar," because the grammar carries
no currency table and cannot tell a settlement-currency-shaped token from an instrument-suffix one.
`parse_spot_product_id` is also total — it returns `None` or a valid parse on any input and never
raises — so "fails closed" is not the right description for it either; there is no unknown state
to fail closed on.

The argument this document actually rests on is not the grammar check but its comment, at
`guards.py:741-742`:

> Spot-only is this agent's CHARTER, not an operator preference, so there is no config field here
> to widen (unlike rail 18's `settlement_currencies`).

This is the sentence the whole verdict rests on. Rail 19's grammar does not veto cTrader — an
adapter with sane id spelling clears it, same as it would for the `BTC-PERP` residual — but the
comment states outright that spot-only is not a knob keel could turn even to widen the grammar for
this case: a leveraged CFD account is excluded on charter grounds, independent of what the
id-shape check happens to catch today. Rail 17 is the rail that actually fails closed against
cTrader, on the live BUY path, by default; rail 19's role here is the charter statement, not the
check.

**6. Curation / `screen.py`.**
Even setting rails 17 and 19 aside, the obvious next question is whether the curation gate at
`keel/compliance/screen.py` independently rejects a CFD. Stated honestly, it does not reliably —
and this document should not claim a defence it cannot actually verify.

`AssetAttestation` (`screen.py:76-86`) is keyed on `asset`, a base-leg symbol — the schema has no
field representing an instrument *wrapper*. The attestation an operator would record for the
*underlying* of a cTrader BTC CFD is the same attestation keel already holds for spot BTC:
`sector=crypto`, `backing=native`, `pays_yield=False`. That attestation is **admitted** by
`screen_asset`, because swap cost and counterparty exposure are properties of the *contract*, not
of BTC, and `AssetAttestation` gives an operator no way to attest to the contract instead of the
asset. So the two failures that look like they should fire —

> "riba_yield: the asset carries a guaranteed/expected return for holding it, which is riba-like
> (§28.4); holding it is not a bare spot position" (`screen.py:226-230`)

and, on the branch a CFD's backing would actually reach — not the `'dayn'` branch, since a CFD is
neither `'ayn` nor `'dayn` nor `native` and so lands on the unknown-backing branch —

> "backing: {backing!r} is not one of ['ayn', 'dayn', 'native'] -- classify it explicitly rather
> than leaving it open (§65.5/§67.2)" (`screen.py:233-237`, secondarily the `'dayn'` branch at
> `screen.py:238-242` if an operator did attest the CFD's own contractual nature rather than BTC)

— only fire if an operator attests the CFD itself rather than reusing BTC's existing spot
attestation, and nothing in the schema forces that. `WAIVABLE_CRITERIA` is exactly `{"history"}`
(`screen.py:60`) — neither shariah criterion is escapable by exemption if it does fire — but
that is beside the point when the criteria are not reliably reached at all. **This is a real
modelling gap in `screen.py`, recorded here as an honest open item, not a defence this document
can claim.**

### A secondary, independent blocker — kept separate so it is not conflated with the charter one

Even for a venue that were charter-compatible, the broker port is **not yet on the live path**,
and this is worth stating clearly precisely because it is a different kind of blocker from
everything above — temporary, not structural.

`keel/commands/_common.py:137-156` (`_build_broker`) hardcodes construction of `CoinbaseClient`:

```python
def _build_broker(  # pragma: no cover -- exercised only against fakes
    config: Config, *, timeout: int | None = None
) -> Any:
    ...
    from keel.data.cb_client import CoinbaseClient
    ...
    return CoinbaseClient(transport)
```

`keel/execution/guards.py:127` sets `DEFAULT_VENUE = "coinbase"`. `keel/data/market_feed.py`
types its two entry points on `CoinbaseClient` specifically, not on the port
(`market_feed.py:20` the import, `:74` and `:120` the `backfill`/`poll_once` signatures).
`keel/agent.py` types the broker parameter as bare `Any` at every call site that carries it
(`agent.py:368,451,594,796,1199`). So today, adding *any* adapter — cTrader included, if the
charter question were somehow different — makes it discoverable and conformance-checkable, not
tradeable.

The existing precedent for this exact state is `packages/keel-broker-robinhood/README.md`, built
just prior to this document on a sibling branch:

> `keel/commands/_common.py` still constructs `CoinbaseClient` directly. The broker-port migration
> that would let the engine route orders through any registered `Broker` (Phase B) has not
> landed. Installing this package registers `robinhood` as a discoverable broker plugin ... and
> nothing more -- no command, rule, or rail currently constructs or calls a `RobinhoodAdapter`.
> This is deliberate: the adapter is built and tested ahead of the migration that will use it, not
> wired in early.

Robinhood is a spot venue — it passes the charter gates cTrader fails. Its adapter being unwired
is a scheduling fact about the migration, and will be lifted when that migration lands. **This is
not the reason cTrader is refused.** cTrader fails rail 17 by default and is barred by rail 19's
charter comment before the question of wiring is ever reached; even after the broker-port
migration ships, that does not move. (The curation screen is a separate, honest open item, not a
third gate this document can claim — see above.)

## What would have to be true for this to change

Not a matter of effort, and not a matter of the broker-port migration landing — that migration
fixes the secondary blocker above, not the charter one. Changing the verdict would require one of
two things:

**(a)** A cTrader-affiliated broker exposing genuine unleveraged spot custody with delivery and
withdrawal through the Open API. No message in the protocol currently supports this — leverage,
margin, and short symmetry are core fields on `ProtoOATrader`/`ProtoOAPosition`/`ProtoOASymbol`,
not optional extensions, and there is no custody or delivery concept anywhere in the model to
build a withdrawal path on top of. This is not a roadmap item anyone on keel's side can execute;
it would require Spotware or a licensee broker to add an account type and a set of messages that
do not exist today.

**(b)** keel abandoning spot-only. That is keel's charter, not a configuration default — rail 19's
own comment says there is no config field for it because there should not be one.

Note specifically that a `swapFree` account does not get to (a). It addresses the financing
charge — trading it for `rolloverCommission` — and touches none of the other three problems: the
account is still leveraged (`leverageInCents`/`maxLeverage` remain core fields), SELL still opens
a short rather than disposing of a holding, and there is still no custody or delivery concept to
withdraw against. `swapFree` is the answer to one question out of four, and not the question that
matters most here.

**(c) A market-data-only integration — not a third way to flip the verdict, but worth answering
directly rather than leaving it implicit.** This document calls cTrader's market data "genuinely
good" above; that praise otherwise reads as an unaddressed opening for "so why not just use it for
data?" Every gate the refusal rests on is **execution-side**: rail 17 gates `is_buy`; rail 19 gates
`OrderIntent.product_id` at order-intent time; `screen.py` gates admission of a *tradeable* asset;
`Broker.get_balances()` and `OrderSpec` are order/account concepts. A read-only, `accounts`-scope
integration using only `ProtoOAGetTrendbarsReq`/`ProtoOASubscribeSpotsReq` touches none of them —
and would not go through the `Broker` port at all: `keel/data/market_feed.py` types
`backfill`/`poll_once` directly on `CoinbaseClient` (`market_feed.py:20,74,120`, cited above), not
on any port interface, so a market-data source is architecturally a different kind of thing from a
`Broker` in this codebase already. `MarketFacts.product_id` does run rail 19's own grammar at
admission (`keel/compliance/screen.py:205`), so even a pure data feed's candidate ids would need to
be spot-shaped to be screenable at all — a `EUR-USD`-style cTrader symbol clears that trivially,
for the same reason it clears rail 19 above.

So the honest question is not "is this barred," it is "is this worth doing," and the answer
deserves a real argument rather than a dismissal. Against it: cTrader's marks are
**broker-quoted**, not exchange prints — a CFD price is a dealer's quote, not a settlement price,
and treating it as a reference feed imports that distinction silently. keel's live-traded universe
is Coinbase USD spot, so a cross-venue FX/CFD mark introduces basis against the venue actually
being traded, for assets keel holds no FX exposure to in the first place. The Python SDK is
unmaintained (this document's own finding, above) — a new integration would commit to the older,
less-supported side of a protocol whose maintainer has not shipped in roughly two years. And
OAuth2 plus a broker-affiliated account plus Spotware's manual app review is disproportionate
machinery to stand up for a reference feed keel does not currently need for any pair it trades.

**Conclusion: not worth it today, but it is not charter-barred the way execution is.** A
market-data-only cTrader integration would not trip rail 17, rail 19, or the curation screen,
because none of those gates see it — it fails on cost/benefit (unmaintained SDK, dealer-quoted
marks with no current use, disproportionate onboarding), not on the compliance grounds the rest of
this document rests on. Refusing cTrader as an execution venue is a charter position; declining it
as a market-data source today is an engineering judgment call, and a future need for cross-venue
FX reference data could reasonably revisit it without touching anything else in this document.

## What was deliberately NOT built

No `packages/keel-broker-ctrader/` package. No `ctrader-open-api` dependency added anywhere in
the workspace. No entry point registered under `keel.brokers`. No conformance test written against
a cTrader fake.

This is related to a decision this codebase made once before, though not the identical one. The
note is not in `guards.py` — `guards.py:746-747` only references it — it lives in
`docs/experiments/2026-08-05-coinbase-asset-class-feasibility.md` (around lines 644 and 767): R1
built, then deleted before merge, a *guard rail* consulting `BrokerCapabilities.asset_classes` —
dead code reading as a **defence**, because nothing on the live path ever calls `capabilities()`.
What this document declines to build is different in kind: not a guard rail, but an **adapter** —
dead code that would read as a **capability**. The two are related, not "the same," and the
distinction matters: `packages/keel-broker-robinhood` is a live counter-example in this same tree
— an unwired adapter built and tested deliberately ahead of the broker-port migration that will
use it (see "Not wired to the live path" above). keel holds no general rule against unwired
adapters; it holds a rule against dead code that misrepresents itself as a defence, or as a viable
capability, when it structurally cannot be either. A cTrader adapter would sit in the tree
implying cTrader is a live option pending only wiring, when the actual state is that rail 17 fails
it by default on the live BUY path and rail 19's charter comment forecloses it regardless of
wiring or grammar. Building it would misrepresent the project's own state to the next reader more
than it would advance anything.
