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

# Part 2 — Ch 5 (Event-Driven Trends), Ch 8 (Trend Systems), Ch 22 (Extreme Events), Ch 23-tail (Ruin / Optimal f), Ch 24 (Diversification)

## §54.12 — The swing filter: a time-independent trend-rule class — *Ch 5*
**Module: `strategy/rules/` (NEW candidate), `analysis/levels.py` (swing pivots)**

A *price swing* is a sustained move ≥ a threshold (the **swing filter**), expressed as a
**percentage of price** (more robust across price regimes than a fixed amount). A swing chart
**ignores time** — only new swing highs/lows matter; sideways action inside a swing produces no
signal.
```
Minimum swing value:  MSV_t = p × price      (use the PRIOR day's MSV_{t-1} to avoid lookahead; p e.g. 0.25%–4%)
Conservative entry:   buy when the current upswing high exceeds the prior upswing high
Active entry:         buy as soon as a new upswing is recognized (first reversal > swing filter)
```
**Why this is a genuinely different rule class (the robustness insight):** a moving average "has an
agenda" — price must keep advancing or the trend is lost; a **swing method lets price move sideways
or stand still within a trend** and signals immediately on an event (no lag). Robust "at the cost of
higher risk." Related: *Keltner's Minor Trend Rule* (buy when the daily trend trades above its
recent high), *pivot points* (n-day high/low, inherent lag), and *Wilder's Swing Index* (`SI`,
combines close-close / open / prior-strength scaled to ±1).

**Crypto-fit + testability:** a percentage swing-filter rule is a new long-only candidate distinct
from both the MA-trend and the Donchian-breakout families — worth backtesting on crypto where
sideways consolidations inside trends are common. Percentage-based ⇒ scales across BTC's price eras.

## §54.13 — N-Day breakout, Donchian 4-week / 40-20, adaptive-N — *Ch 5*
**Module: `strategy/rules/turtle_breakout.py` (extends the built rule), `strategy/backtest.py`**

```
Aggressive:    Buy when today's HIGH  > highest high of the past N days
Conservative:  Buy when today's CLOSE > highest high of the past N days   (confirms later; our close-based bias, §34.1/§54.8)
```
- **N < 5 = event-driven/fast; large N = much greater risk** (initial risk = the channel width =
  entry − opposite N-day extreme). This is *why* the Turtle under-deploys and carries high per-trade
  risk — inherent, matches §54.11.
- **Donchian 4-Week Rule** (~20 trading days) and **Donchian 40/20 Channel Breakout** (40-day entry
  / 20-day exit, asymmetric — the earliest recorded N-day breakout, "very much like the Turtles").
- **Adaptive N** (shrink the window when volatility rises): `N_t = N_initial × (V_normal / V_current)`
  — classified as an adaptive technique (ties to Ch 17, §54.5).
- **Testing (Table 5.7):** all calc periods but one were profitable; **best N differs per market**;
  **`% profitable tests`** varies enormously (crude 95%, BofA 100%, gold 21%, Amazon 26%) — the
  robustness measure (§54.11). Don't pick N by max net profit (best-N jumps across sub-periods).
- Weekly Price Channel = slower/higher-risk; Stridsman *Dynamic Breakout System* (std-dev-based,
  orders placed one day ahead).

**Crypto-fit:** confirms and extends the existing daily Turtle; adaptive-N and the 40/20 asymmetric
variant are cheap sweeps to add to the harness.

## §54.14 — The full ORIGINAL Turtle rules (authoritative spec) — *Ch 5*
**Module: `strategy/rules/turtle_breakout.py`, `strategy/money_mgmt.py`, `execution/guards.py`**

