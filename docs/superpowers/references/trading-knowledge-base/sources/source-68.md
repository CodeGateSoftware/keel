[← Knowledge Base index](../README.md)

## Source 68 — Two PDFs: "Options Trading Strategies" (National Stock Exchange of India Ltd., NCFM module, 2009, 60pp) + "Trading Strategies: Earning More in Investment" (MCM/ICM 2022 Mathematical Contest in Modeling, Problem C, Team #2226491, arXiv:2209.03294v1 [cs.OH], 7 Sep 2022, 23pp)

> **Both documents are wholly out of scope for `keel`, and neither yields a mechanically-testable
> rule.** PDF A is a 22-strategy options-payoff catalog — the entire instrument class is excluded
> (gharar/maisir, not spot) and this is simply a bigger, more systematic restatement of ground the
> Swissquote options series (§42–§49) already covered. PDF B is a student math-contest paper that
> builds a BTC/gold allocation system out of three techniques the KB has **already independently
> declined**: ARIMA price forecasting (a prediction oracle, §6.4/§54 Ch.6), Markowitz mean-variance
> + Sharpe ratio (riba-anchored on a risk-free rate, §33/§50.1/§54.22), and Particle Swarm
> Optimization (a black-box non-reproducible optimizer in the same family as the excluded GA,
> §54.22/§58.16). Nothing here reopens any of those decisions; if anything, PDF B's own
> eye-popping, real-money-implausible backtested return is a fresh, concrete illustration of
> exactly the failure mode §64.1 warned about. **This is a short file because that is the honest
> output — do not read padding into it.**

---

### 68.1 PDF A — "Options Trading Strategies" (NSE, NCFM module, 2009)

**What it is.** A National Stock Exchange of India (NSE) certification-course module (NCFM),
copyright 2009. It is a pure options-payoff catalog: 22 numbered strategies (Long/Short Call,
Synthetic Long Call, Long/Short Put, Covered Call, Long Combo, Protective Call, Covered Put, Long/
Short Straddle, Long/Short Strangle, Collar, Bull/Bear Call Spread, Bull/Bear Put Spread, Long/
Short Call Butterfly, Long/Short Call Condor), each given a "when to use" box, a worked Nifty-index
numeric example, a payoff table, and a payoff diagram built from combining the six basic payoffs
(long asset / short asset / long call / short call / long put / short put) introduced in §1.

**Halal disposition: wholly excluded, not extracted.** Every one of the 22 strategies is built from
buying and/or writing options — by definition gharar (the option's value depends on an uncertain
future exercise decision) and, for every short-option leg (12 of the 22 strategies write at least
one call or put), maisir/premium-for-undertaking-risk with no underlying ownership. None of it is
spot, none of it is long-only-asset-only. This is the **same exclusion already applied wholesale to
the Swissquote options series** (§42 introduction, §43–49 individual strategies) — this source adds
volume (22 strategies vs. Swissquote's ~6) but no new halal reasoning and no technique that
survives the screen. **Reinforces §27.4/§28.1/§42 — do not re-derive the gharar/maisir argument
again; it is settled.**

One structural note worth a single line: the book's own payoff-composition method (build any
complex strategy's diagram by summing simpler payoff legs) is a clean pedagogical device, but it
has no agent-side use — `execution/executor.py` never constructs option payoffs, and the technique
doesn't generalize to a spot long/flat position (which has exactly one payoff shape). Not adopted.

### 68.2 PDF B — "Trading Strategies: Earning More in Investment" (MCM/ICM 2022, Team #2226491)

**What it is.** A submission to the 2022 Mathematical Contest in Modeling (MCM), Problem C,
later posted to arXiv (cs.OH — "Other Computer Science", i.e. not a peer-reviewed finance venue).
The task, as the paper states it: given only past daily BTC and gold prices, decide buy/hold/sell
each day to maximize the value of a $1,000 starting portfolio over ~5 years (Sept 2016 – Sept
2021), accounting for per-trade commissions (1% gold, 2% BTC). The team's pipeline ("CTPModel"):

1. **Dynamic programming state** — normalize daily holdings to `[cash%, gold%, bitcoin%]` of
   total portfolio value; each day's trade is a proportional shift `x` (gold) / `y` (bitcoin).
2. **ARIMA(1,1,1) price forecasting** — fit an ARIMA model over a rolling lookback window to
   forecast each asset's price 3 days ahead; the lookback length `T` is itself tuned (they land on
   `T=60` days by minimizing worst-case R² across the fitting window, then adapt ±1 day per step).
3. **Markowitz mean-variance** — compute the "optimal" gold/BTC/cash allocation ratio from the
   ARIMA-forecast return distribution, assuming near-independence between gold and BTC.
