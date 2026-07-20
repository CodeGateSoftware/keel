[← Knowledge Base index](../README.md)

## Source 59 — "Charting Made Easy" (John J. Murphy, Marketplace Books "Trade Secrets" series, 2000, 82pp)

> **The most saturated source fed to this KB so far — and that is itself the finding.** This is a
> ~57-page-of-content primer by the author of *Technical Analysis of the Financial Markets* (the
> field's standard textbook), written to introduce retail investors to charting. Every chapter maps
> to ground this KB already covers in more depth, from a more rigorous source, or both: chart types
> (§1–3), support/resistance and trendlines (analysis/levels.py, §1.3/§4.8/§7.3/§9.2/§34.3), the full
> reversal/continuation pattern catalog (§24), gaps and retracements, volume/OBV (§54.23), moving
> averages/MACD (core), RSI/stochastics (§55.1, §57.5), multi-timeframe sequencing (§54.24), and the
> confluence principle itself (§8, §34.4 CTS). The back matter (pp. 61–67) is pure MetaStock/CPR
> product marketing and a suggested-reading list — discarded.
>
> Two items earn a genuine "new" tag, both modest: **§59.5** (the key reversal day — a single-bar
> pattern with no existing analog in `analysis/candles.py`) and **§59.9** (ratio/relative-strength
> analysis — a cross-sectional asset-ranking technique distinct from Kaufman's absolute trendiness
> metrics). Everything else below is logged for completeness and reconciliation, not because it adds
> capability.

---

### 59.1 Chart fundamentals — reinforces §1–§3, no action

Bar/candlestick/line charts, "any time dimension," and the two premises "markets trend / trends tend
to persist" are the same ground as source-01/02/03. No new content. The book's plotting mechanics
(OHLC construction) are a non-issue — already handled by the data pipeline.

### 59.2 Support/resistance, trendlines, channels, role reversal — reinforces `analysis/levels.py`

Support/resistance, the "role reversal" of broken levels (resistance→support and vice versa), the
3-touch trendline validation rule, and parallel channel lines are all already implemented in
`analysis/levels.py:find_levels` (≥3 touches) and cited at §1.3/§4.8/§7.3/§9.2/§34.3. The one detail
worth noting without adopting: the book's explicit **3-touch validation for trendlines** ("a third
point is necessary to identify the line as a valid trend line") is the same touch-count logic our
level-finder already applies to horizontal S/R — reassuring that the two geometries (horizontal
levels, sloped trendlines) should use the same touch-count rigor. No parameter change.

### 59.3 Reversal & continuation patterns — reinforces §24, subjective parts stay deferred (§24.5)

Head and shoulders (+ neckline + measured-move target), double/triple tops and bottoms, symmetrical/
ascending/descending triangles, flags and pennants (the "half-mast" rule), saucers (rounding bottoms),
and spike tops/V-reversals — this is the identical pattern catalog as Source 24's "10 Chart Patterns,"
narrated by the more famous author. The measuring techniques (height-of-pattern projected from the
breakout point) are already captured by `pattern_height` (§24.2). Saucers/rounding-bottoms and
subjective free-hand pattern identification remain in the **v2-deferred discretionary bucket** (§24.5)
for the same reason as before: no mechanical rule for "is this saucer-shaped enough," it is curve
fitting by eye. Nothing here changes the deferral decision or adds a new pattern.

### 59.4 Price gaps — mostly N/A to continuous 24/7 spot crypto

Breakaway/runaway(measuring)/exhaustion gaps and the "island reversal" (two gaps trapping a few days
of price action) are a coherent classification, but they are a structural artifact of **markets that
close** — a gap is "no trading took place between yesterday's high/low and today's low/high." Spot
crypto trades continuously; on a continuous feed, a daily candle's open is (barring an exchange
outage or a genuine liquidity air-pocket) equal to the prior candle's close, so gaps in the equities
sense are rare and mostly noise/data-quality artifacts rather than a tradeable structural signal.

- → **No new rule.** The one thing worth flagging for the guards layer: a large **overnight-style
  gap on a continuous feed is itself an anomaly** worth suspicion — this is already the job of the
  **data-spike/bad-tick guard** (§24.3, `execution/guards.py`), which compares an implausible move to
  ATR. Gap classification adds nothing beyond what that guard already does; if anything, treat any
  "gap" bigger than a few ATR as a feed-health event to investigate, not a breakaway-gap trade signal.
- The "island reversal" concept (exhaustion gap immediately followed by a breakaway gap the other way)
  is conceptually the same shape as a **key reversal day taken to an extreme** (§59.5) — same
  reconciliation applies.

### 59.5 ⭐ Key reversal day — NEW single-bar pattern, no existing analog

