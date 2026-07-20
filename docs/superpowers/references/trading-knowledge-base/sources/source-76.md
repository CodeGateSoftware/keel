[← Knowledge Base index](../README.md)

## Source 76 — "Chart Pattern Study guide" ("Chart Patterns v2")

> Ross Cameron / **Warrior Trading**, © 2021, 112pp. Free lead-magnet **slide deck**, not a book:
> it is ~95% annotated eSignal screenshots of US small-cap stocks (NVFY, CCXI, TOPS, PRAN, APOP,
> GNPX, DRYS…) on **1-minute and 5-minute charts**, with a handful of hand-drawn schematic diagrams
> carrying one-line captions. **There is no prose.** No chapter text, no rules paragraphs, no
> statistics, no methodology.

> ### ⚠️ Saturation verdict, stated up front — this is a near-duplicate of §24, and applies *less* well
>
> The KB was already assessed **saturated on chart-pattern catalogs** and the standing recommendation
> was to stop feeding them. This source confirms that recommendation rather than challenging it.
>
> Two things make it **weaker** than §24, not stronger:
>
> 1. **§24's catalog was timeframe-agnostic** (its author's main chart was the daily). **This one is not.**
>    Roughly half the setups here are anchored to the **US equity trading session** — pre-market highs,
>    pre-market pivots, the 9:30am opening-range breakout, gap-and-go, red-to-green, VWAP (session-anchored
>    by definition), and NYSE trading halts. **A 24/7 continuous spot market has no session**, so these are
>    not "hard to port" — they are *undefined*. This extends §59's ruling that price gaps are N/A to
>    continuous spot, and §60's note that equity-open microstructure has no crypto analog.
> 2. **The overlapping half is genuinely the same material as §24**, one layer down in timeframe:
>    flat-top breakout = ascending triangle (§24.1A), head & shoulders + inverted H&S (§24.5, deferred),
>    ABCD flag (**already deferred as harmonics**, README open-judgment #2 / §3.4 / §9.2), moving-average
>    pullback + micro pullback + first-pullback (= the **refuted** pullback/dip-buy family), whole-dollar
>    and half-dollar entries (= round-number S/R, §4.8/§23.6/§24.4), high-of-day break (= an intraday
>    Donchian N-bar high, §23.1/§27.1).
>
> **Additionally, and worth stating because it bears on three of the four open questions:** this source
> contains **no price targets, no numeric stop rules, no lookback parameters on any timeframe we trade,
> no backtest, no sample size, and no win rate anywhere in 112 pages.** It therefore contributes
> **nothing** to the entry-channel-lookback question, **nothing** to the stop/exit-model question, and
> **nothing** to the pattern-derived-target question — the one thing §24 *did* supply (`pattern_height`,
> §24.2) is absent here. Every parameter that does appear (5 candles, 10 candles, 20-EMA, 9-EMA) is
> asserted without derivation — a live illustration of §73.6's point that a rule quoted without its
> trials count `N` is not weak evidence but **no** evidence.
>
> **Net: 1 conditional keeper, 2 reinforcements, 1 structural N/A finding. Nothing changes.**

---

### §76.1 ⚠️ The consecutive-down-bar streak reversal — the book's ONLY mechanically-computable, high-frequency, breakout-uncorrelated trigger — and it lands squarely in the REFUTED family `strategy/rules/`

This is the one item in the source that engages the **top-priority open defect** (trade frequency), so it
gets stated precisely and then judged honestly.

The deck presents the same pattern twice, at two thresholds, as separate slides:

```
Slide 5  "5 MIN CANDLE TO MAKE A NEW HIGH"
         "First 5 min candle to make new high after 5 consecutive"   [5 consecutive red bars]

Slide 33 "1 MIN CANDLE TO MAKE A NEW HIGH"
         "First 1 min candle to make new high after 10 consecutive"  [10 consecutive red bars]
```

Generalised and stripped of timeframe, the trigger is:

```
streak_t = count of consecutive immediately-preceding bars with close < open   (or: lower high)
fire  if  streak_{t-1} >= N   AND   high_t > high_{t-1}
```

**COMPUTABILITY: YES — unambiguously.** This is the only pattern in the entire deck that requires
**zero human judgment**: no trendline to draw, no curve to fit, no neckline, no "U-shaped base". It is
two integer comparisons over OHLC. It is strictly more computable than anything in §24 except the
ascending-triangle/level-break, and unlike that one it is **not** a restatement of Donchian.

It also, on its face, satisfies two of the three criteria the frequency defect demands:

- **(i) precisely computable** — yes, above.
- **(ii) fires more often than a 40-day channel breakout** — yes, by a wide margin. A 5-consecutive-down-bar
  streak on daily BTC is a common event; a 40-day channel breakout happens ~2.6×/yr/asset.
- **(iii) not a dip-buy / mean-reversion setup** — **NO. It fails this, and it fails it fundamentally.**

**Buying the first higher high after a run of five or ten consecutive declining bars *is* the
catch-a-falling-knife trade.** It is the same family as the dip-buying our own sim refuted (~7% win rate
on high-confluence dip buys), as the RSI mean-reversion refuted three times independently
(our sim · §58.10a "the worst model in the book" · §74.3's −6.33bp p<0.01 on Bitcoin), and it fires
**hardest in downtrends**, which is exactly the pathology already documented. The absence of an RSI or a
support level does not change the family — §62.2 gives the reason in closed form: **buying into a decline
is variance-optimal only under genuine mean-reversion (`a < 0`), and that regime has never been verified
on crypto.** Cameron's trigger simply substitutes a *bar-count* proxy for oversold where RSI used the
indicator; the underlying bet is identical.

**VERDICT — logged as an unproven hypothesis in an already-refuted family, NOT as a lead.** This is the
same disposition §60.10 applied to Larry Swing's "FORCESWING" entry: *stricter or differently-specified
filters do not un-refute a refuted family.* Adding it would also cost trials budget we do not have
(`N ≤ 3` on 5yr, §73.3) on a direction three independent sources say is negative.

**The residual value is real but narrow — record it so the judgement is recoverable:**

- It is the **cleanest specification of a streak-exhaustion trigger anywhere in the KB.** Grep confirms
  nothing equivalent exists: §57.1's "N consecutive" is a *losing-trade* sequence breaker (a risk rail,
  not an entry), and §1's deceleration detector counts *shrinking-range* bars in the trend direction, not
  a directional down-streak. So if the **§62.2 corollary is ever acted on** — "any future scale-in/DCA or
  mean-reversion rule should gate on a *measured* `a < 0` (or low ER / H < 0.5), not be assumed" — then
  **this is the trigger to test on the assets that pass that gate**, precisely because it is boolean,
  parameter-thin and needs no indicator. §54.21's run-distribution classifier is, notably, built from the
  *same* run-length statistic this trigger keys on, which makes the gate and the trigger natural partners.
  That is a *conditional* future path, gated on a measurement we have not made — not a build item.
- Its **two thresholds contradict each other with no stated basis** (5 bars on the 5-min chart, 10 on the
  1-min). Neither is derived, tested, or reconciled. Treat both as arbitrary; if ever swept, the sweep is
  `sensitivity`, not `selection` (§73.13c).

**Halal/long-only:** the trigger is long-side as given, so no short→exit-filter conversion is needed. Its
mirror (first new *low* after N consecutive green bars) appears in the deck as a short; for us that is at
most an **exit / don't-buy** signal on a held position, never a short entry.

**Cross-refs:** contradicts nothing; is contradicted *by* §58.10a, §74.3, §62.2 and our own sim.
Related: §57.1 (different object), §1.5 deceleration (different geometry), §54.21 (the measurement that
would license it), §60.10 (the precedent for this exact disposition), §73.3 (why it cannot be sweep-fitted).

---

### §76.2 False-breakout / "trap" material — REINFORCES three shipped or queued items, adds no mechanism `strategy/engine.py`, `execution/executor.py`

The deck devotes an unusual share of its slides to breakouts that **fail** — "False Breakout Trap",
"Flag Pattern False Break", "Bull Flag Trap" (×2), "Bull Trap" (×3), "Whole Dollar Entry Fake Outs",
"1min/5min False Breakout then Rip". For a promotional deck selling breakout setups, spending this much
space on their failure mode is mildly notable, and it is the deck's most useful *directional* content
given that our primary rule is a breakout.

The one mechanical statement it does make is on the bull-flag slide:

> *"Price above this candle's high is considered a buy, **the close of the next candle would be
> confirmation of an entry**."*

i.e. a **two-tier trigger** — intrabar penetration = candidate signal, next-bar **close** beyond = the
confirmation that separates a real break from a wick-poke.

**COMPUTABILITY: YES**, trivially — but **NOT NEW.** This is the close-based confirmation principle the KB
already holds from three directions: §34.1's `stop_trigger=close|intraday` (close-based confirmation to
cut crypto whipsaw), §23.1's Donchian buy **on close** above the channel, and the executor's existing
close-validate / one-candle-validity order lifecycle (§2.2). The "traps" also restate §54.23's
low-volume breakout filter (*"a breakout without volume confirmation is a fakeout risk"*, as §61 already
put it) and §25.1's ADX gate rationale (*"buying false breakouts… markets that are basically just flat"*).