4. **Sharpe-ratio + Particle Swarm Optimization** — since gold/BTC are not actually independent,
   they replace the pure Markowitz output with a Sharpe-ratio objective (`SR = (E[return] − risk-
   free rate) / stdev[return]`), searched over a 6-dimensional decision space (this day's + next
   two days' `x,y`) using a swarm of 100 particles, under three named "risk personalities" (Crazy /
   Middle / Stable, differing only in the objective: raw 3-day-ahead value vs. Sharpe ratio vs. a
   blended return-minus-0.618×σ term).

**Headline result:** the "Crazy" personality turns $1,000 into **$21.86 million** over 5 years
(≈21,856×); "Middle" reaches ~$4,103; "Stable" ~$1,353. A ±1–3% perturbation sensitivity check
confirms the chosen path is a local optimum of their own objective, and a transaction-cost sweep
shows the allocation shifts toward whichever asset got *cheaper to trade* (raising gold's cost or
lowering BTC's pushes the model to hold more BTC, and vice versa) — an artifact of the cost term in
their optimization, not a trading insight.

**Verdict: none of the three core techniques survive on our system, and none is new ground.**

### 68.3 ARIMA price forecasting — reconciles with §6.4 / §54 Ch.6 (already excluded)

ARIMA is a statistical **price-prediction** model: it fits an autoregressive-integrated-
moving-average process to past prices and projects a point forecast forward. That is precisely
the no-oracle violation the design spec's §6.4 rails against — `keel`'s intelligence is deterministic
tested rules + backtest stats, never a forecast of the next N days' price used as a live input to a
decision. The KB already excluded this exact technique once, at arm's length: Kaufman's *Trading
Systems and Methods* Ch. 6 (ARIMA-family models) was excluded on the same no-oracle grounds when
§54 was extracted (README module-map, Kaufman Part-1 recommendation: *"Excluded per halal/no-
oracle/scope: ... Ch 6 ARIMA"*). PDF B's own §5.2 "Disadvantages" section volunteers the reasons this
was the right call independent of any halal concern: *"the ARIMA model requires that the time
series data is stable... it can only capture linear relationships, not nonlinear ones"* and *"the
prices of various financial assets, especially Bitcoin, have undergone major changes in recent
years [so] the data we select from previous periods often cannot replace the situation in recent
years."* That is the model's own authors describing regime non-stationarity and linear-only
capture — the exact failure mode a deterministic, walk-forward-validated rule set (§54.10) is built
to avoid depending on. **No action; reinforces the existing ARIMA exclusion, does not reopen it.**

### 68.4 Markowitz mean-variance + Sharpe-ratio objective — reconciles with §33.1 / §50.1 / §54.22

Two declined-direction items, both already on file, both re-appear here unchanged:

- **Markowitz mean-variance portfolio optimization** was already assessed and declined at §33
  ("A Review on Portfolio Optimization Models for Islamic Finance") — declined because it is a
  quant-stack that (a) requires a return-covariance forecast (itself dependent on a price-
  prediction step, compounding the ARIMA problem above) and (b) treats "cash" as a riskless,
  return-bearing comparator asset, which is the CAPM/MPT riba anchor §50.1 already flagged.
  PDF B's own model explicitly needs `y_f`, "the return of the risk-free asset," inside its Sharpe
  ratio (§3.3.2, eq. 19) — a textbook instance of the exact Rf dependency §50.1 named as the reason
  CAPM/MPT-family objectives are not our lane. **Reinforces, does not add.**
- Kaufman's own GASP genetic-optimizer chapter (§54.22) already established that we adopt only the
  **long-only constraint, info-ratio/drawdown objective, and intermittent-returns insight** from
  that literature, explicitly declining Sharpe/covariance-driven optimizers for the Turtle's
  mostly-cash return profile. PDF B's Sharpe-ratio objective is the same family, applied to a
  different (BTC/gold, not futures) portfolio. No new information.

### 68.5 Particle Swarm Optimization — reconciles with §54.22 / §58.16 / §64.1 (black-box, non-reproducible optimizer class)

PSO searches a continuous decision space using a swarm of interacting candidate solutions with no
closed-form or auditable derivation of *why* a given allocation was chosen — mechanically the same
objection the KB already raised against genetic algorithms and neural networks (§54's black-box AI
exclusion, restated at §58.16 and the README's "Explicit exclusions" list: *"Neural-network and
genetic-algorithm models — excluded as non-reproducible black boxes... knowingly forgoing this
source's best out-of-sample results"*). PSO is not literally a GA, but it is the same class of
population-based metaheuristic with the identical audit problem: the swarm's converged solution
cannot be decomposed into a rule an operator can read, reproduce by hand, or attribute to a
specific market feature — precisely what `strategy/rules/` requires (deterministic, parameterized,
inspectable) and what the deferred-LLM-feature spec (§35.1/§64.7) requires of *any* scoring model
that isn't pure rule logic. **No new exception to carve out; the existing black-box exclusion
already covers this technique by class, not by name.**

### 68.6 ⚠️ Negative exemplar — a fresh, concrete case for §64.1's warning

§64.1 (Cliff & Rollins, "Methods Matter") documented, at adversarial scale (1.1M+ simulated
sessions), that a decade-plus of *published, peer-reviewed* AI/ML trading-superiority claims
**inverted** once re-tested realistically — "good answers to the wrong question." PDF B is a
smaller-scale but strikingly on-point illustration of the same dynamic from the *authoring* side:
a forecast-plus-black-box-optimizer stack (ARIMA → Markowitz/Sharpe → PSO) produces a **21,856×
five-year return on paper**, with no out-of-sample split, no walk-forward firewall, no random-entry
control (§58.11), and no live/paper validation — evaluated only against a perturbation-sensitivity
check that confirms it's a local optimum of *its own* objective, which says nothing about whether
that objective generalizes. The paper is transparent about this itself (§5.2, "Disadvantages":
regime non-stationarity, linear-only ARIMA, "it takes a very long time" to run) — it does not
claim the 21,856× figure is achievable live, and neither should we read it as evidence for
anything. Logged as reinforcement for the **min-sample floor + walk-forward/OOS firewall + zero
evaluation shortcut** posture (§54.10, §58.11, §64.1) — a spectacular unvalidated backtest number
is not evidence, from any source, contest paper or otherwise. **No rule; a caution restated with a
concrete number attached.**

### 68.7 ⛔ Halal exclusions (explicit)

- **PDF A, wholesale:** all 22 strategies — options (gharar), 12 of them net option-writing
  (maisir + unbacked-obligation risk), none spot, none long-only-asset. Same exclusion class as
  §27.4/§28.1/§42–49.
- **PDF B:** the Sharpe-ratio objective's risk-free rate `y_f` (§3.3.2) is a riba-anchored quantity
  by definition (a guaranteed return on a riskless asset) — even though the paper never actually
  trades a bond/bank product, using it as a benchmark inside the objective function imports the
  same CAPM/MPT riba framing §50.1 already declined. No leverage, no shorting, no derivatives
  appear in PDF B — it is a **prediction/optimization-technique** exclusion, not an
  instrument-halal violation, distinct from PDF A's instrument-level exclusion.

### 68.8 Discarded (no agent value)

- **PDF A:** the six basic payoff-diagram primitives (§1.2, long/short asset/call/put) — pedagogical
  scaffolding for building the 22 combination strategies, no standalone agent use since our
  positions are never option legs. All 22 worked Nifty-index numeric examples and payoff tables —
  arithmetic illustrations of the excluded instrument, nothing transferable.
- **PDF B:** the dynamic-programming state normalization (§3.1, eqs. 1–9) — a bookkeeping
  convenience for a 3-asset (cash/gold/BTC) simultaneous-optimization problem; our position ledger
  (per the newly-shipped per-tranche ledger) already tracks holdings per asset without needing a
  joint allocation-ratio formalism, because we do not co-optimize allocation across assets via a
  single objective — each asset trades its own rule independently under shared caps. The ARIMA
  Python appendix (`pmdarima`/`statsmodels` boilerplate) — implementation detail of an excluded
  technique. The three "risk personality" framings (Crazy/Middle/Stable) — a UI/persona layer over
  an already-excluded optimizer, not a sizing or promotion concept we can detach and reuse. The
  transaction-cost sensitivity table (§4.2) — confirms only that their optimizer chases whichever
  asset is momentarily cheaper to trade, an artifact of their cost term, not a generalizable
  fee-awareness rule (we already have maker-fee awareness via §58.1's limit-entry finding).

---

### Net assessment (saturation-honest)

**Nothing new.** PDF A is a bigger options catalog than the Swissquote series already logged and
excluded at §42–49 — it does not change the exclusion or add a technique. PDF B is a well-executed
student contest paper whose entire technical stack (ARIMA forecasting, Markowitz/Sharpe, PSO) sits
squarely inside ground the KB already covered and declined across three separate prior sources
(§6.4/§54 Ch.6 for ARIMA; §33/§50.1/§54.22 for Markowitz/Sharpe; §54.22/§58.16 for black-box
optimizers). The one thing worth keeping is soft: PDF B's own implausible headline return is a
clean, concrete illustration to cite alongside §64.1 the next time an unvalidated-but-spectacular
backtest number needs a rebuttal.

**No harness-testable candidate in this source.** Do not build anything from it.

**Recommendation:** neither NSE-style options-strategy catalogs nor ARIMA/Markowitz/PSO academic
quant papers are a productive vein for `keel` going forward — the first is a closed, fully-excluded
instrument class; the second is a closed, fully-declined technique class. Future sourcing value
remains where the README already says it does: crypto-appropriate, mechanically-testable
*technical* trading-system books (the Kaufman/Katz & McCormick lineage), not derivatives-strategy
catalogs or prediction-and-black-box-optimizer research papers.
