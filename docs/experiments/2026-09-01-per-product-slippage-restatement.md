# Every measured null, re-priced per product — and the flat floor nobody reaches

**Date:** 2026-09-01 · **Issue:** [#335](https://github.com/CodeGateSoftware/keel/issues/335)
(split from #259) · **Driver:** `2026-09-01-per-product-slippage-restatement.py` ·
**240 trials** · **Ledger row:** `per-product-slippage-restatement-2026-09-01`

## The finding

Every experiment document in this repository prices fills at `slippage_pct=0.0005` — the
**floor** of `slippage_for_quote_volume`, which the model reaches only at its $500M/day anchor.
Measured over the 24-asset universe's own cached hourly candles:

| | |
| :-- | --: |
| assets pricing at the floor | **0 of 24** |
| cheapest | BTC-USD, 5.5bp — **1.1×** the floor |
| dearest | TON-USD, 183.8bp — **36.8×** (the cap) |
| median | ≈ **10×** |

Ten assets sit above 10× and four above 20×. #335 names *"the STX/CRO-class 1.15–1.30× floor
entries"* as the live example of a thin-asset candidate reaching the gate. The live example is
the entire universe.

## What it costs

Five rules — the three shipped signal rules plus `cusum_event` (#341) and `triple_barrier`
(#342) — at their shipped defaults, 24 assets, both regimes, run **here** so the A/B is
internally consistent rather than compared against numbers in other documents.

| rule | PF flat | PF per-product | delta |
| :-- | --: | --: | --: |
| `turtle_breakout` | 0.336 | 0.267 | −0.063 |
| `rsi_meanrev` | 0.261 | 0.175 | −0.082 |
| `pullback_continuation` | 0.042 | 0.012 | −0.021 |
| `cusum_event` | 0.343 | 0.243 | −0.080 |
| `triple_barrier` | 0.338 | 0.237 | −0.092 |

**Across all 120 cells the median profit factor falls from 0.309 to 0.219** — a median
overstatement of **0.090**.

That number deserves to be read next to the other one measured this week: the triple barrier's
better exit bought **+0.033** of gross profit factor
([the A/B](2026-09-01-triple-barrier-first-measurement.md)). **The error in the cost model was
2.7× larger than the best genuine improvement any rule change produced.** Every comparison
between strategies in this repository has been made through a lens that mis-priced execution by
more than the differences being compared.

## The one cell that looked positive dies

| rule | product | flat | per-product | n | slippage |
| :-- | :-- | --: | --: | --: | --: |
| `turtle_breakout` | WLD-USD | **1.061** | **0.626** | 58 | 1.209% (24.2×) |

It was the only cell above 1.0 in 120 under flat pricing, it was already below the 100-trade
admission floor, and per-product pricing removes it. **Zero of 120 cells clear PF 1.0 under
per-product pricing.**

## Why this was safe to defer, and why it is not safe to leave

#259 deferred the gate opt-in on the reasoning that the correction is **conservative-only**:
real cost is higher, so a corrected profit factor can only fall, and per-product pricing can
never manufacture an edge. That reasoning is correct and this run confirms it — every one of the
120 deltas is negative or zero.

What it does not survive is the magnitude. A correction assumed to be a rounding adjustment is
worth 0.090 of median profit factor and kills the only positive cell in the corpus. A gate that
prices promotion decisions — the money-moving case — at the best rate the model can produce is
not being conservative; it is being optimistic in the one place optimism is most expensive.

**`keel rules backtest` / `rules promote` now price per product** (`rules.backtest_slippage`),
computed from the product's cached ONE_DAY bars by the same one definition
`simulate.slippage_assumptions` uses. A product with no daily bars falls back to the flat floor
and is **flagged as a fallback**, never presented as a measured verdict.

## Honesty

**No configuration, no argmax, no free parameters.** Every rule runs at its shipped defaults;
there is nothing here that could be selected on.

**This does not restate the older documents' numbers.** The restated intersection, the fee curve
and the hourly turtle sweep all still carry flat-priced figures. Their VERDICTS are unaffected —
the correction only pushes them further from 1.0 — but their levels are optimistic by roughly the
margin measured here, and a reader comparing across documents should know it.

**A unit trap, recorded twice now.** `median_daily_quote_volume` returns a **per-bar** median
despite its name. Read off an hourly series and handed to a model anchored on a daily volume, it
reports every asset as maximally thin. The gate avoids it by reading ONE_DAY bars, as
`simulate` already did; `triple_barrier.per_product_round_trip` cannot (a pure rule has only the
candles handed to it) and scales explicitly instead. Both say so where they do it.

**Validation.** Screening result only: no walk-forward, no out-of-sample split, no CSCV/PBO
(`series_missing`). Same cached candles and ~5-year window as every other document here. Fees
held at the 1.2% taker rate in every cell, so slippage is the only thing that varies.

**Changed nothing about what trades.** No rule row added, nothing promoted, no config or
allowlist touched.

## Recommended next

1. **Do not restate the old documents by re-running them.** The verdicts do not move and the
   compute is not free. What is worth doing is a one-line note in each pointing here.
2. **The account simulator and `paper.py` still carry their own flat literals.** #335's mechanics
   section names them; the gate was the money-moving one and is done, and folding the rest is a
   tidy-up with no verdict attached.
3. **The null is unchanged and firmer: 0 of 138 stands, now at honest cost.**
