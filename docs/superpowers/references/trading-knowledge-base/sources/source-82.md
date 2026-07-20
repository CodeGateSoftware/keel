[← Knowledge Base index](../README.md)

## Source 82 — "Trading Systems" (Zerodha Varsity, Module 10)

**Provenance.** Free online module by **Karthik Rangappa**, published by Zerodha (Indian discount broker),
2017–2019, 16 chapters, still live and maintained (author was answering reader comments in June/July 2026).
Scraped from `https://zerodha.com/varsity/module/trading-systems/` and its chapter pages. Zerodha Varsity is
free educational content of genuinely better quality than the promotional lead-magnets in this KB
(§55, §70) — no upsell, no signal service, and the author repeatedly volunteers the limits of his own
material. That honesty is what makes this source useful, and it is useful almost entirely as a **negative
exemplar**.

**Chapter list (as published):**

| Ch | Title | Disposition |
|---|---|---|
| 1 | What to expect? | extracted — §82.1, §82.2 |
| 2 | Pair Trading logic | ⛔ §82.8 (short leg) |
| 3 | PTM1 C1 — Tracking Pairs | ⛔ §82.8 |
| 4 | PTM1 C2 — Pair stats | ⛔ §82.8 |
| 5 | PTM1 C3 — Pre-trade setup | ⛔ §82.8 |
| 6 | PTM1 C4 — The Density Curve | ⛔ §82.8 |
| 7 | PTM1 C5 — The Pair Trade | ⛔ §82.8 |
| 8 | PTM2 C1 — Straight line Equation | ⛔ §82.8 |
| 9 | PTM2 C2 — Linear Regression | ⛔ §82.8 |
| 10 | PTM2 C3 — The Error Ratio | ⛔ §82.8 |
| 11 | PTM2 C4 — The ADF test | ⛔ §82.8 (one forward pointer kept) |
| 12 | Trade Identification | ⛔ §82.8 |
| 13 | Live Example 1 | ⛔ §82.8 |
| 14 | Live Example 2 | ⛔ §82.8; ⚠️ negative finding §82.7 |
| 15 | Calendar Spreads | ⛔ halal exclusions (futures + short + explicit leverage + cost-of-carry) |
| 16 | Momentum Portfolios | extracted — §82.3, §82.4, §82.5, §82.6 |

> ⚠️ **The title is misleading and the task premise it invited is wrong.** "Trading Systems" is not a course
> on constructing and validating mechanical systems. **Thirteen of sixteen chapters are pair trading**
> (long/short statistical arbitrage on Indian equity futures), one is calendar spreads, and **one** is a
> momentum portfolio. There is no chapter on walk-forward, in-sample/out-of-sample splitting, parameter
> counts, degrees of freedom, or curve-fitting — and this is not an oversight, it is stated policy (§82.1).
> **Net structural exclusion: 14 of 16 chapters are unavailable to a long-only, no-leverage, spot agent,
> and the exclusion is on the short leg, not on a technicality.**

---

### §82.1 ⚠️⚠️ The module contains NO backtesting, by the author's own declaration — the validation verdict `strategy/backtest.py`, `strategy/promotion.py`

This is the most consequential thing in the source, and it is a hole rather than a finding.

Chapter 1, verbatim: *"this module will not include the 'backtest' bit. The onus is on you to backtest the
system and figure out if the system works for you or not."* The stated reason is disarmingly honest:
*"The only reason why I'm not including the backtesting part is that I lack programming skills."* He is
nonetheless clear about the standard: *"no trading system is complete without having the backtesting
results."*

Chapter 16 repeats the admission at the level of the strategy's core claim. On the premise that
last-12-month winners keep winning: *"**This is a claim. I do not have data to back this up**, but I have
successfully used this technique for several years."* The only supporting evidence offered anywhere in the
module is a personal anecdote — *"I had a great run with this strategy in 2009 and '10 but took a bad hit
in 2011"* — and a link to a popular-press *Economist* article.