**No new discriminator is offered** — the deck never says how to tell a real break from a trap ex ante,
only shows both after the fact. Compare §58.12, which supplies an actual controlled result, and §58.1,
which supplies an actual order type. Reinforcement only; **no action.**

**Halal/long-only:** the "bear trap" mirror is a short setup → **don't-buy filter** only.

**Cross-refs:** reinforces §34.1, §23.1, §54.23, §25.1, §2.2. Adds nothing to §58.1's limit-entry finding.

---

### §76.3 "Nth attempt at a level finally breaks" — a small, unvalidated *refinement* candidate against §34.3 `analysis/levels.py`

Two slides state a level-interaction idea §34.3 does not quite cover:

- *"Double Top at Whole Dollar, then **Third Time It Breaks**"*
- *"Resistance at 4.00 then **Finally** a Breakout"* / *"Resistance at 9.50 then a Breakout"*

The framing: repeated **failed attempts** at a horizontal level are not evidence the level will hold —
they are a build-up, and the eventual break is the tradeable event.

**COMPUTABILITY: YES** — we already count touches. `analysis/levels.py` validates a level at **≥3 touches**,
and `is_round_number` already exists.

**But is it new?** Only marginally, and in a direction worth flagging rather than adopting. §34.3 holds
*"first retest of a validated level = best entry, **Nth bounce = exhausted**"* — that is about price
**bouncing off** support. Cameron's claim is about price **breaking through** resistance after N failures,
which is the same touch-count statistic read for the opposite event. The two are compatible (an exhausted
level is one that breaks) but the KB has never stated the breakout half.

