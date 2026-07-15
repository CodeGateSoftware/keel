[← Knowledge Base index](../README.md)

## Source 17 — "Three Trend-Continuation Setups" (Greystone method)

> Two longs + one short. The valuable part: a **menu of 4 entry techniques for the same move**,
> graded by aggressiveness — which concretely operationalizes the CTS execution ladder (§8.1).

### 17.1 KEY — the 4 entry techniques (the concrete menu the CTS score selects among) → `strategy/engine.py`
For a trend-continuation pullback, four ways to enter the *same* move, ordered aggressive→patient:
1. **Aggressive market entry** — buy on the dip into the kill zone. Highest fill rate, lowest strike rate.
2. **Higher-high-higher-close confirmation** (long) / lower-low-lower-close (short) — wait for the next
   candle to close HH+HC **within the kill zone**. Higher strike rate, fewer fills.
3. **Three-bar reversal** — if the confirmation candle is a pin/doji, wait for the 3-bar reversal (§8.2).
4. **Last-chance entry** — a **double-bottom** (long) at the *deepest* pullback on a lower TF: tightest
   stop, **biggest R:R (3–5:1+)**, lowest strike rate (price is weakening-in-trend, so least probable).
Plus a pattern/Fib-inversion/ABCD entry as a 5th route (deferred, §9.3).
→ **This is the menu §8.1's CTS score picks from:** higher confluence score → can take the more
aggressive technique; lower score → demand more confirmation. Ties the two sources together.

### 17.2 NEW — the "kill zone" (entry-validity band) → `strategy/engine.py`
The **kill zone** is the price band where an entry still yields at least the minimum R:R (≥1:1,
ideally toward 2:1). It's bounded by the target (top) and the price where R:R drops below the floor
(bottom). **Entries are only valid inside the kill zone** — a concrete entry-validation gate. As CTS
confluence stacks, the kill zone is *narrowed* to the highest-probability sub-band (cf. §9.1).

### 17.3 NEW — ATR-based stop placement → `execution/executor.py`, `strategy/backtest.py`
Alternative to fixed-tick stops: place the stop **1 ATR below the low of the pullback/outside-return**
(1 ATR above the high for shorts), + front-running. Volatility-adaptive; feeds position sizing
(`quantity = risk_amount / stop_distance`, §1.5) and defines the kill zone's lower bound. Add
`stop_method ∈ {fixed_ticks, atr}` as a tunable rule parameter.

### 17.4 Market spends most time in consolidation → "fewer, better trades" (reinforces)
Forex consolidates ~70–80% of the time; trend-continuation trades are rarer but bigger. Principle
(crypto-applicable): most cycles will produce **no signal — that's normal**; "take fewer trades, be
right more, more profit" (cf. §5.4, §12.2). No new code; sets expectation for the poller's cadence.

### 17.5 Reinforced
Multi-TF top-down bias (daily→4H→1H→15m, §3.2); run→pullback→continuation (§1.2); CTS scoring (§8.1/§9.1);
higher-high-higher-close signal candle (§2.1 bullish mirror); RSI + double-bottom + divergence confluence
(§4.4); R:R floor & sizing 1%/trade (§4.5, §1.5).

### 17.6 Discarded (no agent value)
Greystone-series/tier-one CTAs, "long video / short attention span" aside.