> *"In an uptrend, prices usually open higher, then break sharply to the downside and close below the
> previous day's closing price. (A bottom reversal day opens lower and closes higher.)"* The wider the
> day's range and the heavier the volume, the more significant the warning; an **outside day** (that
> day's high AND low both exceed the prior day's range) is considered more potent.

`analysis/candles.py` has pin bars, doji, marubozu, hammer/shooting-star, three-bar reversal, and
tweezer — but nothing that combines **(a) an outside/wide range bar** with **(b) a close beyond the
prior day's close** and **(c) volume confirmation** into a single discrete signal. That three-part
combination is genuinely absent from the codebase (`grep` for `outside_bar`/`key_reversal` returns
nothing).

- → **NEW candidate:** `is_key_reversal(candles, i, vol) -> Literal["bullish","bearish"] | None`,
  computable from primitives we already have: `range_(c)` for width-vs-prior-day, `c.close` vs
  `candles[i-1].close` for direction, and a volume series for the confirmation leg. Grade by range
  (outside day = stronger) the same way `pattern_confidence()` already grades candle patterns.
- **The bullish form is directly long-only-usable, no short-to-exit conversion needed:** *"opens
  lower and closes higher"* on heavy volume, after a decline, is a legitimate entry-timing confirmation
  signal for our long-only agent — unusual among this KB's sources, most of which hand us a short
  setup to invert. Feeds the confluence set alongside RSI-divergence-grade (§55.1) and MACD-slope
  (§55.2) as another same-day confirming factor.
- **The bearish form → exit / don't-buy filter** on a held long, per the standing lens: a wide-range,
  volume-confirmed reversal against an open position is a plausible addition to the exit-signal set
  alongside §57.2's close-strength exit and §34.1's close-based stop — all three make the **close**
  (or close-vs-open relationship) the decision point, which is the pattern this KB keeps re-deriving
  from independent sources.
- ⚠️ **Unvalidated.** The book gives no win-rate or sample size — this is a hypothesis for the harness,
  not an established edge. It is also a *lagging-confirmation* pattern (needs the full day's bar to
  close), same caveat as every other end-of-day candle signal already in the KB.

### 59.6 Percentage retracements — reinforces existing Fibonacci tooling, no action

50%/one-third-minimum/two-thirds-maximum retracement bands and the 38%/62% "Fibonacci retracements"
are already implemented (`indicators.py:fib_retracements`/`fib_extensions`). No new levels, no new
rule. The one-third/two-thirds framing is a coarser, non-Fibonacci alternative banding that adds
nothing testable beyond what we already compute.

### 59.7 Volume, OBV, and "other volume indicators" — reinforces §54.23

On-Balance Volume (cumulative running total of up-day-volume minus down-day-volume, watched for
divergence from price and for early breakouts during sideways action) and the name-check of
Accumulation/Distribution, Chaikin Oscillator, Market Facilitation Index, and Money Flow are all
already logged under Kaufman's volume chapter (§54.23: Force Index, OBV, MFI, volume-weighted MACD,
NormVol, volume-spike detector, low-volume breakout filter). This source adds no computation detail
Kaufman doesn't already specify more rigorously — it's a shallower restatement of the same indicator
family, with two additional names (Accumulation/Distribution, Chaikin Oscillator) mentioned only in
passing with zero formula or rule given. **Not adopted as new** — if we ever want a third/fourth
volume oscillator beyond Force Index/OBV/MFI, go to a source that actually specifies one.

### 59.8 Multi-timeframe sequencing — reinforces §54.24 (Elder Triple-Screen)

"Begin with monthly, then weekly, then daily, optionally then intraday" is the same top-down
timeframe order as Elder's Triple-Screen (§54.24: weekly tide → daily pullback → hourly entry) and
the general higher-TF-overrules-lower law already adopted. No new mechanism; reinforces the existing
sequencing rule and its rationale (long-term structure first, short-term timing last).

### 59.9 ⭐ Top-down market approach + ratio/relative-strength analysis — NEW cross-sectional ranking tool

Chapters 11 and 14 together describe a **three-step selection funnel**: (1) is the overall market
trending up (a major-average filter); (2) which **sector/industry group** is strongest, found by
dividing the sector index by the market benchmark (`ratio = sector / S&P 500`) — a **rising ratio
line means that sector is outperforming**; (3) within the strongest sector, which **individual stock**
is strongest, found the same way (`ratio = stock / sector index`). *"As much as 50% of a stock's
direction is determined by the direction of its industry group"* — pick the winning group first, then
the winning member within it. The technique is applied identically at the top for the whole-market
question (e.g., `Nasdaq / S&P 500` to see if tech is leading or lagging).