The canonical spec our Turtle is based on. Two systems, capital split equally:
```
System 1 (S1):  enter long when intraday HIGH > 20-day high;  exit when intraday LOW < 10-day low.
   FILTER RULE:  SKIP the S1 entry if the PREVIOUS S1 entry was profitable (whether or not it was
                 taken); take it only if the prior S1 trade LOST ≥ 2L.   ← nuance our build may lack
System 2 (S2):  enter long when HIGH > 55-day high;  exit when LOW < 20-day low.   (no filter)

L (= "N") = 20-day average-off true range:  L_t = (19·L_{t-1} + TR_t)/20 × BPV     (ATR in $ terms)
```
- **Risk control:** stop = **2L from entry**; exit on the stop, an S1/S2 reversal, or a **2% portfolio
  loss** (2L is sized to equal 2% of the portfolio).
- **Position size:** `1 unit = (1% investment)/(L × BPV)` — equalize `L×BPV` across markets
  (**volatility parity**, §54.7).
- **Position limits (correlation caps — the authoritative grounding of our correlation-sizing rail):**
  single market **4 units**; closely-correlated markets **6 units**; less-correlated **10 units**; any
  net direction **12 units**.
- **Pyramiding (grounds §26.1):** add 1 unit (or ½ unit) per **½L** favorable move from the actual
  entry; **max 5 units**; move all stops to 2L from the most recent entry (total risk ≈ 2L×contracts).
- **Portfolio risk management:** for every **10% portfolio drawdown, cut position size 20%**; add 10%
  back per 6⅔% recovery. (De-facto loss limit was 50%.) — grounds the account-DD breaker rail.
- **Result (copper, 30yr):** the **slow S2 (55/20) = steady 30-year profits** (typical long-term
  trend-following); the fast S1 = profits only early. **Slower = better** (matches §54.11).

**Crypto-fit + action:** two concrete adds to the built Turtle — (1) the **S1 profitable-trade filter**
(skip the next breakout if the last one won; take it only after a ≥2L loss — a shakeout catcher that
cuts over-trading), and (2) the **correlation-based unit caps** as the principled form of the
correlation-sizing rail. Both harness-testable. Long-only: keep long entries/exits only.

## §54.15 — Bands & channels; reliability-vs-delay; entry timing — *Ch 8*
**Module: `strategy/rules/` (band rules), `execution/executor.py` (entry/exit bands, timing)**

A band around a trendline slows trading and cuts false signals at the point of greatest indecision
(the trend change) *without* altering the trend profile.
```
Keltner Channel:  AP=(H+L+C)/3, MA=avg(C,10), UB=MA+AP, LB=MA−AP   (use ATR for AP today)
Percentage band:  BU=(1+c)·MA,  BL=(1−c)·MA
General volatility band (scalable by factor s):
   B = MA ± s·c·MA   (% of trend)   |   ± s·c·price   |   ± s·ATR_{t-1}   |   ± s·stdev_{t-1}
Bollinger Bands:  20-day MA ± 2σ(price)   (≈87% band since prices aren't normal)
```
- **Long-only rules for bands:** buy when the close crosses **above the upper band**; **exit when
  price returns to the trendline (center)** → risk limited to half the band width. **Separate
  entry/exit bands** (wide entry, narrow exit) = enter slow, exit fast.
- **Bollinger's own use is MEAN-REVERTING** (fade the band), and he requires volume/breadth
  confirmation — a downside penetration on **non-increasing volume + non-negative breadth** = a valid
  buy. Very-low vol forecasts high vol and vice-versa (VIX-like).
- **Reliability-vs-delay tradeoff (central):** wider band ⇒ more reliable, fewer signals, but delayed
  entries, smaller average profit, greater per-trade risk (⇒ smaller size / more capital).
- **Entry timing — DON'T "improve" naively:** delaying entry to the next open improved the fill ~75%
  of the time **but LOWERED total profit** — because breakouts that never retrace = missed trades (the
  fat tail). A safe contingent entry: **"buy after prices reverse by 0.50×ATR, or enter on the next
  close."** The calculation *period* is the single most important choice — more than the method.

