[← Knowledge Base index](../README.md)

## Source 70 — Two short promotional booklets: "The Truth About Fibonacci Trading" (Bill Poulos, Profits Run Inc., 2004, 22pp) + "Swing Trading Using Candlestick Charting with Pivot Point Analysis" (John L. Person, 2002, 16pp)

> **Both documents are free lead-magnets for a paid course, structurally identical to Source 55's
> WizardTrader booklet** — short, real content up front, a course pitch at the back ("Instant
> Profits" for Poulos; Person's "Target Trading Techniques" course + `nationalfutures.com`
> subscription). Neither is a backtest or a controlled study; both are chart-narrated demonstrations
> ("here's what happened" after the fact), the weakest evidentiary form in this KB.
>
> **PDF 1 ("fib.pdf") is titled "The Truth About Fibonacci Trading," not "...Secrets"** as
> hypothesized going in — **and it is promotional, not critical/debunking.** It sells Fibonacci
> retracement/extension levels as "useful... but not the holy grail," walks 8 chart examples (mixed
> outcomes — including one explicit loser, Example 4.1), and pitches a course at the end. It does
> **not** settle the deferred-to-v2 Fib question in favor of building more Fib tooling — if anything
> it reinforces the existing caution, because the author's own text twice concedes the two structural
> problems that motivated deferring harmonics/Fib-inversion in the first place: **which level will
> hold is unknowable in advance**, and **which swing point to anchor from "becomes a guessing
> game."** Those are admissions against interest from a source trying to sell a Fibonacci-based
> course — worth more than a generic skeptic's essay would be, without being a rigorous refutation.
> See §70.3.
>
> **PDF 2 ("swingtradingpivot.pdf") is a genuine primary source for classic floor-trader pivot
> points** — the P/R1/R2/S1/S2 formula, not just a name-check — bundled with a near-total-duplicate
> candlestick-pattern catalog. Pivots are **formulaic** (one-shot arithmetic on the prior period's
> H/L/C), not touch-validated like `analysis/levels.py:find_levels`. Per §58.6/§58.9's finding that
> **touch-validated support/resistance outlasted formula indicators**, pivots sit closer to the
> "formula" side than the "S/R construct" side — this source gives no controlled test either way, so
> that placement is a hypothesis, not a settled fact. The source's framing is **overwhelmingly
> intraday/futures** (60-min/15-min charts, Sugar/Silver/Dow/S&P/Treasury-Bond futures), which is out
> of scope for a 21–24 day daily-bar hold — **but** the formula itself takes the *prior period's*
> H/L/C as input, and the author explicitly computes Daily, Weekly, *and* Monthly pivots from the
> same formula, so a **daily-bar pivot computed from the prior daily bar, or a weekly pivot from the
> prior week**, survives the intraday objection cleanly. See §70.5.

---

### 70.1 Fibonacci retracement/extension mechanics — reinforces existing `indicators.py` tooling, no action
**Module: `analysis/indicators.py`**

Poulos's method: identify a Swing High and Swing Low (a short-term extreme with **≥2 lower highs /
higher lows on both sides** — exactly the `lookback=2` pivot definition already used by
`levels.swing_highs`/`swing_lows`), then compute retracement levels pulling back from the high and
extension levels projecting beyond it. This is precisely what `indicators.fib_retracements()` and
`fib_extensions()` already do. No new mechanism.

- → **Reinforces** the existing swing-pivot definition (independently arrived at by a different
  author, same 2-bar-each-side rule) and the already-implemented Fib level math. **No action.**

### 70.2 Ratio-set discrepancy — logged, not adopted
**Module: `analysis/indicators.py`**

The book's ratio sets differ from ours:

| | Retracements | Extensions |
|---|---|---|
| **Poulos (fib.pdf)** | 0.236, 0.382, 0.500, 0.618, 0.764 | 0, 0.382, 0.618, 1.000, 1.382, 1.618 |
| **`indicators.py` (shipped)** | 0.382, 0.5, 0.618, **0.786**, **0.886** | **1.272**, 1.618 |

The two retracement sets diverge at the tails (we have 0.786/0.886, the book has 0.236/0.764); the
extension sets diverge almost entirely (we have a narrow 2-point set, the book has a 6-point set
including 1.0 as a "target").

- → **Not adopted as a change.** The book itself supplies the reason not to chase this: per §70.3,
  *"there is no way of knowing which level will provide support"* — its own examples show 0.236 as
  the *weakest* performer and the rest roughly tied, which is not a basis for picking one ratio set
  over another. **Logged only as a sweep candidate**: if Fib retracements are ever promoted past
  low-weight confluence, the *ratio set itself* — not just whether to use Fib at all — is an
  unexamined parameter (`fib_ratios ∈ {current 5, Poulos's 5, union of both}`), consistent with the
  KB's standing rigor bar (§54.10/§54.11) rather than inherited from any one source.

