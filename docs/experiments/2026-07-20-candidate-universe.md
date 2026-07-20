# Candidate universe — 936 products, 7 viable, and the expansion still works

**Date:** 2026-07-20
**KB basis:** §78.12 rule 3 / §73.3 (more assets = zero-trials `T` gain), §5 (asymmetry), §28.4
**Status:** discovery only. **Nothing attested, nothing admitted, allowlist unchanged.**

## The question this answers

PR #105 derived the evidence target: **125 trades**, which at today's ~6 trades/year is **~21
years**. The expansion case rested on reaching ~10 assets to bring that to ~5. That assumed a
universe large enough to supply them. This measures whether one exists.

## Method

Two stages, cheap before expensive.

1. **Venue metadata** (one request): 936 spot products → filter to online, tradable, quoted in
   USDC, with ≥ $5M reported 24h volume, excluding what we already hold.
2. **History probe** (one request per survivor): does daily history exist at the 4-year mark? A
   candidate that fails this can never clear `screen_asset`'s history floor, so probing first
   avoids spending attestation effort on it.

Neither stage decides anything. Per §5's asymmetry a proposal may come from anywhere; **admission
goes through the deterministic screen, which fails closed without a human attestation.**

## Result: 936 → 14 → 7

| # | asset | 24h quote volume | 4yr history | name |
|---:|---|---:|:---:|---|
| 1 | SOL | 52,489,141 | **yes** | Solana |
| 2 | ZEC | 49,290,684 | **yes** | Zcash |
| 3 | XRP | 49,192,732 | no | XRP |
| 4 | HYPE | 24,264,782 | no | Hyperliquid |
| 5 | PUMP | 14,875,212 | no | Pump.fun |
| 6 | XLM | 11,205,793 | **yes** | Stellar Lumens |
| 7 | LTC | 9,460,741 | **yes** | Litecoin |
| 8 | DOGE | 9,216,195 | **yes** | Dogecoin |
| 9 | ADA | 7,968,276 | **yes** | Cardano |
| 10 | LINK | 7,347,511 | **yes** | Chainlink |
| 11 | EURC | 6,821,401 | no | EURC |
| 12 | SUI | 6,555,727 | no | SUI |
| 13 | NEAR | 6,114,253 | no | NEAR Protocol |
| 14 | BONK | 5,973,282 | no | Bonk |

**Seven candidates carry four years of daily history: SOL, ZEC, XLM, LTC, DOGE, ADA, LINK.**

## Does the expansion case survive? Yes, but with less headroom than assumed

Seven candidates plus BTC and ETH is a **maximum of 9** — and only if every one of the seven
attests clean, which is unlikely (see below).

| allowlist | trades/year | years to 125 trades |
|---:|---:|---:|
| 3 (today) | ~6 | ~21 |
| **9 (max realistic)** | **~23** | **~5.4** |
| 6 (a plausible outcome) | ~16 | ~8 |

**The argument holds.** Even the middle case cuts the horizon from ~21 years to ~8, and the
optimistic case reaches the ~5 years PR #105 identified. But the ceiling is 9, not the "10+" the
original framing implied, and the realistic figure depends entirely on the attestation step.

## Questions the attestation step has to answer — which this agent must not answer

These are the open questions a human (with a scholar where needed) has to settle. **Listing them is
not screening them**, and nothing here is a verdict:

- **ZEC** — a privacy coin. Whether that bears on permissibility is a question, not a defect.
- **DOGE** — originated as a joke/memecoin. §28.4's screen is on the *underlying business or
  purpose*; whether "no underlying purpose" is disqualifying is exactly the kind of judgement the
  screen defers to a human.
- **SOL, XLM, LTC, ADA, LINK** — general-purpose infrastructure or payment networks, which is the
  most conventional case, but "looks conventional" is not an attestation.

Note also that three candidates the **history probe already removed** would likely have raised
sector or backing questions anyway: **HYPE** (a perpetual-futures DEX token — leveraged derivatives
as the underlying business), **EURC** (a fiat claim on an issuer, so a `dayn` question), and
**PUMP** (a memecoin launchpad). That the cheap filter removed them first is convenient, not
principled — the screen would have had to consider them.

## What happens next, in order

1. **Attest BTC and ETH** — the assets we already hold fail the screen on paperwork alone.
2. **Decide PAXG** — 439 daily bars against a 4-year floor. Hold it to the same standard as a new
   candidate, or record an explicit reasoned exception. Not silently exempt.
3. **Attest whichever of the seven survive scrutiny**, each with a source.
4. **Fetch five years of daily history** for the admitted set and re-run the validation. The
   Turtle is applied *unchanged* — §73.3's inheritance rule is what makes this zero-trials, and it
   only holds if nothing is re-fitted per asset.

⚠️ **Step 4 is where discipline will be tested.** The temptation on adding six assets will be to
re-tune the rule for them. Doing so converts a zero-trials `T` gain into a fresh sweep and forfeits
the entire reason this path was chosen.

## Caveats

- The $5M/24h floor is a judgement call, chosen to cut 936 to a reviewable list. Lowering it would
  add thinner candidates; §54's liquidity caution and our own tiny size both argue against.
- The history probe checks *existence* at the 4-year mark, not completeness. A candidate marked
  `yes` could still have gaps — `keel fetch --repair-gaps` would surface those after admission.
- Volume figures are the venue's own 24h snapshot on one day, not a median. A quiet or frantic day
  would move the ranking; the floor is deliberately loose enough that it should not move
  membership much.
