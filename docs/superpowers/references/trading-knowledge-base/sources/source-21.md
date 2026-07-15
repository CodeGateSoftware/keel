[← Knowledge Base index](../README.md)

## Source 21 — "High- vs Low-Probability Setup" (AUD/CAD structure short)

> Mostly reinforces. Two useful items: the **complete double-top/bottom rules** (refines §16.1) and
> **seasonality as a confluence factor** (generalizes §14). Demoed as a **short** → our tradeable
> mirror is the **double-bottom at support** (buy). Plus a good discipline lesson (a setup he *avoids*).

### 21.1 Complete double-top rules (refines/supersedes §16.1) → `analysis/candles.py` + `strategy/engine.py`
Full rule set (bearish; **long mirror = double-bottom at support = buy for us**):
1. **In-zone** — price is at an HTF-identified resistance (support for us) level (§7.3 ≥3 touches).
2. **Initial test with RSI extreme** — first test prints **RSI ≥ 80** (≤20 oversold for the long mirror).
3. **Shallow pullback** — the retrace must **NOT** be deep: it must **stay above the 0.382 Fib
   retracement** and not push back into prior structure. (New precise depth rule vs §16.1.)
4. **Second test holds** — tests the high of the first test but does **NOT close above it**.
5. **RSI divergence on the 2nd test** — equal price highs but **lower RSI highs** (§4.4).
6. **Entry** — on a candle **closing below the low of the second-test high** → next-bar market.
7. **Stop** — ATR above the double-top high (§17.3), or just above major structure.
8. **Target** — structure-based (don't fight into prior structure; §7.4).

### 21.2 Seasonality as a CTS confluence factor (generalizes §14) → `analysis/insights.py`, low-weight
Monthly **seasonality** + "best-correlated-year" used to add probability to a setup and to tune R:R /
size / entry-aggressiveness. Generalizes the BTC-specific §14: **seasonality is one low-weight CTS
factor** for any instrument. **Same cautions as §14.4** — non-stationary, tiny samples, must be
backtested, never a standalone trigger. (The "Forecaster" tool is third-party; we'd compute our own
seasonality from the `candles` table — no external dependency.)

### 21.3 Discipline — reject R:R degraded by volatility (the AVOIDED setup) → `strategy/engine.py`
He skips a look-alike GBP/AUD setup for two concrete reasons: (a) **huge ATR** → the ATR-based stop
sits far from entry → **R:R collapses toward 1:1** (below his floor), and (b) **RSI not at the extreme**
(a required filter absent). → Explicit engine gate: **if the ATR-widened stop pushes R:R below the
floor (§4.5), or any required CTS filter is missing, skip the setup.** Reinforces min-move (§4.1),
kill-zone (§17.2), no-"close-enough" (§2.2). "Taking low-quality setups → few wins, few losses →
break even; the edge is the discipline to take only the high-probability ones."

### 21.4 Reinforced
Multi-TF (daily bias → 4H/1H trade → lower-TF precision, §3.2); RSI 80/20 extreme + divergence (§4.4);
ATR stops (§17.3); structure-based targets & "don't fight into structure" (§7.4); patience/fewer-better
-trades (§5.4, §17.4); positive expectancy / tested-repeated-verified (§20.1); "end in hindsight" =
realistic-expectations honesty (§2.3, no cherry-picking winners); role-reversal S/R (§1.3).

### 21.5 Discarded (no agent value)
Forecaster tool promo/walkthrough, "let me know in the comments" CTAs.
