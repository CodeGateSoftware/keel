[← Knowledge Base index](../README.md)

## Source 1 — "Trading for Beginners" course

### 1.1 Core thesis
A strategy = "a series of patterns that happen frequently, built into rules that give a
high probability of being right, traded consistently like a board-game set of
instructions." Operationalized as **confluence stacking** → a weighted scoring engine;
an entry fires only above a confluence threshold. → `strategy/engine.py`

### 1.2 The 6-component edge (confluence checklist)
| # | Component | Deterministic version | Module |
|---|---|---|---|
| 1 | Market condition | bullish/bearish/ranging/choppy from swing structure; **choppy ⇒ no trades** | `analysis/regime.py` |
| 2 | Market phase | `run` (impulse) vs `pullback`; entries only in pullbacks, never mid-run | `analysis/regime.py` |
| 3 | Support/Resistance | swing pivots; touch-count = strength; role reversal (R→S); angular trendlines; round-number "even handles" | `analysis/levels.py` |
| 4 | Deceleration | N consecutive candles with shrinking body/range (ATR-normalized) in trend direction | `analysis/indicators.py` |
| 5 | Indicators | Fibonacci retracement (38.2% strong, 61.8% deep) off last swing; EMAs; ATR | `analysis/indicators.py` |
| 6 | Candlestick pattern | pattern detectors, **counted only at a level** | `analysis/candles.py` |

### 1.3 Candlestick primitives → `analysis/candles.py`
- body = `close−open`; upper wick = `high−max(o,c)`; lower wick = `min(o,c)−low`.
- **High-test / shooting star** (bearish): upper wick ≥ k×body AND close in lower third.
- **Low-test / hammer** (bullish): lower wick ≥ k×body AND close in upper third.
- **Doji** (indecision): `|close−open|` ≤ m×(high−low).
- **Marubozu** (strong momentum/continuation): both wicks ≤ small fraction of body.
- **Three-bar reversal**: rejection wick → next candle takes prior extreme & closes beyond it.
- `k`, `m` = params the backtester tunes.

### 1.4 Seed rule #1 — trend-continuation pullback entry
**Bullish (tradeable, long):**
- Setup: condition=bullish, phase=pullback, price in prior support/role-reversal zone.
- Signal candle: first candle closing **above the previous candle's high**.
- Entry: buy-stop 2 ticks above signal candle high.
- Stop: 2 ticks below pullback's lowest low.
- Target: highest close of prior run (−1 tick, front-running).
- Log confluence: S/R touches, angular, Fib level, deceleration, candlestick, round number.

**Bearish mirror (course's original, NOT a short for us):** used as (a) exit/stop for held
assets, (b) "don't buy" filter. Signal = candle closing below previous candle's low.

### 1.5 Risk & order mechanics → `execution/`
- Order taxonomy: buy/sell **limit** (patient, beyond price) vs buy/sell **stop**
  (breakout, beyond price) vs **market** (now). "Orders for later, market for now."
- "Every order is an order" — entry/stop/target each get equal precision.
- **Front-running**: place orders a tick or two beyond the level to ensure fill.
- **Position sizing (fixed-fractional):**
  ```
  risk_amount = equity × risk_pct            # course default 1%
  quantity    = risk_amount / |entry − stop|
  spend       = quantity × entry             # then clamp to max_per_order/day caps
  ```
- Let profits run, cut losses early. Lower reward:risk ⇒ needs higher win rate
  → promotion guard (e.g. reject 1:1 R:R with <55% win rate).
- ❌ Excluded (halal/spot): leverage, margin, margin-calls, lots.

### 1.6 Positive Expectancy → promotion gate (`strategy/promotion.py`)
```
E = (1 + W/L) × P − 1
```
W=avg win, L=avg loss magnitude, P=win rate. `E>0` ⇒ edge; higher better.
Property: `E×L = P·W − (1−P)·L` (standard expectancy), so **E is expectancy in
R-multiples**. Use as candidate→paper→live threshold.

### 1.7 Backtest schema → `backtests` table (design ask #2)
Columns: `entry_date, entry_time, pair, timeframe, system, entry_type, condition,
phase, sr_score, indicator_notes, deceleration, candlestick, entry_price, stop_price,
target1/2/3, close_date, close_time, exit_price, pnl`.
Required metrics: win rate, avg win, avg loss, expectancy, longest losing streak,
max drawdown, biggest win/loss.

### 1.8 Timeframe/cadence heuristic → config
Observe once/day → Daily; 2–4×/day → 4H; >4×/day → 1H. For our scheduled poller,
`poll_interval_sec` matches candle `granularity`. Test **one asset fully** before adding more.

### 1.9 Discarded (no agent value)
Mindset/accountability, broker choice, demo-account walkthrough, sales pitch.


