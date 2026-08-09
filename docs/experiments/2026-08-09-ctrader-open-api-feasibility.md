# cTrader Open API — can keel trade through it?

**Date:** 2026-08-09
**KB basis:** the spot-only, no-leverage, no-short charter that `keel/execution/guards.py` rail 19
and `keel/compliance/screen.py` already enforce structurally; no new KB citation is needed because
the finding here is architectural, not a fresh halal question.
**Status:** feasibility only. **No code changed, no adapter added, no dependency introduced.**
**Evidence:** documentation review, not a live probe. No cTrader account was opened, no
application registered, no connection made. Every claim below is sourced to a
`help.ctrader.com/open-api/*` page read on 2026-08-09, cited inline. Where the docs are silent,
this document says "docs silent" rather than guessing. Unlike the Coinbase asset-class study
(`docs/experiments/2026-08-05-coinbase-asset-class-feasibility.md`), there is no committed,
re-runnable probe script here — there is nothing to probe against without an account, and none
was opened for this review.

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
| Rail 17 (`withdrawal_capability`) | `withdrawals_enabled` on entry | a CFD has no underlying to ever withdraw | `None`/`False` — fails closed, vetoes every BUY forever |
| Rail 19 (`spot_instrument`) | product id must be a spot shape | irrelevant — the underlying contract is leveraged regardless of id shape | the charter comment says there is no config field to widen this |
| Curation screen (`screen.py`) | `pays_yield`, `backing` | swap/financing is a per-symbol default; a CFD is neither `ayn` nor a bare spot claim | `riba_yield` and/or `dayn`-shaped failures |

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
`github.com/spotware/OpenApiPy`, built on Twisted. Latest release is **0.9.3, dated 2024-08-06**;
the last push to the GitHub repo was 2024-08-07; the repo carries 188 stars and 12 open issues and
is not archived. **No release or commit in roughly two years** as of 2026-08-09 — this reads as a
maintenance-mode project, not an actively developed one. It is TCP-only; the WebSocket transport
is documented as .NET-SDK-only, so a Python integration would be building against the older, less
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
`ASSET_CLASSES: frozenset[str] = frozenset({"spot", "futures", "equity"})`, and
`__post_init__` raises `ValueError` on anything outside that set. There is no `margin`, `cfd`, or
`short` in the vocabulary — an honest cTrader declaration cannot be constructed at all, because
none of the three permitted words describes a leveraged, short-permitting CFD account. That said,
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
`keel/execution/guards.py:644-666`:

> Withdrawal capability — a COMPLIANCE rail, not an operational one (§65.4). Ayub's
> constructive-possession test (`qabd`) has a live condition attached: possession holds only
> while "there is nothing to prevent the buyer from taking physical possession whenever he
> desires". An asset we cannot withdraw is an asset we may not validly POSSESS — so acquiring more
> of it is the thing to stop.

A CFD is a contract with the broker; there is no underlying asset the account can ever take
delivery of or withdraw — cTrader's model has no delivery or custody concept anywhere in it. An
honest adapter reports `withdrawals_enabled=False`, or `None` if it declines to guess. The rail
fails closed on both: `None` produces `withdrawal_capability: UNKNOWN` and `False` produces
`withdrawal_capability: withdrawals are suspended/restricted`. Either way, every BUY is vetoed,
unconditionally, forever.

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

But the sharper point is not the id-shape check, it is what the rail's own comment says about why
there is no way around it, at `guards.py:741-742`:

> Spot-only is this agent's CHARTER, not an operator preference, so there is no config field here
> to widen (unlike rail 18's `settlement_currencies`).

This is the sentence the whole verdict rests on. Rail 17 and rail 19 both fail closed against
cTrader independently, but rail 19's comment states outright that this is not a knob anyone can
turn — it is not a defence built for lack of time, it is a statement of what keel is.

**6. Curation / `screen.py`.**
Even setting rails 17 and 19 aside — imagine an id-shape trick or a future instrument model that
satisfies both — the curation gate at `keel/compliance/screen.py` fails a CFD on two independent,
unwaivable criteria. `attestation.pays_yield` triggers `riba_yield`:

> "riba_yield: the asset carries a guaranteed/expected return for holding it, which is riba-like
> (§28.4); holding it is not a bare spot position" (`screen.py:226-230`)

Every cTrader symbol charges or pays swap by default (`swapLong`/`swapShort`), and even the
swap-free path substitutes `rolloverCommission` — a mandatory daily charge attached to holding the
position, which is the shape this criterion exists to catch. Separately, `backing == "dayn"`
triggers:

> "backing: 'dayn' -- a debt claim on an issuer, not an owned thing. Trading a pure claim is a
> different contract under different rules (§65.5/§67.2), so it is not admitted by this policy"
> (`screen.py:238-242`)

A CFD is a contract with a counterparty broker, not a claim on an owned thing and not a bare spot
position — a CFD carrying daily swap, honestly attested, fails both criteria. `WAIVABLE_CRITERIA`
contains exactly `{"history"}` (`screen.py`), so neither is escapable by exemption.

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
not the reason cTrader is refused.** cTrader fails on rails 17/19 and the curation screen before
the question of wiring is ever reached; even after the broker-port migration ships, those three
gates do not move.

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

## What was deliberately NOT built

No `packages/keel-broker-ctrader/` package. No `ctrader-open-api` dependency added anywhere in
the workspace. No entry point registered under `keel.brokers`. No conformance test written against
a cTrader fake. The reason is the same one `guards.py` itself records having learned once already,
at rail 19's "what was deliberately NOT shipped" note describing R1's now-removed
venue-declaration check: an adapter for a venue every rail vetoes would be dead code that reads
as capability. It would sit in the tree implying cTrader is a live option pending only wiring,
when the actual state is that three independent gates — one of them explicitly charter-locked,
with no knob to turn — refuse it regardless of wiring. Building it would misrepresent the
project's own state to the next reader more than it would advance anything.
