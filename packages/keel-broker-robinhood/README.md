# keel-broker-robinhood

A `Broker` adapter for keel's `Broker` port, implemented against the Robinhood Crypto Trading
API v2.

## What works

| Capability   | Detail                                                                       |
| ------------ | ---------------------------------------------------------------------------- |
| Balances     | Per-holding `Balance` plus one for the account's `buying_power`.              |
| Order status | `get_order` normalizes a Robinhood order object to `OrderStatus`.            |
| Cancel       | `cancel_order` confirms from the venue's returned order, with one re-poll.    |
| Fee summary  | Rates from `fee_tier_status.fee_ratio`, `volume_usd` from `thirty_day_volume`, `fees_usd` summed from 30 days of order history. |
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
Whether `est_total_cost` includes the fee is **not documented**. All three self-consistent
readings (`total == notional`, `total == notional + fee`, `total == notional - fee`) recover the
same fee-exclusive notional, which is what `Preview.est_quote_size` is defined to carry, and
`Preview.detail["cost_basis"]` reports which one this response satisfied. A total satisfying none
of them is priced from the venue's number as sent *and* reported through `Preview.errors`.

A live ask-side row settles the reading empirically -- `64975.78 * 0.001 + 0.61726991 ==
65.59304991`, so the total is **fee-inclusive** there, and assigning it straight into
`est_quote_size` would have double-counted the fee at the confirm gate. The relation is still
re-derived per response rather than hardcoded, because:

**`est_total_cost` is sent on the ask side only.** A `side=bid` row carries `bid`, `quantity`,
`fee_ratio` and `est_fee` and no total at all. That is a complete answer, not a degraded one: a
sell prices from `bid * quantity` with the venue's own `est_fee` beside it, `errors` stays empty,
and `cost_basis` reads `price_x_quantity`.

**None of that makes the preview a broker quote.** `/estimated_price/` prices a *quantity*: it
does not validate the order, check buying power, check the account's own size bounds, or reserve
anything, so an order it prices happily can still be rejected the instant it is placed.
`supports_native_preview` stays `False` and `synthetic` stays `True`.

### How `fees_usd` is built, and what it can still get wrong

