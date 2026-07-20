[← Knowledge Base index](../README.md)

## Source 78 — The three operational tools §73 left as a gap: Deflated Sharpe Ratio, PBO/CSCV, and the Haircut Sharpe Ratio

(A) David H. Bailey & Marcos López de Prado, **"The Deflated Sharpe Ratio: Correcting for Selection Bias,
Backtest Overfitting and Non-Normality"**, *Journal of Portfolio Management* 40(5), 2014, 94–107 (22pp;
first version Apr 2014, this version Jul 2014; SSRN 2460551)
(B) David H. Bailey, Jonathan M. Borwein, Marcos López de Prado & Qiji Jim Zhu, **"The Probability of
Backtest Overfitting"**, *Journal of Computational Finance*, revised version Feb 2015 (34pp; SSRN 2326253 /
test cases at SSRN 2568435)
(C) Campbell R. Harvey & Yan Liu, **"Backtesting"**, *Journal of Portfolio Management*, Fall 2015, 12–28
(17pp; code at `faculty.fuqua.duke.edu/~charvey/backtesting`)

> **This source exists because §73 logged a specific gap and named the papers that fill it.**
> §73.14's final row reads: *"The 'Deflated Sharpe Ratio' proper — **NOT IN THIS PAPER.** … **Logged as a
> gap:** the DSR/PBO paper is a well-targeted future source."* (A) and (B) are literally that paper pair,
> by the same four authors. §73 supplied the **diagnosis** — MinBTL, `E[max_N]`, and the argument that a
> backtest without a trial count is not weak evidence but *no* evidence. §78 supplies the **instruments**:
> a deflated significance statistic, a non-parametric overfit probability, and — most importantly for us —
> **a procedure for converting our heavily-correlated swept configurations into an honest count of
> independent trials.**
>
> Nothing here restates §73. Every section below either extends a §73 result, supplies the missing
> operational form of one, or contests one.

⚠️ **One thing to read before §78.9.** §73.15 recorded that Bailey et al. *dismiss* the White /
Romano–Wolf / **Harvey et al.** econometric family as not transferring to investment strategies. Paper (C)
**is** Harvey. Admitting it means admitting a method §73 partly waved off. §78.11 handles that head-on and
does **not** fully resolve it.

**Halal:** these are three statistics papers. No instrument, no rate, no direction, no discounting. The
only riba contact is the same one §73.4 already resolved (the risk-free rate inside the Sharpe definition,
removable by `rf = 0` without touching the algebra). One new item — (C)'s worked strategies are long/short
hedge portfolios — is a *context* exclusion, not a method exclusion. Full table at §78.14.

**Implementability note that governs the whole file.** `keel` uses **Python stdlib + `Decimal` only** —
NumPy, Pandas, SciPy and statsmodels have all been explicitly declined. Every formula below carries an
explicit verdict on what a stdlib implementation needs. Short version: **DSR, `E[max_N]`, the implied-`N`
conversion and CSCV are all fully stdlib-portable**; Harvey & Liu's Bonferroni/Holm/BHY adjustments are
stdlib-portable *given a t-distribution CDF*, and their full correlation-adjusted model needs a Monte-Carlo
loop that `random` can supply. **All three papers' published worked examples were reproduced exactly with
stdlib-only code while extracting this source** (§78.3, §78.6) — that is the strongest available evidence
the port is real and not aspirational.

---

## §78.1 ⭐ The general `E[max SR]` — §73.1's formula was the zero-mean, unit-variance special case
**Module: `strategy/promotion.py`**

(A)'s Eq. (1), proved in its Appendix A.1:

```
E[max{SR_n}]  ≈  E[{SR_n}]  +  √V[{SR_n}] · ( (1−γ)·Z⁻¹[1 − 1/N]  +  γ·Z⁻¹[1 − 1/(N·e)] )

    γ  = 0.5772156649…   (Euler–Mascheroni)
    Z⁻¹ = inverse standard-normal CDF
    N  = number of INDEPENDENT trials,  N ≫ 1
```

The derivation is two lines: standardise `y_n ~ N(μ,σ²)` to `x_n = (y_n − μ)/σ`; because `σ > 0` preserves
order, `max{y_n} = μ + σ·max{x_n}`; expectation is linear. **§73.1's `E[max_N]` is exactly the bracketed
term** — i.e. §73 used the special case `E[{SR_n}] = 0`, `V[{SR_n}] = 1`.

**Why the general form matters to us and the special case does not quite.** §73.1 assumed *every trial's
true Sharpe is zero and the estimator has unit variance*. That is the right null for the question "could
this be pure luck?" But when we sweep `entry_lookback ∈ {20,30,40,55,80}` the resulting `SR` estimates are
**not** unit-variance draws — they are tightly clustered, because the configurations are near-duplicates.
`V[{SR_n}]` is a **measurable property of our own sweep output**, and it is small. Small `V` shrinks the
selection-bias term proportionally to `√V`.

⇒ **This is the first of two corrections in this source that push in our favour**, and both require the
trials ledger to record more than a count:

> The ledger must store, per trial, **the trial's own performance statistic** — not just that a trial
> happened. `E[{SR_n}]` and `V[{SR_n}]` are then free. A ledger that stores only `N` cannot compute the
> general form and is stuck with §73.1's harsher unit-variance assumption.

**Exhibit 1 numbers, worth carrying:** at `E[{SR_n}] = 0`, going from `V = 1` to `V = 4` roughly **doubles**
`E[max{SR_n}]` at every `N` (≈3.25 → ≈6.5 at N = 1000). Dispersion across trials is as strong a driver of
selection bias as trial count. A sweep that produces wildly varying Sharpes is *more* dangerous than one
that produces a flat plateau — which is **§54.10's robustness plateau and §73.13 #2, now with the exact
mechanism**: the plateau is safer both because neighbours are correlated (§78.2) *and* because `V` is small.

