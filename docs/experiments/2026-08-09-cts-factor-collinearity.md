# CTS factor collinearity — the suspected momentum cluster is not there, and the real defect is a different one

**Date:** 2026-08-09
**Issue:** #208 (from the QuantCrawler teardown #193, §1.5 — "confluence, not consensus")
**Harness:** `keel/research/cts_factors.py` — the reusable, importable, unit-tested measurement
library, alongside `research/independence.py` and `research/cscv.py`. It is library code, so it
lives in the package and ships in the wheel; `scripts/` is release/operator tooling and is
excluded from the wheel, which is the wrong home for something `tests/research/` imports.
**Script:** `docs/experiments/2026-08-09-cts-factor-collinearity.py` — the pre-declared
configuration and the report that produced every number below, and *only* that. Same split as
`2026-08-08-between-family-independence.py` (driver) against `research/independence.py`
(library), and `2026-08-05-coinbase-asset-class-probe.py`.
**Status:** measurement only. **No weight, factor, gate, threshold or rule changed.**
**Ledger:** three `diagnostic_only` rows, session `cts-factor-collinearity-2026-08-09` —
`...-unconditional-daily-`, `...-unconditional-hourly-` and `...-conditional-fired-2026-08-09`.
Each carries its own `hypotheses_tested` (45 / 36 / 45) so the multiple-testing budget is
charged per arm rather than pooled. This run influenced no shipped decision.

**Verdict: the suspicion in #208 is refuted for momentum and confirmed, weakly, for trend.**

The three factors the issue names as one momentum axis — `rsi_extreme`, `rsi_divergence`,
`deceleration` — have a **mean within-cluster φ of −0.018** on the primary sample, against
**+0.025** for the rest of the matrix. They are not merely uncorrelated; the sign is negative,
and the two RSI factors are **mutually exclusive**: over 6,822 observations they co-occur
**zero times** (Jaccard 0.000, lift 0.00). Sharing an input array is not the same as sharing an
answer, and this is the case where it was not.

The trend pair *does* cluster: `condition_aligned` × `ema_fan_aligned` **φ = +0.190**, against
+0.018 for the rest of the matrix — an order of magnitude above background, stable on all five
assets (0.110 to 0.218) and reproduced at **+0.201** on a 27× larger hourly sample. It is the
one pre-declared cluster that is real. It is also small: φ = 0.190 is 3.6% shared variance.

⭐ **The finding worth acting on is not collinearity at all.** `round_number_proximity`
(weight 1) is **present on 100.0% of BTC-USD, ETH-USD and PAXG-USD bars** and ~20% of ADA/XLM
bars. `levels.is_round_number(price, step=Decimal("0.005"))` treats `step` as an **absolute**
0.005, and Coinbase quotes those three products to two decimals — so every price is an exact
multiple of 0.005 and the check can never fail. On three of five live assets that factor is a
**constant +1 added to every CTS score**, which is worse than redundancy: a redundant factor at
least varies.

| the question #208 asked | answer | number |
|---|---|---:|
| momentum cluster is collinear? | **no — refuted** | mean within φ **−0.018** |
| trend cluster is collinear? | yes, mildly | mean within φ **+0.190** |
| does collinearity inflate the score? | **barely** | Var ratio **1.161** (σ 7.8% wide) |
| largest collinearity anywhere in the matrix | not a pre-declared cluster | `deceleration` × `candlestick_pattern` **φ +0.254** |
| is any factor broken? | **yes, and it is not a correlation problem** | `round_number_proximity` P(present) = **1.000** on 3 of 5 assets |

## What this is, and what it explicitly is not

The teardown's stated rule is one indicator per category with **3-of-4 agreement**. ⚠️ **That
ratio is unvalidated SEO-adjacent content and is used here for nothing.** No threshold below is
theirs; no result is compared against it; the number 3, the number 4 and the four categories
appear in no computation. What was taken seriously is the *mechanism*, which is not theirs and
not in dispute: an **additive** score presumes its terms are close to independent, because
`total = Σ wᵢxᵢ` treats every point as new evidence. keel's CTS puts 4 of 14 raw points on three
momentum reads and another 4 on two trend reads. Whether those reads actually move together was
never measured. Now it is.

This is a measurement, not a redesign. `DEFAULT_WEIGHTS` is read and never written; nothing in
`keel/research/cts_factors.py` is imported by the live path.

## Method

### There was no sample, so one had to be reconstructed