**Crypto-fit:** the ATR/stdev **volatility band** is a clean long-only candidate (buy upper-band
close, exit to center); Bollinger-as-mean-reversion is a candidate for the **high-noise/low-ER assets**
where trend rules fail (§54.17). Crypto has real volume → the volume-confirmed Bollinger buy is viable.

## §54.16 — More single-trend systems: Volatility System, TRIX, Raschke First Cross — *Ch 8*
**Module: `strategy/rules/` (candidates), `analysis/indicators.py`**

- **Volatility System (Bookstaber):** `Buy if close rises > k × ATR_{t-1} from the prior close`
  (k ≈ 3). = the volatility-breakout of §54.3, independently confirmed.
- **TRIX (triple exponential smoothing):** smooth ln(price) three times with the same constant
  (≈6-day); buy when the TRIX trendline rises 2 consecutive days (or crosses its 3-day signal line);
  smooth but low-lag. A candidate momentum-trend indicator.
- **Raschke "First Cross" (buy the FIRST pullback in a new trend — long-only viable):**
  ```
  osc = fastMA − slowMA;  trend = MA(osc)
  B1: osc_{t-1} > trend_t AND osc_t ≤ trend_t   (oscillator crosses its trend, turning down)
  B2: low_t > low_{t-1}                          (current bar's low is rising)
  B3: → BUY                                       (a pullback within an up-move, re-entering long)
  ```
  Selectivity: the start of a trend is a unique, strong event; this waits for the first impulse to
  exhaust and enters the resumption. Pairs naturally with a longer trend filter.

**Crypto-fit:** First-Cross is a *disciplined* pullback-in-uptrend entry (unlike the refuted
dip-buyers, it requires an established trend first) — a candidate that could time Turtle entries.

## §54.17 — Kaufman's Strategy Selection Indicator: ER vs profit factor — *Ch 8 / 23*
**Module: `analysis/regime.py`, `strategy/engine.py`, allowlist/portfolio selection** — *the empirical backbone for §54.1/§54.9*

Qualify each market by its **noise (Efficiency Ratio, §54.1)** to decide *which strategy* fits:
**low noise (high ER) → trending strategies; high noise (low ER) → mean-reversion.**
```
ER_t = |C_t − C_{t−n}| / Σ|C_i − C_{i−1}|      (65-day window used for selection)
```
- **Empirical (Table 23.7 / Fig 23.12, 1990–2011, wide market set):** a **clear positive
  relationship between average ER and profit factor.** Highest: Eurodollar (ER 0.18, PF 2.43), AAPL
  (0.15, PF 2.28), Eurobund, crude (0.14, PF 2.12). Bottom (PF < 1, net losers on a trend system):
  MRK 0.71, gold 0.94, wheat 0.99, MSFT 0.88, GE 0.88, **S&P 1.16**.
- **Rule:** markets with **profit factor < 1.0 are NOT trend candidates → treat with mean-reversion,
  or don't trade.** Farthest up-and-right = the best trend markets.

**Crypto-fit (the concrete ETH answer, now empirically grounded):** compute each allowlist asset's
65-day average ER and its trend-system profit factor on the cached 5yr data; **trade the Turtle only
on assets whose ER/PF clears the bar, and route low-ER (noisy) assets to a mean-reversion rule (or
stand aside).** This is Kaufman's own named method — the authoritative backbone under the §54.1 ER
diagnostic, §54.9 market-ranking, and §54.11 `% profitable tests`.

## §54.18 — Probability of ruin; required-gain asymmetry; optimal f — *Ch 23*
**Module: `sim/metrics.py`, `sim/report.py`, `strategy/money_mgmt.py`, `strategy/promotion.py`**

**Risk of ruin (equal wins/losses):** `R = [(1−A)/(1+A)]^c`, where `A = 2P−1` (trader's advantage,
P = win rate) and `c` = capital in units. E.g. 60% win, $10k units: A=0.20, R=(1/3)^c → 1 unit = 33%,
2 units = 11%. **More capital or more edge ⇒ lower ruin.** With a profit goal G the formula extends.