This is a **genuinely different technique** from anything already in the KB. Kaufman's Efficiency
Ratio / ADXR / CSI / Strategy Selection Indicator (§54.9, §54.17, §54.21) rank an asset's **own price
series against itself** — "is this asset trending, in absolute terms." Ratio/relative-strength instead
ranks an asset **against a benchmark** — "is this asset outperforming the reference." The two are
complementary lenses, not duplicates: an asset can be trending (high ER) while still lagging the
benchmark, or range-bound in absolute terms while quietly gaining relative share.

- → **NEW, mechanically testable:** `relative_strength_ratio(asset_closes, benchmark_closes) ->
  list[float]` (elementwise division), then apply the **same trend tools we already have** to the
  ratio series itself — a moving-average cross, a trendline break, or an ADX/ER read on the ratio —
  exactly as the book directs ("apply technical analysis to ratio charts"). No new math primitive is
  required; only a new *input series* (a ratio) fed through existing trend/breakout machinery.
- **Direct application to the open "asset-trendiness ranking" defect:** the KB has been assembling an
  absolute per-asset trendiness gate (§54.9/§54.11/§54.17/§54.21) to decide keep/drop/allocate for
  ETH and future allowlist candidates. A **relative-strength ratio vs. BTC** (`ETH-close / BTC-close`,
  trend-graded) is a cheap complementary signal: prefer allocating fresh entries toward whichever
  allowlisted asset's ratio-vs-BTC is currently rising, and treat a breaking-down ratio trendline as a
  demotion/reduce-weight signal — a **relative** overlay on top of the **absolute** trendiness gate,
  not a replacement for it.
- **The "sector" tier is thin for our allowlist.** With a narrow BTC/ETH(-and-maybe-a-few) halal
  allowlist (§28.3/§33), there is no meaningful sub-sector grouping to rank the way the book ranks
  Semiconductors vs. Technology vs. S&P 500 — that middle tier of the funnel has **no crypto analog at
  our current allowlist size** and should not be manufactured (narrative buckets like "L1s" or
  "DeFi" would reintroduce the haram-sector screening problem, §28.4/§41.1, for no proven benefit).
  Use the top tier (overall-market filter, e.g., total-crypto-cap or BTC trend as the macro gate —
  already effectively our regime filter) and the bottom tier (asset-vs-BTC ratio) directly; skip the
  middle tier until the allowlist is large enough to need it.
- ⚠️ **Unvalidated — no sample/backtest given.** This is a candidate feature for `analysis/regime.py`
  to sweep through the harness alongside the existing absolute-trendiness metrics, not an adopted
  rule. Same rigor bar as the rest of the KB (§54.10/§54.11): grade it empirically before trusting it.

### 59.10 Moving averages, Bollinger Bands, MACD — reinforces existing tooling

