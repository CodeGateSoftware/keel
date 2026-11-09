# Do the last two digits of PAXG's price predict the next four hours? — No

**Pre-registered.** The driver `2026-11-09-paxg-last-two-digits.py` was committed and pushed
(30dbe5b) before it ran on real data. Its docstring sets out the method and the decision rule.
Not a promotion decision: three `diagnostic_only` rows in the trials ledger.

## The claim
An outside observer claimed that the last two digits of PAXG's price on the starting candle
predict whether it rises or falls over the next four hours. No rule came with the claim. The
test therefore asks the question any such rule depends on: do the digits carry any information
about the sign of the 4-hour move? Each test can find any mapping from digits to direction,
including one fitted to the data, so a null here also rules out every specific rule.

## Result

| arm | windows | in-sample chi-square p | out-of-sample hit rate | null median / p95 | OOS p | shows dependence |
| :--- | ---: | ---: | ---: | ---: | ---: | :---: |
| Coinbase, dollar digits | 3,275 | 0.097 | 48.9% | 48.7% / 50.9% | 0.45 | no |
| Coinbase, cents | 3,275 | 0.233 | 49.2% | 48.8% / 51.3% | 0.39 | no |
| Binance 2021–26, dollar digits | 10,082 | 0.737 | 48.9% | 49.6% / 50.9% | 0.80 | no |

Bonferroni alpha is 0.0167. **Verdict: not supported.** No arm comes close: on all three, the
map learned from the first 60% of windows did no better on the last 40% than a map learned
from shuffled outcomes.

## Economics, even if the claim had been true
- The median absolute 4-hour move is **0.20%** on Coinbase and 0.17% on Binance.
- The Advanced Trade round trip at 0.9% taker per leg costs **1.8%**, about nine times the
  typical move.
- Buying on every "up" call in the test set: mean gross return per call was −0.009%, +0.015%
  or +0.010% depending on the arm. Mean net return was **−1.80%** on all three.

A perfect directional call on a typical window earns about 0.2% before costs, so no
direction-prediction edge on this horizon can pay keel's fees.

## Why one would expect this
For a price in the thousands, the last two digits are the fastest-cycling part of the number.
A 0.2% move on a $4,000 price is $8 in the dollar digits, so 4-hour volatility wraps them
around many times over. They are close to uniform noise. The best-known exception is clustering
near round numbers (support and resistance at …00). Any such effect would sit inside a 100-cell
test like this one and would have to be large to matter. Eyeballed patterns in digits are a
textbook case of seeing structure in a small sample. Here, 100 cells with about 33 windows
each will always produce a few digits that look like they "work" by chance.

## Limits
- The test has power against a broad dependence. A narrow rule, such as one digit or a
  handful, at a few points of edge, could hide inside 100 cells. Testing such a rule needs the
  friend's exact mapping, stated in advance and scored only on windows after it was written.
- The Binance and Coinbase samples overlap from 2025-05 to 2026-07. The replication is not
  fully independent there.
