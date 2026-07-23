# Bankroll Sizing Comparison: Kelly Criterion Family vs keel's Fixed-Fractional Risk

## Purpose and framing

`keel` is a halal (riba-free), spot-only, long-only, no-leverage crypto trading agent. It currently sizes every trade with fixed-fractional risk sizing (`keel/execution/sizing.py::size`): risk a constant `risk_pct` of equity per trade over the entry-to-stop distance, with a config default of `risk_pct = 0.01` (1%).

This report studies the Kelly Criterion and its relatives (drawn from the `keeks` family of bankroll-growth formulas) **purely as mathematics of optimal capital allocation** -- a Monte Carlo exercise in bankroll-growth arithmetic run against simulated win/loss trade sequences with stdlib-only Python. Nothing here trades real money, wagers on chance for its own sake, or involves interest (riba); it is a study of how fast a bankroll compounds under different constant-risk-fraction rules, applied to keel's own promotion-floor edge numbers, to ask an engineering question: is keel's fixed 1% risk needlessly conservative, or is there a good reason to stay conservative anyway?

## Thesis

keel's PROMOTION FLOOR rule requires win_rate >= 0.55 and R:R (min_rr) >= 1.5 before a strategy is promoted to live trading. For a rule sitting exactly at that floor (p=0.55, b=1.5), full-Kelly risk fraction is:

```
f* = (b*p - q) / b = (1.5 * 0.55 - 0.45) / 1.5 = 0.375 / 1.5 = 0.25   (25% of equity per trade)
half-Kelly  = 0.1250  (12.50%)
quarter-Kelly = 0.0625  (6.25%)
keel's actual risk_pct = 0.01  (1.00%)  ~= 4.0% of full Kelly
```

The question: is keel leaving growth on the table by risking only ~4% of the full-Kelly-implied fraction at its own promotion floor, or is sub-Kelly sizing correct once you account for estimation error in `p`/`b`, correlation between trades, and drawdown pain that a pure log-growth-maximizer ignores?

## Method

Two deterministic (seeded) Monte Carlo experiments, implemented in `simulate.py` next to this report, using only the Python standard library (`random`, `statistics`). Every path uses `random.Random(seed)` with an explicit integer seed; strategies compared within the same experiment share seeds per path index (common random numbers), so differences between strategies reflect sizing, not differing luck. Money is modeled as `float` (this is an educational sim, not the Decimal-only production `keel` sizing code). A trade wins with probability `p`, paying `+b * f * bankroll` where `f` is the fraction risked and `b` is the reward:risk multiple; a loss costs `f * bankroll`. A path is considered ruined and stopped once bankroll falls to or below $1 (bankroll cannot go negative under fractional betting, but going effectively to zero is treated as ruin).

## Experiment 1: reproducing the keeks binary comparison

Setup: 1000 bets, p=0.55, even-money (b=1.0), initial bankroll $1,000, 500 independent seeded paths per strategy. Strategies: Full Kelly, Half Kelly, Quarter Kelly, Fixed-1% (keel), CPPI (floor ratchets at 80% of peak equity, multiplier=3), Naive-flat ($10 constant stake), and Drawdown-adjusted Kelly (scales full Kelly to zero as current drawdown approaches a 20% tolerance ceiling).

| Strategy | Median terminal | Mean terminal | Stdev terminal | Median max DD | Ruin rate |
|---|---|---|---|---|---|
| Full Kelly | $122,449.19 | $16,950,828 | $122,915,963 | 89.55% | 0.00% |
| Half Kelly (0.5) | $38,571.91 | $165,237.73 | $418,200.53 | 61.02% | 0.00% |
| Quarter Kelly (0.25) | $8,481.72 | $12,625.86 | $12,188.01 | 35.53% | 0.00% |
| Fixed-1% (keel) | $2,534.59 | $2,737.90 | $930.42 | 15.32% | 0.00% |
| CPPI (floor=0.8, m=3) | $640.00 | $1,560.75 | $6,300.56 | 60.00% | 0.00% |
| Naive-flat ($10) | $1,980.00 | $2,003.84 | $324.11 | 11.37% | 0.00% |
| Drawdown-adj Kelly (max_dd=0.20) | $993.44 | $1,225.88 | $637.01 | 20.00% | 0.00% |