> **Implementability: ✅ fully stdlib.** Needs `math.log/sqrt/exp` plus an inverse-normal CDF. The
> Acklam rational approximation is ~20 lines of arithmetic and reproduces the paper's published
> `E[max]` values to 4 decimals (verified: N=10 → 1.5746, N=128 → 2.6163, N=1000 → 3.2551, matching
> §73.1's table exactly). **No new dependency.** ⚠️ In `Decimal` the rational approximation needs its
> coefficients declared as `Decimal` strings; simplest is to compute `Z⁻¹` in `float` and convert —
> the input `1 − 1/N` is exact and the output feeds a ratio, so float precision is not the binding
> constraint here. (A) itself computes this in `float` via `scipy.stats.norm.ppf`.

---

## §78.2 ⭐⭐⭐ Appendix A.3 — converting M DEPENDENT trials into N IMPLIED INDEPENDENT trials
**Module: `strategy/promotion.py`, `strategy/backtest.py`**

**This is the single most important item in the source for this project**, because it is the one that
makes the planned trials ledger *honest* rather than merely *large*.

(A) states the problem in its own words:

> *"It is critical to understand that the N used to compute `E[max{SR_n}]` corresponds to the number of
> **independent** trials. Suppose that we run M trials, where only N trials are independent, N < M.
> Clearly, using M instead of N will overstate `E[max{SR_n}]`. So given M dependent trials we need to
> derive the number of 'implied independent trials', N̂."*

That is precisely §73.2's caveat #1 (*"`N` must be the number of independent trials… the paper suggests
PCA-style dimension reduction"*) — **but here it comes with a closed-form procedure instead of a
suggestion.**

### The procedure, in full

**Step 1 — average off-diagonal correlation.** Form the `M × M` correlation matrix `C` of the trials'
performance series. Let `C̃` be `C` with every off-diagonal entry replaced by a single constant `ρ`.
Choose `ρ` so the quadratic form is unchanged for the unit vector `x = 1_M`. That reduces to the plain
equal-weighted average of the off-diagonals (Eq. 8):

```
        Σᵢ Σⱼ ρᵢⱼ − M          2 · Σᵢ Σⱼ>ᵢ ρᵢⱼ
ρ̂  =  ─────────────────  =  ──────────────────
          M(M − 1)               M(M − 1)
```

**Step 2 — the bound.** A proper correlation matrix is positive-definite, so `1'C1 = M + M(M−1)ρ > 0`,
giving `ρ ∈ (−1/(M−1), 1]`. For large `M`, `−1/(M−1) ≈ 0`, so in practice `0 < ρ ≤ 1`. (A) notes
*"the larger the number of trials, the more positive the average correlation is likely to be."*

**Step 3 — interpolate.** As `ρ → 1` all trials collapse into one, `N → 1`. As `ρ → 0` they are all
independent, `N → M`. Interpolate linearly between the extremes (Eq. 9):

```
                    ⭐  N̂  =  ρ̂  +  (1 − ρ̂)·M   ⭐
```

### What this does to §73.3's verdict on `keel` — computed

§73.3 priced our grid at `M ≈ 336` (six `TurtleBreakout` parameters) and concluded **~55 years / 143
trades required**. Applying Eq. (9), at our honest `SR_annualized = 0.395` and 5.0 yr of BTC/ETH history:

| M (raw grid) | ρ̂ assumed | **N̂ implied** | `E[max_N̂]` | **MinBTL** | trades | data/MinBTL ratio |
|---:|---:|---:|---:|---:|---:|---:|
| 336 | 0.00 | 336.0 | 2.931 | **55.1 yr** | 143 | 0.091 ← §73.3's figure |
| 336 | 0.50 | 168.5 | 2.709 | 47.0 yr | 122 | 0.106 |
| 336 | 0.75 | 84.8 | 2.472 | 39.2 yr | 102 | 0.128 |
| 336 | **0.90** | **34.5** | 2.130 | **29.1 yr** | 76 | 0.172 |
| 336 | 0.95 | 17.8 | 1.848 | **21.9 yr** | 57 | 0.229 |
| 2,016 | 0.90 | 202.5 | 2.770 | 49.2 yr | 128 | 0.102 |

**Read the M = 336 column downward. This is the finding.**

> **§73.3's "~55 years, ratio 0.09" is the ρ̂ = 0 row — i.e. it assumed our 336 configurations were
> 336 genuinely independent experiments. They plainly are not.** `entry_lookback ∈ {20, 40, 55}` on the
> same 1,819 BTC bars produce trade sequences that overlap heavily; a plausible ρ̂ for a Donchian-lookback
> sweep is **0.85–0.95**, not 0. At ρ̂ = 0.90 the requirement falls from **55 years to 29 years**, and the
> effective trial count from 336 to **≈ 34**.

Three consequences, in order of importance:

1. **§73.3's headline number is a defensible upper bound, but it is not the estimate.** §73 said so
   itself (*"a raw grid-size count of N is conservative — which for a promotion gate is the right
   direction to err, but we should say which we are using"*). §78.2 lets us stop erring and start
   measuring. **The `§73.3` figure should be relabelled `MinBTL(ρ̂ = 0)` in the sim report, with the
   measured-ρ̂ figure alongside it.**
2. ⚠️ **It does NOT rescue the project.** Even at an aggressive ρ̂ = 0.95, MinBTL is **21.9 years against
   5.0 available** — a ratio of 0.23. §73's conclusion that *"the specific values 40 and 20 are not
   defensible as selected by walk-forward"* survives every value of ρ̂ in the admissible range. The
   correction changes 11× short to 4× short. **It is a real correction and it is not a reprieve.**
3. ⭐ **It gives the ledger a second, better-founded reason to record per-trial return series.** ρ̂ is
   computable *only* if the ledger keeps each trial's P&L series, not just its headline number. That is
   the same requirement as §78.1's `V[{SR_n}]` **and the same requirement as CSCV's matrix `M`
   (§78.6)** — three of this source's four tools need the identical artifact. ⇒ **Design the ledger row
   as `(config, provenance, per-bar or per-trade P&L series)` once, and all three become computable.**

### ⚠️ (A)'s own two warnings about this method, both of which bind on us

> *"First, correlation is a limited notion of linear dependence. Second, in practice M almost always
> exceeds the sample length, T. Then the estimate of average correlation may itself be overfit. In general
> for short samples `T < ½·M(M−1)`, the correlation matrix will be numerically ill-conditioned, and it is
> not guaranteed that `1'C1 > 0`. Estimating an average correlation is then pointless, because there are
> more correlations than independent pairs of observations!"*

**Check this against our numbers before shipping.** With `M = 336`, `½·M(M−1) = 56,280` — vastly more than
`T = 1,819` daily bars, and astronomically more than our ~13–30 *trades*. **By (A)'s own test, a
correlation matrix estimated across all 336 configurations on our data is ill-conditioned and its ρ̂ is
not to be trusted.** Two admissible responses:

- **Estimate ρ̂ on a small, deliberately-chosen subset** (e.g. the 5–8 configurations actually compared in
  a given sweep), where `½·M(M−1)` is 10–28 and `T` is 1,819. This is well-conditioned and is also the
  honest unit — we did not run 336 independent decisions, we ran a handful of sweeps.
- **Report ρ̂ as an assumption with a sensitivity band**, exactly as the table above does, rather than as
  a measurement. This is the safer default for the first release: *"MinBTL is 55 yr at ρ̂=0 and 29 yr at
  ρ̂=0.9; we have 5."* The conclusion is invariant, so the assumption is not load-bearing.

(A) offers two further paths — dimension reduction on the correlation matrix, and information-theoretic
estimates of non-redundant sources (entropy / total correlation, citing Watanabe 1960 and Studený &
Vejnarová 1999). **Both are declined here:** the first is the PCA route §73.2 already flagged and needs an
eigen-decomposition we have no library for; the second is a research project. Logged so no one re-mines
them.

> **Implementability: ✅ fully stdlib, and cheap.** Pearson correlation of two equal-length lists is
> ~8 lines of `sum`/`sqrt`. `ρ̂` is a double loop over the upper triangle. Eq. (9) is one line.
> For `M = 8` that is 28 correlations. `Decimal`-safe throughout (only `sqrt`, which `Decimal` has
> natively). **⚠️ The hard part is not the arithmetic — it is deciding which trials belong in the
> matrix, which is the same discipline problem §73.6 identified and no code solves.**

---

## §78.3 ⭐⭐ The Deflated Sharpe Ratio — the full formula, with the non-Normality terms
**Module: `sim/report.py`, `strategy/promotion.py`**

(A) Eq. (2). DSR is the Probabilistic Sharpe Ratio (Bailey & López de Prado 2012a) evaluated against a
**multiplicity-adjusted** rejection threshold rather than zero:

```
                              ⎡        (ŜR − ŜR₀) · √(T − 1)          ⎤
DSR  ≡  PSR(ŜR₀)  =   Z ⎢ ─────────────────────────────────────── ⎥
                              ⎣   √( 1 − γ̂₃·ŜR + ((γ̂₄ − 1)/4)·ŜR² )   ⎦

where   ŜR₀ = √V[{SR_n}] · ( (1−γ)·Z⁻¹[1 − 1/N] + γ·Z⁻¹[1 − 1/(N·e)] )      ← §78.1, i.e. E[max] at μ=0
```

| symbol | meaning | where we get it |
|---|---|---|
| `ŜR` | the selected strategy's estimated Sharpe | `BacktestResult` (per-trade, rf = 0, §73.4) |
| `ŜR₀` | the **rejection threshold**: expected max SR under the null of zero true skill | §78.1 × §78.2's `N̂` |
| `T` | sample length, in units of the return series | number of **trades** under §73.4's per-trade unit |
| `γ̂₃` | **skewness** of the returns distribution | new — computable from the trade P&L list |
| `γ̂₄` | **kurtosis** (non-excess; Normal = 3) | new — computable from the trade P&L list |
| `Z` | standard-normal CDF | `0.5·(1 + erf(x/√2))`, `math.erf` is stdlib |
| `N` | **independent** trials | §78.2 |

**⚠️ Unit discipline, which is easy to get wrong and which the paper's example silently exercises:**
`ŜR` and `ŜR₀` must be in the **same** unit. (A)'s example carries `V[{SR_n}] = ½` as an *annualized*
variance and de-annualizes by dividing by 250 observations/year: `ŜR₀ = √(V/250) · E[max]`. Under §73.4's
per-trade formulation **there is nothing to de-annualize** — `V` is the variance of per-trade Sharpes
across trials, `ŜR` is the per-trade Sharpe, `T` is the trade count. That is simpler than the paper's
setup, not harder.

### How the non-Normality terms behave — the part that is genuinely new to the KB

The denominator `√(1 − γ̂₃·ŜR + ((γ̂₄−1)/4)·ŜR²)` is the standard error of the Sharpe estimator under
non-Normal returns. Its direction of effect:

- **Negative skew inflates the denominator ⇒ deflates DSR.** A strategy that mostly wins small and
  occasionally loses big has a *less* reliable Sharpe than its point estimate suggests.
- **Fat tails (`γ̂₄ > 3`) inflate the denominator ⇒ deflate DSR.**
- Positive skew *reduces* the denominator and **raises** DSR.

**This matters more for `keel` than for (A)'s hedge-fund audience, and in the opposite direction.**
A long-only Turtle breakout with a hard 2N ATR stop is a **positive-skew** strategy by construction — many
small stopped-out losses, a few large trend rides. §54.11's breakout profile and §27's Turtle spec both
describe exactly that shape. So:

> ⭐ **The non-Normality correction is likely to work in our favour**, because `γ̂₃ > 0` *reduces* the
> denominator. This is the second of the two corrections in this source that push our way (§78.2 was the
> first). Neither is large enough to change the verdict, but both should be computed rather than assumed —
> and both are free, given a ledger that stores trade P&L.

⚠️ **Counter-caution, non-negotiable:** the same positive skew means the per-trade P&L distribution is far
from Normal, and `T` for us is **13 trades on BTC** (30 pooled). Skewness and kurtosis estimated on 13
observations are close to noise. **Report `γ̂₃`/`γ̂₄` with their sample size attached, and default to
`γ̂₃ = 0, γ̂₄ = 3` (the Normal case) when `T < 30`** — that reduces DSR to plain PSR-against-a-deflated-
threshold and is the conservative choice.

### The paper's worked example — and its verification

A strategist backtests treasury-auction seasonality combinations, finds `ŜR = 2.5` annualized over 5 years
of daily data, and pitches it. The investor demands four disclosures: `N = 100`, `V[{SR_n}] = ½`,
`T = 1250`, `γ̂₃ = −3`, `γ̂₄ = 10`. Result:

```
ŜR₀ = 0.1132 (non-annualized)      DSR = 0.9004  <  0.95      ⇒  investor declines
```

Two counterfactuals the paper draws out, both instructive:

- **At `N = 46` instead of 100, `DSR = 0.9505`** — it would have passed. *"Should the strategist have made
  his discovery after running only N = 46 independent trials, the investor may have allocated some funds."*
- **With Normal returns (`γ̂₃ = 0, γ̂₄ = 3`), `DSR = 0.9505` at `N = 88`** — i.e. the non-Normality alone
  cost 42 trials' worth of budget. *"If non-Normal returns had not inflated the performance so much, the
  investor would have been willing to accept a much larger number of trials."*

> ✅ **All four of these numbers — 0.1132, 0.9004, 0.9505 at N=46, and 0.9505 at N=88 under Normality —
> were reproduced to four decimal places using Python stdlib only** (`math` + an Acklam inverse-normal +
> `math.erf`) while extracting this source. **The DSR is a solved implementation problem for this
> project, not a research task.**

> **Implementability: ✅ fully stdlib, ~30 lines total.** Needs: `math.erf` (stdlib, for `Z`), an
> inverse-normal (§78.1), and sample skew/kurtosis (two passes over a list). **`Decimal` note:** `erf`
> is not a `Decimal` method — compute the final CDF in `float`. This is safe because DSR is a probability
> read to 2–3 decimals, not a money quantity; the `Decimal` discipline exists for balances and prices
> (§28/§30 spot-settlement exactness), not for report statistics. **State that boundary explicitly in the
> code** so the next reader does not "fix" it.

---

## §78.4 ⭐ DSR computed for `keel`'s Turtle — and why it is currently uncomputable-in-principle
**Module: `sim/report.py`**

Using §73.3's honest inputs (`SR_trade = 0.245`, rf = 0, per-trade unit) and §78.2's implied-`N`:

| `T` (trades) | M | ρ̂ | N̂ | `V[{SR_n}]` | ŜR₀ | **DSR** |
|---:|---:|---:|---:|---:|---:|---:|
| 13 (BTC) | 336 | 0.90 | 34.5 | 0.01 | 0.213 | **0.54** |
| 13 | 336 | 0.90 | 34.5 | 0.04 | 0.426 | **0.27** |
| 30 (pooled) | 336 | 0.90 | 34.5 | 0.01 | 0.213 | **0.57** |
| 30 | 336 | 0.75 | 84.8 | 0.04 | 0.494 | **0.09** |
| 68 (the target) | 336 | 0.90 | 34.5 | 0.01 | 0.213 | **0.60** |
| 68 | 26 | 0.90 | 3.5 | 0.01 | 0.096 | **0.88** |

(`γ̂₃ = 0, γ̂₄ = 3` throughout, per the `T < 30` rule above.)

**Three readings, all uncomfortable and all useful:**

1. **Nothing in this table reaches 0.95.** The conventional DSR bar is *"is the true SR greater than the
   deflated threshold at 95% confidence?"* Our best case — 68 trades, only 26 raw trials, ρ̂ = 0.9 —
   reaches **0.88**. The realistic present case is **0.3–0.6**, i.e. a coin flip.
2. ⚠️ **`V[{SR_n}]` is currently a guess, and it swings the answer by 3×** (0.54 → 0.27 at `T=13`).
   That single unmeasured quantity dominates the result. **This is the strongest argument in the source
   for building the ledger before the next sweep, and it is sharper than §73.6's argument.** §73 said
   *"without `N` there is no threshold."* §78 says: **even with `N`, without `V[{SR_n}]` the threshold is
   uncertain by a factor of two, so the very next sweep must record every node's Sharpe as it runs —
   because after the sweep is over that information is gone.** A sweep run without the ledger does not
   merely fail to increment a counter; it **destroys the data needed to score itself**.
3. **The `T = 68` row is the bridge to §78.12.** Trade count enters DSR through `√(T−1)`, so more trades
   raise DSR at fixed everything-else. Deflation and deployment are not purely opposed. See §78.12.

⚠️ **Do not ship this as a blocking gate.** §73.12 already argued the MinBTL gate must ship
reporting-only first, *"because a gate that blocks everything on day one gets disabled rather than
heeded."* DSR fails harder and for the same reason. **Ship DSR as a reported number with its inputs
itemised** (`T`, `N̂`, `M`, `ρ̂`, `V`, `γ̂₃`, `γ̂₄`) so that a reader can see *which* input is killing it —
which, per row 2, is not the strategy but our own missing bookkeeping.

---

## §78.5 ⭐ "When should we stop testing?" — the 1/e rule, a pre-registration protocol we can actually follow
**Module: `strategy/backtest.py` (sweep driver), process**

(A) closes with an answer to a question §73 raised but did not resolve. §73.12 #3 gave the trials-budget
inversion (`N ≤ 3` at our SR) and admitted *"enforcing this literally would end parameter sweeping."*
(A) offers a procedure instead of a prohibition, from optimal-stopping theory (the secretary problem /
Bruss's 1/e law):

> *"From the set of strategy configurations that are theoretically justifiable, sample a fraction 1/e of
> them (roughly 37%) at random and measure their performance. After that, keep drawing and measuring the
> performance of additional configurations from that set, one by one, until you find one that beats all of
> the previous. That is the optimal number of trials, and that 'best so far' strategy the one that should
> be selected."*

And the framing that makes it more than a heuristic:

> *"Multiple testing exercises should be carefully planned in advance, so as to avoid running an
> unnecessarily large number of trials. **Investment theory, not computational power, should motivate what
> experiments are worth conducting.**"*

**Why this is a real fit for us and not a curiosity.** Note the precondition: *"the set of configurations
that are **theoretically justifiable**."* The 1/e rule operates on a **pre-declared, externally-motivated
candidate set** — which is exactly §73.13 #1's *"reclassify most parameters as `a_priori`"* and §74.13's
observation that [A]/[B] restrained themselves to *"the most popular ones"* from Brock et al. to control
data snooping. ⇒ **The three fit together into one protocol:**

```
1. Declare the candidate set from the KB, not from the data.      (§73.13 #1, §74.13)
   e.g. donchian_entry_n ∈ {40, 55, 80, 95, 150, 200}             (§54.11, §58.6, §74.2)
2. Freeze it. Log it. This is M, and it is now small (6, not 336).
3. Measure ⌊6/e⌋ = 2 of them at random. Record. Select none.
4. Continue one at a time; take the first that beats all previous.
5. Increment the ledger by 6, not by "one sweep".
```

⚠️ **Honest limit:** the 1/e rule optimises *"choose a near-best as soon as possible"*, i.e. it minimises
trials-spent. It does **not** make the resulting selection statistically valid — MinBTL still says we
cannot afford 6 fitted trials at SR 0.395. Its value is that it converts an unbounded sweep into a
**bounded, pre-registered, ledger-able** procedure, and it gives the sweep driver a stopping rule that is
not "when the numbers look good." **Adopt it as sweep protocol; do not mistake it for a validity test.**

> **Implementability: ✅ trivial** — `random.sample`, a loop, a comparison. Zero new maths. The cost is
> entirely in the discipline of declaring the candidate set first.

---

## §78.6 ⭐⭐ PBO via CSCV — the algorithm, in enough detail to implement directly
**Module: `strategy/backtest.py`, `sim/report.py`**

(B)'s contribution is a **model-free, non-parametric, deterministic** estimate of the probability that a
backtest selection process is overfit. It needs **no distributional assumption, no forecasting model, and
no knowledge of the trading rule** — only the matrix of per-period P&L across the configurations tried.

**Definition 2.2 (PBO).** *"A strategy with optimal performance IS is not necessarily optimal OOS.
Moreover, there is a non-null probability that this strategy with optimal performance IS ranks below the
median OOS. This is what we define as the probability of backtest overfit."*

### Algorithm 2.3 (CSCV) — verbatim structure, lettered as in the paper

**First**, form a matrix `M` of order `(T × N)`: each of the `N` columns is one configuration's P&L series
over `t = 1…T`. Two conditions only:
  - (i) it is a true matrix — same rows for every column, observations synchronous across trials;
  - (ii) the performance metric can be estimated on subsamples of each column.
  *"If different model configurations trade with different frequencies, observations should be aggregated
  to match a common index."*

**Second**, partition `M` across rows into an **even** number `S` of disjoint submatrices `M_s` of equal
dimensions, each of order `(T/S × N)`.

**Third**, form all combinations `C_S` of the `M_s` taken in groups of size `S/2`:

```
             ⎛  S  ⎞      S/2−1   S − i
 #(C_S)  =  ⎜     ⎟  =    ∏     ─────────
             ⎝ S/2 ⎠       i=0    S/2 − i
```

**Fourth**, for each combination `c ∈ C_S`:

  a) Form the **training set `J`** by joining the `S/2` submatrices that constitute `c`, **in their
     original order**. `J` is `(T/2 × N)`.
  b) Form the **testing set `J̄`** as the complement of `J` in `M` — all rows not in `J`, in their
     original order. *"The order in forming J and J̄ does not matter for some performance measures such
     as the Sharpe ratio but does matter for others e.g. return/maximum-drawdown ratio."*
  c) Form a vector `R^c` of `N` performance statistics, the `n`-th being the performance of column `n`
     of the **training set**. Let `r^c` be the rank of the components of `R^c` (the IS ranking).
  d) Repeat (c) on `J̄` to obtain `R̄^c` and `r̄^c` — the OOS statistics and ranks.
  e) Determine `n*`, the index of the **best-performing strategy IS** (`r^c_{n*} = N`).
  f) Define the **relative rank of the IS-best strategy's OOS rank**:
     `ω̄_c := r̄^c_{n*} / (N + 1)  ∈ (0,1)`.
  g) Define the **logit** `λ_c = ln( ω̄_c / (1 − ω̄_c) )`.
     *"High logit values imply a consistency between IS and OOS performances, which indicates a low level
     of backtest overfitting."*