50/200-day (and 20-day short-term) moving averages, price-vs-average support/resistance behavior, and
MACD's dual-weighted-average-crossover mechanic are all already core to `analysis/indicators.py` and
the trend-following pivot. **Bollinger Bands** (20-period MA ± 2 standard deviations, "touch the band
= over-extended, expect a pullback to the average") is not previously named in this KB by that term,
but it is the same *mean ± volatility-multiple* shape as Kaufman's **ATR/stdev volatility bands**
already logged as a candidate (§54.15) — a different volatility estimator (close-to-close stdev vs.
true range) around the same idea. **Reinforces §54.15**; the one thing worth carrying forward if that
candidate is ever built is Bollinger's concrete default parameterization (20 periods, 2σ) as one point
in the sweep, not a new mechanism.

### 59.11 Oscillators: RSI and Stochastics — reinforces (and re-confirms an exclusion)

RSI (70/30 overbought/oversold, 9- and 14-day periods) is already deeply built out (`indicators.py:
rsi`, `is_overbought`/`is_oversold`, `rsi_divergence`, and the graded strength ladder of §55.1). The
stochastic oscillator (14-day, 80/20 bands, %K/%D crossover signals) is **already explicitly excluded**
— §57.5 discarded it as a near-duplicate of RSI under the KISS principle (§26). This source's
description of stochastics adds no new mechanic and changes nothing; it independently reconfirms that
the prior exclusion was reasonable (it really is the same shape as RSI, just with different band
placement).

### 59.12 ⛔ Options: put/call ratio and VIX — excluded (halal + practical)

The put/call ratio (contrarian sentiment from options volume) and the CBOE Volatility Index (VIX,
contrarian — rising VIX = bearish extreme, falling VIX = bullish extreme) are both **derived from an
options market**, and options are wholesale-excluded from this KB on gharar/maisir grounds (§27.4,
§28.1, §42–§49). Two further reasons neither is worth chasing a crypto analog for even as a
*sentiment overlay* (as opposed to a tradeable instrument):
- **No comparable data pipeline exists** for us (no Deribit/OKX options open-interest or skew feed is
  wired into the agent), so this is speculative scope creep with no current implementation path.
- **It is a forecasting/sentiment-timing tool by construction** ("option traders get too bullish near
  tops"), which sits close to the **no-prediction-oracle** boundary (§6.4) — the agent's intelligence
  is deterministic tested rules, not a crowd-sentiment-implies-reversal heuristic. If a genuine
  crypto sentiment proxy (e.g., a funding-rate or fear/greed index) is ever proposed, it should come
  from a source that specifies it directly and be evaluated against §6.4 on its own merits — not
  smuggled in as "the crypto VIX."

### 59.13 The principle of confirmation — reinforces CTS confluence, no action

"The more technical evidence agreeing, the stronger the conclusion; look for the moving averages,
oscillators, volume, and multiple timeframes to agree" is a plain-language restatement of the CTS
confluence-scoring design this agent already runs on (§8, §34.4's A+/B/C conviction sizing). No new
mechanism — a clean, independent articulation of why confluence works, nothing to build.

### 59.14 ⛔ Halal exclusions

- **Options (put/call ratio, VIX)** — non-spot derivative-market data; see §59.12. Options themselves
  remain wholesale-excluded per §27.4/§28.1/§42–§49 (gharar/maisir, not spot).
- No leverage, margin, shorting, or carry content appears in this source at all — it is purely a
  charting-mechanics primer with no position-management or instrument discussion beyond the options
  chapter. Nothing else to flag.

### 59.15 Discarded (no agent value)

Front matter (title/copyright/table of contents), the Introduction's late-1990s Nasdaq/biotech
sector-rotation anecdote (used only to motivate the booklet, not a rule), the **"Investing Resource
Guide"** (pp. 59–65: paid advertisements for *Technical Analysis of the Financial Markets*, Martin
Pring's CD-ROM course, Clif Droke's book, William Jiler's 1962 primer, MetaStock software, John
Murphy's "CPR" pattern-recognition plug-in, and an 82-minute Murphy video) — all cross-sell copy, not
content. Also discarded: "Rewards Weekly" email-list signup, "About the Author" bio, and the back-cover
blurb. None of this carries agent-relevant information.

---

### Net assessment (saturation-honest)

**The single most saturated source fed to this KB.** Of 82 pages, ~57 are content and essentially all
of that content already exists elsewhere in this KB, mostly in more rigorous form:

- **New:** §59.5 the **key reversal day** (a single-bar outside-range + close-beyond-prior-close +
  volume pattern with no existing `analysis/candles.py` analog; unusually, its bullish form is
  directly long-usable without an exit-only conversion) and §59.9 **ratio/relative-strength analysis**
  (a cross-sectional asset-vs-benchmark ranking tool, complementary to — not a duplicate of —
  Kaufman's absolute trendiness metrics §54.9/§54.17/§54.21; directly relevant to the open
  asset-trendiness-ranking defect, usable today as an ETH-vs-BTC ratio-trend overlay). Both are
  unvalidated hypotheses for the harness, not adopted rules.
- **Reinforces:** chart types/trends (§59.1), S/R and trendlines (§59.2, `analysis/levels.py`),
  the full reversal/continuation pattern catalog (§59.3, §24; subjective geometry stays deferred per
  §24.5), Fibonacci retracements (§59.6), volume/OBV (§59.7, §54.23), multi-timeframe sequencing
  (§59.8, §54.24), moving averages/Bollinger (§59.10, §54.15), RSI (§59.11), and the confluence
  principle itself (§59.13, §8/§34.4).
- **Re-confirms an existing exclusion:** stochastics (§59.11 → §57.5/§26 KISS).
- **N/A / low-applicability:** price gaps (§59.4) — a structural artifact of markets that close, mostly
  meaningless on a continuous 24/7 spot feed; any large "gap" on our data is a feed-health question for
  the existing data-spike guard (§24.3), not a breakaway-gap trade signal.
- **Excluded:** put/call ratio and VIX (§59.12, §59.14) — options-market-derived, non-spot, and
  borderline oracle-like even as a sentiment overlay.

**Recommendation:** this stream (classic-Murphy-style TA primers) has now been mined about as deep as
it goes — Sources 24/25/27/54/55 already cover this ground more rigorously, and this source's own
"Suggested Reading" page points to the *same author's* longer, more authoritative textbook, which
would almost certainly be pure duplication if fed next. **Do not seek out more general TA-primer
titles; they will not clear the bar this source barely cleared.** The two live threads worth pulling
next are (a) prototyping §59.9's relative-strength ratio alongside the Kaufman absolute-trendiness
metrics already queued for the asset-ranking build, and (b) implementing §59.5's key-reversal-day
detector as one more graded entry-confirmation/exit-trigger primitive in `analysis/candles.py`,
consistent with the graded (not boolean) pattern style §55.1 already established.