**Risk of ruin — UNEQUAL wins/losses (the trend-follower case, spreadsheet-ready, Vince/Griffin):**
```
AvgWin% = |AvgWin/Investment|;  AvgLoss% = |AvgLoss/Investment|
Z = (ProbWin·AvgWin%) − (ProbLoss·AvgLoss%)
A = sqrt( (ProbWin·AvgWin%)² + (ProbLoss·AvgLoss%)² )
P = 0.5·(1 + Z/A)
Risk of Ruin = ((1−P)/P) ^ (MaxRisk / A)
```
(Table 23.8: 40% win, $400/$200, 25% max-risk ⇒ ROR 0.63%; halving capital ⇒ 7.9%→28%; ROR rises
FASTER than capital falls, and jumps as avg-win shrinks or max-risk tightens.)

**Required-gain asymmetry:** `Required gain = 1/(1−PercentLoss) − 1` — a 50% loss needs a **100% gain**
to recover (the case for preservation-first, §33/Sortino).

**Optimal f** = the optimal fixed fraction of the account to risk per trade (maximize capital at risk
while avoiding ruin). Two levels: (1) % of portfolio at risk vs cash, (2) size per instrument. Optimal
f is famously **too aggressive**; use **fractional f** in practice. Monte-Carlo (shuffling return
blocks) is a severe robustness test, but for trend-following moving the end-of-trend loss elsewhere is
"unfair" (the big loss is intrinsically tied to the prior trend via lag).

**Crypto-fit + action:** add **risk-of-ruin** (unequal-wins form) as a `sim` metric alongside the
verdict — a direct check the Turtle's 1%-risk sizing keeps ROR ≈ 0. Keep sizing at **fractional f /
the 1% rail** (never full optimal f). The required-gain asymmetry is the math behind the preservation bar.

## §54.19 — Entering & compounding a position — *Ch 23*
**Module: `strategy/money_mgmt.py`, `execution/executor.py`, `execution/guards.py`**

- **Averaging INTO a position** (spacing entries over a few days) generally **improves trend-following**
  (replaces one uncertain entry with a stable average); on noisy markets, spacing ~2 days turned S&P
  from negative to positive (Table 23.9). Total entry time should scale with the holding period (no
  sense for a fast trend).
- **Waiting for a better price (min-threshold + max-window):** wait for a pullback of a set threshold
  after the signal, **but enter at market by the close of day N if no pullback occurs** (the window is
  mandatory or you miss the fat tail). Cut trades ~40%, turned gold loss→profit; **noisy markets benefit
  most** (Table 23.10). Ties to Raschke First Cross (§54.16).
- **Compounding = pyramid on PROFITS ONLY** (scale in on new-high profits, min days between adds, max
  ~5 units): improves total profit AND profit factor as spacing grows (Table 23.12). Structures
  (Fig 23.14): upright pyramid (scaled-down adds, safest) / inverted pyramid (equal adds, max leverage,
  fragile) / reflecting pyramid. **NEVER average down (add on losses)** — it helped only a persistent
  uptrend (AAPL) and hurt everything else (Table 23.13). **Confirms the no-martingale rail + §26.1
  pyramiding-on-winners.**
- **Equity-trend / reserves management:** increasing size as equity rises leaves you fully invested at
  the top when losses begin (a 100% gain then 50% loss nets flat — the volatility tax). Instead **hold
  the investment constant and accumulate profits as reserves**, so proportionately more equity trades
  during losing phases (counter-cyclical); periodically **redistribute back to the original
  margin/reserve ratio**. Trading on the equity curve (exit when the equity MA turns down) is
  inconsistent — hurts the most-trending market; hypothetical equity always flatters → caution.

**Crypto-fit:** these are the money-mgmt levers for the Turtle's **under-deployment** (§54.14) —
pyramid-on-profits (already scoped §26.1) and scale-in entries; keep the no-average-down rail absolute.

