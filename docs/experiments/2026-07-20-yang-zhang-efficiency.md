# Yang–Zhang volatility on 24/7 crypto — the 8× does not replicate

**Date:** 2026-07-20
**KB basis:** §79.9 (Yang & Zhang 2000, via Baltas–Kosowski)
**Verdict:** ⛔ **Do NOT substitute Yang–Zhang for close-to-close or ATR in sizing, stops, the
correlation rail or the shock detector.** The estimator ships and is tested; it is deliberately
**not wired into anything.**

## Why this was measured rather than adopted

§79.9 is explicit: Yang–Zhang is *"almost 8 times more efficient"* than close-to-close, **but**
`σ²_OPEN` measures the overnight gap and 24/7 spot has no overnight session, so the KB flagged
that *"the 8× efficiency figure is derived for gapping markets and must be re-verified
empirically on our own series before it is claimed."*

This is that re-verification. It costs no trials budget — an estimator change is an estimation
improvement, not a parameter choice (§73.12).

## Method

Two passes. The first was confounded and is recorded because the correction matters.

**Pass 1 (confounded).** Compare the variance of each estimator's own rolling output. This
conflates estimator noise with genuine time-variation in volatility: both estimators track a
real, moving σ, so most of that variance is signal. Ratios came out 0.93–1.93×, which is
uninformative rather than wrong.

**Pass 2 (the real test).** Score each daily-bar estimator against a **high-frequency realized-
volatility benchmark** built from the *hourly* bars covering the same window — ~1,440
observations against the estimator's 60. Efficiency = `MSE(close-to-close) / MSE(Yang–Zhang)`;
above 1 means Yang–Zhang wins.

Window 60 daily bars, step 5, real cached candles.

## Result

| series | n | MSE YZ | MSE CC | efficiency | bias YZ | bias CC |
|---|---:|---:|---:|---:|---:|---:|
| BTC-USD | 354 | 0.002084 | 0.002094 | **1.01×** | +0.0303 | −0.0028 |
| ETH-USD | 354 | 0.004931 | 0.003989 | **0.81×** | +0.0512 | +0.0079 |
| PAXG-USD | 76 | 0.004419 | 0.002002 | **0.45×** | +0.0547 | −0.0018 |

**The 8× does not replicate. Nothing close to it.** Yang–Zhang is at best equal (BTC) and
materially *worse* on ETH and PAXG.

**And it is biased upward.** YZ overestimates volatility by +0.03 to +0.055 annualised across
every series, while close-to-close is near-unbiased (−0.003 to +0.008). That is the more
damaging finding: a systematically high σ would inflate ATR-derived stop distances and shrink
volatility-scaled position sizes across the board.

## Why

Two mechanisms, both downstream of the KB's own warning:

1. **The efficiency gain is mostly the overnight term**, and on continuous 24/7 spot it has
   almost nothing to measure. Remove it and Yang–Zhang collapses toward `k·STDEV + (1−k)·RS`,
   which is not meaningfully better-informed than close-to-close.
2. **`σ²_OPEN` is not exactly zero on our data** — `O(t)` is close to but not identical to
   `C(t−1)` at bar boundaries. That residue is pure noise, it enters the variance as a strictly
   positive term, and it is a sufficient explanation for the upward bias.

## Decision

- **Do not wire Yang–Zhang into ATR sizing, the 2N stop, the correlation rail, or the §54.20
  shock detector.** The existing true-range/close-to-close machinery is not improved by it here.
- **Keep `yang_zhang_volatility` and `close_to_close_volatility` in `analysis/indicators.py`**,
  tested and documented, so this measurement is reproducible and so a future gapping-market
  venue (equities, futures) can use the estimator where its assumptions actually hold.
- **Correct the KB's expectation for this project.** §79.9 called it a "free win"; on our data it
  is free but it is not a win. The KB's *instruction* was right — it said measure before
  claiming — and the measurement is what saved us from a silent degradation of every
  volatility-derived quantity in the system.

## Caveats

- One window length (60) and one benchmark construction. A shorter window might favour the range
  estimator more, since range-based estimators gain most where the close-to-close sample is
  small — untested.
- The hourly benchmark is itself an estimate, and inherits any microstructure noise in hourly
  bars. It is far more precise than 60 daily observations, which is what the comparison needs.
- PAXG's 76 windows are a short sample on a young series; BTC and ETH carry the weight.
