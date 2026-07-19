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

#### 17.1a Refinement — how to actually DRAW the entry zones (from a later re-paste of this method)

§17.1 names the techniques but not the geometry. A worked GBP/JPY walkthrough by the same author
supplies the missing construction, which is the implementable half:

- **Shallow ("first chance") zone = highest high → highest close** of the structure being pulled back
  into. Entry is a **retest of that band on a lower TF**, stop just below the retest.
- **Deep ("last chance") zone = lowest low → lowest close** of the most recent pullback. Same
  retest-on-lower-TF treatment; this is §17.1 #4's double-bottom.
- The **middle zone** is entered on the HH+HC candle (§17.1 #2) **at market**, because by then price
  is deeper in and the extra confirmation buys back the aggressiveness.
- ⭐ **The zones are not fixed — they are shifted so each one still clears the R:R floor.** In the
  walkthrough the author explicitly moves a zone boundary down *"because we want a minimum of a one
  reward-to-risk profile."* That makes the kill zone (§17.2) the **constraint that positions the
  zones**, not merely a validity check applied afterwards.
- Probability and R:R run **inversely** across the three, for a stated structural reason: a pullback
  deep enough to reach the last-chance zone means *"the whole trend is running out of steam, which
  means we could see another reversal"* — i.e. the best R:R is available exactly where trend-continuation
  is least likely. Same conclusion §17.1 already records; the walkthrough supplies the *why*.

#### 17.1b Refinement — RSI extreme as a "don't chase" ENTRY-TIMING veto (not a reversal signal)

Distinct from RSI-as-confluence (§4.4): with a **bullish** bias established top-down, RSI **> 80** on the
trading timeframe is used to **veto entering now** and force waiting for the pullback — *"it doesn't make
sense to just buy this up now… you're going to have a lot of pain when price starts dipping."* The signal
is not "reverse", it is "**do not chase; the entry you want has not arrived**".
→ Maps cleanly onto our existing `indicators.is_overbought(thr=80.0)` (already the same threshold) as an
**entry-timing gate** in `strategy/engine.py`, complementing the kill-zone check rather than duplicating
it: the kill zone says *where* an entry is valid, this says *when* not to take an otherwise-valid one.

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
