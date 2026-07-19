[← Knowledge Base index](../README.md)

## Source 55 — "Killer Patterns" (WizardTrader.com, 2006–2009, 22pp)

> **Low-grade source, one genuinely useful idea.** This is a **free affiliate lead-magnet** (pp. 1–4 and
> 21–22 are branding, 60%-commission reseller terms, newsletter CTAs and the CFTC disclaimer), leaving
> ~8 pages of content. No backtests, no statistics, no sample sizes — the author's stated basis is
> *"my trading, observations and reading of the markets over the last decade."* Every chart is
> **EURUSD M5** (5-minute forex), i.e. exactly the scalping timeframe our §4.1 rail rejects.
>
> Against Source 54 (Kaufman, 1,232pp) this is not comparable in rigour, and **most of it is already
> covered**: we have `macd()` (12/26/9 + histogram, `analysis/indicators.py:176`), `rsi_divergence()`
> (`:140`), swing-pivot detection, MACD-up as a confluence factor (§25.1, §25.3) and Elder's
> weekly-MACD tide (§54.24).
>
> **The one thing worth keeping is §55.1** — a mechanically-specifiable *ordinal strength ladder* for
> divergences. We have divergence as a **boolean** (`rsi_divergence -> bool`, CTS weight 2); this source
> grades it. That is directly analogous to `analysis/candles.py:pattern_confidence()`, which already
> grades candlestick patterns, so the shape fits machinery we have.

---

### 55.1 ⭐ Divergence **strength ladder** → `analysis/indicators.py` + CTS weighting

The book's substantive contribution: divergences are **not equal**, and the ranking is a function of two
independently-observable facts — what price did at the second/third extreme, and what the histogram did.
Reading its four named tiers as a table (bullish form; bearish is the mirror):

| Tier | Price at the later extreme | MACD histogram | Book's rank |
|---|---|---|---|
| **Triple** | third *lower* low | third *higher* bottom | strongest of all |
| **Strongest** | new *lower* low | *higher* bottom | "nearly always signal good trades" |
| **Next best** | **double bottom** (equal depth) | *higher* bottom | weaker |
| **Weakest** | new *lower* low | **double bottom** (equal) | weakest |

- → **NEW, and mechanically testable:** replace the boolean `rsi_divergence` / add
  `macd_hist_divergence` returning an **ordinal grade** (`triple > strong > next_best > weak | None`)
  rather than a flag. The classification needs only the pivot lists we already compute
  (`indicators._swing_highs`/`_swing_lows`) plus equality-within-tolerance on the two extremes —
  the same `_approx_equal` tolerance idiom `analysis/candles.py:155` already uses for tweezers.
- → **Feeds CTS directly.** `strategy/indicators_cts.py:59` currently scores `rsi_divergence` as a flat
  weight-2 boolean. A graded divergence lets CTS weight a triple divergence above a weak one instead of
  treating them identically — consistent with the **A+/B/C conviction sizing** already in §34.4.
- ⚠️ **Implementation detail the book is explicit about, and it matters:** *"There can be some space
  between the tops or the bottoms that make up each MACD histogram pattern — these tops (or bottoms)
  don't have to immediately follow each other."* A detector that only compares **adjacent** pivots will
  miss most real divergences. Our `rsi_divergence(lookback=...)` should be checked against this.
- ⚠️ **Unvalidated.** No win-rate, no sample. This is a *hypothesis to run through the harness*
  (backtest → paper → promotion gate), not an established edge. Given §54.10's expectations-first
  rigour, grade the ladder empirically before trusting the book's ordering — the ranking is plausible
  but asserted, not measured.

### 55.2 Histogram **slope** as the trigger, and the confirm/continuation pair → refines §25.3

- *"The slope of the MACD histogram is more important than whether or not it is above or below the
  centre line… trade in the direction of the slope."* Trigger = the histogram **ticks** (one bar turns
  against the prior bar), not a zero-line cross. Best long: histogram **below** the centre line and slope
  turns **up** (bears exhausted).
- **Confirmation rule:** price and histogram making new extremes **at the same time** confirms trend
  continuation (both new highs → expect higher).
- **Continuation-without-price rule:** a new histogram extreme **without** a matching price extreme still
  implies continuation, *"even stronger when the histogram has reached its highest/lowest level for the
  past three or four months."*
- → **Refines** the existing binary `macd_up` confluence factor (§25.3): slope-direction + centre-line
  position + "extreme over N months" are three cheap, already-computable discriminators over one flag.
  The 3–4-month extreme window is a concrete, testable parameter.
- ⚠️ Note the **self-contradiction**: the book insists these signals are *"more worthwhile on weekly
  charts and on charts of a longer time frame… there are just too many moves up and down on the daily
  charts and on the charts of even shorter time frames to be useful"* — while illustrating every example
  on **M5**. Take the guidance, discard the examples. See §55.5.

### 55.3 Two-bar trailing stop → a tighter variant for `execution/executor.py`'s trail set

After entry, *"this stop should then be moved up to lock in paper profits so that it is placed just
below the lowest price level for the last two bars"* — ratchet-only (*"if prices start to rise, your stop
doesn't move"*, mirrored for longs).
- → A **2-bar channel trail**: strictly tighter than the 10-day Donchian/channel-low trail (§23.2/§27.1)
  and than ATR-scaled trails (§17.3). Trivial to add as another `trail_method` option swept by the
  backtester.