**Verdict against §73/§78, stated explicitly as required.** This module's validation methodology is not
*looser* than Bailey/Borwein/López de Prado/Zhu — **it is absent.** There is no in-sample/out-of-sample
split, no walk-forward, no trials accounting, no multiple-testing correction, no significance test, no
benchmark, no sample-size floor. It therefore cannot contradict §73/§78 on the merits, because it never
engages them. Where the two are placed side by side there is no contest and **we side with §73/§78
without reservation**: a broker's free educational module carries no evidentiary weight against
peer-reviewed statistics, and nothing in it may be cited to relax MinBTL (§73.2), the trials budget
(§73.5), the a-priori parameter requirement (§73.12), PBO/CSCV (§78), or the promotion gate.

**What it is genuinely good for.** It is a clean, freshly-dated, non-adversarial specimen for the file
§64.1 / §68.6 / §58.11 already maintain — a competently written, widely read, honestly presented trading
system that **has never been tested**, offered to an audience that will trade it. Its virtue is that it
says so out loud. The *Economist*-article-as-evidence move is worth remembering as the mildest form of the
failure §73 names, and the *"I have successfully used this technique for several years"* line is the
canonical statement of the survivorship/recall evidence that MinBTL exists to displace.

**Stdlib portability:** N/A (no method).

---

### §82.2 ⚠️ The one instruction that would actively ENCOURAGE overfitting — flagged as directed `strategy/backtest.py`

Chapter 16, Step 5, on how to choose the capital-allocation weights across the portfolio:

> *"the approach to capital allocation should come from your backtesting process, this also means you will
> have to **backtest various capital allocation techniques to figure out which works well for you**."*

And in Step 5's list of "ideas" to try: 50/50 across top-5 and remaining-7; 40% across top-3 and 60% across
the remaining 9; or — for contrarians — overweight the *bottom* 5 ranks. Chapter 16.4 extends the same
invitation to the lookback and holding period (monthly / fortnightly / weekly / daily / intraday), and to
the ranking variable itself (price return, quarterly sales, EPS growth, profit margin, EBITDA margin),
closing with *"the options are plenty, and your imagination only restricts it."*

**This is sweep-the-configuration-space-and-keep-the-winner with zero controls**, and it is offered as the
recommended procedure. It is exactly §73.1's target. Counted honestly, the chapter's own menu spans roughly
5 holding periods × 5 ranking variables × 4 weighting schemes ≈ **100 configurations before any parameter
inside one is touched** — and §73.3 computed that at our observed Sharpe we can afford **N ≤ 3**. Selecting
the best of ~100 on a single history, with no OOS block and no deflation, produces a number with no
out-of-sample meaning; §78's PBO would be expected to approach a coin flip.

⚠️ **The "contrarian, overweight the bottom 5" suggestion deserves separate flagging.** It inverts the
strategy's own stated premise mid-chapter, with no test and no argument, purely as another cell to try.
That is the signature of a search over signs as well as parameters — the most damaging kind, because it
doubles `N` while feeling like creativity.

**Agent module:** none — this is a rail-defence note. If any future momentum work reaches
`strategy/backtest.py`, the horizons and weights must be fixed **a priori** per §73.12/§74.13, and the
trials ledger (§73, item 1) must record them. Nothing from this module may be swept.

---

### §82.3 ⭐ A concrete number for §75.1's "ranking is a no-op" — the universe-to-portfolio ratio `strategy/money_mgmt.py`

Chapter 16, Step 1, from the author's own practice: *"I would suggest you have at least **150-200 stocks in
your tracking universe** if you wish to build a momentum portfolio of **12-15 stocks**."* Chapter 16 Step 5
adds *"A good momentum portfolio contains about 10-12 stocks. I'm comfortable with up to 15 stocks."*

That is a required **universe : portfolio ratio of roughly 12:1 to 16:1** for a cross-sectional ranking
engine to have anything to select.

