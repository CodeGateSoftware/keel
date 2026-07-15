[← Knowledge Base index](../README.md)

## Source 16 — "£4,500 GBP/JPY Trade" (live confluence short)

> A live setup demoed as a **short** (double-top at resistance). Long-only translation: the
> **double-bottom-at-support mirror = a BUY entry** for us; the short version = exit/don't-buy filter.
> Mostly reinforces; two concrete additions (double-top/bottom V entry, Bat pattern ratios).

### 16.1 NEW entry primitive — V-top / double-top (and its mirror, double-bottom) → `analysis/candles.py` / engine
After price rallies into resistance **already overbought** (RSI ≥ 80 extreme, §3.3) with
**deceleration** (§1.4): wait for a **second test that tests the high of the initial test but does
NOT close above it** → enter next-bar market. That's the V-top/double-top. **Our tradeable mirror =
double-bottom at support** (second test holds the prior low, doesn't close below) → **buy**. Precise,
backtestable. Stop above the double-top high (below the double-bottom low for us).

### 16.2 Deferred harmonic — Bat pattern ratios (extends §9.4/§15) → future `strategy/rules/`
- **B = 0.50 retracement of X–A** (Bat is shallower than Gartley's 0.618).
- **C = 0.382 retracement of A–B.**
- **D completion = 0.886 of X–A** (the deep completion).
- **Stop = 1.13 inversion beyond X; target = 0.382 of A–D** (roll with price). Demoed R:R > 2:1.
- **Still deferred** with Gartley (§15.4) — harmonic family, v2, long-mirror only, same paper gate.

### 16.3 Reinforced
Partial exits / position splitting (take 70% at target, trail 30% — §3.5); RSI-80 extreme filter
(§3.3); deceleration (§1.4); look-left structure / role-reversal S/R (§1.3); multi-TF drill-down
daily→4H→1H→15m (§3.2); "not greedy, a 2:1 all day" (R:R floor, §4.5); order-type roles
(sell-limit target / buy-stop stop, §1.5).

### 16.4 Discarded (no agent value)
Live P&L flexing, "juiciest setup of the week", tier-one/cheat-sheet CTAs.