## §54.20 — Extreme events / price shocks: crisis management — *Ch 22*
**Module: `execution/guards.py` (NEW price-shock detector + crisis mode), `agent.py`, `strategy/backtest.py`**

Price shocks are the most likely cause of catastrophic loss and the biggest gap between backtest and
reality. Key word = **UNEXPECTED** (Lehman evolved over days = not a shock; 9/11 = a shock).
- **During a shock, diversification FAILS — correlations go to 1** (money moves, not fundamentals;
  flight to safety). Confirmed by 2008.
- **Backtests can't identify shocks** and treat them as normal, so the "best" parameters are often the
  **greatest beneficiaries of unpredictable shocks** → OOS/live is never as good as sim (humility,
  reinforces §54.10).
- **Shocks don't always hurt trend-followers** — a shock can favor the existing position (9/11 favored
  existing shorts; 2008 = trend-followers' best year, already short). Against you ⇒ filled at the worst
  price. **Prices reverse after the shock is absorbed** (extreme → longer reversal).
- **Identify a price shock (mechanical):** a **1-day trading range ≥ ~5 × recent ATR**.
- **Crisis-management override (well-defined rules, invoked on shock detection):**
  1. **Windfall profit** from the shock → **exit / reduce** the position (take it; volatility always
     rises after a shock).
  2. **Large loss** from the shock → **HOLD, expecting the reversal** (may take days).
  3. **Adequate reserves** → add on peak volume or once volatility declines.
- **System disconnect:** after a shock, moving averages go **out of phase** with the market (catching up
  to an event long past). Dogmatically following the trendline direction post-shock = out of phase with
  reality → **crisis mode must override the main strategy; reinitialize trends at the new price level
  once volatility drops.**

**Crypto-fit (high value):** crypto has frequent shocks (flash crashes, exchange/de-peg events, 20%+
days). A **price-shock detector (1-day range ≥ ~5×ATR) + a crisis-management mode** is a genuinely new
rail/behavior for `keel` — pause new entries, take windfalls, hold-or-exit per rule, and re-baseline
the trend after volatility subsides. Long-only: "hold shorts" → "take windfall / hold-for-reversal on longs."

## §54.21 — Theory of runs: a trending-vs-mean-reverting diagnostic — *Ch 22*
**Module: `analysis/regime.py` (per-market run profile), `sim/metrics.py` (information ratio)**

Gambling theory (transaction costs = the house edge; only money management changes the odds). Run
probabilities: `P(run of length n) = (1/2)^(n+2)`; average length of runs longer than n is `n+2`.
- **Applied to markets (Table 22.2, 15 markets):** compare each market's **actual up/down run
  distribution vs the random `(1/2)^(n+2)` expectation.** **DAX = far MORE 1-day reversals than random
  (noisy, mean-reverting bias); Eurodollar = far FEWER short reversals + a fat tail (trending bias).**
  ⇒ a **per-market run-distribution profile** classifies a market as trending or mean-reverting — a
  cheap diagnostic that complements ER (§54.17) and CSI (§54.9). Application: **"after a signal, wait
  for the noisy market to reverse before entering; enter the trending market immediately."**
- **Martingale (double-down on losses)** worked spectacularly on trending Eurodollar (capped info
  ratio 8.28) but failed on noisy S&P (1.04). **⛔ EXCLUDED regardless — doubling down = averaging into
  a loser, which VIOLATES the no-martingale rail (§5.1) and the risk-only-increases-via-gate principle.
  Logged as tested-and-rejected-by-rail.** Anti-Martingale (add on wins) = pyramiding, already
  sanctioned (§26.1/§54.19).
- **The information ratio (`Net P&L / annualized StDev of daily P&L`) is "the best measure of results"**
  — used throughout; reinforces adding it to the sim (§54.10).

**Crypto-fit:** add a per-asset run-distribution profile as a second, orthogonal trend/mean-revert
classifier (with ER); it directly informs entry-timing (wait-for-reversal on noisy assets).

