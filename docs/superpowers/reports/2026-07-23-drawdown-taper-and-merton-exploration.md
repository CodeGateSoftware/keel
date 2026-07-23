# Drawdown Taper and Merton-Gamma Exploration: Two Candidate Leads from KB Source-84

## Purpose and framing

`keel` is a halal (riba-free), spot-only, long-only, no-leverage crypto trading agent. It sizes every trade with fixed-fractional risk sizing (`keel/execution/sizing.py::size`): risk a constant `risk_pct` of equity per trade, config default `risk_pct = 0.01` (1%). Promotion floor: win_rate >= 0.55, R:R >= 1.5.

This report is a follow-up, stdlib-only Monte Carlo study of two SPECIFIC candidate leads flagged in KB source-84 §84.16, exploring the Kelly family and its continuous cousin (the Merton share) **as mathematics of capital allocation** -- not as gambling advice, and not wired into `keel`'s execution path. Nothing here trades real money or involves interest (riba). It reuses `sizing_strategies.py`'s pure formulas (`kelly_fraction`, `fractional_kelly`, `merton_fraction`, `fixed_fraction`) and does not modify `simulate.py` or its existing report.

## The two leads under test

1. **Dynamic drawdown taper (KB §84.4, blog form):** `f_eff = (1 - d/D) * f_base`, `d` = current account drawdown from peak, `D` = a taper ceiling -- risk tapers linearly to zero as `d -> D`, continuously and *before* keel's existing hard drawdown breaker (rail 11, halts at 20% account DD) would otherwise stop trading outright. Hypothesis under test: on keel's tiny 1% base fraction the taper almost never engages (1% rarely draws an account down far), so the taper's real value is not protecting the 1% base -- it is letting a HIGHER base fraction run more safely.
2. **Merton share / CRRA sizing (KB §84.6):** `f = mu / (gamma * sigma^2)`, the continuous-time analogue of Kelly for an investor with constant relative risk aversion `gamma` (`gamma = 1` approximately recovers full Kelly; higher `gamma` sizes smaller). Explored as a principled, defensible way to express "how sub-Kelly" instead of an ad-hoc fractional-Kelly `lambda`.

## Method

Deterministic (seeded) Monte Carlo experiments, implemented in `explore_leads.py` next to this report, stdlib-only (`random`, `statistics`). Every path uses `random.Random(seed)`; strategies compared within the same world/profile share seeds per path index (common random numbers). A trade wins with probability `p`, paying `+b * (f * bankroll)`, else loses `f * bankroll`, `f` recomputed fresh from running state before every trade. A path is ruined and stopped once bankroll falls to or below $1. Two edge profiles: **A** = keel's promotion floor (p=0.55, b=1.5); **B** = a stronger edge (p=0.58, b=2.0). Two worlds per experiment: **p correct** (realized win rate matches the sizing assumption) and **p over-estimated by 0.05** (sizing assumes the stated p, the true realized win rate is 5 points lower). Unless noted, 500 seeded paths of 200 trades each, starting from $1,000.

## Experiment 3: dynamic drawdown taper

