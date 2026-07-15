[← Knowledge Base index](../README.md)

## Source 2 — "Trading for Beginners — Part 2"

### 2.0 The strategy framework: Identify → Predict → Decide → Execute
Every entry passes through 4 stages, each a gate. This is the canonical evaluation
order for `strategy/engine.py`:
1. **Identify** — trend condition + phase-two pullback.
2. **Predict** — EMA fan confirms trend likely to continue.
3. **Decide** — a qualifying pin bar at the 8 EMA gives the trigger.
4. **Execute** — place entry/stop/target orders.

### 2.1 Seed rule #2 — "Daily Chore" (EMA-fan pullback) — FLAGSHIP, fully mechanical
This supersedes seed rule #1 as the primary buildable rule (it's the same
pullback-continuation idea but with fully-quantified filters).

**Indicators:** EMA(8), EMA(20), EMA(50) — exponential (faster-reacting than SMA; all
indicators lag, so prefer EMA). Three used together for confluence.

**Bullish (tradeable long for us):**
- **Identify:** bullish trend, in a phase-two pullback (down).
- **Predict — EMA fan:** `EMA8 > EMA20 > EMA50` (8 nearest price). Wider fan = stronger
  trend. **Any crossover ⇒ INVALID** (reversal/consolidation risk) — no setups.
- **Decide — pin bar:** a candle whose **open AND close are both within the upper 30%
  of the candle's high–low range**, and which **touches or is below the EMA8**.
- **Execute:**
  - Entry = buy-stop 2 ticks **above** the signal candle's high.
  - Stop = sell-stop 2 ticks **below** the signal candle's low.
  - Target = buy-limit at a **1:1 measured move** (stop distance projected up). Initial/
    conservative; can extend to 2:1, 3:1, or scale-out multi-targets later.

**Bearish mirror (NOT a short for us):** `EMA8 < EMA20 < EMA50`; pin bar open+close in
**lower 30%**, touching or above EMA8. → wire to **exit rule for held assets** and
**"don't buy" filter**, never a short entry (long-only spot / no borrowing).

**Extra confluence filter:** role-reversal S/R (prior support→resistance / resistance→support).

**Reconciliation with Source 1:** this 30%-body "pin bar" is a *stricter, quantified*
version of §1.3's high-test/low-test candle — Source 1 required only the **close** in the
outer third; Source 2 requires **both open and close** in the outer 30%. Use the
Source-2 (stricter) definition as the default; keep the looser one as a tunable variant.

### 2.2 Order lifecycle rules → `execution/executor.py`
- **Validate on candle CLOSE only.** A signal isn't real until the candle closes (daily
  close = 5pm America/New_York). For our poller: evaluate a candle only once closed.
- **One-candle order validity.** The pending entry order is good for the **immediately
  following candle only**. If it doesn't trigger that candle, **cancel it** — then
  re-evaluate, since the new candle may itself be a fresh signal.
- **Binary, no "close enough."** EMA touch and the 30% test are strict yes/no. Consistency
  demands binary gates ("can't be half pregnant").
- **Round-number breathing room.** If an even-handle level sits just beyond the stop, widen
  the stop a tick or two (accept a slightly worse R:R for fewer stop-hunts).

### 2.3 Backtest accuracy: intrabar order-of-events → `strategy/backtest.py`
When a single candle's range spans **both** the entry and the stop, you cannot tell from
that candle whether entry or stop hit first (or whether price breached the stop *before*
triggering entry, which **invalidates** the trade entirely). **Resolution: drop to a
finer granularity** (e.g. 1h/15m candles for that timestamp) and replay the sequence.
Our backtester MUST do this whenever entry and stop fall inside one bar — otherwise
results are optimistically wrong. Key mantra: **"backtesting exists to LOWER expectations,
not raise them."**

### 2.4 Journaling — two independent tracks → `journal` + `pnl` + a review report
1. **System-performance track:** compare live results vs backtest to detect **edge decay
   or improvement**; is a losing streak within historically-normal bounds?
2. **Self-review track (live only):** emotional state 0–10 (**only enter when
   desperation/fear ≤ ~5–6**), errors made + their $ impact, `rules_followed` (yes/no/%),
   excess losses (> planned risk), missed entries, trades that shouldn't have been taken,
   trade-management notes, screenshots, "action taken to improve." **Goal: zero errors/month;
   execute the plan flawlessly.**
- **Quarterly pivot-table review:** slice P&L by pair / timeframe / month / day / news event
  to find where money leaks — e.g. one unprofitable pair bleeding via "death by a thousand
  cuts," a bad time-of-day, or a too-aggressive position-size ramp after win streaks.
  → our `pnl_daily` + `orders` tables must carry enough dimensions to support this slicing.
- Principle: **"what gets measured gets mastered."**

### 2.5 Journal schema additions (beyond §1.7 backtest columns)
`errors_made, error_pnl_impact, rules_followed(y/n/%), emotion_pre(0-10),
emotion_during, emotion_exit, atr, chart_notes, action_to_improve, screenshot_ref`.

### 2.6 Fees erode returns → reinforce the fee gate + fee tracking (design ask #2)
Four leaks: **silly donations** (trading without a proven system), **rollover/carry
costs**, **spread fluctuations**, **tax**. A **1% recurring fee ≈ years of extra work**
(compounding example: 8% vs 7% net over decades = millions / +7 years to FI). → Model
fees realistically in the backtester and **track actual fees per trade in the DB**;
reconcile against the halal-cb live fee gate.
- **Halal/spot notes:** rollover/carry and spread-betting/tax mechanics are Forex-specific.
  **Carry/rollover = interest ⇒ riba ⇒ already excluded** (we hold spot, no overnight
  financing). Keep only the general lesson: *fees compound — measure and minimize them.*

### 2.7 Signal alerts → agent notification
Course uses platform price-cross alerts to avoid screen-watching. Analog for us: the
scheduled poller detects the signal and **notifies** (the confirm-mode prompt / audit log),
so no human needs to watch charts.

### 2.8 Portfolio-risk philosophy (context, not an agent tool)
"Earn your right to risk"; **never risk >10% of net worth in speculation**. Informs an
optional top-level *speculation-capital cap* (fund the trading portfolio with only a small,
losable slice) — matches the design's "scope the API key to a dedicated portfolio."

### 2.9 Discarded from Source 2 (no agent value)
TradingView/trade-nation platform walkthroughs & affiliate links, IQ-test / wealth-test /
30-day-program pitch, tax/accountant specifics, wealth-pyramid motivational framing.


