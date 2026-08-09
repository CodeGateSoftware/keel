# keel-broker-robinhood

A `Broker` adapter for keel's `Broker` port, implemented against the Robinhood Crypto Trading
API v2.

## What works

| Capability   | Detail                                                                       |
| ------------ | ---------------------------------------------------------------------------- |
| Balances     | Per-holding `Balance` plus one for the account's `buying_power`.              |
| Order status | `get_order` normalizes a Robinhood order object to `OrderStatus`.            |
| Cancel       | `cancel_order` confirms from the venue's returned order, with one re-poll.    |
| Fee summary  | Rates from `fee_tier_status.fee_ratio`, `volume_usd` from `thirty_day_volume`. |
| Preview      | Synthetic only (`synthetic=True`) -- there is no native preview endpoint.     |

Three order kinds are supported: `MarketIOCByBase` (market, sized in the asset), `LimitGTC`
(resting limit), and `StopLimitGTC` (resting stop-limit). The two resting kinds carry
`time_in_force: "gtc"` because that is the only value Robinhood documents; a market order carries
no `time_in_force` field at all, which is why `market_ioc_base` is declared despite the port's
name saying IOC. That is a naming impedance, not a capability lie -- a market order is immediate
by construction, and there is no resting-market variant here to confuse it with.

### What a preview reads, and why it is still synthetic

A market preview prices off `GET /api/v2/crypto/trading/estimated_price/`. The row that endpoint
actually returns was confirmed against a real credential in #217:

```
{'symbol', 'side', 'quantity', 'timestamp', 'fee_ratio', 'est_fee', 'ask', 'est_total_cost'}
```

There is **no `price` field**. The unit price is in the column named after the side that was asked
for -- `ask` for a buy, `bid` for a sell. The adapter read `price` until #217, and the consequence
was total: every market preview against the real venue came back `est_quote_size = 0.000` with
`errors` populated, so confirm mode was unusable on this venue. It is read from the requested
side's column only, with no fallback to the other one: pricing a sell off an `ask` overstates the
proceeds of an exit, and a row that does not carry the requested side is treated as unpriced.

The adapter also reads the venue's own `est_fee` instead of multiplying by the account's fee tier,
and reconciles `est_total_cost` against `price * quantity` and `est_fee` on every response.
Whether `est_total_cost` includes the fee is **not documented and not assumed**. All three
self-consistent readings (`total == notional`, `total == notional + fee`, `total == notional -
fee`) recover the same fee-exclusive notional, which is what `Preview.est_quote_size` is defined
to carry, and `Preview.detail["cost_basis"]` reports which one this response satisfied. A total
satisfying none of them is priced from the venue's number as sent *and* reported through
`Preview.errors`.

**None of that makes the preview a broker quote.** `/estimated_price/` prices a *quantity*: it
does not validate the order, check buying power, check the account's own size bounds, or reserve
anything, so an order it prices happily can still be rejected the instant it is placed.
`supports_native_preview` stays `False` and `synthetic` stays `True`.

## What does NOT work

### No candles

This API has no OHLC or historical-candles endpoint at all, under any path. `get_candles` raises
`ValueError` for every granularity, unconditionally. Robinhood is an **execution venue only**
in this codebase -- candles for any rule that runs against a Robinhood-listed product must come
from somewhere else (e.g. Coinbase market data for the same pair, if the pair trades on both).

### No quote-sized market orders

Robinhood's `market_order_config` accepts only `asset_quantity`. There is no way to place a
market order sized in USD on this API. keel places entries as `MarketIOCByQuote` ("spend $N") --
so **this adapter cannot open positions under keel's current entry model.** It can size exits
(`MarketIOCByBase`), and it can place resting take-profit limits and protective stop-limits.
Synthesizing a quote-sized market order by dividing an estimated price by the requested spend is
deliberately not done anywhere in this package: it would substitute a different sizing basis --
an estimate taken moments before placement, instead of the size the caller actually asked for --
on the live-money path, and it would do so silently. `translate.to_order_body` raises
`UnsupportedOrder` for `MarketIOCByQuote` with this reasoning in the message, as a second gate
behind the adapter's capability declaration.

### No published minimum order size

`GET /api/v2/crypto/trading/trading_pairs/` publishes `asset_increment`, `quote_increment` and
`max_order_size`, and **no minimum of any kind** -- neither `min_order_amount` nor
`min_order_size`. This was confirmed live across four cursor pages in #217; the fixture had
invented `min_order_amount`, and `transport.get_trading_pairs`' docstring named it as an input.

The consequence is for the pre-flight sizing check proposed in #198: increment rounding and an
upper bound can be validated locally against this endpoint, and a **lower** bound cannot be
validated at all, because the venue never states one. An undersized order is discoverable only as
a rejection at placement. Anything designing that check must not assume a minimum is available
here.

### No sandbox

Robinhood ships no test environment for this API. Every test in this repository's
`tests/broker_robinhood/` suite runs against a canned, in-memory `Transport`. There is no way to
exercise this adapter end to end without placing a real order with real money, which is why the
conformance suite against the fake transport is the only signal this package has before a human
runs it live.

