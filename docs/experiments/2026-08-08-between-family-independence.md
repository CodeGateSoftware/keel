# Between-family independence — the §80.16 harness exists, and it is calibrated

> **Cost note (added 2026-09-02).** The figures below are priced at the flat 5bp
> slippage floor. [the per-product restatement](2026-09-01-per-product-slippage-restatement.md) later measured that **no
> asset in keel's universe reaches that floor** — the range is 1.1× to 36.8× — so every
> profit factor here is optimistic by roughly 0.09 at the median. **The verdict is
> unaffected:** the correction only ever moves a number *down*, and every result here was
> already negative. Nothing on this page has been rewritten; records are appended to, not
> revised.

**Date:** 2026-08-08
**KB basis:** §80.16 (the measurement), §73.5 (why it is non-optional), §74.3/§58.10a (why arm B
is refuted), §80.10/§80.14 (who the real candidate is)
**Script:** `docs/experiments/2026-08-08-between-family-independence.py`
**Status:** harness build + calibration. **No parameter, rule, or gate changed.**
**Ledger:** `diagnostic_only` — this run influenced no shipped decision.

**Verdict:** the §80.16 pipeline is built and **validated against a published in-repo result to
three decimals**. The between-family measurement it was pointed at is **degenerate**: on daily
bars `rsi_meanrev` takes **zero trades on all five allowlisted assets**, so there is no
relationship to measure. That is a fact about the rule and the bar clock, not a harness fault.

⭐ **The finding is in the third pass, which was not the one this run set out to make.** Pointing
the same harness at the shipped turtle against *itself on a different asset* — same family, same
parameters, same bar clock, only the underlying varying — gives **mean P&L correlation 0.011,
position correlation 0.175, Jaccard 0.144.** Against the cross-horizon 0.508 / 0.585 / 0.510
measured on the same assets by the same method, **asset breadth is roughly an order of magnitude
better than horizon breadth as a source of independent evidence.** §79.2's horizon ladder was
refuted for being 2× worse than a 0.22 benchmark; the allowlist clears that benchmark outright.

Three passes:

| pass | varies | mean P&L corr (closed) | outcome |
|---|---|---:|---|
| between-family | rule family | — | degenerate, arm B never trades |
| cross-horizon (calibration) | lookback | **0.508** (published, reproduced) | validates the pipeline |
| **cross-asset** | **the underlying** | **0.011** | **the finding** |

## Why this ran at all, and what it explicitly is not

§80.16 is the KB's one confirmed hole: four papers, 30,000+ tested rules, two asset classes, 114
years, **zero** between-family correlation measurements. It is a build task. `research/independence.py`
has implemented the five measurements since PR #103 — but **nothing in the repo drives them over
two real rules.** The 2026-07-20 cross-horizon run was ad hoc and left no script behind, so every
future §80.16 question started from zero. This build closes that.

⚠️ **This is not a candidacy test for `rsi_meanrev`, and no number below should be read as one.**
That family is settled-refuted three ways (§74.3): our own sim (16% win, PF 0.17); §58.10a Katz &
McCormick — worst model in a 36-market, 14-year book, *worse than random*; §74.3 Hudson & Urquhart
— **−6.33 bp vs buy-and-hold on Bitcoin, p < 0.01**. The KB instruction is *"Treat RSI mean-reversion
as settled and closed. Build no further variants."* Under §73.5 independence is **necessary, never
sufficient** — an uncorrelated stream with negative edge is still strictly worse than adding
nothing. Arm B is here because it is the only other implemented risk-defined rule, which makes it
a free shakedown.

## Method

Both arms on **`ONE_DAY`**. `RsiMeanReversion` defaults to `timeframe=ONE_HOUR`; `TurtleBreakout`
is fixed `ONE_DAY`. Running them natively would confound **family** separation with **horizon**
separation — and horizon is already measured on these assets at 0.508. Holding the bar clock
constant is what isolates the variable §80.16 asks about. `timeframe` is the only departure from
`RsiMeanReversion`'s constructor defaults; arm A is byte-identical to `keel-live.db` rules 1–5.

Pre-declared before the run (§78.5): assets = the live allowlist; fee 0.006 / slippage 0.0005
(matching `cli._SIM_FEE_PCT`); both P&L conventions reported.

**Arm A reproduces the shipped trade counts exactly** — 13 / 13 / 4 / 6 / 8 for BTC / ETH / PAXG /
ADA / XLM, matching `config.live-sandbox.yaml:32-39`. The harness is wired to the real rule.

## Result 1 — the between-family measurement is degenerate

| asset | bars | A trades | B trades | A days in mkt | B days | both |
|---|---:|---:|---:|---:|---:|---:|
| BTC-USD | 1845 | 13 | **0** | 432 | 0 | 0 |
| ETH-USD | 1845 | 13 | **0** | 300 | 0 | 0 |
| PAXG-USD | 456 | 4 | **0** | 96 | 0 | 0 |
| ADA-USD | 1833 | 6 | **0** | 130 | 0 | 0 |
| XLM-USD | 1833 | 8 | **0** | 80 | 0 | 0 |

