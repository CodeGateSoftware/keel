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

### No sandbox

Robinhood ships no test environment for this API. Every test in this repository's
`tests/broker_robinhood/` suite runs against a canned, in-memory `Transport`. There is no way to
exercise this adapter end to end without placing a real order with real money, which is why the
conformance suite against the fake transport is the only signal this package has before a human
runs it live.

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
