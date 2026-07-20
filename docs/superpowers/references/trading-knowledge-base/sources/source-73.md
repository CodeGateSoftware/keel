[← Knowledge Base index](../README.md)

## Source 73 — "Pseudo-Mathematics and Financial Charlatanism: The Effects of Backtest Overfitting on Out-of-Sample Performance" (David H. Bailey, Jonathan M. Borwein, Marcos López de Prado & Qiji Jim Zhu, *Notices of the American Mathematical Society*, April 2014, 34pp)

**What it is:** a short, formal, peer-reviewed mathematics paper — not a trading book. It does
not contain a single trading rule, indicator, or parameter. What it contains is the **statistical
theory of the exact situation this project is in**: a small sample, a swept parameter space, and a
selected-best configuration whose reported performance we would like to believe.

Its central object is **Minimum Backtest Length (MinBTL)** — a closed-form answer to *"how many
years of data do I need before a best-of-N backtest means anything?"* — plus the result that the
**expected maximum Sharpe ratio under the null rises without bound as the number of trials grows**.

**Why it earns a full extraction here rather than a triage line:** it is the rigorous statistical
account of the finding produced by our own experiment run the same day
(`docs/experiments/2026-07-20-adx-ablation-and-random-entry-control.md`). That experiment concluded
we need ~68 trades / ~26 years for the Turtle's edge to clear z ≥ 2. **This paper's MinBTL formula
reproduces that number exactly — and then shows it is an underestimate**, because the experiment's
z ≥ 2 bar silently assumed a trials budget we have already exceeded (§73.3).

⚠️ **Halal caveat up front:** the paper's headline statistic is the **Sharpe ratio**, which the KB
has repeatedly declined on riba grounds (§33, §50.1, §54.22, §64.4, §68.6) because it subtracts a
risk-free rate. §73.4 addresses this head-on rather than importing Sharpe uncritically. **Short
answer: the machinery survives rf = 0 intact, and survives it *better* than the original.**

---

## §73.0 The one-paragraph result
**Module: `strategy/promotion.py`**

> *"We prove that high performance is easily achievable after backtesting a relatively small number
> of alternative strategy configurations, a practice we denote 'backtest overfitting.' The higher
> the number of configurations tried, the greater is the probability that the backtest is overfit.
> Because financial analysts rarely report the number of configurations tried for a given backtest,
> investors cannot evaluate the degree of overfitting in most investment claims and analysis."*

And the conclusion, which is stronger than the usual disclaimer:

> *"The standard warning that 'past performance is not an indicator of future results' understates
> the risks associated with investing on overfit backtests. When financial advisors do not control
> for overfitting, positive backtested performance will often be followed by **negative** investment
> results."*

Not zero. **Negative.** §73.7 explains the mechanism.

---

## §73.1 ⭐⭐ The expected maximum performance under the NULL rises with the number of trials
**Module: `strategy/promotion.py`, `strategy/backtest.py`**

This is the formal version of *"sweeping parameters manufactures apparent edge."* It is the single
most important thing in the paper for us, because **we sweep**.

**Proposition 2.1.** Given `N` IID standard-normal draws `x_n ~ Z`, the expected maximum is

```
E[max_N]  ≈  (1 − γ)·Z⁻¹[1 − 1/N]  +  γ·Z⁻¹[1 − 1/(N·e)]
                                                        γ = 0.5772156649…  (Euler–Mascheroni)
                                                        Z⁻¹ = inverse standard-normal CDF
                                                        valid for N >> 1
upper bound:   E[max_N]  <  √(2·ln N)
```

The setup: `N` strategies, **all with a true Sharpe ratio of exactly zero**, each evaluated on one
year of data. Under Lo's asymptotic result (Eq. 2.3), with `μ = 0` and `y = 1` the estimated
annualized Sharpe is distributed `N(0,1)` — so the best-of-N is precisely the expected maximum of
`N` standard normals.

**The numbers, computed directly from Eq. 2.4:**

| trials `N` | `E[max_N]` = expected best IS Sharpe, when every true Sharpe is **zero** |
|---:|---:|
| 2 | 0.52 |
| 5 | 1.19 |
| 10 | **1.57** |
| 20 | 1.90 |
| 45 | 2.24 |
| 100 | 2.53 |
| 128 | 2.62 |
| 1,000 | 3.26 |

> *"if the researcher tries only N = 10 alternative configurations of an investment strategy, he or
> she is expected to find a strategy with a Sharpe ratio IS of 1.57, despite the fact that all
> strategies are expected to deliver a Sharpe ratio of zero OOS (including the 'optimal' one
> selected IS)."*

**Ten trials. Not ten thousand.** Ten. That is fewer configurations than we sweep on the ATR stop
multiple alone.

**The operational consequence:** a reported best-of-N performance figure is **not** an estimate of
edge. It is an estimate of edge **plus a selection bias whose size is `E[max_N]` and is computable
in advance.** The bias is not a vague worry; it is a number you can subtract.

**⚠️ And the killer corollary, which contradicts a comfortable assumption:**

> *"Because the hold-out method does not take into account the number of trials attempted before
> selecting a model, it cannot assess the representativeness of a backtest."*

An OOS split does **not** neutralise this. We have an OOS firewall (§54.10) and it is necessary —
but the paper is explicit that it is **not sufficient**, because the split is silent about `N`. Two
researchers with identical OOS discipline and identical OOS numbers have produced results of wildly
different credibility if one tried 3 configurations and the other tried 3,000. Nothing in a
walk-forward loop records that difference. **We must record it separately.**

---

## §73.2 ⭐⭐ Minimum Backtest Length (MinBTL) — the formula
**Module: `strategy/promotion.py`**

Rescaling Prop 2.1 by the standard deviation of the annualized Sharpe estimator (`y^(−1/2)` for
`y` years) and solving for `y`:

**Theorem 3.1.**

```
                ⎛  (1 − γ)·Z⁻¹[1 − 1/N] + γ·Z⁻¹[1 − 1/(N·e)]  ⎞²        2·ln N
MinBTL (years) ≈ ⎜  ─────────────────────────────────────────  ⎟   <   ───────────
                ⎝                 E[max_N]                      ⎠        E[max_N]²
```