**Fifth**, collect all `λ_c` over `c ∈ C_S` into the distribution `f(λ)` (relative frequency; integrates
to 1). Then:

```
                      ⭐  PBO  =  φ  =  ∫₋∞⁰ f(λ) dλ   =   fraction of combinations with  λ_c ≤ 0  ⭐
```

*"This represents the rate at which optimal IS strategies underperform the median of the OOS trials."*
`φ ≈ 0` ⇒ no significant overfitting. `φ ≈ 1` ⇒ high likelihood of overfitting. *"In accordance with
standard applications of the Neyman-Pearson framework, a customary approach would be to reject models for
which PBO is estimated to be greater than 0.05."*

### ⭐ This is a near-perfect fit for a stdlib codebase

> **Implementability: ✅ fully stdlib, and the cleanest fit of anything in §73–§78.**
> `itertools.combinations` (stdlib) for step Three. List slicing and concatenation for (a)/(b).
> `sorted()` with an index for the ranks in (c)/(d). `math.log` for the logit in (g). A counter for the
> integral. **No matrix algebra, no distribution, no inverse CDF, no RNG.** `Decimal` throughout is fine
> — the only transcendental is `log`, applied to a ratio of ranks, and `Decimal.ln()` exists natively.
> Estimated ~60 lines. **This should be built before DSR**, despite DSR having the higher profile,
> because it has fewer unmeasured inputs (§78.4 row 2) and no distributional assumptions.

### Parameter choices, computed for our data

(B) recommends `S = 16` — *"S must be large enough so that the number of combinations suffices to draw
inference"* but *"if we believe that the performance series is time-dependent and incorporates seasonal
effects, S cannot be too large, or the relevant time structure may be shuttered across the partitions."*

