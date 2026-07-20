# MinBTL — how much evidence the Turtle actually needs

**Date:** 2026-07-20
**KB basis:** §78.1 (E[max SR]), §78.2 (N̂), §78.3 (DSR), §73.2 (MinBTL)
**Status:** reporting only. **No gate changed, no parameter touched.**

## Why now

The allowlist-expansion argument (PR #104) rested on an assumed target of **100 trades** — the
existing `min_trades` floor. That number was never derived; it was inherited. MinBTL is the formula
that derives it, and it determines how far the allowlist actually needs to expand.

This is §78.13's last outstanding item (1–3 and 6). Zero trials budget — an arithmetic report over
a ledger that already exists spends nothing.

## Verification before use

Every downstream number rests on `E[max{SR_n}]`, so it is tested against **published** values
rather than only for internal consistency:

| check | expected | source |
|---|---|---|
| `E[max]` at N=10 / 128 / 1000 | 1.5746 / 2.6163 / 3.2551 | §73.1's table, verified in §78.1 |
| MinBTL at N=45, SR=1 | ~5 years | §73.2's worked example |
| MinBTL at N=7, SR=1 | ~2 years | §73.2's "far more alarming" example |
| N̂ at ρ=0.90, M=336 | ~34 | §78.2's worked correction |

All reproduce.

## The result

Ledger state: **M = 69** rows, **N_decisions = 30** (diagnostics excluded). Observed annualised
Sharpe **0.395**, realised frequency **~6 trades/year**.

| assumed ρ̂ | N̂ | E[max] | MinBTL (years) | **MinBTL (trades)** |
|---:|---:|---:|---:|---:|
| 0.00 (independent) | 30.0 | 2.073 | 27.6 | **165** |
| **0.50 (measured)** | **15.5** | **1.800** | **20.8** | **125** |
| 0.90 (highly correlated) | 3.9 | 1.052 | 7.1 | 43 |

### ρ̂ = 0.50 is not a guess — we measured it today

§78.2 requires `ρ̂`, the correlation among trial outcomes, and flags that estimating it directly is
ill-conditioned (`T < ½M(M−1)` binds). But PR #103's independence measurement produced exactly this
quantity empirically: **mean P&L correlation of 0.508 across horizon variants of the same rule**.
Our trials are overwhelmingly lookback sweeps — the same family of near-duplicate configurations
that measurement covered — so 0.5 is the best-supported column, not the middle one by default.

⚠️ It is still an assumption band, and the honest reading is **125 trades with a plausible range of
43–165**, not a point estimate.

## What this changes

**1. `min_trades = 100` is inside the band but is not conservative.** It sits around ρ̂ ≈ 0.65 — more
lenient than the measured 0.5 column (125) and far more lenient than the independent-trials
reading (165). It is defensible, and it is **not** the cautious choice it reads as. Two independent
lines already put the requirement near or above it: the random-entry experiment's ~68 trades to
clear z≥2, and now MinBTL's 125.

⛔ **This is not a licence to raise the floor to 125.** Raising a promotion threshold on the basis
of our own numbers is a decision, hence a trial, and the floor's current value has a §25.5
justification. Recorded as a finding; any change goes through the normal route.

**2. It sizes the allowlist expansion, which is the actionable part.** At a 125-trade target:

| allowlist | trades/year | years to 125 trades |
|---:|---:|---:|
| 3 (today) | ~6 | **~21 years** |
| 6 | ~13 | ~10 years |
| **10** | **~26** | **~5 years** |
| 15 | ~39 | ~3 years |

The current book needs about **21 years** to accumulate the evidence its own edge requires. Ten
assets brings that to roughly five. **That is the entire case for allowlist expansion, now with a
derived target instead of an inherited one.**

**3. We are at 31 of 125 trades — about a quarter of the way**, after five years of history.

## What is NOT computed, and why

**DSR is not reported.** It needs `V[{SR_n}]`, the variance of Sharpes across trials, which
requires a per-trial Sharpe on every ledger row. The 39 backfilled rows are `series_missing` — the
sweeps that produced them destroyed their series (§78.4), which is the precise failure §78.4
predicted. The command reports `DSR: NOT COMPUTED` rather than substituting a plausible default,
and will compute it once trials recorded going forward carry their own performance statistic.

**The Harvey–Liu haircut (§78.13 item 7) is not built.** It needs per-trial p-values, which have
the same missing input. Deferred rather than approximated.

## Caveats

- MinBTL is **necessary, not sufficient** — §73.2 states plainly that *"a backtest may be overfit
  even if it is computed on a sample greater than MinBTL."* Clearing it is a floor, not a
  certificate.
- `N_decisions = 30` comes from a reconstruction whose two largest components were deliberately
  over-counted (see the backfill note), which pushes MinBTL **up**. Erring toward more evidence is
  the right direction for a promotion gate, but the number is not precise.
- The Sharpe of 0.395 is in-sample. §79.13's consolation applies — with 31 years and 55 futures,
  47 of 55 assets fail `t = 1.65`, so an unremarkable Sharpe is not itself the problem.
