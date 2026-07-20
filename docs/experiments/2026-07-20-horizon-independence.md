# Cross-horizon independence — the horizon ladder does NOT add independent evidence

**Date:** 2026-07-20
**KB basis:** §79.2 (horizon breadth), §80.16 (the measurement), §73.5 (why it is non-optional)
**Verdict:** ⛔ **Do not build the horizon ladder as a knowability fix.** Measured cross-horizon
P&L correlation on our three cryptos is **0.508**, against §79.2's 0.22 benchmark — more than
double.

## Why this was measured before building

§79.2 identified horizon breadth as *"the one breadth axis the halal 3-asset allowlist does NOT
cap"*, reporting cross-frequency correlation of **0.22** for the same rule at different horizons,
and named it the replacement for the two under-deployment fixes that had already died (§75.1
killed ranking, §79.1 killed per-asset frequency).

But that 0.22 is **portfolio-level over 71 assets**, and the KB flagged that it *"must be measured
on our 3 correlated cryptos"* before being credited.

§73.5 makes this non-optional rather than diligent: correlated streams inflate `N` without adding
independent evidence — two rules that fire together are one rule counted twice, spending trials
budget twice for one observation's worth of information. **A correlated addition is strictly worse
than adding nothing.**

## Method

`keel/research/independence.py`, implementing §80.16's five measurements verbatim: daily {0,1}
position vectors, Jaccard overlap, position correlation, entry-timing distance, and daily-P&L
correlation. Pure stdlib `Decimal`.

Ladder: the shipped Turtle at four scaled horizons holding the 2:1 entry/exit shape constant —
**10/5, 20/10, 40/20, 80/40** — on real cached daily candles. 18 horizon pairs across three assets.

## Result

| | mean across 18 pairs | §79.2 benchmark |
|---|---:|---:|
| Jaccard overlap | 0.510 | — |
| Position correlation | 0.585 | — |
| **P&L correlation** | **0.508** | **0.22** |

Per pair, the picture is worse where it matters most:

| pair | BTC P&L corr | ETH P&L corr | median entry gap |
|---|---:|---:|---:|
| 40/20 vs 80/40 | **0.802** | **0.934** | **0 days** |
| 20/10 vs 40/20 | 0.652 | 0.553 | 0 days |
| 10/5 vs 80/40 | 0.486 | 0.425 | 49 / 82 days |

**Adjacent rungs are nearly the same rule.** 40/20 and 80/40 correlate at 0.80 on BTC and 0.93 on
ETH, with a **median entry gap of zero days** — they open on the same day. On PAXG, 20/10 vs 40/20
reaches 0.970 P&L and 0.968 position correlation; that is one rule wearing two names.

**There is a real gradient, and it is not enough.** The further apart the horizons, the lower the
correlation — the most separated pair (10/5 vs 80/40) is the best at ~0.43–0.49. But even the
*best* pair is roughly double §79.2's 0.22, and it is the pair whose short rung trades most often
and least well.

## Decision

⛔ **The horizon ladder is not a knowability fix on this book, and should not be built as one.**

It would add trades. Those trades would be ~0.5-correlated with the ones we already have, which
per §73.5 means it consumes trials budget, inflates apparent trade count, and leaves the evidence
problem exactly where it was. That is the same failure mode as the S1+S2 ensemble, which was
retracted on measurement the same day.

**This does not refute §79.2.** Their 0.22 is a portfolio-level figure over 71 diverse futures
markets; ours is three assets that are substantially one long-crypto-beta bet plus gold. The KB's
own caveat anticipated exactly this. What is refuted is the *transfer* of that number to our book.

## What it costs and what it saves

Zero trials budget — no configuration was selected. It saves building a feature (overlapping
tranches, §79.3's Jegadeesh–Titman construction, plus the exposure-rail work concurrent rungs
would need) whose stated purpose the measurement shows it cannot serve.

The measurement module is reusable and is the thing §80.16 says nobody has published. It is also
**the gate the second rule class must pass** — weekly TSMOM (§80.10) or any `macd_divergence`
revival must be measured against the shipped Turtle with this same tool *before* being built, not
after.

## Caveats

- **PAXG's 80/40 rungs are a small sample** — 439 daily bars produce few trades, and its
  near-zero P&L correlations there (0.072, −0.000) are more plausibly small-sample artifacts than
  genuine independence. BTC and ETH carry the conclusion.
- Correlation is measured over the whole window, not conditioned on regime. Two rules can be
  near-independent in chop and converge in a strong trend — §74.12's warning — which would make
  these figures optimistic, not pessimistic.
- The ladder was built by scaling one rule. A ladder of *structurally different* rules at
  different horizons is a different object and is not tested here.
