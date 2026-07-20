[← Knowledge Base index](../README.md)

## Source 83 — "Risk Management and Trading Psychology" (Zerodha Varsity, Module 9)

**Provenance:** Zerodha Varsity, Module 9, by Karthik Rangappa (Zerodha, India; free online
textbook-style course, first published 2017, still maintained — comment threads run to Dec 2025).
Scraped from `https://zerodha.com/varsity/module/risk-management/` and the per-chapter URLs.
Indian-equity/F&O framing throughout (Nifty, stock futures, lots, margins, Rupees).

**16 chapters.** Twelve were scraped and read in full; four were not opened, each for a stated reason:

| # | Chapter | Status here |
|---|---|---|
| 1 | Orientation note | not opened — front matter |
| 2 | Risk (Part 1) | read → §83.10 |
| 3 | Risk (Part 2) – Variance & Covariance | read → §83.2 |
| 4 | Risk (Part 3) – Variance-Covariance Matrix | read → §83.2 |
| 5 | Risk (Part 4) – Correlation Matrix & Portfolio Variance | read → §83.2 |
| 6 | Equity Curve | read → **§83.2 (the keeper is here)** |
| 7 | Expected Returns | read → §83.8 (negative exemplar) |
| 8 | Portfolio Optimization (Part 1) | not opened — ⛔ MPT, and ch. 9 restates its method in full |
| 9 | Portfolio Optimization (Part 2) | read → ⛔ excluded, see *Halal / declined-stack exclusions* |
| 10 | Value at Risk | read → §83.4 |
| 11 | Position Sizing for active trader | read → §83.6, §83.9 |
| 12 | Position Sizing (Part 2) | read → **§83.1 (the keeper is here)** |
| 13 | Position Sizing (Part 3) | read → §83.3, §83.7 |
| 14 | Kelly's Criterion | read → §83.5, §83.11 |
| 15 | Trading Biases | not opened — psychology, saturated (see *Discarded*) |
| 16 | Trading Biases (Part 2) | not opened — psychology, saturated (see *Discarded*) |

> **Honest headline: HEAVILY SATURATED — three keepers, and two of the three are small.**
> This module is a competent undergraduate treatment of exactly the ground §54 (Kaufman) already
> covers at far greater depth, plus a portfolio-theory arc that terminates in Markowitz mean-variance
> optimization — a direction this project declined four times over (§33, §50.1, §54.22, §68).
> Its position-sizing half is explicitly a summary of **Van Tharp**, whom §54 already supersedes.
> **Nothing here reshapes the risk model.** What it does supply is (a) one sizing-base distinction the
> KB genuinely never drew, (b) one arithmetic trick that makes a portfolio-level risk number
> computable in pure stdlib — which closes a loop **§79.8 itself left open** — and (c) a clean,
> citable statement of a VaR procedure that we should look at and then decline.

---

### §83.1 ⭐ The equity BASE for the next trade is a choice, and we made it by default `strategy/money_mgmt.py`, `execution/sizing.py`, `execution/equity.py`

Ch. 12's actual contribution — and the one thing in this module the KB has no analog for — is that
before you can apply *any* fixed-fractional rule you must answer a prior question: **with positions
already open, what number is "current equity"?** It gives three named models (attributed to Van Tharp):

| Model | Base for the next trade | Source's verdict |
|---|---|---|
| **Core equity** | starting capital **minus capital already committed**; unrealised P&L ignored entirely | conservative; liked for simplicity; shrinks allocation as positions stack |
| **Total equity** | free cash **+** committed capital **+** *unrealised* P&L on every open position | *"somewhat like counting the chicken before they hatch"* — the author's least favoured |
| **Reduced total equity** | free cash + committed capital, **plus only the profit a stop has already LOCKED IN** — unrealised profit above the stop is excluded | the author's preference; *"forces you to practice basic stop loss principles"* |

