[← Knowledge Base index](../README.md)

## Source 81 — "Technical Analysis" (Zerodha Varsity Module 2)

> Zerodha Varsity, **Module 2 — Technical Analysis**, 177pp, free. Indian-equities educational
> textbook by Karthik Rangappa (Zerodha), well regarded as a beginner primer. 20 chapters:
> 1–2 introduction/assumptions · 3 chart types · 4–10 candlesticks (16 patterns) · 11 support &
> resistance · 12 volumes · 13 moving averages · 14–15 indicators (RSI, MACD, Bollinger) ·
> 16 Fibonacci retracements · 17–18 Dow theory · 19 "The Finale" (practical scouting workflow) ·
> 20 supplementary indicator notes (ADX, Alligator, Aroon, ATR, ATR bands, Supertrend, VWAP).

> ### ⚠️ Saturation verdict, stated up front — this is the fourth saturation call on the TA stream, and it holds
>
> The KB was already assessed **saturated on TA primers and chart-pattern catalogs** three
> independent times (§24 → §59 "the most saturated source yet" → §76 → §77's "stop feeding
> one-page infographics"). **This source does not challenge that call.** Chapters 1–18 are a
> near-total duplicate of ground the KB already holds from better sources:
>
> | Zerodha chapter | Already held, better, at |
> |---|---|
> | 4–10 candlesticks (marubozu, doji, spinning top, hammer/hanging man, shooting star, engulfing, piercing, dark cloud, harami, morning/evening star) | §1.3, §2.1, §59.5, §70.7 catalog |
> | 11 support & resistance (≥3 touches, horizontal line fit) | `analysis/levels.py`, §34.3, §58.6 |
> | 12 volumes (confirm price, smart money) | §54.23 (which also supplies OBV/MFI/Force Index/NormVol) |
> | 13 moving averages + crossover (9/21, 25/50, 50/100, 100/200) | §23.1, §26.2, §37, §58.9 (48 MA models tested), §74.8 |
> | 14 RSI 14/30/70 | **REFUTED 3×** — §58.10a, §74.3 (−6.33bp, p<0.01 on BTC), our own sim |
> | 15 MACD 12/26/9 crossover | **never cleared even nominal significance** (§80); §74.5 |
> | 15 Bollinger 20/±2SD mean-reversion | **REFUTED** — §74.4 (−5.34bp, p<0.01 on BTC) |
> | 16 Fibonacci retracements | §59.6, §70 — low-weight confluence only |
> | 17–18 Dow theory, double/triple top-bottom, range breakout + measured move, flags | §24.1–§24.4, §24.2 `pattern_height`, §34.3, §55.4 |
> | 20 ADX>25 + DI crossover · ATR · ATR bands · VWAP | §25.1 (and §58.2 *contests* it) · §54.3 · §54.15 · §54.23 |
>
> **Worse, the book is pointed the wrong way for us on its own terms.** §1.3 sets expectations
> explicitly: TA is *"best used to identify short term trades"*, *"do not use TA to identify long
> term investment opportunities"*, holding period *"anywhere between few minutes and few weeks,
> and usually not beyond that."* Our hold is ~21–24 days on daily bars, and **§74.10 is the decisive
> crypto-specific finding**: on Bitcoin buy-and-hold *beats* technical rules intraday while daily
> SMAs win. The book's recommended frame is the one where the evidence says the edge is not.
>
> **Net: 2 genuinely new named constructs (both from the supplementary appendix, not the body),
> 2 small parameter/structure refinements, 1 clean negative finding, 1 mechanism note. Nothing
> shipped changes. No open defect is closed.**

---

### §81.1 ⭐ Aroon and the Aroon Oscillator — grep-verified absent from the KB, and it is the CONTINUOUS form of the rule we already trade `analysis/regime.py` `analysis/indicators.py`

The book's supplementary appendix (Ch 20) carries Tushar Chande's **Aroon** (1995). The definition
given is exact and needs no judgment:

```
Aroon-Up(n)   = 100 × (n − bars_since_highest_high_of_last_n) / n
Aroon-Down(n) = 100 × (n − bars_since_lowest_low_of_last_n)  / n
Aroon-Oscillator(n) = Aroon-Up(n) − Aroon-Down(n)     # range −100 … +100
```

The book states the framing that makes it interesting: Aroon *"measures the number of periods
since price recorded an x-day high or low"* — it is **time relative to price**, where every other
oscillator in the KB is price relative to time.

**COMPUTABILITY: YES, unambiguously.** Two `argmax`/`argmin` calls over a rolling window on OHLC.
No smoothing constant, no drawn line, no pivot-identification ambiguity, no human judgment.

**Why it is worth logging rather than discarding as one more indicator.** Our shipped entry is
`close > max(high, prior 40)`. In Aroon terms that is exactly **`Aroon-Up(40) == 100`**. Aroon is
therefore not a new family competing with the Donchian channel — **it is the graded, continuous
relaxation of the boolean we already fire on**, computed from the identical statistic (the recency
of the channel extreme) with no new data and no new indicator machinery. Two consequences:

- **It is a candidate trend-regime gate built from the SAME statistic as the entry.** This matters
  because §58.2 contests the shipped ADX>25 gate (*"do not rely on indicators like the ADX for
  trendiness determination"*, no OOS benefit) while §74.7 independently vindicates *some* gate on
  crypto (the breakout rule *"delivers outperformance in strongly trending markets"* and was
  negative across every lookback in the quiet 2015–16 window). An Aroon-Oscillator gate is a cheap
  alternative to test in that ablation — and unlike ADX it shares the entry's own geometry, so a
  gate/entry disagreement is interpretable rather than a black box.
- **It rides the entry-lookback question rather than adding a new axis.** Sweeping Aroon's `n` is
  the same axis as `donchian_entry_n`, which §74.2/§79.5/§79.15 already say to sweep far past 40
  (plateau ≈150–250 days, floor above 50–100). It costs no additional trials budget dimension.

**⚠️ Honest caveats, and they are heavy:**
- **The source supplies zero validation.** The Aroon section is a lightly-edited copy of
  stockcharts.com boilerplate plus two screenshots of Zerodha's own charting product. No sample,
  no backtest, no win rate — exactly the §73.6 problem (a rule quoted without its trials count `N`
  is not weak evidence but **no** evidence), and the same class of evidence §76 discarded.
- **The book contradicts itself on the default:** the text says the default period is 25 days, the
  screenshot two paragraphs later says 14. Neither is derived. Treat both as arbitrary; per
  §73.13c any sweep here is `sensitivity`, not `selection`.
- Its asserted entry rule (*buy when Aroon-Up > 50 and Aroon-Down < 30*) is unvalidated and, as a
  standalone entry, is just a weaker restatement of the breakout. **Do not adopt the entry rule.**
  The value is the *measurement*, as a gate/grade.

**Long-only / halal:** direction-blind measurement; the Aroon-Down branch becomes an **exit /
don't-buy** input on a held position, never a short.

**Status: logged as a cheap ablation candidate against the contested ADX gate — NOT a build
commitment.** Same disposition as §70.5 and §76.3.

**Cross-refs:** §23.1/§27.1/§74.1 (the Donchian entry it generalises), §58.2 (the ADX gate it could
replace), §74.7 (why a gate is warranted on crypto), §74.2/§79.5/§79.15 (the lookback axis),
§54.1/§54.9/§54.17/§54.21 (the four existing trendiness measures it would join), §73.6/§73.13c.

---

### §81.2 ⭐ Supertrend — grep-verified absent by name; a fully-specified ONE-WAY ATR ratchet trailing stop with a stated multiplier band `execution/executor.py`

Also from the supplementary appendix, and also fully specified:

```
basic_upper = (high + low)/2 + multiplier × ATR(period)
basic_lower = (high + low)/2 − multiplier × ATR(period)
# the plotted line ratchets: in an uptrend the lower line only moves UP, never down,
# until a close crosses it, at which point the line flips to the other side.
Long while close > line;  exit long on a CLOSE below the line.
Defaults given: period = 7, multiplier = 3.   Author's recommendation: keep multiplier 3–4.
```

The book states the trade-off explicitly and correctly: *"If the multiplier value is too high, then
lesser number of signals are generated. Likewise if the multiplier value is too small, then the
frequency of signals increase, hence chances of generating false trading signals are quite high."*

**COMPUTABILITY: YES.** ATR is already in `analysis/indicators.py`; the rest is arithmetic plus a
running max/min. It exits on the **close**, matching §34.1's `stop_trigger=close` and §35.3's
liquidity-sweep-vs-BOS reasoning.

**What is actually new here, stated narrowly:**
1. **The name and the exact formulation.** The KB holds four volatility-adaptive trails — Kase
   Dev-Stop (`ATR + f·STDEV`, §54.6), the ER-adaptive ATR stop (§54.8), Parabolic SAR (§54.8) and
   MEMA (§58.13b). Supertrend is none of them: it is anchored on the **bar midpoint `(H+L)/2`**
   rather than the close or an average, which no existing candidate uses.
2. **It satisfies rail 10 (no-stop-widening) BY CONSTRUCTION**, not by a guard — the same property
   the README already singles out as preferable in the §58.13b note (*"never polled further away
   from the market, only closer"*). It belongs in that shortlist.
3. **A third external data point on stop WIDTH**, the open question §58.12 opened.

**⚠️ The width comparison must NOT be made naively — this is the part a future reader will get
wrong.** Three sources now state a stop width, on three different ATR lookbacks, and the
multipliers are therefore **not comparable numbers**:

| Source | Stated width | ATR lookback |
|---|---|---|
| §58.12 (controlled test) | ~1.5 × ATR | **50** |
| shipped Turtle rule | 2 × ATR ("2N") | **20** |
| §81.2 (this source, asserted) | 3–4 × ATR | **7** |

A 7-bar ATR is materially smaller than a 50-bar ATR on trending crypto, so `3 × ATR(7)` is not
"twice as wide" as `1.5 × ATR(50)` — it may be narrower. **Any sweep must fix the ATR lookback
before comparing multipliers.** And the scoring discipline is settled: **score on EXPECTANCY, not
win rate** (§58.12 — past ~2 ATR the win rate keeps rising while every other metric degrades).

**⚠️ And §79.7 already reframed this whole question**, which caps this item's value: [B] found an
interior stop optimum whose *entire curve sat below the no-stop baseline* when a trend exit was
already doing the work, so the queued item is a single **`stop-off` ABLATION** (1 trial,
§58.0 component-isolation), not a width sweep. Supertrend does not change that. It is a candidate
*formulation* for the trail if the ablation says a trail earns its place; it is not evidence.

**Long-only / halal:** the red/short half of the indicator becomes an **exit signal** on a held
position, never a short entry. Note the book itself concedes the flip is a poor exit — *"waiting
for the sell signal to exit the existing long position can sometime lead to taking a loss"* — and
then falls back to *"the trader should use his discretion here"*, which is exactly the
non-mechanical escape hatch we cannot implement. If adopted, the exit rule is the close-cross, full
stop; no discretion clause.

**Cross-refs:** §54.6/§54.8 (existing volatility trails), §58.13b (the one-way property), §34.1/§35.3
(close-based triggers), §58.12 + §79.7 (the stop-width question and its reframing), §23.2 (channel-low
trail), rail 10.

---

### §81.3 The missing averaging window for the low-volume breakout filter — a free `a_priori` value `strategy/engine.py`

§54.23 holds the **low-volume breakout filter** (*"only take a Turtle breakout if it fires on
above-average volume"*) and §61.3 pairs narrow range with above-average volume — but grep across
`README.md` + all of `sources/` for `10 day average volume` / `10-day average volume` returns
**zero hits**, and the only volume lookback stated anywhere is §60.9's 20-day *liquidity floor*,
which is a universe-admission filter, not a signal filter. **The averaging window for the signal
filter has never been specified.** This source specifies it:

```
High Volume    = today's volume  >  10-day average volume
Low Volume     = today's volume  <  10-day average volume
Average Volume = today's volume  ≈  10-day average volume
```

and it is used consistently downstream (§19.5's scouting checklist requires *"volume at least equal
to or more than the 10 day average volume"*; §18.5's worked example calls *"at least 30% more than
the 10 day average volumes"* attractive).

**COMPUTABILITY: YES**, trivially — a 10-bar SMA over the volume series.

**Status: adopt as an `a_priori` default, not as a swept parameter.** This is precisely the §73.13a
mechanism — *reclassify parameters as `a_priori` from the KB's own literature, because the knowledge
base is a trials-budget subsidy* — and §74.13 records that working researchers do exactly this
(*"following Brock et al. (1992), restraining ourselves to the most popular ones"*). At `N ≤ 3`
(§73.3) we cannot afford to fit a volume window; taking the conventional 10 costs **zero** trials
budget. It is a small item, and it is free.

⚠️ Do **not** promote it beyond that: this source offers no test of 10 against any alternative.

**Cross-refs:** §54.23, §61.3, §60.9 (a different, longer window for a different job), §73.13a, §74.13.

---

### §81.4 The "Grand Checklist" — a HARD-GATE vs SIZE-MODIFIER two-tier structure a flat confluence score cannot express `strategy/engine.py`

The book builds one checklist incrementally across chapters 11→12→15→18 and finalises it at §18.6.
Stripped of the equity specifics, the structure is:

| # | Item | Tier |
|---|---|---|
| 1 | Recognisable candlestick pattern | **GATE** — fail ⇒ drop the candidate |
| 2 | S&R confirms; stop sits at/near the level | **GATE** |
| 3 | Volume ≥ 10-day average on the signal bar | **GATE** |
| 4 | Dow-theory context (primary/secondary trend, range/double formation) | soft |
| 5 | Indicators (MACD, RSI) confirm | **SIZE MODIFIER ONLY** |
| 6 | Reward:Risk ≥ 1.5 | **GATE** — *"even a trade that looks attractive must be dropped"* |

The load-bearing sentence, and the reason this earns a section, is the explicit asymmetry the
author draws around item 5: *"When indicators confirm, I increase my bet size, but when indicators
don't confirm I still go ahead with my decision to buy, but I scale down my bet size… I would not
do this with the first three checklist points."*

**COMPUTABILITY: the STRUCTURE is computable; several of the book's ITEMS are not.** Item 1
("recognisable pattern") and item 4 ("Dow patterns") are the discretionary geometry the KB has
repeatedly deferred (§24.5, §76.5). The *tiering* is what ports.

**Is it new?** Grep for `size.modif|only modulate|never a gate|hard.*soft` across `README.md` +
`sources/` returns **zero hits**. The KB has CTS confluence scoring → graded A+/B/C sizing
(§8.1, §34.4) and a top-down evaluation *order* (§34.2, structure→location→pattern→trigger). But a
weighted score and an evaluation order are both different objects from a **per-factor tier
declaration**: a scalar score cannot express *"this factor may raise conviction but may never veto,
and that one may veto but never raise conviction."* With a flat score, a strong reading on a
soft factor can silently substitute for a failed hard factor — which is exactly the failure mode
§34.2's ordering was introduced to prevent and only partially does.

**Concretely, the refinement:** mark each CTS factor as `gate` or `weight` in the engine, and
require that no accumulation of `weight` factors can carry a candidate past a failed `gate`. That
is a small, testable structural constraint, not a new rule.

**⚠️ Its epistemic status is one trader's stated personal practice, with no sample and no test.**
And the specific tier assignments **do not port** — item 5 puts MACD/RSI in the soft tier, whereas
for us RSI is refuted outright (§58.10a/§74.3, do not include it at any weight) and MACD crossovers
never cleared significance (§80). Take the *taxonomy*, not the *assignments*. Item 6 (R:R ≥ 1.5) is
already ours (`strategy/promotion.py`, R:R ≥ 1.5–2), and is anyway subordinate to the breakeven-
win-rate formula `win_rate > 1/(1+R:R)` (§35.2), which is strictly better than a fixed threshold.

**Two smaller notes from the same chapters, both reinforcement only:**
- §19.5's post-entry rule — *"once you place a trade, do nothing till either your target is achieved
  or stoploss is triggered… of course you can trail your stoploss"* — is a plain-language statement
  of rails 8/9/10 plus a permitted one-way trail. An automated agent **is** this rule; nothing to add.
- §8.x's *"a trade should satisfy at least 3 to 4"* of the 6 points is an M-of-N confluence quorum,
  functionally the CTS score threshold (§8.1). No new mechanism.

**Cross-refs:** §8.1, §34.2, §34.4 (what exists), §35.2 (the better R:R bar), §24.5/§76.5 (why its
gate items are not computable for us), rails 8/9/10.

---

### §81.5 "Well spaced in time" — a MINIMUM-TEMPORAL-SEPARATION requirement our level validator lacks `analysis/levels.py`

`analysis/levels.py` validates a level at **≥3 touches**. It does not, per grep
(`well spaced|well-spaced|minimum separation|min_separation|temporal separation` → **zero hits**),
require those touches to be separated in time. This source makes that requirement explicit and
repeats it three times:

- §11.3 step 3, on S&R construction: identify ≥3 price-action zones at the same level, and *"make
  sure these price zones are well spaced in time… the more distance between two price action zones,
  the more powerful is the S&R identification."*
- §17.4, on the double bottom/top, with an actual number: after the first extreme the price must
  trade away from it *"for at least 2 weeks (well spaced in time)"* before retesting.
- §17.5: *"the more number of times the price tests, and reacts to a certain price level, the more
  sacred the price level"* — the touch-count monotonicity we already hold.

**COMPUTABILITY: YES** for the separation constraint (a minimum bar gap between accepted touches;
≥2 weeks ≈ **≥10 trading sessions**, and on our 24/7 daily bars ≈ 14 calendar bars). **NO** for the
level *zone width* the book pairs it with — it admits the band is arbitrary (*"There is no specific
rule for this range, I just subtracted and added 3 points"*), so that half is discarded.

**Why it matters beyond levels — and the honest limit of that.** The KB's live gap on
`macd_divergence` (§58.10c, §80.14) is that popular sources specify divergence only qualitatively,
with no pivot-identification rule, lookback, or **minimum separation**. §55.1 says the detector must
compare *non-adjacent* pivots without defining "non-adjacent"; §58.10c supplies the only fully
specified detector we have (its conditions 1–4 encode ordering and recency). A concrete
minimum-separation constant of ~10 bars is a plausible `a_priori` value for that slot — **but this
book supplies it for double bottoms, not for divergence, and offers no derivation for it in either
context.** Recorded as a transferable prior, explicitly **not** as the divergence definition the KB
is looking for. See §81.6.

**Long-only / halal:** the double-*top* mirror is a **don't-buy / exit** filter only.

**Status: a cheap refinement to the existing touch validator; unvalidated by the source.** Same tier
as §76.3.

**Cross-refs:** `analysis/levels.py`, §34.3, §58.6 (S/R constructs outlasted formula indicators),
§55.1 + §58.10c + §80.14 (the divergence-specification gap), §76.3.

---

### §81.6 ⚠️ NEGATIVE FINDING — the book does NOT define MACD divergence. The concept is absent, not merely vague.

This was the highest-value thing the source could have supplied, so the finding is recorded
precisely rather than glossed.

**Grepping the full extracted text of all 177 pages for `divergen` returns 7 hits. Every one of
them is the indicator's own NAME** — "Moving Average Convergence and Divergence" — used in its
literal internal sense: *"a divergence occurs when the moving averages move away from each other."*
**Price-vs-oscillator divergence — the construct §58.10c and §80.14 need specified — is never
mentioned anywhere in the book, in any form.** There is no lower-low-in-price-vs-higher-low-in-MACD
passage to critique; the idea simply does not appear.

**Direct answer: NO.** This source supplies no definition of MACD divergence, precise or otherwise.
The §58.10c detector remains the only fully-specified one in the KB, and §80's demotion of
`macd_divergence` from "leading candidate" to "unproven candidate" stands untouched.

What the book does supply on MACD is already held and already assessed:
- The formulae, exactly (12-day EMA − 26-day EMA = MACD line; 9-period EMA of that = signal line),
  with a worked 40-row Nifty arithmetic table. **Computable but not new** — §54, §55.2 and the
  shipped `analysis/indicators.py` already carry MACD 12/26/9.
- Two entry readings: **centre-line crossing** and **signal-line crossover**. Both are the crossover
  family, which per §80 **never cleared even nominal significance**; §74.5's positive MACD-family
  result is on a different construct. Nothing to reopen.
- ⚠️ **A care-level flag worth recording.** The chapter body says the signal line is a *9-day
  **EMA** of the MACD line*; the chapter's own Key Takeaway #4 says it is the *9-day **SMA** of the
  MACD line*. The two are different indicators and the book never reconciles them. EMA is correct
  (Appel). This is the same class of internal inconsistency §77 flagged in the Warrior "Profit
  Trifecta" table — a reason to treat this source's unsourced numbers as loose, which is exactly the
  posture taken in §81.1 and §81.2 above.

**Cross-refs:** §58.10c (the detector we do have), §80.14 (why we still need one), §55.1, §55.2,
§74.5, §80 (crossover family closed), §77 (the internal-inconsistency precedent).

---

### §81.7 Where the book CONTRADICTS or explains our refuted items — two notes, neither actionable

The brief was to log refuted-family material only where the source *contradicts* the refutation.
Two places qualify, and both cut in our favour rather than against.

**(a) RSI — the book itself abandons the mean-reversion reading in trends.** §14.1 gives the
classical 30/70 reading, then immediately undercuts it: in a sustained uptrend *"the RSI will remain
stuck in the overbought region for a long time"* and the trader *"would be looking at shorting
opportunities but the stock on the other hand will be in a different orbit"* — so, it concludes,
*"if the RSI is fixed in an overbought region for a prolonged period, look for buying opportunities
instead of shorting."* That is a **momentum/persistence** reading, the structural opposite of the
mean-reversion reading refuted three times independently (§58.10a "the worst model in the book",
§74.3 −6.33bp p<0.01 on Bitcoin, our own sim). A beginner primer arriving at the same conclusion
from casual observation is mild corroboration that the refutation is not a crypto artifact.

**COMPUTABILITY: NO.** *"Fixed in a region for a prolonged period"* has no threshold — no bar count,
no persistence definition. It is not a rule; it is an observation. **No action.** And it does not
resurrect RSI in any form: §74.3 closed the family, and §80 records that our position is settled.

**(b) Bollinger — the book states the failure MECHANISM behind §74.4's negative result.** §15.2
teaches the standard mean-reversion trade (short at +2SD, buy at −2SD, target the 20-SMA) and then
documents its own losing case: when price hugs a band, *"the upper band expanded. This is called an
envelope expansion. The BB signal fails when there is an envelope expansion… BB works well in
sideways markets, and fails in a trending market."*

This does not contradict §74.4 (BB significantly negative on BTC, −5.34bp, p<0.01) — it **explains**
it, and the explanation is the same one §62.2 gives in closed form: buying into a decline is
variance-optimal only under genuine mean-reversion (`a < 0`), a regime never verified on crypto. It
also reinforces §74.4's demotion of the queued §54.15 ATR/stdev volatility-band candidate — the band
construct's *own textbooks* say it inverts in exactly the regime we deliberately trade.
**Reinforcement only; no action.**

**Cross-refs:** §58.10a, §74.3, §74.4, §54.15 (demoted), §62.2, §80.

---

### §81.8 ⛔ Discarded (no agent value)

Generous by design; this is a beginner equities primer and most of it is duplicate rather than
inapplicable.

- **The entire candlestick block, chapters 4–10 (~65pp, 16 patterns).** Marubozu, spinning top,
  doji, paper umbrella / hammer / hanging man, shooting star, bullish & bearish engulfing, piercing,
  dark cloud cover, harami, morning & evening star. The KB already holds this catalog from §1.3,
  §2.1, §59.5 and §70.7. The book's numeric thresholds (lower shadow ≥ 2× real body; doji open≈close
  within 1–2%; piercing = P2 engulfs 50–100% of P1) are conventional and add no discriminating power
  the existing primitives lack — and the whole block is subordinate to a **"prior trend"**
  requirement the book never defines mechanically (how many bars, measured how). Note §77's rule
  applies in reverse here: this source *does* give definitions, but a definition of an already-held
  primitive is not new content.
- **Gaps (§10.1) and the morning/evening star patterns built on them.** Both stars require a **gap
  down then gap up** across three sessions. **Structurally N/A** — §59 and §76.4 already ruled gaps
  inapplicable to 24/7 continuous spot, where a gap-shaped print is a **feed-health** question
  (§24.3 data-spike guard), not a signal. Roughly a fifth of the candlestick block dies with this.
- **All short setups** — bearish marubozu, shooting star, hanging man, bearish engulfing, dark cloud,
  bearish harami, evening star, double/triple top, range breakdown, BB +2SD short, MACD bearish
  crossover, Supertrend red flip. Excluded under the non-negotiable lens; the bearish geometries
  survive only as **exit / don't-buy filters** on held positions, per §24.1B.
- **§19.1's charting-software and data-vendor material (~4pp)** — Metastock/Amibroker/Zerodha Pi
  product tour, plus Ch 20's "On Kite" screenshots and colour-customisation instructions. Vendor
  documentation. It also advertises *"Artificial Intelligence and Genetic Algorithms"* as
  optimisation features — the exact non-reproducible black-box class **excluded** at §54/§58.16.
- **§19.5's "The Scalper" section.** 1-min/5-min timeframe, hold for minutes, RRR of 0.5–0.75
  acceptable, *"use margins effectively, do not over leverage."* **Triply excluded**: margin = riba;
  the RRR floor contradicts our breakeven-win-rate bar (§35.2, §57.4); and §74.10 says the intraday
  frame is where the crypto edge is *not*.
- **The Alligator indicator (Ch 20).** Three SMAs (13/8/5) with offsets; the buy condition requires
  *"all three MAs are separated"* with **no separation threshold given** ⇒ **COMPUTABILITY: NO** as
  written. Even fully specified it is the 10/20/50 SMA stack already noted at §26.2/§60, and §58.9
  found MA-formula indicators refuted across 48/48 models.
- **§5.4's signal-bar range filter** — avoid bars with range below 1% or above 10% of price. The
  upper bound is a crude, non-adaptive version of §54.20's **price-shock detector** (1-day range ≥
  ~5·ATR → crisis mode), which is volatility-scaled where this is a fixed percentage; on crypto a
  fixed 10% band would fire constantly. The lower bound *contradicts* §61.3's NR7 finding, which
  treats a narrow-range bar as a positive **precursor** — but they concern different bars (Zerodha's
  is the entry/signal bar, Farley's is the pre-breakout contraction), so this is not a genuine
  conflict and settles nothing. **No action.**
- **§12.2's "smart money" narrative** and the price×volume 2×2 expectation table (up/up = bullish,
  up/down = weak hands, down/up = bearish, down/down = weak hands). The *directional* content is
  §54.23's "volume confirms price"; the institutional-flow story is unobservable to us and, read as
  inference about who is trading, brushes the no-oracle rail (§6.4). Only the threshold survives, at
  §81.3.
- **§17.2's Dow accumulation → mark-up → distribution → mark-down phase model.** Qualitative,
  no thresholds, no measurement, no sample — the identical disposition §61.1 received (*"adds
  nothing here… qualitative restatement"*); §54's ER/ADXR/CSI/SSI stack subsumes it. Also brushes
  no-oracle in its "smart money knows first" framing.
- **§17.1 tenet 6, "all indices must confirm with each other."** Requires a market-breadth basket;
  already ruled **N/A (no crypto analog)** at §25.6/§54.24.
- **Ch 16 Fibonacci retracements.** Held at §59.6/§70 as low-weight confluence; anchor-swing
  selection remains the *"guessing game"* §70 recorded. No new ratios, no new anchoring rule.
- **§18.4's flag formation.** *"Price decline can last anywhere between 5 and 15 trading sessions"*
  is the only number given; the pattern itself needs two hand-placed parallel lines ⇒
  **COMPUTABILITY: NO**, same ruling as §76.5/§24.5. The 5–15 bar window is not enough to build a
  detector from.
- **§19.4's opportunity-universe construction** (Nifty 50, bid-ask spread, ≥500,000 shares/day,
  "EQ segment", *"make sure the stock is not operator driven — unfortunately there is no
  quantifiable method"*). Equity-market plumbing; the liquidity floor duplicates §60.9/§22.1, and
  the author himself concedes the last criterion is unquantifiable. Our allowlist is 3 assets, so
  §75.1's finding applies: universe *ranking* is a no-op at `|allowlist| = 3`.
- **All Indian-market specifics** — 9:15–15:30 session, the 3:20 PM entry convention that every
  candlestick trade setup is anchored to, NSE/BSE segments, rupee price examples, Nifty/Sensex.
  §76.4's ruling stands: a 24/7 market has no session, so a "close-of-session entry" is undefined
  for us (our daily close is an arbitrary UTC cut, not a settlement event).
- **Chapters 1–3 in their entirety** — the restaurant/food-street analogy for TA vs FA, the four
  assumptions (markets discount everything, how-over-why, price moves in trends, history repeats),
  OHLC definitions, line/bar/candlestick chart anatomy. Beginner scaffolding, fully saturated
  (§1, §3, §4).

---

### Net assessment (saturation-honest)

- **NEW, grep-verified absent, and worth logging: two — and both come from a 17-page appendix of
  copy-pasted vendor indicator notes, not from the 160 pages of actual textbook.** §81.1 **Aroon**
  (the continuous form of our own Donchian entry; a candidate trend gate sharing the entry's
  geometry, relevant because §58.2 contests the shipped ADX gate) and §81.2 **Supertrend** (a named
  one-way ATR ratchet trail satisfying rail 10 by construction, plus a third data point on stop
  width that must not be compared naively across ATR lookbacks). **Both are asserted with zero
  validation, and one has contradictory defaults inside the same section.** Logged as cheap
  ablation candidates, not build items — §70.5/§76.3 disposition.
- **SMALL REFINEMENTS (2):** §81.3 the **10-day** averaging window for §54.23's above-average-volume
  breakout filter — free, `a_priori`, costs no trials budget (§73.13a/§74.13); and §81.4 the
  **hard-gate vs size-modifier tiering** of confluence factors, which a flat CTS score cannot
  express. §81.5 adds a **minimum temporal separation between level touches** (≈10 sessions) to the
  ≥3-touch validator.
- **CLEAN NEGATIVE (the answer to the question this source was fed to answer):** §81.6 — **the book
  does not define MACD divergence at all.** All 7 occurrences of "divergen" in 177 pages refer to
  the indicator's own name. §58.10c remains the KB's only specified detector; §80's demotion stands.
- **REINFORCES:** RSI mean-reversion fails in trends, from the source's own admission (§81.7a,
  corroborating §58.10a/§74.3); Bollinger's failure **mechanism** in trending markets — envelope
  expansion — explaining §74.4's significantly-negative BTC result and re-justifying §54.15's
  demotion (§81.7b); take-every-signal / don't-cherry-pick trend-following (§54.11, §58.4); close is
  the most important price (§34.1); volume confirms breakouts (§54.23).
- **RE-CONFIRMS EXISTING RULINGS:** gaps and session-anchored setups are N/A to 24/7 spot (§59,
  §76.4); hand-drawn flag/trendline geometry is not computable (§24.5, §76.5); market breadth has no
  crypto analog (§25.6); genetic/AI optimisers are excluded black boxes (§54, §58.16).
- **EXCLUDED (halal):** the scalping section's margin advice (riba); all short setups → exit /
  don't-buy filters only.
- **No open defect is closed.** The KB's live questions — trade frequency (now redirected to
  horizon breadth by §79.1/§79.2), entry-channel lookback (§74.2/§79.5/§79.15), the exit/stop model
  (§79.7's ablation), the exit/entry ratio (§79.6), and the trials budget (§73.3) — receive **no
  parameter, no test, no sample** from this source that bears on any of them.
- **RECOMMENDATION — the fourth saturation call, and it should now be treated as closed.**
  **Stop feeding general technical-analysis primers and textbooks.** §24, §59, §76 and §77 each said
  a version of this; this source confirms it a fourth time, and the confirmation is sharper than the
  previous ones because Zerodha Varsity is a *good* example of the genre — free, well-regarded,
  carefully worked, with actual arithmetic tables — and it still yielded two unvalidated appendix
  indicators. **The genre's ceiling is not a quality problem; it is structural.** A primer teaches
  what an indicator *is*; this KB now only needs to know what an indicator *earns*, which requires
  controlled tests (§58), crypto-specific samples (§74), or validation theory (§73, §79). Feed those.
