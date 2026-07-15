[← Knowledge Base index](../README.md)

## Source 4 — "Trading Terminology Explained" (glossary)

> Much reinforces Sources 1–3. Captured below: only the **new** concepts, especially the
> ones that upgrade our **risk rails** (the user made rails non-negotiable).

### 4.1 NEW risk rails → `execution/guards.py` (extend the hard rails in the design)
- **Total open-exposure cap.** Per-trade risk isn't enough: 5 open trades × 1% = **5%
  aggregate exposure**. Add a hard rail capping the **sum of at-risk capital across all
  open positions** (in addition to the existing per-order / per-day $ caps). Un-overridable
  even in bypass mode.
- **Correlation-adjusted sizing.** Correlated instruments = hidden concentration (long
  EURUSD + NZDUSD ≈ short USD twice). **Crypto is extreme here** — most alts co-move with
  BTC, so buying BTC+ETH+… simultaneously is one big "long crypto beta" bet. Rail: when
  opening correlated positions, **split/scale down risk** (e.g. 0.5% each instead of 1%,
  or weight toward the higher-probability one) so correlated exposure doesn't stack. Needs a
  rolling correlation estimate across allowlist assets.
- **Minimum-move guard (anti-scalping).** A target must clear **spread + round-trip fees by
  a margin** or the trade is fee-dominated and rejected. Source is blunt: scalping 1–2 pip
  moves with a 2-pip spread "costs more to get in and out than it will ever profit."
  → reject any rule/target whose expected move ≤ k × (fees+spread).

### 4.2 Bid/ask/spread & slippage → realistic backtest + fill logging → `strategy/backtest.py`, DB
- **You buy at ask, sell at bid**; the spread is an **instant round-trip cost** — you're
  down the spread the moment you enter. Model it explicitly in the backtester.
- **Slippage**: fill differs from intended price in fast/illiquid conditions. Backtester
  applies a slippage assumption; the live executor **logs intended vs actual fill** (store
  slippage per order in the DB) so we can measure real execution quality.

### 4.3 Liquidity & volume filter (new) → `strategy/engine.py`
Illiquid markets move against large orders. For our allowlist, BTC/ETH are deep but PAXG is
thinner. Filter: **skip or shrink orders that are large relative to recent volume / book
depth**; prefer liquid pairs. (Coinbase order book / 24h volume as the input.)

### 4.4 RSI convergence / divergence (new momentum signal) → `analysis/indicators.py`
- **Convergence:** price and RSI move together → trend continuation likely.
- **Divergence:** price makes a higher high but RSI makes a lower high (or inverse) →
  momentum fading → likely reversal/pullback. A concrete, implementable **exhaustion
  detector** — complements §1.4 "deceleration." Feeds confluence + the "don't-buy/exit" side.

### 4.5 Concrete promotion thresholds (reconciles §1.6 / §2.1) → `strategy/promotion.py`
Source gives explicit numbers: aim for **reward:risk ≈ 1.5–2:1 with win rate ≥ ~55%**
(or lower win rate only if R:R is much larger). Use as the **default promotion gate**
alongside positive expectancy `E>0` (§1.6): a candidate must clear expectancy **and** these
floors before paper→live. (Low-R:R + low-win-rate rules lose to spread/fees — rejected.)

### 4.6 Drawdown expectations & time-in-drawdown (new metric) → metrics + `pnl`
"**Most of the time is spent in drawdown**" (the gaps between new equity highs). Track not
just **max drawdown** but **time-in-drawdown** and **max losing streak**, and surface them
so a normal losing run isn't mistaken for a broken system (mental-stability tooling from §2.4).

### 4.7 Compounding → size on current equity
Reinvesting profits compounds returns; fixed-fractional sizing on **current** equity (§1.5)
already does this. Note: our design keeps a cash buffer / speculation-cap, so compounding is
bounded by the funded portfolio, not unlimited.

### 4.8 Gaps as structure (minor for crypto) → `analysis/levels.py`
A gap (open far from prior close) marks a significant level → usable as S/R. **Crypto trades
24/7**, so continuous-pair gaps are rare (mostly low-liquidity spikes); low priority, but
gap/spike levels can seed S/R.

### 4.9 EXPLICIT EXCLUSIONS (halal / spot / sanity) — flag, don't implement
- **Carry trades / positive-negative carry** = borrowing a low-rate currency to earn a
  high-rate one = **interest arbitrage = RIBA. Hard-excluded.** (Also not a spot mechanic.)
- **Hedging** (simultaneous long+short offset) — requires shorting; **not possible on
  long-only spot** and pointless for us. Excluded.
- **Scalping** — spread/fee-dominated (see §4.1 guard). Not a style we trade.
- **ECN vs market-maker broker** distinction — N/A; Coinbase is an exchange.
- **Short selling / unlimited-downside** discussion — excluded (long-only spot, no borrowing).

### 4.10 Psychology terms → mostly auto-handled by a mechanical agent; feed self-review
FOMO, overtrading, confirmation bias, impatience — a rule-gated agent is structurally immune
to most (entries only fire on rules; the daily-trade cap curbs overtrading). Where a **human**
acts in confirm-mode, these map to the §2.4 self-review journal scores. No new code beyond the
existing caps + journal.

### 4.11 Reinforced (already captured; no new entries)
Candlesticks/OHLC, support/resistance & structure, swing highs/lows, runs↔pullbacks /
extensions↔retracements, breakouts, order types & stop/target roles, OCO/GTC/trailing-stop
(cf. §3.5), moving averages, ATR/volatility, win rate, expectancy (§1.6), backtesting &
journaling (§2.3–2.5), 1% position sizing (§1.5), long/short/bullish/bearish, pip↔tick.