`get_fee_summary().fees_usd` used to be a hardcoded `Decimal("0")`. That was not a cosmetic gap
(#197). `FeeSummary`'s own docstring names subscription-lapse detection as its consumer, and the
contradiction it looks for is *a fee charged while the user claims a fee-free allowance*. A
constant zero can never contradict anything, so against this venue the check did not fail loudly
-- it **passed, every time, for every account**. A rail that always passes is worse than an
absent one, because it reads as coverage.

The v2 API still publishes no account-level fees-paid total, so the number is built the only way
the API allows: `GET /api/v2/crypto/trading/orders/`, filtered to the same trailing 30 days
`thirty_day_volume` covers, with each order's `fee_charged` summed.

Four decisions in that sentence are load-bearing:

**The window filter is `updated_at_start`, not `created_at_start`.** Both are documented and
either would compile. A fee is charged when an execution happens, and an execution necessarily
bumps `updated_at` -- so the `updated_at_start` result set is a *superset* of the orders carrying
an in-window fee and can never omit one. `created_at_start` has no such property: a `StopLimitGTC`
resting for forty days and filling this morning was created outside the window and charged its fee
inside it. keel rests GTC brackets by design, so that is this engine's normal case, not a corner.
Under-reporting is the false negative #197 exists to close, so between two imperfect filters the
correct one is the one that cannot under-report.

**No `state` filter is sent, and every state is counted.** `state` is the obvious narrowing and it
is a trap: an order that partially fills and is then cancelled ends `canceled` while having been
charged a real fee on the part that executed, and filtering to `filled` would drop it. No filter is
needed anyway -- `fee_charged` is documented as the fee charged *based on executed fills*, so the
field is already its own state filter, reading zero on an order that never traded.

**`estimated_fee_remaining` is never read.** The neighbouring v2 field is the fee that *will* be
charged on an order's unfilled remainder -- explicitly conditional, explicitly an estimate.
`fees_usd` is consumed as an observation, and an estimate cannot honestly contradict anything.

**An incomplete sweep raises rather than returning a partial sum.** `FeeSummary` has no field to
mark a total as partial (`fees_usd` is a bare `Decimal`), so a truncated sum is indistinguishable
from a complete one and would be read as an observation -- the same always-passing false negative
in a new costume. `RobinhoodTransport._paginate` already raises past `_MAX_PAGES`, and
`get_fee_summary` does not catch it. This inverts the rule `_account` and `cancel_order` follow
("a raise on the way out of a position can trap it"), and safely: `get_fee_summary` is a
reconciliation read, never a step in an unwind.

Cost is **1 + N requests**, N being the history pages in the window: one `GET /accounts/` plus the
page walk, capped at 20. Worst case 21 per call against a 100 req/min limit with no backoff in the
transport; realistically 2 for an account trading a handful of times a month. The server-side
window filter is what stops that growing with the account's total age forever.

Two things this still cannot get exactly right, both stated so nobody reads more into the number
than it carries:

- **An order straddling the window edge contributes its whole fee.** `fee_charged` is an
  *order*-level total, and v2's `executions[]` rows carry only `effective_price`, `quantity` and
  `timestamp` -- no per-execution fee -- so a fee cannot be split at the boundary even in
  principle. This over-counts, never under-counts, which is the survivable direction: an
  over-count points lapse detection at a fee that was genuinely charged, just slightly earlier
  than the window claims; an under-count hides one.
- **The window is not provably identical to the venue's own.** `thirty_day_volume`'s boundary is
  undocumented (it may be calendar-day aligned, it may exclude today) while this window is cut
  from the local clock. The two match in length and intent, not necessarily to the second. Treat
  `fees_usd` and `volume_usd` as comparable magnitudes over the same nominal window; do not divide
  one by the other to derive an exact effective rate.

⚠️ **No order object, and no response from the orders LIST endpoint, has ever been observed
live** -- see "No sandbox" below. Every field name above is read from the documentation alone.

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

### This venue is not internally consistent about quoting money

Some endpoints send money as unquoted JSON numbers and others send the same kinds of value as
quoted strings, and `accounts` does **both in the same object**:

| | fields |
| --- | --- |
| unquoted numbers | `estimated_price.{ask,bid,quantity,fee_ratio,est_fee,est_total_cost}`, `accounts.fee_tier_status.*`, `holdings.{total_quantity,quantity_available_for_trading}` |
| quoted strings | `accounts.buying_power`, `trading_pairs.{asset_increment,quote_increment,max_order_size,min_order_amount}`, `best_bid_ask.{bid,ask}` |

There is therefore no venue-wide rule to code against and no field that may be assumed to be one
form or the other. Two things together make every read safe, and **both** are required:
`json.loads(..., parse_float=Decimal)` in the transport, so an unquoted number never passes
through a binary `float`; and `Decimal(str(value))` in the adapter, which is exact for a `str` and
a round-trip no-op for a `Decimal`. Do not "simplify" either into `Decimal(value)`, and do not add
an `isinstance` branch — there is nothing stable to branch on. The fixtures mirror the venue field
for field, mixed quoting included, so the suite exercises both paths.

### The minimum order size is published, but only on some pairs

`GET /api/v2/crypto/trading/trading_pairs/` publishes `asset_increment`, `quote_increment`,
`max_order_size` -- and `min_order_amount` on **63 of the 89 pairs**, including BTC-USD (`0.1`)
and ETH-USD. The other 26 pairs omit the key entirely. `min_order_size` does not exist on any pair.

⚠️ **This section previously said the venue publishes no minimum of any kind, and that was
false.** #217 F3 reached it from a probe run, and #218 deleted `min_order_amount` from
`tests/fixtures/rh_trading_pairs.json` on the strength of it. The probe's `shape_of` reduced a
list to its FIRST element, and `results[0]` is BILL-USD -- one of the 26 pairs that genuinely lack
the field. A run across all four cursor pages was still a run that read one row. #230 fixed the
probe to merge every element and restored the field.

The consequence for the pre-flight sizing check proposed in #198: increment rounding, an upper
bound **and** a lower bound can all be validated locally against this endpoint for the assets keel
trades. The lower bound must be read as optional per pair -- absent means "the venue states none
for this pair", and an undersized order there is still discoverable only as a rejection at
placement.

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
responses.

⚠️ **Read that sentence with #230 in mind.** Until then the probe summarised a list by its FIRST
element, so "matches observed responses" meant "matches `results[0]`" -- which is how a field on
63 of 89 trading pairs was declared non-existent and deleted. The probe now merges every element
of a list and marks a partially present key `key (63/89)`, so the claim means what it says; but a
probe run still only corroborates what the account's own data exercises, and a fixture is only as
corroborated as its most recent run.

⚠️ **And read it with #413 in mind too: the probe compares SHAPES, never values.** It matched
`rh_best_bid_ask.json` on every run while that fixture carried `bid` `65380.00` / `ask`
`65480.00` -- a tidy 15 bps spread BTC-USD does not produce. The venue returns `bid` ABOVE `ask`
on its tightest pairs (BTC-USD and DOGE-USD on every sample taken 2026-08-19, ETH-USD on two of
three; XLM-USD and ADA-USD never), because the two legs are sampled independently and then
stamped with a single `timestamp`. The fixture now carries an observed crossed BTC-USD row, and
`rh_best_bid_ask_uncrossed.json` an observed XLM-USD one, because either alone would state a rule
the endpoint does not follow. That old fixture also invented `"next": null, "previous": null`,
which this endpoint does not send. Anything reading this endpoint must tolerate a non-positive
spread -- see `transport.get_best_bid_ask` and #413. **Two of the three order fixtures are now observed.** `scripts/robinhood_order_probe.py`
placed one real BTC-USD limit buy on 2026-08-20 (#412) -- 0.0001 BTC at $36,352.78, 50% below
the bid so that it could not fill -- polled it, and cancelled it. `rh_order_open.json` and
`rh_order_canceled.json` are that order's own responses, with `account_number` replaced by the
repository's `AB1234567890` placeholder and nothing else altered. The run corrected four claims
the documentation-derived fixtures had made:

| field | fixtures claimed | venue actually sends |
| --- | --- | --- |
| `filled_asset_quantity` | unquoted number | QUOTED string, padded to 18dp |
| `limit_order_config.asset_quantity` | unquoted number | QUOTED string, padded to 18dp |
| `limit_order_config.limit_price` | unquoted number | QUOTED string, padded to 18dp |
| `limit_order_config.time_in_force` | `"gtc"` | **absent** -- accepted on the way in, never echoed back |

`fee_charged` and `estimated_fee_remaining` are UNQUOTED numbers, which is what the fixtures
already said, so #197's fee sweep reads a real value rather than the silent `Decimal("0")` #412
warned about.

**`rh_order_filled.json` remains doc-derived** and is marked as such wherever it is used: the
probe prices its order 50% below the bid precisely so it cannot fill, so `average_price`, the
`executions[]` rows and a non-zero `fee_charged` have still never been observed. Its field
quoting follows the conventions the open/cancelled observations established, which is an
inference from the same venue rather than an observation of a filled order.

The script gained a sixth probe with #197, against the orders LIST endpoint and
`rh_orders.json`. It is a GET, so it stays inside the read-only guarantee, and it does verify the
list endpoint's path, that the signature is accepted, and the pagination envelope. **It verifies
the order OBJECT's field names only if the account happens to have order history.** On an account
that has never traded crypto here, `results` comes back empty and `compare_shapes` skips a list
whose rows are `<empty>` -- so the report prints a shape match it has not earned. An operator
reading a clean `orders` line must check whether any row came back before treating it as
corroboration. `fee_charged`'s JSON quoting -- quoted string or unquoted number, unknown at this
venue and undecided by its own docs -- is exactly what one real row would settle.

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

1. **`fees_usd` is now summed from order history (#197), but the sum has never been checked
   against a real order.** The always-zero constant is gone, so subscription-lapse detection is
   no longer inert-and-always-passing here -- that item is closed. What replaces it is narrower
   and must not be skipped: the sum is built from field names (`fee_charged`, `updated_at`, the
   list envelope) that no live response has ever corroborated, because observing an order object
   requires placing a real order. If `fee_charged` is spelled differently at the venue, every
   row parses to `None` and the total silently returns to zero -- the original failure, arrived
   at from a different direction. Run `scripts/robinhood_smoke.py` against an account with order
   history before trusting the number, and confirm the `orders` probe actually returned a row.

2. **A placement retry is only safe when the caller passes an `idempotency_key`.** (#409,
   resolved at the port.) Without one the `client_order_id` is minted per ATTEMPT, so a caller
   that retries after a timeout -- exactly when the first request may already have reached the
   venue -- places a **second live order**, because Robinhood has nothing to match the retry
   against. That remains the DEFAULT, and deliberately: an id derived from the spec would
   silently collapse two orders a strategy genuinely meant to place twice. What changed is that
   the port now carries the distinction, so the caller can state which it means:
   `place_order(spec, idempotency_key=...)` resolves every attempt under that key to one venue
   id via `keel_broker_api.port.resolve_client_order_id`. Whatever calls `place_order` in
   Phase B must pass a key before it retries placement.

3. **`Preview.synthetic` is invisible at the confirm gate on today's CLI path.**
   `keel/cli.py`'s `_interactive_confirm` takes a raw `dict` and renders it by iterating
   `.items()` -- it has no `Preview` field to read and nowhere to display `synthetic`. Every
   preview this adapter produces is `synthetic=True` (there is no native preview endpoint here),
   and `Preview`'s own docstring requires that "approving an estimate must never look identical
   to approving a broker's own quote". Until that CLI path is migrated to the port's `Preview`
   type, approving a Robinhood estimate looks exactly like approving a Coinbase quote. The same
   applies to `Preview.errors`, which this adapter populates whenever it could not price an
   order -- an unpriced preview currently renders as a normal one.

4. **Rate limiting is handled for reads only.** Robinhood allows 100 requests/minute sustained
   (300 burst). Since #411 the transport retries a GET on 429 and 5xx with exponential backoff,
   honouring `Retry-After` where the venue sends one and clamping it so a server-controlled
   header cannot park a trading loop. A status that survives every attempt still raises, so a
   persistent quota problem stays visible rather than becoming a hang.

   ⚠️ **A POST is never retried**, and that is deliberate rather than unfinished. A 429 or 5xx on
   `create_order` is an UNKNOWN outcome, not a refusal -- the venue may have accepted the order
   before the response was lost -- and the transport cannot tell whether two attempts would carry
   the same `client_order_id`, because the body arrives already built and a key-derived id (#409)
   looks exactly like a fresh uuid4 from there. A placement retry is safe only where the
   `idempotency_key` is known, which is above the adapter, not inside the transport.

   Backoff is not throttling, and the aggregate rate is still unbounded. Per-call account caching
   keeps each public adapter method to a single `GET /accounts/`, but `get_fee_summary` is not a
   single-request method: its order-history sweep is 1 + N requests, bounded at 21 by
   `_MAX_PAGES`. Whatever calls it in Phase B should call it on a schedule, not per order.

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
