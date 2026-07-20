[← Knowledge Base index](../README.md)

## Source 62 — Three academic papers on optimal execution timing and time-domain portfolio theory

> **Paper A:** Labadie, M. & Lehalle, C.-A. (2013), *"Optimal starting times, stopping times and risk
> measures for algorithmic trading: Target Close and Implementation Shortfall"* (arXiv:1205.3482v6,
> 27pp).
> **Paper B:** Bebbington, P. & Kühn, R. (2016), *"Optimal trading strategies — a time series approach"*
> (J. Stat. Mech. (JSTAT) 2016, King's Research Portal manuscript, 15pp).
> **Paper C:** Ramasamy, V. & Prabakaran, G. (2018), *"Optimal Trading Strategies and Performance of
> Options at NSE"* (Int. J. Adv. Res. 6(5), 8pp).
>
> **All three are read abstract/conclusion-first per the method note, and the verdict is blunt: two are
> genuine stochastic-calculus / time-series papers whose *results* are almost entirely untranslatable
> (they solve a precisely-stated academic problem that is not our problem), but each yields exactly one
> structural insight worth keeping — and the third (Paper C) is a weak, wholesale-excluded options paper
> with nothing to extract at all.**
>
> - **Paper A** is intraday **block-order execution scheduling** (splitting one large order into ~100
>   slices within a single trading session to trade off market impact against price risk). Its "starting
>   time" and "stopping time" are *when within one day to begin/finish slicing one order* — not *when to
>   enter or exit a multi-day position*. Despite the title, it does not bear on our exit-timing question
>   in any direct way. Kept: the **TC/IS mirror-symmetry** insight (§62.1) and an independent
>   **Hurst-exponent** derivation of the trend/noise/mean-revert trichotomy that triangulates with §54's
>   Efficiency Ratio.
> - **Paper B** is a **time-domain reformulation of Markowitz mean-variance** for a single asset — how to
>   split a *fixed total position* across a discrete time horizon to minimize variance, using the
>   **autocovariance matrix** of the price/return process. Kept: a rigorous, independent (statistics, not
>   technical-analysis) confirmation of *why* our tested mean-reversion/DCA rule needs a genuinely
>   mean-reverting regime to be sound — which explains, after the fact, why dip-buying was refuted on
>   crypto (§62.2).
> - **Paper C** is not really an academic paper by our standards (secondary NSE data, N≈27 expiries, no
>   risk-adjusted stats, no out-of-sample test) and its entire subject is **options straddle/strangle
>   premium-selling strategies** — doubly/triply excluded (derivatives + index-not-spot + short-vol). It
>   is logged and discarded wholesale (§62.3), consuming none of the module map.

---

## §62.1 — Paper A: the TC/IS mirror-symmetry and the Hurst-exponent trichotomy (excluded machinery, kept insight) — `execution/executor.py`, `analysis/regime.py`

**The problem the paper actually solves.** A trader must execute a large order (say 150,000 shares) of
a single stock over one trading day. Two benchmarks are considered: **Target Close (TC)** — trade as
close as possible to the closing auction price, with the *end time fixed* (market close) and the
**start time unknown**; and **Implementation Shortfall (IS)** — trade against the price at decision time,
with the *start time fixed* and the **stop time unknown**. Both are solved with an Almgren-Chriss
mean-variance framework: minimize `E[cost] + λ·Var[cost]` over the intraday trading curve, where cost
is a power-law temporary market-impact function `h(v) = κσ√τ·(v/V)^γ` calibrated from proprietary
volume/impact data, subject to a maximum-participation-rate (Percentage-of-Volume) constraint.

**Why none of the machinery ports.** This requires: (1) **continuous intraday trading** — the position
is sliced across ~100 pillars in one session; our agent places a handful of daily-bar entries a month.
(2) An order size that is a **material fraction of the asset's daily volume**, big enough that trading it
moves the price — our position sizes (1% risk of a modest spot account) are immaterial against BTC/ETH
daily spot volume; there is no market-impact problem to solve. (3) A **calibrated impact function**
(κ, γ) from historical order-flow data we do not have and do not need. (4) A risk-aversion Lagrange
multiplier λ and a mean-variance objective — the same MPT/mean-variance lineage already declined for
portfolio construction (§33, §54.22); here it is applied to timing a single order, but it is the same
family. → **§62.1 as a whole is compliance/scope-excluded from the build: excluded on scope grounds
(continuous trading + institutional order sizing + proprietary impact calibration), not on riba grounds**
— no risk-free rate appears anywhere in this paper.

**What survives — two structural points:**

1. **⭐ The mirror-symmetry between starting-time and stopping-time optimization (§2.5).** The paper's
   central formal result is that the TC (find-the-start) and IS (find-the-stop) problems are **the same
   recursive formula with time running backwards** — "TC can be seen as a reverse IS." *Reading:* our
   project's two open, unrelated-feeling problems — **under-deployment** (when to commit capital: an
   entry-timing / starting-time question) and **exit timing** (`max_hold`/time-stop, §57.2) — are
   formally the same *kind* of question, just at opposite ends of a hold. This does not hand us an
   algorithm (our hold horizon is ~24 days of daily bars, not ~100 intraday slices, and we have no
   impact function), but it is a legitimate reason to design a deployment-cadence rule and a time-stop
   rule **as a mirrored pair** rather than as two independent features — e.g. if `max_hold` uses "N days
   with no progress → exit," the deployment-side analogue is "N days of a live confirmed signal with no
   fill → escalate/re-evaluate," rather than treating them as unrelated one-offs.