where the **denominator `E[max_N]` is the IS annualized Sharpe you actually observed** (the level
you are being asked to trust), and the **numerator is the expected-maximum-under-the-null from
Prop 2.1 at your trial count `N`**.

Read plainly:

> **MinBTL = (selection bias ÷ observed performance)², in years.**

The paper's own worked example, which our implementation reproduces to the digit:

> *"if only 5 years of data are available, no more than 45 independent model configurations should
> be tried, or we are almost guaranteed to produce strategies with an annualized Sharpe ratio IS of
> 1, but an expected Sharpe ratio OOS of zero."*

And the far more alarming one:

> *"After trying only 7 independent strategy configurations, the expected maximum SR IS is 1 for a
> 2-year long backtest, while the expected SR OOS is 0."*

**Three caveats the paper states itself, all of which bind on us:**

1. **`N` must be the number of *independent* trials.** Correlated configurations (entry lookback 39
   vs 40) count as fewer than their raw combinatorial count. The paper suggests PCA-style dimension
   reduction to recover the effective `N`. This makes a raw grid-size count of `N` **conservative**
   — which for a promotion gate is the right direction to err, but we should say which we are using.
2. **MinBTL is necessary, not sufficient.** *"a backtest may be overfit even if it is computed on a
   sample greater than MinBTL."* It is a floor, not a certificate.
3. Prop 2.1 assumes the trials are independent, *"which leads to a quite conservative estimate."*

**The most useful form for us is the inversion** — not "how much data do I need" (we cannot make
more BTC history) but **"how many configurations may I try, given the data I have and the
performance I observe?"**

```
Trials budget:   try N such that   E[max_N]  ≤  SR_observed · √y
```

---

## §73.3 ⭐⭐ MinBTL COMPUTED FOR KEEL — and it reproduces today's experiment exactly
**Module: `strategy/promotion.py`, `strategy/backtest.py`**

### Assumptions (stated so they can be attacked)

| input | value | source |
|---|---|---|
| BTC gate-ON expectancy | **$1,368/trade** | experiment Part 1 |
| BTC edge **over the random-entry null** | **$1,783/trade** (1,368 − (−415)) | experiment Part 2 |
| BTC implied per-trade σ | **$7,272** (= null stdev $2,017 × √13) | experiment Part 2 |
| BTC trade frequency | **2.6 trades/yr** (13 trades / 5 yr) | experiment |
| usable history | **5.0 yr** BTC/ETH (1,819 bars); **1.19 yr** PAXG (435 bars) | experiment |

Derived, using **trades — not calendar days — as the return unit** (this choice is deliberate and
is defended in §73.4):

```
SR_per_trade   = 1,783 / 7,272                    = 0.245
SR_annualized  = 0.245 × √2.6                     = 0.395        ← "q" = trades per year
Pooled cross-check: 791/(931·√30) × √6.0          = 0.380        ← independent, agrees
```

**An annualized Sharpe of ≈ 0.39 is the honest number for our one positive-edge rule.** Not 1.5.
Not 2. Around 0.4. Everything below follows from that.

### The result

```
                     N trials   E[max_N]    MinBTL     = BTC trades
                     ────────   ────────   ────────    ────────────
                            1     2.000†     25.6 yr          67
                           26     2.014      26.0 yr          68     ← today's experiment
                           45     2.236      32.0 yr          83
                           84     2.469      39.1 yr         102
                          336     2.931      55.1 yr         143
                        2,016     3.449      76.3 yr         198
                     († the z ≥ 2 bar, as a single-trial MinTRL-style threshold)
```

### ⭐ The reconciliation, which is exact and not a coincidence

The experiment derived **~68 trades ⇒ ~26 years** from a `z ≥ 2` significance bar. MinBTL at
**N = 26 trials** gives **26.0 years = 68 trades.** They agree to three significant figures.

They agree because they are the same calculation with the threshold parameterised differently.
`E[max_N]` first crosses **2.0 at exactly N = 26**. So:

> **The experiment's "z ≥ 2" bar is, in this paper's language, a MinBTL computed with an implicit
> trials budget of 26 configurations.**

That is the refinement this paper contributes, and it is not flattering:

- **It corroborates the experiment's arithmetic completely.** Two independent derivations —
  a Monte-Carlo random-entry null and a closed-form extreme-value formula — land on the same
  ~68 trades. The experiment's headline finding is **confirmed, not contested.**
- **It refines the interpretation, downward.** A fixed 2σ bar is only the correct bar if you tried
  **~26** configurations. Our shipped `entry_lookback=40 / exit_lookback=20` carries the code
  comment *"walk-forward OOS default (was 20)"* — it was **selected** from alternatives. Add the
  ADX threshold, the ATR stop multiple, the exit method and the trail method and the grid is in the
  **hundreds to low thousands**, not 26. At N = 336 the requirement is **~55 years / 143 trades**;
  at N ≈ 2,000 it is **~76 years / 198 trades.**
- **So the true figure is worse than 26 years — plausibly 2×–3× worse.** The experiment's ~26 years
  was the *best case*, obtained by implicitly assuming we had barely swept at all.

### ⚠️ PAXG is not merely thin — it is formally inadmissible

PAXG has **1.19 years** of history. The trials budget inversion gives, for an observed
SR ≈ 0.30–0.40, a maximum affordable `E[max_N]` of `0.40 × √1.19 = 0.44`. `E[max_N]` at **N = 2** is
already **0.52**. Therefore:

> **On PAXG we cannot afford even TWO configurations.** Any parameter chosen by comparing two
> alternatives on PAXG's window is, by Theorem 3.1, uninterpretable. This is a stronger statement
> than the experiment's caveat (*"too short to conclude anything about that asset"*) — it is a
> **quantified prohibition on selection**, not a soft warning about conclusions.

