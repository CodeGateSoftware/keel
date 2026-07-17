[← Knowledge Base index](../README.md)

## Source 24 — "10 Chart Patterns Every Pro Trader Should Know" (Vince Stanzione, ebook, 24pp)

> Second Vince Stanzione / Deriv **ebook** (companion to [Source 23](./source-23.md)). Same CFD-broker
> promo framing (premise = leveraged CFDs, freely short — halal-excluded), same "these patterns work on
> stocks/forex/**crypto**" claim. A **beginner chart-pattern catalog** (H&S, double top/bottom, cup &
> handle, rounding top/bottom, ascending/descending triangle, rising/falling wedge).
>
> **Saturation-honest:** low new signal. Double top/bottom already have *fuller* rules than this book
> gives (§16.1/§21.1); candlesticks/timeframes/measured-move targets are already covered. The one useful
> thread is that **several of the bullish patterns are breakout / buy-strength setups that reinforce the
> §23.1 Donchian breakout thesis** — the direction we're pivoting toward after dip-buying was refuted.
> Consistent with our standing judgment, the **subjective/discretionary patterns are deferred to v2**
> (same treatment as harmonics — hard to detect deterministically, overfit-prone).

---

### 24.1 The 10 patterns, sorted by our long-only + mechanizability lens
Author gives each pattern an entry, a stop, and a **measured-move target (= the pattern's own height)**.
Sorting them by what's actually usable for a deterministic, long-only spot agent:

**A. Bullish breakout / buy-strength → reinforce the §23.1 breakout family (KEEP as candidate inputs):**
- **Ascending triangle** — horizontal **resistance** + rising support (higher lows into a flat ceiling);
  **buy on close above resistance**, target = triangle height. This is essentially a **range/resistance
  breakout** and is the most *mechanizable* pattern here — it's the same buy-strength thesis as the
  Donchian N-bar-high breakout (§23.1). Horizontal resistance = an S/R level (§4.8) we already detect.
- **Cup & handle** and **rounding bottom / saucer** — bullish base → breakout; buy on the breakout,
  target = **base/cup height**. Author notes he sees rounding *tops* in BTC specifically. Same
  breakout-of-a-base idea; geometry is fuzzier (see deferral note).
- **Falling wedge** — contracting range that breaks **up** (bullish); buy on breakout, target = wedge
  height. (Author warns many misread it as bearish.)
- **Inverse head & shoulders** — bullish reversal; buy just above neckline, stop at 2nd-shoulder low,
  target = head-to-neckline distance. Defined R:R but geometry is discretionary.

**B. Bearish / topping → exit & "don't-buy" filters only (long-only adaptation, never shorts):**
- **Head & shoulders**, **double top (M)**, **rounding top**, **descending triangle**, **rising wedge**
  — all short setups in the book. For us they are **exit signals on held positions + don't-buy filters**
  (topping/loss-of-strength), per the non-negotiable adaptation lens. Double-top already has fuller rules
  (§21.1) than this book; rounding top / rising wedge = weak-strength context, low weight.

### 24.2 Measured-move-by-pattern-height target → `execution/executor.py` (new `target_method` option)
The recurring, mechanical takeaway: **target = the height of the pattern**, projected from the breakout
point (triangle height, cup height, wedge height, H&S head-to-neckline). This is a concrete take-profit
model that complements the existing `target_method` options the backtester already compares (1:1 measured
move §2.1, swing-high §7, Fib extension §3.1/§9). → Add **`target_method = pattern_height`** as another
option for breakout-family rules, tuned/compared by the backtester. No claim it's better — just another
lever to sweep. ⚠️ Crypto caveat (§22.1): pattern-height targets must be validated against crypto's
larger ATR; a forex-sized target may be hit trivially or leave a lot on the table.

### 24.3 Data quality & liquidity → reinforces feed-health rail (§10.4) + allowlist liquidity
"**Garbage in, garbage out**" section, genuinely useful reinforcement:
- **Spike/erroneous-data heuristic:** a sudden order-of-magnitude jump (e.g. `11, 12, 13 → 100`) is
  almost certainly a **bad tick**, not a move; charting packages won't flag it. → concrete sanity check
  for the **stale-data / feed-health guard (§10.4)**: reject/clip a bar whose move is implausibly large
  vs recent ATR (a "data-spike guard"). Note: good feeds may correct spikes **hours later** → don't act
  on a single anomalous print.
- **Illiquid markets are unsuitable** for pattern/technical trading (gappy, spiky). → reinforces the
  **allowlist = liquid majors only** posture (major cryptos), consistent with §22.1/§10.

### 24.4 Reinforced (nothing new)
- **Candlestick anatomy** (body/wick, green-up/red-down, Homma/Dow) — identical to §23.7 / §1.3, no new info.
- **Timeframes** (1m oversensitive/false signals → 1h → **1d = author's main** → 1w/1m for long-term
  trend changes) — reinforces multi-TF bias (§3.2) and the false-signal/lag trade-off (§23.3, §1.4).
- **Continuation vs reversal** pattern taxonomy; **round numbers as S/R**; support=floor / resistance=
  ceiling — all already in §4.8 / §23.6 / §1.3.
- **"Chart patterns are a guide, not a guarantee"** / reduce emotion / help you exit losers — reinforces
  the edge-not-certainty and discipline themes (§5, §1.1); we express this as **backtested edge + gates**,
  never a standalone trigger.

### 24.5 Deferred to v2 (subjective geometry — same call as harmonics)
**Head & shoulders, cup & handle, rounding top/bottom, wedges** rely on **discretionary trendline/curve
fitting** ("draw a line along the highs/lows", "U-shaped base") that is hard to detect deterministically
and overfit-prone — exactly why we deferred Gartley/harmonics to v2 (README open-judgment #2). **Deferred:
low-weight, must be backtested before any inclusion.** The **exception is the ascending-triangle /
horizontal-resistance breakout (§24.1A)**, which reduces to a level-break we can already compute
deterministically → folds into the §23.1 breakout family rather than a bespoke detector.

### 24.6 ⛔ Halal / spot exclusions (same as §23.8)
Book's premise = leveraged CFDs + free shorting → **riba/leverage/short excluded**. All bearish patterns
are short setups in the book; we take only the long side / exit-filter side. No swaps/pairs content here.

### 24.7 Discarded (no agent value)
Deriv/MT5/DerivX/cTrader platform promos & links; finviz screener plug (we detect on our own allowlist,
not a screener); "use a demo account" CTA; author bio & socials; company/regulatory boilerplate; glossary
(all terms already in Source 4); "download the 10-pattern cheat-sheet" CTA; About-Deriv page.

---

### Net assessment (saturation-honest)
- **NEW (small):** `target_method = pattern_height` take-profit option (§24.2); a **data-spike guard**
  heuristic for the feed-health rail (§24.3).
- **REINFORCES (the useful part):** the **§23.1 breakout / buy-strength thesis** — ascending-triangle /
  base-breakout patterns point the same way as Donchian, strengthening the case to prototype a
  **breakout-family rule** as the replacement for the refuted dip-buyers.
- **REINFORCES (nothing new):** candlesticks, timeframes, round-number S/R, guide-not-guarantee discipline.
- **DEFERRED to v2:** H&S, cup & handle, rounding, wedges (subjective geometry — like harmonics).
- **EXCLUDED:** CFD/leverage/short premise; bearish patterns → exit/don't-buy only.
- **No change to the next action:** still "prototype the long-only breakout-family rule and validate via
  `keel simulate`." This book adds a second target-model lever (pattern height) and a data-spike guard to
  fold in when we build it. See [[halal-cb-autotrade-project]], [[halal-cb-transcript-workflow]].