2. **⭐ The self-similarity exponent `H` (Hurst exponent) as a fourth, independent derivation of the
   trend/martingale/mean-revert trichotomy (§3, §4.3, §5).** The paper generalizes the risk measure from
   variance to the **p-variation**, with `p = 1/H`. It shows explicitly: **`H > 1/2` ⇒ trend, `H = 1/2` ⇒
   martingale (no exploitable structure), `H < 1/2` ⇒ mean-reverting** — and that a *more aggressive*
   execution schedule (later start, steeper finish) corresponds to a higher `p` (lower `H`, i.e. the
   opposite direction from what "more trending" might suggest naively — the aggressiveness here is a
   function of the *fixed-close* anchor of the TC problem specifically, not a general trending-market
   rule; see caveat below). *Reading:* this is the **third independent mathematical route** (after
   Kaufman's empirical Efficiency Ratio/ADXR, §54.1/§54.9, and Paper B's autocorrelation coefficient
   below) to the same trend/noise/mean-revert classification our project already uses to gate strategy
   choice. It is **reinforcement, not new content** — logged because three independent formalisms
   (fractal self-similarity, empirical trend-strength indicators, and time-series autocovariance)
   converging on the same trichotomy is a meaningfully stronger validation of the "classify the regime,
   then choose the method" design than any one of them alone (feeds the open **data-driven
   asset-trendiness ranking** defect, §54.9/§54.17/§54.21).

**⚠️ Blunt caveat — do not import the "aggressiveness" direction as a rule.** Paper A's finding that
higher trend-confidence ⇒ *later* start / *back-loaded* execution is an artifact of the TC problem's
**fixed terminal deadline** (must finish by the close). Paper B's finding below, for an *unconstrained*
horizon, points the opposite way (momentum ⇒ *front-load*). The two are not in tension about markets —
they are exact answers to two differently-anchored formal problems, neither of which is our problem
(placing one entry order for a ~24-day hold with no terminal deadline and no impact cost). This is
exactly the kind of "precise answer to the wrong question" the project brief warned about: useful as a
reminder that **timing-curve shape does not transfer across problem formulations**, even when the
qualitative regime-classification underneath does.

---

## §62.2 — Paper B: time-domain Markowitz, autocovariance, and why DCA needs genuine mean-reversion — `analysis/regime.py`, `strategy/money_mgmt.py`

**The problem the paper actually solves.** Translate Markowitz mean-variance portfolio optimization from
the cross-sectional (many-assets) domain into the **time domain**: given a *single* traded asset and a
fixed discrete horizon of `T` time-steps, split a total position across those steps to **minimize the
variance** of the resulting P&L for a given target expected return, using the asset's own
**auto-covariance matrix** (not a cross-asset covariance matrix) as the risk model. Solved in closed form
for white-noise and AR(1) synthetic processes, then applied to 65 years of S&P500 daily closes, with an
explicit treatment of finite-sample estimation noise and a Stein-type shrinkage "cleaning" strategy for
the sample auto-covariance matrix (paralleling the random-matrix-theory covariance cleaning literature).

The paper explicitly states it **ignores discounting** in this exploratory study — so no risk-free rate
enters anywhere in the model. Nothing to flag on the riba axis for this paper specifically. It remains
excluded from adoption on other grounds: it is literally Markowitz mean-variance math (§62.2 below), the
same declined-direction machinery as §33/§54.22 (MPT/GASP), just re-derived in the time domain instead of
across assets — reinforcing, not reversing, that decline.