`scripts/robinhood_smoke.py` narrows that gap without placing anything: it is a read-only,
GET-only probe that compares each endpoint's live shape against the committed fixture. After the
first run of it (#217), the five READ fixtures -- `rh_accounts.json`, `rh_holdings.json`,
`rh_trading_pairs.json`, `rh_best_bid_ask.json`, `rh_estimated_price.json` -- match observed
responses. **The three order fixtures (`rh_order_open.json`, `rh_order_filled.json`,
`rh_order_canceled.json`) remain unverified against the venue**, because observing an order
object requires placing a real order, which that script refuses by construction. Their field
names are still read from the documentation alone, and `place_order` / `get_order` /
`cancel_order` all depend on them.

### `fees_usd` is always zero

`get_fee_summary().fees_usd` is hardcoded to `Decimal("0")`. The API exposes a fee *rate*
(`fee_tier_status.fee_ratio`) and a trailing volume figure, but no account-level total of fees
actually paid. keel's subscription-lapse detection reads `fees_usd` to notice a fee charged while
the user claims a fee-free allowance; against this venue that check cannot fire, and lapse
detection here has to fall back on the venue subscription attestation alone.

## Not wired to the live path

`keel/commands/_common.py` still constructs `CoinbaseClient` directly. The broker-port migration
that would let the engine route orders through any registered `Broker` (Phase B) has not landed.
Installing this package registers `robinhood` as a discoverable broker plugin (see the
`keel.brokers` entry point in `pyproject.toml`) and nothing more -- no command, rule, or rail
currently constructs or calls a `RobinhoodAdapter`. This is deliberate: the adapter is built and
tested ahead of the migration that will use it, not wired in early.

### Must fix BEFORE wiring this to the live path

The gaps above are capability limits -- things this venue cannot do, which the adapter refuses
honestly. The list below is different: these are places where wiring this adapter up **as it
stands** would degrade a safety property keel already has. Phase B must trip over this section.

1. **`fees_usd` is always zero, so subscription-lapse detection is inert AND always-passing
   against this venue.** This is not merely "a missing number". `FeeSummary.fees_usd` is the
   field lapse detection reads to notice a fee charged while the user claims a fee-free
   allowance. A constant zero can never contradict the claim, so the check does not fail
   loudly -- it *passes*, every time, for every account. Anything consuming a Robinhood
   `FeeSummary` must treat `fees_usd` as "not reported", never as "no fees were charged", and
   the migration must decide whether an always-passing check is acceptable or whether the venue
   should be excluded from that check by name. Closing it properly means paging order history
   and summing per-order `fee_charged`, which needs its own rate-limit design.

2. **A fresh `client_order_id` per `place_order` call means no retry is ever deduplicated.**
   The uuid is minted per ATTEMPT, so a caller that retries after a timeout -- exactly when the
   first request may already have reached the venue -- places a **second live order**. Robinhood
   has nothing to match the retry against, because the id differs. The current behaviour is the
   right default for the opposite hazard (an id minted per spec would silently collapse two
   orders a strategy genuinely meant to place twice), and the port has no "retry of" concept to
   disambiguate the two. So this is a documented tradeoff, not a solved problem: whatever calls
   `place_order` in Phase B must not retry placement blindly.

3. **`Preview.synthetic` is invisible at the confirm gate on today's CLI path.**
   `keel/cli.py`'s `_interactive_confirm` takes a raw `dict` and renders it by iterating
   `.items()` -- it has no `Preview` field to read and nowhere to display `synthetic`. Every
   preview this adapter produces is `synthetic=True` (there is no native preview endpoint here),
   and `Preview`'s own docstring requires that "approving an estimate must never look identical
   to approving a broker's own quote". Until that CLI path is migrated to the port's `Preview`
   type, approving a Robinhood estimate looks exactly like approving a Coinbase quote. The same
   applies to `Preview.errors`, which this adapter populates whenever it could not price an
   order -- an unpriced preview currently renders as a normal one.

4. **No rate limiting or backoff.** Robinhood allows 100 requests/minute sustained (300 burst)
   and this transport does not throttle or retry. Per-call account caching keeps each public
   adapter method to a single `GET /accounts/`, but nothing bounds the engine's aggregate rate.

5. **No candle source is composed.** Point 1 of "What does NOT work" means this adapter cannot
   be a venue's sole broker; Phase B has to decide how an engine pairs an execution venue that
   serves no bars with a separate market-data source.

## Credentials

Robinhood authenticates with an Ed25519 keypair, not an API secret. Robinhood's API credential
page takes the base64-encoded **public** key; the base64-encoded 32-byte private seed stays
local and is never sent anywhere except into the per-request signature.

Follow this repository's existing secret convention: a git-ignored `.env` at the repo root, read
via `dotenv_values` (`keel_core.config.load_secrets` does this today for `CDP_API_KEY` /
`CDP_API_SECRET`). This adapter documents two names for that same file -- `ROBINHOOD_API_KEY`
and `ROBINHOOD_PRIVATE_KEY` -- but **`load_secrets` does not read them yet.** Nothing in
`keel_core` wires them today; they are passed straight to
`RobinhoodTransport(api_key=..., private_key_b64=...)` by whatever code eventually constructs
one.

Generating a keypair with `pynacl`:

```python
import base64
import nacl.signing

signing_key = nacl.signing.SigningKey.generate()
private_key_b64 = base64.b64encode(bytes(signing_key)).decode()
public_key_b64 = base64.b64encode(bytes(signing_key.verify_key)).decode()

print("ROBINHOOD_PRIVATE_KEY=", private_key_b64)   # keep local, put in .env
print("public key for Robinhood's API credential page:", public_key_b64)
```

## Terms of service

Only the official, documented Robinhood Crypto Trading API (`https://trading.robinhood.com`,
per `https://docs.robinhood.com/crypto/trading/`) is used anywhere in this package. No
reverse-engineered or undocumented endpoint, and no equity or options endpoint, is touched --
ever. Robinhood publishes no public API for equities; anything claiming to be one is unofficial
and permanently out of scope for this package.
