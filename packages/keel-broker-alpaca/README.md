# keel-broker-alpaca

A `Broker` adapter for keel's broker port, implemented against Alpaca's publicly
documented Trading and Market Data APIs (https://docs.alpaca.markets/).

**Not affiliated with, endorsed by, or sponsored by Alpaca.** This is an original
implementation of keel's port against the venue's public API — no Alpaca SDK, no code
from any third-party Alpaca adapter. "Alpaca" appears here solely to identify what this
package talks to.

US equities, cash account, long-only, regular session. Paper and live are separate
hosts selected by an explicit `endpoint` choice — `transport.TRADING_HOSTS` is the only
construction path to a trading host (no constructor parameter accepts a host URL), so
there is no configuration path from a paper credential to
`https://api.alpaca.markets`, by construction.

## Cash-account posture (#372)

The cash half of the scope is **enforced, not just declared**. Alpaca has no
`account_type` field; `/v2/account`'s `multiplier` is the venue's account margin
classification (`1` cash-equivalent — buying power equals cash, shorts refused; `2` reg T
margin; `4` PDT day-trading margin), and the venue's *default* for any account over
$2,000 of equity — a $100k paper account included — is margin. Every broker build calls
`AlpacaAdapter.verify_cash_account()`: multiplier 1 passes, anything else raises
`CashAccountRequired` (margin borrowing is riba; the refusal names the PDT $25k
margin-account threshold the posture sidesteps and the fix — set the account's max margin
multiplier to 1), and a classification that cannot be read at all raises the same
fail-closed refusal, cause chained. The declared half is
`BrokerCapabilities.cash_only=True`, rendered as "cash only" in `keel brokers list`. The
operator-facing walk (including the T+1 settlement and opt-out obligations) is the
runbook's "Account posture" and "T+1 settlement" sections.

## What works

| Capability    | Detail                                                                       |
| ------------- | ---------------------------------------------------------------------------- |
| Balances      | USD row from the account (`available` = buying power clamped at cash, surfacing the T+1 settlement gap), one row per long position (`available` from `qty_available`). |
| Candles       | Split-adjusted bars, `15Min`/`1Hour`/`1Day` mapped onto keel's `Granularity`, paginated to the end of the window. Data tier (IEX/SIP) declared per request. |
| Orders        | All four port kinds: notional market, fractional-qty market, GTC limit, GTC stop-limit. `extended_hours: false` pinned on every body. |
| Preview       | Synthetic only (`synthetic=True`) — no preview endpoint exists. Prices off the latest quote's crossed side (ask for buys, bid for sells), surfaces `best_bid`/`best_ask`, and computes sell-side regulatory fees. |
| Order status  | `get_order` maps Alpaca's status enum to the port's vocabulary; unknown statuses stay `PENDING`. |
| Cancel        | 204 from `DELETE /v2/orders/{id}` is the venue confirmation; 404/422 and any transport failure answer `False`. |
| Session       | `is_market_open()` reads the venue's clock (`/v2/clock`) — no local calendar. |
| Posture       | `verify_cash_account()` at broker build: `/v2/account`'s `multiplier` must be the cash classification (1); margin (2/4) and unreadable answers refuse, fail-closed (#372). |
| Rate limits   | 429 retried with `Retry-After` when sent, exponential backoff otherwise, bounded attempt budget (FR-11). |

## Fees (FR-7)

Commission is $0. Sells carry regulatory pass-throughs, modelled in `fees.py` with the
rates as provenance-commented constants:

- **SEC Section 31**: $22.90 per $1,000,000 of sale proceeds — the figure Alpaca's own
  regulatory-fees page still publishes. The SEC's advisory 2026-2 rate ($20.60 per $1M)
  has been in force since 2026-04-04, so the venue's page is the stale side; the model
  deliberately tracks what the venue itself charges, which over-states the statutory
  rate by ~$0.02 per $10k (conservative for a sell preview's proceeds), and that delta
  is the re-measurement trigger for when Alpaca's page updates.
- **FINRA TAF**: $0.000166 per share, capped at $8.30 per trade — the cap is on
  Alpaca's page; the per-share rate is FINRA Schedule A §4(b)(7), in force since
  2021-01-01.

CAT (buys and sells, sub-cent per trade) is a documented omission. A fee summary is NOT
offered: `supports_fee_summary` is `false` because Alpaca's Trading API publishes no fee
tiers, no fees-paid total, and no volume window — the three things a `FeeSummary` would
assert. Fabricating zeros would read as coverage (the #197 lesson).

## Declared capability gaps

- **Bracket/OCO and stop-market are not declared.** The port's `OrderSpec` has no
  bracket concept and no stop-market kind; keel's stop-loss + take-profit exit legs ride
  as the separate `StopLimitGTC`/`LimitGTC` orders the port already models. This adapter
  does not invent venue-side order kinds the engine cannot ask for.
- **No fee summary** (above).
- **`MarketOnOpen`/`MarketOnClose`**: available at the venue, unused by the engine, and
  not expressible in the port's order vocabulary — recorded per FR-3.
- **Corporate actions (FR-10)**: bars are requested split-adjusted (`adjustment=split`,
  pinned in `transport.BAR_ADJUSTMENT` so a cached series can always state its policy);
  consuming split/dividend announcements and the dividend-purification recording flow
  are Phase B work.
- **Extended/overnight sessions**: OFF by posture; every order body pins
  `extended_hours: false` (FR-9).

## Running the conformance suite

```sh
uv run pytest tests/conformance/test_alpaca_conformance.py tests/broker_alpaca -q
```

Everything runs against canned fixtures in `tests/fixtures/alpaca_*.json` — no network,
no credentials, no orders.
