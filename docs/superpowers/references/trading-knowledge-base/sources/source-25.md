[← Knowledge Base index](../README.md)

## Source 25 — "The Complete Guide to Trading" (Corporate Finance Institute, 2018, 116pp; author Colis Cheng)

> **Different author/publisher** from Sources 23–24 (a broad CFI educational textbook, not a broker promo).
> Three parts: **(1) The Markets** — asset classes, fixed income, money market, stocks, ETFs, commodity
> futures, forex; **(2) Trading Concepts** — random walk, fundamental vs technical, reading charts, value/
> growth investing; **(3) Technical Indicators & Trading Strategies** — the extractable core.
>
> **Saturation-honest:** Parts One & Two are mostly out-of-scope background (fixed income / money markets =
> bonds/interest = riba context; stock value/growth investing = N/A to crypto spot) or already covered
> (candles, timeframes, S/R, Fib, MACD). **But Part Three delivers one genuinely NEW, high-value tool —
> the ADX trend-strength indicator (§25.1)** — which is exactly the missing piece for the breakout /
> trend-following pivot ([[halal-cb-autotrade-project]]: dip-buying refuted, need buy-strength + a filter
> against false breakouts and ranging chop). It also contributes a strong principle — **"good trade vs
> winning trade" (§25.5)** — that validates our expectancy-based promotion gate and the per-class floor
> flagged in §23.1.

---

### 25.1 ⭐ ADX — Average Directional Movement Index (trend-strength filter) → `analysis/indicators.py` (NEW)
Wilder's ADX measures **trend STRENGTH, not direction** (a strong up- or down-trend both give high ADX).
Genuinely new to our indicator set (we have EMA/RSI/MACD/ATR/Fib/deceleration — no ADX). Mechanics:
- Built from **+DMI / −DMI** (directional movement: today's high/low vs yesterday's), smoothed into **DX**,
  then ADX = smoothed DX. **Default period 14** (range 7–30; lower = faster/more false signals, higher =
  smoother/laggier — same trade-off as §23.3/§1.4). Deterministic, **hand-rollable** (Wilder smoothing,
  no libs — consistent with our stdlib/Decimal stack). Scale 0–100.
- **Reading:** **ADX < 20–25 = ranging / no trend** (stand aside); **> 25 = genuine trend** (25–50
  moderate, 50–100 strong). **Slope** matters: up-sloping ADX = strengthening, down-sloping = weakening;
  a slope turn can lead the 25 cross (but early = more false signals).
- **Why this is the missing piece:** the book's exact words — ADX "helped many analysts avoid being lured
  into **buying false breakouts** or buying into markets that are basically just flat and going nowhere."
  That is **precisely** the failure mode we need to guard against when we add the Donchian/ascending-
  triangle breakout family (§23.1/§24.1): only take a breakout when **ADX confirms a real trend**. It also
  guards the dip-buy failure (§ project memo): ADX<25 flags the ranging/knife-catching regime to avoid.
- **Confluence use (canonical):** ADX is direction-blind → **combine with a direction/price tool**
  (MA slope, +DMI vs −DMI, or an S/R breakout). Ideal pattern from the book: *range breakout through
  resistance + rising ADX = confirmed breakout*. → Add ADX as a **new CTS confluence factor** and/or a
  **regime gate** (see §25.2). ⚠️ Long-only: the "strong downtrend also = high ADX" case is a
  **don't-buy / exit** signal for us, never a short.

### 25.2 The three simplest trend-following strategies → reinforce the §23.1 breakout pivot
The book's Part-Three strategy trio — all **buy-strength / trend-following**, the direction we're pivoting
toward. Each is a candidate rule (long side only) and/or a regime filter, all deterministic:

- **#3 The Simple ADX strategy (most useful):** ADX > 25 **and** price rising → be long / hold; ADX < 25
  → **exit and stand aside** until ADX ≥ 25 again. (Aggressive: 20 instead of 25.) → For us this is both
  a **candidate rule** *and* a clean **trend/no-trade regime gate** for `analysis/regime.py` (folds the
  §23.1 breakout entries under an "only when ADX confirms a trend" condition). Short leg (ADX>25 +
  declining → sell/hold short) → **exit/don't-buy** only.
