[← Knowledge Base index](../README.md)

## Source 7 — "End-to-End Trend Trading Strategy"

> Same 6-principle confluence skeleton as Sources 1–2, re-specified with **different
> parameters**. Key insight: Sources 2 and 7 are **two parameterizations of ONE rule family**.

### 7.1 KEY SYNTHESIS — unify into a parameterized "pullback-continuation" rule family
Seed rule #2 (§2.1, "Daily Chore") and this strategy are the *same setup* with different knobs.
Rather than store them as separate rules, model **one parameterized rule** the backtester tunes:

| Knob | Source 2 ("Daily Chore") | Source 7 | → Parameter |
|---|---|---|---|
| EMA set | 8 / 20 / 50 | 20 / 50 / 200 | `ema_periods` |
| Trend/fan confirm | 8>20>50 fan | bullish structure + 200 EMA context | `trend_filter` |
| Entry zone | touch of EMA8 | **dip into the 20–50 EMA band** | `entry_zone` |
| Front-run buffer | 2 ticks | **3–5 ticks** | `buffer_ticks` |
| Target | 1:1 measured move | **previous swing high** | `target_method` |
| Trigger candle | pin bar (30% body) | low-test / doji / tweezer | `signal_patterns` |

This is precisely the "curated, data-tuned library" from the design: the *structure* is fixed
and interpretable; the *parameters* are optimized on our data and forward-proven via the paper gate.

### 7.2 NEW candlestick primitive — tweezer tops / bottoms → `analysis/candles.py`
- **Tweezer bottom (bullish):** two candles with **equal lows** and open/close in the **top 50%**
  of the body → support rejection.
- **Tweezer top (bearish):** two candles with **equal highs** → resistance rejection.
- **Reliability ranking** (for confluence weighting): **low-test/hammer (highest, most
  aggressive) > tweezer > doji (lower strike rate)**. Encode as per-pattern confidence weights.

### 7.3 NEW threshold — S/R strength ≥ 3 touches (higher timeframe) → `analysis/levels.py`
A level is "strong/valid" only with a **minimum of 3 tests on a higher timeframe** (example
used 6). Quantifies the §1.3 touch-count score → default `min_sr_touches = 3`, higher = stronger
confluence. Pairs with the multi-timeframe bias idea (§3.2).

### 7.4 NEW target method — previous swing high/low → `execution/executor.py`, backtest
Target = the **last swing high** (bullish) / swing low (bearish), not a fixed 1:1. Gives a
**third `target_method`** for the backtester to compare against 1:1 measured move (§2.1) and
Fib 1.272/1.618 extension (§3.1). Example R:R ranged ~1.5:1 to ~3:1 depending on structure
distance — consistent with the §4.5 promotion floor (R:R ≥ 1.5–2).

### 7.5 EMA(200) as macro trend context → multi-timeframe bias
The 200 EMA is the long-term trend filter (only take longs above it, etc.), reinforcing the
§3.2 higher-TF bias gate. Add to `trend_filter`.

### 7.6 DESIGN VALIDATION — "cockpit checklist" + "bake your own cake"
- **Checklist framing:** trade a fixed checklist to remove in-the-moment emotion — this is
  literally our deterministic rule engine (evaluate the same gates every cycle).
- **Cake analogy:** "buy an algorithm online = lowest quality"; the world-class path is
  *learn the ingredients, then build & refine your own*. Independently re-validates §3.7/§5.4
  (own-your-rules, no black-box) — now stated by **four** separate sources.

### 7.7 Reinforced
6-principle confluence order (condition→phase→S/R→deceleration→indicators→candle) = §1.2 /
§2.0; resistance↔support role reversal; angular + horizontal S/R + round numbers; deceleration
(§1.4); MACD convergence/divergence (§4.4).

### 7.8 Discarded from Source 7 (no agent value)
Tier-one/30-day-challenge pitch, "let me know in comments" CTAs.


