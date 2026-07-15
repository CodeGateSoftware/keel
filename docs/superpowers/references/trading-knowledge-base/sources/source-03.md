[← Knowledge Base index](../README.md)

## Source 3 — "TradingView Full Tutorial" (mostly platform UI)

> Thin source: ~80% is UI/aesthetics with no agent value. Only the transferable
> technique + one design confirmation are kept.

### 3.1 Fibonacci EXTENSION targets (new — extends §1.5 retracement) → `analysis/levels.py`
Beyond retracements (0.382 / 0.5 / 0.618 / 0.786 / 0.886), use **extensions to project
where a move runs to**: **1.272** and **1.618** are the "golden" target ratios (measured
swing-low → swing-high → back). Gives a **rule-derived take-profit** alternative to the
fixed 1:1 measured move — the backtester can compare `target = 1:1` vs `target = 1.272/1.618 ext`.

### 3.2 Multi-timeframe top-down analysis (new — affects engine architecture) → `strategy/engine.py`
Form **bias on a higher timeframe**, **trigger entries on a lower one**: Daily = macro
bias, 4H = trading timeframe, 1H/15m = entry refinement. Concretely our engine should:
(a) compute regime/trend on a higher-TF candle series as a **gating bias**, (b) only look
for the trigger (pin bar / EMA touch) on the lower TF, in the bias direction. Dovetails
with §2.3 (we already need multiple granularities in the `candles` table).

### 3.3 RSI as an extremes filter + simple mean-reversion rule → `analysis/indicators.py`
- Use RSI at **80 / 20** (stricter than the conventional 70/30) as overbought/oversold.
- Demoed rule: RSI > 80 → wait for candle close → short → stop 3 ticks above high → 1:1
  or 1.5:1 target. **Long-only translation:** the *oversold* side is the tradeable one —
  **RSI < 20 bounce** = candidate **buy**; RSI > 80 = **exit / don't-buy** filter for held assets.
- **MACD** also available (default 12/26/9) as an additional confluence indicator.

### 3.4 Harmonic (XABCD) patterns — candidate detectors, DEFERRED (overfit risk)
Gartley, Bat, Butterfly, Crab, Shark, and the **Cipher** pattern — swing-leg structures
defined by Fib ratios. Potentially powerful but **complex and overfit-prone**; the source
promotes them commercially. **Decision: log as future candidate rules, do NOT build in v1.**
If added later, they go through the same paper-trading proving gate as everything else.

### 3.5 Order-management semantics (new — real executor logic) → `execution/executor.py`
- **Bracket / OCO (one-cancels-other):** an entry's stop + target must be **linked** so
  that when one fills, the other **auto-cancels**. Without OCO, a stopped-out trade whose
  target later triggers can silently **re-enter you in the wrong direction**. Our executor
  must place stop+target as a linked bracket and cancel the sibling on fill.
- **Partial scale-out / multiple targets:** split a position (e.g. half at T1, half at T2)
  by summing sub-orders to the total size. **Safe ordering:** place entry + protective stop
  first; add the multiple target legs **only after the entry fills** (unlinked target legs
  placed early can themselves become new positions on a reversal).
- **Time-in-force:** GTD (good-till-date, auto-cancel at time), GTC (good-till-cancelled).
  Pairs with §2.2's one-candle order validity → use a GTD matching the next candle boundary.
- **Position sizing by % risk given stop price** — reconfirms §1.5 fixed-fractional sizing.

### 3.6 News/event blackout filter (new) → optional gate in `strategy/engine.py`
Economic-calendar events (FOMC, CPI, rate decisions) are plotted on charts to **avoid
trading around high-impact releases**. **Crypto translation:** no central-bank calendar per
se, but macro prints (FOMC/CPI) *do* move crypto, plus crypto-specific events (unlocks,
upgrades, listings). Optional filter: **skip entries within a window around scheduled
high-impact events**. Needs an events data source; defer wiring but note the hook.

### 3.7 DESIGN CONFIRMATION — no black-box strategies (validates our approach)
The source explicitly warns: trading platform-provided **prebuilt strategies / Pine-script
signals blind = no better than a signal service**; you must *develop, test, and own* a
strategy to have the conviction to trade it. This directly validates our decisions:
**curated + interpretable rules (not black-box mining)** and the **mandatory paper-trading
proving gate**. Keep as rationale in the spec.

### 3.8 Reinforced from earlier sources
- **Alerts → notify** (don't watch charts) = our poller's signal notification (cf. §2.7).
- **Screenshot each setup → journal** = store a chart/context snapshot ref per trade (cf. §2.5).

### 3.9 Discarded from Source 3 (no agent value)
All UI/customization (themes, colors, grid, watermark, watchlists), drawing-tool aesthetics
(brush/arrow/text/callout/ruler/zoom/magnet), split-screen layouts, DOM/"Dome" tape-reading
(explicitly "not for beginners"), stock screeners, Pine-script authoring, social/forum/
"ideas"/"the Leap" competition, mobile/desktop app install, pricing/Black-Friday, broker
affiliate links.