**Why this is live for us and not academic.** `keel/execution/sizing.py::size()` is
`qty = (equity × risk_pct) / |entry − stop|`, and the `equity` it is handed is marked to market. That
is the **Total equity model** — the one this source rates as the riskiest of the three — and we are
running it not as a decision but as a default. §65.9 already noticed one edge of this problem (a
rewards credit inflating the sizing base by ~$1/trade) without noticing the general shape: **any**
unrealised gain inflates the base, and it inflates it hardest exactly when a trend has run and the
next breakout signal fires — i.e. the sizing error is *positively correlated with late-trend entries*.

**The reduced-total-equity base, made mechanical for us:**
```
locked_in(tranche) = max(0, (stop_price − entry_price) × qty)     # long-only; 0 before breakeven
sizing_equity      = free_cash + Σ_tranches (entry_price × qty)
                                + Σ_tranches locked_in(tranche)
```
Two properties make this fit `keel` unusually well:
1. **It is monotone-safe under rail 9.** No-stop-widening guarantees a long's stop only ever ratchets
   toward profit, so `locked_in` is **non-decreasing per tranche by construction** — the same
   "satisfies the rail rather than needing to be checked against it" property §58.13b prized in MEMA.
2. **It composes with the per-tranche `positions` ledger** shipped in PR #96; every term above is a
   column that already exists.

⚠️ **This must NOT be applied to rail 11's drawdown equity.** `execution/equity.py` includes unrealised
P&L *deliberately* — a drawdown breaker that saw only locked-in P&L would read 0% while a position
bled and would fire only after the loss was booked, which is backwards for a circuit breaker. So the
correct outcome is **two different equity definitions for two different jobs**: mark-to-market for the
drawdown breaker (fires while you bleed), reduced-total for the sizing base (never sizes off profit a
stop has not secured). That "same quantity, different job, different definition" move has direct
precedent in §73.4, which re-admitted per-trade Sharpe as a *null-test statistic* without reopening
the four declinations of Sharpe as an *optimisation objective*.

**Stdlib portability: ✅ trivial.** Addition and `max()` over ≤ a handful of `Decimal` tranches. No
floats, no libraries, exact.

**Halal:** unaffected. The source's worked example uses *futures margin* as the committed-capital
term; for us the committed term is simply cash actually spent on spot — the model is
instrument-agnostic and the margin framing drops out.

**Status: a sweep/ablation candidate, not a build commitment.** It changes position size and therefore
every backtest number, so it is a `parameter_provenance`-bearing decision (§73.12) and costs trials
budget (§73.3) unless argued a_priori. The a_priori argument is available: this source and §54.19 both
independently warn against sizing off equity that a stop has not secured.

**Cross-refs:** §54.19 (reserves/equity model — related but *different*: Kaufman's is about *whether
to compound*, this is about *what the base is*), §65.9, §73.4, §73.12, §58.13b, §66.2.

---

### §83.2 ⭐⭐ Portfolio-level risk WITHOUT a covariance matrix — and it closes a loop §79.8 left open `strategy/money_mgmt.py`, `sim/metrics.py`

Chs. 3–5 build the textbook machinery: variance, covariance, an `n × n` variance-covariance matrix, a
correlation matrix `ρ(x,y) = Cov(x,y)/(σx·σy)`, weighted standard deviations `wᵢ·σᵢ`, and finally
```
portfolio σ = sqrt( transpose(w·σ) × CorrelationMatrix × (w·σ) )
```
All of that is standard and all of it wants a matrix library. **Then ch. 6 quietly demolishes its own
five chapters of setup.** It constructs a synthetic normalised portfolio series (start at 100, split
across assets by weight, compound each sleeve on its own daily return, sum the sleeves each day),
takes the plain standard deviation of *that one series'* daily returns, and gets **the identical
number** — the source's words: *"the STDEV function gives us the exact same value!"*

That identity is elementary once stated, but it is the operationally decisive fact for this project:

**A single portfolio-risk number for BTC/ETH/PAXG is obtainable with zero linear algebra.**
```
for each day t:
    port_value[t] = Σ_assets  sleeve_value[asset][t]      # each sleeve compounded on its own return
    port_ret[t]   = port_value[t]/port_value[t-1] − 1
sigma_portfolio_daily = stdev(port_ret)                    # plain sample stdev
sigma_portfolio_ann   = sigma_portfolio_daily × sqrt(252)
```
Correlation between the assets is **never estimated and never needs to be** — it is absorbed exactly,
by construction, in the summed series. No matrix, no transpose, no inversion, no conditioning problem.

**Stdlib portability: ✅ fully, and this is the whole point.** Running sums over a `deque`, one
`math.sqrt` (or `Decimal.sqrt()`) at the end. Contrast the matrix route, which is `❌` under the
declined NumPy/Pandas stack. **This is the rare case where the stdlib constraint costs nothing at all**
— joining §58.3/§74.6's finding that long-only cost nothing, as a second instance of a constraint
turning out to be free.

**Why it matters beyond convenience — it closes §79.8's open loop.** §79.8 ported (A)'s inverse-vol
sizing `40% / σᵢ(t;60)` and correctly flagged that **the 40% constant is not portable**, because it is
calibrated to land the *portfolio* near 12–15% vol given 71 assets, and instructed: *"port the
mechanism, re-derive the constant from a portfolio-vol target."* **Nothing in the KB could compute a
portfolio-vol number to re-derive it against.** §54.22 gives rolling *pairwise* correlations and
§54.7 gives *per-position* volatility parity; neither aggregates. This does, at N=3, in stdlib.
It is also the natural measurement partner for §83.3's proposed rail.

⚠️ **Two caveats, both ours not the source's.** (1) Applied to *our* returns it inherits §54.22's GASP
objection — a whole-period σ over the Turtle's intermittent, mostly-cash series understates risk,
because flat days enter the sample. Compute it over **exposed days only**, or per-trade, exactly as
§73.4 resolved the same problem for Sharpe. (2) It is a *measurement*, not an optimiser. Feeding it to
a weight-search is the MPT direction and is excluded — see below.

**Cross-refs:** §79.8, §79.9 (Yang–Zhang σ would feed this more efficiently), §54.7, §54.22, §73.4.

---

### §83.3 ⭐ A portfolio VOLATILITY budget — a candidate rail, and rail 4 does NOT already cover it `execution/guards.py`

Ch. 13.4 ends its "percentage volatility" sizing section with a portfolio-level instruction the KB has
no equivalent of: after setting per-position volatility exposure, **separately cap the total volatility
exposure of the whole portfolio** (the source's illustrative figure is 15% of capital), with the
sanity check *"if every position goes against you, then you stand to lose 75k on 5L on a single day —
how does that feel?"*
```
Σ_open_positions ( qty × ATR(20) )  ≤  V_pct × equity
```

**I checked whether this is already enforced, and it is not.** `keel/execution/guards.py` rail 4 is
commented *"sum of at-risk capital across all open positions"* but the code compares
`total_exposure + intent.notional` against `max_exposure_usd` — **it caps notional dollars and is
volatility-blind.** Rail 6 (per-asset concentration) is likewise a notional fraction. So the aggregate
quantity this source caps is not capped anywhere.

⚠️ **But be precise about when it actually bites, because most of the time it does not.** With one
tranche per asset, 1%-risk sizing and a 2N stop, each position's ATR-exposure is pinned near 1% of
equity and three assets bound the aggregate near 3% — the per-trade rail bounds the portfolio for
free, and this rail would never fire. **It becomes load-bearing exactly when the two queued
deployment levers land:** §75.1's concurrent-slot lift (multiple tranches per asset — already legal on
the live path, still capped at `portfolio_sim.py:600` in the harness) and §26.1/§54.19 pyramiding.
Both multiply position count while leaving per-trade risk untouched, and at that point nothing bounds
aggregate volatility exposure. **So: build it as a precondition of pulling those levers, not before.**