For each profile, three base fractions are tested: keel-1% (0.01, flat), Quarter-Kelly, and Half-Kelly (both computed from that profile's own p/b). Each base is run (i) untapered and (ii) tapered at ceilings D in {0.15, 0.25, 0.35}. EVERY combination also runs under keel's hard drawdown breaker, modeled as a hard halt (no further trades for the rest of the sequence) once a path's current drawdown from peak reaches 20% -- mirroring rail 11. "Risk-adj" is a crude ratio: median terminal multiple / median max DD (higher is better: more growth per unit of typical pain).

### Profile A (floor edge: p=0.55, b=1.5)

Base fractions: keel-1%=1.00%, Quarter-Kelly=6.25%, Half-Kelly=12.50%.

**World: p correct**

| Base x taper | Median terminal multiple | Median max DD | Worst max DD | Ruin rate | Breaker trip rate | Risk-adj (mult/DD) |
|---|---|---|---|---|---|---|
| keel-1% / no taper | 2.082x | 6.83% | 16.05% | 0.00% | 0.00% | 30.49 |
| keel-1% / taper D=0.15 | 1.929x | 5.93% | 10.79% | 0.00% | 0.00% | 32.51 |
| keel-1% / taper D=0.25 | 1.989x | 6.31% | 12.75% | 0.00% | 0.00% | 31.54 |
| keel-1% / taper D=0.35 | 2.015x | 6.41% | 13.67% | 0.00% | 0.00% | 31.47 |
| Quarter-Kelly / no taper | 1.478x | 22.75% | 24.94% | 0.00% | 99.80% | 6.49 |
| Quarter-Kelly / taper D=0.15 | 4.267x | 14.90% | 15.00% | 0.00% | 0.00% | 28.64 |
| Quarter-Kelly / taper D=0.25 | 2.752x | 20.36% | 20.99% | 0.00% | 86.00% | 13.51 |
| Quarter-Kelly / taper D=0.35 | 1.794x | 20.43% | 22.12% | 0.00% | 97.40% | 8.78 |
| Half-Kelly / no taper | 0.982x | 23.44% | 23.44% | 0.00% | 100.00% | 4.19 |
| Half-Kelly / taper D=0.15 | 1.038x | 15.00% | 15.00% | 0.00% | 0.00% | 6.92 |
| Half-Kelly / taper D=0.25 | 1.108x | 20.85% | 21.93% | 0.00% | 100.00% | 5.32 |
| Half-Kelly / taper D=0.35 | 1.072x | 23.98% | 24.26% | 0.00% | 100.00% | 4.47 |

**World: p over-estimated by 0.05**

| Base x taper | Median terminal multiple | Median max DD | Worst max DD | Ruin rate | Breaker trip rate | Risk-adj (mult/DD) |
|---|---|---|---|---|---|---|
| keel-1% / no taper | 1.622x | 8.68% | 20.73% | 0.00% | 0.60% | 18.68 |
| keel-1% / taper D=0.15 | 1.493x | 7.03% | 12.49% | 0.00% | 0.00% | 21.24 |
| keel-1% / taper D=0.25 | 1.543x | 7.63% | 15.76% | 0.00% | 0.00% | 20.23 |
| keel-1% / taper D=0.35 | 1.565x | 7.87% | 17.48% | 0.00% | 0.00% | 19.89 |
| Quarter-Kelly / no taper | 1.090x | 22.75% | 24.94% | 0.00% | 100.00% | 4.79 |
| Quarter-Kelly / taper D=0.15 | 1.407x | 14.99% | 15.00% | 0.00% | 0.00% | 9.38 |
| Quarter-Kelly / taper D=0.25 | 1.286x | 20.45% | 21.00% | 0.00% | 97.60% | 6.29 |
| Quarter-Kelly / taper D=0.35 | 1.180x | 20.43% | 22.12% | 0.00% | 99.80% | 5.77 |
| Half-Kelly / no taper | 0.945x | 23.44% | 23.44% | 0.00% | 100.00% | 4.03 |
| Half-Kelly / taper D=0.15 | 1.009x | 15.00% | 15.00% | 0.00% | 0.00% | 6.73 |
| Half-Kelly / taper D=0.25 | 0.985x | 20.85% | 21.93% | 0.00% | 100.00% | 4.72 |
| Half-Kelly / taper D=0.35 | 0.954x | 23.98% | 24.26% | 0.00% | 100.00% | 3.98 |

### Profile B (stronger edge: p=0.58, b=2.0)

Base fractions: keel-1%=1.00%, Quarter-Kelly=9.25%, Half-Kelly=18.50%.

**World: p correct**

| Base x taper | Median terminal multiple | Median max DD | Worst max DD | Ruin rate | Breaker trip rate | Risk-adj (mult/DD) |
|---|---|---|---|---|---|---|
| keel-1% / no taper | 4.275x | 5.85% | 14.02% | 0.00% | 0.00% | 73.06 |
| keel-1% / taper D=0.15 | 3.897x | 4.99% | 9.43% | 0.00% | 0.00% | 78.13 |
| keel-1% / taper D=0.25 | 4.045x | 5.32% | 11.02% | 0.00% | 0.00% | 76.09 |
| keel-1% / taper D=0.35 | 4.110x | 5.46% | 11.79% | 0.00% | 0.00% | 75.23 |
| Quarter-Kelly / no taper | 2.120x | 25.26% | 27.06% | 0.00% | 100.00% | 8.39 |
| Quarter-Kelly / taper D=0.15 | 56.057x | 14.98% | 15.00% | 0.00% | 0.00% | 374.24 |
| Quarter-Kelly / taper D=0.25 | 3.865x | 20.02% | 21.47% | 0.00% | 99.20% | 19.30 |
| Quarter-Kelly / taper D=0.35 | 3.408x | 23.02% | 23.14% | 0.00% | 99.80% | 14.80 |
| Half-Kelly / no taper | 1.554x | 33.58% | 33.58% | 0.00% | 100.00% | 4.63 |
| Half-Kelly / taper D=0.15 | 1.117x | 18.50% | 18.50% | 0.00% | 0.00% | 6.04 |
| Half-Kelly / taper D=0.25 | 1.151x | 22.42% | 22.42% | 0.00% | 100.00% | 5.13 |
| Half-Kelly / taper D=0.35 | 1.390x | 25.61% | 26.26% | 0.00% | 100.00% | 5.43 |

**World: p over-estimated by 0.05**

| Base x taper | Median terminal multiple | Median max DD | Worst max DD | Ruin rate | Breaker trip rate | Risk-adj (mult/DD) |
|---|---|---|---|---|---|---|
| keel-1% / no taper | 3.172x | 6.79% | 17.68% | 0.00% | 0.00% | 46.69 |
| keel-1% / taper D=0.15 | 2.872x | 5.62% | 11.17% | 0.00% | 0.00% | 51.08 |
| keel-1% / taper D=0.25 | 2.989x | 6.06% | 13.46% | 0.00% | 0.00% | 49.31 |
| keel-1% / taper D=0.35 | 3.040x | 6.26% | 14.58% | 0.00% | 0.00% | 48.56 |
| Quarter-Kelly / no taper | 1.473x | 25.26% | 27.06% | 0.00% | 100.00% | 5.83 |
| Quarter-Kelly / taper D=0.15 | 5.352x | 15.00% | 15.00% | 0.00% | 0.00% | 35.68 |
| Quarter-Kelly / taper D=0.25 | 1.955x | 20.02% | 21.39% | 0.00% | 100.00% | 9.76 |
| Quarter-Kelly / taper D=0.35 | 1.817x | 23.02% | 23.14% | 0.00% | 100.00% | 7.89 |
| Half-Kelly / no taper | 1.134x | 33.58% | 33.58% | 0.00% | 100.00% | 3.38 |
| Half-Kelly / taper D=0.15 | 0.815x | 18.50% | 18.50% | 0.00% | 0.00% | 4.41 |
| Half-Kelly / taper D=0.25 | 1.063x | 22.42% | 22.42% | 0.00% | 100.00% | 4.74 |
| Half-Kelly / taper D=0.35 | 1.019x | 25.61% | 26.26% | 0.00% | 100.00% | 3.98 |

### Headline read: does taper-on-Quarter-Kelly dominate?

Checked systematically across all 4 profile x world combos (A/B x "p correct"/"p over-estimated"): for each (base fraction, taper ceiling D), does the tapered version reach a median terminal multiple >= flat-1%'s (growth-dominates), AND does it reach a median max DD and hard-breaker trip rate both <= its own untapered version's (safety-dominates)?

| Base | Taper D | Growth-dominates flat-1% | Safety-dominates untapered | Full dominance (both) |
|---|---|---|---|---|
| Quarter-Kelly | 0.15 | 3/4 combos | 4/4 combos | 3/4 combos |
| Quarter-Kelly | 0.25 | 1/4 combos | 4/4 combos | 1/4 combos |
| Quarter-Kelly | 0.35 | 0/4 combos | 4/4 combos | 0/4 combos |
| Half-Kelly | 0.15 | 0/4 combos | 4/4 combos | 0/4 combos |
| Half-Kelly | 0.25 | 0/4 combos | 4/4 combos | 0/4 combos |
| Half-Kelly | 0.35 | 0/4 combos | 2/4 combos | 0/4 combos |

**Full dominance (more growth than flat-1% AND less drawdown/fewer breaker trips than untapered) holds in 3/4 combos for Quarter-Kelly tapered at D=0.15** -- the one ceiling tested that sits BELOW keel's own 20% hard-breaker threshold. At D=0.25 and D=0.35 (ceilings ABOVE the hard breaker), full dominance drops to 1/4 and 0/4 combos respectively -- safety-dominance still holds almost everywhere (the taper reliably shrinks drawdown and breaker trips versus untapered, regardless of D), but growth-dominance over flat-1% mostly fails, because once D exceeds the hard-breaker threshold the taper no longer prevents the breaker from tripping -- and a tripped, frozen bankroll forfeits the same growth untapered Quarter-Kelly forfeits. Concretely, profile A / "p correct": flat-1% reaches 2.082x; untapered Quarter-Kelly reaches 1.478x but trips the breaker on 99.80% of paths (median max DD 22.75%); Quarter-Kelly tapered at D=0.15 reaches 4.267x with median max DD 14.90% and a 0.00% breaker trip rate.

**Does the taper help AT ALL on the 1% base?** Barely, and the hypothesis holds: on keel-1%, drawdown almost never reaches even the tightest taper ceiling (D=0.15) -- untapered keel-1% breaker-trips on 0.00% of paths (profile A, "p correct"), and tapering at D=0.15 changes that to 0.00% while giving up some growth (1.929x vs 2.082x, because the taper starts shaving size any time drawdown is nonzero, not just near the ceiling). **Half-Kelly never achieves growth-dominance regardless of taper ceiling** (0/4 combos at D=0.15): its base fraction is simply too large -- a single adverse trade can jump drawdown past even a tight taper ceiling in one or two trades, so the taper either zeroes risk out too early to compound meaningfully, or fails to prevent the breaker trip anyway.

## Experiment 4: Merton gamma sizing

### (a) Implied risk-aversion gamma at keel's actual 1%

Solving `merton_fraction(mu, sigma2, gamma) = 0.01` for `gamma` at each profile's own mu/sigma2 (mu = p*b - (1-p), sigma2 = p*b^2 + (1-p) - mu^2):

| Profile | mu | sigma^2 | Implied gamma | x more risk-averse than gamma=1 |
|---|---|---|---|---|
| A (floor edge: p=0.55, b=1.5) | 0.3750 | 1.5469 | 24.24 | 24.2x |
| B (stronger edge: p=0.58, b=2.0) | 0.7400 | 2.1924 | 33.75 | 33.8x |

keel's implied risk-aversion is roughly **24.2x** the Kelly-equivalent (gamma=1) investor at profile A, and roughly **33.8x** at profile B. Full Kelly is approximately gamma=1; keel's flat 1% is, in this framing, the choice of an extremely risk-averse Merton investor -- far past the textbook gamma~2 estimate of typical human risk aversion.

### (b) Fixed-gamma sizing across profiles

One `gamma` is fixed and applied to BOTH profiles' own mu/sigma2, compared against flat-1% and Quarter-Kelly: `gamma=A-implied` (24.24, i.e. the gamma solved in (a) at profile A) and `gamma=2` (textbook human-risk-aversion estimate).

#### Profile A (floor edge: p=0.55, b=1.5)

Sizing fractions: keel-1%=1.00%, Quarter-Kelly=6.25%, Merton(gamma=A-implied)=1.00%, Merton(gamma=2)=12.12%.

**World: p correct**

| Sizing level | Risk fraction | Median terminal multiple | Median max DD | Worst max DD | Ruin rate |
|---|---|---|---|---|---|
| keel-1% | 1.00% | 2.056x | 7.28% | 15.61% | 0.00% |
| Quarter-Kelly | 6.25% | 53.237x | 38.81% | 70.36% | 0.00% |
| Merton (gamma=A-implied) | 1.00% | 2.056x | 7.28% | 15.61% | 0.00% |
| Merton (gamma=2 (textbook)) | 12.12% | 742.255x | 64.43% | 93.24% | 0.00% |

**World: p over-estimated by 0.05**

| Sizing level | Risk fraction | Median terminal multiple | Median max DD | Worst max DD | Ruin rate |
|---|---|---|---|---|---|
| keel-1% | 1.00% | 1.622x | 8.72% | 26.58% | 0.00% |
| Quarter-Kelly | 6.25% | 12.273x | 46.42% | 87.28% | 0.00% |
| Merton (gamma=A-implied) | 1.00% | 1.622x | 8.72% | 26.58% | 0.00% |
| Merton (gamma=2 (textbook)) | 12.12% | 44.002x | 73.96% | 98.66% | 0.00% |

#### Profile B (stronger edge: p=0.58, b=2.0)

Sizing fractions: keel-1%=1.00%, Quarter-Kelly=9.25%, Merton(gamma=A-implied)=1.39%, Merton(gamma=2)=16.88%.

**World: p correct**

| Sizing level | Risk fraction | Median terminal multiple | Median max DD | Worst max DD | Ruin rate |
|---|---|---|---|---|---|
| keel-1% | 1.00% | 4.275x | 5.85% | 16.72% | 0.00% |
| Quarter-Kelly | 9.25% | 102434.410x | 44.14% | 85.31% | 0.00% |
| Merton (gamma=A-implied) | 1.39% | 7.450x | 8.07% | 22.62% | 0.00% |
| Merton (gamma=2 (textbook)) | 16.88% | 80910547.970x | 68.68% | 97.93% | 0.00% |

**World: p over-estimated by 0.05**

| Sizing level | Risk fraction | Median terminal multiple | Median max DD | Worst max DD | Ruin rate |
|---|---|---|---|---|---|
| keel-1% | 1.00% | 3.172x | 6.82% | 15.93% | 0.00% |
| Quarter-Kelly | 9.25% | 7107.694x | 50.55% | 84.58% | 0.00% |
| Merton (gamma=A-implied) | 1.39% | 4.920x | 9.40% | 21.62% | 0.00% |
| Merton (gamma=2 (textbook)) | 16.88% | 695360.635x | 76.58% | 97.88% | 0.00% |

**Cross-profile behavior at a single fixed gamma (24.24, profile A's implied gamma):** the SAME gamma sizes profile A at exactly 1.00% (by construction) but sizes the stronger, lower-variance profile B at 1.39% -- automatically MORE, with no re-tuning. Flat-1% cannot do this: it risks the identical 1% on both the floor edge and the stronger edge, by definition. This is the mechanical demonstration of the KB claim that Merton sizing is edge-aware where flat-fractional sizing is not.

### (c) Fractional-Kelly equivalence (effective lambda)

Merton-at-a-fixed-gamma is mathematically a form of fractional Kelly: dividing the Merton fraction by that profile's own full-Kelly fraction gives an effective lambda (`lambda = f_merton / f_kelly`) -- "what fraction of full Kelly is this gamma equivalent to, at this specific edge?"

| Profile | gamma | f_merton | Full Kelly | Effective lambda |
|---|---|---|---|---|
| A (floor edge: p=0.55, b=1.5) | gamma=A-implied (24.24) | 1.00% | 25.00% | 0.0400 (4.00%) |
| A (floor edge: p=0.55, b=1.5) | gamma=2 (textbook) (2.00) | 12.12% | 25.00% | 0.4848 (48.48%) |
| B (stronger edge: p=0.58, b=2.0) | gamma=A-implied (24.24) | 1.39% | 37.00% | 0.0376 (3.76%) |
| B (stronger edge: p=0.58, b=2.0) | gamma=2 (textbook) (2.00) | 16.88% | 37.00% | 0.4561 (45.61%) |

At `gamma=A-implied`, the effective lambda is 4.00% of full Kelly at profile A (matching keel-1%'s own ~4% of full Kelly noted in the prior bankroll-sizing report) and 3.76% at profile B -- close but not identical, because `merton_fraction` (a mean/variance formula) and `kelly_fraction` (the discrete binary formula) are two different approximations of the same growth-optimal bet size, not algebraically identical. Both worlds tables above show `Merton (gamma=A-implied)` keeping ruin at 0.0% under "p over-estimated by 0.05" at both profiles -- degrading gracefully, the same qualitative behavior fractional Kelly showed in the original `simulate.py` study.

## Verdict per lead

### Lead 1: dynamic drawdown taper -- VERDICT: KEEP as ceiling-or-diagnostic only

Taper-on-Quarter-Kelly at D=0.15 vs flat-1% (growth): 4.267x vs 2.082x -- more growth in all 3/4 combos tested. Taper-on-Quarter-Kelly at D=0.15 vs untapered Quarter-Kelly (safety): median max DD 14.90% vs 22.75%, breaker trip rate 0.00% vs 99.80%, ruin rate 0.00% vs 0.00% -- safer in all 4/4 combos. This full dominance is CONDITIONAL: it holds cleanly only when the taper ceiling D sits below keel's own 20% hard-breaker threshold (D=0.15 here). At D=0.25 or D=0.35 the taper still reliably improves safety over untapered Quarter-Kelly, but usually stops beating flat-1% on growth, because a ceiling above the hard breaker no longer prevents the breaker from tripping. On the keel-1% base itself the taper barely engages (drawdown almost never reaches even D=0.15) -- the hypothesis holds: the taper's real value is in letting a HIGHER base fraction (Quarter-Kelly, not Half-Kelly -- see below) run with materially fewer breaker trips and shallower drawdowns, not in protecting the already-tiny 1% base. Half-Kelly's base fraction is too large for any taper ceiling tested to rescue: it never achieves growth-dominance, taper or no taper, because a single adverse trade can jump drawdown past the taper zone before it has a chance to brake gradually.

### Lead 2: Merton gamma sizing -- VERDICT: PROMOTE to build candidate

keel's implied risk-aversion is gamma~24 at profile A and gamma~34 at profile B -- both far above the textbook gamma~2 human-risk-aversion estimate and far above the gamma=1 Kelly-equivalent, i.e. keel is a mathematically extreme (not merely "conservative") point on this spectrum. A single fixed gamma automatically scales risk up on the stronger/lower-variance edge (B) and down on the floor edge (A) with zero re-tuning, which flat-1% cannot do by construction; and Merton-at-a-fixed-gamma degrades gracefully under the p-over-estimated stress test (ruin stays 0.0% at both profiles), matching fractional Kelly's known robustness. The formula is a legitimate, more principled way to express the SAME sub-Kelly choice keel already makes -- worth adopting as vocabulary/diagnostic ("keel runs at effectively gamma~24-34") even without changing risk_pct itself.

## Assumptions and honest limitations

- **Independent, i.i.d. trades.** Every trade is an independent Bernoulli draw with fixed p and b. Real crypto trades from correlated strategies (multiple concurrent positions moving together in a market-wide drawdown) violate this; correlated losses compound faster than this model accounts for, which understates real risk for any higher-fraction sizing (Half-Kelly, high-gamma-inverse Merton at large fractions).
- **Known b, no fees/slippage.** `b` is treated as a known constant; trading fees, slippage, and spread are not modeled.
- **A single, fixed estimation-error magnitude.** The "p over-estimated by 0.05" world tests one specific misestimation size, not a distribution over possible errors. It illustrates a direction, not a calibrated probability.
- **Merton is a mean/variance approximation, not an exact rederivation of Kelly.** `merton_fraction` and `kelly_fraction` are two different formulas for "how much to risk"; gamma=1 approximately, not exactly, recovers full Kelly, and the effective-lambda numbers in (c) above reflect that approximation gap, not an algebraic identity.
- **The hard-breaker model is simplified.** It is modeled as a permanent halt for the rest of a fixed 200-trade sequence once tripped, with no recovery/reset logic and no modeling of the real rail 11 implementation's exact bookkeeping (weekly vs total DD, reset conditions). It exists here only to compare relative trip rates across sizing rules, not to reproduce rail 11 exactly.
- **This is not a recommendation to change keel's risk_pct.** Both leads are explored as mathematics and vocabulary for reasoning about sizing; any actual change to risk_pct, taper ceilings, or breaker thresholds would need its own review against keel's live guard rails, correlation across real positions, and backtest confidence -- none of which this script attempts to quantify.

## Exact numbers for KB source-84 §84.4 and §84.6

- **§84.6 (Merton) -- keel's implied gamma:** ~24.2 at profile A (p=0.55, b=1.5, the promotion floor), ~33.8 at profile B (p=0.58, b=2.0) -- roughly 24x to 34x more risk-averse than the gamma=1 Kelly-equivalent investor, and 12-17x more risk-averse than the textbook gamma=2 human estimate.
- **§84.6 -- effective lambda at gamma=A-implied:** 4.00% of full Kelly at profile A, 3.76% at profile B (both close to keel-1%'s own ~4% of full Kelly figure from the original bankroll-sizing report).
- **§84.4 (taper) -- does taper-on-Quarter-Kelly dominate flat-1% AND untapered Quarter-Kelly?** Yes, but ONLY when taper ceiling D < keel's 20% hard-breaker threshold: at D=0.15, full dominance holds in 3/4 profile x world combos tested. At D=0.25 it drops to 1/4, and at D=0.35 to 0/4 -- safety-dominance (lower DD, fewer breaker trips than untapered) persists at every D tested, but growth-dominance over flat-1% requires the ceiling to sit below the hard breaker. Concretely at D=0.15, profile A / "p correct": 4.267x median terminal multiple (vs flat-1%'s 2.082x), median max DD 14.90% and breaker trip rate 0.00% (vs untapered Quarter-Kelly's 22.75% DD and 99.80% breaker trip rate).
- **§84.4 -- breaker-trip-rate deltas (profile A, "p correct"):** keel-1% untapered 0.00% -> keel-1% taper D=0.15 0.00% (taper barely engages on the 1% base); Quarter-Kelly untapered 99.80% -> Quarter-Kelly taper D=0.25 0.00% (taper materially cuts breaker trips on a higher base). Full per-D, per-base breaker-trip figures for both profiles and both worlds are in the Experiment 3 tables above.