**Reading this table**: Full Kelly has the highest median/mean terminal wealth, but also the widest dispersion (stdev) and the deepest typical drawdowns -- the classic Kelly trait of being growth-optimal in expectation while remaining a psychologically brutal ride. Half- and Quarter-Kelly trade away some terminal wealth for a large cut in drawdown depth and variance -- this is the textbook "why half-Kelly" lesson the `keeks` library is built to demonstrate. keel's Fixed-1% sits far below all Kelly variants on terminal wealth because it never lets its risk keep pace with a compounding bankroll's *edge*, but it also never comes close to the Kelly variants' drawdowns. CPPI at multiplier=3 with an 80%-of-peak floor risks a large fraction of the cushion (60% of bankroll at a fresh high) -- well above this edge's Kelly-optimal level -- and its results show the cost of over-levering an insurance-style rule. Naive-flat ($10) decays into an ever-shrinking fraction of a growing bankroll (or a growing fraction of a shrinking one), producing its own distinct, non-Kelly growth curve.

## Experiment 2: risk_pct vs the Kelly family at keel's own edge numbers

Setup: 200-trade bootstrap sequences, 500 seeded paths, initial bankroll $1,000. Each trade wins with probability `p` paying `+b * (risk_pct * equity)`, else loses `risk_pct * equity`. Two edge profiles: (A) the promotion floor itself, p=0.55, b=1.5; (B) a stronger edge, p=0.58, b=2.0. Sizing levels are constant risk fractions: keel-1% (0.01), and the Quarter-/Half-/Full-Kelly fractions implied by each profile's own p and b. For each profile, two worlds are simulated: **p correct** (the realized win rate matches what sizing assumed) and **p over-estimated by 0.05** (sizing was computed assuming the stated p, but the true win rate actually realized is 5 percentage points lower -- an estimation-error stress test).

### Profile A (floor edge: p=0.55, b=1.5)

Kelly fractions for this profile: Quarter-Kelly=6.25%, Half-Kelly=12.50%, Full-Kelly=25.00%, keel-1%=1.00%.

| Sizing level | Risk fraction | World | Median terminal multiple | Median max DD | Worst max DD | Ruin rate |
|---|---|---|---|---|---|---|
| keel-1% | 1.00% | p correct | 2.030x | 6.90% | 19.18% | 0.00% |
| Quarter-Kelly | 6.25% | p correct | 49.142x | 38.81% | 76.00% | 0.00% |
| Half-Kelly | 12.50% | p correct | 720.771x | 65.60% | 95.44% | 0.00% |
| Full-Kelly | 25.00% | p correct | 5076.555x | 93.20% | 99.94% | 0.00% |
| keel-1% | 1.00% | p over-estimated by 0.05 | 1.622x | 8.75% | 24.70% | 0.00% |
| Quarter-Kelly | 6.25% | p over-estimated by 0.05 | 12.273x | 46.42% | 87.20% | 0.00% |
| Half-Kelly | 12.50% | p over-estimated by 0.05 | 46.150x | 75.15% | 99.16% | 0.00% |
| Full-Kelly | 25.00% | p over-estimated by 0.05 | 21.697x | 97.46% | 100.00% | 3.60% |

### Profile B (stronger edge: p=0.58, b=2.0)

Kelly fractions for this profile: Quarter-Kelly=9.25%, Half-Kelly=18.50%, Full-Kelly=37.00%, keel-1%=1.00%.

| Sizing level | Risk fraction | World | Median terminal multiple | Median max DD | Worst max DD | Ruin rate |
|---|---|---|---|---|---|---|
| keel-1% | 1.00% | p correct | 4.150x | 5.87% | 16.55% | 0.00% |
| Quarter-Kelly | 9.25% | p correct | 78446.605x | 45.49% | 82.57% | 0.00% |
| Half-Kelly | 18.50% | p correct | 148341260.795x | 73.33% | 97.48% | 0.00% |
| Full-Kelly | 37.00% | p correct | 40467861601.408x | 96.73% | 99.98% | 0.20% |
| keel-1% | 1.00% | p over-estimated by 0.05 | 3.079x | 6.82% | 15.73% | 0.00% |
| Quarter-Kelly | 9.25% | p over-estimated by 0.05 | 5443.234x | 50.53% | 81.28% | 0.00% |
| Half-Kelly | 18.50% | p over-estimated by 0.05 | 823440.800x | 80.22% | 98.01% | 0.00% |
| Full-Kelly | 37.00% | p over-estimated by 0.05 | 1566835.013x | 98.60% | 100.00% | 0.60% |

## What this means for keel