**Stdlib portability: ✅.** `qty × ATR` per open tranche, summed, compared to a `Decimal` fraction of
equity. ATR(20) already exists in `analysis/indicators.py`.

**Halal:** clean — a cap, not a hedge, and it can only reduce exposure. Note the source's own version
is stated over *futures* positions; the arithmetic is instrument-independent.

**Rail-list check:** the README hard-rails list has **no volatility-denominated aggregate cap.** Its
closest neighbours are rail 4 (notional total exposure) and rail 6 (notional per-asset) — both
different quantities. This is a genuine gap, with the narrow trigger condition stated above.

**Cross-refs:** §75.1, §26.1, §54.19, §54.7, §83.2 (supplies the measurement side).

---

### §83.4 ⚠️ Value at Risk — the exact procedure, and the verdict: **as taught, it would mislead on crypto**

Ch. 10 gives a fully specified, stdlib-computable procedure. Reproduced as steps, not prose:

1. Build the portfolio daily-return series (the §83.2 synthetic series).
2. `bin_width = (max_ret − min_ret) / 25`; build a bin array; histogram the returns.
3. **Plot it and eyeball whether it looks like a bell curve.** The source does exactly this on 126
   observations, concludes *"clearly what we see above is a bell-shaped curve, hence it is quite
   reasonable to assume that the portfolio returns are normally distributed,"* and proceeds.
4. Sort returns descending. **VaR** = the least value within the best 95% of observations (its worked
   figure: −1.48%). **CVaR** = the mean of the worst 5% (its figure: −2.39%).

**Stdlib portability: ✅ entirely.** Sort a list, index a percentile, average a tail. No distribution
fitting, no libraries. Ironically the *arithmetic* is fully non-parametric — it is historical-simulation
VaR — so the computation itself never uses the normal assumption it spends a chapter establishing.

**Verdict — asked directly, answered directly: YES, VaR as taught here would mislead on crypto.**
Five reasons, in descending order of how badly:

1. **The normality step is not a test, it is a glance at a bar chart** — and it is performed on 126
   daily observations of a 6-month Indian bull market. On crypto that check will pass routinely and
   be wrong routinely: the KB's own shock detector (§54.20) keys on a **1-day range ≥ ~5·ATR**, an
   event a fitted normal assigns essentially zero probability. A procedure whose gating step is
   "does the histogram look like a bell" is exactly the procedure a fat tail defeats.
2. **The 95% cut deliberately discards the only observations we care about.** VaR is a *threshold*
   statistic; it is silent on the shape beyond it. §54.20's whole thesis is that price shocks are the
   likeliest cause of catastrophic loss and the largest sim-to-live gap. VaR's construction removes
   them from the answer.
3. **It is a one-day, fully-invested, buy-and-hold portfolio statistic.** We hold ~21–24 days,
   intermittently, mostly in cash. Computed over our whole series it inherits §54.22's GASP problem
   and understates; computed over exposed days it answers a question a drawdown number already
   answers better.
4. **VaR is not sub-additive** — a portfolio's VaR can exceed the sum of its parts — so it is a
   *particularly* poor choice for the "aggregate risk across a correlated basket" job (open question 2)
   that it superficially looks suited to.
5. **The illustrative numbers are themselves a warning.** A −1.48% worst-case daily loss "with 95%
   confidence" for a 5-stock portfolio is a figure crypto invalidates several times a year.

**CVaR is the better half of the chapter and is still not adopted.** CVaR/expected-shortfall *is*
coherent, *is* sub-additive, and does look into the tail. Grep-checked: `CVaR` appears once in this
KB, at §33, where it is *"reviewed as another downside measure (we use drawdown/Sortino; CVaR noted,
not adopted — quant stack declined)."* Nothing here overturns that call, and I am not reopening it:
maximum drawdown and Sortino already answer "how bad does the tail get" **on the actual realised path**,
which for a 3-asset, ~2.6-trades/year/asset system with `N ≈ 23` trades is the more honest object than
a 5th-percentile estimate off a sample that thin. **Recorded, assessed, declined. No build item.**

