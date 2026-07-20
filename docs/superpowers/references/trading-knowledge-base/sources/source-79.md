[← Knowledge Base index](../README.md)

## Source 79 — Three papers on trading FREQUENCY, stop-losses, and whether time-series momentum exists at all

**(A) Baltas & Kosowski, "Momentum Strategies in Futures Markets and Trend-Following Funds"**
(Imperial College Business School working paper, this version 14 Feb 2013, ~55pp incl. tables/appendix).
**71 futures contracts** (26 commodities, 23 equity indices, 7 currencies, 15 bond/rate), **Dec 1974 – Jan
2012**, evaluation window Jan 1978 – Jan 2012 (409 months ≈ 34 years). Full lookback × holding-period grid
(*J* × *K*) run **independently at MONTHLY, WEEKLY and DAILY rebalancing frequencies** — the exact
experiment the brief needed. Also: portfolio turnover per frequency, cross-frequency correlations, the
Yang–Zhang volatility estimator, CTA-index replication, and capacity-constraint tests.

**(B) Clare, Seaton, Smith & Thomas, "Breaking into the Blackbox: Trend Following, Stop Losses and the
Frequency of Trading — the case of the S&P500"** (*Journal of Asset Management* 14(3), 2013, pp.182–194;
accepted version, 20pp). Cass Business School / University of York. **S&P 500**, daily data Jul 1988 – Jun
2011 plus a 1952–2011 monthly extension. Three rule families (simple MA, MA crossover, **channel
breakout**) × lookbacks 10–450 days × **{daily decisions, end-of-month decisions}**, at **0.2%
transaction cost per trade**. Plus an explicit stop-loss battery and a trend-vs-fundamentals race.

**(C) Huang, Li, Wang & Zhou, "Time series momentum: Is it there?"** (*Journal of Financial Economics*
135(3), 2020, pp.774–794, 21pp). **⚠️ THE ADVERSARIAL COUNTERWEIGHT.** Re-examines Moskowitz, Ooi &
Pedersen (2012) — hereafter **MOP** — on **the same 55 futures contracts, 1985:01–2015:12**, with the OOS
window 2000:01–2015:12. Asset-by-asset regressions, pooled regressions, wild and pairs bootstraps, fixed
effects, and a novel no-predictability-required alternative strategy.

> **Why this batch was commissioned, and what it actually returned.** The project's stated priority was to
> buy **trade frequency**, because at ~2.6 trades/yr/asset the Turtle can never accumulate enough sample to
> be distinguished from random entries (§58.11, §73.3 — ~68 trades for z≥2, i.e. ~26 years). Papers (A) and
> (B) between them run the definitive frequency experiment. **The answer is that trading the same rule on
> the same asset more often buys NOTHING, and costs turnover.** That is reported plainly below (§79.1). The
> route that survives is **breadth — more horizons, more rule classes, more concurrent tranches** — and (A)
> supplies both the measurement that justifies it (§79.2) and the named construction that implements it
> (§79.3). Paper (C) then attacks the premise underneath all of it, and its scope is narrower than its
> title suggests but wider than is comfortable (§79.10–§79.14).

---

### §79.1 ⭐⭐ Does trading MORE OFTEN help? **NO.** Two independent papers, two methods, one answer `strategy/engine.py`, `strategy/backtest.py`

This is the headline, and it cuts against the project's stated priority.

**(A) Baltas & Kosowski — the frequency grid.** Their three chosen representative strategies (the "FTB"
benchmarks: monthly *M*¹₁₂, weekly *W*¹₈, daily *D*¹₁₅) over the same 34 years and the same 71 assets
(Table III, Panel A):

| | monthly *M*¹₁₂ | weekly *W*¹₈ | daily *D*¹₁₅ |
|---|---:|---:|---:|
| Annualised mean | 18.54% | 15.72% | 18.44% |
| Annualised vol | 14.88% | 12.57% | 15.25% |
| **Sharpe ratio** | **1.25** | **1.26** | **1.21** |
| Max drawdown | 22.12% | 12.03% | 15.65% |
| **Portfolio turnover** | **23.5%** | **77.1%** | **238.1%** |
| Sharpe after 2/20 fees | 0.89 | 0.88 | 0.88 |

**Read the last two rows together. The Sharpe ratio is flat across a 10× range of turnover.** Daily
rebalancing delivers *the same* risk-adjusted return as monthly while trading ten times as much. Their own
text: *"the effects hold for higher frequencies of rebalancing, **without any drop** in the mean return or
Sharpe ratio levels"* — phrased as reassurance for CTAs, but for a cost-paying agent it is the opposite.
**Equal gross edge at 10× the cost is a strictly worse trade.** And their paper explicitly excludes
transaction costs (§79.4), so even this flat comparison flatters the high-frequency arm.

**(B) Clare et al. — the direct daily-vs-monthly test, WITH costs.** Same rules, same data, same 0.2%
per-trade cost, decisions taken either every day (their Table 1) or only at each month-end (Table 2). Best
Sharpe in each family:

| Rule family | best DAILY decisions | best END-OF-MONTH decisions |
|---|---:|---:|
| Simple moving average | 0.54 (400d) | **0.59** (450d) |
| MA crossover | 0.56 (150/300) | **0.58** (100/250) |
| **Channel breakout (our family)** | 0.59 (250d) | **0.62** (250d) |
| Buy-and-hold | 0.31 | 0.31 |

**Monthly wins in all three families, including ours.** Their stated conclusion, first in a numbered list:
*"i) there is no advantage in trading daily rather than monthly."* And in the abstract: *"monthly end of
month investment decision rules are superior to those which trade more frequently: this adds to the growing
view that **trading can damage your wealth**."*

The same paper also shows a **1952–2011 robustness leg** (Table 3): a 12-month MA computed from
**end-of-month prices only** achieves Sharpe 0.58 — *"there is no benefit in calculating an average based
on daily data: the end-of-month suffices."* Even the *input* data can be coarsened without loss.

⇒ **The plan to solve under-deployment by making the existing rule fire more often on the existing three
assets is misconceived.** Nothing in either paper supports it; both papers' explicit conclusions oppose it.
The epistemics problem (§58.11/§73.3) must be attacked through **breadth**, not through per-asset
frequency. §79.2 and §79.3 are the constructive half.

⚠️ **Honest scope limit on this finding.** Both papers vary the **decision/rebalancing cadence** of an
*already-firing* rule. Neither tests "add a second, uncorrelated rule class" — which is a different
intervention and is *not* refuted here (§74.5/§58.10c `macd_divergence` survives untouched). What is
refuted is *checking the same signal more often* and *shortening the same signal's lookback*.

### §79.2 ⭐⭐ Where statistical power comes from: HORIZON breadth — cross-frequency correlation is only **0.22** `strategy/rules/`, `strategy/promotion.py`

This is the most valuable constructive result in the batch, and it lands directly on the 3-asset problem.

(A) Table III, Panel B — unconditional correlations between the nine representative strategies (all built
from the *same* 71 futures, differing only in lookback *J*, holding period *K*, and rebalancing frequency):

| | *M*¹₁₂ (monthly) | *W*¹₈ (weekly) | *D*¹₁₅ (daily) |
|---|---:|---:|---:|
| *M*¹₁₂ | 1.00 | | |
| *W*¹₈ | **0.41** | 1.00 | |
| *D*¹₁₅ | **0.22** | **0.52** | 1.00 |