- **#1 Golden Cross / Death Cross (50/200 MA):** long while **50-MA > 200-MA** (golden cross = bullish
  long-term regime; death cross = bearish). New *long-horizon* bias filter — our EMA fan is 8/20/50 (§2),
  shorter. → Candidate coarse **bull/bear regime bias** (`50>200` = long-eligible). Death cross = stand
  aside, never short.
- **#2 The 5/8/13 EMA strategy:** three EMAs; **5 crosses 13 = signal**; **fanning out = strong trend**;
  **closes staying above the 5-EMA = trend intact**; **convergence/flattening = range/consolidation
  (no-trade)**. → Mostly **reinforces our EMA-fan concept (§2)** with alternate (Fibonacci) periods to
  sweep, plus two useful regime cues (closes-vs-fastest-MA = trend-intact; MA-convergence = range).
- **Book's own caveat (matches us):** these "will **not** work well in range-bound or extremely volatile
  markets" → exactly why the **ADX/regime gate** (§25.1/§25.2) and our choppy=no-trade rule (§1.2) matter.

### 25.3 MACD — concrete defaults + trend-strength confirmation → reinforces existing MACD (§indicators)
We already have MACD; this pins the **standard params: 12-EMA − 26-EMA, 9-period signal line, histogram**,
and two uses: (a) **momentum/strength confirmation** — a MACD upturn confirms a price move is a *real
trending move vs a temporary correction* (the USD/SGD example) → useful as a **breakout-confirmation
confluence factor** alongside ADX; (b) **MACD divergence** (price new high, MACD turns down) = impending
trend-change / take-profit warning → reinforces our divergence handling (§1.4). Crossovers are whippy —
use as confirmation, not a standalone trigger (consistent with our confluence-only rule).