**Is 1% too timid?** Mathematically, yes, relative to the growth-maximizing Kelly fraction: at the promotion floor (p=0.55, b=1.5) full Kelly is 25% of equity per trade, and keel's 1% is roughly 4% of that. In the "p correct" worlds of Experiment 2, every Kelly-family fraction (even Quarter-Kelly) compounds to a dramatically larger median terminal multiple than keel-1% over 200 trades, because 1% barely lets a real edge compound -- the bankroll grows close to linearly rather than geometrically at that scale. Purely as an optimal-growth-rate statement, the thesis holds: keel is far to the conservative side of the Kelly curve.

**Does the estimation-error run defend sub-Kelly?** Yes, and this is the more important half of the story. In the "p over-estimated by 0.05" worlds, Full-Kelly's edge assumption breaks: at the floor profile (b=1.5, breakeven p=0.40), an assumed p=0.55 with a true p=0.50 is still a real edge -- full Kelly at the *true* p=0.50 would be ~16.7% (down from the 25% it was sized at), not zero -- but Full-Kelly was sized as if the edge were 8-plus points thicker than it actually is, and that overbetting shows up directly in the numbers: median terminal multiple collapses from 5077x ("p correct") to 22x ("p over-estimated"), and a ruin rate that was 0.0% becomes 3.60%. Half- and Quarter-Kelly degrade far more gracefully under the identical misestimation (their ruin rates stay at 0.0%), because they were never betting the full assumed edge in the first place -- the classic argument for sub-Kelly sizing is that it functions as a margin of safety against exactly the kind of parameter error a live trading system cannot avoid (p and b are estimated from a finite, noisy backtest sample, not known constants). keel's actual 1%, while far more conservative than even Quarter-Kelly, sits on the same side of that argument as the fractional-Kelly strategies: it is far more robust to an over-optimistic edge estimate than Full-Kelly is, just at a much larger cost in forgone growth.

**Net read**: the honest conclusion is that keel's 1% is not "wrong" -- it is an extreme point on the same sub-Kelly safety spectrum that Half- and Quarter-Kelly occupy, just pushed much further toward safety than the math alone would require. If keel's backtested p and b estimates were trustworthy point estimates with no correlation between trades, something in the Quarter-Kelly neighborhood (order of 5-6% at the floor edge) would capture most of the available growth while still being far more robust to estimation error than Full- or Half-Kelly. keel's actual 1% leaves a substantial amount of that growth unclaimed. Whether closing some of that gap is worth it depends on factors this simulation does not model (see Assumptions below) -- most importantly, real trades are not independent, identically-distributed coin flips, and a backtest's p/b point estimates carry real sampling uncertainty that a single-scenario stress test can only gesture at.

## Assumptions and honest limitations

- **Independent, i.i.d. trades.** The simulation treats every trade as an independent Bernoulli draw with fixed p and b. Real crypto trades from correlated strategies (e.g. multiple concurrent BTC/ETH positions moving together in a market-wide drawdown) violate this; correlated losses compound faster than this model's math accounts for, which understates the real risk of any of the higher-fraction strategies (Full/Half Kelly, CPPI at m=3).
- **Known b, no fees/slippage.** `b` (R:R) is treated as a known constant per trade; trading fees, slippage, and spread are not modeled. Real R:R realized on a live book is noisier and typically worse than backtested R:R.
- **A single, fixed estimation-error stress test.** The "p over-estimated by 0.05" world tests one specific magnitude of misestimation, not a distribution over possible estimation errors. It illustrates the *direction* of the Full-Kelly fragility argument, not a calibrated probability of it occurring.
- **No position limits, correlation caps, or per-order/per-day caps.** keel's real guards (order caps, day caps, portfolio-level exposure limits) are not modeled here; they are additional risk controls that a real deployment would layer on top of whatever risk_pct is chosen, and they change the practical consequences of raising risk_pct.
- **Float money, not Decimal.** This sim uses `float` for bankroll math for simplicity; keel's real sizing code (`keel/execution/sizing.py`) is Decimal-only by design, because money should never touch float in the production path. That distinction does not change the qualitative conclusions here but is worth flagging.
- **This is not a recommendation to change keel's risk_pct.** The result is a mathematical observation about the growth/safety tradeoff at different Kelly fractions, not a specific proposed new value; any change to keel's actual risk_pct would need its own review against keel's real guard rails, correlation across live positions, and backtest confidence -- none of which this script attempts to quantify.