The issue proposes dumping per-factor contributions from the `signals` table. That table has
persisted a full `cts_factors` breakdown since P3 Task 1 (`engine._persist_signal`) — and it
holds **one row** in `keel.db` and **zero** in `keel-live.db`. The live book is five daily
turtles that fire a handful of times a year, and only gate-cleared signals are ever written. A
correlation matrix from one observation is not a weak measurement, it is not a measurement.

It is recoverable because `engine.assemble_cts_context` is a **pure function of
`(setup, candles)`** — no repo, no clock, no network — and `keel.db.candles` holds 611,176 bars
back to 2021-07-18. So the harness replays the real scoring path over history: shipped
`assemble_cts_context`, shipped `indicators_cts.score`, nothing reimplemented.

### Both samples, because the obvious one is a collider

| arm | population | granularity | window | N |
|---|---|---|---|---:|
| **1 (headline)** | every bar | ONE_DAY | expanding | **6,822** |
| 2 | every bar | ONE_HOUR | rolling 500 | **186,725** |
| 3 | every 20th bar, BTC only | ONE_HOUR | 250 / 500 / 1000 | 2,207 each |
| 4 | fired, gate-cleared signals | ONE_DAY | expanding | **77** |

**N for arm 1 = 6,822** = 1,647 (BTC) + 1,647 (ETH) + 258 (PAXG) + 1,635 (ADA) + 1,635 (XLM)
daily bars, being every cached bar after a 200-bar warm-up (the EMA fan's longest period), on
the five live-allowlist assets. Each observation is one bar with a synthetic `Setup` priced at
that bar's close. Only `setup.entry` is read by the context assembly — `stop`, `target` and
`context` are not consulted — so the synthetic setup invents no risk model, it only supplies a
price for the three factors that need one.

**Restricting to fired signals would have been selecting on the outcome.** `Rule.detect()` and
the engine's choppy / higher-TF-bias / kill-zone gates are functions of the same regime and
momentum state several factors read — the choppy gate admits a bar only when
`regime.detect_condition` is tradeable, which is most of what `condition_aligned` measures.
Conditioning on a common descendant of the variables being correlated is a collider, and it
moves the correlations by an unknown amount and sign. Arm 4 measures it anyway and shows exactly
that (below). The unconditional arm carries the conclusion.

### The window is not a free parameter, and it is the live path's own

Several `analysis.*` calls behind the context read the **whole list handed to them**:
`levels.find_levels` scans every pivot in the series, `regime.detect_phase` compares the last
close against `candles[0]`. Factor presence therefore depends on how much history the caller
passes. The live path — `agent.run_once` → `repo.get_candles(product, gran)` with no bounds →
`engine.evaluate` — passes the **entire cached series**, which arm 1's expanding window
reproduces exactly. Arm 2 needs a fixed window only because expanding is O(n²) and infeasible
over 44k hourly bars; arm 3 measures what that substitution costs (nothing, for these clusters).

### Measures

`independence.compare()` does **not** fit and is not used: its five §80.16 measurements assume
daily calendar-aligned in-market vectors plus a P&L series and derive entries from rising edges
of a held position. Factor presence is a per-bar flag with no holding period and no P&L, so
`pnl_correlation`, `entry_distances` and `median_entry_distance` have no analog. Two primitives
transfer and are imported by name:

- **φ** — Pearson correlation of two {0,1} vectors, computed exactly from the 2×2 contingency
  table (integer arithmetic and one square root, which matters at 186,725 × 36). It is
  *identical* to `independence.pearson` on the same input, and
  `tests/research/test_cts_factors.py::test_phi_agrees_with_shipped_pearson` asserts that on
  five seeds against the shipped function as oracle. That oracle is why `_pearson` was
  **deliberately promoted to `pearson`** — an oracle behind a private name is one refactor from
  vanishing.
- **Jaccard** — `independence.jaccard`, unchanged. Robust where φ is not: a factor present on 2%
  of bars makes the joint-absence cell dominate φ's denominator.
- **lift** — P(both)/(P(a)P(b)). The one measure whose scale does not shrink with rarity, so it
  catches "rare, but always together".

Clusters were **pre-declared** in `cts_factors.SUSPECTED_CLUSTERS`, taken verbatim from #208
before the run, so no cluster here was found by staring at a matrix.

### Multiple testing — controlled, and then discounted

10 of the 11 factors vary in arm 1 (`seasonality` is weighted 0 and hardcoded `False`), giving
**45 pairwise tests**. Family-wise error is controlled by **Holm–Bonferroni at α = 0.05** over
exactly the pairs tested — not over 11·10/2, which would charge the budget for pairs no test was
run on. Arm 2 has 9 varying factors (`sr_touches` is constant, see below) and so **36 tests**.

⚠️ **Then ignore the p-values, and this is said plainly rather than dressed up.** At N = 6,822,
φ = 0.041 clears Holm; at N = 186,725, φ = 0.054 lands at p = 1.9×10⁻¹¹⁷. **23 of 45 pairs are
"significant" in arm 1 and 12 of the 12 printed in arm 2.** Significance here answers "is this
correlation exactly zero", which nobody asked. The question asked is "is it large enough to
distort an additive score", which is an effect-size question. The correction is reported to show
the budget was accounted for, not because it carries any argument. **No conclusion in this
document rests on a p-value.**

## Result 1 — the momentum cluster does not exist

Arm 1, pooled, N = 6,822:

| pair | φ | Jaccard | lift |
|---|---:|---:|---:|
| `rsi_extreme` × `rsi_divergence` | **−0.049** | **0.000** | **0.00** |
| `deceleration` × `rsi_extreme` | −0.019 | 0.015 | 0.73 |
| `deceleration` × `rsi_divergence` | +0.014 | 0.070 | 1.09 |
| **mean within cluster** | **−0.018** | 0.028 | |
| *mean over every other pair* | *+0.025* | | |

**The within-cluster correlation is below background, and negative.** The cluster shares 4 of 14
raw points and behaves like three unrelated factors that occasionally exclude one another.

⭐ **`rsi_extreme` and `rsi_divergence` never co-occur — not "rarely", zero times in 6,822
bars.** This is structural once looked at: `rsi_divergence` scores only when
`indicators.rsi_divergence(...) == "bullish"`, which requires price making a lower low while RSI
makes a higher low, and that higher low is by construction *off* the oversold extreme that
`rsi_extreme` requires. Two factors reading the same array, wired to fire in disjoint states.
Same array, opposite question.

Consistent on every asset separately, so the pooling is not hiding a cancellation:

| within-cluster mean φ | BTC | ETH | PAXG | ADA | XLM |
|---|---:|---:|---:|---:|---:|
| momentum | +0.007 | −0.024 | −0.032 | −0.018 | −0.036 |
| trend | +0.187 | +0.155 | +0.110 | +0.218 | +0.199 |

And reproduced on the 27× larger hourly sample (arm 2, N = 186,725): momentum mean within φ
**−0.004**, largest-magnitude within-cluster pair **−0.011**; trend **+0.201** against +0.009
background.

⚠️ **This is a negative result and it is reported as one.** #208's structural argument — two
factors off the same `indicators.rsi(closes)` array, a third momentum read on the same closes —
is a correct description of the wiring and a wrong prediction about the output. The measurement
was worth making precisely because the wiring looked damning.

## Result 2 — the trend pair is the only real cluster, and it is small

`condition_aligned` × `ema_fan_aligned`: **φ = +0.190**, Jaccard 0.268, lift 1.47 (arm 1);
**φ = +0.201**, Jaccard 0.270, lift 1.47 (arm 2). Background for the rest of the matrix is
+0.018 and +0.009 respectively — so the pair sits **10–22× above background**, on both samples,
on every asset.

φ = 0.190 is **3.6% shared variance**. Those two factors carry 4 of 14 raw points. That is a
real effect, it is the only pre-declared cluster that survived, and it is not large enough to
justify a code change on its own.

## Result 3 — the strongest pair in the matrix was not predicted by anyone

| pair | φ | Jaccard | lift |
|---|---:|---:|---:|
| **`deceleration` × `candlestick_pattern`** | **+0.254** | 0.244 | **2.10** |
| `condition_aligned` × `rsi_divergence` | −0.195 | **0.000** | **0.00** |
| `ema_fan_aligned` × `rsi_extreme` | +0.191 | 0.066 | 2.90 |
| `condition_aligned` × `ema_fan_aligned` | +0.190 | 0.268 | 1.47 |
| `in_pullback` × `fib_confluence` | +0.168 | 0.377 | 1.10 |

`deceleration` × `candlestick_pattern` is the largest |φ| anywhere in the matrix on **all three**
unconditional configurations (0.254 daily, 0.257 hourly, 0.252 at every window length tested) —
and it crosses the very category boundary the teardown's taxonomy would draw, "momentum" against
"candlestick". Both are in fact reads of *recent bar geometry*: a decelerating leg and a
long-wicked reversal candle are two descriptions of the same exhaustion bar. **If any pair in
CTS is double-counting, it is this one, and it is not one #208 named.** It is worth 2 of 14 raw
points, and at φ = 0.254 (6.5% shared variance) it is still not large.

⭐ **A second structural pattern shows up that additivity handles worse than redundancy:
mutual exclusion.** `condition_aligned` × `rsi_divergence` co-occur **zero** times (lift 0.00),
as do `rsi_extreme` × `rsi_divergence`. A pair that can never both score means the ceiling of
14 raw points is unreachable — the achievable maximum is lower than the nominal one, so the
`entry_technique` thresholds (`low=5`, `high=8`) sit at a different place on the real
distribution than on the nominal one. Observed mean total is **5.14** with σ 2.03, against a
nominal ceiling of 14. This is unmeasured elsewhere and is flagged, not fixed.

## Result 4 — how much does any of this actually inflate the score?

The single number that answers #208's worry. Additivity assumes the cross terms vanish; this is
how much they do not. `independent` is the variance the same factors would produce at the same
base rates with zero correlation (`Σ wᵢ² pᵢ(1−pᵢ)`, each factor Bernoulli):

| arm | N | mean total | Var observed | Var independent | **ratio** |
|---|---:|---:|---:|---:|---:|
| 1 — unconditional, daily | 6,822 | 5.14 | 4.11 | 3.54 | **1.161** |
| 2 — unconditional, hourly | 186,725 | 4.05 | 3.14 | 2.88 | **1.089** |
| 4 — fired signals only | 77 | 6.97 | 1.68 | 2.54 | **0.663** |

**Correlation widens the CTS total's standard deviation by 7.8%** (√1.161) on the primary
sample, 4.4% on the hourly one. Reading the ratio as the equicorrelation
`n_eff = n/(1+(n−1)ρ̄)` gives **≈8.6 effective independent factors out of the 10 that vary**.
⚠️ That is the standard equicorrelated approximation reported for interpretation only — it is
**not** §78.2's `N̂ = ρ̂ + (1−ρ̂)·M`, which corrects a trials count and is a different quantity,
and it is fed into no gate and no MinBTL computation.

**8.6 of 10 is not the "confluence, not consensus" failure the issue anticipated.**

## Result 5 — the conditional sample, and why it is not the headline

Arm 4 drives the real `engine.evaluate` with the shipped turtle (byte-identical to
`keel-live.db` rules 1–5), gates and all: **77 gate-cleared signals** over 7,822 daily bars
(BTC 21, ETH 27, PAXG 2, ADA 14, XLM 13). Conditioning does exactly what the collider argument
predicts:

| factor | P(present), every bar | P(present), fired |
|---|---:|---:|
| `condition_aligned` | 0.275 | **0.818** |
| `ema_fan_aligned` | 0.301 | **0.909** |
| `rsi_extreme` | 0.023 | **0.416** |
| `deceleration` | 0.174 | **0.013** |
| `candlestick_pattern` | 0.202 | **0.013** |

The gates admit bars where the trend factors are already true and the exhaustion factors are
already false, so the variance ratio falls **below 1** (0.663) — conditioning has truncated the
distribution, not revealed independence. Every correlation in this arm is a conditional one and
none of them estimates the quantity #208 asked about. Reporting only this table would have been
selecting on the outcome. Both clusters point the same way here anyway (momentum −0.047, trend
+0.202), and only **3 of 45** pairs survive Holm at N = 77.

⚠️ Two live factors are all but dead on the population that actually gets scored:
`deceleration` and `candlestick_pattern` each fire on **1 of 77** gate-cleared signals. They
contribute 2 of 14 nominal raw points and ~0.03 in practice.

## Result 6 — window sensitivity, and one factor that vanishes with it

BTC-USD hourly, every 20th bar, N = 2,207 each:

| window | momentum mean φ | trend mean φ | Var ratio | P(`in_pullback`) | strongest pair |
|---:|---:|---:|---:|---:|---|
| 250 | −0.014 | +0.201 | 1.069 | 0.842 | `deceleration`×`candlestick_pattern` 0.252 |
| 500 | −0.014 | +0.201 | 1.065 | 0.851 | `deceleration`×`candlestick_pattern` 0.252 |
| 1000 | −0.014 | +0.201 | 1.033 | 0.857 | `deceleration`×`candlestick_pattern` 0.252 |

Both cluster columns are **identical by construction, not by luck**: every factor in either
cluster reads a bounded lookback (`detect_condition` 20 bars, RSI 14, the fan's longest period
200), so past the warm-up the window cannot reach them. `P(in_pullback)` is the control proving
the window is applied at all — `detect_phase` compares against `candles[0]`, so it moves.

⚠️ **`sr_touches` is present on 79.9% of daily bars and 0.0% of hourly bars under a 500-bar
window** — constant, hence dropped from arm 2's testing family entirely. `find_levels` needs
enough history to accumulate 3 distinct touches at a level, and 500 hourly bars never provide
it. A factor worth 2 of 14 points is therefore load-bearing or completely inert depending on how
much history the caller happens to pass, and the live caller passes an **ever-growing** series.
Not a correlation finding, but it fell out of this one.

## What this changes

**0. Nothing, in code, in this PR.** The issue is explicit that this is research and not a
drive-by tweak to the scoring weights, and no weight, factor, threshold or gate was touched.
Everything below is a recommendation or a follow-up issue.

**1. #208's headline hypothesis is refuted and should be closed as such.** The momentum cluster
is not a cluster (mean φ −0.018, and the two RSI factors are mutually exclusive). Collapsing
`{rsi_extreme, rsi_divergence, deceleration}` to one representative — the remedy the issue
proposes — would **destroy information**, not remove duplication. Do not do it.

**2. Recommend a follow-up issue on `levels.is_round_number`, which is the real defect this run
surfaced.** `step=Decimal("0.005")` is an absolute half-cent, not a fraction of price, so on any
product Coinbase quotes to 2 decimals the check is `price % 0.005 == 0` → always true. Verified
directly: P(present) = **1.0000** on BTC-USD, ETH-USD and PAXG-USD (2dp), 0.2195 on ADA-USD
(5dp), 0.1901 on XLM-USD (6dp). Three of five live assets receive an unconditional +1 on every
CTS score, and the same factor means something entirely different on the other two. This is a
correctness bug in a shipped analysis primitive, not a weighting question, and it is out of
scope here by the issue's own terms.

**3. The collapse candidate, if there ever is one, is `deceleration` × `candlestick_pattern`
(φ 0.254) — not the momentum trio.** Recommend it be *recorded* and not acted on: 6.5% shared
variance over 2 of 14 raw points does not justify spending trials budget, and arm 4 shows both
factors fire on 1 of 77 real signals anyway. The cheaper question is why they almost never fire
on gate-cleared bars at all.

**4. Additive CTS is defensible on this evidence.** ≈8.6 effective independent factors out of
10 active, 7.8% variance inflation. The teardown's critique is a correct general principle that
keel's implementation happens to survive. ⚠️ **That is a statement about independence, not about
edge.** Under §73.5 independence is necessary and never sufficient: whether any of these 11
factors *predicts* anything is a separate, unasked and unanswered question, and nothing here
should be read as validating CTS.

**5. `seasonality` remains structurally untestable and should stay out of every future family
count.** Weight 0 and hardcoded `False` in `assemble_cts_context` — it is not a factor scoring 0,
it is a factor that is never computed.

## Caveats

- In-sample, one window, no out-of-sample split, no promotion gate. A diagnostic.
- **Correlation, not causation, and not edge.** Nothing here says a factor is useful.
- Bars are **serially dependent**, and the 45 tests are not independent of one another. The
  Holm correction handles the multiplicity across pairs; it does **not** correct for
  autocorrelation within a series, which inflates effective N and makes every p-value optimistic.
  Since no conclusion rests on a p-value this does not move the finding, but a future run
  quoting significance would need a block bootstrap. Arm 3's `step=20` thinning was a cost
  measure, not a remedy.
- Arm 1 pools five assets on the assumption the factor relationship is the same on each; the
  per-asset table is printed for exactly that reason and the clusters agree across all five.
  `round_number_proximity` is the one factor that emphatically does **not** pool, and it is
  reported per-asset for that reason.
- PAXG-USD contributes 258 of 6,822 daily observations (listed 2025-05-08) and 2 of 77 fired
  signals. Its per-asset column is the weakest in every table.
- Arm 4's N = 77 supports almost nothing on its own; it is here to demonstrate the selection
  effect, which it does, and not to estimate a correlation.
- The synthetic setup prices entry at the bar's **close**. Three factors
  (`round_number_proximity`, `fib_confluence`, `sr_touches` via `nearest_level`) are measured
  against that price, so their base rates would shift under a different entry convention. The
  close is the neutral choice — it is what a market order fills at — but it is a choice.
- All five assets are crypto over one broadly-correlated window. §79.16's warning about rising
  cross-market correlation applies to factor structure as much as to returns.