### 25.4 Candlestick reinforcements → `analysis/candles.py` (mostly already have)
- **Doji family:** long-legged (indecision/possible reversal at trend end), **dragonfly** (long lower
  tail, closes at high — bullish rejection after a downtrend → **buy context** for us), **gravestone**
  (mirror → **topping / exit-don't-buy**), four-price (rare). Reinforces §1.3 doji primitive; dragonfly/
  gravestone are the tradeable-mirror / exit pair.
- **Pin bar (hammer) reversal:** long tail ≥ 2–3× body, tiny opposite wick; **stronger at a prior S/R
  level** and when **price is overextended from the 10/21 EMA** and preceded by several same-direction
  candles (overbought/oversold). Reinforces our pin-bar-at-level confluence (§2.1/§16.1). Management
  detail worth noting: stop just beyond the pin tail, first target = touch of the MA, then trail
  stop-to-breakeven. ⚠️ **Presented as a 5-minute SCALPING strategy → the scalping framing is EXCLUDED**
  by our anti-scalping / min-move rail (§4.1); we keep only the pin-bar-at-level reversal logic on our
  normal timeframes.
- **Reference:** book cites **Bulkowski's pattern site** for *statistical reliability* of each candle
  pattern ("know if that indication has proven accurate 90% of the time"). Reinforces our core rule:
  **patterns earn inclusion only via backtested edge**, never on faith — a good external stats reference.

### 25.5 ⭐ "Good trade vs winning trade" + trade psychology → validates promotion gate + rails + no-oracle
Part-Three psychology chapters are non-mechanical but **strongly validate our design principles** — worth
recording because one directly resolves an open design question:
- **"A good trade vs a trade that wins":** a trade is *good* if it had **favorable R:R + odds in favor**,
  regardless of whether it won or lost; a lucky win on a bad-R:R setup is a *bad trade*. → **Directly
  validates evaluating rules by expectancy / R:R over a sample, not by individual win/loss** — i.e. our
  promotion gate — **and reinforces the §23.1 per-rule-class floor**: a trend-follower with a *low win%
  but high R:R and positive expectancy* is making "good trades" and should pass, so the global 55%-win
  floor is the wrong bar for that class.
- **"Trade management > market analysis":** outcomes are driven more by how you manage a trade than by
  entry → reinforces our executor/rails/trailing focus (§17/§19).
- **"The market can't be predicted":** winning traders accept unpredictability and watch for being wrong,
  rather than trusting a forecast → **direct alignment with our NO-prediction-oracle principle (§6.4)**.
- **Self-discipline / "I made the rule so I can break it":** the hardest discipline is following your own
  rules → **our un-overridable rails + automated agent enforce this by construction** (removes the human
  override temptation; §4.10/§5). Confirm-mode bias-detection (§6.1) is the human-in-loop analog.
- **Patience / "when in doubt sit it out" / don't trade from boredom / don't add to losers / cut losers
  fast** → reinforce confluence gate, min-move rail, **no-averaging-into-losers (§5.1)**, exit discipline.
- **Trading journal (Bonus skill #7):** record entry + reason + stop + target + outcome + your reaction →
  reinforces our journaling design ask (journal fields in DB / `agent_state`, §2.5/§4.6).

### 25.6 ⛔ Out of scope / excluded / N/A
- **TRIN / ARMS index (§ Part-Three chapter): NOT APPLICABLE — discard.** It's a **stock-market breadth**
  oscillator = `(advances/declines) / (adv-volume/decl-volume)` across an *index basket* (S&P/NASDAQ).
  A single crypto/USD pair has **no advance-decline breadth** → no crypto analog. (A hypothetical
  crypto-market-breadth version across many coins is far-future, not backtestable now — noted, not built.)
- **Part One asset classes:** fixed income + money market = **interest-bearing = riba, excluded**; stocks/
  ETFs/commodity-futures/forex = background, out of scope for crypto spot (market literacy already in §4).
- **Value / growth investing (Part Two):** equity fundamental strategies, N/A to a technical crypto-spot
  agent (value's "buy undervalued" only loosely echoes DCA discipline, §12).
- **All short setups** (death cross, descending triangle, gravestone, ADX+downtrend) → **exit / don't-buy
  filters**, never shorts (adaptation lens).

### 25.7 Discarded (no agent value)
CFI course/certification promos & "check out our online courses" CTAs; company boilerplate; Random Walk
Theory philosophy (only reinforces "markets not random", §8); generic "how to read a stock chart" basics;
inspirational masters' quotes (Livermore/Templeton/etc. — motivation, no mechanics); pivot-point
arithmetic (published-daily convenience, we compute our own levels §4.8).

---

### Net assessment (saturation-honest)
- **GENUINELY NEW & high-value:** the **ADX trend-strength indicator** (§25.1) — the missing filter to
  confirm real trends / reject false breakouts & ranging chop, exactly what the breakout pivot needs.
  New `analysis/indicators.py` component + new CTS confluence factor + new `regime.py` trend/no-trade gate.
- **NEW (secondary):** 50/200 golden-cross long-horizon bias filter; the **"good trade vs winning trade"**
  principle that validates expectancy-based promotion + the per-class floor for trend-followers (§23.1).
- **REINFORCES:** the §23.1 buy-strength pivot (all three trend-following strategies point that way);
  MACD defaults (12/26/9) & divergence; doji/pin-bar-at-level; journaling; rails; no-prediction-oracle.
- **EXCLUDED / N/A:** TRIN (no crypto breadth), riba asset classes, value/growth equity investing,
  scalping framing, all shorts.
- **Next action (sharpened):** when prototyping the long-only breakout-family rule, **gate it on ADX>25 +
  uptrend** (and optionally MACD-up confirmation) to reject false breakouts, and validate via
  `keel simulate`. ADX is now the top new-indicator to build. Also implement the **per-rule-class
  promotion floor** (low-win/high-R:R for trend-followers), now doubly-motivated (§23.1 + §25.5).
  See [[halal-cb-autotrade-project]], [[halal-cb-transcript-workflow]].
