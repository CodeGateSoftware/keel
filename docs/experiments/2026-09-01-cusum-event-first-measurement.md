# CUSUM event gating, first measurement — feasible, and without a gross edge

> **Cost note (added 2026-09-02).** The figures below are priced at the flat 5bp
> slippage floor. [the per-product restatement](2026-09-01-per-product-slippage-restatement.md) later measured that **no
> asset in keel's universe reaches that floor** — the range is 1.1× to 36.8× — so every
> profit factor here is optimistic by roughly 0.09 at the median. **The verdict is
> unaffected:** the correction only ever moves a number *down*, and every result here was
> already negative. Nothing on this page has been rewritten; records are appended to, not
> revised.

**Date:** 2026-09-01 · **Issue:** [#341](https://github.com/CodeGateSoftware/keel/issues/341) ·
**Rule:** `cusum_event` · **Driver:** `2026-09-01-cusum-event-first-measurement.py` ·
**Ledger row:** `cusum-event-first-measurement-2026-09-01` (168 trials disclosed)

## Declared before the run

* **Primary metric: `n_trades`.** An event filter trades less by construction, and the ρ=−0.77
  bind between edge and sample size means a rule that fires rarely cannot be admitted whatever
  its profit factor. Declared primary exactly as the `rsi_meanrev` diagnostic grid declared it.
* **Secondary: profit factor at fee 0 / 0.006 / 0.012.** Zero bounds from above everything an
  execution fix could ever buy; 0.006 is the maker rate this account cannot reach; 0.012 is what
  it pays.
* **Arm A is ONE configuration** — the shipped default. One config means no argmax, so nothing
  in arm A is a maximum-of-N.
* **Arm B sweeps the rule's own headline knob**, `threshold_friction_mult` ∈ {1,2,3,4} at the
  taker rate. Disclosed as a sweep; its per-asset best is a maximum of four draws.

24 assets, `ONE_HOUR`, ~5 years of cached candles — the same universe as the restated
intersection, so this sits beside the null it is compared against rather than beside a different
population.

## 1. Feasibility: YES, and it contradicts the worry filed with the issue

#341 was filed with the concern that *"gating cuts n on rules already below the admission
floor"*. **It does not.**

| | |
| :-- | --: |
| assets with n ≥ 100 (the admission floor) | **21 of 24** |
| median n | **553** |
| range | 32 (PAXG-USD) – 884 (FET-USD) |

A 5% cumulative move in an hourly crypto series is a common event, not a rare one. The
feasibility objection to this rule is answered and should not be repeated. The three assets below
the floor are PAXG-USD (32), PAXG-USDT (51) and TON-USD (69) — gold and the thinnest name in the
universe, which is the expected shape.

## 2. Profitability: no, and not marginally

Arm A, all 24 assets:

| fee | PF median | PF max | above 1.0 |
| :-- | --: | --: | --: |
| 0% | 0.925 | 1.238 | 8 / 24 |
| 0.6% (maker, unreachable) | 0.553 | 0.750 | **0 / 24** |
| 1.2% (taker, actual) | 0.343 | 0.479 | **0 / 24** |

Best cell at the rate actually paid: AVAX-USD, PF 0.479, n=601.

**The intersection of n ≥ 100 and PF > 1.0 is empty across all 168 trials, in both arms.**

## 3. The diagnostic: this is the `rsi_meanrev` disease, not the `turtle_breakout` one

The number that matters is the zero-fee column. **At zero cost only 8 of 24 clear 1.0, and the
median is 0.925.**

`turtle_breakout` at zero fee was profitable on 4 of 4 with a maximum of 2.713 — a real gross
edge that cost destroyed, which is why cheaper execution was a coherent thing to want. This rule
has **essentially no gross edge for cost to destroy**: it loses money at a median before a single
fee is charged.

Same symptom as turtle at 1.2%, different disease — and the one that cannot be fixed by
execution, because zero fee bounds from above everything an execution improvement could ever buy
and zero fee is already a median loss.

## 4. The knob works exactly as designed, and converges to nothing

Arm B, `threshold_friction_mult` at the taker rate:

| mult | threshold | median n | PF median | PF max | above 1.0 | n ≥ 100 |
| --: | --: | --: | --: | --: | --: | --: |
| 1 | 2.5% | 1252 | 0.226 | 0.388 | 0 | 23 |
| 2 | 5.0% | 553 | 0.343 | 0.479 | 0 | 21 |
| 3 | 7.5% | 330 | 0.361 | 0.533 | 0 | 21 |
| 4 | 10.0% | 224 | 0.379 | 0.593 | 0 | 20 |

Raising the multiple monotonically raises median PF and cuts median n. **The mechanism is real**
— trading less does lose less per toll. It is also converging to a ceiling well below 1.0 while n
falls toward the admission floor, so there is no multiple at which both conditions hold. A
fourfold increase in the threshold buys 0.15 of profit factor and costs 82% of the sample.

## 5. The source's own setting is the worst cell

`mult=1` is the paper's 2.0–2.5% threshold, and on this venue it is *exactly one round trip*.
It is the worst arm-B cell on every axis: the lowest median PF (0.226) with the highest median n
(1252) — **the most trading at the least edge.**

That is #341's friction-scaling argument measured rather than asserted. A threshold spelled as a
percentage looks conservative; the same threshold spelled as `threshold_friction_mult=1` says
what it is.

## Honesty

**Selection bias.** Arm A is one pre-declared configuration and carries none. Arm B's per-asset
best is a maximum of four draws and must never be quoted as an edge estimate — the best cell
overall is CRV-USD at mult=4, PF 0.593, n=322, still 0.4 short of 1.0. The bias does not change
the verdict in either direction.

**Validation.** Screening result only. No walk-forward, no out-of-sample split, no CSCV/PBO and
no deflated Sharpe: `backtest` emits aggregates and no per-trade series (`series_missing`). Same
cached candles and the same ~5-year window as every other document here. `slippage_pct` held at
0.0005 in every cell, so the zero column is zero *fee*, not zero cost — round-trip friction there
is still 0.10% of notional.

**Changed nothing.** A document, a driver script and a ledger row. No rule row was added, nothing
was promoted, no config was touched, no allowlist changed. `cusum_event` remains registered and
untraded.

## Recommended next

1. **Not further `cusum_event` tuning.** It is negative at zero cost on 16 of 24 assets, and its
   own headline knob moves median PF by 0.15 across a fourfold range. A different event
   definition is a new rule with a new pre-registration, not a continuation of this one.
2. **#342 (triple-barrier exits) is now the only untested half of the source's claim** — but its
   prior tightened here rather than loosening: the entry half has no gross edge for a better exit
   to harvest.
3. **The null grows rather than breaks.** The measured intersection goes from 0 of 90 to **0 of
   114**. That is the result, and publishing it is the point.
