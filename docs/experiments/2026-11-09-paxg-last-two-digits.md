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

## ⚠️ Erratum (2026-11-09, after merge)
Review of PR #1019 found the errors below. They were merged unfixed. The verdict, the table and
the driver are unaffected. The driver is left byte-identical to the pre-registered commit
(30dbe5b), so its docstring corrections are recorded here rather than edited in.

1. **Gross return per "up" call, cents arm.** The Economics section reads "+0.015%". The jsonl
   gives `mean_gross_up_call` = −0.000149, so the figures are **−0.009%, −0.015% and +0.010%**
   (Coinbase dollar digits, Coinbase cents, Binance).
2. **Net return per "up" call.** It reads "−1.80% on all three". The arms give **−1.80%, −1.81%
   and −1.78%**.
3. **"Why one would expect this" is backwards for the dollar digits.** An $8 move covers 8% of
   their $100 cycle, so it doesn't wrap them. Only the cents ($1 cycle) wrap, about eight times.
   The correct argument: the digit a window starts on is effectively random from one window to
   the next, and direction could depend on it only through some structure near particular
   levels. The round-number clustering ("…00") is the known candidate. `is_round_number`'s
   scale problem is in [`2026-08-11-round-number-scale.md`](2026-08-11-round-number-scale.md).
4. **Overclaim.** "So a null here also rules out every specific rule" (and the driver
   docstring's matching sentence) goes too far, as the Limits section says. A null shows no
   broad dependence. It does not exclude a narrow rule with a small edge.
5. **Fee argument scope.** "No direction-prediction edge on this horizon can pay keel's fees"
   rests on the median move. Read it as "no predictor that fires on typical windows". A selective
   predictor firing only before large moves isn't covered by that arithmetic.
6. **Driver docstring wording.**
   - Fee citation: the docstring names `keel-advanced-trade-is-not-fee-free`, which is not a repo
     document. The reference is issue #836.
   - Shared random stream: both permutation tests in an arm draw from one `random.Random(1109)`
     stream, so the out-of-sample null depends on the 2,000 chi-square shuffles before it. The
     stream is reseeded per arm, so `usd_dollars` and `usd_cents` (same windows) share outcome
     shuffles and have correlated nulls. Bonferroni holds under any dependence.
   - Even train split: if the train set's up/down split were exactly even, tied and unseen
     digits would map to "down". It wasn't, in this run.
7. **Method check, now committed.** Before the real run, the driver's `run_arm` was tested on
   synthetic data: pure noise gave p 0.29 (chi-square) and 0.047 (out of sample), both above the 0.0167 bar; a planted 56/44 split by
   digit gave p 0.003 on both tests (dependence found). The script is
   `2026-11-09-paxg-last-two-digits-method-check.py`.