## §54.22 — Diversification & portfolio allocation — *Ch 24*
**Module: `strategy/money_mgmt.py`, `execution/guards.py` (correlation rail), `sim/*`** — *skip MPT (declined)*

- **Diversification reduces *systematic* (not market) risk;** benefit is greatest across *unrelated*
  groups with *unrelated* decision methods. Fig 24.1: risk falls fast from 1→4 assets then flattens;
  ideal (independent) → 1/n, but **real markets hit a ~50% floor. ~4 assets captures most of the
  benefit.** ⇒ a narrow **BTC/ETH/PAXG allowlist is already near the practical floor**; adding
  correlated alts adds little (reinforces §51.1). **PAXG (gold-backed) is the genuine diversifier**
  vs the highly-correlated BTC/ETH.
- **Multiple-strategy diversification (less correlated by *functional attribute*):** trend-following /
  mean-reverting / spreading / fundamental / carry. **All trend-followers are correlated** (they extract
  from the same moves). ⇒ **pairing the Turtle with a mean-reversion rule (on high-noise/low-ER assets,
  §54.17) = genuine strategy diversification.** (Spreading/carry = ⛔ riba/shorting.)
- **Balanced (equal-risk) sizing — 3 ways:** equal-dollar / equal-risk by annualized StDev / equal-risk
  by **ATR** (best when H/L/C available; StDev if only closes). Equal-dollar concentrates risk on
  volatile names — avoid. Table 24.1: `shares = scaled(inverse-vol %) × investment / price` =
  **volatility-parity sizing across the allowlist** (grounds §54.7).
- **Changing correlations:** a single long-period (or rolling-average) correlation **hides short-term
  extremes** — in 2008 everything → ±1. **Use a 60-day ROLLING correlation** (Fig 24.3). The only ways
  to avoid the correlation-→1 risk: **(1) be out of the market as much as possible, (2) cap portfolio
  leverage, (3) use uncorrelated strategies.** ⇒ the correlation-sizing rail should use a **rolling**
  correlation, and the Turtle's **mostly-cash** profile is itself a crisis hedge (a feature, not only a bug).
- **MPT / mean-variance:** `σ²_R = Σw_i²σ_i² + ΣΣw_i w_j·corr_ij·σ_iσ_j`; Excel-Solver maximizes the
  info ratio s.t. `Σw=1, w≥0`. **⛔ DECLINED (spec §10 — CAPM/Rf = riba + quant-stack).** The
  **long-only constraint (w≥0)** and the **info-ratio objective** are compatible and adoptable; the
  optimizer is not.
