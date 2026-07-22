# The `-USD` / `USDC` quote-currency mismatch — design

**Date:** 2026-07-22
**Status:** Approved design
**Trigger:** found during the first supervised live-order attempt.

## The defect

The codebase conflates two different things:

- the **product's quote leg** — what an order actually spends (`BTC-USD` spends **USD**);
- **`config.quote_currency`** — a single global setting (default `USDC`).

Nothing derives the first from the product, so three places disagree with reality:

1. **Rail 13 (`usdc_funding`) guards the wrong balance.** `executor` fetches
   `_fetch_available_quote(broker, config.quote_currency)` and the rail compares *that* to the
   order notional — but the order settles in the product's quote leg.
   - **False veto** (observed): $49 USD available, $0.25 USDC, `BTC-USD` $5 order → vetoed.
   - **False pass** (the serious one): ample USDC, no USD → the rail **approves** an order that
     spends USD the account does not have. That is exactly the "never draw from a linked
     bank/ACH source" case rail 13 exists to prevent. A safety rail that can pass when it should
     veto is worse than no rail, because it is trusted.
2. **The screen's settlement check is vacuous.** `quotable_in_settlement_currency =
   product.endswith(f"-{quote}") or bool(candles)`. Every screened product is `-USD` while
   `quote` is `USDC`, so it always falls through to `bool(candles)` — i.e.
   `ScreenPolicy.require_settlement_quote` re-checks "do we have bars", which the history rail
   already covers. One of four admission criteria does nothing.
3. **`config.quote_currency` never matched this deployment.** Products, cached history, rules and
   the simulator are all `-USD` (`_default_sim_products`, `_history_product`, `keel fetch`).

## Fix

**One rule: the currency an order spends is a property of the PRODUCT, never of global config.**

1. **`keel_core.products.quote_currency_of(product_id) -> str | None`** — the leg after the last
   `-`, uppercased. `None` for a malformed id (no `-`, empty leg), so callers fail closed.
2. **Rail 13 guards the product's quote leg.** The executor fetches the balance of
   `quote_currency_of(intent.product_id)`; an unresolvable product id yields `None`, which the
   rail already treats as unknown and vetoes. The violation message names the actual currency
   instead of hardcoding "USDC". The rail key stays `usdc_funding` (it is an identifier other
   code and `LIVE_STATE_RAILS` match on).
3. **The screen's settlement check becomes real:** `quotable_in_settlement_currency =
   quote_currency_of(product) == policy settlement currency`. The `or bool(candles)` escape is
   removed — that clause is what made the criterion vacuous.
4. **`config.quote_currency: USD`** in the repo config and both templates, matching the products
   actually traded and cached. With (3) real, leaving it at `USDC` would reject every `-USD`
   asset — the setting has to describe reality, and reality is `-USD`.

`config.quote_currency` keeps a clear, narrower meaning: **the settlement currency this
deployment trades in**, used to screen candidates and to exclude the settlement balance from
holdings. It is no longer used to decide which balance funds a given order.

## Non-goals

- No change to *which* products are traded, and no re-fetching history under a different quote.
- Rail 13's fail-closed semantics are unchanged; only the balance it reads changes.
- No new veto for "product quoted in a currency you did not configure" — that is a real gap
  (§ below) but adding a veto the night after a live test is the wrong sequencing.

## Known remaining gap (recorded, not fixed)

Nothing yet *rejects an order* whose product quote leg differs from `config.quote_currency`; the
screen rejects such an asset at admission, but a live-seeded rule bypasses admission. Worth a
follow-up rail once this lands and the live path is proven.

## Testing

- `quote_currency_of`: `BTC-USD`→`USD`, `BTC-USDC`→`USDC`, `eth-usd`→`USD`, `BTC`→`None`,
  `""`→`None`, `BTC-`→`None`.
- **The false-pass hole**: ample `config.quote_currency` balance but an empty product-quote
  balance must VETO. This is the headline regression test.
- The false veto: sufficient product-quote balance passes even when the configured currency
  balance is 0.
- A malformed product id fails closed.
- Screen: `-USD` product with settlement `USD` passes; `-USDC` product with settlement `USD`
  fails **even with candles present** (proving the vacuous clause is gone).
- Both config templates stay byte-identical to the repo config where the existing test requires.
