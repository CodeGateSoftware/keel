[← Knowledge Base index](../README.md)

## Source 27 — "How to Trade Commodities" (Vince Stanzione, ebook, 50pp)

> Fourth Vince Stanzione / Deriv **ebook**. Commodities aren't our market, but the author (as always)
> stresses the techniques port across markets, and one section is the payoff. Same CFD/leverage/short/
> options premise (halal-excluded).
>
> **Saturation-honest:** the majority is **duplication/reinforcement** of Sources 23–26 — it literally
> **re-embeds the Source-24 10-chart-pattern guide** (page 33), and repeats MA-crossover, RSI 30/70,
> MACD 12/26/9, the three market states, S/R, round numbers, trailing stops, drawdown-recovery, and 5%
> sizing. **The one high-value payoff is §27.1: the Turtle Trading system** — the named, battle-tested,
> published formalization of *exactly* the breakout family we've been assembling (Donchian entry + ATR
> volatility sizing + ATR stops), plus a concrete **asymmetric 20-in / 10-out channel** refinement over
> §23.1's symmetric 20/20. It is the canonical spec + confidence anchor for the breakout pivot
> ([[halal-cb-autotrade-project]]).

---

### 27.1 ⭐ Turtle Trading — the canonical Donchian breakout system (Dennis/Eckhardt, 1980s) → the breakout-family spec
The book gives the full, named **Turtle** system. **Nothing here is a brand-new mechanic** — the KB
already has each piece (Donchian entry §23.1, ATR stops §17.3, risk-sizing §1.5, trailing §19.1/§23.2) —
but Turtle **composes them into one proven, decades-validated trend-follower**, which is (a) a strong
confidence anchor that our buy-strength pivot is a real, documented edge, not an invention, and (b) a
precise spec to build. Components (long-only adaptation in brackets):
- **Entry — breakout method:** buy when price **exceeds the highest high of the past 20 days**; [the "sell
  when price falls below the 20-day low" leg is, for us, an **exit**, never a short].
- **⭐ Exit — asymmetric channel (NEW vs §23.1):** exit a long when price **falls below the lowest low of
  the past 10 days**. The **10-day exit channel is SHORTER than the 20-day entry channel** — locks profit
  faster than a symmetric 20/20 Donchian while the longer entry channel still filters entries. → makes
  `entry_lookback` and `exit_lookback` **independent params** (Turtle System-1 = 20/10; System-2 = 55/20)
  to sweep in the harness. Plus an **ATR trailing stop** as the alternative exit (§27.2).
- **⭐ Position sizing — ATR ("N") volatility-based:** *the more volatile the asset, the smaller the
  position* (size inversely proportional to ATR). This is the KB's `quantity = risk_amount / stop_distance`
  (§1.5, live in `keel/execution/sizing.py`) with **stop_distance set from ATR** → **crypto-essential**
  (§22.1 says volatility-adaptive sizing is mandatory for crypto's 10–20%/hr swings).
- **⭐ Stops — ATR-multiple:** stop at a fixed **ATR multiple below entry** (Turtles used ~2N). This is
  §17.3, now with the concrete multiple to test — and it **directly attacks the milestone-6 defect**
  ("stops too tight for crypto ATR, blown through at ~-2R"): ATR-scaled stops give volatility-appropriate
  room by construction.
- **Risk — fixed-fractional 1–2%/trade:** matches our §1.5 / 1%-cap posture exactly.
- **Stated edges (verbatim, same as §23.1):** winners left to run · exact rule-based exit (no guessing) ·
  rule-based · risk always defined · "even with more losers than winners you can make money if winners
  gain more than losers" → reinforces the **low-win/high-R:R per-class promotion floor** (§23.1/§25.5).
- **Timeframe adaptations:** 20-hour-high / 10-hour-low, or 20-min / 10-min for shorter systems (same knob
  as §23.1). Author explicitly: "Donchian channels are the basis for the Turtle Traders' entry."

→ **This is the concrete spec for the rule to prototype:** long-only Turtle — **enter 20-day-high breakout
+ ADX>25 confirmation (§25.1); size by ATR; stop at ~2×ATR; exit on 10-day-low OR ATR trailing stop; 1%
risk; low-win/high-R:R promotion floor.** Validate via `keel simulate`. It unifies Sources 22/23/25.

### 27.2 Trailing-stop & breakout-scan reinforcements → `execution/executor.py`, `analysis/insights.py`
- **ATR trailing stop** (Turtle exit) + the **10%/channel trailing stop** (silver example: stop ratchets
  up with price, holds flat when price stalls, stops out on reversal) → reinforce §19.1/§23.2 trailing.
- **Breakout watchlist idea:** scan for **"commodities making new 20-day highs"** to find breakout
  candidates (cocoa's +150% 2024 breakout cited). → For us this is exactly the **Donchian entry scan** run
  across the allowlist each cycle (engine already evaluates the allowlist; frame the new-20-day-high scan
  as the breakout trigger). Reinforces the buy-strength thesis with a live example.

### 27.3 Reinforced (already covered — no new signal)
- **Chart patterns (page 33): literal re-embed of the Source-24 guide** (H&S, double top/bottom, cup &
  handle, rounding, triangles, wedges) → nothing new; see §24 (subjective ones deferred to v2, ascending
  triangle folds into breakout family).
- **MA-crossover 6/21** (buy when short crosses above long) → §23 / source-02. Note again: our EMA-fan
  (§2) treats a crossover as *invalidation*, not a signal — crossovers are whippy; keep as context.
- **RSI 30/70** (oversold buy / overbought = "monitor long more carefully or reduce," reframed long-only)
  → §23.3. **MACD 12/26/9** crossover + histogram + "try other params" → §25.3.
- **Three market states, S/R, round numbers, breakouts, volume (high vol confirms a move), multi-TF
  (author = daily, 6–12mo horizon), seasonality (Seasonax, WTI/nat-gas seasonal patterns)** → §23.6, §4.8,
  §14 (seasonality stays low-weight/deferred, backtested only). **Order types** (market/limit/stop/**Buy
  Stop for breakout entries**/stop-limit/TP/SL, GTC) → §1/§4 (Buy-Stop = the mechanical breakout entry).
- **Risk:** drawdown-recovery table, keep losses ≤5%, "my profit comes from catching a trend," KISS /
  "paralysis by analysis" (keep indicators few) → §23.5/§25.5/§26; **price-first** ("price is the key
  factor, not news; 50→55→60 = uptrend regardless of opinion") = no-prediction-oracle (§6.4).

### 27.4 ⛔ Halal / spot exclusions (heavy in this book)
- **CFDs + leverage (up to 1:500!)** → **riba/leverage, excluded** (§4.9); cash-only spot for us.
- **Swap / daily financing / rollover charges** (explicitly "a daily interest charge") → **riba, excluded**
  (mirrors §18, §23.8).
- **Shorting** (extensive: "long oil then flip short") → **exit/don't-buy only**, never shorts.
- **Pairs trades** (long gold / short copper; long platinum / short silver) & **hedging a physical holding
  with a CFD** → **hedging, excluded** (§4.9). Our diversification = multiple **uncorrelated LONG spot**
  positions (correlation-sizing rail §4.1).
- **Digital options** (large section — Rise/Fall, Higher/Lower, Touch/No-Touch, Ends-Between, Accumulator
  Options) → **excluded**: not spot, and fixed-odds binary/barrier options carry **maysir/gharar**
  (gambling/excessive-uncertainty) concerns — clearly outside a halal spot mandate.
- **Commodity futures/lots** (leverage), **commodity stocks/ETFs** (GDX, DBC, uranium trust) → out of
  scope (equities/futures). Commodities themselves aren't our market — only the technical methods port.

### 27.5 Discarded (no agent value)
Commodities history (Mesopotamia/Edo rice tickets/CBOT/CME); asset-group tours (energies/metals/livestock/
ag); gold price-driver fundamentals (inflation/supply/demand/geopolitics — commodity-specific, not crypto);
hedger-vs-speculator explainer; Deriv/MT5 platform & account promos; leverage math worked examples; digital-
option payoff walk-throughs; resource plugs (Barchart/ShareScope/Seasonax/Finviz/Futures.tradingcharts —
commodity/stock tools, we use Coinbase data); glossary (all in Source 4); affiliate pitch; About-Deriv.

---

### Net assessment (saturation-honest)
- **HIGH-VALUE (composition, not brand-new parts):** the **Turtle Trading** formalization (§27.1) — names
  and validates our breakout pivot as a proven, published system, and pins the concrete spec: **20-day-high
  entry / 10-day-low exit (asymmetric — NEW vs §23.1) + ATR volatility sizing + ~2×ATR stop + 1% risk +
  ATR trailing.** Directly targets the "stops too tight for crypto" defect via ATR-scaled stops.
- **REINFORCES:** buy-strength/trend-following pivot (with a real breakout example), ATR sizing/stops,
  trailing, low-win/high-R:R floor, price-first/no-oracle, KISS-keep-indicators-few.
- **DUPLICATES:** the Source-24 chart-pattern guide (re-embedded); MA/RSI/MACD/states/S&R.
- **EXCLUDED:** leverage/CFDs, swap-financing (riba), shorting, pairs/hedging, **digital options
  (maysir/gharar)**, futures/lots, commodity stocks/ETFs.
- **Next action (now fully specified):** prototype the **long-only Turtle-style breakout rule** — Donchian
  20/10 asymmetric channel + ADX>25 confirmation (§25.1) + ATR sizing & ~2×ATR stop + ATR/10-day-low exit
  + low-win/high-R:R promotion floor — and validate via `keel simulate` on the cached 5yr data. **Stanzione
  ebooks (23/24/26/27) have saturated**; this one earned its keep solely on the Turtle section. See
  [[halal-cb-autotrade-project]], [[halal-cb-transcript-workflow]].