**Three closed-form results, and what each says once translated:**

1. **i.i.d. (white-noise) case → uniform allocation `π* = (1/T, ..., 1/T)`.** When returns have *no*
   serial correlation, the variance-minimizing split of a fixed position across the horizon is to spread
   it **evenly** — this is, formally, dollar-cost-averaging, and it is provably optimal **only in the
   absence of a timing signal**. *Reading:* this is the null case against which the other two cases are
   the deviation. It does not endorse DCA as a strategy for us (our project doesn't select "spread a
   fixed position evenly" as a design choice at all — we place one entry per rule trigger), but it
   supplies the missing theoretical reason *why* DCA is the textbook answer when no signal exists, which
   sharpens the next point.

2. **AR(1) with `a < 0` (mean-reverting returns) → scale in over time, `π*_GO = (1+a, -a, 0, ..., 0)`
   with `1+a < 1` and `-a > 0`** — i.e. a **smaller** initial position, added to at the next step. This
   is exactly the mathematical shape of dip-buying/DCA-into-weakness. *Reading — the useful, honest
   point:* the paper's math says this is variance-optimal **only when `a < 0` is genuinely true** (the
   asset's returns really are mean-reverting over the relevant horizon). Our project's own empirical
   result — **pullback-continuation and RSI mean-reversion were REFUTED on crypto** — is exactly what
   this predicts if crypto's daily-bar return process does *not* actually satisfy `a < 0` at the horizon
   tested (i.e. BTC/ETH daily bars are closer to trending/noisy than mean-reverting, consistent with
   Kaufman's Efficiency-Ratio finding that crypto is comparatively noisy, §54.1/§54.9). This is a genuine
   **reconciliation, not a new rule**: it gives a rigorous, independent explanation for *why* dip-buying
   failed (a regime-mismatch, not a bug), and it reinforces the existing prescription that any future
   mean-reversion rule must be **gated behind the ER/ADXR/run-distribution trend classifier**
   (§54.21) rather than applied as a standing strategy.

3. **AR(1) with `a > 0` (momentum/positively-autocorrelated returns) → trim into the move,
   `π*_GO = (1+a, -a, 0, ..., 0)` with `-a < 0`** — a full long position at the first step, **partially
   offset by a short** at the second. *Halal translation:* we cannot short; the long-only reading is
   **"don't hold a constant full-size position through a positively-autocorrelated run — reduce exposure
   as it extends."* This is not a new rule either: it is the same prescription already adopted as
   **trail-to-breakeven (§26.2)** and **split-exit / partial-fixed-target-then-trail (§60.3)**. Logged as
   an independent variance-theoretic justification for those exits, arrived at from statistics rather
   than technical-analysis convention — a second confirmation, not a build item.

**Sampling-noise / shrinkage finding (§62.2, technical craft, excluded as a build item).** The paper
shows finite-sample auto-covariance estimates **underestimate risk**, and that "cleaning" the sample
matrix via Stein-shrinkage toward a diagonal (independent-increments) target produces *smoother* optimal
weights and substantially lower realized risk on the S&P500 data — a direct time-domain analogue of the
Marchenko-Pastur-style covariance cleaning literature. This parallels the **rolling-correlation /
GASP-insight caution** already logged at §54.22 (don't trust noisy, over-fit covariance/correlation
estimates), but it requires estimating a `T×T` auto-covariance matrix across many overlapping windows —
infrastructure our system has no use for, since we never split one entry into multiple time-sliced
sub-trades. **Not adopted; reinforces the existing "don't over-fit correlation estimates" posture only.**

---

## §62.3 — Paper C: NSE options straddle/strangle "optimal strategies" — ⛔ wholesale excluded, no extraction

Ramasamy & Prabakaran study four **options** strategies (long straddle, short straddle, long strangle,
short strangle) on NSE Nifty index options, using ~27 monthly expiries (2014–2016) of secondary
newspaper/bulletin data, and conclude that **short strangle** had the highest cumulative return in their
small sample. There is nothing here that survives the halal screen or the scope filter:

- **Options themselves** — every strategy in the paper is built from calls and puts; gharar/maisir,
  doubly excluded regardless of direction (§27.4/§28.1, consistent with the entire §42–49 options-series
  exclusion already logged).
- **Short premium-selling** (short straddle/strangle, the paper's own best performer) — sells uncapped
  risk for a capped premium with no underlying ownership; the closest existing analogue is the already-
  excluded "selling a naked call/put to open" (§45/§48) — triply excluded here (derivative + short +
  premium-for-assumed-risk).
- **Instrument** — **index options** (Nifty), not a spot asset at all; no position in an owned asset ever
  exists in this paper's strategies, which is the opposite of our long-only spot model.
- **Methodology** — no risk-adjustment (Sharpe/Sortino/drawdown), no out-of-sample split, N≈27, and the
  "optimal" conclusion is simply "which of four fixed structures had the highest raw cumulative return in
  one small sample" — this would not clear our own promotion-floor bar (§54.10/§54.11) even if the
  instrument were permissible.

**Nothing in this paper is extracted.** It consumes no module-map row. Logged only so the source is
accounted for and not re-fed.

---

## Reconciliation with prior sources

- **§54 (Kaufman) — the anchor source.** Papers A and B each independently re-derive the same
  trend/martingale/mean-revert trichotomy that §54.1's Efficiency Ratio and §54.9/§54.17/§54.21's
  market-ranking machinery already give us empirically — Paper A via the **Hurst exponent** (fractal
  self-similarity), Paper B via the **AR(1) autocorrelation coefficient** (time-series statistics). Three
  independent mathematical routes to the same classification is a genuine strengthening of confidence in
  the "classify the regime, then gate strategy choice on it" design, **but adds no new indicator or
  threshold** — the existing ER/ADXR/run-distribution tooling remains the one to build against.
- **§33/§54.22 (declined MPT/mean-variance direction).** Both Papers A and B are Markowitz-lineage
  mean-variance mathematics (cost-functional Lagrangians, λ risk-aversion, covariance/auto-covariance
  matrices) — the same family already declined for portfolio construction. Nothing here reverses that
  decision; if anything, re-encountering the identical machinery in two more papers (now applied to
  *timing* rather than *cross-asset allocation*) reinforces treating the whole quant-mean-variance stack
  as declined-direction for this project, while still mining the occasional qualitative nugget that
  survives translation (as done here).
- **§57.2 (no `max_hold`/time-stop exists).** Paper A's TC/IS mirror-symmetry (§62.1) is a reason to
  design the still-unbuilt time-stop and the deployment-cadence question as a **mirrored pair** rather
  than two unrelated backlog items.
- **§60.2 (deployment-cadence rank-pick, targets under-deployment).** Paper B's momentum/mean-reversion
  results do not hand over a better cadence formula, but they explain *why* front-loading (not
  drip-feeding) is the theoretically sound response once a trend is confirmed — which is exactly what
  the already-adopted **Donchian/Turtle breakout** does (deploy the full sized position immediately on a
  confirmed signal, no time-slicing). No rule change; the existing rule is, if anything, independently
  validated by a totally different branch of math.
- **§54.21 (run-distribution trending/mean-revert classifier) and the refuted dip-buy/RSI-mean-reversion
  finding (project history).** Paper B's `a<0` result is the cleanest theoretical explanation on file for
  *why* the pullback-continuation/RSI-mean-reversion family failed empirically on crypto: the rule
  assumed a mean-reverting regime it never verified, and the project's own regime-agnostic dip-buy tests
  found crypto largely does not satisfy `a<0` at the tested horizon. This is the single most useful
  "aha" in this source — a rigorous *explanation* of an existing, already-accepted empirical finding,
  not a new rule.
- **§26.2/§60.3 (trail-to-breakeven, split-exit).** Paper B's `a>0` result (trim into a positively-
  autocorrelated run) is an independent statistical justification for exits already adopted — reinforces,
  does not extend.
- **Halal exclusions (§28/§30, options series §42–49, §45/§48).** Paper C adds no new exclusion category;
  it is a straightforward re-instance of the already-logged options/short-premium exclusion, and Paper
  B's `a>0` short-offset term is handled the same way the KB has always handled short legs in
  otherwise-adoptable results: keep the long leg, discard the short leg, translate its intent (reduce
  exposure) into a long-only equivalent already on file.

---

## Halal exclusions (explicit)

| Item | Source | Exclusion | Grounds |
|---|---|---|---|
| Market-impact-optimal execution scheduling (TC/IS, shooting method) | Paper A, §2–2.8 | Scope-excluded (not riba) | Requires continuous intraday trading + institutional order sizes + proprietary impact calibration — structurally N/A, no r involved |
| Mean-variance Lagrangian / risk-aversion λ (both papers) | Paper A §2.1/2.6; Paper B §II–III | Declined-direction (quant-stack), not riba | Same MPT/mean-variance family already declined at §33/§54.22; no risk-free rate present in either paper |
| AR(1) short-offset term (`-a` at t=2 when a>0) | Paper B, §III.B | Excluded (shorting) | Long-only spot rule; translated to "trim exposure," not "short," per the standing short→exit-filter convention |
| All options strategies (straddle/strangle, long & short) | Paper C, entire paper | Excluded (derivatives) | Gharar/maisir, not spot; consistent with §27.4/§28.1 and the full §42–49 options-series exclusion |
| Short strangle/straddle specifically | Paper C | Excluded (short + derivative) | Naked/uncapped-risk premium-selling, same category as §45/§48 |
| Index options (Nifty), not a spot asset | Paper C | Excluded (instrument) | No ownership of an underlying ever exists in these strategies |

**Note on the risk-free rate specifically:** unlike much of the surrounding literature this KB has
screened, **neither Paper A nor Paper B invokes a risk-free rate `r`** — Paper A's cost functional is
pure market-impact-vs-price-risk with no discounting term, and Paper B explicitly states it "ignore[s]
economic factors such as discounting" in its own introduction. The exclusions above are therefore on
**scope/instrument/leverage grounds**, not riba grounds, for both surviving papers. Paper C likewise
involves no risk-free rate — its exclusion is purely on the derivatives/short-selling/instrument axes.

---

## Discarded (no agent value)

- **Paper A:** the full stochastic-calculus machinery — the shooting-method ODE solver (§2.2), the
  explicit recursive TC/IS formulas and their PVol-constraint case split (§2.3–2.7), the p-variation risk
  measure and its equivalence to Lévy/fractional-Brownian-motion self-similar models (§3), the empirical
  CAC40 implied-`p`-vs-liquidity regression (§4.3) — all precisely calibrated to a problem (splitting one
  institutional block order across ~100 intraday pillars) we do not have.
- **Paper B:** the closed-form Toeplitz auto-covariance-matrix algebra for white-noise/AR(1) processes
  (§III.A–B), the Marchenko-Pastur / random-matrix-theory spectral comparison and Stein-shrinkage cleaning
  procedure applied to 65 years of S&P500 data (§IV) — sound statistics, but for a variance-minimization
  problem (splitting one fixed position across many time-slots) that doesn't match how our system places
  an order.
- **Paper C:** discarded in full — see §62.3. Every payoff diagram, breakeven-point calculation, and the
  2014–2016 NSE return table are options-specific and contribute nothing to a long-only spot system.

---

## Net assessment (saturation-honest)

**Two genuinely academic papers, both almost entirely untranslatable by design — and both worth having
read anyway, because each supplies exactly one thing:** Paper A's **entry/exit mirror-symmetry** (design
the still-missing deployment-cadence and time-stop features as a matched pair, §62.1) and its **Hurst-
exponent triangulation** of the trend/noise classification (third independent confirmation, alongside
Kaufman's ER and Paper B's own autocorrelation route). Paper B's **cleanest available explanation for why
dip-buying failed on crypto** (it required `a<0` mean-reversion that was never verified, §62.2) and an
independent statistical justification for exits already adopted (trail-to-breakeven, split-exit). **No
new rules, no new indicators, no new rails.** Both papers reinforce/explain existing project decisions
rather than reshape them — closer in kind to §54.22's GASP-insight role (explaining *why* an existing
choice is sound) than to §54.1's or §27's role (handing over a new buildable rule).

**Paper C is a negative-value source** — a thin, methodologically weak options paper with zero
applicability; logged only to close it out. Not a stream worth following further (no author/publisher
overlap with anything else in the KB).

**Recommendation:** no build action from this source. If a `max_hold` time-stop and a deployment-cadence
rule are ever prototyped (both still open per §57.2/§60.2), design them together per §62.1's mirror
insight rather than independently. Do not seek out more institutional-execution-microstructure papers
(Almgren-Chriss-family) — the entire genre is structurally N/A to a small, long-only, daily-bar spot
agent, as this source demonstrates conclusively. Time-series/econophysics papers in Paper B's vein
(autocovariance, regime statistics) are moderately more promising if a future one directly addresses
**regime detection** (trending vs mean-reverting classification) rather than **portfolio-weight
optimization**, since the latter half is saturated MPT-lineage math we've now declined three times.