**Status: logged as a cheap ablation candidate, NOT a build commitment** — the same status §70.5 got.
Concretely: `touch_count` at the broken level as an optional **grade** on a breakout signal, not a gate.
Caveats that keep it out of the build queue: (a) the source offers **zero** validation — no sample, no
win rate, no counterfactual on how often the level simply held; (b) §58.6's finding that
support-resistance constructs outlast formula indicators is supportive of *levels*, not of *this scoring
of them*; (c) it costs trials budget (§73.3) for a plausibly tiny effect, and our resistance level for
the primary rule is the Donchian channel high, which is a rolling extreme with no natural touch count.

**Halal/long-only:** long-side as stated (breaking resistance upward); the descending mirror is a
don't-buy filter.

**Cross-refs:** refines §34.3; related §4.8 round-number S/R, §23.6, §70.5 (same "logged, not built"
status), §58.6.

---

### §76.4 ⛔ STRUCTURAL N/A — roughly half the deck requires a market session that 24/7 spot does not have

Recorded explicitly because it is the single largest category in the source and because a future reader
should not have to re-derive it.

| Setup in the deck | Why it does not port |
|---|---|
| Break of **pre-market** highs (slide 16); break of **pre-market pivot** (17) | No pre-market exists. Crypto trades continuously. |
| **1-min opening range breakout** (19) | No open. The "opening range" is undefined. |
| **Gap and Go** (10, 10a, 10b); **Gap Down Reversal** (27); **History of Gap Fades** (41) | Gaps require a closed-then-reopened market. Already ruled N/A to 24/7 continuous spot by §59; a gap-shaped print on our feed is a **feed-health question** (§24.3 data-spike guard), not a signal. |
| **Red to Green move** (20) — price crossing the prior day's close after the open | Depends on an official daily close/open boundary. Our "daily close" is an arbitrary UTC cut, not a settlement event, so the level carries none of the meaning the setup relies on. |
| **VWAP** breakout / VWAP fade / first pullback after VWAP break (21b, 40, 40a–40k) — a large block | **VWAP is session-anchored by definition** — it resets at the open. With no open there is no anchor, and a rolling VWAP is a different indicator with different behaviour, not the same tool. Note §54.23 already holds VWAP for the KB, but as an **executor slippage tool** (split/pace a larger order), *not* as a signal — that stays the only sanctioned use. |
| **Trading halts** — shorting a halt resumption (38), shorting into a halt (38a), no-news squeeze then NYSE halt (31a, 31b) | No exchange-halt mechanism; also short-side, doubly excluded. |
| **Recent IPO breakout** (26, 26b, 26c) | No analog. A newly listed token would fail allowlist admission on liquidity (§22.1/§24.3) and on the §71.6 screening axes long before any pattern question arose. |
| **Short squeeze** (DRYS $4→$100, slide 30) | Requires a borrow/short-interest mechanic that presupposes the shorting we exclude; unobservable to us in any case. |

