[← Knowledge Base index](../README.md)

# Source 54 — "Trading Systems and Methods" (Perry J. Kaufman, 5th ed., Wiley, 2013)

**Type:** systematic-trading **textbook / encyclopedia**, 1,232 pp. The canonical
quant-trading reference — wall-to-wall *mechanical, backtestable* methods with formulas and
parameters. This is the highest-value source the KB has received: it is the structural
**opposite** of the saturated Swissquote/Stanzione primers, and it lands directly on the two
open defects in [[halal-cb-autotrade-project]] — the **crypto stop/risk model** and
**validation rigor** — while independently *validating the trend-following pivot* (the Turtle).

**Scope note (partial extraction, part 1 of the book).** At 1,232 pp this book is too large to
transcribe whole. Following the ebook routine's crypto-appropriate/testability bias, this file
extracts the **five highest-value chapters** for the project's current state:

| Ch | Title | Why extracted |
|----|-------|---------------|
| 1 (§ "Measuring Noise") | Efficiency Ratio | per-asset trend-tradability diagnostic |
| 17 | Adaptive Techniques | KAMA + ER-adaptive stops for noisy crypto |
| 20 | Advanced Techniques (Volatility & Noise) | crypto stop/risk model |
| 21 | System Testing | upgrades the `keel simulate` harness |
| 23 | Risk Control | Kaufman on stops + market ranking (the ETH answer) |

Chapters recommended for a **part-2 pass** and those **excluded** are listed at the bottom.
Sections below use the KB convention **§54.x** = *source 54, section x*.

---

## §54.1 — Price noise & the Efficiency Ratio (ER) — *Ch 1*
**Module: `analysis/regime.py`, `analysis/indicators.py` (NEW indicator), `strategy/promotion.py` (per-asset gate)**

