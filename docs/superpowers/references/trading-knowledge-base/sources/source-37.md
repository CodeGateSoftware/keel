[← Knowledge Base index](../README.md)

## Source 37 — "How to Trade Forex" (Vince Stanzione, Deriv ebook, 76pp)

> Sixth Stanzione / Deriv ebook (23/24/26/27/36/37). **Near-total duplicate** of the technical content
> already extracted from the crypto ([23](./source-23.md)) and commodities ([27](./source-27.md)) ebooks —
> same author, same publisher, same trio of tools, **even the same reused appendices** (the "Grain of Rice"
> compounding fable, the 5% money-management appendix — verbatim from §23.4/§25).
>
> **Saturation-honest: essentially nothing new.** The technical chapter is the identical **Moving-average
> crossover + Donchian channel (20-day rule) + RSI 70/30** already covered. Forex-specific material is
> out-of-scope background or excluded (digital options / multipliers / carry). One marginal item: trading
> **sessions / time-of-day liquidity** (§37.1). Logged mainly to record it was seen and deduplicated.

---

### 37.1 Trading sessions / time-of-day liquidity → marginal; maps to existing time-bucket analysis
The one forex-specific concept: *"the most active times are the **London–New York overlap**; 24-hour
liquidity via Sydney/Tokyo overnight."* → For **crypto this is weak** — Coinbase spot trades **24/7 with no
session structure** — but crypto does have **known intraday liquidity/volatility patterns** (US hours more
active). This loosely maps to our existing **pivot-slice time-bucket analysis** (§20.7: auto-prune weak
day/time buckets) and the seasonality-as-low-weight-factor stance (§14/§21). **Nothing to add** — the
time-of-day dimension is already handled by pivot-slice pruning; a fixed "session" model doesn't port to
24/7 crypto. Low weight, backtested only.

### 37.2 Duplicated (already fully covered — no re-extraction)
- **Moving-average crossover** (fast/slow MA cross = buy/sell) → §23 / source-02 (note: our EMA-fan treats
  a crossover as *invalidation*, not a signal).
- **Donchian channel — "20-day rule / 4-week rule", Turtle basis** → **§23.1 / §27.1** (the breakout-family
  spec: entry on N-bar high, exit on N-bar low; ATR sizing/stops). This ebook restates it verbatim; the
  canonical version is Source 27 (Turtle) — no refinement here.
- **RSI 70/30** (oversold-buy / overbought = exit-or-caution, long-only) → §23.3 / §25.3.
- **Money management** (5% fixed-fractional, drawdown-recovery table, no-averaging) → §23.4 / §25 / §26 —
  reused appendix, identical.
- **KISS / keep-it-simple / price-first / cut-losses-let-winners-run** → §26 / §25.

### 37.3 ⛔ Excluded / out of scope
- **Digital options** (fixed payout/barrier) → **excluded** (not spot, maisir/gharar, §27.4/§28).
- **Multipliers** (leveraged product) → **excluded** (leverage/riba, §4.9).
- **Carry / swap / rollover** (forex interest-rate-differential trades) → **excluded** (riba — the whole of
  Source 18; §23.8/§27.4).
- **Shorting** ("go long or short a pair") → **exit/don't-buy only**, never shorts.
- **Currency-pair mechanics** (base/quote, pip value, majors/minors/exotics, "who uses forex", BIS $6.6T
  stats, London/NY session detail) → **out-of-scope background**; not our market (we trade crypto/USD spot).
  (FX pairs themselves aren't traded; only the technical *methods* were ever the point, and those duplicate.)

### 37.4 Discarded (no agent value)
Deriv/MT5/Deriv-Trader platform promos & multiplier/digital-option walk-throughs; author bio; "Grain of
Rice" fable (dup of §23); glossary (dup of §4); affiliate pitch; regulatory boilerplate; About-Deriv.

---

### Net assessment (saturation-honest)
- **No new content.** A forex repackaging of the **MA/Donchian/RSI + 5%-money-mgmt** material already in
  Sources 23 & 27, with reused appendices. **Reinforces** the Donchian/Turtle breakout family (§27.1) and
  the standard RSI/MA/money-mgmt — nothing refined.
- **Marginal:** forex sessions / time-of-day liquidity (§37.1) → already covered by pivot-slice time-bucket
  pruning; doesn't port cleanly to 24/7 crypto.
- **EXCLUDED:** digital options, multipliers, carry/swap (riba), shorting; FX-pair background out of scope.
- **The Stanzione/Deriv ebook stream is exhausted** (23/24/26/27/36/37 — the last two added ~nothing).
  **Recommend stopping Stanzione/Deriv titles**; value is in **new-technique strategy books** (e.g. T3
  Source 34, Quantified-Edge Source 35) and the **Turtle-rule build**. See [[halal-cb-autotrade-project]],
  [[halal-cb-transcript-workflow]].