**Halal note on the same block:** the deck's Setup 3 and Setup 4 are explicitly **short** entries
("Short @ 55.25 with stop @ 55.51", "Short first candle to make a new low", VWAP Fade (Short), Trend Shift
Short, flat-bottom breakdown, bear-flag breakdown). All excluded under the non-negotiable lens; the
bearish geometries survive only as **exit / don't-buy filters** on held positions, per §24.1B.

---

### §76.5 Reinforced, nothing new

- **Flat-top breakout** ("buy first candle that breaks flat top" — equal highs forming horizontal
  resistance) = **§24.1A's ascending triangle**, which the KB already folded into the §23.1 breakout family
  as "a level-break we can already compute deterministically, rather than a bespoke detector." Identical
  call here. Computable — and already computed.
- **Buying break of high of day** (23, 23a) = an intraday **Donchian N-bar high**. Same rule as ours, at a
  timeframe we don't trade (§23.1/§27.1/§74.1).
- **Whole-dollar & half-dollar entries** ("buy first candle to break the .00 or .50") = **round-number S/R**,
  §4.8 / §23.6 / §24.4, already shipped as `is_round_number`.
- **Moving-average pullback** (1st/2nd 5-min pullback to the 9/20 EMA), **micro pullbacks** (Entry I/II/III
  — buy a stop above the prior bar's high after a 1-bar pause), **first pullback after a red/green**,
  **first pullback after signs of strength** — all the **pullback-continuation family**: mechanically these
  are §34.3's buy-stop-above-pullback-high and §54.16's Raschke First Cross, at 1-min. Nothing added; the
  family's crypto status is unchanged (trend-confirmed pullback kept per §61, context-free dip-buy refuted).
- **Ascending / descending trendline S/R** (7, 7a, 7b, 7c) — hand-drawn angular trendlines.
  **COMPUTABILITY: NO** — this is exactly the discretionary line-fitting the KB excludes (§55's
  "discretionary trend-line drawing", §24.5). Angular S/R is already listed in `analysis/levels.py`
  scope; nothing here specifies how to derive one deterministically.
- **Head & Shoulders / Inverted Head & Shoulders** (8, 8a, 21d, 21e) — **deferred to v2** per §24.5 and
  README open-judgment #2. No new geometry, no measured-move target given (§24 at least gave one).
- **ABCD flag pattern** (4, 4a–4d) — **already deferred**: ABCD is named explicitly in README
  open-judgment #2's harmonics deferral and in §3.4 / §9.2 / §17. The deck's version is drawn with two
  converging hand-placed trendlines, i.e. strictly *more* subjective than the equal-measured-move
  formulation §9.2 already recorded. **COMPUTABILITY: NO.** Deferral unchanged.
- **Bull flag** as a *shape* (a sharp advance, a shallow multi-bar pause, then continuation) — the
  continuation-pattern taxonomy of §24.4; §34 already noted bull-flag continuation as a long-side setup
  that ports. No parameters given here (how shallow, how many bars, on what volume), so nothing to add.

---

### §76.6 ⛔ Discarded (no agent value)

Generous by design — this is day-trading education for US small-cap stocks, and most of it is not
adaptable, merely inapplicable.

- **The entire visual medium.** ~95% of the 112 pages are eSignal screenshots of individual small-cap
  tickers with an arrow drawn on them. A screenshot of one trade that worked is an anecdote, not a rule;
  the deck never aggregates, never counts, never shows a loser except as a labelled "trap." §68.6/§64.1/§58.11
  cover why this is the weakest possible evidence class.
- **The whole 1-minute / 5-minute frame.** Our hold is ~21–24 days on daily bars. §74.10 is the decisive
  crypto-specific finding here: on Bitcoin, buy-and-hold **beats** technical rules intraday while SMAs win
  on daily — the deck's timeframe is the one where the evidence says the edge is *not*. Same disposition
  already applied to §55's M5 frame, §57's first-hour/1-min scalping, and §61's 60/120-min bonus report.
- **The instrument class.** Sub-$10 US small-caps with float rotations, dilution, and news catalysts
  (NVFY, TOPS, CCXI, PRAN, APOP…). "Parabolic Momentum CADC — Chinese Stock", "Buyout Headline that Ends
  up Not Being Real", "No News Squeeze then Halt by NYSE" — equity-specific microstructure with no crypto
  analog and, in the news-catalyst cases, brushing the no-oracle rail (§6.4).
- **"Good Pre-Market Chart" vs "Bad Pre-Market Chart"** (14, 14a) — a purely visual eyeball comparison
  with no stated criteria. Not a rule.
- **"Continuation and Multi Day Parabolic Momentum"** (28, 29) — screenshots of parabolic advances with no
  entry, stop, target or exit rule attached. Adjacent to §54.20's price-shock detector (1-day range ≥ ~5·ATR
  → crisis mode), which is quantified where this is not; nothing to fold in.