| `S` | combinations `C(S, S/2)` | σ[f(λ)] ≤ √(1/4·#C) | rows per submatrix at `T = 1,819` |
|---:|---:|---:|---:|
| 8 | 70 | 0.0598 | 227 (~9 months) |
| 10 | 252 | 0.0315 | 181 (~7 months) |
| 12 | 924 | 0.0165 | 151 (~5 months) |
| **16** | **12,870** | **0.0044** | **113 (~quarterly)** ← (B)'s recommendation |

⚠️ **(B) states "if S = 16, we will form 12,780 combinations." The correct value is C(16,8) = 12,870.**
A transposition typo in the paper; its σ estimate (`<0.0045`) is right for 12,870. Noted so an
implementer does not chase the discrepancy. (The paper's own reasoning — *"if M contains 4 years of daily
data, S = 16 would equate to quarterly partitions, and the serial correlation structure would be
preserved"* — matches our 5 years / 1,819 bars almost exactly, so **S = 16 transfers directly**.)

**On `N` (the number of columns / configurations):** *"N must be large enough to provide sufficient
granularity to the values of the relative rank ω̄_c. If N is too small, ω̄_c will take only a very few
values… making f(λ) too discontinuous… if the investor is sensitive to values of φ < 1/10, it is clear
that the range of values that the logits can adopt must be greater than 10, and so N ≫ 10 is required."*

> ⚠️ **This creates a direct, unavoidable tension with §73.** MinBTL says *sweep fewer configurations*
> (`N ≤ 3`). CSCV says *to measure whether your sweeping overfit, you need `N ≫ 10` columns.* Both are by
> the same authors. **The resolution is that these are different `N`s and the tension is only apparent:**
> MinBTL's `N` counts trials whose outcome **influenced a shipped decision**; CSCV's `N` counts columns in
> a **diagnostic matrix**. Per §73.13 #3 — *"sweep to characterise sensitivity, never to select"* — a
> CSCV run is precisely a sensitivity characterisation whose output is a probability, not a parameter
> value. **A CSCV diagnostic does not increment the ledger, provided its selected column is discarded.**
> That condition must be enforced by process, and it is exactly where the discipline will fail if
> unwatched. See §78.7's Strathern warning.

**On `T`:** *"PBO is evaluated by comparing combinations of T/2 observations with their complements. But
the backtest works with T observations… Therefore, T should be chosen to be double the number of
observations used by the investor to choose a model configuration."* ⚠️ For us, `T` in daily bars is
1,819 — fine. `T` in **trades** is 13–30, which halved is 6–15, which is **not enough to compute a
per-trade Sharpe on**. ⇒ **Run CSCV on the daily-bar P&L series, not the per-trade series.** This is the
one place in §73–§78 where §73.4's per-trade unit choice must be set aside — and it reintroduces §54.22's
mostly-cash-days objection into the CSCV metric. **Mitigation: use return/max-drawdown or Sortino as the
CSCV performance metric rather than Sharpe** — (B) is explicit that the procedure is *"generic and can be
applied to any performance evaluation metric R (Sortino ratio, Jensen's Alpha, Probabilistic Sharpe Ratio,
etc.)."* This aligns CSCV with §54.22's and §73.4's endorsed **verdict** statistic. ⚠️ But then step (b)'s
order caveat binds: drawdown-based metrics are order-dependent, so submatrices must be joined in original
order. The algorithm above already says so; it is easy to get wrong.

---

## §78.7 ⚠️ CSCV's limitations and the two ways to misuse it
**Module: process; `strategy/promotion.py`**

(B) devotes a full section to this, and three of its five application limits land on us:

1. ⭐ **The file-drawer requirement.** *"The researcher must provide full information regarding the actual
   trials conducted… **Hiding trials will lead to an underestimation of the overfit**, because each logit
   will be evaluated under a biased relative rank ω̄_c."* And symmetrically: *"adding trials that are
   doomed to fail in order to make one particular model configuration succeed biases the result. If a
   model configuration is obviously flawed, it should have never been tried in the first place."*
   ⇒ **PBO is gameable in both directions.** Padding the matrix with deliberate losers *lowers* PBO. This
   is the same discipline problem as §73.6/§73.10, now with an exploitable failure mode attached.
2. **Guided searches.** *"the columns of matrix M should be the final outcome of each guided search (i.e.,
   after it has converged to a solution), and not the intermediate steps."* Relevant if the deferred
   LLM-proposal feature (§35.1, §64.7, §73.9) ever ships: an LLM iterating toward a config contributes
   **one** column, not one per iteration.
3. **It does not check whether the backtest is correct.** *"If the backtest is flawed due to bad
   assumptions, such as incorrect transaction costs or using data not available at the moment of making a
   decision, our approach will be making an assessment based on flawed information."* ⇒ **PBO is
   orthogonal to look-ahead bias and fee realism.** It does not substitute for `strategy/backtest.py`'s
   intrabar order-of-events and spread/slippage modelling. Both are needed.
4. **A high PBO does not mean no strategy is skillful.** *"it is entirely possible that all the N
   strategies have high but similar Sharpe ratios. Since none of the strategies is clearly better than the
   rest, PBO will be high. Here overfitting is among many 'skillful' strategies."* ⚠️ **This is our
   plateau case exactly.** §54.10/§73.13 tell us to prefer a broad plateau; a broad plateau is a set of
   near-identical configurations; that produces **high PBO by construction**. ⇒ **Read PBO alongside the
   performance-degradation plot (§78.8), never alone.** A high PBO with a flat, positive OOS scatter is
   the *good* outcome; a high PBO with a steeply negative slope is the bad one.

5. ⛔ **The Strathern warning — the sharpest process rule in either paper:**

> *"We must warn the reader against applying CSCV to guide the search for an optimal strategy. That would
> constitute a gross misuse of our method. As Strathern put it, **'when a measure becomes a target, it
> ceases to be a good measure.'** Any counter-overfitting technique used to select an optimal strategy
> will result in overfitting. CSCV can be employed to evaluate the quality of a strategy selection
> process, but **PBO should not be the objective function on which such selection relies.**"*

⇒ **A hard rail for `strategy/promotion.py`: PBO (and DSR, and MinBTL) may gate or report. None of them
may ever appear in a sweep's ranking key.** Minimising PBO across configurations is itself a sweep, and
by §73.1 it manufactures apparent edge in exactly the same way. This generalises §73.12's *"explicitly NOT
recommended: a per-configuration p-value"* into a rule about the whole family of overfitting statistics.

### CSCV vs the hold-out method — why (B) says our OOS firewall is not enough

(B) gives five reasons hold-out is *"unsatisfactory"*, of which two are new relative to §73.1's argument:

- *"Hold-out is clearly inadequate for small samples… Weiss and Kulikowski argue that hold-out should not
  be applied to an analysis with less than 1,000 observations. **For example, if a strategy trades on a
  weekly basis, hold-out should not be used on backtests of less than 20 years.**"* ⚠️ Our rule trades
  **2.6×/year**. By that standard the required backtest length is far beyond 20 years — an independent
  route to the same order of magnitude as §73.3's MinBTL, from an entirely different argument.
- *"Different hold-outs are thus likely to lead to different conclusions"* (Van Belle & Kerr, on hold-out
  variance). CSCV's answer is symmetry: **every training set is re-used as a testing set and vice versa**,
  so *"the decline in performance can only result from overfitting, not arbitrary discrepancies between
  the training and testing sets."*

⇒ **§54.10's OOS firewall stands** (it is necessary), but §78.6 supplies the tool §73.1 said was missing:
a validation that is **not** silent about the search process. And CSCV has a property no hold-out has —
*"running CSCV twice on the same inputs generates identical results. Therefore, for each analysis, CSCV
will provide a single result, φ, which can be independently replicated and verified by another user."*
**Deterministic and reproducible**, which matters for an agent whose artifacts are meant to be auditable.

---

## §78.8 The three other statistics CSCV yields for free
**Module: `sim/report.py`**

Once the `(R_{n*}, R̄_{n*})` pairs exist, three more diagnostics cost nothing:

1. **Performance degradation.** Regress OOS on IS across combinations: `R̄_{n*} = α + β·R_{n*} + ε`.
   *"the β will be negative in most practical cases, due to compensation effects."* This is §73.7's
   Prop 6.1/6.3 inversion, **measured on our own data instead of assumed**. ⭐ That is a genuine upgrade:
   §73.7 could only argue from AR(1) theory plus §62.2 that we are in the memory regime. CSCV gives the
   **actual slope**. Paper's examples: overfit case slope **−0.75** (adj R² 0.17); real strategy **−0.35**
   (adj R² 0.05).
2. **Probability of loss** `Prob[R̄_{n*} < 0]`. ⚠️ *"even if φ ≈ 0, Prob[R̄ < 0] could be high, in which
   case the strategy's performance OOS is probably poor **for reasons other than overfitting**."* These
   are separate failure modes and must be reported separately.
3. **Stochastic dominance.** Does the distribution of `R̄_{n*}` (selected-IS configs, OOS) dominate the
   distribution of all `R̄` (every config, OOS)? *"Should that not be the case, it would present strong
   evidence that strategy selection optimization does not provide consistently better OOS results than a
   random strategy selection."* First-order: `Prob[R̄_{n*} ≥ x] ≥ Prob[Mean(R̄) ≥ x]` for all `x`.
   Second-order: `SD2[x] = ∫₋∞ˣ (Prob[Mean(R̄) ≤ y] − Prob[R̄_{n*} ≤ y]) dy ≥ 0`.

> ⭐ **Statistic 3 is the direct test of the question this project actually needs answered:** *"is our
> parameter selection better than picking a configuration at random?"* That is §58.11's random-entry-null
> question (§73.11) lifted from **entries** to **the selection process itself**. Neither §58.11 nor §73
> could ask it. **This is the most under-rated item in the source.**

**The paper's two worked cases, as calibration for what our numbers should look like:**

| case | PBO φ | Prob[SR_OOS<0] | degradation slope |
|---|---:|---:|---:|
| 8,800-node seasonal mesh on a **pure random walk** (§73.8's example) | **0.55** | 0.53 | −0.61 |
| same mesh, random walk **with a real seasonal effect injected** | **0.13** | 0.13 | — |
| a real (unnamed) investment strategy | **0.0004** | 0.03 | −0.35 |
| an overfit real strategy | **0.74** | 0.74 | −0.75 |

⭐ **CSCV correctly separated the injected-signal case (0.13) from the pure-noise case (0.55) on the same
mesh and the same 4-year sample length.** That is the validation that matters: the method has *power*,
not merely conservatism, on a sample barely shorter than ours.

> **Implementability: ✅ fully stdlib.** (1) is ordinary least squares on two lists — `β = Σ(x−x̄)(y−ȳ)
> / Σ(x−x̄)²`, ~6 lines. (2) is a counter. (3) is two sorted empirical CDFs and a cumulative sum.

---

## §78.9 Harvey & Liu — the haircut Sharpe ratio, and the multiple-testing adjustments
**Module: `strategy/promotion.py`, `sim/report.py`**

(C) attacks the same problem from the **econometrics** side rather than the extreme-value side. Its
opening target is a practitioner convention:

> *"A common practice in evaluating backtests of trading strategies is to discount the reported Sharpe
> ratios by 50%. … The 50% haircut is only a rule of thumb. Our article's goal is to develop an analytical
> way to determine the haircut's magnitude."*

### The core mechanism

`t-statistic = μ̂ / (σ̂/√T)`, and `ŜR = μ̂/σ̂ = t/√T`. So a Sharpe ratio **is** a t-statistic up to `√T`,
which means multiple-testing p-value corrections apply to it directly. Under independence:

```
p^S  =  Pr(|r| > ŜR·√T)                    single-test p-value, r ~ t_(T−1)
p^M  =  1 − (1 − p^S)^N                    multiple-testing p-value, N tests
p^M  =  Pr(|r| > HSR·√T)   ⇒  solve for   HSR = the HAIRCUT SHARPE RATIO
haircut  hc = (ŜR − HSR)/ŜR
```

Worked: *"assuming three years of monthly returns (T = 240), an annual Sharpe ratio of 0.75 yields a
p-value of 0.0008 for a single test. When N = 200, `p^M` = 0.15, implying an adjusted annual Sharpe ratio
of 0.32… multiple testing with 200 tests reduces the original Sharpe ratio by approximately 60%."*

### The three adjustments (order the `M` p-values ascending: `p_(1) ≤ … ≤ p_(M)`)

```
Bonferroni:   p^Bonf_(i)  =  min[ M · p_(i) , 1 ]                       # FWER; inflates all equally

Holm:         p^Holm_(i)  =  min[ max_{j≤i} { (M − j + 1)·p_(j) } , 1 ] # FWER; step-down, less harsh
                                                                        #   ⇒ p^Holm ≤ p^Bonf always

BHY:          p^BHY_(M)   =  p_(M)                                      # FDR; step-UP from the largest
              p^BHY_(i)   =  min[ p^BHY_(i+1) ,  (M · c(M) / i) · p_(i) ]   for i ≤ M−1
                             where  c(M) = Σ_{j=1..M} 1/j
```

The choice of `c(M) = Σ 1/j` (Benjamini–Yekutieli 2001, rather than Benjamini–Hochberg's `c(M)=1`) is
deliberate: *"This allows our test to work under arbitrary dependency for the test statistics."*
⭐ **That is directly relevant to us** — our trials are heavily dependent (§78.2), and BHY is the only one
of the three that is valid without an independence assumption.

**FWER vs FDR, stated as a design choice rather than a technicality:** Bonferroni and Holm control the
probability of **even one** false discovery; BHY controls the **proportion**. (C) is unambiguous about
which suits finance:

> *"Although this type of approach seems appropriate for a space mission (given the catastrophic
> consequence of a part failing), asset managers may be willing to accept the fact that the number of
> false discoveries will increase with the number of tests. … **In the end, we advocate the BHY method.**
> The FWER seems appropriate for applications where a false discovery brings a severe consequence. In
> financial applications, it seems reasonable to control for the rate of false discoveries, rather than
> the absolute number."*

⚠️ **We should be careful about inheriting that preference wholesale.** (C)'s audience allocates across a
portfolio of many strategies, where a few false discoveries are diluted. `keel` runs **one** validated rule
on **three** assets with real capital. A false discovery here is not diluted by anything — it *is* the
portfolio. **That argues for the FWER family (Holm) rather than BHY, on the paper's own reasoning applied
to our different situation.** Recording this as a deliberate, argued departure from (C)'s recommendation,
not an oversight.

> **Implementability: ⚠️ mostly stdlib, with one real gap.** Bonferroni is one line; Holm is a sort plus
> a running max; BHY is a sort plus a reverse pass with a harmonic-sum constant — all pure list
> manipulation, **fully `Decimal`-safe, no dependency.** The gap is `p^S = Pr(|r| > ŜR·√T)` under a
> **t-distribution with T−1 df**, which needs the regularised incomplete beta function — *not* in the
> stdlib. Three options, in order of preference: (1) **use the Normal approximation** — (C)'s own
> endnote 3 says *"without the normality assumption, the t-statistic becomes asymptotically normally
> distributed, based on the central limit theorem"*, so `math.erf` suffices for `T` ≳ 30; ⚠️ our BTC
> `T = 13` trades is below that, so on the per-trade unit the approximation is doing real work and must be
> flagged; (2) implement the continued-fraction incomplete beta (~30 lines, standard, deterministic);
> (3) run on the daily-bar series where `T = 1,819` and the Normal approximation is exact for our
> purposes. **Recommend (3) for the haircut and (1) with a flag elsewhere.**
>
> **The full HLZ correlation-adjusted model is a bigger lift but is still stdlib-reachable.** It requires
> `B = 5,000` simulations, each drawing `N` strategies from a mixture (mean zero with prob `p₀`,
> else exponential with mean `λ`), with innovations equicorrelated at `ρ`. `random.gauss` and
> `random.expovariate` are stdlib, and **equicorrelated normals need no Cholesky** —
> `x_i = √ρ·z₀ + √(1−ρ)·z_i` gives pairwise correlation `ρ` exactly. So this is implementable, at the cost
> of a slow, seeded, non-deterministic-unless-seeded computation. ⚠️ **Against CSCV's determinism
> (§78.7) that is a real disadvantage.** Defer it; the Bonferroni/Holm path needs no simulation.

---

## §78.10 ⭐⭐ The nonlinearity finding — and what it says about our SR ≈ 0.395
**Module: `strategy/promotion.py`**

(C)'s headline empirical result, and the reason it is not merely a restatement of §73:

> *"We argue that it is a serious mistake to use the usual 50% haircut. Our results show that the multiple
> testing haircut is **nonlinear**. The highest Sharpe ratios are only moderately penalized, while the
> **marginal Sharpe ratios are heavily penalized**. This makes economic sense. The marginal Sharpe ratio
> strategies should be thrown out. The strategies with very high Sharpe ratios are probably true
> discoveries. In these cases, a 50% haircut is too punitive."*

Exhibit 1's three real strategies show the shape (Bonferroni-adjusted):

| strategy | annualized ŜR | haircut at N=10 | at N=50 | at N=100 |
|---|---:|---:|---:|---:|
| E/P (least profitable) | 0.43 | 26.6% | **50.0%** | **61.6%** |
| MOM | 0.67 | 10.9% | 19.2% | 23.0% |
| BAB (most profitable) | 0.78 | 4.6% | 7.9% | 9.3% |

And the general statement from Exhibits 2–3:

> *"the haircut is almost always more than and sometimes much larger than 50% when the annualized Sharpe
> ratio is less than 0.4. On the other hand, when the Sharpe ratio is greater than 1.0, the haircut is at
> most 25%. This shows the 50% rule of thumb discount for the Sharpe ratio is inappropriate: **50% is too
> lenient for relatively small Sharpe ratios (< 0.4) and too harsh for large ones (> 1.0).**"*

### ⚠️⚠️ Applied to `keel`, this is the harshest single verdict in §73–§78

**Our honest annualized Sharpe is 0.395** (§73.3 — `SR_trade 0.245 × √2.6 trades/yr`; the pooled
cross-check gives 0.380). That sits **exactly in the region Exhibits 2 and 3 identify as the one where
the haircut approaches or reaches 100%.** Reading Exhibit 2's curves: haircut Sharpe hits **zero** below
an original annualized SR of roughly **0.35–0.4 at N = 10**, **~0.5 at N = 50**, and **~0.6 at N = 200**.

> **At any trial count above ~10, an original annualized Sharpe of 0.395 haircuts to approximately zero.**
> Harvey & Liu's method does not say our rule is 30% weaker than reported. It says that, conditioned on
> the search that produced it, **there is no residual Sharpe to allocate against.**

This is **harsher than DSR's verdict** (§78.4: DSR 0.3–0.6, i.e. "unresolved") and harsher than MinBTL's
(§73.3: "you need 5–11× more data"). Both of those say *we cannot tell*. Harvey & Liu says *the adjusted
estimate is zero*. ⚠️ **Two authorities, two different answers, on the same inputs.** §78.11.

⭐ **But note what the nonlinearity also implies, which is genuinely encouraging and is the most useful
thing in (C) for us:** the haircut is a *steep* function of raw Sharpe near 0.4. Small improvements in
`SR_annualized` in this region buy **disproportionately large** improvements in the surviving Sharpe —
moving from 0.4 to 0.6 takes the Bonferroni haircut at N=50 from ~100% to roughly 50%, i.e. from
"nothing survives" to "half survives." ⇒ **We are on the steepest part of the curve, where effort is
best rewarded.** And since `SR_annualized = SR_trade × √(trades/yr)`, **both levers apply.** This is the
same arithmetic as §73.13 #4 arriving from a completely different direction, and it feeds §78.12.

### Exhibit 4 — minimum profitability hurdles, the one directly-portable table

At 5% significance with **300 assumed tests**, the minimum average **monthly** return required:

| observations | σ = 5% | σ = 10% | σ = 15% |
|---|---:|---:|---:|
| 120, single test | 0.258% | 0.516% | 0.775% |
| 120, **Holm** | 0.486% | 0.972% | 1.459% |
| 240, single | 0.183% | 0.365% | 0.548% |
| 240, **Holm** | 0.344% | 0.688% | 1.031% |
| 240, **BHY** | 0.307% | 0.616% | 0.923% |
| 1000, single | 0.089% | 0.179% | 0.268% |
| 1000, **Holm** | 0.169% | 0.337% | 0.505% |

(Exhibit 4, Panels A–D. Note BHY is consistently the most lenient of the three — ~11% below Holm — which
is the FDR-vs-FWER difference of §78.9 made numerical.) ⚠️ **Crypto volatility is far above the 15% column** — BTC annualized volatility runs
50–80%. Extrapolating linearly in σ (which Eq. 1 permits, since the hurdle is `t·σ/√T`), a 300-test
Holm hurdle at 240 observations and σ = 60% is roughly **4.1% per month**. That is a demanding but not
absurd bar for a trend-following rule in a bull regime, and it is a **cleanly reportable number** — much
easier to communicate than a DSR. ⇒ **Adopt the minimum-profitability-hurdle framing for the sim
report**, computed for our actual σ and `T`, alongside (not instead of) DSR.

---

## §78.11 ⚠️⚠️ THE CONTESTED AUTHORITIES — DSR vs Harvey & Liu, and §73's dismissal of the Harvey family
**Module: `strategy/promotion.py` (which statistic gates), process**

**This section exists because the KB already contains a partial dismissal of paper (C), and honesty
requires confronting it rather than quietly adding (C) as a complement.**

### What §73 said

§73.15's "Discarded" list contains:

> *"§1's survey of econometric-overfitting literature (White's Reality Check, Romano–Wolf stepwise
> testing, **Harvey et al.**) — the paper explains why these do not transfer to investment strategies
> (they need explicit point forecasts with defined horizons; our rules emit qualitative buy/hold/exit
> signals over undefined periods, exactly as described). **Correctly excluded by the paper's own
> argument, and its reasoning applies verbatim to `keel`'s rules.**"*

Paper (B) restates that dismissal in its own words:

> *"See White, Romano et al., **Harvey et al.** … Essentially these methods propose a way to adjust the
> p-values of estimated regression coefficients to account for the multiplicity of trials. **These are
> valuable approaches when the trading rule relies on an econometric specification. That is not generally
> the case** … Investment strategies in general are not amenable to characterization through a system of
> algebraic equations."*

### What is right and what is wrong in that dismissal

**Right:** if the method required a regression specification, it would not transfer. `TurtleBreakout`
emits `buy`/`hold`/`exit`; there is no coefficient to adjust.

⚠️ **Wrong as applied to paper (C) specifically.** (C)'s adjustment does **not** operate on regression
coefficients. It operates on `ŜR = t/√T` — the strategy's realized Sharpe ratio. Its inputs are: a Sharpe
ratio, a sample length, a trial count, and an assumed average correlation. **Every one of those is a
quantity `keel` can produce from `BacktestResult` without any econometric model whatsoever.** (C) says so
directly: *"Our method is based on a single test statistic that summarizes a strategy's performance over
the entire sample."* No forecast, no horizon, no equation.

⇒ **The §73.15 dismissal is over-broad.** It is correct for White (1999) and Romano–Wolf, which do require
a forecast-error framework. It is **not** correct for Harvey & Liu (2015), whose only structural
requirement is that the performance statistic have a probabilistic interpretation. **§73.15's Harvey entry
should be narrowed to "White / Romano–Wolf," and Harvey & Liu admitted.** That is a correction to the KB,
recorded here rather than by editing §73.

### But the disagreement between the two camps is real and I cannot fully resolve it

(A) is generous in print: *"In an excellent recent study, Harvey and Liu 2014 … The role of HL's threshold
is analogous to the role played by our `E[max{SR_n}]`… From that perspective, **these two methods are
complementary, and we encourage the reader to compute DSR using both thresholds**, `E[max{SR_n}]` as well
as HL's."* (C) is equally generous back, and both restate the other's method fairly (C's "Multiple Testing
and Cross-Validation" section is the clearest short account of PBO in either paper).

**But their published disagreements are substantive:**

| | Bailey/López de Prado (A,B) | Harvey & Liu (C) |
|---|---|---|
| **error rate controlled** | none explicitly; DSR is a confidence level on one selected strategy | FWER (Bonferroni/Holm) or **FDR** (BHY), explicitly |
| **what is being judged** | *this strategy, relative to the pool it was selected from* | *this strategy, absolutely — is its return non-zero?* |
| **dependence between trials** | handled by shrinking `N` (§78.2, `N̂ = ρ̂ + (1−ρ̂)M`) | handled inside the test (BHY under arbitrary dependency; HLZ simulation with correlation ρ) |
| **on OOS/hold-out** | (B): unreliable; CSCV supersedes it | (C): *"one should be very cautious of OOS tests"* but proposes **merging** IS multiple-testing with OOS validation and *"looking at the intersection of survivors"* |
| **stated objection to the other** | (B): econometric methods need a specification we do not have | (C): *"it will rarely be considered significant in the PBO framework, as it is dominated by other, more significant strategies"* — PBO can reject a genuinely-true factor merely for being outranked |

⭐ **(C)'s objection is the sharper one and it is not answered anywhere in (A) or (B).** Read it in full:

> *"consider a case with a group of factors that are all true. The one with the smallest t-ratio, although
> dominated by other factors in terms of t-ratios, may still be declared significant in our multiple-testing
> framework. In contrast, it will rarely be considered significant in the PBO framework, as it is dominated
> by other, more significant strategies."*

This is **exactly (B)'s own limitation #4** (§78.7: *"it is entirely possible that all N strategies have
high but similar Sharpe ratios… PBO will be high. Here overfitting is among many 'skillful' strategies"*)
— **restated by an opponent as a fatal objection rather than a caveat.** (B) acknowledges it; it does not
answer it.

### ⇒ The KB's verdict: a division of labour, with one unresolved residue

> **Use DSR/PBO to judge the SEARCH. Use Harvey & Liu to judge the STRATEGY.**

| question | statistic | why this one |
|---|---|---|
| *"Was my parameter-selection process better than choosing at random?"* | **PBO / stochastic dominance** (§78.6, §78.8) | relative-rank, model-free, deterministic, reproducible; asks about the process |
| *"Given everything I tried, how confident am I that this rule's true Sharpe exceeds the selection-bias floor?"* | **DSR** (§78.3) | one number, one strategy, includes non-Normality; directly extends §73's `E[max_N]` |
| *"After honest multiple-testing adjustment, how much Sharpe survives — and what monthly return must I clear?"* | **Haircut SR / minimum-profitability hurdle** (§78.9, §78.10) | absolute rather than relative; immune to (C)'s domination objection; **produces a communicable hurdle rather than a probability** |

**⚠️ The residue I cannot resolve, stated plainly.** On our own numbers these three do **not** agree:

- MinBTL (§73.3, refined §78.2): *"you need 22–55 years and you have 5"* — **verdict: unknowable.**
- DSR (§78.4): *"0.3–0.6, and the dominant unknown is your own missing ledger"* — **verdict: unresolved,
  pending bookkeeping.**
- Haircut SR (§78.10): *"at SR 0.395 and N > 10, the adjusted Sharpe is ≈ 0"* — **verdict: nothing
  survives.**

They are not measuring the same thing, so this is not a contradiction in the strict sense — but they do
give three different answers to *"should this rule be running?"*, and **I have no principled basis in
either paper for choosing among them.** What can be said:

- **They agree on direction and on magnitude of the problem.** All three say the evidence is far short of
  what would be needed. None says the rule is good. The dispersion is between *"can't tell"* and
  *"nothing there"* — **not** between *"bad"* and *"good"*. For a decision about whether to keep sweeping,
  that agreement is sufficient and the disagreement is immaterial.
- **They disagree about what to do next, and that disagreement is decision-relevant.** DSR says *build the
  ledger, then re-ask* (the answer is inputs-limited). Haircut SR says *the answer will still be zero;
  raise the raw Sharpe or the trade count first* (§78.10's steep-curve point). Both are affordable, and
  §78.12 argues they are the same action.
- **This is logged as an open disagreement between two authorities, deliberately.** Per the KB's own
  practice with §74.9 (the [A]/[B] lookback disagreement — *"do not average them into a single 'correct'
  value"*), the honest handling is to carry both and report both, not to synthesise a false consensus.
  **Report all three numbers in the sim artifact. Do not combine them into a single score.** A combined
  score would hide precisely the information that makes them worth having.

---

## §78.12 ⚠️⭐ Deflate-for-trials vs deploy-for-evidence — the second tension, and why it is smaller than it looks
**Module: `strategy/promotion.py`, `execution/`, process**

**The apparent conflict.** This source says the project has likely **overspent its trials budget** and
must **deflate** its results. A separate live priority says the project must **raise trade frequency** to
accumulate enough evidence to validate anything (the rule trades ~2.6×/yr/asset; ~68 trades are needed to
clear `z ≥ 2` vs random entries). Deflating for what we tried and deploying more to learn appear to pull
opposite ways.

**They do not, and §73.13's own formula shows why.** From §73.13 #4:

```
MinBTL  ∝  1 / ( SR_trade²  ×  trades_per_year )
```

`N` (trials) and `q` (trades/year) enter the problem at **completely different points**. `N` sets the
**numerator** of the requirement — the selection-bias level `E[max_N]` you must clear. `q` sets the
**denominator** — how fast you accumulate evidence per calendar year. They are not two ends of one lever;
they are the numerator and denominator of a ratio. **You can push both in the favourable direction at
once**, and the actions that do so are different actions.

The same structure appears in every statistic in this source, which is the strongest evidence it is real
rather than an artifact of one formula:

| statistic | where trials `N` enter | where trade count enters |
|---|---|---|
| MinBTL (§73.2) | `E[max_N]`, numerator | `q` in `SR_ann = SR_trade·√q`, denominator |
| **DSR (§78.3)** | `ŜR₀` threshold, subtracted | `√(T−1)`, **multiplied** |
| **Haircut SR (§78.9)** | `p^M = 1−(1−p^S)^N`, inflates p | `ŜR·√T` inside `p^S`, deflates p |
| **PBO (§78.6)** | `N` columns — needed `≫10`, and **does not increment the ledger** if the result is discarded (§78.6) | `T` rows — more data, strictly better |

**Read the DSR row.** `DSR = Z[ (ŜR − ŜR₀)·√(T−1) / denom ]`. Trials enter through `ŜR₀`, a **subtraction**;
trades enter through `√(T−1)`, a **multiplication**. Once `ŜR > ŜR₀` at all, **more trades raise DSR
without bound**, regardless of how many trials were spent. §78.4's table shows it directly: at `M=336,
ρ̂=0.9, V=0.01`, DSR goes 0.54 → 0.57 → 0.60 as `T` goes 13 → 30 → 68. Slow, but monotone and free.

⚠️ **And the crucial asymmetry, which decides the priority order:** if `ŜR ≤ ŜR₀`, the numerator is
negative and **more trades push DSR toward 0, not 1.** Deployment accelerates the verdict; it does not
determine it. ⇒ **Trade count is an evidence multiplier, not an evidence source.** Which is the arithmetic
form of a thing the project already believes.

### ⇒ The reconciliation, as four operational rules

1. **The two are not in conflict; they were never competing for the same budget.** Deflation is a
   **reporting** obligation (report `N`, `ρ̂`, `V`, DSR, haircut). Deployment is an **evidence-gathering**
   action. Doing the first costs no trades; doing the second costs no trials — *provided* the added
   deployment is not itself a selection (rule 3).
2. ⭐ **The ledger must land before the next sweep, but it need NOT land before the next trade.** The
   memory's *"otherwise the sweep cannot be scored properly and burns budget blind"* is exactly right and
   §78.4 row 2 sharpens it: a sweep run without the ledger **destroys** the `V[{SR_n}]` needed to score
   it. But **running the already-shipped 40/20 rule live increments `T` and increments nothing else.**
   There is no reason to pause deployment while the ledger is built. **Build the ledger; keep trading.**
3. ⚠️ **Frequency must be bought without selection, or it is not free.** §73.13 #4 established that
   `SR_trade` enters **squared** while `q` enters linearly, so dropping the ADX gate to buy trades is a
   bad trade. §78 adds a second reason: **any *choice* about how to raise frequency is a trial.**
   Choosing between "add `macd_divergence`" and "loosen ADX to 20" by comparing their backtests
   increments `N` and raises `ŜR₀` for everything. The frequency-raising changes that are genuinely free
   are the ones justified `a_priori`:
   - **more assets** on the same unchanged rule (§58.4's whole-basket, no per-asset fit; §73.3's PAXG
     inheritance rule) — pure `T` gain, zero trials;
   - **§60.2's rank-and-fill deployment cadence** — a capital-deployment policy, not a rule parameter;
   - a second rule class adopted on **external** evidence (§74.5's crypto-specific MACD significance)
     rather than chosen by comparing our own backtests — ⚠️ but §74.12 warns "uncorrelated" must be
     *measured*, and §78.2 now says that measurement is the **same `ρ̂` computation** as the trials
     correction. One tool, two uses.
4. **The steep part of the curve is where we are (§78.10), and both levers move along it.**
   `SR_ann = SR_trade × √q`. At `SR_ann ≈ 0.4` the haircut curve is near-vertical, so *either* raising
   per-trade quality *or* raising frequency moves the surviving Sharpe disproportionately. §73.13 #4's
   "prefer frequency" and §78.10's "raise the raw Sharpe" are **the same recommendation on the same
   quantity**, differing only in which factor is cheaper to move. Frequency is cheaper (more assets,
   no fitting). ⇒ **Buy `q` through breadth, not through weakened entry criteria.**

> **Net:** the tension is largely illusory and dissolves once `N` and `q` are seen as numerator and
> denominator. The one place it is **real**: every candidate mechanism for raising frequency is a decision,
> and decisions are trials (§73.10). ⇒ **Adopt frequency-raising changes that are justified from the KB
> (`a_priori`, free) and refuse ones that require comparing our own backtests to choose (`fitted`,
> expensive).** That is §73.13 #1's "the knowledge base is a trials-budget subsidy" applied to the
> deployment problem instead of the parameter problem — **and it is the first time that reframing has been
> load-bearing for something other than parameter choice.**

---

## §78.13 ⭐ The build, in dependency order
**Module: `strategy/promotion.py`, `strategy/backtest.py`, `sim/report.py`**

Everything below is stdlib-only and reuses fields the harness already produces or the ledger will.

**0. ⭐⭐ The ledger row — one design decision that unlocks three tools.** §73.12 #1 specified an
append-only trials ledger. §78 constrains its *schema*: three separate tools (§78.1's `V[{SR_n}]`,
§78.2's `ρ̂`, §78.6's CSCV matrix) all need **per-trial P&L series**, not per-trial summary numbers.

```
TrialRecord:
    trial_id, timestamp, session
    rule + full parameter dict
    provenance:  a_priori | fitted          (§73.12 #4)
    kind:        sweep_node | ablation | rule_retirement | asset_prune | threshold_nudge
    decision:    selected | rejected | diagnostic_only     ← diagnostic_only does NOT count toward N (§78.6)
    performance: per-trade P&L list  AND  per-bar P&L list  ← the unlock
    summary:     SR_trade, expectancy, trade_count
```

⚠️ Storing the P&L series is the only expensive part (a few KB per trial). **Do it anyway.** Without it,
`V[{SR_n}]`, `ρ̂`, and CSCV are all permanently unavailable for every trial recorded before the schema
changes — and §78.4 showed `V` alone swings DSR by 3×.

**1. `E[max_N]` + inverse-normal helper** (§78.1). ~25 lines. Verify against §73.1's table.
**2. `N̂ = ρ̂ + (1−ρ̂)·M`** (§78.2) with a Pearson-correlation helper. ~20 lines. ⚠️ Ship with the
ill-conditioning guard: refuse to estimate `ρ̂` when `T < ½·M(M−1)`; fall back to the assumption band.
**3. Report `MinBTL(ρ̂=0)` and `MinBTL(ρ̂=measured)` side by side**, as a ratio (§73.12 #2), never a
boolean.
**4. PBO via CSCV** (§78.6), `S = 16`, on the **daily-bar** series with a drawdown-aware or Sortino metric
(§78.6's unit caveat + §54.22 + §73.4's verdict statistic). ~60 lines. **Build this before DSR** — fewer
unmeasured inputs, no distributional assumptions, deterministic and independently replicable.
**5. The three free CSCV companions** (§78.8) — degradation slope, `Prob[R̄<0]`, stochastic dominance.
~25 lines on top of #4. ⭐ The dominance test is the direct *"is our selection better than random?"* check.
**6. DSR** (§78.3), reporting-only, with every input itemised. ~30 lines. Default `γ̂₃=0, γ̂₄=3` when
`T < 30`.
**7. Haircut Sharpe, Bonferroni + Holm** (§78.9), on the daily-bar series where the Normal approximation
for `p^S` is safe. ~25 lines. ⚠️ Prefer **Holm** over (C)'s recommended BHY, per §78.9's argument that our
single-strategy situation is FWER-shaped, not FDR-shaped. Report the **minimum monthly return hurdle**
(§78.10) as the communicable form.
**8. Sweep protocol: pre-declared candidate set + the 1/e stopping rule** (§78.5). Process, not code,
plus a `frozen_candidate_set` field the sweep driver refuses to exceed.

**⛔ Explicitly NOT built:**
- **Any of these as a sweep ranking key** (§78.7's Strathern rail). PBO, DSR and haircut may gate or
  report; none may ever be optimised.
- **The HLZ correlation-adjusted simulation** (§78.9) — 5,000 seeded simulations, non-deterministic,
  supersedes nothing that Holm does not already give us at a fraction of the cost.
- **Entropy / information-theoretic `N̂`** and **PCA dimension reduction** (§78.2) — no library, and the
  correlation route is adequate given the assumption-band handling.
- **Per-configuration single-trial p-values**, still (§73.12, §73.8). Unchanged.

---

## §78.14 ⛔ Halal exclusions and screening

| item | status |
|---|---|
| **Sharpe ratio throughout (A) and (C)** | ⚠️ **Riba in the benchmark only. SURVIVES `rf = 0`**, exactly as §73.4 established: the rate enters solely in defining what `μ` is measured against, and every derivation here needs only asymptotic normality and `√T` scaling. (C) makes this even cleaner than (A) — its `r_t` is *"the realized return… net gain/loss"*, and setting the benchmark to cash changes no step. Consistent with `sim/metrics.py`'s existing `rf = 0` policy. |
| **DSR, PSR, `E[max{SR_n}]`, `N̂ = ρ̂+(1−ρ̂)M`** | ✅ **Fully admissible.** Extreme-value statistics and correlation algebra over an abstract return series. No rate, no instrument, no direction. |
| **PBO / CSCV and its four statistics** | ✅ **Fully admissible, and the cleanest of all** — model-free, non-parametric, operates only on a P&L matrix. It does not know what a strategy *is*, let alone what it trades. |
| **Bonferroni / Holm / BHY** | ✅ **Fully admissible.** General-purpose multiple-testing p-value adjustments from the statistics literature (1979/1995/2001), with no financial content whatsoever. |
| **(C)'s three worked strategies — E/P, MOM, BAB** | ⛔ **Context excluded, method retained.** All three are *"zero-cost hedge portfolios that simultaneously take long and short positions on the cross-section of U.S. equities"*; BAB (betting-against-beta) is explicitly *"potential distortions induced by **leverage**."* **Short + leverage + equities.** The *haircut numbers* in Exhibit 1 are used here only to illustrate the nonlinearity (§78.10); **none of the three strategies is a candidate.** Same handling as §74's exclusion of [A]'s Strategy 1/3 columns. |
| **(A)'s numerical example — treasury-auction seasonality, "selling off-the-run bonds a few days before the auction"** | ⛔ **Doubly excluded and used only as a negative exemplar.** Bonds = riba instrument (§51/§52's standing exclusion); *selling* off-the-run = short. Extracted purely for the DSR arithmetic (§78.3), which is instrument-blind. Also reinforces §6.4/§73.8: it is a *seasonality* search, the family the KB has repeatedly declined. |
| **(B)'s Example — `Side ∈ {−1,1}` in the 8,800-node mesh** | ⛔ **The `−1` branch excluded (short).** Identical to §73.14's handling: our mesh has `Side = {+1}`, which **halves `M`** and therefore lowers `N̂` and `E[max]`. **Third recorded instance** (after §58.3 and §74.6) of the long-only constraint not costing performance — here it directly reduces the trials bill. |
| **(C)'s VaR extension** (endnote 23: multiple-testing-adjusted VaR) | ⚪ **Not adopted, not excluded.** `VaR(α)/σ = SR − z_α`, so the same adjustment applies to VaR. No riba. Declined because the KB's risk vocabulary is drawdown/semivariance (§33, §54.22), not VaR; adopting VaR here would import a metric the KB has not otherwise chosen. Logged as available if ever wanted. |
| **Leverage / derivatives / discounting** | ✅ **Absent from all three papers' mathematics.** The only mentions are in (C)'s excluded BAB example. Nothing to reformulate. |
| **Hedge-fund/allocator framing** (A §"When should we stop testing", C's allocator audience) | ⚪ Out of scope, not haram. Institutional context; extracted only where it changes a formula's applicability (§78.9's FWER-vs-FDR argument). |

> **Net: identical halal outcome to §73, from three more papers.** The single riba contact is the
> risk-free rate inside the Sharpe definition, removable by substitution without touching one line of
> the derivations. Every excluded item is an *illustrative example* (bonds, hedge portfolios, BAB
> leverage, the short branch of a mesh), never a *method*. **No formula in this source required
> reformulation to be admissible.**

---

## §78.15 Discarded (no agent value)

- **(A)'s Snippet 1** (`getExpMaxSR` / `getDistMaxSR` / `simulate`, ~35 lines of Python). ⚠️ **The
  analytic function's *content* is fully extracted at §78.1 and reproduced in stdlib** — but the snippet
  itself imports `numpy`, `scipy.stats` and `pandas`, all declined by this project, and its purpose is the
  Monte-Carlo *verification* of Eq. (6), not its use. **The numerical-verification path (`getDistMaxSR`,
  10,000 iterations of `np.random.normal`) is not needed:** (A)'s Exhibits 3.1/3.2 already establish the
  analytic formula's accuracy (max error < 0.05 at `V=1` for `N<50`, converging to 0.006 by `N=1000`;
  ~0.11 at `V=4`, consistent with the `σ=√V` scaling). **We inherit the verification rather than repeat
  it.** Recorded so no one re-mines a NumPy snippet for a NumPy-free codebase.
- **(A) Appendix A.1's proof** and **Appendix A.2's experimental-verification protocol** — results
  extracted (§78.1, and the error magnitudes above); the algebra and the heat-map methodology add nothing
  implementable.
- **Exhibits 1, 2, 3.1, 3.2, 4** (A) and **Figures 1–13** (B) — plots. All numerical content extracted
  (`E[max]` vs `N` and `V`; DSR vs `N`; the `{M, ρ̂, N̂}` surface; the four PBO/degradation/dominance
  cases). The images add nothing.
- **(B)'s measure-theoretic Section 2.1** — `(T, F, Prob)`, the ranking space `Ω` of `N!` permutations,
  `Ω*_n = {f ∈ Ω | f_n = N}`, and Definition 2.1's `Σ E[r̄_n | r ∈ Ω*_n]·Prob[r ∈ Ω*_n] ≤ N/2`.
  **This is the formal scaffolding for Definition 2.2, which is extracted in plain language at §78.6.**
  The measure theory is not implementable and not needed to implement CSCV.
- **(B)'s comparison to K-fold CV and LOOCV** (Section 4) — the *conclusions* are extracted (equal-size
  train/test, symmetry, time-order preservation, determinism, §78.7); the comparative discussion is not
  actionable for us since we were never going to use K-FCV on 1,819 bars.
- **(C)'s full HLZ structural model** (Exhibit 7's `{ρ, p₀, λ}` parameter grid, the linear interpolation
  scheme for intermediate ρ, the `B=5,000` simulation procedure) — ⚠️ **implementable in stdlib
  (§78.9's equicorrelation trick) but deliberately declined** for the reasons at §78.13: slow,
  non-deterministic, and dominated by Holm for our purposes. The parameter table is left here as a
  pointer should that judgement ever be revisited: `ρ=0 → p₀=0.396, λ=0.550`; `ρ=0.2 → 0.444, 0.555`;
  `ρ=0.4 → 0.485, 0.554`; `ρ=0.6 → 0.601, 0.555`; `ρ=0.8 → 0.840, 0.560`.
- **(C)'s MATLAB programs** `Haircut_SR` and `Profit_Hurdle` (`faculty.fuqua.duke.edu/~charvey/backtesting`)
  — MATLAB, external dependency, and the underlying formulas are extracted at §78.9/§78.10. The
  **input-vector schema is worth keeping as an API sketch** for our own implementation: sampling
  frequency, #obs, Sharpe, is-it-annualized, is-it-AC-corrected, AC level, #tests assumed, average
  correlation assumed. That is a well-designed interface and we should mirror it.
- **The autocorrelation correction** (C endnote 26, following Lo 2002) — mentioned but not derived in (C);
  its effect is visible only in Exhibit 5's worked output (SR 1.000 → AC-corrected 0.912 at AC = 0.1).
  ⚠️ **Flagged rather than discarded:** §62.2 established our series is AR(1)-shaped, so an AC correction
  would apply — but the formula is in Lo (2002), not here, and under §73.4's **per-trade** unit the
  correction's motivation (autocorrelated *daily* returns inflating an annualized Sharpe) largely
  dissolves. **Logged as a possible future source (Lo 2002, "The Statistics of Sharpe Ratios"), not an
  action.**
- **(B)'s and (C)'s introductory polemic** — the Wittgenstein epigraph, the dead salmon fMRI Ig Nobel,
  AllTrials, *"most claimed research findings in financial economics are likely false"*, the SciDAC
  digression, the Netflix-scale-data comparison. Rhetorically effective, operationally empty, and
  substantially duplicated from §73.9's fraud framing.
- **Bibliographies and the SSRN/LBL URLs** — including `datagrid.lbl.gov/backtest`, the online
  overfitting demo, which §73.15 already logged as a dead-ish 2014 link.

---

## Net assessment

**§73 diagnosed; §78 supplies the instruments. Taken together they are one source in two parts, and the
second part is the one that can be built.** §73 gave `MinBTL` and `E[max_N]` and then said, in effect,
*count your trials honestly and you will find you cannot afford what you have already spent*. §78 answers
the question §73 could not: **what "honestly" means when the trials are not independent.**

Four things change as a result:

1. ⭐⭐ **§73.3's central number is refined, and in our favour — but not enough to matter.** `N̂ = ρ̂ +
   (1−ρ̂)·M` (§78.2) says our 336 raw configurations are perhaps **34 effective trials** at a plausible
   ρ̂ = 0.9, bringing MinBTL from **55 years to 29**. Against 5.0 years available, the verdict is
   unchanged: the specific values 40 and 20 remain undefensible as fitted parameters and should be
   re-derived `a_priori` (§73.12 #4, §74.2). **The correction is real, it is measurable, and it moves us
   from 11× short to 4× short.**
2. ⭐⭐ **The ledger's schema is now determined, and it is not just a counter.** Three separate tools —
   `V[{SR_n}]`, `ρ̂`, and CSCV's matrix — all require **per-trial P&L series**. §78.4 showed `V` alone
   swings DSR by 3×. ⇒ **A sweep run before the ledger exists does not merely fail to increment `N`; it
   destroys the information needed to score itself.** That is a stronger version of the project's own
   *"otherwise the sweep cannot be scored properly and burns budget blind"* — and it is the concrete
   reason the ledger must land first.
3. ⭐ **CSCV/PBO is the best-value build in the entire §73–§78 block, and it is not the famous one.**
   It is `itertools.combinations` + sorting + `log()`; it is deterministic and independently replicable;
   it makes no distributional assumption; and its stochastic-dominance companion answers the question the
   project most needs answered — *"is our parameter selection better than picking at random?"* — which
   neither §58.11's random-entry null nor §73's MinBTL can ask. **Build it before DSR.**
4. ⚠️ **Three statistics, three different verdicts on the same rule, and no principled tie-break.**
   MinBTL: *unknowable*. DSR: *unresolved, pending our own bookkeeping* (0.3–0.6). Harvey & Liu's
   haircut: *at SR 0.395 with N > 10, nothing survives*. They agree on direction and on the magnitude of
   the shortfall; they disagree about whether the answer is "can't tell" or "zero", and about what to do
   next. **Report all three. Do not average them into a score** — the disagreement is the information.

**On the contested authority.** §73.15's dismissal of *"White, Romano–Wolf, Harvey et al."* is
**over-broad as applied to Harvey & Liu (2015)**, whose method operates on a realized Sharpe ratio, a
sample length, a trial count and an assumed correlation — none of which requires the econometric
specification Bailey et al. object to. It should be narrowed to White and Romano–Wolf. But (C)'s
counter-objection to PBO — *"a true factor dominated by others will rarely be considered significant in
the PBO framework"* — is (B)'s own limitation #4 restated as a fatal flaw, and **(B) acknowledges it
without answering it.** §78.11 proposes a division of labour (PBO judges the search; haircut SR judges the
strategy; DSR sits between) and is explicit that this is a working allocation, **not a resolution.**

**On the deflate-vs-deploy tension: largely illusory, and the residue is actionable.** `N` and trade count
enter every statistic in this source at opposite ends of a ratio — `N` subtracts from the numerator, `T`
multiplies it. Both can be pushed favourably at once, by different actions. **Build the ledger; keep
trading.** The one real constraint: *how* frequency is raised matters, because every mechanism-choice is a
trial. Breadth (more assets on the unchanged rule, §58.4) and cadence (§60.2) are free; choosing between
frequency-raising mechanisms by comparing our own backtests is not. ⚠️ And the asymmetry that sets the
priority: **more trades accelerate the verdict, they do not determine it** — if `ŜR ≤ ŜR₀`, additional
trades drive DSR toward zero, not one.

**Honest about limits.** ρ̂ is not safely estimable on our data at `M = 336` by (A)'s own ill-conditioning
test (`T < ½M(M−1)`), so it must be reported as an **assumption band**, not a measurement — fortunately
the conclusion is invariant across the band. `V[{SR_n}]` is currently unknown and dominates DSR. Skewness
and kurtosis on 13 trades are noise, so the non-Normality terms — which would likely help us, given the
Turtle's positive skew — must be defaulted off until `T ≥ 30`. CSCV must run on **daily bars**, not
trades, which re-opens §54.22's intermittent-returns objection and forces a drawdown-based metric.
And the deepest limit is the one §73.6 already named and no formula in §78 touches: **counting `N`
honestly across sessions is a discipline problem, and PBO is gameable in *both* directions — hiding
trials understates overfitting, and padding the matrix with deliberate losers does too.**