All five excluded from the means — named in the output, never silently dropped. `_pearson`
returns 0 for a constant series by design, so averaging those zeros in would have manufactured a
"perfectly independent" headline out of a rule that never traded.

**Why**, from the script's reproduction of `detect()`'s first gate:

| asset | min RSI(14), daily | gate-1 fires in ~5 years |
|---|---:|---:|
| BTC-USD | 15.50 | 4 |
| ETH-USD | 12.86 | 3 |
| ADA-USD | 12.15 | 4 |
| PAXG-USD | 20.74 | **0** |
| XLM-USD | 20.65 | **0** |

`oversold = 20` on a **daily** RSI(14) is close to unreachable on these assets — PAXG and XLM
never printed a sub-20 daily RSI in the entire window. The handful that do fire on BTC/ETH/ADA are
then killed downstream by the support-level gate (3+ touches within 0.5% of the bar's low). The
rule's `ONE_HOUR` default is not incidental; that family needs a fast bar clock to fire at all.

⇒ **A same-horizon between-family measurement against `rsi_meanrev` is not obtainable.** Not
"came out low" — not obtainable. Any number produced by dropping to hourly for arm B would mix
family with horizon and be uninterpretable against §80.16.

## Result 2 — the calibration, which is the finding worth keeping

A between-family figure is only worth reading if the pipeline behind it can reproduce a
cross-horizon figure that is already known. Shipped 40/20 vs 80/40, same five measurements:

| asset | A trd | B trd | Jaccard | pos corr | **P&L corr (closed)** | P&L corr (mtm) | median gap | **published** |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| BTC-USD | 13 | 10 | 0.707 | 0.774 | **0.802** | 0.813 | 0 d | **0.802** |
| ETH-USD | 13 | 8 | 0.702 | 0.810 | **0.934** | 0.759 | 0 d | **0.934** |
| PAXG-USD | 4 | 3 | 0.500 | 0.619 | 0.074 | 0.473 | 4 d | n/a |

**Exact reproduction on both published assets, plus the "median entry gap of zero days" claim.**
The harness is trustworthy.

⭐ **It also settles a methodological question PR #103's write-up left open: which P&L convention
that experiment used.** `closed` (whole net P&L booked to the exit bar, the
`2026-07-20-first-pbo-run.md` convention) reproduces 0.802 / 0.934; mark-to-market gives 0.813 /
0.759. **Future §80.16 tables should stay on `closed` for cross-experiment comparability.** This
is a comparability choice fixed by prior work, not a selection on this run — both columns print
either way.

⚠️ PAXG's two conventions disagree sharply (0.074 vs 0.473) on 4 and 3 trades over a 456-bar
window. At that sample the estimator is noise. It carried no weight in PR #103 either — that
write-up's own note is that *"BTC and ETH carry the conclusion."*

## Result 3 — cross-asset: the shipped turtle against itself, and the reason to expand

Same rule, same parameters, same daily clock. Only the underlying varies. Series aligned on
**common timestamps** — comparing positionally would pair BTC's day 400 with PAXG's day 400,
which are years apart.

| pair | days | Jaccard | pos corr | P&L (mtm) | P&L (closed) | median entry gap |
|---|---:|---:|---:|---:|---:|---:|
| BTC vs ETH | 1845 | 0.321 | 0.374 | 0.301 | 0.036 | 13 d |
| ADA vs XLM | 1833 | 0.288 | 0.430 | 0.117 | −0.001 | 8 d |
| ETH vs ADA | 1833 | 0.270 | 0.402 | 0.101 | −0.000 | 37 d |
| BTC vs ADA | 1833 | 0.210 | 0.334 | 0.117 | 0.076 | 69 d |
| BTC vs XLM | 1833 | 0.162 | 0.329 | −0.022 | −0.001 | 70 d |
| ETH vs XLM | 1833 | 0.142 | 0.246 | 0.011 | −0.000 | 44 d |
| BTC vs PAXG | 456 | 0.049 | **−0.073** | −0.063 | 0.002 | 114 d |
| ETH vs PAXG | 456 | **0.000** | **−0.158** | −0.002 | −0.001 | 45 d |
| PAXG vs ADA | 455 | **0.000** | **−0.088** | 0.002 | 0.002 | 84 d |
| PAXG vs XLM | 455 | **0.000** | **−0.042** | −0.005 | −0.002 | 91 d |
| **mean (10 pairs)** | | **0.144** | **0.175** | **0.056** | **0.011** | |
| *cross-horizon, same method* | | *0.510* | *0.585* | — | *0.508* | *0 d* |

**1. Asset breadth is the independence axis that works — and it is now measured, not assumed.**
Every one of the three measures is 3–45× better than the cross-horizon equivalent. The sharpest
single contrast is the entry gap: adjacent horizons of the same rule open **on the same day**
(median 0), while the same rule on different assets opens **8 to 114 days apart.**

**2. `2026-07-20-minbtl-sizing.md`'s allowlist case rested on an assumption this run tests.**
That table projected 3 assets → ~21 years to sufficiency, 10 assets → ~5, by assuming added
assets multiply trades/year roughly linearly. That is only true if the added trades carry
independent information. At ρ̄ ≈ 0.18 on positions and ≈ 0.01 on P&L, they largely do. **The
expansion case is stronger than it was when it was made.**

**3. PAXG is the most valuable asset in the book, and it is the one with the least history.**
Zero Jaccard against ETH, ADA and XLM — over the common window it is *never* in the market at
the same time as any of them — and negative position correlation against all four cryptos. Gold
is a different factor, as §80.12's ETH↔gold +5.45 already hinted. It also has 456 daily bars
against ~1,830, and 4 trades. **The best diversifier here is the one the evidence base can say
least about.**

**4. Effective breadth: ~2.9 of 5 streams** by `n/(1+(n−1)ρ̄)` on the conservative
position-correlation mean (4.8 of 5 if the P&L mean is used). ⚠️ This is the standard
equicorrelation approximation, **not** §78.2's `N̂ = ρ̂ + (1−ρ̂)·M`, which corrects a *trials*
count and is a different quantity. It is reported for interpretation and is fed into no gate and
no MinBTL computation.

⚠️ **Read the P&L correlations with the sparsity in mind.** These rules are out of the market
75–95% of the time, so both P&L series are mostly joint zeros and the correlation is dominated by
that mass. Jaccard and position correlation are the more robust measures — and all three agree
here, which is the reassuring part. The comparison against cross-horizon is apples-to-apples:
same convention, same sparsity structure, same assets, same script.

## What this changes

**0. The uniform-parameter design is vindicated on the axis that matters, and the ranking of
breadth levers is now empirical.** One parameter set across five assets produces ~2.9–4.8
effective independent streams. Five *per-asset fitted* parameter sets would not — they would be
five fits of the same underlying beta, spending trials budget to manufacture the correlation this
measurement shows the current design avoids. Ordering the three breadth axes by measured mean P&L
correlation on identical methodology: **asset 0.011 → horizon 0.508 → family (unmeasurable, arm
refuted).** §79.1 redirected the frequency plan to breadth without saying *which* breadth; this
says which.

**1. The §80.16 measurement is now a script, not a plan.** Point it at any two `Rule` instances
and it returns all five measurements plus both P&L conventions. That is the reusable asset; the
rsi arm was the shakedown.

**2. Two of keel's four rule kinds are refuted, not merely dormant.** `rsi_meanrev` (§74.3) and
`pullback_continuation` (same dip-buy family, §62.2) are closed with external corroboration.
`RULE_REGISTRY` reads as four options; it is really **one trend-follower, one DCA sleeve, and two
closed files.** Anyone reading the registry as a menu of available diversification is misreading
it — worth a comment at `agent.py:99`.

**3. The real candidate is unbuilt, and now unblocked.** §80.14 closed the MACD family
(`macd_divergence` demoted — never cleared even the nominal level). §80.10 promoted **Liu &
Tsyvinski weekly time-series momentum** to leading second-rule-class candidate. §80.16's own note
is that the economic prior favours it — dense, continuous, centre-of-distribution, ~5–7× shorter
horizon than a 40-day Donchian tail event — *"but the prior is a reason to test this candidate
FIRST, not to skip the test."* When it is implemented it becomes arm B here, and the run stops
being a shakedown.

⚠️ Note for that build: weekly TSM is a **coarser** clock than the daily Turtle, so it will hit
the mirror image of this run's problem. The harness will need a resample step onto a shared daily
index before it can measure a weekly rule against a daily one.

**4. Order of operations for the candidate.** §80.16's independence test answers *"does it add
independent evidence?"*. It does not answer *"does it have edge?"* — and §80.4/§80.8/§80.15 give
three separate demonstrations of rules that passed an in-sample correction and died forward. Run
§58.11's random-entry control first; independence second. A rule that fails the null does not need
an independence measurement.

## Caveats

- In-sample, one window, no promotion gate, no out-of-sample split. A diagnostic.
- ⚠️ **The cross-asset correlations rest on 4–13 trades per asset.** The position vectors are
  dense (80–432 in-market days each) so Jaccard and position correlation are reasonably
  supported, but every pairwise figure is a small-sample estimate and none carries a confidence
  interval. The *direction* — asset breadth beating horizon breadth by a wide margin on three
  independent measures — is far more robust than any individual cell.
- Correlation measured over a window in which crypto was broadly trending together. A regime
  where the four cryptos move as one would raise these figures; §79.16 flags exactly that
  (rising cross-market correlation as a leading indicator that independence is breaking down).
  This measurement is a snapshot, not a constant.
- `backtest()` sizes at `qty = 1` unit, so P&L is quote-currency per unit and is **not comparable
  across assets**. Correlations are scale-invariant so per-asset figures are sound; raw P&L is
  never pooled here.
- PAXG-USD has ~456 daily bars (listed 2025-05-08) against ~1,830 for the others. PAXG-USDT has
  the long history, but §71.4a excludes the USDT quote.
- The calibration re-measures a pair PR #103 already measured. It spends no new decision budget —
  it re-derives a known answer as a correctness check, which is what `diagnostic_only` is for.