*Noise* is the erratic, unpredictable movement around the underlying direction (a "drunken
sailor's walk"). **Noise ≠ volatility** — they must not be confused. Kaufman measures noise with
the **Efficiency Ratio (ER)**, also called *fractal efficiency*:

```
        | P_t − P_{t−n} |              net directional move
ER_t = ─────────────────────  =  ────────────────────────────────
        Σ | P_i − P_{i−1} |          sum of the individual moves
```

- **ER ≈ 1** → strong clean trend (low noise). **ER ≈ 0** → pure noise, no exploitable trend.
- Alternatives: *price density* and *fractal dimension* (formulas in book); **ER is the clearest**
  and is what Kaufman uses throughout.
- **Empirical finding (crypto-relevant):** noise varies by market — equity indices are the
  **noisiest**, short-term rates the least; *developing markets have LOW noise, mature markets
  HIGH noise*, and **noise rises as a market matures / gains participants.**
- **Low-noise market → a simple breakout captures a large % of the move; high-noise → the trend is
  identified too late and there is no profit left.** To avoid being faked out by noise, **require a
  larger price change before entering** (wider breakout threshold in noisy conditions).

**Crypto-fit + testability:** ER is a cheap, hand-rollable (Decimal) indicator. It gives a
**principled, quantitative answer to the ETH-trend-fit problem** (see §54.9, §54.11): compute a
rolling ER per allowlist asset; only trend-trade assets whose ER clears a threshold. This is the
same idea as the market-ranking-by-trendiness (§54.9) and reframes the "keep-or-drop-ETH"
question as *"is ETH's ER/robustness above the bar right now?"* rather than a static decision.

---

## §54.2 — Measuring volatility (the stop-model foundation) — *Ch 20*
**Module: `analysis/indicators.py`, `execution/executor.py`, `execution/guards.py`**

**Price-volatility relationship:** volatility is roughly proportional to price level (*lognormal*)
— higher price ⇒ larger absolute swings. So for crypto **use % / log returns, or price-scaled
ATR**, never fixed dollar amounts (reinforces §22.1 crypto-vol calibration and the ATR sizing
already in the Turtle).

**Five practical volatility measures over the past `n` days:**
1. `V = Close_t − Close_{t−n}` (change in price) — depends on only 2 points; understates.
2. `V = Max(High…) − Min(Low…)` (max fluctuation) — estimates the **largest move for a holding
   period** ⇒ a natural basis for a stop/target if you know the avg hold.
3. `V = Average(TrueRange, n)` — **ATR, the most popular; the guideline for future volatility**,
   used for stops, sizing, and current risk level. *(Already built in `keel`.)*
4. `V = Σ|Close_i − Close_{i−1}|` (sum of abs changes) — like ATR but misses the prior-day gap.
5. `V = Stdev(returns) × √252` — **classic annualized volatility** (financial standard).

**ATR vs annualized volatility:** ATR is smoother and jumps less on gaps; annualized vol reacts
harder to shocks. Both convert to dollars by ×price. Kaufman: the two most useful are **ATR and
annualized volatility.**

**Relative Volatility** `RV = V(n)/V(m)` with `m ≥ 10·n` — a **volatility filter** (is current
vol high or low vs normal?). Lag the long window so it doesn't overlap the short one.

**Crypto-fit + testability:** all hand-rollable; ATR already present. Adds annualized-vol and
relative-vol as new `indicators.py` helpers, and RV as a filter input (see §54.4).

---

## §54.3 — ATR stops & profit targets; the volatility-breakout entry — *Ch 20 / 23*
**Module: `execution/executor.py` (stops/targets), `strategy/rules/` (NEW candidate rule)**

**Stops & targets from volatility (the most common use):**
```
Profit target (long):   PT = entry + k × ATR      (k ≈ 3)
Stop-loss   (long):     SL = entry − k × ATR      (k ≈ 3)
```
- **Stop trigger in noisy markets:** use the intraday high/low to *detect* the breach but **exit
  on the close** — capturing the pullback improves the fill. (Consistent with §34.1 close-based
  stop; this is Kaufman's explicit recommendation, see §54.8.)
- **Profit-taking** is the mirror — exit on the intraday *spike*, not the close.
- **Trend-follower caveat (load-bearing for the Turtle):** for long-term trends a profit target
  *hurts* — you forfeit the rare **fat-tail** move. If you must take profits, you then need a
  **re-entry rule** to rejoin an intact trend. Profit-taking helps only *fast/noisy* systems.

**Volatility-breakout rule (Bookstaber) — NEW candidate for `strategy/rules/`:**
```
Buy  if next close ≥ current close + k × ATR(n)     (k ≈ 3; long-only: ignore the sell side)
```
A pure volatility breakout — structurally a cousin of the Turtle (buy strength) but keyed off ATR
rather than a Donchian channel. **Testable via the harness** as an alternative/complement to the
Turtle; k is the tunable.

**Crypto-fit:** k×ATR stops/targets scale to crypto's high vol and directly attack the
"stops-too-tight" defect. Long-only: keep only the buy branch; short setups → exit/don't-buy.

---

## §54.4 — Trade selection by volatility (entry filter + exit) — *Ch 20*
**Module: `strategy/engine.py` (entry filter), `execution/executor.py` (exit), `analysis/regime.py`**

Expectations for selecting trades by volatility:
- **Entering on very HIGH volatility = very high risk** (outcomes range from big wins to big
  losses; return/risk declines). **Best long-run performance often *avoids* these.**
- **Entering on extreme LOW volatility** seems safe but prices often have no direction (small,
  frequent losses). A short-term *drop* in vol before entry can be a good trigger.
- **Entering on LOW (but not dead) volatility is preferable** — confirmed by Kaufman's tests
  (Table 20.2): a low-vol entry filter **improved every market**, cut trades ~30%, and raised
  profit/trade ~47%.

**Volatility entry filter (mechanical):**
1. Compute a fast and a slow trend.
2. Compute volatility (any §54.2 measure), **excluding the current bar**.
3. **Enter only if today's vol is above the low threshold and below the high threshold.**
   Thresholds set in std-dev units of 20-day vol (1σ = top 16%, 2σ = top 2.5%, 3σ = top 0.13%;
   short-window vol can exceed 3σ, so test factors > 3).

**High-volatility exit + reset rule:** exit when today's return > `factor × 20-day vol`; **do not
re-enter until vol falls back to ~¼** (market "back to normal"). Table 20.3: *exit-and-reenter*
beat both no-exit and exit-only.

**Crypto-fit + testability:** a configurable low-vol entry filter + high-vol exit/reset is a clean
harness experiment: does gating Turtle entries on a volatility band (avoid buying breakouts during
blow-off vol) improve edge? Default OFF (live unchanged/safe), sweep thresholds, validate.

---

## §54.5 — Kaufman's Adaptive Moving Average (KAMA) & variations — *Ch 17*
**Module: `analysis/indicators.py` (NEW), `strategy/rules/` (NEW adaptive-trend candidate)**

An *adaptive* technique changes speed with market conditions: **slow (lag more) when noisy, fast
when the trend is clean.** KAMA is Kaufman's signature method and is built directly on ER (§54.1)
— **purpose-built for the noise problem crypto has.**

```
KAMA_t = KAMA_{t−1} + sc_t × (p_t − KAMA_{t−1})
sc_t   = [ ER_t × (fastest − slowest) + slowest ]²
fastest = 2/(fast+1)   (nominal fast = 2  → 0.6667)
slowest = 2/(slow+1)   (nominal slow = 30 → 0.0645)
```
- Squaring `sc` makes the slow end ≈ a 900-period trend (barely moves in noise) and lets the fast
  end reach ~4-period responsiveness in a clean trend.
- **ER window:** 8–10 days typical, keep **< 14** (prices rarely move one direction ≥14 bars).
- **Trade the KAMA** by the direction of the trendline (buy when it turns up); apply a **small
  threshold filter** (e.g. 0.1 std-dev of the trendline's changes) to suppress whipsaw on minor
  penetrations.
- **Variations (same family):** *Chande VIDYA* (EMA whose speed scales with relative volatility
  `k = stdev(C,n)/stdev(C,m)`), *correlation-r² adaptive*, *Ehlers MAMA/FAMA* (phase-rate), *McGinley
  Dynamics*. Kaufman's 20-yr comparison (Table 17.1): **all adaptive methods were profitable on
  average across markets; KAMA slowest/steadiest** (avg profit factor 1.53).

**Crypto-fit + testability:** KAMA is a strong **new candidate trend rule** for noisy crypto —
it should hold trends longer than a fixed MA and step aside in chop, potentially fixing the weak
ETH trend-fit. Hand-rollable in Decimal. Validate through backtest→paper→promotion like the Turtle.

---

## §54.6 — ER-adaptive & Parabolic trailing stops (long-only) — *Ch 17*
**Module: `execution/executor.py` (trailing-stop algorithms)**

**Volker Knapp's ER-adaptive ATR stop** (uses 10-day ATR + 10-day ER) — a trailing stop that
**tightens as the trend gets cleaner and gives room when noisy**, a direct answer to
"stops-too-tight for crypto":
```
Initial stop = entry − 6 × ATR(10)              (wide, like the Turtle's 2N but wider)
if ER < 0.30:            leave the stop unchanged
if 0.30 ≤ ER < 0.60:     tighten long stop by 0.1 × ATR
if ER ≥ 0.60:            tighten long stop by 0.2 × ATR
Trailing stop only ADVANCES (never loosens); applies to the next day.
```
(Rules are asymmetric long vs short; we keep the long branch only.)

**Wilder's Parabolic SAR as a long-only trailing exit:**
```
SAR_t = SAR_{t−1} + AF × (High_t − SAR_{t−1})     (long)
AF starts 0.02, +0.02 on each new high, capped at 0.20.
Guard: SAR may never exceed the low of today or the prior day (noise protection).
```
SAR is a *stop-and-reverse* system; **for long-only spot it is purely an EXIT/trailing-stop** (we
never reverse to short). The AF acceleration = "time is the enemy" (a position must keep working).

Kaufman also lists KAMA itself, swing highs/lows, and highest-high/lowest-low of `n` as valid
stop mechanisms (see §54.8).

**Crypto-fit:** the ER-adaptive stop is the most promising — it combines the wide-initial-stop the
Turtle already proved (fixes tight-stop blowthrough) with automatic tightening in clean trends.
Testable as an alternate exit for the Turtle. Long-only throughout.

---

## §54.7 — Position sizing = volatility parity; managing risk *without* stops — *Ch 23*
**Module: `strategy/money_mgmt.py`, `execution/guards.py`**

**Target-volatility sizing (risk-parity):**
```
Investment size = (annualized stdev of the strategy's returns) / target_volatility
```
- Target vol typically **15%** (aggressive); practical floor **6–8%**; conservative ~10%.
- Reduce all position sizes by the % overshoot if the implied investment is too large.

**Three ways to size a position (stocks/crypto), given cash to invest:**
1. `cash / price` — naive; concentrates risk on cheap, volatile names. **Avoid.**
2. `cash / (100 × annualized vol)` — risk-adjusted.
3. `cash / (ATR × √252)` — **true-range method; the better risk measure.** *(Matches the Turtle's
   ATR sizing.)*

**Equal-risk (vol-parity) across assets is the most conservative allocation** unless you can
*select* better trades in advance (see market-ranking §54.9). Reducing size as volatility rises
keeps portfolio vol stable (otherwise it swings 6%→25%).

**Managing risk WITHOUT stops — *volatility stabilization*:** an alternative/complement to
stop-losses is to **shrink position size as volatility increases** (rebalance size as vol changes,
accounting for switching costs). Useful where stops get gapped through (crypto shocks).

**Risk-control overlays (pick one):**
1. % of initial margin (50–70%) — **N/A (no margin/leverage; riba).**
2. **% of portfolio / account value (1.0–2.5%)** — equalized risk across all markets. *This is our
   1% risk rail.*
3. **Maximum Adverse Excursion (MAE)** stop — place just beyond each trade's historic MAE, or
   2.5% of price, whichever is smaller.

**Halal note:** all "leverage / margin / reserves-to-leverage-returns" discussion is **excluded**
— size from actual cash only. The *volatility-parity* and *% of account* ideas are the halal-safe
takeaways.

---

## §54.8 — Kaufman on stops & profit-taking; the Kase Dev-Stop — *Ch 23*
**Module: `execution/executor.py`, `strategy/promotion.py`**

**Kaufman's principles (authoritative):**
- Stop-loss & profit-taking are a **duel with price noise.** Because of noise, **stop-losses
  should trigger on the CLOSING price** (or, if detected intraday, **exit on the close** to catch
  the pullback); **profit-taking** should exit at the **intraday spike.**
- **Stops that work ADAPT TO VOLATILITY** (std-dev / ATR stops) — *not* fixed-dollar or
  fixed-percentage. **Trailing stops are more practical than initial stops.**
- Random-walk fact: (times a stop is hit) × (stop distance) ≈ constant, so **a stop must beat a
  random event to add value.** Closer stops get hit more.
- Table 23.5 heat-map (bonds, trending): a **volatility stop beat no-stop across almost all trend
  speeds** — a robust improvement *in trending markets*; noisy equity indices may differ. So
  **validate stop value per market/regime** (crypto ≠ bonds).
- **The honest caveat:** stops work most of the time **except when you need them most** — a price
  shock/gap fills you at the worst price. *Proximity risk*: many trend systems cluster the same
  stop, worsening the fill. ⇒ complement stops with **volatility stabilization** (§54.7).

**Cynthia Kase Dev-Stop (std-dev volatility stop) — NEW trailing-stop candidate:**
```
1. TR = true range of the past 2 trading days.
2. ATR = rolling average of that TR (20 daily / 30 intraday periods).
3. STDEV = std-dev of those TRs over the same window.
4. DDEV = ATR + f × STDEV       (f = 1, 2.06–2.25, or 3.20–3.50 — larger corrects for skew/more risk)
5. Long dev-stop = trade_high − DDEV      (trailing, applied to the best price of the trade)
```

**Profit-target menu:** longs `PT = entry + f × V` (V = ATR or annualized vol); **multiple target
levels / scaling out** (floor-trader technique: exit in thirds around the target to cut risk while
keeping upside) — folds into the existing partial-exit machinery.

**Crypto-fit:** confirms our §34.1 close-based-stop direction and gives two concrete
volatility-adaptive trailing stops (Kase Dev-Stop, ER-adaptive §54.6) to test against the Turtle's
fixed 2N stop.

---

## §54.9 — Ranking markets by trendiness (the ETH answer) — *Ch 23*
**Module: `analysis/regime.py`, `strategy/engine.py`, allowlist/portfolio selection**

*"Knowing which market to trade at the right time would clearly improve performance."* Kaufman
gives explicit **trendiness rankings** — this is the principled reframing of the open ETH
question: **don't statically keep-or-drop an asset — rank the allowlist by trendiness and deploy
capital to whatever is actually trending now.**

**Measures of trendiness (rank markets, trade the top):**
1. Correlation coefficient `r²` (only values > 0.25).
2. Sum of net moves over `n`, `2n`, `4n` days.
3. Slope of an `n`-day linear regression → $/day.
4. **Wilder's ADX** (only values > 0.20 / 0.25).
5. Average absolute price change.

**Wilder's Directional Movement / ADX (authoritative formulas — we already built ADX for the
Turtle; this grounds it and adds ADXR):**
```
+DM = High_t − High_{t−1};  −DM = Low_{t−1} − Low_t   (take the larger; the part outside yesterday)
PDI14 = PDM14 / TR14;   MDI14 = MDM14 / TR14          (Wilder smoothing, sc ≈ 0.071 = 1/14)
DX  = 100 × |PDI14 − MDI14| / (PDI14 + MDI14)
ADX = smoothed DX (sc ≈ 0.133 = 1/14 avg)
ADXR = (ADX_t + ADX_{t−14}) / 2
```
Ruggiero trend rules: **ADX > 25 = trending; ADX < 20 = consolidating** (matches our Turtle gate).

**Commodity Selection Index (CSI) — a market-allocation ranking:**
```
CSI = ADXR × ATR14 × K        (K folds in the per-market constants; for equities/crypto V=1, M=investment)
```
Rank products by CSI daily/weekly; **trade (or size up) the highest-CSI markets.**

**Crypto-fit + testability:** compute ADXR / ER / regression-slope per allowlist asset and either
(a) gate entries per-asset on trendiness, or (b) **allocate capital proportional to CSI/ER** so
BTC/ETH/PAXG get traded only when trending. This resolves the "ETH's losing trades are
load-bearing for sample size" tension: ETH participates when its trendiness clears the bar and
sits out when it doesn't — no arbitrary drop. Validate against the current static-allowlist sim.

---

## §54.10 — System-testing rigor (harness upgrade) — *Ch 21*
**Module: `sim/*`, `strategy/backtest.py`, `strategy/promotion.py`, `keel simulate`**

*"Numbers are like people; torture them enough and they'll tell you anything."* This chapter is a
direct upgrade to the milestone-6 validation harness.

- **Testing validates ideas — it is NOT for discovery.** Overfitting is the sin. Define
  **EXPECTATIONS in advance** (should it work daily? expected win %? risk?). If results differ
  from expectations, something is wrong. *(Wire "expected vs actual" into the sim report.)*
- **Pick ONE success metric up front** — the industry favors the **Sharpe / information ratio**.
  Do **not** optimize raw max profit (→ a couple of huge trades + many losses; stale performance
  looks acceptable). *(We already use Sortino/drawdown — keep it; add information ratio.)*
- **Parameter test ranges:** use **geometric / percentage increments** (1, 1.5, 2.2, 3.3, 5, 7.6…)
  not even spacing — otherwise the fragile low end is under-sampled and the long end dominates.
  **Deliberately limit ranges** (forces a-priori reasoning = validation, not fishing). Order
  parameters **by importance** (trend period before stop). Distinguish **continuous / discrete /
  coded(regime)** params — coded rule-switches break continuity and can't be auto-maximized.
- **In-sample / out-of-sample (OOS):** ~50% in-sample to build; **validate on OOS exactly ONCE.**
  Best practice = **alternating random time periods** fixed at the start (not first-50/last-50 →
  regime bias). **Report daily returns, not total profits** (else gaps between in-sample windows
  leak = cheating).
- **Walk-forward (step-forward) testing:** choose params on an in-sample window → apply forward to
  the next OOS window → roll. **Short-term bias:** windows too short (2yr) flatter fast models →
  use **~5yr** windows. **FEEDBACK is the cardinal sin:** *"You cannot fix anything once you've
  used the out-of-sample data"* — touching OOS then re-tuning guarantees overfit.
- **"Best" = MOST ROBUST params, not the maximum.** Look for a **broad plateau** of profitability
  on the 2D/3D result map (robust) vs a lone spike (fragile/overfit). Use seeding / multiple random
  starts to confirm the region.
- **Realistic transaction costs are mandatory** — too-low = fantasy, too-high = everything looks
  like a loss. Include slippage + commission. *(We already model this — keep calibrated to Coinbase
  fees.)*
- **Drawdown-probability sanity check:**
  ```
  P(drawdown ≥ P) = (1 − P/100)^(2μ/σ²)      μ = avg fractional return, σ = stdev of returns
  ```
  Use to check whether observed DD is within expectation. **"Systems should not systematically
  decay from day one"** — steady losses to the prior max-DD = model broken.
- **"Profiting from the worst results":** a *persistent* losing region is INFORMATION (e.g. short
  trends 1–11 days lose on noisy markets → use a fast trend for *entry timing* of a slower trend,
  or fade it). A *single-drawdown* loss (price shock) is NOT informative — it can't be predicted or
  engineered around.

**Crypto-fit + testability:** concrete additions to `keel simulate` — an expectations block,
information-ratio, geometric parameter sweeps, walk-forward with a hard OOS/feedback firewall, a
robustness-plateau view, and the drawdown-probability check. This sharpens (does not replace) the
existing G1/G2/G3 verdict + gap detectors.

---

## §54.11 — Trend-following works; the breakout profile (validates the Turtle) — *Ch 21*
**Module: `strategy/promotion.py` (per-class floors), portfolio deployment**

Kaufman tests **five trend methods** — Moving Average, Exponential Smoothing, Linearly-Weighted
Average, **Linear-Regression Slope**, and **N-Day Breakout** — across **17 markets over 20 years**
with identical rules. **Key conclusions, all directly on-point for the project:**

- **All five are profitable on average ⇒ "trend-following works. It's not the method, it's the
  market."** The market's *trendiness* matters more than the exact indicator.
- **N-Day Breakout (= the Turtle family) had the HIGHEST profit factor (2.59) and highest
  per-contract returns, but the FEWEST trades (~54 in 20yr ≈ 2.7/yr) and the HIGHEST risk**
  (initial risk = highest-high − lowest-low over the period). **This validates the Turtle's
  observed profile: low trade frequency + mostly-cash + high per-trade edge is *inherent* to
  breakout trend-following, NOT a bug.** The under-deployment finding is expected behavior; the
  lever is risk-per-trade / pyramiding (§26.1), not "the rule is broken."
- **Slower calculation periods are uniformly better; the best range often sits near the maximum
  tested (≈80 days)** — supports the Turtle's daily/long-horizon design over hourly.
- **Linear-Regression Slope** is the other strong performer (avg PF 1.94) — a **NEW candidate
  trend rule** worth testing on crypto (slope of an n-day regression as the entry/exit signal).
- **Robustness by market (`% of profitable tests`)** cleanly separates trending vs non-trending:
  EURUSD 87%, crude 85%, copper 83%, DAX 82%, USDJPY 81% … vs S&P 19%, gold 8%, wheat 14%.
  **~70% "% profitable tests" is the acceptance threshold.**

**Crypto-fit + testability (the ETH diagnostic, concretely):** run all 5 trend methods across
BTC/ETH/PAXG on the cached 5yr data and compute each asset's **`% profitable tests`**. An asset
below ~70% robustness is **structurally not a trend-trading candidate** — a data-driven,
non-arbitrary basis for the keep/drop/allocate decision that matches the ER (§54.1) and CSI (§54.9)
rankings. Confirms the **per-rule-class promotion floor** (trend-followers legitimately fail the
flat 55%-win bar; low-win/high-R:R is by design).

---

## Halal / compliance lens applied
- **Excluded (riba / not-spot):** all *leverage/margin/reserves-to-leverage* discussion (§54.7 —
  size from cash only); the **carry trade & spreads/arbitrage** (Ch 13, not extracted); interest-rate
  futures examples.
- **Long-only translation:** every short-side signal is kept only as an **exit / don't-buy** filter.
  The Parabolic SAR and other *stop-and-reverse* methods become **pure trailing-stops** (never
  reverse to short) — §54.6.
- **VIX / implied-volatility trading (§54.2 area, Ch 20):** no direct crypto analog in our data
  pipeline (a crypto DVOL exists but isn't wired), and VIX-index products are non-spot — kept as a
  **concept only** (volatility is mean-reverting; enter on low vol). Not a live method.
- **"Pips" → %/ticks/ATR** throughout, per KB convention.

## Deferred to v2 / recommended part-2 pass (NOT yet extracted)
High-value chapters worth a **part-2** extraction (crypto-appropriate, mechanical):
- **Ch 5 Event-Driven Trends** — N-Day Breakout variations + **swing filter** (a noise-filtered
  entry) + Point-and-Figure as a noise filter. *(Directly extends the Turtle.)*
- **Ch 8 Trend Systems** — bands/channels, comparison of trend systems, **selecting the right trend
  speed**, early exits.
- **Ch 22 Practical Considerations** — **extreme events / price shocks**, gambling *theory of runs*.
- **Ch 23 tail** — probability of success & **ruin**, compounding, equity trends, **optimal f**
  (money-mgmt sizing).
- **Ch 24 Diversification** — changing **correlations** + **volatility stabilization** (portfolio
  rail; skip the MPT/mean-variance parts — declined as riba/quant-stack per spec §10).
- **Ch 12 Volume** (crypto has real volume) and **Ch 19 Elder Triple-Screen** (clean mechanical
  multi-TF) — medium value.

**Deferred/subjective (consistent with prior KB judgment):** Ch 10 Seasonality, Ch 11 Cycle
Analysis (maximum entropy / trig regression = prediction-oracle-adjacent, overfit), Ch 15 pattern
recognition (mostly 24/7-crypto-N/A gaps/weekdays), Ch 18 Market Profile (low priority per §35).

**Excluded (halal / no-oracle / scope):**
- **Ch 13 Spreads & Arbitrage / the Carry Trade** — riba + requires shorting one leg (not long-only
  spot). Excluded like Source 18.
- **Ch 14 Behavioral: Elliott Wave, Fibonacci projections, Gann Time/Space, Financial Astrology** —
  subjective/overfit (Elliott/Fib → v2 with harmonics) or pseudoscience (astrology, Gann) → excluded
  under the no-oracle principle (§6.4). *(Its "Measuring the News / Event Trading" belongs to the
  deferred LLM feature, not the deterministic core.)*
- **Ch 6 ARIMA** — a prediction oracle; the project explicitly declined ARIMA (spec §10).
- **Ch 20 tail: Expert Systems / Fuzzy Logic / Neural Networks / Genetic Algorithms / hedge-fund
  replication** — non-reproducible / black-box; conflicts with the deterministic-backtestable core
  and the §5 LLM-asymmetry (LLM may propose→backtest, never decide). Not for the live loop.

## Discarded (no agent value)
Historical narrative (pre-1980 hand-charting), TradeStation/Excel UI how-to and companion-website
program pointers (`TSM *`), futures-specific plumbing (back-adjusted/continuous-contract roll
mechanics, open-interest, margin/exchange-governor detail), and stock/commodity-specific examples
with no crypto analog.

## Status / saturation
**The opposite of saturated — this is a foundational, multi-session source.** Part 1 (this file)
already delivers: an **ER trend-diagnostic** (§54.1), a **crypto volatility/stop model** (§54.2–4,
§54.6, §54.8), **KAMA + linear-regression-slope + volatility-breakout** as new candidate rules
(§54.3, §54.5, §54.11), a **market-ranking/CSI answer to the ETH question** (§54.9), and a concrete
**harness-rigor upgrade** (§54.10) — plus independent **validation that trend-following works and
the Turtle's low-frequency/high-risk profile is by design** (§54.11). Recommend a **part-2 pass**
over Ch 5 / 8 / 22 / 23-tail / 24 (listed above). More crypto-appropriate *technical* strategy
books remain welcome; this one is the anchor. See [[halal-cb-autotrade-project]].