The correct handling: PAXG **inherits** parameters chosen on BTC/ETH; it never gets its own fit.
(This happens to align with §58.4's *"trade the complete basket without selection based on
historical performance"* — arrived at from a completely different direction.)

### The trials budget we can actually afford

| available history | observed annualized SR | **max affordable N** |
|---|---:|---:|
| 5.0 yr (BTC/ETH) | 0.30 | **2** |
| 5.0 yr | 0.40 (our estimate) | **3** |
| 5.0 yr | 1.00 | 45 |
| 1.19 yr (PAXG) | 0.40 | **1 — none** |

**We can afford two or three independent configurations. We have tried orders of magnitude more.**

This is the paper's contribution to the project stated as bluntly as it can be. It does **not** say
the Turtle has no edge — the experiment already established the sample cannot resolve that. It says
something narrower and more actionable: **the specific values 40 and 20 are not defensible as
"selected by walk-forward," because the data cannot support a selection at all.** They should be
held as *a priori* choices justified by external literature (§54.11's *"slower is uniformly better,
best range near the maximum tested ≈ 80 days"*; §58.6's *"optimum 80–95"*) rather than as an
empirical finding of ours.

---

## §73.4 ⭐⭐ Does the deflation machinery survive rf = 0 and long-only? — the halal reconciliation
**Module: `sim/metrics.py`, `strategy/promotion.py`**

The KB has declined the Sharpe ratio four separate times on riba grounds — §33 (MPT/portfolio
optimization), §50.1 (CAPM/alpha/beta), §54.22 (GASP: Kaufman independently endorses drawdown
semivariance over Sharpe), §64.4 and §68.6 (GA/PSO papers using Sharpe with an explicit `Rf`).
`keel/sim/metrics.py` states it in its own docstring: *"`rf = 0` everywhere (halal policy:
riba-free), so Sharpe and Sortino are simple mean/downside-risk ratios with no risk-free
subtraction."* This paper leans on Sharpe throughout. So: does any of it survive?

### 1. The riba objection does not touch the derivation. **It survives — and improves.**

The paper's Eq. 2.2 is `SR = (μ/σ)·√q` where `μ` is the mean **excess** return. The risk-free rate
enters in exactly one place: defining the benchmark that `μ` is measured against.

Nothing downstream depends on what that benchmark *is*. Theorem 3.1 requires only that:
(a) under the null the estimator is asymptotically normal with mean equal to the true value, and
(b) its standard error scales as `y^(−1/2)`.

Both hold for **any** location-scale ratio statistic. Setting `rf = 0` re-specifies the null from
*"the strategy adds nothing over lending at the risk-free rate"* to *"the strategy adds nothing over
holding cash."* **That is the correct null for a halal spot agent anyway** — we cannot lend at a
risk-free rate, so the original null tests a counterfactual unavailable to us. The paper's own
footnote 2 confirms the generality: *"several authors have proved that its asymptotic distribution
follows a Normal law even when the returns are not IID Normal. The same result applies to the
Information Ratio. The only requirement is that the returns be ergodic."*

> **Verdict: MinBTL and Prop 2.1 apply unchanged under `rf = 0`. The riba objection was always
> about the *benchmark*, never about the *algebra*, and this is the first source in the KB where
> that distinction has a practical payoff.** Every prior Sharpe-declining section (§33, §50.1,
> §54.22, §50.1, §68.6) rejected Sharpe *as an objective to optimize*. Nothing there forbids using
> a Sharpe-shaped quantity *as a null-hypothesis test statistic* — a different job entirely.

### 2. But §54.22's objection is separate, statistical, and **binding**

§54.22 (Kaufman's GASP) says: **do not compute a whole-period Sharpe on the Turtle's intermittent,
mostly-cash daily returns.** A rule in the market ~15% of the time has ~85% zero-return days, which
deflates σ and inflates the ratio. That objection is about **the return series**, not the risk-free
rate, and it survives setting `rf = 0` completely intact.

**The fix is the choice of return unit, and it is clean:** compute the statistic **per trade**, not
per calendar day, and set `q` = trades per year rather than 365.

```
SR_trade      = expectancy_per_trade / σ_per_trade      # rf = 0, cash-relative
SR_annualized = SR_trade × √(trades per year)
```

Zero-return days simply do not exist in this formulation — there is no day on which we were flat and
counted a zero. §54.22's objection dissolves rather than being traded off. This is precisely the
computation performed in §73.3, and its agreement with the experiment's independent Monte-Carlo
figure is evidence the unit choice is sound.

**Bonus:** `expectancy` and per-trade dispersion are already `BacktestResult` fields, so this is a
few lines of arithmetic over data the harness already produces — not a new metrics subsystem.

### 3. On which statistic — the honest three-way split

| statistic | MinBTL applicable? | why |
|---|---|---|
| **Per-trade Sharpe, rf = 0** | ✅ **Yes, exactly** | Location-scale ratio, asymptotically normal, standard error `∝ y^(−1/2)`. This is the only statistic the paper's algebra literally covers. |
| **Sortino, rf = 0** | ⚠️ **In spirit only** | Prop 2.1's expected-maximum result is distribution-free once the statistic is standardised, so *"the best of N Sortinos overstates edge, and by roughly this much"* holds. But Lo's Eq. 2.3 variance `(1 + SR²/2q)/y` is derived for Sharpe under IID Normal; **there is no closed-form Sortino analogue in this paper.** Using MinBTL on a Sortino would be an unproven extension. |
| **Raw expectancy** | ❌ **No** | Not scale-free. `E[max_N]` is expressed in standard deviations; expectancy in dollars. Must be divided by per-trade σ first — at which point it *is* the per-trade Sharpe. |

### ⇒ The recommended split, which contradicts nothing already adopted

> **Use the rf = 0 per-trade Sharpe as the MinBTL *gate* statistic. Keep Sortino / max-drawdown as
> the *verdict* statistic.**

These are different jobs and there is no conflict. The gate answers *"could this number have been
manufactured by my own sweeping?"* The verdict answers *"is the risk profile acceptable?"* §54.22
endorses drawdown semivariance for the second question and is silent on the first. We now have a
principled answer to both without importing a risk-free rate anywhere.

### 4. Long-only, shorting, leverage

**Nothing in the paper requires any of them.** The derivation is over an abstract return series
`Δm_τ = μ + σε_τ`; the strategy generating it is irrelevant. Section 5's illustrative aside
mentions *"shorting a position just before a sell-off"* purely as an example of how overfitting
targets specific data points — remove it and the argument is unchanged. Example 8.1's parameter mesh
includes a `Side ∈ {−1, 1}` dimension; **for us that dimension collapses to `{+1}`, which merely
halves `N` and strengthens our position.** No leverage, no derivatives, no discounting, no
risk-free rate outside the Sharpe definition already handled above.

> **This paper is riba-free in substance. It is the cleanest case in the KB of a Sharpe-using source
> that survives the halal screen wholesale after a single well-defined substitution.**

---

## §73.5 Model complexity: `N` grows as 2^(number of parameters)
**Module: `strategy/rules/`, `strategy/promotion.py`**

> *"Consider a one-parameter model that may adopt two possible values… Overfitting will be
> difficult, because N = 2. Let's say that we make the model more complex, by adding 4 more
> parameters so that the total number of parameters becomes 5, i.e. N = 2⁵ = 32."*
>
> *"A relatively simple strategy with just 7 binomial independent parameters offers N = 2⁷ = 128
> trials, with an expected maximum Sharpe ratio above 2.6."*

And the aim squarely at us:

> *"**Most Technical Analysis strategies rely on filters**, which are sets of conditions that trigger
> trading actions, like the random switches exemplified earlier. Accordingly, **extra caution is
> warranted to guard against overfitting in using Technical Analysis strategies**, as well as in
> complex non-parametric modeling tools, such as Neural Networks and Kernel Estimators."*

`TurtleBreakout` currently exposes `entry_lookback`, `exit_lookback`, `adx_period`,
`adx_threshold`, `atr_period`, `atr_stop_mult` — **six parameters**, before any exit-method or
trail-method switch. At merely two candidate values each that is `2⁶ = 64` trials and
`E[max_64] ≈ 2.35` — an expected best-of-sweep Sharpe of 2.35 **from a rule with no edge at all.**
Our observed 0.40 is not remotely in that territory, which is itself informative (§73.11).

**This retroactively strengthens two positions the KB already holds** and gives them a formula
rather than a slogan:
- §26 **KISS / few parameters** — now quantified: each binary parameter added *doubles* `N` and
  raises the null's expected maximum.
- §58.16(b) Katz & McCormick's finding that *"the optimization of one or two parameters had minimal
  curve-fitting effect"* while many-parameter models curve-fit badly. **Same result, arrived at
  empirically there and analytically here.**

⚠️ It also **re-condemns the neural-network / genetic-algorithm exclusion** (§54, §58.16, §68.6)
from a third independent direction — those models have effectively unbounded `N`, so `E[max_N]` and
therefore the required IS performance is unbounded too.

---

## §73.6 ⭐ Report `N`, or the backtest is uninterpretable
**Module: `strategy/promotion.py`, `keel simulate` report artifact**

The paper sets this in its own display block, the only claim so honoured:

> *"**A researcher that does not report the number of trials N used to identify the selected backtest
> configuration makes it impossible to assess the risk of overfitting.**"*

This is not a stylistic complaint. It follows mechanically from Theorem 3.1: the required IS
performance threshold is a function of `N`, so **without `N` there is no threshold, and without a
threshold a reported number cannot be compared to anything.** A backtest without a trial count is
not weak evidence — it is *no* evidence, because its selection bias is unbounded.

**Directly implementable, and cheap:** the `keel simulate` artifact must carry a
`trials_attempted` field — the total size of every grid searched to arrive at the reported
configuration, cumulative across sessions, including abandoned sweeps. The paper is explicit that
abandoned experiments are the whole problem (§73.10). A field that only counts the sweep that
happened to produce the winner is worse than no field at all, because it looks like disclosure.

⚠️ **This is a discipline problem more than a code problem.** `N` accumulates across sessions,
across agents, and across weeks. Every parameter ever eyeballed and rejected counts. A realistic
implementation needs a **persisted, append-only trials ledger** that a sweep increments, not a
number recomputed per-run — otherwise `N` silently resets every session and the gate is theatre.

---

## §73.7 ⚠️ Overfitting is not neutral — with memory in the series it is actively NEGATIVE
**Module: `strategy/promotion.py`, `analysis/insights.py` (edge decay)**

The paper's most counterintuitive result, and the one that upgrades overfitting from "wasted effort"
to "harmful."

**Section 5 — memoryless case (good news).** Monte-Carlo over `N = 1,000` Gaussian random walks,
`T = 1,000`, split IS/OOS. Selecting the best IS configuration pushes IS Sharpe into the **1.2–2.6**
range (centred ~1.7) while **OOS Sharpe stays centred on zero** (Figures 3, 5). Overfitting bought
nothing, but cost nothing. *"there is no reason to expect overfitting to induce negative
performance."*

**Section 6 — with compensation effects (the bad news).** Real financial series have memory:
*"overcrowded investment opportunities, major corrections, economic cycles, reversal of financial
flows, structural breaks, bubble bursts."* Two formalisations, both proved:

**Proposition 6.1 (global constraint).** Re-centring each path to a common mean `μ` — one single
constraint, one degree of freedom removed — gives, for two configurations A and B of the same model:

```
SR_IS(A) > SR_IS(B)   ⟺   SR_OOS(A) < SR_OOS(B)
```

The ordering **inverts exactly**. Figure 6 shows the resulting scatter: slope **−0.97**,
**adj R² = 0.85**, and OOS Sharpes clustered around **−1.2**. The underlying process was trendless.

**Proposition 6.3 (serial dependence).** The same inversion arises from a plain AR(1) process
(`φ = 0.995`) with **no** global constraint — a much weaker and more realistic assumption. *"Such
serial correlation is a well-known statistical feature, present in the performance of most hedge
fund strategies."* Half-life `τ = −ln2 / ln φ` (Prop 6.2) — 138 observations at `φ = 0.995`.

> *"**IS backtest optimization is in fact detrimental to OOS performance.**"*
>
> *"It will be around zero if the process has no memory, **but it may be significantly negative if
> the process has memory**."*

**Why this matters specifically for keel.** Crypto plainly has memory — regime shifts, the halving
narrative (§14), bubble/bust cycles (§54.20 extreme events), and the mean-reversion/momentum
alternation that §62.2's AR(1) analysis made central to the KB's account of *why* pullback-buying
was refuted. **§62.2 already establishes that our return series is AR(1)-shaped.** Proposition 6.3
is stated for exactly that process. So we are in the *compensation-effects* regime, not the benign
memoryless one, and:

> **The more we optimized the Turtle's parameters in-sample, the worse its true forward performance
> is likely to be — not merely no better.**

⚠️ This meaningfully re-frames the **edge-decay** machinery (§6.3, §20.7, `analysis/insights.py`).
The KB has treated decay as *the market arbitraging an edge away*. Prop 6.3 supplies a second,
entirely mechanical cause requiring no market participant at all: **a serially-correlated series
plus in-sample selection produces observed decay by construction.** Two hypotheses, indistinguishable
from the decay curve alone. Anything the decay detector flags is now ambiguous between "the edge
died" and "the edge was a selection artifact all along," and the disambiguator is `N`.

---

## §73.8 The practical worked example — 8,800 configurations on a random walk
**Module: `strategy/backtest.py` (as a negative exemplar)**

**Example 8.1.** Four parameters — `Entry_day ∈ {1..22}`, `Holding_period ∈ {1..20}`,
`Stop_loss ∈ {0..10}`, `Side ∈ {−1,1}` — a **four-dimensional mesh of 8,800 nodes**, searched over
**1,000 daily prices (~4 years) of a pure random walk with no seasonal effect whatsoever.**

Result: the optimal node delivered **annualized Sharpe = 1.27**, with a **PSR-Stat of 2.83**,
*"which implies a less than 1% probability that the true Sharpe ratio is below 0."*

> *"we have been able to identify a plausible seasonal strategy with a SR of 1.27 **despite the fact
> that no true seasonal effect exists**."*

⚠️ **Note carefully what failed here.** The Probabilistic Sharpe Ratio — a legitimate,
non-Normality-aware significance test — returned "over 99% confident this is real" **on pure
noise**. PSR is a *single-trial* test; it knows nothing of the other 8,799 nodes. **A correct
significance test applied without a trial count still gives a confidently wrong answer.**

**This is the sharpest available warning about our own harness.** Our sweep is smaller than 8,800
but the same shape, and our data window (5 years) is barely longer than theirs (4 years). It also
retires a tempting shortcut: adding a per-configuration p-value or confidence interval to the sim
report would **not** solve the problem and would create false comfort. **The correction must be
multiplicity-aware.**

**Reinforces §68.6** (the contest paper's unvalidated 21,856× backtest) and **§58.17** (Katz &
McCormick's own "625% annualized" survivor portfolio) — this is now the third documented instance in
the KB of a spectacular backtest number produced by selection, and the only one with a *proof*
attached.

---

## §73.9 The fraud framing — `N` as an economic cost
**Module: none (framing); `analysis/insights.py` for LLM-proposed strategies**

Section 7's mailing scam: send `2ⁿ·x` investors a coin-flip market forecast, halve the list each
month, and after `n` rounds `x` investors have witnessed `n` consecutive infallible forecasts.

> *"Not reporting the number of trials (N) involved in identifying a successful backtest is a similar
> kind of fraud. The investment manager only publicizes the model that works, but says nothing about
> all the failed attempts."*

The paper's defence is elegant: *"require the investment manager to produce a number `n` for which
the scheme is uneconomic."* Since manufacturing a spurious result costs `2ⁿ` experiments, a
sufficiently large demanded `n` makes fraud more expensive than the payoff. It draws the parallel to
selective publication in medical trials and the `alltrials.net` response.

**Where this bites for keel:** the deferred LLM feature (§35.1, §64.7). An LLM proposing strategies
is a **trial generator with essentially zero marginal cost per trial** — precisely the regime where
the economic-cost defence collapses. §64.1 already justifies granting LLM-proposed strategies zero
evaluation shortcut on empirical grounds (published AI/ML superiority claims inverting under
realistic-scale re-testing). §73.9 supplies the *analytic* reason: **each LLM-proposed candidate
increments `N`, therefore raises the bar for every other candidate**, including ones proposed by a
human. If the LLM feature ever ships, its proposals must increment the same trials ledger as a
hand-run sweep. Otherwise it is a machine for inflating `E[max_N]` while the recorded `N` stays flat.

---

## §73.10 Selection bias hides in places that do not look like sweeps
**Module: `strategy/promotion.py`, `analysis/regime.py`**

> *"Large mutual fund groups typically discontinue and replace poorly performing funds, introducing
> survivorship and selection bias. **While the motivation of this practice may be entirely innocent,
> the effect is the same as that of hiding experiments and inflating expectations.**"*

The phrase *"the motivation may be entirely innocent"* is the operative one. `N` accrues from any
process that discards underperformers, whether or not anyone thought of it as a parameter search.

**Concretely, for us, these all increment `N`:**
- **Asset pruning.** Any consideration of dropping ETH on realized P&L is a trial. (§58.4 already
  argues against it on the grounds that in↔OOS market-profitability correlation was only r = 0.15;
  §73.10 adds that doing it *also silently raises the promotion bar for everything else*.)
- **Rule retirement.** Refuting `rsi_meanrev` and `pullback_continuation` on crypto data were trials.
  They were *good* trials with the right outcome — but they count.
- **Threshold nudges** made outside a formal sweep — the kind that never generate an artifact.
- **The ADX ablation run today.** It was one comparison (ON vs OFF), correctly pre-registered against
  an external hypothesis (§58.2), and reported honestly including its negative parts. That is the
  cheapest possible trial and close to the ideal form. But `N` still went up by one.

⇒ **The trials ledger (§73.6) must count decisions, not just grid nodes.**

---

## §73.11 Reconciliation with prior sources and with today's experiment

**vs §54.10 (Kaufman, testing rigor) — deepens and partly undercuts.** §54.10 gives the qualitative
disciplines: in-sample/out-of-sample split, walk-forward, *"validate on OOS exactly ONCE,"* the
feedback firewall, *"best = most robust params, not the maximum,"* the robustness plateau. **Every
one is right and this paper endorses them all implicitly.** But §54.10 has **no way to say how much
data you need**, and its OOS prescription is explicitly stated by this paper to be insufficient on
its own: *"the hold-out method… cannot assess the representativeness of a backtest."*

⇒ §54.10 tells us **how** to test. §73.2 tells us **whether we have enough data to test at all.**
That question was previously unaddressed anywhere in the KB. Kaufman's **robustness plateau** is,
in this light, an informal defence against exactly Prop 2.1 — a broad plateau is evidence that many
neighbouring configurations work, i.e. that the effective independent `N` is small; a lone spike is
the signature of a best-of-N draw from the null. **Now formalised, and worth stating that way in the
sim report.**

**vs §54.11 (breakout profile) — supplies the missing consequence.** §54.11 established that
low trade frequency (~2.7/yr) is **inherent** to N-day breakout trend-following, not a bug, and that
the lever is risk-per-trade rather than "the rule is broken." True — but §54.11 treated that as a
*returns/deployment* fact. Theorem 3.1 shows it is also an **epistemic** fact: low frequency means
few return observations per year, which means a low annualized Sharpe for a given per-trade edge,
which means a large MinBTL. **The breakout family is intrinsically the hardest rule class to
validate**, and that follows from its defining characteristic. This is the formal backing for the
experiment's Conclusion 3.

**vs §54.22 (GASP / declined Sharpe) — resolved rather than contradicted.** See §73.4. §54.22's
objection is to *whole-period Sharpe on intermittent returns*, which the per-trade formulation
sidesteps entirely. Its endorsement of drawdown semivariance as the **verdict** metric stands
untouched.

**vs §58.11 (random-entry null) — complementary, not redundant.** These answer different questions
and both are needed:

| | asks | catches |
|---|---|---|
| **§58.11** random-entry control | *is the ENTRY better than chance?* | a rule with no signal content |
| **§73.2** MinBTL | *could this number have been manufactured by MY OWN SWEEPING?* | a rule whose apparent edge is a selection artifact |

A rule can pass one and fail the other. §58.11 is a null over the *data*; §73.2 is a null over the
*researcher's search process*. Running both is genuinely more informative than running either twice.
**And today's experiment showed the two converge numerically** (§73.3) — which is the strongest
available evidence that both are correctly implemented.

**vs §58.16 (multiple-comparison correction) — upgraded from a note to a formula.** §58.16(a)
recorded that Katz & McCormick applied a multiple-comparison correction (*"8.7% uncorrected; 99.9%
corrected"*) and observed that our sweeps should adopt the discipline, since *"sweeping 7 stop widths
× 6 look-backs is 42 comparisons."* **That instinct was exactly right and is now a closed-form
result** — `E[max_42] ≈ 2.19`, requiring **48 years** at our SR. §58.16's note can be marked
superseded by §73.2, which is strictly more precise.

**vs §58.17 (the "625% annualized" survivor portfolio) — same error, now with a proof.** §58.17
diagnosed the book's closing portfolio as selection bias *"dressed in a legitimate-looking
procedure,"* and logged for `promotion.py` that per-asset rule selection must sit **inside** the
walk-forward loop. §73.7's Prop 6.1/6.3 sharpen the stakes: with memory in the series, that error
does not merely fail to help OOS — **it produces systematically negative OOS results.** §58.17's
prescription is unchanged and now more urgent.

**vs §63.2 (hindsight-ceiling diagnostic) — a matched pair, upper and lower bound.** §63.2 benchmarks
realized P&L against the **best achievable** on the same history under our actual caps. §73.2
benchmarks reported performance against the **best achievable by luck alone** given `N` trials.
Together they bracket a result from both sides — *"how far below perfect are we"* and *"how far above
noise are we"* — which is a more complete diagnostic than either alone. Neither is self-referential
backtesting.

**vs §64.1 (Methods Matter) — analytic backing for an empirical finding.** §64.1 documented a
decade of published AI/ML trading-superiority claims **inverting** under realistic-scale re-testing.
§73.7's compensation-effect propositions predict exactly that inversion — `SR_IS(A) > SR_IS(B) ⟺
SR_OOS(A) < SR_OOS(B)` **is** an inversion theorem. The KB's sharpest empirical warning and its
sharpest analytic one now describe the same phenomenon.

**vs §6.4 (no prediction oracle)** — §73.8's Example 8.1 is a clean demonstration that a
*"which calendar interval makes money?"* search finds an answer on pure noise. Independent support
for the KB's standing exclusion of seasonality/calendar prediction as an oracle. It also, in
passing, **weakens §58.16's seasonality result** further — Katz & McCormick's seasonal models were
the best conventional family in their book, and §73.8 is the direct demonstration that such findings
arise from unreported search. The KB already declined to port them; that decision looks better now.

**vs today's experiment — corroborates, then refines downward.** Fully covered in §73.3. The
arithmetic agrees to three significant figures; the interpretation tightens because a fixed 2σ bar
encodes an implicit N = 26 that we have exceeded.

---

## §73.12 ⭐ Concrete promotion-gate additions this paper justifies
**Module: `strategy/promotion.py`, `keel simulate` report**

Ranked by value-per-unit-effort. All four are implementable against fields the harness already has.

### 1. ⭐⭐ A persisted trials ledger, and `trials_attempted` in every sim artifact
Append-only, incremented by every sweep node, every ablation, every rule retirement, every asset
prune (§73.6, §73.10). **This is a prerequisite for #2 and #3 — neither can be computed without it.**
It is also the cheapest item and the one with independent value: it makes the search process
auditable, which nothing currently does.

⚠️ Getting `N` honest is harder than getting it computed. It must survive session boundaries and
must count decisions, not just grid nodes.

### 2. ⭐⭐ A MinBTL gate
```
SR_trade      = expectancy / σ_per_trade                      # rf = 0
SR_annualized = SR_trade × √(trades_per_year)
E_max_N       = (1−γ)·Z⁻¹[1 − 1/N] + γ·Z⁻¹[1 − 1/(N·e)]       # γ = 0.5772156649
MinBTL_years  = (E_max_N / SR_annualized)²

BLOCK promotion if   MinBTL_years > years_of_data_available
```
~15 lines. Needs an inverse-normal CDF, which is a rational approximation — no new dependency
(and `sim/metrics.py`'s no-NumPy/Decimal-only constraint is satisfiable, though the inverse-normal
will want care in `Decimal`).

Report the gate as a **ratio** (`data_available / MinBTL_required`) rather than a boolean, so a
near-miss is visible instead of collapsing to "fail."

⚠️ **Be honest about what happens when this ships: at present values it fails, hard, for every
rule we have.** `MinBTL(N=336) ≈ 55 yr` vs `5 yr` available is a ratio of 0.09. That is not a
reason to weaken the gate — it is the finding. It should ship **reporting-only first** (surface the
ratio, do not block), because a gate that blocks everything on day one gets disabled rather than
heeded, and because we will want several sessions of ledger data before trusting `N`.

### 3. ⭐ A trials budget, enforced *before* a sweep runs
The inversion (§73.2) is more actionable than the gate, because it constrains a decision we are
about to make rather than judging one already made:
```
max_affordable_N  =  largest N such that  E[max_N] ≤ SR_expected × √years_available
```
At `SR ≈ 0.40`, 5 years ⇒ **N ≤ 3**. Enforcing this literally would end parameter sweeping. What it
should actually drive: **pre-register a small `N` and stick to it**, and treat parameters outside that
budget as *a priori* choices from external literature rather than fitted values. See §73.13.

### 4. ⭐ A `parameter_provenance` field on every rule parameter
Each parameter tagged **`a_priori`** (chosen from literature/theory, never fitted — does not count
toward `N`) or **`fitted`** (selected by comparing outcomes on our data — counts toward `N`).
This makes the trials budget manageable rather than hopeless: parameters justified externally are
free. `TurtleBreakout`'s `atr_period=20` is `a_priori` (the Turtle's canonical "N", §54.14) and
should be marked and frozen; `entry_lookback=40` is currently `fitted` and per §73.3 cannot be
justified that way — it should be **re-derived as `a_priori`** from §54.11 (*"best range near the
maximum tested ≈ 80 days"*) and §58.6 (*"optimum 80–95"*), both of which point to a **longer**
lookback than 40 anyway.

**Explicitly NOT recommended:** a per-configuration p-value or confidence interval in the sim report.
§73.8 demonstrates that a correct single-trial significance test (PSR-Stat 2.83, ">99% confident")
returns a confidently wrong answer on pure noise when multiplicity is ignored. It would create false
comfort. The correction must be multiplicity-aware or absent.

---

## §73.13 ⚠️ Does this change how the project should SWEEP? — yes, fundamentally
**Module: `strategy/promotion.py`, `keel simulate`, `strategy/rules/`**

The project's current method — sweep a grid, pick the best, ship it — is **the exact procedure this
paper was written to discredit**, and our data window cannot support it. The trials budget at our
observed performance is **2–3 independent configurations on 5 years, and zero on PAXG.**

But "stop sweeping" is not implementable and not quite right either. Four changes that are:

**1. Reclassify most parameters as `a_priori` rather than fitted.** This is the highest-leverage
change and it costs nothing. A parameter taken from Kaufman or Katz & McCormick because *their*
tests over 17–36 markets and 20 years support it does **not** increment our `N`. It is a prior, not
a fit. **We have a 70-source knowledge base precisely so that we do not have to fit parameters on
1,819 bars of BTC.** The KB's real function, on this reading, is as a **trials-budget subsidy** —
every parameter it can justify externally is a parameter we do not have to spend `N` on. That
reframing is worth more than any single gate.

**2. Prefer the plateau, and say why.** §54.10's *"best = most robust, not the maximum"* is now
formally motivated: a broad plateau indicates low *effective independent* `N` (neighbours agree, so
they are not independent trials); a lone spike is the signature of a best-of-N draw from the null.
Where a sweep is genuinely needed, **report the plateau width alongside the chosen value** and treat
a narrow peak as disqualifying rather than merely suspicious.

**3. Sweep to characterise sensitivity, never to select.** A sweep answering *"how much does
performance degrade as `entry_lookback` moves from 20 to 80?"* is a robustness measurement. A sweep
answering *"which lookback is best?"* is a selection and costs `N`. Same computation, different
epistemic status — and the difference must be recorded, because only the second increments the
ledger. This mirrors §54.10's *"testing validates ideas — it is NOT for discovery."*

**4. Prefer changes that raise trade frequency over changes that raise per-trade edge.** MinBTL
scales as `1/SR_annualized²` and `SR_annualized = SR_trade × √(trades per year)`, so
**`MinBTL ∝ 1 / (SR_trade² × trades_per_year)`.** Doubling trade frequency **halves** MinBTL, at
constant per-trade quality. This is a genuinely new argument for §60.2 rank-and-fill deployment
cadence — not "more trades make more money" (which is uncertain) but **"more trades make the rule
knowable"** (which is arithmetic). It is also independent confirmation of the experiment's
Conclusion 2: the ADX gate should *not* be dropped for frequency, because that trade buys count at
the cost of `SR_trade`, and `SR_trade` enters MinBTL **squared** while frequency enters linearly.
**Buy frequency without degrading per-trade quality, or do not buy it.**

⇒ The experiment's *"under-deployment is an epistemics problem, not just a returns problem"* now has
a formula: `MinBTL ∝ 1/(SR_trade² × trades_per_year)`. Both terms are levers; one of them is squared.

---

## §73.14 ⛔ Halal exclusions, and what survives reformulation

| item | status |
|---|---|
| **Sharpe ratio as defined in Eq. 2.2** (excess over *"the rate of return paid by a risk-free asset, such as a Government Note"*) | ⚠️ **Riba in the benchmark. SURVIVES reformulation with `rf = 0`** — the derivation depends only on asymptotic normality and `y^(−1/2)` scaling, not on what the benchmark is. Consistent with `sim/metrics.py`'s existing `rf = 0` policy and with the KB's four prior declinations (§33, §50.1, §54.22, §68.6), which rejected Sharpe *as an optimization objective*, not as a test statistic. See §73.4. |
| **MinBTL (Thm 3.1), Prop 2.1, `E[max_N]`** | ✅ **Fully admissible.** Pure extreme-value statistics over an abstract return series. No rate, no instrument, no direction. |
| **Prop 6.1 / 6.2 / 6.3 (compensation effects)** | ✅ **Fully admissible.** Properties of AR(1) and re-centred processes. |
| **Information ratio** (mentioned in §1 and footnote 2) | ✅ **Admissible** — benchmark-relative, not rate-relative. Already flagged as adoptable in §54.10 and §54.22. |
| **`Side ∈ {−1, +1}` in Example 8.1's mesh** | ⛔ **The `−1` branch is excluded (short).** Our mesh has `Side = {+1}` only. Effect: **halves `N`**, i.e. the constraint *helps* us here. Consistent with §58.3 (long-only improved the tested breakout in both samples) — a second instance of the halal constraint not being a performance cost. |
| **Leverage** | ⚠️ Mentioned once, as a *consequence* of overfitting: *"believing in such an artificially enhanced high performance strategy will often lead to over-leveraging, so overfitting is still very damaging."* We do not leverage, so this specific harm channel is closed to us by construction (§28.1, §65). Nothing to exclude. |
| **Hedge-fund / mutual-fund industry context** (Sections 1, 7) | ⚪ Out of scope, not haram. Institutional framing; extracted only for §73.10's survivorship-bias point. |
| **PSR-Stat / Probabilistic Sharpe Ratio** (footnote 5, Ex 8.1) | ⚠️ **Cited, not derived here** (Bailey & López de Prado 2012). Same `rf = 0` reformulation would apply. **Not adopted** — §73.8 shows PSR is single-trial and gives a confidently wrong answer under multiplicity, which is our problem. A candidate future source, not an action. |
| **The "Deflated Sharpe Ratio" proper** | ⚠️ **NOT IN THIS PAPER.** DSR is Bailey & López de Prado's *later* work; this paper cites the companion *"The probability of backtest overfitting"* (ref [1]) as *"a more precise measure."* What this paper gives is MinBTL and `E[max_N]` — the *inputs* to deflation, and arguably the more directly usable form for us. **Logged as a gap:** the DSR/PBO paper is a well-targeted future source. |

> **Net: the only riba contact is the risk-free rate inside the Sharpe definition, and it is
> removable by substitution without touching a single step of the mathematics. No shorting, no
> leverage, no derivatives, no discounting anywhere in the derivation. This is the cleanest halal
> outcome of any quantitative-finance paper in the KB.**

---

## §73.15 Discarded (no agent value)

- **§10.1–§10.4, the mathematical appendices** (~4pp) — Fisher–Tippett–Gnedenko applied to the
  Gaussian, Gumbel normalizing constants from Resnick/Embrechts, and the algebra behind Props 6.1,
  6.2, 6.3. **The results are extracted above; the proofs add nothing implementable.** Recorded so
  no one re-mines them.
- **§10.5 "Reproducing the results"** — two dead-ish 2014 URLs (quantresearch.info,
  financial-math.org). No code in the document itself.
- **The polemic** (~3pp across §1.1, §7, §9) — Sir Andrew Wiles on protecting mathematics' good name,
  Leontief's 1982 *Science* editorial on academic economics, Newton and the South Sea Bubble, the
  attack on *"stochastic oscillators, Fibonacci ratios, cycles, Elliot wave, Golden ratio, parabolic
  SAR, pivot point"* as *"scientifically unsound."* Rhetorically excellent, operationally empty.
  ⚠️ One note worth keeping: that list **includes items the KB carries** — Fibonacci retracements as
  low-weight confluence (§59.6), pivot points as an untested candidate (§70.5). This paper offers no
  test of either, so it does not refute them; but it is a **second independent voice** (with §70's
  own admission that anchor selection *"becomes a guessing game"*) for keeping harmonics/Fib-inversion
  deferred and treating §70.5 pivots as a cheap ablation rather than a build commitment.
- **Figures 3–8** — Monte-Carlo scatter plots. Numerical content extracted (slopes, adj R², ranges);
  the images add nothing.
- **§1's survey of econometric-overfitting literature** (White's Reality Check, Romano–Wolf stepwise
  testing, Harvey et al.) — the paper explains why these do not transfer to investment strategies
  (they need explicit point forecasts with defined horizons; our rules emit qualitative
  buy/hold/exit signals over undefined periods, exactly as described). **Correctly excluded by the
  paper's own argument, and its reasoning applies verbatim to `keel`'s rules.**
- **The machine-learning overfitting literature** (Section 1) — dismissed by the authors for four
  stated reasons, the fourth being *"some methods do not control for the number of trials
  attempted."* Consistent with the KB's standing exclusion of black-box models (§54, §58.16, §68.6).

---

## Net assessment

**A landmark paper, and the highest-value non-trading source in the KB.** It contains no rule, no
indicator, no parameter — and it is nonetheless the most consequential thing extracted since §58,
because it is the first source that quantifies **whether our validation method can work at all** on
the data we have.

Three findings that change the project's posture:

1. **MinBTL reproduces today's experiment to three significant figures, then refines it downward.**
   The ~68 trades / ~26 years figure is MinBTL at exactly **N = 26 trials**. We have swept far more
   than 26 configurations, so the true requirement is 2×–3× larger — **~55 years at N ≈ 336.** The
   experiment's arithmetic is confirmed; its conclusion was the optimistic case.
2. **The deflation machinery survives `rf = 0` completely**, applied to a **per-trade** Sharpe (which
   simultaneously resolves §54.22's intermittent-returns objection, since flat days never enter).
   Sortino keeps its role as the **verdict** statistic. No risk-free rate anywhere. The KB's four
   prior Sharpe declinations were about Sharpe *as an objective*; this is Sharpe *as a null-test
   statistic*, and the distinction finally pays.
3. **The KB's own function is reframed.** If our data can support only **2–3 fitted parameters**,
   then every parameter justifiable from external literature is a parameter we do not have to spend
   `N` on. **The knowledge base is a trials-budget subsidy.** That is a better argument for its
   existence than "more knowledge is better," and it makes `a_priori` vs `fitted` parameter
   provenance (§73.12 #4) the cheapest high-value change available.

**Honest about limits.** MinBTL is *necessary, not sufficient*. The independent-trials assumption
makes a raw grid-size `N` conservative. The per-trade Sharpe reformulation is sound but is our
extension, not the paper's. And the hardest part of implementing any of this is not the arithmetic —
it is **counting `N` honestly across sessions**, which is a discipline problem that no amount of code
solves on its own.

**Untranslatable pieces, flagged as required:** the compensation-effect propositions (6.1/6.3) are
proved for continuous processes with equal IS/OOS standard deviations; our discrete daily-bar,
non-Gaussian, fee-and-slippage-laden backtest satisfies neither exactly. They should be read as
**direction-of-effect** results — *in-sample optimization on a memory-bearing series hurts OOS* — and
not as a quantitative prediction of how much. Likewise `E[max_N]` assumes trials are independent
draws from a null; our sweep nodes are heavily correlated, so the effective `N` is smaller than the
grid size by an unknown factor. Both approximations err toward **over**-stating the required
evidence, which for a promotion gate is the safe direction — but a reader should not mistake
"~55 years" for a measurement.