Their reading: *"strategies with different rebalancing frequencies are not strongly correlated with each
other, which means that they **capture different empirical features of the data**… Clearly, both short-term
and long-term momentum features exist in the time-series of the dataset, but these phenomena appear to be
**distinct from each other**."* Corroborated independently by the factor decomposition (Table IV): the
monthly strategy loads on UMD and negatively on the bond PTF factor; the weekly on FX and stock PTF
factors; the daily on bond and stock PTF factors — **different risk exposures, not one effect resampled.**

**Why this matters more than it looks.** §73.3 says the promotion gate needs roughly **68 approximately
independent trades** for z ≥ 2. §75.1 established that ranking cannot produce signals on a 3-asset
allowlist, and warned that extra trades from the *same* rule on the *same* asset are **correlated** and
therefore help deployment but not knowability. §74.12 then showed that MOM and MA rules are **not**
independent families — their similarity *increases* with trend strength. So the KB had a problem: every
proposed source of extra trades was correlated with the ones we already have.

**§79.2 is the first measured, external counter-example.** The *same* rule family, on the *same* assets, at
**different horizons**, produces streams correlated at only **0.22**. Horizon is a genuine diversification
axis where rule-family was not. And unlike asset breadth, **horizon breadth is available to us** — the
halal allowlist caps assets at 3, but it places no cap on lookbacks.

⇒ **Concrete recommendation: run the Turtle as a small ladder of horizons (e.g. a fast, a medium and a slow
`entry_lookback`) rather than as one point estimate.** This (a) multiplies trades per asset without
shortening any individual signal — the thing §79.1 refutes; (b) produces *near-independent* trades, which
is what §73.3's arithmetic actually requires; and (c) is the empirical form of §54.10's
**robustness-plateau** criterion — you *trade* the plateau instead of point-selecting inside it, which
§74.9 argued for on the grounds that [A] and [B] there disagreed on the right lookback.

⚠️ Two caveats kept in view. **(1)** The 0.22 is a *portfolio-level* correlation across 71 assets; on 3
correlated crypto assets the between-horizon correlation will be materially higher and **must be measured
before the independence is claimed** — exactly the discipline §74.12 demanded of `macd_divergence`. **(2)**
A horizon ladder multiplies parameters and therefore `N` (§73.5); it is only affordable if the horizons are
fixed **`a_priori`** (§73.12) from §54.13/§58.6/§74.2/§79.5 rather than swept.

### §79.3 ⭐⭐ The overlapping-portfolio construction — the named, standard mechanism for §75.1's blocked half `keel/sim/portfolio_sim.py`, `strategy/money_mgmt.py`

(A), §4.2: *"Instead of forming a new momentum portfolio every K periods, when the previous portfolio is
unwound, we follow the **overlapping methodology of Jegadeesh and Titman (2001)** and rebalance the
portfolio at the end of each month/week/day. The respective return is then constructed as the
**equally-weighted average across the K active portfolios** during the period of interest. In other words,
**1/K-th of the portfolio is only rebalanced every month/week/day.**"* Their worked example: with *K* = 3,
at end-January the Jan–Feb–Mar portfolio has just been built, the Dec–Jan–Feb one has a month left, the
Nov–Dec–Jan one is unwound and replaced; the January return is the equal-weighted average of the three.

**This is precisely the structure §75.1 identified as blocked in our harness.** §75.1's orchestrator
correction split §60.2 into (a) *ranking* — a no-op at `|allowlist| = 3` — and (b) *keeping N slots filled
concurrently*, which is **not** a no-op but is **untestable**, because `keel/sim/portfolio_sim.py:600`
still enforces `# only one RULE position per asset at a time` while the live executor (PR #96, per-tranche
`positions` table) can already hold several.

§79.3 supplies three things the KB did not have:
1. **A name and a citation.** This is not a bespoke idea; it is the standard portfolio construction in the
   momentum literature (Jegadeesh & Titman 2001), used here across 34 years and 71 assets.
2. **A capital rule.** Deploy **1/K** of the sleeve per period and hold *K* overlapping tranches, rather
   than all-in/all-out on one signal. That converts the Turtle from a lumpy binary exposure into a smooth
   one, and it is the mechanism by which a slow signal can still produce a steady stream of decisions.
3. **A reason it is the right fix and a horizon ladder is the complement.** Overlapping tranches of one
   horizon are highly correlated (they are the same signal, staggered) ⇒ **they buy DEPLOYMENT, exactly as
   §75.1 warned.** Tranches across *different* horizons (§79.2) buy **KNOWABILITY.** Build both, but do not
   confuse them: only the second moves the z-statistic.

⇒ **Action (unchanged in priority, now externally grounded): lift the sim's one-position-per-asset cap to
match the live executor, then re-run the S1+S2 ensemble** — which §75.1 showed was rejected on an artifact
of line 600, not on its merits. Touches no rails.

### §79.4 ⚠️ Turnover is the hidden variable, and neither paper's cost model is ours `sim/account.py`, `strategy/backtest.py`

(A) states plainly: *"for simplicity, we do not incorporate transaction costs into the momentum strategies
that we study"*, justified by futures being liquid and cheap. Turnover, however, **is** reported (§79.1
table): 23.5% monthly / 77.1% weekly / 238.1% daily — *"the turnover of daily strategies is approximately
**one order of magnitude larger** than that of monthly strategies."*

(B) **does** charge costs — 0.2% per trade — and it is under that charge that monthly beats daily. It is
also (B) that produces the sharpest cost-related result: *"short-term signals give far worse returns than
the longer signals, basically because **overtrading detracts from performance**."*