- ⚠️ **Expect it to underperform on crypto** and be honest about why: a 2-bar low is *exactly* the
  shakeout-prone stop §34.1 was added to fix ("stop-out artists"), and milestone-6's diagnosed defect was
  **stops too tight for crypto**. Worth including in the sweep only as a **lower bound** — if a 2-bar
  trail ever wins, that is evidence about the asset's noise profile (§54.1 ER), not a vindication.

### 55.4 Downtrend-break → retest-of-prior-low **long** setup (the book's only long-only setup)

p.20, the one buy setup: **(i)** a downtrend line is broken by rising prices; **(ii)** prices then decline
back to a **previous low** — *"at this point there is a good buying opportunity."*
- → This is **structurally the same trade we already run**: a first retest of a validated level after a
  structure break — i.e. §34.3's *"first retest of a validated level = best entry, Nth bounce = exhausted"*
  combined with the pullback-continuation family (§2.1/§7.1). **Reinforcement, not new.**
- The one addition worth noting: the book qualifies the setup as **better when the broken trend's slope
  was unusually steep** and when *"the pullback… occurs with falling volume"* — the falling-volume
  qualifier aligns with Kaufman's volume work (§54.23) and is cheap to test as a filter.

### 55.5 Timeframe guidance reinforces low-turnover (§28.3) — cite it, don't credit it

The explicit *"weekly and longer ≫ daily; shorter timeframes have too many moves to be useful"* is a
useful independent restatement of our **anti-scalping rail (§4.1)** and the **low-turnover-as-compliance**
principle (§28.3). It arrives from a pure-mechanics angle (signal-to-noise) rather than a Shariah one,
which is worth having on the record — but it changes nothing.

### 55.6 ⛔ Excluded (halal / spot / long-only)

- **All bearish setups → shorts.** The book is short-first: it opens with *"Entering a **short trade**
  based on three sell signals would be a no-brainer"*, and every bearish divergence tier instructs *"go
  short."* Per the non-negotiable adaptation lens, all of these convert to **exit / don't-buy filters on
  a held long** — never a short. The bearish-divergence grade from §55.1 is therefore an **exit-side
  confidence score**, which is genuinely useful: a *triple* bearish divergence is a stronger exit signal
  than a *weak* one.
- **The M5/scalping framing** — every chart is 5-minute EURUSD. Rejected by the min-move/anti-scalping
  rail (§4.1) and §28.3. Same disposition as §25's scalping framing (source-25.md:79): keep the geometry,
  discard the timeframe.
- **Hand-drawn trend lines as a primary signal** (p.20). The book's own guidance is subjective and
  untestable — *"don't try and force the issue by attempting to identify patterns that just aren't
  there… you'll know when you've really found a great set up fairly quickly."* That is the same class of
  discretionary judgement we exclude under no-oracle (cf. Elliott/Gann). Our **mechanical pivot-clustered
  levels** (`analysis/levels.py:find_levels`, ≥3 touches) already cover the testable part.
- **FX framing** (pips, EURUSD/GBPUSD pairs) → converted to %/ATR per the standing lens.

### 55.7 Discarded (no agent value)

Cover and branding pages; the **free-rebranding / 60%-affiliate-commission** offer and its "promote in
forums, by PPC, paid advertising" instructions; repeated *"Visit WizardTrader.com…"* footers on all 22
pages; newsletter sign-up CTAs ("Must Have Discoveries From a Trading Veteran"); copyright/reproduction
terms; the cross-sell to the paid Wizard Trader eBook (*"combine the trading set-ups here with those in
the Wizard Trader eBook"* — the book repeatedly defers its own confirmation logic to a product it is
selling); the CFTC Rule 4.41 futures/options disclaimer (p.22, N/A — we trade spot).

### Net assessment (saturation-honest)

**Heavily saturated — one keeper.** MACD, MACD histogram, divergence detection, swing pivots,
trailing stops, retest-entries and multi-timeframe bias are all already in the KB and mostly already in
code. Of ~8 content pages:

- **New:** §55.1's divergence **strength ladder** (boolean → ordinal grade, and the non-adjacent-pivot
  caveat). One idea, unvalidated, but cheap to implement on existing primitives and it plugs into CTS
  and the A+/B/C sizing we already have.
- **Refines:** §55.2 (slope + centre-line + N-month-extreme over a bare `macd_up` flag), §55.3 (a 2-bar
  trail as the tight lower bound of the trail sweep — expected to lose, informative if it doesn't).
- **Reinforces:** §55.4 (retest-after-break = §34.3 + §2.1), §55.5 (longer timeframes = §4.1/§28.3).
- **Excluded:** the entire short side, the M5 scalping frame, discretionary trend-line drawing.

**Recommendation:** implement §55.1 as a graded detector and let the harness rank the tiers empirically;
treat the book's own ordering as a hypothesis. Do not seek out more from this publisher — it is a funnel
for a paid product and defers its actual confirmation logic to that product. **This stream is exhausted
at one source.**
