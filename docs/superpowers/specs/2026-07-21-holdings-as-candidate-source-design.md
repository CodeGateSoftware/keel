# Broker holdings as a candidate source — design

**Date:** 2026-07-21
**Status:** Approved design (pending user review)
**Workstream:** C of three. Item 3 of the 2026-07-21 requirements.

## Context

> "Fetch user's list of assets to trade from his broker, coinbase for the time being, then vet each
> of them through our rules. In the future, our LLM will propose a list of assets to trade and must
> go through the same vetting process and test simulations before adding to the user's asset list."

The vetting gate **already exists** and is the valuable part of this codebase's compliance work:

- `compliance/screen.screen_asset(facts, attestation, policy) -> ScreenResult` — deterministic
  admission. Checks history depth (4y of daily bars), liquidity (median daily volume), settlement
  in the quote currency, and the **attested** shariah classification (sector / backing / yield).
  **`attestation=None` fails closed** — sector and backing cannot be derived from price data, so an
  unclassified asset is *unknown*, and unknown is a rejection.
- `keel assets discover` proposes candidates from **venue-wide** metadata (~936 products → a
  shortlist), `keel assets screen` vets, `keel assets attest` records a human classification with a
  source, `keel assets list` shows what has been attested.

What is missing is a **source**: the user's own Coinbase holdings. Today the only proposer is
venue-wide discovery, which answers "what could anyone trade?" rather than "what do *I* already
hold that this system might trade?".

## Goals

1. **A new candidate source, not a new gate.** Fetch the assets the user actually holds at the
   broker and run each through the *existing, unmodified* `screen_asset` path.
2. **Make "held" and "admitted" impossible to confuse.** Holding an asset is not a reason to trade
   it. The command admits nothing.
3. **Leave a clean seam for the LLM proposer**, which must enter the same gate rather than a
   parallel one.

## Non-goals

- **No change to `screen_asset`, `ScreenPolicy`, or the attestation requirement.** If this work
  needed to weaken the gate to admit the user's own holdings, that would be evidence against the
  holdings, not against the gate.
- No automatic attestation. A holding is not a classification; a human still records sector and
  backing with a source.
- No automatic allowlist mutation. `config.allowlist` stays a deliberate human edit.
- The LLM proposer itself is **not** built here (it is off-by-default, API-key-gated, and needs its
  own design). This spec only ensures the seam it must use exists.

## Design

### 3.1 `keel assets holdings`

```
keel assets holdings [--min-balance 0] [--screen]
```

1. Fetch `broker.get_accounts()` (already implemented, read-only, proven against the live API).
2. Derive the candidate set: accounts with `available_balance > --min-balance`, **excluding** the
   settlement/quote currency and fiat — you cannot "trade" the currency you settle in, and listing
   it as a rejected candidate is noise, not information.
3. For each remaining asset, report:
   - **held** balance;
   - whether it is on the current `config.allowlist`;
   - whether an attestation exists (`repo.get_asset_attestation`);
   - with `--screen`, the full `ScreenResult` — the same `ADMIT`/`REJECT` plus failure reasons that
     `keel assets screen` prints.

The command **never** writes: no attestation, no allowlist change, no DB mutation. It is a
read-only report, in the same family as `assets discover`.

### 3.2 One gate, shared by construction

`assets_screen` currently inlines the attestation lookup, the `AssetAttestation` construction and
the `screen_asset` call. That block is extracted to:

```python
def _screen_product(repo, product, quote) -> screen_mod.ScreenResult
```

and **both** `assets screen` and `assets holdings --screen` call it. This is the mechanism that
makes "the same vetting process" true rather than aspirational: there is one call site of
`screen_asset` behind one helper, so a future LLM-proposed candidate cannot accidentally get a
laxer path. A test asserts both commands produce the same verdict for the same asset.

### 3.3 "No local history" is not the same as "bad asset"

`_market_facts` computes `daily_bars` from **cached candles in the local DB**. A newly-surfaced
holding will usually have none, so the screen rejects it for insufficient history — which is
correct (we genuinely cannot validate a rule on data we do not have) but easy to misread as "this
asset is unsuitable".

So when `daily_bars == 0`, the output says so explicitly and names the fix:

```
REJECT  SOL      no local history -- run `keel fetch --products SOL-USDC` first,
                 then re-screen. This is a MISSING-DATA verdict, not a verdict about the asset.
```

This distinction is the single most likely misreading of the feature, so it is handled in the
output rather than left to the operator. For the same reason, the **liquidity** and **settlement**
failures are suppressed on that path (shown as `· not assessable without history`): with zero bars
median volume is 0 *because* there are no bars, and `quotable_in_settlement_currency` degenerates
to `bool(candles)` — printing either as a finding would assert about the asset exactly what the
missing-data message exists to deny. The `history` failure remains, because it is the real one.

⚠️ **A pre-existing weakness this surfaced, deliberately NOT fixed here.** Because every product
this codebase screens is `-USD` while `quote_currency` is `USDC`,
`MarketFacts.quotable_in_settlement_currency` reduces to `bool(candles)` for all of them — so
`ScreenPolicy.require_settlement_quote` currently re-checks "do we have bars" rather than
settlement. That affects `assets screen` today, independently of this work. Changing it alters a
**compliance rail's** behaviour and deserves its own deliberate change, not a quiet edit inside a
feature PR.

### 3.3a Known limits of the holdings source

- Staked/wrapped balance types (`ETH2`, `CBETH`) and non-settlement stablecoins (`USDT`, `DAI`)
  appear as candidates, and for a balance with no `-USD` product the `keel fetch` hint will not
  resolve. They are correctly REJECTED either way (unattested, no history), so the cost is a
  redundant row, never a wrong admission. Filtering them properly needs the venue product list,
  which is a network call this read-only report deliberately does not make.
- The fiat exclusion list is static. A fiat Coinbase quotes that is missing from it shows up as an
  extra rejected row — cosmetic, never an admission.

### 3.4 The seam for the LLM proposer

A proposer is anything that produces a list of asset codes. `assets discover` (venue metadata) and
`assets holdings` (broker balances) are the two implemented ones; an LLM proposer is a third. All
three converge on `_screen_product`, and none of them can admit anything — admission requires a
human `keel assets attest` plus a passing screen plus a deliberate `config.allowlist` edit.

Recorded here so the LLM work inherits it: per the project's §5 asymmetry, an LLM may *propose* and
may *veto*, but may never *admit*. Nothing in this spec grants a proposer new authority.

## Components

| File | Change |
|---|---|
| `keel/cli.py` | extract `_screen_product`; new `assets holdings` command |
| `tests/compliance/test_assets_cli.py` (or nearest existing) | new tests |

No new modules, no schema change, no new dependency.

## Testing

- Holdings are fetched from the broker and the quote currency / fiat are excluded.
- `--min-balance` filters dust.
- A held asset that is unattested is reported **REJECT** (fail closed) — holding it changes nothing.
- A held asset already on the allowlist is marked as such.
- `--screen` produces the *same* verdict as `keel assets screen` for the same asset (one gate).
- `daily_bars == 0` produces the explicit missing-data message, not a bare rejection.
- The command writes nothing: no attestation row, no allowlist change, no DB mutation.
- Broker failure is reported as an error, not as an empty (and therefore falsely clean) result.