### 70.3 ⭐ The book's own admissions-against-interest → reinforces (does not overturn) the deferred-to-v2 Fib judgment
**Module: `strategy/rules/` (harmonics/Fib-inversion — remains deferred)**

Two structural admissions, made by an author selling a Fibonacci-based course, that argue for
caution rather than adoption:

1. *"There are a few problems to deal with here. First, there is no way of knowing which level will
   provide support. The 0.236 level seems to provide the weakest support, while the other levels
   provide support with approximately the same frequency. Second, the market will not always resume
   its uptrend after finding temporary support, but instead continue to decline."* (p.11, echoed for
   extensions p.20.)
2. *"Another problem is determining which Swing Low to start from in creating the Fibonacci
   Retracement Levels. One way is from the last Swing Low... Another is from the lowest Swing Low of
   the past 30 days. The point is, there is no one right way to do it, and consequently **it becomes
   a guessing game**."* (p.12, repeated verbatim at p.21.)
3. Closing line: *"Alone, Fibonacci Levels will not make you rich... never enter or exit a trade
   based on Fibonacci Levels alone."*

This is **not a rigorous debunking** — no sample size, no win-rate, no controlled test, just 8
chart-narrated examples (one of which, Example 4.1, is an explicit loss: *"the market gapped down
through all levels of support and never looked back. A long trade here would have been a loser."*).
But it is a **useful, if modest, data point**: the KB's most-valued recent material has been
negative/cautionary findings from sources that had no reason to manufacture them (§58's controlled
negative results), and here the *opposite* case — a promotional source *still* concedes the anchor-
selection step is "a guessing game" — corroborates the standing v2-deferral rationale for
harmonics/Fib-inversion (**discretionary curve-fitting, overfit**) from an unexpected direction,
rather than giving grounds to promote Fib out of deferral.

- → **No change to the deferred-to-v2 status.** Plain Fib retracements/extensions remain shipped as
  low-weight confluence (§59.6); harmonics and Fib-inversion remain deferred. This source is
  corroborating, not decisive — it's one promotional booklet's honesty, not a controlled study.

### 70.4 ⛔ Bearish Fib setups → exit/don't-buy filters, per the standing lens

Every worked example is presented direction-neutral ("the same points made... are equally applicable
to markets in a downtrend"), but the practical use in a downtrend is a short entry. Per the
non-negotiable adaptation lens: a Fib resistance level rejecting price on a held long is a candidate
**exit signal**; it is never a new short entry. No new mechanism — same conversion already applied
throughout the KB.

---

### 70.5 ⭐ Classic floor-trader pivot points (P/R1/R2/S1/S2) — NEW formula candidate, formula-vs-S/R status unresolved
**Module: `analysis/levels.py`**

Person's formula, from the prior period's High/Low/Close:

```
P  = (H + L + C) / 3
R1 = (P × 2) - L
R2 = P + H - L
S1 = (P × 2) - H
S2 = P - H + L
```

(Only two levels each side are given in this source — no R3/S3, unlike some fuller floor-pivot
variants elsewhere.) The pivot and its four bands can be computed for **any period** — the book
demonstrates Daily, Weekly, and Monthly pivots from the same formula, each computed once per period
from that period's own prior H/L/C.

**Is this a support/resistance construct or a formula indicator?** Genuinely ambiguous, and this
source does not resolve it either way — no controlled test is offered, only anecdotes (a monthly S1
of 6.09 that missed the actual low by two ticks on Sugar futures; a weekly S1 that landed within 9
points of the low during an S&P selloff; several near-misses of 10-16 "ticks"). Two considerations
pull in opposite directions:

- **Toward "formula indicator" (the side §58.9 found refuted 48/48 times):** unlike
  `levels.find_levels` — which requires **≥3 actual touches** by real price pivots before a level
  counts — a floor pivot is **pure one-shot arithmetic** on a single prior bar. It does not require
  the market to have actually respected that price before; it asserts a level from H/L/C algebra
  alone, the same category error (deriving a level from a formula rather than from observed
  behavior) that made ATR-band volatility breakouts (§54.3/§58.7) and 48/48 moving-average models
  (§58.9) lose to the Donchian/HHLL channel.
- **Toward "S/R construct" (the side §58.6 found durable):** the inputs are the *actual traded*
  high/low/close of the prior period — not a smoothed average or a fixed multiplier of volatility —
  so it is closer in spirit to Donchian's "yesterday's real extremes" than to an EMA crossover's
  synthetic curve. And the self-fulfilling-prophecy logic that already justifies
  `is_round_number()` (many market participants compute and watch the same number) applies at least
  as strongly here — pivot points are one of the most widely disseminated numbers in retail
  technical analysis (the book itself sells a subscription service that just faxes/emails these
  numbers to clients).

**Recommendation: log as an untested candidate for the existing "magnet level" family in
`analysis/levels.py`, not a validated rule.** A daily pivot computed from the prior *daily* bar (and
a weekly pivot from the prior *week*) is mechanically trivial to add (`pivot_points(prior_high,
prior_low, prior_close) -> dict[str, Decimal]`, same shape as `fib_retracements`/`fib_extensions`)
and daily/weekly framing sidesteps the intraday objection cleanly — **this is not the source's
native framing, and that gap must be named rather than assumed away**, per the task brief. Before
building it, run the cheap ablation the KB's rigor bar already demands (§54.10/§58.0): does a pivot
level add anything over `find_levels`'s touch-validated levels + `is_round_number`'s magnet levels,
on the same data? Given `find_levels` already captures genuine multi-touch S/R and round numbers
already capture the "everyone's watching this number" effect, a third magnet-level source is
**incremental, not clearly additive** — worth a one-flag sweep, not a build commitment.

### 70.6 "Rule of Multiple Verification" → reinforces CTS confluence, no action
**Module: `strategy/engine.py`**

Person cites Arthur Sklarew (1980s) via a secondhand paraphrase: *"the accuracy of any technical
price forecast can be improved greatly by... not rely[ing] solely on one single technical signal or
indicator, but look[ing] for confirmation from other technical indicators."* His own examples pair
a pivot-point target with a candlestick reversal signal (and sometimes a chart pattern) before
acting. This is the same confluence principle already central to CTS scoring (§8, §34.4) and
independently restated by nearly every source fed to this KB (most recently §59.13). **No new
mechanism — pure reinforcement.**

### 70.7 Candlestick pattern catalog → reinforces `analysis/candles.py`, near-total duplicate
**Module: `analysis/candles.py`**

Hammer/hanging-man, shooting star, doji (gravestone/dragonfly/rickshaw variants), spinning tops,
evening star, bearish/bullish engulfing, dark cloud cover, harami/harami cross, morning star,
piercing pattern, and the Falling/Rising Three Methods continuation patterns — this is the identical
candlestick catalog already extracted from Sources 1/2/55/59 and implemented in `analysis/candles.py`
(pin bars, doji, engulfing-family, three-bar reversal, tweezer). No new pattern, no new
disambiguation rule not already covered by `pattern_confidence()`'s grading approach.

- → **No action.** The one incidental detail worth a passing note: the book's Harami discussion adds
  *"if this formation occurred on high volume or at an important Pivot Point... a short position
  would be warranted"* — i.e., volume + level-confluence upgrades a Harami's grade, which is the same
  shape as `pattern_confidence()` already implements and adds nothing new to it.

### 70.8 3-EMA crossover ("VMA," 4/9/18-period) → duplicate, reinforces KISS exclusion
**Module: `analysis/indicators.py`**

A three-exponential-moving-average system (4, 9, 18 periods) reading "golden"/"dead" crossovers is
shown once (US Dollar Index chart) with no formula detail beyond the periods. This is the same MA-
crossover family already logged and exhaustively covered (§23/§26/§27/§37/§54.16/§59.10) and already
subject to the **48-model negative result** (§58.9 — no MA crossover variant was profitable on a
portfolio basis). No new parameter worth sweeping; reinforces KISS (§26) and §58.9's caution rather
than adding anything.

### 70.9 Gap classification (breakaway/midpoint/exhaustion) → reinforces §59.4, N/A to continuous crypto
**Module: `execution/guards.py` (data-spike guard)**

The Sugar futures chart labels a "Break Away Gap," "Mid Point Gap," and "Exhaustion Gap" sequence
used (alongside the pivot target) as one leg of the "multiple verification." Identical
classification already logged at §59.4 and already assessed as a structural artifact of **markets
that close** — irrelevant to continuous 24/7 spot crypto except as a feed-health question for the
existing data-spike/bad-tick guard (§24.3). No new content; reinforces the prior assessment.

---

### 70.10 ⛔ Halal exclusions

- **Instrument context throughout swingtradingpivot.pdf is futures** — Sugar #11, Silver, Dow Jones
  Industrial, S&P 500, and **US Treasury Bond** futures (an interest-rate instrument — riba-adjacent
  by construction, though used here only as a price chart, not as a strategy involving the interest
  itself). Author is a "22-year veteran of the Futures and Options Trading industry." None of this
  blocks extracting the **pivot arithmetic**, which is instrument-agnostic OHLC math and is treated
  the same way this KB has always extracted formulas from otherwise-excluded-instrument sources
  (Stanzione's forex/commodities ebooks, §23/§27/§37) — but the worked examples themselves are not
  usable as spot-crypto case studies.
- **"Sell short a position"** (Harami-cross + pivot-resistance confluence example, p.7) — converted
  to an exit/don't-buy filter on a held long per the standing lens; never a new short entry.
  Similarly, every bearish Fib setup in fib.pdf (§70.4).
- **No leverage, margin, or carry content appears in either source directly** (fib.pdf is pure chart
  geometry; swingtradingpivot.pdf's futures framing carries the *implicit* margin/leverage of futures
  trading but never states terms) — nothing to actively exclude beyond the instrument-context note
  above.
- **Intraday/scalping framing** (60-minute and 15-minute chart pivot examples, swingtradingpivot.pdf
  Ch.4) — out of scope per the anti-scalping rail (§4.1) and the 21–24 day daily-bar hold; the
  **Daily/Weekly/Monthly pivot variants the same source also demonstrates are the ones that survive
  this objection** (§70.5).

### 70.11 Discarded (no agent value)

- **fib.pdf:** cover page; the closing sales pitch for the "Instant Profits" course (manual, CD-ROM,
  DVD, "4 trading blueprints," 90-day guarantee, `instantprofitstodaycom` link) — pure course-funnel
  copy, same structure as Source 55's WizardTrader upsell.
- **swingtradingpivot.pdf:** author bio (floor-trading pedigree, George Lane/Dan Gramza/Steve Nison
  name-drops); the closing pitch for the "Target Trading Techniques" course and the
  `nationalfutures.com` subscription service (daily/weekly/monthly pivot numbers by fax/email/Excel
  sheet — a service offering, not a technique); the CFTC-style futures/options risk disclosure
  (p.16, N/A — we trade spot); repeated screenshot chrome ("PDF created with FinePrint pdfFactory Pro
  trial version") on every page.

---

### Net assessment (saturation-honest)

**Two short, thin, promotional sources — one incidental confirmation, one small genuine addition,
nothing that changes a standing judgment.**

- **fib.pdf ("The Truth About Fibonacci Trading," Bill Poulos, 2004):** **promotional, not
  critical/debunking** — the title suggested going in that it might be a skeptic's essay; it is
  instead a course lead-magnet, structurally identical to Source 55. It does **not** supply grounds
  to promote harmonics/Fib-inversion out of v2-deferral. If anything it mildly **reinforces** the
  deferral, because the author's own text twice admits the two problems (unknowable which level
  holds; anchor-swing selection is "a guessing game") that are the actual reasons harmonics/
  Fib-inversion were deferred as overfit/subjective in the first place (§70.3) — an admission against
  interest from a source with every incentive not to make it, though still far short of a controlled
  refutation. **No code change.**
- **swingtradingpivot.pdf ("Swing Trading Using Candlestick Charting with Pivot Point Analysis," John
  L. Person, 2002):** genuine primary source for the classic floor-trader pivot formula (P/R1/R2/S1/
  S2), which the KB did not previously have in worked form. Its status relative to §58.6/§58.9 is
  **unresolved by this source** — pivots are formulaic like the refuted MA/ATR-band models, but
  derived from real traded H/L/C like the durable Donchian/HHLL construct, and no controlled test is
  offered either way. **Worth a one-flag ablation sweep as a daily/weekly "magnet level" candidate in
  `analysis/levels.py`, not a build commitment** — it may be subsumed by the existing touch-validated
  `find_levels` + `is_round_number` magnet levels rather than adding anything. The rest of the source
  (candlestick catalog, 3-EMA crossover, gap classification, confluence principle) is a near-total
  duplicate of material already in the KB from Sources 1/2/23/26/27/37/54/55/59.

**Recommendation:** do not seek out further single-technique promotional booklets from either
publisher (Profits Run / `nationalfutures.com`) — both patterns (lead-magnet + course pitch,
anecdote-not-backtest) are now established and are unlikely to yield more than this source did.
The one live thread worth pulling from this pair is prototyping `pivot_points()` as a cheap,
untested `analysis/levels.py` candidate and running it through the same ablation discipline already
applied to every other unvalidated candidate in this KB.