**Cross-refs:** §54.20, §54.22, §33, §73.3, §24.3.

---

### §83.5 Kelly's Criterion — new *formula*, but not a new *decision* `strategy/money_mgmt.py`

Grep-checked before claiming novelty: `Kelly` returns exactly one hit across all 82 prior sources, and
it is the surname in the citation *"Gu, Kelly & Xiu 2018"* in §79. So the closed form is genuinely
absent:
```
Kelly% = W − [(1 − W) / R]      W = win rate;  R = avg win / avg loss
```
(worked example: W = 0.6, R = 1.384 → 31%.)

**But the decision it would inform is already made, twice over.** §54.18 holds optimal-f and states
plainly that optimal-f is *"famously too aggressive; use fractional f in practice"*; the 1% rail is the
project's fractional-f. Kelly is the same maximum-growth family reaching the same conclusion — and
this source reaches it independently and for the same reason, calling a 70% Kelly allocation *"not a
very smart thing to do… there is still a 30% chance to lose 70% of your capital."* **Reinforcement,
not a lead.** Adding a full-Kelly sizing path would violate the 1% rail and is not proposed.

**The one part with any residual value** is the author's own modification: use Kelly% not as an
allocation but as a **scaling factor on a bounded risk cap** — `risk_this_trade = Kelly% × max_risk%`,
so a 30% Kelly on a 5% cap risks 1.5%. Structurally that is **`keel`'s CTS conviction sizing (§34.4)
with a mechanically-derived multiplier instead of a heuristic A+/B/C grade** — and it is bounded by
construction, so it moves *only within* the rails exactly as §8.1/§10.6 require.
⚠️ Logged as an idea only, and a weak one: `W` and `R` would be estimated from the same ~23-trade
sample §58.11 measured as having essentially no statistical power (highest z = 0.88), so a
Kelly% computed from it is noise wearing a formula. **Do not build until `N` supports it.**

**Stdlib portability: ✅ trivial** (two divisions). **Halal:** clean — no rate, no leverage; the
gambling provenance is historical, not structural, and §65.6 settles that trading for price
appreciation is permissible regardless.

**Cross-refs:** §54.18, §34.4, §8.1, §10.6, §58.11, §73.3, §65.6.

---

### §83.6 Percentage-Risk sizing — an independent restatement of the 1% rail, verbatim `strategy/money_mgmt.py`

Ch. 14.1 derives, from scratch and without naming any prior source, exactly our sizing rule:
`qty = (max_risk% × equity) / (entry − stop)`, with the framing *"as a thumb rule, professional traders
do not risk more than 1 to 3% of their capital on any single trade."* Its worked rejection is the
useful part — a "great" setup that would have risked 6.57% of capital is refused **on the sizing rule
alone, with the setup quality never re-examined**, which is precisely the rails-bound-conviction
principle (§8.1). It then applies the ch. 12 core-equity reduction to the *next* trade's threshold,
tying §83.6 back to §83.1.

**Nothing new.** Logged because independent re-derivation of a shipped rail from a different market and
tradition is mild evidence the rail is not an artifact of one lineage. Our 1% sits at the
conservative end of its stated 1–3% band — consistent with, and stricter than, the source.
**Stdlib: ✅** (it is `sizing.py::size()` as written).

---

### §83.7 The two other Van Tharp models — assessed and rejected `strategy/money_mgmt.py`

- **Unit per fixed amount** ("1 lot per ₹100,000"). The source rejects it itself, on two grounds we
  share: it is **volatility-blind** (its own example pairs a 14%-vol index against a 40%-vol stock at
  equal weight) and it **does not scale** — position count only steps up when capital doubles.
  ⛔ Not adopted; §54.7/§27.1 ATR sizing dominates it on the first ground and §63.1's
  capital-derived-caps-that-float-with-equity on the second.