- **GASP (Kaufman's genetic-algorithm allocation) — the crucial insight for keel's sim (adopt the idea,
  not the GA):** MPT/mean-variance **fails on active-trading returns because they are INTERMITTENT** —
  zero on days with no position. A strategy in the market only 10–20% of the time shows an artificially
  LOW StDev (half the days are zeros) ⇒ **looks less risky ⇒ gets over-allocated**, and covariance over
  non-trading days is meaningless. **⇒ Do NOT compute whole-period Sharpe/StDev/covariance on the
  Turtle's mostly-cash returns — it understates risk and distorts allocation.** GASP's objective is
  `OF = AROR / σ_D`, where **σ_D = StDev of daily DRAWDOWNS (semivariance)** = σ(E_high − E_current),
  counting only days not at a new equity high. **This is essentially keel's existing Sortino/drawdown
  bar — so Ch 24 independently VALIDATES using drawdown/semivariance over Sharpe.** (Risk via
  regression residuals is rejected — it penalizes prolonged *gains*.)

**Crypto-fit + action:** (1) confirm the narrow allowlist + PAXG-as-diversifier; (2) make the
correlation rail **rolling (60-day)**; (3) **do not report a whole-period Sharpe for the Turtle** — the
mostly-cash days distort it; keep the drawdown/Sortino verdict (now textbook-endorsed) and consider a
mean-reversion partner rule for high-noise assets.

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
- **Martingale / doubling-down / averaging-into-losers (§54.19, §54.21, Ch 22–23):** ⛔ EXCLUDED —
  violates the no-averaging-into-losers & no-martingale rails (§5.1) and the "risk may only increase
  via the gate" principle. It tested well *only* on a strongly-trending market; rejected regardless.
  Anti-Martingale (adding on winners) = pyramiding, which IS sanctioned (§26.1/§54.19).
- **MPT / mean-variance optimization (§54.22, Ch 24):** ⛔ DECLINED (spec §10 — CAPM/`Rf` = riba +
  quant-stack). Adopt only its **long-only constraint (w≥0)** and **info-ratio / drawdown objective**;
  not the optimizer. Genetic-algorithm allocation (GASP) itself = non-reproducible (excluded like the
  Ch 20-tail black-box methods); only its *insight* about intermittent-return distortion is adopted.
- **"Pips" → %/ticks/ATR** throughout, per KB convention.

## Extraction status by chapter
- **Part 1 (extracted):** Ch 1 (noise/ER) · Ch 17 (adaptive/KAMA) · Ch 20 (volatility & stops) ·
  Ch 21 (system testing) · Ch 23-core (risk/stops/market-ranking).
- **Part 2 (extracted — this file, §54.12–§54.22):** Ch 5 (swing filter, N-day breakout, full Turtle
  rules) · Ch 8 (bands/channels, single-trend systems, Strategy Selection Indicator) · Ch 22 (extreme
  events / crisis management, theory of runs) · Ch 23-tail (ruin, compounding, optimal f) · Ch 24
  (diversification, rolling correlation, GASP insight).
- **Remaining (optional part-3, medium value):** **Ch 12 Volume** (crypto has real volume) and
  **Ch 19 Elder Triple-Screen** (clean mechanical multi-TF).

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
**The opposite of saturated — this is the foundational anchor source.** Parts 1 & 2 (this file,
§54.1–§54.22) together deliver a coherent build agenda for the project's open problems:
- **The ETH keep/drop/allocate answer (now triangulated three ways):** ER trend-diagnostic (§54.1),
  Kaufman's **Strategy Selection Indicator** with the empirical ER→profit-factor relationship (§54.17),
  market-ranking / CSI (§54.9), the `%-profitable-tests`≥~70% robustness bar (§54.11), and the
  run-distribution classifier (§54.21) — **trade the Turtle only where trendiness clears the bar; route
  low-ER/noisy assets to mean-reversion or stand aside.**
- **The crypto stop/risk model:** ATR stops/targets, low-vol entry filter, high-vol exit/reset (§54.2–4),
  three volatility-adaptive trailing stops (§54.6, §54.8), risk-of-ruin (§54.18), volatility-parity
  sizing (§54.7, §54.22), and a **price-shock detector + crisis-management mode** (§54.20).
- **New candidate rules:** KAMA adaptive-trend (§54.5), linear-regression-slope & volatility-breakout
  (§54.3, §54.11, §54.16), the **swing filter** (§54.12), Raschke First-Cross pullback (§54.16), and a
  **mean-reversion partner** for high-noise assets (§54.22).
- **Authoritative Turtle spec** (§54.14) adds two concrete build items to the shipped rule: the **S1
  profitable-trade filter** and **correlation-based unit caps**; §54.19 gives the **pyramiding /
  scale-in** levers for its under-deployment.
- **Harness rigor:** walk-forward + OOS/feedback firewall, robustness-plateau, drawdown-probability,
  information ratio (§54.10); and Ch 24's key correction — **do NOT use whole-period Sharpe/covariance
  on the Turtle's intermittent mostly-cash returns; the drawdown/Sortino verdict is textbook-endorsed**
  (§54.22).

Only **Ch 12 (Volume)** and **Ch 19 (Elder Triple-Screen)** remain as an optional part-3. More
crypto-appropriate *technical* strategy books remain welcome; this one is the anchor for the next
build phase. See [[halal-cb-autotrade-project]].