**Why this is worth keeping despite being a rule of thumb.** §75.1 established, and §60.2 was bounded by,
the finding that *ranking* is a no-op at `|allowlist| = 3` — but as a qualitative argument ("ranking only
bites when candidates outnumber slots"). §82.3 supplies the first **number** attached to that argument from
a practitioner running the strategy: our ratio is **3 : 3 = 1:1**, against a practitioner floor of ~12:1.
We are short by more than an order of magnitude, not marginally. The halal allowlist caps assets at 3, so
this gap is **structural and unclosable** — it cannot be engineered around, only routed around (which is
what §79.2's horizon-breadth ladder does).

⇒ **Cross-sectional ranking of any kind is formally out of reach for this agent, and §82.3 is the cleanest
one-line justification for saying so.** This retires the ranking half of §60.2 as a build candidate rather
than leaving it queued.

**Stdlib portability:** trivial (integer comparison); but the conclusion is that the code should not exist.

---

### §82.4 Ch16's momentum-portfolio specification in full — mechanically complete, and fully saturated by §79/§80 `strategy/rules/`

Recorded so a future reader does not re-scrape the module. The six steps:

1. **Define a tracking universe** — 150–200 liquid names (he suggests BSE 500 or Nifty 50 as defaults;
   custom filters by market cap or price are permitted).
2. **Set up data** — daily closes, **1 year of history**, adjusted for splits/bonuses/special dividends.
   (*"Clean data is the crucial building block to any trading strategy."*)
3. **Calculate returns** — `return = (ending_value / starting_value) − 1` over the trailing 12 months,
   for every name in the universe.
4. **Rank** — sort descending on that return; rank 1 = highest.
5. **Create the portfolio** — buy ranks 1..N (N = 10–15), **equally weighted**: `capital / N` per name.
6. **Rebalance monthly** — recompute at the **last trading day of the month post-close**, buy on the **first
   trading day of the month**, hold to month-end; sell names that dropped out of the top N, buy the new
   entrants. *"chances are that out of the initial portfolio, only a hand full of stocks would have changed
   positions."*

The stated premise: *"if the stock has done well… for the last 12 months, it implies that it has good
momentum… The expectation is that this momentum will continue onto the 13th month."*

**Saturation verdict — total.** This is textbook Jegadeesh–Titman 12-1 cross-sectional momentum, presented
without the citation, without the statistics, and without the short leg. Every component is already held in
a strictly more rigorous form:

- The **J × K formation/holding grid** with monthly/weekly/daily rebalancing — §79, measured across 34
  years and 71 assets with cross-frequency correlations (§79.2) and a costs treatment (§79.1). Varsity
  offers the same grid as an untested menu (§82.2).
- **Equal-weight N-slot deployment from a ranked candidate list** — §60.2, which already carries the
  explicit caveat that *"the 1/15 equal-weight number itself does not port — it presumes a broad
  multi-hundred-stock universe."* §82.3 now quantifies that caveat.
- **Overlapping tranches / the 1/K capital rule** — §79.3, which Varsity does not have at all (his
  rebalance is all-in/all-out at month end).
- **Whether momentum exists in crypto at all** — §80.10/§80.11, which is asset-class-correct where Varsity
  is Indian equities, and which found the effect at **1–3 weeks** in BTC, with **Ethereum weak-to-reversing**.
- **Cross-sectional vs time-series momentum** — §80 already logged the cross-sectional literature
  (Stoffels 2017) as ⛔ **excluded**, for requiring both a short leg and a large universe.

**The one structural difference worth noting.** Varsity's version is **long-only** — buy the top N, no short
leg, unlike the cross-sectional literature §80 excluded. So the shorting objection does not apply here.
**The universe objection does, and it is fatal on its own (§82.3).** A long-only cross-sectional momentum
portfolio over a 3-name allowlist degenerates to "hold whichever of BTC/ETH/PAXG rose most last year,"
which is a 1-of-3 selection with no statistical content and directly contradicts §58.4's finding that
market selection on realized profitability does not persist (in↔OOS correlation r = 0.15) and §73.3's
prohibition on per-asset fitting.

⇒ **Nothing to build. Logged as fully subsumed.**

**Stdlib portability:** the whole of steps 3–6 is one division, one `sorted()`, and one slice — pure
stdlib, `Decimal`-safe. Portability is not the constraint here; universe size is.

---

### §82.5 The 12-month/1-month pairing AGREES with §79.1 on frequency — independent, but only anecdotal, corroboration

Open question 2 asked what this source says about trade frequency. It agrees with the KB's position, from a
completely different tradition and with none of the evidence.

The default is **rank on 12-month return, hold for one month, rebalance at month-end** — i.e. a slow signal
with a monthly decision cadence. Faster variants are offered (§82.2) but the *default* he actually traded
for years is the slow one. When a reader asked in the comments whether a 6-month lookback would be better
("after 1 year of momentum it might break"), the author's reply was non-committal: *"you can do that if you
want to set up super short term trades."* No costs analysis appears anywhere in the module.

This lines up with **§79.1** (end-of-month beats daily once costs are charged; 10× turnover buys no extra
gross edge) — but as *practice*, not evidence. §79.1 measured it; §82.5 merely does it. **Weight: zero
additional evidentiary value; recorded only because open question 2 asked and the answer is "agrees."**

⚠️ One weak convergence worth a line, flagged as weak. The **12-month formation window ≈ 250 trading days**
sits inside the **150–250 day plateau** §74.2 found for the entry-channel lookback, and above our
`entry_lookback = 40`. Two unrelated traditions (crypto trend-breakout, Indian equity momentum) landing on
a similar timescale is mildly reassuring for the "40 is too short" direction of §74.2 — but this is
coincidence of order-of-magnitude between two *different* rule families on *different* asset classes, one
of them untested. **It is not evidence and must not be counted as a trial or a confirmation.**

---

### §82.6 ⚠️ Momentum's drawdown asymmetry — reinforcement only, from an anecdote `analysis/regime.py`

Chapter 16.5, "Word of caution": *"the price-based momentum strategy works well only when the market is
trending up. When the markets turn choppy, the momentum strategy performs poorly, and when the markets go
down, **the momentum portfolio bleeds heavier than the markets itself**."*

The claim that a momentum book's downside is *worse than* the benchmark's — not merely correlated with it —
is a real documented phenomenon in the academic literature (momentum crashes), and it is not currently held
anywhere in this KB (grep: no hits for `momentum crash`). But this source supplies **no measurement** — no
drawdown figure, no beta, no period, only *"I had a great run… in 2009 and '10 but took a bad hit in 2011."*

⇒ **Reinforces the regime-gating direction already established by §74.7 (rules work in trending markets,
fail in quiet ones) and §54's trendiness stack. Adds no threshold and no number.** If momentum ever becomes
a live second rule class per §80.10, its *downside* behaviour should be measured explicitly rather than
assumed symmetric with the breakout's — but that instruction comes from the literature, not from here.

---

### §82.7 ⚠️ NEGATIVE FINDING — the chapter titled "Position Sizing" contains no money management at all

Open question 5 asked for position-sizing / money-management formulae. **This module yields exactly none**,
and the reason is worth recording because the chapter title is actively misleading.

Chapter 14.1 is headed "Position Sizing." Its entire content is **beta-neutral lot matching** for a
long/short futures pair: given `beta = 0.79`, ICICI lot size 2750, HDFC lot size 500, compute
`2750 / 0.79 = 3481`, round to 3500, therefore trade 7 HDFC lots against 1 ICICI lot so the two legs offset.
That is contract-quantity arithmetic for hedging one leg against another — it is not sizing.

**Nowhere in the sixteen chapters is there:** a risk-per-trade fraction, a volatility- or ATR-derived size,
a fixed-fractional or fixed-ratio rule, optimal-f or Kelly, a drawdown-scaled exposure, or a stop-loss of
any kind. The only allocation rule in the module is Chapter 16's `capital / N` equal weight (§82.4), which
§60.2 already holds. There is **no stop-loss discussion anywhere in the module** — the pair trades exit on
mean reversion of the residual and the momentum portfolio exits on monthly re-ranking.

⇒ **Open question 5: zero yield, confirmed by exhaustive read rather than by absence of search.** §54's
risk/money-management chapters and §54.20's ruin/optimal-f material remain the KB's authority, untouched.

---

### §82.8 ⛔ Pair trading / cointegration / ADF — the KB's first encounter with statistical arbitrage, and it is structurally unavailable to us

Thirteen chapters build a relative-value pair trade in two flavours: (Method 1) price ratios/spreads with a
normal-distribution and density-curve overlay; (Method 2) OLS regression of `Y` on `X`, choose which name is
`X` by the **error ratio** (run the regression both ways, keep the ordering with the lower ratio), then test
the **residual** for stationarity via the **Augmented Dickey-Fuller test** — `p ≤ 0.05` required — and trade
when the residual reaches **±2 standard errors** from its mean, exiting on reversion to the mean.

**Why it is excluded, and the exclusion is structural not technical.** The trade *is* the pairing: you buy
the cheap leg and **sell the expensive leg**. Chapter 2 states it plainly (*"buying the cheaper stock…
and selling expensive one"*), Chapter 14's worked trade is *"short HDFC and go long on ICICI"*, and every
example is on **stock futures** with lot sizes. There is no long-only reduction of a pair trade — removing
the short leg removes the market-neutrality that is the strategy's entire rationale, leaving an outright
directional bet chosen by a spread signal. This is not the standing "short leg → exit/don't-buy filter"
adaptation (that convention applies to *directional* short setups); here the short leg is half of a single
instrument. ⛔ **Excluded: short leg + futures instrument + (Ch 15) explicit leverage.**

**Novelty note, stated honestly.** Grep across `sources/` returns **zero** prior hits for `cointegrat`,
`co-integrat`, `dickey`, `unit root`, `market-neutral`, `beta-neutral`, `tracking universe`, and none for
`pair trad` as a *strategy* (the two hits for "pairs trades" are §27's excluded long-gold/short-copper aside
and §56's currency *pairs*). ⚠️ **One qualification, so the novelty claim is not overstated:** `stationar`
*does* appear in four prior sources, but never as the testable time-series property — §14.4/§21 use
"non-stationary" colloquially about regime instability, §68 likewise about ARIMA, and §80's **"stationary
bootstrap"** (Politis–Romano) is an unrelated object, a resampling scheme, not a unit-root test. **Formal
stationarity/unit-root testing is genuinely new to the KB — and it is new material we cannot use.**
Recording it here means the module does not need re-reading.

**The one forward pointer worth keeping.** §62 (Paper B) established that scale-in/DCA is variance-optimal
only under genuine mean reversion (`AR(1)` coefficient `a < 0`), and concluded that *"the required `a<0`
regime was never verified before those rules were trusted"* — which is the KB's cleanest account of why
dip-buying and RSI mean-reversion were refuted on crypto. **The ADF test is the named, standard instrument
for exactly that verification**, and it is a third route to the same trend/martingale/mean-revert
trichotomy §54.1's Efficiency Ratio and §62's Hurst exponent already give us. Applied to a *single asset's
own returns* rather than to a pair residual, it would test that premise directly.

⚠️ **But this source cannot supply it, and says so.** Chapter 11.3, verbatim: *"Frankly, this is a highly
complex process and unfortunately, I could not find a single source online which will help you run an ADF
test for free. I do have an excel sheet (which has a paid plugin) to run an ADF test, but unfortunately, I
cannot share it here… If you are a programmer, **I've been told** that there are Python plugins easily
available."* The load-bearing statistical gate of his own flagship system is a black box he does not compute
and cannot explain — which is a second instance of §82.1's pattern.

**Stdlib portability of ADF — assessed, since the project has declined statsmodels/SciPy/NumPy.** The
standard `statsmodels.tsa.stattools.adfuller` is unavailable to us. A stdlib implementation is *feasible in
principle* — the test is an OLS regression of `Δy_t` on `y_{t−1}` plus `k` lagged differences, and the
τ-statistic is compared against a **hardcoded Dickey–Fuller critical-value table** (the critical values are
non-standard constants, not `t` quantiles, which is the only reason it looks hard) — but **this source
provides none of the machinery**: no regression formulae, no lag-selection rule, no critical values, no
p-value interpolation. What Chapter 11.2 *does* give is a crude three-condition proxy that is stdlib-trivial:
split the series into three parts and require (i) similar means, (ii) similar standard deviations, and
(iii) no autocorrelation. That is a weak screen, not the test.

⇒ **Logged as a research pointer for a future, properly-sourced extraction, NOT as a build item and NOT as
a lead.** If single-asset stationarity testing is ever wanted, it needs a primary statistical source with
the critical-value table, not a broker's tutorial that outsources the computation.

⚠️ **One outright statistical error, recorded as a rigor flag.** Answering a reader in the comments, the
author states *"the two stocks will be stationary if they are normally distributed."* That is false —
stationarity and normality are independent properties (a Gaussian random walk is normally distributed at
every point and emphatically non-stationary; a bounded non-Gaussian series can be stationary). Minor in
isolation, but it is an error about the exact concept the chapter exists to teach, and it calibrates how
much weight the module's statistical content can carry.

---

### §82.9 The intercept / explanatory-power veto — small, generic, and recorded for completeness

Chapter 14.2 supplies the module's one piece of genuine analytical discipline. Having found a pair that
passes every filter (ADF `p = 0.048`, residual at `+2.67` SD, both large private banks, similar business),
he **rejects the trade** on the regression intercept: `intercept = 1626` against `HDFC price = 2024`, so
~80% of `Y`'s price is what the model *cannot* explain. *"if we are trading this pair, then we are
essentially trading a very small probability here… I'd look at risk first and then the reward."*

The generalizable principle — **reject a signal whose fitted model explains little of the quantity it claims
to explain, even when every threshold passes** — is sound and is a nice instance of not treating a
green-light checklist as sufficient. But it is a restatement of ordinary `R²` discipline, it is specific to
regression-based signals (we have none), and the KB's equivalent discipline is already carried in stronger
form by §78's PBO/CSCV and §58.11's random-entry null, both of which ask the harder question: *is this
better than nothing?* rather than *does the model fit?*

⇒ **Reinforcement only. No action.**

---

## Halal exclusions ⛔

- **The entire pair-trading system (Ch 2–14, 13 chapters).** Requires a **short leg** by construction —
  the trade is simultaneously long one name and short the other. Long-only rail. Additionally traded via
  **stock futures with lot sizes** in every worked example. ⛔ excluded, §82.8.
- **Calendar spreads (Ch 15).** Quadruply excluded: (i) **futures** on both legs (non-spot, gharar per
  §27.4/§65.11); (ii) a **short leg** (sell the near-month, buy the current-month, or the reverse);
  (iii) **explicit leverage recommendation** — *"since you simultaneously buy-sell the same asset, you take
  out the directional risk involved in the trade, hence it does make sense to **top up the leverage**"* —
  riba, and precisely the reasoning §63 and §56 exclude; (iv) the entire pricing basis is **cost of carry**
  (*"the futures price of Near month contract is always higher… owing to the 'cost of carry'"*), which is
  §18's excluded carry/rollover in its purest form. Nothing extracted.
- **The Chapter 1 opening trade** — a **short strangle on Bank Nifty**, i.e. selling naked call + naked put
  options to collect premium. Options (gharar/maisir, §42–§49, §65.11) + naked short + premium-for-no-risk.
  The chapter also celebrates a PNB call option that rose 20,600% overnight as the module's hook. ⛔.
- **"Volatility based Delta hedging"** — announced in Ch 1.3 as planned system #2. It was never written
  (the published module has no such chapter), and would be ⛔ options-based in any case. Recorded so a
  future reader does not go looking for it.
- **Intraday variants** (Ch 15's *"most of the trades closing within the same day"*; Ch 16.4's *"even do an
  intraday momentum portfolio"*) — out of scope by timeframe against our daily bars / ~21–24 day holds,
  and against §74.10's explicit "daily beats intraday" finding.

## Discarded (no agent value)

- **Ch 3–7, Method 1 (ratio/spread pair trading)** — density curves, normal-distribution overlays and
  z-score triggers on a two-name spread. Excluded with the rest of §82.8; the underlying normal-distribution
  and standard-deviation material is elementary and saturated by §04/§54.
- **Ch 8–10 (straight-line equation, linear regression, error ratio)** — an Excel-level introduction to OLS
  for readers with no statistics. The **error ratio** (regress both ways, keep the ordering with the lower
  ratio) is a real technique but exists only to decide which leg of a *pair* is the dependent variable; it
  has no meaning for a single asset.
- **Ch 12–13 (trade identification, live example 1)** — worked walkthroughs of the excluded pair trade
  (TATA Motors vs TATA Motors DVR).
- **The BSE/NSE market-structure context throughout** — Nifty 50 / BSE 500 index construction, Z-category
  stocks, market-cap and price filters, corporate-action adjustment (splits/bonuses/special dividends).
  N/A to spot crypto, which has no corporate actions and a 3-name allowlist.
- **Ch 16's fundamental-momentum variants** — ranking by quarterly sales growth, EPS growth, profit margin,
  EBITDA margin. No crypto analog (=§50.3, §38.3), and offered as untested menu items (§82.2).
- **Ch 1's "systematic deduction" framing** — the definition of a trading system as *"an approach you can
  define as a process and quantify"* versus gut/friend/TV/broker tips. Correct and completely saturated by
  §1, §2, §20 and §54's opening chapters.
- **The comment threads** (250–350 per chapter) — reader Q&A, mostly requests for help running the ADF test
  without programming skills. Two items were pulled out above (§82.5's 6-month-lookback exchange, §82.8's
  normality error); the rest carries nothing.

## Net assessment

**Heavily saturated — two keepers, both of them negatives, and one large exclusion recorded so the module
need not be read again.**

The honest summary is that this source was mis-framed by its title. It is not a system-construction and
validation course; it is a pair-trading tutorial with a momentum-portfolio appendix. Measured against the
five open questions:

| Open question | Yield |
|---|---|
| 1. System construction & validation methodology | **Negative — the module explicitly has none (§82.1), and contains one actively overfitting-encouraging instruction (§82.2). Side with §73/§78.** |
| 2. Trade frequency / holding period | Agrees with §79.1 (12-month formation, monthly rebalance) — but as untested practice, zero evidentiary weight (§82.5) |
| 3. Entry/exit rule specifications | Nothing new. The 12-month formation window's loose coincidence with §74.2's 150–250d plateau is noted and explicitly discounted (§82.5) |
| 4. A second, breakout-uncorrelated rule class | Nothing usable. Its cross-sectional momentum portfolio is fully subsumed by §79/§80 and is structurally out of reach at `\|allowlist\| = 3` (§82.3, §82.4) |
| 5. Position sizing / money management | **Zero — the chapter titled "Position Sizing" is beta-neutral lot matching, and the module contains no stop-loss anywhere (§82.7)** |

Against **§54**, the saturation is essentially total on everything the two overlap on, which is little: §54
covers system testing, risk and money management from a vastly more rigorous base, and this module covers
none of the three. Against **§79/§80**, the momentum chapter is comprehensively outclassed — those sources
have the J×K grid, the cross-frequency correlations, the costs treatment, the overlapping-portfolio
construction, and crypto-specific evidence, all of which Chapter 16 lacks. Against **§73/§78**, there is no
contest to adjudicate.

**What earned the file:** (a) **§82.1** — a clean, well-written, honestly-labelled specimen of an untested
system published to a large audience, which strengthens the §64.1/§68.6/§58.11 exhibit series with a case
where the author volunteers the omission; (b) **§82.2** — a concrete, quotable instance of
optimize-on-history advice, ~100 configurations deep, useful as the thing §73.12's a-priori requirement
exists to forbid; (c) **§82.3** — the first *number* (12:1–16:1 universe-to-portfolio) behind §75.1's
qualitative "ranking is a no-op," which **retires the ranking half of §60.2 as a build candidate**;
(d) **§82.7** — an exhaustively-verified zero on open question 5; and (e) the §82.8 exclusion record,
covering the KB's first encounter with cointegration/stat-arb, so the judgement is recoverable rather than
silently lost (the precedent set by row 69).

⚠️ **Stream assessment: do not feed more Zerodha Varsity modules on strategy.** The catalogue is
derivatives-heavy (Futures Trading, Options Theory, Option Strategies — all ⛔ by instrument) and the house
style, on this evidence, is clear exposition with **no empirical validation attached**. Varsity's genuine
strength is market-mechanics education, which this KB saturated at §04/§37. Nothing here justifies another
credit.