**Our cost is worse than either.** §74's assessment used ~0.6% round-trip on Coinbase — 3× Clare's
per-trade charge, and infinitely more than (A)'s zero. The KB's own §58.1 (limit entry at the breakout
bar's midpoint, maker rather than taker) is the standing mitigation and is reinforced here: at high
turnover the maker/taker spread stops being a refinement and becomes the whole margin.

⇒ **Any frequency proposal must be evaluated net, at our real fee schedule, with turnover reported as a
first-class sim metric.** Grep-verified: `turnover` appears nowhere in the sim modules as a reported
statistic — it is discussed in the KB (§28.3, §63, §64, §65) only as a *compliance* notion (and §65.6
demoted even that). **Reporting per-rule annual turnover in the sim artifact is a cheap, un-swept
addition** and is the metric that would have made §79.1's trade-off visible on our own data.

### §79.5 ⭐ The lookback has a CEILING — this BOUNDS the §74.2 "sweep longer" recommendation `strategy/rules/`

§74.2 is currently flagged as *"the most actionable item in the KB"*: three sources said our
`entry_lookback = 40` is too short and pointed at 80–95 (§58.6) and 150–200 (§74.2). **§79.5 is the first
source that says where to stop.**

(A) Table II, Panel A (monthly, Sharpe ratio by lookback *J*, holding *K* = 1 month):

| *J* (months) | 1 | 3 | 6 | 9 | 12 | 24 | 36 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Sharpe | 0.92 | 0.97 | 0.89 | 1.13 | **1.25** | 0.78 | 0.60 |

The peak is at **12 months (~252 trading days)** and performance **falls off sharply beyond it** — 24 and
36-month lookbacks are materially worse than a 1-month lookback. (A) notes the corresponding regression
evidence: monthly *t*-statistics are positive and significant through the first 12 lags, then *"there are
relatively weak signs of return reversals and all lags up to 60 months fail to document any other
significant effect."*

(B) independently agrees at the far end: its breakout Sharpes rise to 0.59/0.62 at 250 days and then
**decline** at 300/350/400/450 (0.54, 0.46, 0.45, 0.43 daily; 0.53, 0.44, 0.44, 0.45 monthly).

⇒ **The evidence describes a PLATEAU roughly spanning 150–250 trading days, with degradation on both
sides.** Combined with §58.6 (80–95), §74.2 (150–200 significant, 50 not) and our own 20 → 40 walk-forward,
the consistent picture is: **our 40 is on the short flank of the plateau; the plateau's far edge is around
250 days; beyond ~1 year the effect reverses.** This makes the §74.2 sweep *finite and cheap* — and, per
§73.12, makes a value inside the plateau defensible as **`a_priori`** (five sources now agree on the
region), which costs no trials budget, rather than as a *fitted* parameter we cannot afford (`N ≤ 3`,
§73.3).

⚠️ Do **not** read this as endorsing a point value. §54.10/§74.9 both say report the plateau, not the peak.
§79.5's contribution is the **upper bound**, which the KB previously lacked entirely.

### §79.6 ⭐⭐ NEW: a SHORTER exit channel than the entry channel is a MISTAKE — our 40/20 asymmetry is on the wrong side `strategy/rules/turtle_breakout.py`

Grep-verified as new: `donchian_exit` returns nothing; `exit channel` appears only in §27 (the canonical
Turtle spec, asserted not tested) and `exit_lookback` only in §27 and §73. **No source in the KB has ever
tested the entry/exit channel-length ratio.** Our code carries
`exit_lookback: int = 20,  # Donchian-low asymmetric exit (days); half the entry (was 10)`.

(B), Table 4 Panel A, tests exactly this — a breakout entry of length *X* with a **shorter** breakout exit
of length *Y* ("Opening/Closing Breakouts", *X*/*Y*), on daily S&P 500 data with costs:

| Entry/Exit | 50/10 | 100/25 | 150/50 | 200/50 | 200/100 | 250/100 | 250/150 | **250/200** |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Sharpe | −0.19 | −0.08 | 0.15 | 0.14 | 0.33 | 0.29 | 0.39 | **0.52** |

and the symmetric 250-day breakout with no shortened exit (Table 1, Panel D) scores **0.59**.

The pattern is **monotone in the exit/entry ratio**: 200/50 (ratio 0.25) → 0.14; 250/100 (0.4) → 0.29;
250/150 (0.6) → 0.39; 250/200 (0.8) → 0.52; 250/250 (1.0) → 0.59. Their own text, understated:
*"Typically the stop loss rule on the downside is a shorter signal. **Interestingly the longer signals
reveal higher returns and Sharpe ratios.**"*

⇒ **Our 40/20 sits at ratio 0.50, in the middle of the losing region of this curve.** This is a concrete,
cheap, previously-untested sweep dimension that is *independent* of the entry-lookback question §74.2
already owns — and it points the same direction (longer). It also has a second, free benefit for this
project: **a longer exit channel lengthens the average hold, which reduces round-trips and therefore fees**
(§79.4), rather than trading them away.

⚠️ Caveats. This is one index, one market regime, one paper; the Turtle's asymmetric exit is canonical
(§54.14, Richard Dennis's own spec) and has a rationale we should not discard on a single table — a shorter
exit protects the fat-tailed downside a long channel would ride through. And a longer exit channel widens
the *effective* stop, which interacts with §58.12 (§79.7). **Sweep `exit_lookback` jointly with
`entry_lookback` on the ratio, do not point-move it** — and count it against the trials budget.

### §79.7 ⭐ Stop-losses: (B)'s exact test, and why it does **NOT** actually contradict §58.12 `execution/executor.py`

The brief flagged this as a live disagreement. Having read (B)'s actual battery, **it is not a
contradiction — the two experiments answer different questions**, and reconciling them yields a sharper
rule than either alone.

**What (B) tested.** Three stop families layered on a *trend-following rule that already has a trend exit*:
1. **Breakout stop-loss** — exit on a shorter-channel break (Table 4 Panel A; this is §79.6's table).
2. **Percentage trailing stop** on a 200-day breakout entry (Table 4 Panel B), stop at 3–15% below entry.
3. **"Purchase cost" stop** (Table 5) — sell when the return falls **5 standard deviations below the
   initial purchase price**, the most active of the Lei & Li (2009) rules.

**Family 2 is the one that matters to us.** Annualised Sharpe by stop width, against an unstopped 200-day
breakout baseline of **0.56** (Table 1, Panel D):

| Stop width | 3% | 5% | 7% | 10% | **12%** | 15% | *none* |
|---|---:|---:|---:|---:|---:|---:|---:|
| Sharpe | −0.11 | 0.08 | 0.31 | 0.52 | **0.54** | 0.49 | **0.56** |
| Ann. return | 3.02 | 4.47 | 6.82 | 9.52 | **10.13** | 9.61 | 10.61 |

Their conclusion: *"In both cases stop-loss rules would seem to make performance worse… **simple
trend-following rules are still better than introducing stop-losses: a change of trend is the best stop
loss.**"* And on family 3: *"The results in Table 5 show that the rule has **no beneficial impact**."*

**Now read that curve carefully — it has an INTERIOR MAXIMUM at 12%.** Too tight (3%) is catastrophic; it
improves monotonically to 12%; it *falls back* at 15%. **That is §58.12's shape exactly** (Katz &
McCormick: ARRR −2.54 at 0.5·ATR, best −1.46 at 1.5·ATR, worse again at 3.5·ATR — *"too wide a stop
increases the percentage of wins… too tight a stop… drastically cuts the percentage of winning trades"*).

**The reconciliation, which is clean:**
- **§58.12 tested stops on RANDOM entries with a profit target and no trend exit.** There, the stop is the
  *only* risk-management mechanism, so an interior optimum in stop width is the whole result. (Note every
  cell in §58.12's table is still negative — 1.5·ATR is *least bad*, not good.)
- **§79.7 tested stops layered on top of a 200-day channel exit that is already doing that job.** There,
  the stop is *redundant* with a better mechanism, and the best it can do is approach — never beat — the
  unstopped baseline.

⇒ **The synthesis: stop width has an interior optimum (§58.12 stands), AND a well-chosen trend exit
dominates any stop (§79.7 stands). Both are true because they are answers to different questions.**

**What this means for our ATR 2N stop and the queued stop-width sweep:**
- ⚠️ **Neither paper tested a volatility-scaled stop.** (B)'s family 2 is a **fixed percentage** of entry
  price — *not* ATR-scaled, not trailing in the §54.6/§58.13b sense, and calibrated on an equity index
  whose volatility is a fraction of BTC's. **Our ATR 2N stop is untouched by this evidence.** Do not
  retire it on the strength of (B).
- ⭐ **The queued stop-width sweep should be re-scoped.** The valuable question is no longer "what stop
  width?" in isolation but **"does the ATR stop add anything over the Donchian-20 exit alone?"** — i.e.
  run a **stop-off arm**. That is a single ablation, costs 1 trial, is exactly the §58.0 component-isolation
  discipline, and is the question both this paper and §58.12, read together, actually pose.
- ⭐ **§79.6 and §79.7 are the same finding seen twice.** (B)'s "breakout stop-loss" family *is* our
  asymmetric exit channel, and lengthening it toward the entry channel was what improved it. "A change of
  trend is the best stop loss" and "lengthen the exit channel" are one recommendation.

### §79.8 ⭐ Position sizing / volatility scaling — the explicit formulae, with stdlib portability `strategy/money_mgmt.py`

(A), eq. (3), the aggregate time-series momentum portfolio:

```
R_J^K(t, t+K) = (1/N_t) · Σ_{i=1..N_t}  sign[R_i(t−J, t)] · (40% / σ_i(t;60)) · R_i(t, t+K)
```

Three separable components, each independently portable:

1. **Signal**: `sign[R_i(t−J,t)]` — a **binary** long/short on the sign of the *J*-period past return.
   ⛔ Long-only: the `−1` branch collapses to **0 (flat / don't-buy)**, never a short. §73's `Side` mesh
   dimension is already `{+1}` for us; this is the same collapse in a different paper.
2. **Inverse-volatility position scaling**: `40% / σ_i(t;60)` — target an **ex-ante annualised volatility
   of 40% per instrument**, with σ estimated on a **rolling 60-trading-day window**. This is §54.7's
   volatility-parity sizing with concrete constants supplied. The 40% is calibrated to produce ~12–15%
   *portfolio* volatility after the 1/N aggregation; (A) verifies the realised result: 14.88% / 12.57% /
   15.25% ex-post for the three frequencies. ⚠️ **The 40% is not portable as a number** — it is set so that
   the *portfolio* lands near 12–15% given 71 assets. On 3 crypto assets the same target would be wildly
   over-sized. **Port the mechanism, re-derive the constant from a portfolio-vol target.**
3. **Equal-risk aggregation**: the `1/N_t` prefix — equal *risk*, not equal *dollars*, across the admitted
   basket. Identical in spirit to §54.22's equal-risk-by-ATR allocation and to §58.4's "trade the whole
   admitted basket without selection."

**Stdlib portability (project has declined NumPy/Pandas/SciPy; stdlib + `Decimal` only):**

| Formula | Portable? | Notes |
|---|---|---|
| `sign(past return)` | ✅ trivial | one `Decimal` comparison |
| `40% / σ(t;60)` sizing | ✅ | one division; σ from §79.9 or from existing ATR |
| `1/N` equal-risk aggregation | ✅ | arithmetic mean over ≤3 assets |
| 60-day rolling σ | ✅ | running sums over a `deque`; O(1) per bar |
| Yang–Zhang σ (§79.9) | ✅ | needs `math.log` only; see below |
| Carhart-4 / Fung–Hsieh factor regressions | ❌ **and not wanted** | needs OLS + external factor data; the declined MPT/CAPM direction (§33, §50.1, §54.22) and riba-bearing via `RF` |
| Newey–West / two-way clustered SEs | ❌ | would need a matrix library; and per §73.8 we should **not** be emitting per-config p-values anyway |

⚠️ (A) uses `1/K`-overlapping weights (§79.3) rather than all-in — note that **the sizing formula and the
overlapping construction are separable**; adopt either without the other.

### §79.9 ⭐⭐ NEW: the Yang–Zhang volatility estimator — ~8× more statistically efficient than close-to-close σ, from OHLC we already store `analysis/indicators.py`

Grep-verified new: `Yang`, `Rogers`, `Satchell`, `Parkinson`, `Garman`, `range estimator`,
`estimator efficiency` all return **zero hits** across the entire KB. The KB's volatility toolkit is
ATR (§54.2), annualised stdev and relative vol (§54.2) — no *range-based* estimator and no discussion of
estimator **efficiency** anywhere.

(A) uses the **Yang & Zhang (2000)** estimator throughout, described as *"the first-in-literature unbiased
volatility estimator that is independent of both the opening jump and the drift of the underlying price
process"* and, citing Shu & Zhang (2006) and their own prior work, *"the most efficient volatility
estimator within a pool of range estimators."*

Full specification (Appendix A). With *O, H, L, C* the daily **log**-prices of day *t*, and *D* past days:

```
o(t) = O(t) − C(t−1)        # overnight jump
c(t) = C(t) − O(t)
h(t) = H(t) − O(t)
l(t) = L(t) − O(t)
r(t) = C(t) − C(t−1)        # close-to-close

σ²_YZ(t;D) = σ²_OPEN(t;D) + k·σ²_STDEV(t;D) + (1−k)·σ²_RS(t;D)

  σ²_STDEV(t;D) = (261/D) · Σ_{i=0..D−1} [ r(t−i) − r̄(t) ]²
  σ²_OPEN (t;D) = (261/D) · Σ_{i=0..D−1} [ o(t−i) − ō(t) ]²
  σ²_RS   (t;D) = (261/D) · Σ_{i=0..D−1} [ h(t)·(h(t) − c(t)) + l(t)·(l(t) − c(t)) ]

  k = 0.34 / ( 1.34 + (D+1)/(D−1) )
```
where `r̄`, `ō` are the *D*-window means and **261** is their trading-days-per-year constant.

**The efficiency claim, and why it is the most valuable line in this batch for us.** (A): *"Yang and Zhang
(2000) show that their estimator is **1 + 1/k** times more efficient than the ordinary STDEV estimator.
Throughout the paper we use D = 60, hence the YZ estimator is **almost 8 times more efficient**."*
(Check: *D* = 60 ⇒ `k = 0.34/(1.34 + 61/59) = 0.34/2.374 ≈ 0.1432` ⇒ `1 + 1/k ≈ 7.98`.)

⇒ **This attacks the epistemics problem (§58.11/§73.3) from a completely different direction than
frequency or breadth: it extracts ~8× more information about volatility from the SAME data.** Every
volatility-dependent quantity in this system — ATR-based position sizing (§54.7/§54.14), the 2N stop, the
`atr_period=20` a_priori parameter (§73.12), the correlation-adjusted sizing rail, the price-shock detector
(§54.20) — is currently driven by a close-to-close or true-range estimate. A materially less noisy σ makes
all of them less noisy, at zero cost in trials budget, on data we already have.

**Halal / practicality notes.** No riba, no shorting, no derivatives anywhere in the estimator — it is pure
descriptive statistics on OHLC. **Fully stdlib-portable**: `math.log` plus running sums; no matrix
algebra, no special functions. ⚠️ One crypto adaptation: `σ²_OPEN` measures the **overnight gap**, and
**24/7 spot has no overnight session** — for a continuous daily-bar series `O(t) ≈ C(t−1)` so `o(t) ≈ 0`
and that term degenerates toward zero, leaving `k·STDEV + (1−k)·RS`. That is not a defect (the estimator
remains well-defined and is still ~unbiased under the Rogers–Satchell component), but the **8× efficiency
figure is derived for gapping markets and must be re-verified empirically on our own series before it is
claimed.** Cheap to check: compare rolling-window estimator variance against close-to-close on our stored
candles. ⭐ **Recommended as a queued `analysis/indicators.py` addition, ranking alongside §74.2 and
§79.6 — and unlike those it is a pure estimation improvement, not a parameter choice, so it does not
increment `N`.**

---

## The adversarial check — Huang, Li, Wang & Zhou (2020)

### §79.10 ⚠️⚠️ What Huang et al. actually tested — be precise about the specification `strategy/promotion.py`

The paper tests **one thing**: whether the **past 12-month return predicts the next one-month return**, in
the MOP regression specification, on 55 futures contracts. Formally (their eqs. 1, 3, 5):

```
(1)  r^i_{t+1}            = α + β · r^i_{t−12,t}                    + ε          # raw
(3)  r^i_{t+1}/σ^i_t      = α + β · r^i_{t−h+1,t}/σ^i_{t−h}         + ε          # volatility-scaled
(5)  r^i_{t+1}/σ^i_t      = α + β · sign(r^i_{t−h+1,t})             + ε          # sign specification
```

with `σ^i_t` an exponentially-weighted daily-return volatility (δ chosen so Σ(1−δ)δ^j·j = 60 days).

**It is a monthly return-sign regression. It is not a test of channel breakouts, moving averages, ADX
gates, stops, or any price-level rule.** That distinction is load-bearing for §79.12.

**The four results, in order of severity:**

**1. Asset-by-asset, in sample (Table 2, Fig. 1A).** Of 55 assets, only **8** have a significant regression
slope at the 10% level (three at 5%); average **R² = 0.39%**; 17 assets have *negative* slopes; only 5
assets exceed R² = 1%. At the 10% level, **47 of 55 assets have a t-statistic below 1.65.**

**2. Asset-by-asset, out of sample (Table 2, Fig. 1B).** Training on 1985–1999, testing 2000–2015 with the
Campbell–Thompson `R²_OS`: **45 of 55 assets have a NEGATIVE `R²_OS`** — the in-sample-fitted model
forecasts *worse than the historical mean*. Only 3 are significantly positive. **Average `R²_OS` = −0.67%.**
Robust to 1-, 3-, 6-month horizons and to removing volatility scaling.

**3. The pooled t-statistic is an artifact of pooling (Tables 3–8).** They replicate MOP's pooled
*t* = 4.34 — then show it is not significant:
- **Fixed effects.** The 55 assets emphatically do *not* share a common mean or Sharpe (ANOVA p = 0.08,
  Welch p < 10⁻³, Kruskal–Wallis p < 10⁻¹⁰, bootstrap p = 0; for Sharpe, all p < 10⁻⁵). Pooling without
  fixed effects biases β upward by exactly `Cov(r/σ, μ/σ)/Var(r/σ)` (their eq. 7) — positive whenever
  realised returns correlate with their own means, i.e. always. Controlling for fixed effects drops
  *t* from **4.34 → 3.37** (Table 8).
- **Bootstrap.** Because the regressor is persistent and heteroskedastic, the correct 97.5% critical value
  is not 1.96. Wild and pairs bootstraps (1,000 replications each) give critical values of **12.53 and
  4.83** at *h* = 12 — *"They are larger than 4.34, the t-statistic from the pooled regression with real
  data… Hence, a high t-statistic found by MOP is **not statistically significant** in supporting the
  existence of TSM."* Robust across all four asset classes (Table 5), without volatility scaling (Table 6),
  and on the pre-2009 MOP window (Table 7).
- **Volatility scaling is doing real work.** Without it, the pooled *t* falls from 4.34 to **1.68**
  (Table 6) — *"it seems at least partially responsible for the performance of the TSM trading strategy."*

**4. ⭐⭐ The economic result — TSM vs "TSH", and this is the paper's actual punch (§79.11).**

### §79.11 ⚠️⚠️ The TSH test: a strategy needing ZERO predictability performs the same as TSM `strategy/promotion.py`, `sim/benchmark.py`

Huang et al. construct **TSH — "time series history"**:

```
TSM :  r^TSM_{t+1,i} = sign( r^i_{t−12,t} ) · r^i_{t+1}     # buy if the past 12-month return ≥ 0
TSH :  r^TSH_{t+1,i} = sign( r^i_{1,t}    ) · r^i_{t+1}     # buy if the HISTORICAL SAMPLE MEAN ≥ 0
```

TSH uses **no predictability whatsoever** — only the asset's full-sample-to-date average return. It is
profitable for a trivial reason they state explicitly: without predictability, `Pr(r_{t−12,t} > 0) =
Φ(√12·μ/σ)`, so *"the TSM strategy tends to buy an asset with high mean return (i.e., Sharpe ratio)"* —
i.e. TSM is, mechanically, a mean-return tilt wearing a momentum costume.

**Results:**
- **Asset level (Table 9):** of 55 assets, only 5 show TSM beating TSH on mean return; on Sharpe, the count
  of significant differences is **7 of 55 in each direction** — about what 10%-level testing yields by
  chance.
- **Portfolio level (Table 10):** under equal weighting, mean difference **0.14% p.a., p = 0.19**; alpha
  differences **0.10% (p = 0.29)** against Fama–French-4 and **−0.02% (p = 0.84)** against
  Asness–Moskowitz–Pedersen-3. *"The alpha differential between the TSM and TSH strategies is always
  indifferent from zero."* Robust across four weighting schemes (equal, volatility, past-12m-return,
  zero-investment).
- **Predictive-slope test (Table 11, following Lewellen 2015):** regressing realised returns on TSM
  forecasts gives a slope of **0.19** (t = 0.61) — a perfect forecast would give 1.0, and *"a value less
  than 0.5 indicates no predictability."* Regressing TSM forecasts on **TSH** forecasts gives a slope of
  **1.09 (t = 18.56), R² = 40%** — the two forecasts are the same object.
- **Calibration (Fig. 5):** simulating with a known slope β, TSM ≈ TSH at β = 0.1 (p = 0.93) and β = 0.2
  (p = 0.57), and TSM **dominates** at β = 0.4 (p = 0). The estimated real-data slope is **β̂ = 0.08.**
  *"the advantage of the TSM strategy is not apparent as long as the slope is small."*

⇒ **Conclusion: TSM's profits are real but are attributable to cross-asset differences in MEAN RETURN, not
to time-series predictability.**

**⭐⭐ The transfer to this project is direct and uncomfortable, and it is the single most actionable item
in paper (C).** Ported to a long-only 3-asset crypto allowlist over 2021–2026, TSH degenerates to:
*"BTC's historical mean return is positive, therefore hold BTC."* **That is buy-and-hold.** Huang's argument
therefore becomes: *your trend rule must beat holding the asset, not merely be profitable.*

- ✅ **§74.1 already used exactly this benchmark** — every number quoted there is Δ *vs buy-and-hold*, and
  the Donchian family was the only one clearing it with significance. §79.11 supplies the theoretical
  reason that is the right benchmark.
- ⚠️ **Our own harness does not enforce it.** `keel/sim/benchmark.py` exists but implements **DCA
  accumulation** benchmarks (`dca_into_allowlist`, `dca_into_btc`) which *"never sell — pure
  accumulation"* and contribute monthly. That is a *savings-plan* comparator, not a same-capital
  **hold-from-t₀** comparator, and the two can differ by a great deal in a trending market.
- ⇒ **Recommendation: add a same-capital buy-and-hold arm to the promotion gate alongside §58.11's
  random-entry control.** §73.11 already frames §58.11 (a null over the *data*) and §73.2 (a null over the
  *researcher's search*) as complementary. **§79.11 identifies a third null we do not run: a null over the
  ALTERNATIVE — "could I have done this well by simply holding?"** For a long-only spot agent that *can*
  hold, this is arguably the most economically meaningful of the three, and it is nearly free to compute.

### §79.12 ⭐⭐ What Huang et al. does and does NOT undercut — the precise scope `strategy/rules/`

The brief asked for exactness here. Taking the paper on its own terms and its own stated limits:

**IT DOES UNDERCUT:**
1. **The MOP claim that "TSM is everywhere."** In their words: *"a lack of empirical evidence exists to
   support that the TSM is everywhere."*
2. **The specific 12-month-return-sign predictive regression**, in and out of sample, asset by asset and
   pooled — including the versions with and without volatility scaling, with and without fixed effects.
3. **The attribution of trend-following profits to time-series predictability.** This is the deepest cut:
   the profits survive, the *explanation* does not.
4. **Any inference drawn from a POOLED t-statistic across heterogeneous assets without fixed effects.**
   ⚠️ **This one lands on us.** Grep-verified: `keel/sim/report.py:104` defines `POOLED_KEY =
   "__pooled__"`, and the pooled entry is `summarize()` over *"every rule's trades concatenated"* — BTC,
   ETH and PAXG, three assets with radically different mean returns and volatilities, stacked with no
   fixed-effects control. §73.3's own SR figure is quoted with a pooled cross-check (*"pooled figures give
   0.380 independently"*), and the 2026-07-20 ablation reports pooled PF and expectancy. **Per Huang's
   eq. (7) those pooled statistics are biased UPWARD, and the bias grows with cross-asset heterogeneity —
   which on BTC/ETH/PAXG is extreme.** This does not overturn the ablation's *direction* (gate-ON also won
   per-asset — removing the gate turned ETH outright negative), but it means **the pooled numbers should
   be reported as demeaned/per-asset, never as the headline**, and any future significance claim built on
   the pooled sample is overstated by an unknown positive amount.

**IT DOES NOT UNDERCUT:**
1. **Trend following in general.** It never tests a moving average, a channel breakout, a Donchian rule,
   an ADX gate, a stop, or any price-level rule. It tests one monthly return-sign regression.
2. **Predictability as such.** Their own final remark, stated unprompted: *"our results do not claim in any
   way that there is no predictability in the asset classes, but that the predictability, if it exists,
   **is not as simple as a constant 12-month return rule**."* They explicitly point to richer predictor
   sets and to machine-learning work (Gu, Kelly & Xiu 2018; Freyberger et al. 2019) as finding *stronger*
   predictability, while noting *"none of them is related to TSM."*
3. **Cross-sectional momentum**, which they distinguish throughout and do not test.
4. **Crypto.** 55 futures, 1985–2015. **Zero crypto exposure, ending six years before our window opens.**
   §74's crypto-specific breakout evidence (§74.1, §74.7, §74.8) is untouched by this paper.
5. **The profitability of the strategy.** From the abstract: *"From an investment perspective, **the TSM
   strategy is profitable**, but its performance is virtually the same as that of a similar strategy that
   is based on historical sample mean and does not require predictability."*
6. **The methodology when the signal is genuinely strong.** Fig. 5, Panel C: at β = 0.4, TSM beats TSH
   *"in almost all the simulated data sets"* (p = 0). The test has power; it is the futures data that is
   weak.
7. **The daily/short-horizon effects Baltas & Kosowski document.** Huang tests one horizon (12-month
   lookback, 1-month hold) at one frequency (monthly). See §79.14.

⇒ **The honest one-line verdict: Huang et al. is a devastating refutation of a specific monthly regression
and of the "everywhere" claim; it is NOT a refutation of trend following, and it is silent on crypto and on
channel breakouts. But its methodological lesson — that pooled statistics over heterogeneous assets
over-reject, and that a profitable strategy may owe its profit to something other than the effect it claims
— transfers to us completely, and one of its two halves lands on our own harness (§79.12 item 4).**

### §79.13 ⚠️⚠️ Huang's asset-by-asset failure IS the KB's epistemics problem, published in a top journal `strategy/promotion.py`

This deserves separating out, because it re-frames a result the KB currently treats as a local
embarrassment.

§58.11/§73.3 established that our Turtle cannot clear z ≥ 2 on 5 years of BTC/ETH data — the highest
observed z was 0.88, and ~68 trades (~26 years) would be needed. That was recorded as *our* problem, a
consequence of a thin allowlist and a slow rule.

**§79.13 shows it is the field's problem.** Huang et al. have **31 years of monthly data on 55 liquid
futures with a 40-year academic literature behind them**, and still find that **47 of 55 assets fail to
reach t = 1.65**, that the **average in-sample R² is 0.39%**, and that **45 of 55 have negative
out-of-sample R²**. Their entire critique of MOP is that the only way to make the effect look significant
is to **pool** — which is the very operation that manufactures the significance.

Three consequences:
1. **Recalibrate expectations for a single asset.** *No one* demonstrates asset-level time-series momentum
   significance reliably, on any asset class, with vastly more data than we will ever have. An
   expectation that BTC alone will produce a clean per-asset z ≥ 2 is not conservative — it is
   unattainable, and §73.3 already computed why.
2. **This strengthens the §79.2 breadth conclusion and weakens the frequency one further.** The field's own
   workaround for thin per-asset power is aggregation *across assets and horizons* — and Huang's critique
   is precisely that naive aggregation cheats. The correct version aggregates **with fixed effects** (or,
   equivalently for us, per-asset demeaning) and over **genuinely distinct** streams (§79.2's ρ = 0.22),
   not over restacked copies of the same signal.
3. **It hardens §73.6's requirement.** If the published literature's flagship result dissolves under a
   bootstrap that accounts for persistence and heteroskedasticity, then our own single-trial statistics are
   worth even less than §73.8 already implied. **Report `N`; report per-asset; do not headline the pool.**

### §79.14 ⚠️ (A) and (C) use nearly the same data and reach opposite conclusions — how to hold both `strategy/rules/`

This is the most important interpretive question in the batch and it should not be smoothed over.

| | Baltas & Kosowski (A) | Huang et al. (C) |
|---|---|---|
| Data | 71 futures, 1974–2012 | 55 futures, 1985–2015 (MOP's set) |
| Verdict | *"strong time-series momentum effects across monthly, weekly and daily frequencies"*, Sharpe > 1.20 | *"the evidence on TSM is weak, particularly for the large cross section of assets"* |
| Method | Portfolio construction, Sharpe/alpha vs factor models | Predictive regressions, bootstrap-corrected inference, TSH null |
| Date | 2013 | 2020 |

**They are not actually testing the same proposition, and that is the resolution:**
- (A) asks: **"is this portfolio profitable and does a factor model explain it?"** Answer: profitable,
  Sharpe ≈ 1.25, alpha 13–20% p.a. unexplained.
- (C) asks: **"is the profit caused by time-series PREDICTABILITY?"** Answer: no — a no-predictability
  strategy (TSH) does the same.

**Both can be true simultaneously, and Huang says so** ("the TSM strategy is profitable"). (A) never
attempts to attribute its profits to predictability rather than to a mean-return tilt, so (C) does not
contradict (A)'s numbers — it reinterprets their *cause*. (C) is the later, more rigorous paper on the
narrower question, published in the JFE; (A) is a working paper on the broader question.

⚠️ **But note what survives (C)'s reinterpretation and what does not, for us specifically.** If TSM's
profit is a mean-return tilt, then on a **long-only** allowlist of assets we have *already selected* for
positive expected return, the tilt is largely pre-purchased by the allowlist itself — which is exactly
§79.11's point that the benchmark must be buy-and-hold. **(A)'s frequency and correlation findings
(§79.1–§79.3, §79.5) are structural facts about the data and survive (C) intact** — they are statements
about turnover and cross-correlation, not about predictability. **It is (A)'s implicit "and therefore this
is alpha" that (C) attacks.** Since this KB is taking §79.1/§79.2/§79.3/§79.5 from (A) and not its alpha
claims, **the two papers can be used together without inconsistency.**

### §79.15 ⚠️ Short-lookback rules are not merely weaker — they are CATASTROPHIC. A hard floor under any frequency push `strategy/rules/`

Worth isolating because it is the sharpest available warning against the tempting fix of "shorten the
lookback so it fires more often."

(B), Table 1, daily decisions, 0.2% cost, against buy-and-hold's **+9.49% / Sharpe 0.31**:

| Rule | 10-day | 25-day | 50-day |
|---|---:|---:|---:|
| Simple MA — ann. return | **−5.37%** | −0.21% | +2.53% |
| Simple MA — Sharpe | **−0.79** | −0.36 | −0.12 |
| Breakout — ann. return | −0.53% | +3.95% | +5.90% |
| Breakout — Sharpe | **−0.38** | +0.01 | +0.19 |

A 10-day MA rule loses **15 percentage points a year** against passive holding. (B)'s summary:
*"whipsawing is not a problem **provided the technical signals are of reasonable length (not too short)**."*
And the conclusion: technical rules work *"beyond the very shortest time period (say, 50–100 days)."*

⭐ Independent corroboration from (A), which is careful and honest about it: their most profitable daily
strategy is (1,1) with Sharpe **1.51** — and they **refuse to use it**, because the subsample analysis
shows it earned Sharpe **2.63 in the first half of the sample and 0.37 in the second**, with insignificant
alpha post-1994. Their explanation: *"financial markets became progressively more computerised and
therefore to a certain extent more efficient, hence eliminating the trivial serial day-to-day return
correlation that is captured by the (1,1) daily strategy."* **This is §74.11's edge-decay finding
appearing in a completely different market and decade, and it decays FASTEST at the shortest horizon.**

⇒ **Two rails on any frequency work.** (1) There is a hard floor around 50 days below which trend rules on
daily bars are actively loss-making — our `entry_lookback = 40` is *below it* by this evidence, reinforcing
§74.2/§79.5 from yet another direction. (2) **The shortest-horizon signals are the first to decay**, so
buying frequency by shortening lookbacks buys the most perishable edge available. Both point the same way:
**lengthen, and buy frequency from breadth instead.**

### §79.16 Capacity, crowding, and correlation-regime shift — secondary but on-point `analysis/regime.py`, `execution/guards.py`

(A)'s third contribution is a capacity study: lagged CTA industry fund flows have a **negative but
statistically and economically insignificant** effect on subsequent momentum-strategy returns (Table VII),
across all frequencies and asset classes; and even assuming the entire $264bn systematic-CTA AUM were
invested in the strategy, the implied positions stay below CFTC open interest for about half the assets
and represent only 2.3% / 0.2% / 2.9% / 0.9% of the commodity / currency / equity / rate OTC derivative
markets. **Conclusion: no capacity constraint.** ⇒ **Not actionable for us** — we are a single retail-scale
spot account; crowding-out is not our failure mode. Logged so the judgement is recoverable.

⭐ **One genuinely on-point observation is buried in their conclusion.** Explaining why CTA performance was
poor in 2009–2011 despite no capacity constraint, they report an unreported PCA result: *"the average
explained variance of the **first principal component** has been around **25%** up to the end of 2008, but
soon after Lehman Brothers collapse, it increases dramatically and averages close to **40%** for the period
2009–2011, **peaking at 45%**"* — offered as evidence *"that the data generating process has changed after
the recent financial crisis and the degree of market co-movement has increased."*

⇒ Their candidate explanations for trend-following underperformance are **(b) absence of sufficient price
trends** and **(c) increased correlation between markets, which reduces diversification benefits** — not
crowding. That is a **regime diagnostic we could actually compute**: rising cross-asset correlation
predicts trend-following underperformance *and* silently reduces effective breadth. §54.22 already directs
us to a **rolling 60-day correlation** rather than a single/average figure precisely because
*"correlations → 1"* in crisis. **§79.16 supplies the empirical warrant and a second use for that same
number: not only as a sizing/rail input, but as a leading indicator that the trend regime is degrading and
that a horizon ladder's (§79.2) independence assumption is breaking down.** ⚠️ On a 3-asset allowlist a
"first principal component" is barely meaningful; the portable form is the rolling pairwise
BTC/ETH correlation, which §54.22 already asks for.

---

## ⛔ Halal exclusions and adaptation

- ⛔ **All three papers are built on FUTURES and forwards** — 71 futures contracts (A), 55 futures and
  currency forwards (C), and (B)'s momentum literature review is about futures/CFD strategies.
  **Derivatives are excluded outright** (gharar/maisir; §27.4, §28.1, §65.11, §67.5). **Nothing here is an
  instrument recommendation.** What ports is the *arithmetic* — turnover-vs-Sharpe, cross-frequency
  correlation, the overlapping construction, the volatility estimator, the exit-channel ratio, the TSH
  null — all of which are statements about price series, not about contracts.
- ⛔ **Every strategy in (A) and (C) is LONG/SHORT by construction.** (A) eq. (3) takes
  `sign[R(t−J,t)] ∈ {−1,+1}`; (C)'s TSM *"buys assets with positive past 12-month return and **sells**
  assets with negative past 12-month return."* **The `−1` branch becomes FLAT / don't-buy for us, never a
  short.**
- ⭐⭐ **And it costs us nothing — this is the FOURTH independent instance.** (C), Table 10 and text:
  *"the performance of the two strategies mainly stems from the **long legs** and… the performance of
  their **short legs is always indifferent from zero**."* Under equal weighting the TSM long leg earns
  0.34% monthly (t = 4.92) with alpha 0.12% (t = 2.29), while the short leg earns −0.05% (t = −0.72) with
  alpha −0.03% (t = −0.46). **They flag this as a new finding not shown by MOP or Goyal & Jegadeesh.**
  Joins **§58.3** (long-only *improved* the tested breakout in both samples), **§74.6** (short leg
  contributes *exactly* nothing to the Bitcoin breakout) and **§73**'s `Side` mesh collapsing to `{+1}`
  and halving `N`. ⇒ **Four independent instances, three asset classes, four decades: the halal long-only
  constraint is not costing performance on trend-following rules.** In the (C) case it is stronger than
  neutral — the short leg is where the *unrewarded* risk sits.
- ⛔ **Leverage / margin.** (A) §6.2 builds an explicit margin model (margin-to-notional ratios of 4%
  currency / 10% equity / 3% bond / 10% commodity; margin-to-equity 10%) to size the capacity experiment.
  **Wholly excluded (riba, §28.1, §65.1).** Note this also means (A)'s "40% target volatility per
  instrument" is only achievable *with* leverage — reinforcing §79.8's warning that the constant does not
  port.
- ⛔ **Risk-free rate.** Present in every Sharpe ratio and in (A)'s Carhart/Fung–Hsieh factor models
  (`MSCI − RF`, `TCM 10Y`, `BAA Spread`). Per **§73.4** the Sharpe is usable by substitution with
  **`rf = 0`**, which re-specifies the null to *"adds nothing over holding cash"* — the correct null for a
  spot agent. The **factor models themselves are excluded** as the declined MPT/CAPM direction (§33,
  §50.1, §54.22) and are not needed for anything extracted here.
- ⛔ **(B)'s fundamental-metric race** (dividend yield, earnings yield, Fed Model, **GEYR = gilt-equity
  yield ratio**, CAPE, and its T-Bill comparison) — equity fundamentals with **no crypto analog** (=§38.3,
  §50), and GEYR/Fed Model/T-Bill are **explicitly bond-yield-based ⇒ riba**. The *result* is nonetheless
  worth one line as reinforcement: the 10-month MA beats all six fundamental predictors on Sharpe (0.54 vs
  0.39–0.48) over 1952–2011, with the advantage coming *"from the subdued volatility in the Trend
  Following returns."* Consistent with §54.11 and with the no-oracle rail (§6.4).
- ⛔ **(A)'s hedge-fund 2/20 fee analysis, CTA database work, and open-interest/CFTC capacity machinery** —
  fund-industry structure, not applicable (§79.16).
- N/A: (C) notes the TSM short leg is where the risk-adjusted return vanishes; **crypto spot market
  structure and the halal constraint again point the same way** (cf. §74's note on [B]).

## Discarded (no agent value)

- **(A)'s entire CTA-replication programme** (Tables IV–VI, Figures 2–3, 6): regressing the BarclayHedge
  AUM-weighted systematic-CTA index on Fung–Hsieh 7- and 9-factor models augmented with the FTB factors.
  Its finding (adjusted R² doubling from ~24% to >50%; CTA alpha going insignificant) is a
  **hedge-fund-benchmarking** result. We are not benchmarking managers and do not have the factor data.
- **(A)'s capacity-constraint apparatus** in full — performance-flow regressions (Tables VII–VIII, Fig. 8),
  the open-interest exceedance thought experiment (Fig. 9, eqs. 9–12), the BIS OTC-notional robustness
  check. Not our failure mode (§79.16); the one salvaged item is the PCA correlation-regime observation.
- **(A)'s Fung–Hsieh "primitive trend-following" lookback-straddle factors** — option-based constructs,
  ⛔ excluded instrument, and used only as regression controls.
- **(C)'s factor-model tables in their pricing role** (Tables 10's Fama–French-4 and
  Asness–Moskowitz–Pedersen-3 loadings on SMB/HML/UMD/"value everywhere"/"momentum everywhere") — the
  declined MPT direction; only the **long-leg vs short-leg** contrast and the **alpha differentials** were
  extracted.
- **(C)'s bootstrap implementation details** — the wild bootstrap's Rademacher draws (eqs. 8–10), the pairs
  bootstrap resampling (eq. 11), the Gonçalves–Kaffo fixed-effects demeaning correction (eqs. 12–14). The
  *lesson* (naive t-statistics over-reject on persistent heteroskedastic regressors) is extracted at
  §79.12; the machinery needs a matrix library we have declined and, per §73.8, we should not be emitting
  per-config p-values at all.
- **(B)'s momentum literature review** (Jegadeesh–Titman, Rouwenhorst, Korajczyk–Sadka, Lesmond et al. on
  short-selling costs) — cross-sectional equity momentum, ⛔ requires a short leg and a large universe.
- **Summary-statistics tables** for all 71 / 55 individual futures contracts (A's Table I, C's Table 1) —
  per-contract means, vols, skew, kurtosis for cattle, hogs, pork bellies, orange juice, lumber, gilts.
  No crypto analog, no transferable content. *(One line kept for calibration only: in (A), **34 of 71**
  univariate long-only strategies have a **negative** Sharpe, and the best is RBOB Gasoline at 0.51 — a
  useful reminder of the scale at which "good" per-asset trend Sharpes actually live, against which our
  SR ≈ 0.395 (§73.3) is unremarkable rather than alarming.)*
- Bibliographies, NBER-recession decompositions, and (A)'s discussion of commodity-market financialisation.

## Net assessment

**This batch was commissioned to buy trade frequency and it returned a refusal.** Two independent
papers, on different markets, with different methods, one of them charging realistic transaction costs,
both conclude that trading the same trend rule more often produces **no gain gross and a loss net**
(§79.1). That is a plain negative result against the project's stated priority and it should be recorded
as such: **the "trade more often per asset" half of the under-deployment plan is now refuted from outside,
joining §75.1's demotion of the ranking half.**

**What replaces it is better than what it replaced.** §79.2's measured cross-frequency correlation of
**0.22** is the first external evidence the KB has that a genuinely *independent* stream of trades is
obtainable from a 3-asset allowlist — via **horizons**, the one breadth axis the halal constraint does not
cap. §79.3 supplies the standard, cited construction (Jegadeesh–Titman overlapping portfolios) for running
it, and it is the same structure §75.1 identified as blocked by `portfolio_sim.py:600`. Together they turn
"lift the sim's one-position-per-asset cap" from a local code fix into the enabling step for the only
knowability-improving mechanism the KB has found. §79.5 bounds the §74.2 sweep from above for the first
time (plateau ~150–250 days, reversal beyond a year), and §79.6 opens a cheap, entirely untested dimension
— the **exit** channel, where our 40/20 ratio sits in the losing region of the only controlled test that
exists. §79.9's Yang–Zhang estimator is the quiet winner: ~8× more efficient volatility estimation from
data we already store, in pure stdlib, at **zero cost in trials budget**.

**On the adversarial paper, the honest verdict is that it is serious but narrower than its title.** Huang
et al. destroys the specific 12-month TSMOM regression and the "momentum is everywhere" claim, and shows
the profits are a mean-return tilt rather than predictability. It says nothing about channel breakouts,
nothing about daily bars, and nothing about crypto — so §74's crypto-specific corroboration of our rule
stands. **But two of its findings land on us squarely and neither is comfortable:** our sim's
`__pooled__` statistic is exactly the estimator Huang proves is biased upward on heterogeneous assets
(§79.12 item 4), and its TSH construction implies our real benchmark is **same-capital buy-and-hold**, a
comparator the harness does not currently run (§79.11). And §79.13 recasts our thin-sample problem as the
field's: with 31 years and 55 assets, 47 of 55 fail to reach t = 1.65. **We are not failing to clear a bar
that others clear; the bar is not cleared per-asset by anyone.**

⚠️ **Saturation against §54 is real but partial.** §54.13 already names the Donchian 40/20; §54.7 already
has volatility-parity sizing; §54.10/§54.11 already have the robustness-plateau and the "slower is better"
finding; §54.14's original Turtle already prefers the slow S2. **§54 anticipated the direction of §79.1 and
§79.5 without the controlled experiment.** What is genuinely new here is: the frequency/turnover trade-off
*measured* (§79.1), cross-frequency correlation *measured* (§79.2), the overlapping-portfolio construction
*named* (§79.3), the lookback *ceiling* (§79.5), the exit-channel ratio *tested* (§79.6), a range-based
volatility estimator *at all* (§79.9), and the TSH null (§79.11). The stop-loss material (§79.7) is the
weakest and is best read as refining §58.12 rather than opposing it.

**Single most actionable item: §79.9 (Yang–Zhang) — it is free in trials budget, stdlib-portable, and
improves every volatility-dependent quantity in the system. Single most consequential: §79.1 — the
frequency plan needs to be redirected to breadth before more effort is spent on it.**