- **Percentage margin** (fix X% of capital as *margin* per trade). ⛔ **Excluded on halal grounds** —
  margin is the sizing unit, and the entire model is a leverage-budgeting device (riba, §18/§28.1).
  Its only non-margin residue is "cap the notional per trade at X% of capital", which is **rail 2**
  (per-order cap) and **rail 6** (per-asset concentration cap) already.
- **Percentage volatility** (ATR-denominated sizing: `qty = vol_budget% × equity / ATR`). ⧉ Already
  held — this is §54.7 volatility parity / §27.1 Turtle ATR sizing, with the useful footnote that
  Van Tharp specifies **ATR rather than high-minus-low** precisely to avoid ignoring gaps. N/A for
  24/7 spot (§59's gap finding), but the reasoning is sound. Its portfolio-level extension is the only
  new part and is broken out at §83.3.

---

### §83.8 ⚠️ The annualised expected-return band — a clean NEGATIVE exemplar for §6.4 `analysis/insights.py`

Ch. 7 annualises a 126-day sample mean by ×252, annualises portfolio σ by ×√252, and reports a
one-year forward return range as `E[R] ± k·σ`, arriving at *"the returns are likely to fluctuate
between +37.51% and +72.79%"* at 1σ, and **+2.23% to +108.07% at 3σ — a 99%-confidence band in which
losing money does not appear at all.**

To the author's credit this is flagged in the text (*"we are in a bull market… the numbers we have got
here is positively biased"*) — but the method is presented and taught anyway. **File it as an
exemplar, alongside §68.6 (a ~21,856× backtest with no OOS split), §73.8 (SR 1.27 from a mesh over a
pure random walk) and §58.17.** The failure is not the arithmetic; it is estimating a forward mean
from an in-sample window short enough that the regime *is* the estimate. This is the no-prediction-
oracle principle (§6.4) meeting a concrete, well-intentioned violation.

⚠️ Note the same ×252 mean-annualisation would be *far* worse on crypto than on Nifty. **Not adopted.**

---

### §83.9 ⧉ Required-gain asymmetry — exact duplicate of §54.18

Ch. 11.3's "recovery trauma" table is `required_gain = loss/(1 − loss)`: 10% loss → 11.1% gain, 60%
loss → 150% gain. §54.18 already carries this identically as
`Required gain = 1/(1−PercentLoss) − 1` (algebraically the same expression), and `analysis/pnl.py`
already ships a recovery table per the module map. **Nothing added.** The only distinct emphasis is
that recovery asymmetry bites *hardest on small accounts*, which pushes traders toward oversizing —
a behavioural observation, not a mechanism.

---

### §83.10 Systematic vs unsystematic risk at N = 3 — the arithmetic says our basket is undiversified, and we cannot fix it `analysis/regime.py`, `CompliancePolicy`

Ch. 2's diversification curve is the standard one: unsystematic (asset-specific) risk falls steeply
with holdings and flattens at roughly **~20 names**, beyond which only systematic risk remains;
systematic risk *cannot* be diversified and can only be **hedged**.

Read against our constraints this yields one uncomfortable, honest conclusion and one closed door:

- **We sit at the far-left, steepest part of that curve and cannot move right.** `|allowlist| = 3`, and
  two of the three are crypto beta. This is not a defect to fix — the allowlist is narrow *because* of
  §65/§71's admission screening, and §51 already recorded that "redundancy doesn't diversify" makes
  correlated alts a near-single exposure, so *widening* it with more crypto would add names without
  adding diversification. The correct statement of our position: **we run an essentially
  undiversified book and rely on stops, the 1% rail and the drawdown breakers rather than on
  diversification.** Worth writing down plainly, because portfolio-theory language elsewhere in the
  KB can imply otherwise.
- ⛔ **The chapter's remedy for the residual risk is hedging, which is excluded** (§4.9, §10.10, §18).
  So the one lever it offers against the risk we cannot diversify away is unavailable to us by
  construction. That is a real, accepted cost — logged, not worked around.
- PAXG is the partial exception and the reason it earns its allowlist slot: `guards.py` already
  encodes it in `UNCORRELATED_ASSETS`, exempting it from rail 5's correlated-size scaling. This
  chapter is the textbook justification for that exemption existing at all.

Everything else in ch. 2 (the Satyam single-stock blow-up; company-specific vs macro risk drivers) is
**equity-specific and has no crypto analog** — there are no earnings, no management misconduct and no
sector peers for BTC. **Not portable.**

---

### §83.11 Expectancy / breakeven arithmetic — agreement, plus one caution `strategy/promotion.py`

Kelly's two inputs are our two promotion inputs under different names: `W` = win rate, `R` = avg
win / avg loss = R:R. The source's `Kelly% > 0` condition, expanded, is
`W − (1−W)/R > 0  ⟺  W > 1/(1+R)` — **algebraically identical to the KB's breakeven-win-rate floor
`win_rate > 1/(1+R:R)`** (§23.1/§25.5/§35.2). A fourth independent derivation of an already-settled
formula: **agreement, no refinement.** It also independently endorses `R > 1` as the desirable regime
(*"a number greater than 1 is always desirable"*), consistent with the R:R ≥ 1.5–2 promotion bar.

⚠️ **One caution the source does not flag and we should.** It computes `W` and `R` from a **10-trade**
table and treats the result as actionable. That is the §58.11/§73.3 error in miniature: at our own
`N ≈ 23`, with BTC's null stdev at $2,017/trade, `W` and `R` are estimated far too noisily to drive
sizing. **The formula is fine; the sample discipline around it in this source is not.** Our promotion
gate's `min_trades: 100` is what stands between the two, and §58.11's measurement (≈68 trades needed
to clear z ≥ 2) vindicated it.

---

## Halal / declined-stack exclusions

- ⛔ **Chs. 8–9, Portfolio Optimization — Markowitz mean-variance optimisation / efficient frontier.**
  Excel Solver is used to vary weights subject to `Σw = 100%`, minimising portfolio variance and then
  tracing max/min return at fixed risk levels to draw the efficient frontier. **Declined on both
  standing grounds** and not reopened: the MPT/mean-variance direction was rejected at §33 (Islamic
  portfolio-optimisation review), §50.1 (CAPM/α/β), §54.22 (Kaufman's own MPT chapter) and §68
  (Markowitz + PSO), on riba grounds via the risk-free rate and as part of the declined quant stack
  (§73.13's warning against sweep-and-pick-the-best applies with full force to a Solver search over
  weights).
  **Salvage attempted, and it is thin.** Two observations only: (i) this presentation is unusually
  clean of riba — there is **no risk-free rate anywhere in it**, no capital market line, no tangency
  portfolio, no Sharpe maximisation; it is pure min-variance/max-return, so the *riba* objection is
  weaker here than at §50.1 while the *declined-stack* objection is untouched; (ii) it never imposes
  `w ≥ 0`, so its own frontier admits short weights — a defect for anyone, and disqualifying for us.
  **Net: nothing adopted. The only durable piece of chs. 3–9 is the ch. 6 measurement identity at
  §83.2, which is a risk *number*, not an allocator, and must not be fed to a weight search.**
- ⛔ **Hedging as the remedy for systematic risk** (ch. 2) — excluded (§4.9, §10.10, §18); see §83.10.
- ⛔ **Percentage-margin position sizing** (ch. 13.3) — margin as the sizing unit; leverage/riba
  (§18, §28.1). See §83.7.
- ⛔ **Instrument context throughout** — Nifty/stock futures, lots, lot sizes, margin blocked, and an
  options-delta answer in the comment threads. Futures and options are excluded instruments
  (gharar/maisir, not spot; §27.4, §42–§49, §65.11). The *arithmetic* was extracted regardless where
  it is instrument-independent, with the margin term reinterpreted as cash actually spent.
- ⛔ **Short positions** — ch. 11.2's motivating Nifty setup is a short (or long puts). Long-only:
  converts to a don't-buy filter, never a short entry.

---

## Discarded (no agent value)

- **Chs. 15–16, "Trading Biases" (Parts 1–2) — not opened, deliberately.** The KB's psychology stream
  was assessed saturated at §69 (*"the rails already encode, mechanically, the disciplines it
  teaches"*), with a standing recommendation to stop feeding it, after §23/§24/§26/§57 and six further
  documents in the §69 triage batch. Chapter summaries name **anchoring bias** and a WhatsApp-group
  herding video — recency/anchoring/confirmation/herding are all already covered and all already
  mechanised (rails 8/9/11/16 make averaging-down, stop-widening, revenge-sizing and streak-chasing
  *impossible* rather than discouraged). Reading them to confirm saturation a seventh time is not a
  good use of the budget. **If a future reader disagrees, the two URLs are in the chapter table.**
- **Ch. 11.1–11.2, the poker anecdote and gambler's fallacy.** Well told and genuinely useful for a
  human; **structurally impossible for this agent**, which has no memory of streaks influencing size —
  size is `(equity × risk_pct)/stop_distance` and nothing else. Its one mechanical residue, "long
  streaks do not change the next trade's odds", is already the *premise* of rail 16 (the consecutive-
  loss breaker halts entries because the streak may indicate **edge decay**, explicitly *not* because
  a reversal is due) — so the KB's use of streaks is already the correct inversion of this fallacy.
- **Ch. 1, orientation note** — front matter.
- **Ch. 6.1's opening** — the author writing from a misty mountain shack listening to Bob Marley.
  Charming; not a rule.
- **All Excel mechanics** (`=STDEV()`, `=frequency()`, ctrl+shift+enter array formulas, Solver
  invocation, ~8 downloadable .xlsx workbooks). The *procedures* were extracted where portable; the
  spreadsheet operation of them is not.
- **The Black Monday 1987 narrative and the rise-of-the-quants history** (ch. 10.1–10.2) — good
  context, no rule. §54.20 already supplies the mechanical shock treatment this history motivates.

---

## Net assessment

**Three keepers from sixteen chapters, and one of them is a caveat-heavy candidate rail.** This is a
well-written free textbook that arrives ~30 sources too late: everything in its position-sizing half
is Van Tharp, whom §54 (Kaufman) already covers deeper and with tables; everything in its
portfolio half runs toward an optimisation direction this project declined four times.

What survives:
- **§83.1** — the sizing-base question (`core` / `total` / `reduced-total` equity). Genuinely absent
  from the KB, genuinely live (`sizing.py` runs the model this source rates riskiest), and it sharpens
  §65.9's narrow rewards-credit finding into the general case. **Small but real.**
- **§83.2** — portfolio σ from a synthetic weighted equity series, no covariance matrix. The best item
  here: it makes a portfolio-level risk number computable under the stdlib-only constraint and thereby
  **closes the loop §79.8 explicitly left open** ("re-derive the constant from a portfolio-vol
  target") when nothing in the KB could compute that target.
- **§83.3** — an aggregate volatility-exposure cap. Verified against the code as **not** covered by
  rail 4 (which is notional, despite its comment) — but honestly bounded: it only bites once
  concurrent tranches (§75.1) or pyramiding (§26.1) multiply position count.

What was assessed and declined: **VaR** (§83.4 — would mislead on crypto; CVaR better but already
declined at §33), **Kelly** (§83.5 — new formula, already-made decision), **MPT** (chs. 8–9 — not
reopened).

**Stream verdict: exhausted at one source.** Zerodha Varsity's other modules are Indian-market
technical analysis, futures and options — the first is saturated many times over and the latter two
are excluded instruments. **Do not feed more Varsity.**
