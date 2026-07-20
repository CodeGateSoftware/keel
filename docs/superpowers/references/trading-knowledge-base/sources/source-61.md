[← Knowledge Base index](../README.md)

## Source 61 — "Pattern Cycles: Mastering Short-Term Trading With Technical Analysis" + "Bonus Report 2: 3 Swing Trading Examples" (Alan Farley, Traders' Library, 2000, 16pp + 16pp)

> **Two free companion lead-magnets from the same author, filed as one source.** `patterncycles.pdf`
> (16pp) is the main essay — a crowd-psychology narrative of markets rotating through **Bottoms →
> Breakouts → Trends → Highs → Tops → Reversals → Declines** and back. `alanfarley.pdf` ("Bonus Report
> 2") is a companion **3 worked swing-trade examples + a ~70-term glossary**. Both are pre-print-era
> Traders' Library promo material (© 2000) for Farley's paid book/newsletter — no backtests, no
> statistics, purely descriptive/discretionary pattern reading illustrated on **5-minute, 60-minute,
> 120-minute and 3-hour charts**. Farley is a day/swing trader operating in minutes-to-hours; `keel`
> holds ~21–24 days on daily bars. That mismatch does most of the filtering work here — see §61.9.
>
> Against the anchor sources (§54 Kaufman, §34 T3 Live, §25 CFI, §55/§57), this is **heavily saturated**:
> candlesticks, MACD, Fibonacci retracement/extension, double tops/bottoms, H&S, triangles, round-number
> levels, multi-timeframe confluence and volume-confirmation are all already in the KB and mostly already
> in code. **One item earns real new-agent-module value** (§61.3, a volatility-contraction breakout-timing
> filter); the rest reinforces, refines at the margins, or is excluded by direction/timeframe/no-oracle.

---

### 61.1 The Pattern Cycles phase-rotation model → `analysis/regime.py` (reinforces, does not extend)

*(patterncycles.pdf, throughout)*

The book's organizing idea: price doesn't trend forever in either direction, so markets cycle through
named phases — a bottom forms, breaks out, trends, makes new highs, tops, reverses, and declines back
into a new bottom — driven by a fear/greed feedback loop (*"falling price ignites fear... fresh rallies
awaken greed, inviting momentum players to become greater fools"*).

- **This is a qualitative taxonomy, not a computable classifier.** It names phases (bottom/breakout/
  trend/top/decline) the way a human chart-reader would, but gives no measurable threshold for *when* an
  asset has entered "trend" phase versus "range" phase beyond eyeballing chart shape. `regime.py` already
  has a phase concept (run/pullback, choppy = no-trade) and an **ADX>25 gate** (§25.1) that *is*
  measurable.
- **Reconciliation with §54.1/§54.9/§54.17/§54.21 (the market-ranking / ER stack):** Kaufman's Efficiency
  Ratio, ADXR/CSI ranking, the run-distribution trending/mean-revert classifier, and the empirical
  ER→profit-factor Strategy Selection Indicator are all **quantitative, backtestable, and already answer
  the "is this asset worth trading at all" question** with a number and a threshold. Farley's phase model
  describes the *same underlying phenomenon* (markets alternate trend and range) from a narrative,
  discretionary angle with no threshold, no sample, and no measurement. **It does not refine §54's
  answer — it does not subsume it either; it is simply a lower-resolution restatement of the same
  observation** ("bulls and bears fight for control" ≈ "efficiency ratio measures directional efficiency
  vs noise"). **Verdict: §54's ER/ADXR/CSI stack remains the authoritative, sole mechanism for the
  asset-trendiness gate. Farley contributes nothing actionable to that open question** — it is
  corroborating color, not a new tool.
- The **8-phase vocabulary itself (Bottoms/Breakouts/Trends/Highs/Tops/Reversals/Declines)** has no direct
  code mapping worth building — each phase, individually, decomposes into patterns already covered
  elsewhere in the KB (double bottoms §16.1/§21.1, breakouts §23.1/§27.1/§54.11, trend continuation
  §54.16, tops/reversals §21.1/§24.1, Elliott declines → excluded, §61.6).

### 61.2 Double-bottom variants (Adam & Eve, Big W) → reinforces §34.3 / §55.4 / §24 (deferred geometry)

*(patterncycles.pdf, "Bottoms")*

- **Adam & Eve**: a double bottom where the first low is sharp/spiked ("Adam") and the second is slower
  and rounded ("Eve"). Purely a **shape-reading refinement of the double-bottom pattern** we already have
  fuller rules for (RSI-extreme + shallow-pullback>0.382 + divergence, §16.1/§21.1). The shape distinction
  (sharp vs rounded) is discretionary curve-reading, same class as the subjective chart-pattern geometry
  already deferred to v2 (README, "Adam and Eve Top" analog already logged and excluded at §55.6/§24.5).
- **Big W**: an elaborated double-bottom map — center-pivot retracement (38–62% of the decline), a
  "Turtle Reversal" (price briefly violates the last low then snaps back above it — a **stop-hunt/shakeout**,
  the exact behavior §34.1's close-based-stop-confirmation was built to filter out), then re-entry at the
  first pullback to the center pivot after the next breakout leg. **Structurally identical** to
  §34.3's "first retest of a validated level = best entry" combined with the pullback-continuation family
  (§2.1/§7.1). **Reinforcement, not new.** The "Turtle Reversal" naming is coincidental — it has nothing
  to do with the Donchian/Turtle system (§27.1/§54.14); it just means a wick-through-then-recover, which
  is exactly what a close-based stop (§34.1) is designed to survive.

### 61.3 ⭐ Volatility-contraction breakout precursor (NR7 / "Silent Alarm") → candidate for `analysis/indicators.py` + `analysis/regime.py`

*(alanfarley.pdf glossary; the mechanism is implicit in patterncycles.pdf's "Breakouts" section too — congestion
narrows before a thrust)*

The glossary defines two related, **mechanically specifiable** concepts absent from the codebase and from
every prior source:

- **Narrow Range Bar (NR)** — a bar whose high-low range is smaller than the prior bar's.
- **NR7 / NR7-2** — the **narrowest high-low range of the last 7 bars** (NR7), or two such bars in a row
  (NR7-2): *"a low volatility time-price convergence that often precedes a major price expansion."*
- **Silent Alarm** — a rare **high-volume narrow-range bar**, read as "flags an impending breakout."
- **Coiled Spring** (glossary) — the trading strategy built on this: *"executes a position at the
  interface between a range-bound market and a trending market."*

- → **This is the one genuinely new, computable idea in the source.** It is a volatility-*contraction*
  filter — the range-compression precursor to expansion, conceptually adjacent to a Bollinger-Band squeeze
  but defined purely from bar ranges (no indicator dependency), and it is **directly computable on our
  daily bars** — nothing about NR7 requires an intraday timeframe. It complements, rather than duplicates,
  the existing volatility toolkit: ATR/ADX/Efficiency Ratio (§54.1, §25.1) measure *how much* an asset is
  trending right now; NR7 flags *when a range is about to end*, i.e., a timing filter for **when to check
  the breakout condition**, not a replacement for the ADX/Donchian entry gate itself.
- **Candidate wiring:** a `range_contraction(lookback=7)` flag in `analysis/indicators.py` (7-bar rolling
  min of `high-low`, is current bar the min) feeding either (a) a breakout-timing gate in
  `strategy/rules/` — only arm the Donchian breakout watch when NR7 has recently fired, cutting false
  starts inside choppy ranges — or (b) a CTS confluence factor alongside the low-volume-breakout filter
  (§54.23): **narrow range + above-average volume together** (Silent Alarm) is a stronger precursor than
  either alone.
- ⚠️ **Unvalidated.** No sample, no win-rate — this is a hypothesis to run through `keel simulate`, same
  epistemic status as §55.1's divergence ladder. Given the Efficiency-Ratio/ADXR stack already gates
  *which assets* to trade, NR7's marginal value would be in **entry timing within an already-qualified
  asset**, not asset selection.

### 61.4 Breakout quality: gap vs. volume, and the measured-move ratio → refines §54.23 / dup of §24.2

*(patterncycles.pdf, "Breakouts"; "Highs")*

- *"The appearance of a sharp breakout gap has tremendous buy power. But... when strong volume fails to
  appear, the gap may fill quickly and trap the emotional longs. Non-gapping, high volume surges provide a
  comfortable price floor similar to gaps."* → This is the **same claim** as Kaufman's low-volume breakout
  filter (§54.23): a breakout without volume confirmation is a fakeout risk. **Reinforces, does not add.**
  The gap-specific half is **structurally N/A**: crypto trades 24/7 with no session open/close, so
  traditional session gaps barely exist (only illiquidity-driven price jumps, already handled by the
  data-spike/bad-tick guard, §24.3). Keep the volume-confirmation half; discard the gap framing.
- **1.38× measured-move target** (*"this new high breakout should extend no more than 1.38 times the
  distance between that low and the resistance top"*) — this is a **Fibonacci-extension ratio (1.382)
  applied to a pattern's own height**, i.e. the same family as the existing `target_method=pattern_height`
  (§24.2) and Fib-extension targets (§3.1/§9). **Dup, not new** — if a ratio variant is wanted, 1.382 is
  already inside the standard Fib-extension set most systems already sweep; no code change indicated.

### 61.5 "Walking the band" vocabulary (Bollinger patterns) → reinforces don't-fade-strength, not a new indicator

*(alanfarley.pdf glossary: Climbing the Ladder, Slippery Slope, Foot in Floor, Head in Ceiling)*

The glossary names four Bollinger-Band *behaviors*: price riding the **upper** band in a sustained rally
("Climbing the Ladder") or the **lower** band in a sustained decline ("Slippery Slope"), versus short-lived
band-touch reversals ("Foot in Floor" / "Head in Ceiling"). **Bollinger Bands appear nowhere in the
codebase or any prior source.** However:

- The *behavior* they're naming — "a strong trend rides the band and shouldn't be faded" — is exactly the
  don't-fade-strength principle the project already committed to after **RSI mean-reversion was refuted
  on crypto** (see project context) and is functionally covered by **Kaufman's ATR/stdev volatility band**
  (§54.15), which does the same envelope job on ATR instead of a rolling stdev. **Not adding Bollinger
  Bands as a parallel indicator** — it would duplicate §54.15's mechanism for no new capability. Logged
  only because the terminology ("walking the band") is a clean, quotable restatement of a decision already
  made.

### 61.6 ⛔ Elliott-derived structure (First Rise/First Failure, Five Wave Decline, 3rd-of-3rd) → excluded, no-oracle

*(patterncycles.pdf, "Trends", "Tops", "Declines")*

- **5-wave impulse / 3rd-of-3rd / 4th-wave correction** and the mirrored **Five Wave Decline (5WD)** are
  textbook Elliott Wave counting, explicitly invoked (*"Look no further than R.N. Elliott's work in the
  1930s"*). Already excluded per the standing no-oracle policy alongside Elliott/Gann (README, hard-rails
  "Explicit exclusions"; §57.5's ABC-wave exclusion is the same family).
- **First Rise/First Failure (FR/FF)** — reversal signalled by a **100% retracement** of the last dynamic
  thrust, cross-verified against a **hand-drawn trendline break on the same bar**. The Fibonacci-retracement
  half is not new (§1.4/§1.5 already do retracement math); the **trendline-break half is discretionary
  line-drawing**, the same class of untestable judgment already excluded at §55.6 ("hand-drawn trend lines
  as a primary signal... same class of discretionary judgement we exclude under no-oracle").
- **Verdict: discard the wave-counting and trendline-drawing mechanics; the retracement arithmetic
  underneath is already implemented.**

### 61.7 "Dip Trip" (glossary) → sharpens the refuted-vs-kept pullback distinction

*(alanfarley.pdf glossary)*

Glossary definition: *"Dip Trip — A trading strategy that buys pullbacks in an active bull market."* Worth
recording precisely **because of the qualifier**: this is buying a pullback **within a confirmed uptrend**
— structurally the Raschke First-Cross / pullback-in-uptrend family already adopted from Kaufman (§54.16),
**not** the context-free RSI mean-reversion rule that was refuted on crypto (no ADX/trend-confirmation
gate, buying weakness anywhere). The term is a one-line confirmation that the project's post-mortem
distinction — *pullback-in-a-validated-trend works, pullback-with-no-trend-context doesn't* — matches how
a professional swing trader actually scopes the setup. No rule change; reinforces the existing gate
(pullback entries must sit behind an ADX/trend confirmation, never bare).

### 61.8 Glossary terminology reinforcing existing mechanisms (no action)

*(alanfarley.pdf glossary)*

- **Cross-Verification (CV) / CVx4** — *"the convergence of unrelated directional information at a single
  price level"* — this is **confluence scoring** by another name (§8's core CTS concept, already the
  spine of `strategy/engine.py`'s scoring).
- **Market Numbers** — round-number support/resistance (multiples/fractions of 10) — already in
  `analysis/levels.py` (§1.3/§4.8).
- **Trend Relativity Error** — analyzing on one timeframe, executing on another — the exact failure mode
  Elder's Triple-Screen and the higher-timeframe-overrules-lower law (§54.24) already guard against.
- **Hard Right Edge** — the chart's live edge, where a trader must act without seeing what comes next —
  a restatement of the no-oracle premise (§6.4), not a new rule.
- **Accumulation-Distribution / OBV** used to judge whether a new-high breakout will run or base first
  (patterncycles.pdf, "Highs") — already in the Kaufman volume toolkit (Force Index/OBV/MFI, §54.23).

All five are cited here only to close the loop that the KB has already absorbed this vocabulary under
different names — **no §54/§8/§24 content needs updating.**

### 61.9 Explicit timeframe-mismatch note — most of the Bonus Report is out of scope by frame

*(alanfarley.pdf, all 3 worked examples)*

Every worked example is charted on an **intraday or sub-daily frame**: AMZN on **60-minute and 3-hour**
charts (with a daily chart only for the broader setup), NVDA on a **120-minute** chart (an "Island
Reversal" false-breakout/bull-trap), ALKS on a **60-minute** chart with a **stochastic oscillator**
confirming an oversold bounce before a short entry. The glossary compounds this with explicitly
intraday-tuned parameters: **"5-8-13"** (Bollinger 13-bar/2-stdev + 5- and 8-bar SMAs, described as aligned
to "short-term Fibonacci cycles") and **"6-18 Swing"** (a crossover system "used to track intraday buying
and selling pressure").

- **`keel` trades daily bars with a ~21–24 day average hold and an anti-scalping/min-move rail.** None of
  the intraday chart work, the 5-8-13/6-18 parameter sets, or the sub-daily entry timing in these examples
  transfers. This is the same disposition applied to §55's M5 EURUSD framing and §57's first-hour/1-minute
  scalping content: **keep any mechanically-testable geometry, discard the timeframe.** In this source,
  once the intraday framing is stripped, what's left of the 3 examples is: round-number resistance ($10,
  $25 "Market Numbers"), 50-/200-day MA levels (already covered), a **head-and-shoulders** short setup
  (deferred subjective geometry, §24.5), and a **stochastic** oversold reading (excluded per KISS, §57.5)
  — i.e., nothing new survives the filter.
- **Stochastic oscillator** (ALKS example) — same near-duplicate-of-RSI exclusion already made at §57.5;
  not adopted.

### 61.10 ⛔ Halal exclusions (long-only / spot / no-derivatives)

Neither PDF discusses leverage, margin, options, or interest — this is pure discretionary TA, not a
broker product pitch (unlike the Stanzione/Swissquote ebooks). The one halal-relevant issue is **direction**:

- **Short selling is the default direction for reversal/topping/declining-phase content.** patterncycles.pdf's
  "Tops," "Reversals," and "Declines" sections are written short-first (*"skilled traders wait... before
  they enter large short sales"*), and the Bonus Report's **third worked example is an explicit short
  trade** (ALKS head-and-shoulders, entering "short sales," a "target" below current price, opening with
  *"less than 25% [of traders] said they ever [sold short]... these were hard-core traders"* as a pitch
  *for* shorting). Per the non-negotiable adaptation lens: **none of this is adopted as a short entry.**
  The topping/reversal/decline pattern recognition converts to **exit signals on held longs** and
  **don't-buy filters** (e.g., a confirmed double-top / descending-triangle pattern → sell the held
  position or decline to re-enter, never open a short).
- No riba/carry/derivatives content to flag — this source is instrument-neutral technical analysis and
  would sit on a spot chart exactly as drawn; the exclusion here is **direction only.**

### 61.11 Discarded (no agent value)

- **patterncycles.pdf** cover/title page; the closing "Copyright © 2000 Traders' Library" line.
- **alanfarley.pdf** cover/title page ("3 Swing Trading Examples, With Charts, Instructions, and
  Definitions, to Get You Started").
- Glossary entries with zero mechanical content or that are pure background/finance-history trivia:
  **Bucket Shops** (Jesse Livermore-era history), **January Effect** (equity-specific tax-selling
  seasonality, N/A to crypto), **Electronic Communications Networks (ECNs)** (equity market-structure
  trivia), **Window Dressing** (institutional quarter-end reporting behavior, N/A to a spot retail agent),
  **Random Walk** (a definitional foil, not a rule), **Dow Theory** (historical reference, not
  implementable), **Fractals** (used here as a one-line definition, not Kaufman's adaptive-indicator sense
  — no relation to KAMA §54.5).
- Standard candlestick-pattern definitions already fully covered by `analysis/candles.py`: Doji, Hammer,
  Harami, Dark Cloud Cover, Shooting Star, Abandoned Baby, Reflection.
- Standard chart-pattern definitions already covered/deferred: Ascending/Descending/Symmetrical Triangle,
  Cup & Handle (+ "Cup and Two Handles"), Rising Wedge, Flags, Pennants, Rectangle, Head and Shoulders /
  Inverse Head and Shoulders, "3rd Watch" (triple-top breakout), "Mesa Top."
- Standard backtest-stat definitions already in `strategy/stats.py`/`strategy/backtest.py`: AvgWIN,
  AvgLOSS, %WIN.
- Vague, non-mechanizable strategy-name entries with no rule attached beyond their own one-line
  definition: **Finger Finder, Power Spike, Fade, Bear Hug, Trendlet, Whipsaw, Noise, Setup, Signpost,
  Charting Landscape, Trend Mirrors, Empty Zone, Execution Trigger/Zone, Failure Target, Profit Target,
  Historical Volatility, Hole in the Wall.** These are Farley's house vocabulary for concepts the KB
  already has under other names (entry trigger, stop, target, S/R) with no additional testable content.
- **Farley's Accumulation-Distribution Accelerator (ADA)** — named as "a technical indicator that measures
  the trend of accumulation-distribution" with no formula given; not implementable from the text.

### Net assessment (saturation-honest)

**Two 16-page lead-magnets, one small new idea.** Of the combined ~32 pages (much of it charts, cover
pages, and a ~5-page glossary of already-covered terms):

- **New:** §61.3's **NR7/Silent Alarm volatility-contraction precursor** — a cheap, daily-bar-computable
  breakout-timing filter with no prior-source equivalent. Unvalidated; a `keel simulate` candidate, not a
  default.
- **Refines (marginal):** §61.4's volume-confirmed-breakout framing (restates §54.23; gap half N/A to
  24/7 crypto), §61.5's "walking the band" vocabulary (restates §54.15's ATR-band don't-fade-strength
  principle without adding a new indicator).
- **Reinforces:** §61.1 (phase-rotation ≈ a lower-resolution restatement of §54's ER/ADXR stack — does
  **not** refine or extend the asset-trendiness answer), §61.2 (double-bottom/Big W = §34.3/§55.4),
  §61.7 (Dip Trip sharpens the trend-confirmed-pullback vs refuted-context-free-dip-buy distinction),
  §61.8 (CV/Market Numbers/Trend Relativity Error = existing confluence/levels/multi-TF concepts by other
  names).
- **Excluded:** the entire short side (topping/reversal/decline sections + the ALKS worked example),
  Elliott-derived structure (5-wave, 3rd-of-3rd, FR/FF's trendline half), the stochastic oscillator, and —
  per §61.9 — nearly all of the Bonus Report's worked-example mechanics, which are framed on 60-minute/
  120-minute/3-hour charts with intraday-tuned indicator settings (5-8-13, 6-18 Swing).

**Recommendation:** implement §61.3 (NR7 + Silent Alarm) as a breakout-timing sweep candidate alongside
§55.1's divergence ladder and §57.1/§57.2's rails — same "cheap, unvalidated, worth a backtest slot"
tier. Nothing here reopens the asset-trendiness question beyond what §54 already answers. This author's
stream can be considered exhausted at one filing (two companion PDFs, one source number) — it is
descriptive/discretionary TA writing, not a quant reference; further Farley titles would likely just
re-narrate the same phase-rotation story with new tickers.