- **"Huge Panic Sell Off"** (33c) — a screenshot; the tradeable claim underneath it is §76.1's, already judged.
- **Warrior Trading branding, copyright page, and the slide-deck scaffolding.**

---

### Net assessment (saturation-honest)

- **NEW: nothing adopted.** The one genuinely novel, genuinely computable, genuinely high-frequency,
  genuinely breakout-uncorrelated trigger in the book (§76.1) is a **member of the refuted dip-buy family**,
  and is logged as such — a conditional future test vehicle *if and only if* §62.2's measured-`a<0` gate is
  ever built, never a lead. Recording that honestly is the finding.
- **REFINEMENT CANDIDATE (unvalidated, not queued):** touch-count-before-break as a breakout grade (§76.3),
  the breaking-through complement to §34.3's bouncing-off. Cheap ablation at best; likely not worth `N`.
- **REINFORCES:** close-based breakout confirmation (§34.1/§23.1), volume filter (§54.23), ADX gate
  rationale (§25.1), round-number S/R (§4.8/§23.6), flat-top = ascending-triangle = level-break folded into
  the Donchian family (§24.1A), pullback-continuation geometry (§34.3/§54.16).
- **RE-CONFIRMS EXISTING DEFERRALS:** H&S and hand-drawn trendline geometry (§24.5), ABCD (README #2 / §3.4).
- **N/A (structural, not shariah):** ~half the deck — pre-market, opening range, gaps, red-to-green, VWAP
  as a signal, halts, IPOs, short squeezes. A 24/7 market has no session (extends §59, §60).
- **EXCLUDED (halal):** all short setups — bear flags, flat-bottom breakdowns, VWAP fades, trend-shift
  shorts, halt shorts → **exit / don't-buy filters only**, never shorts.
- **No change to any next action.** The standing recommendation stands and is strengthened:
  **stop feeding chart-pattern catalogs.** The KB's live defects — trade frequency, entry-channel lookback,
  the exit/stop model, and the trials budget — are not addressable by pattern taxonomy, and this source
  supplies **no parameter, no target, no stop and no sample** for any of them. The productive directions
  remain §60.2 rank-and-fill, §58.10c/§74.5 `macd_divergence` as a *measured*-uncorrelated second rule
  class (§74.12), and sweeping `donchian_entry_n` far past 40 (§74.2).
