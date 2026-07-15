[← Knowledge Base index](../README.md)

## Source 12 — "Should I Sell Bitcoin Now? — DCA vs Timing"

> **Most on-topic source for us:** it's specifically about **Bitcoin spot + DCA + long-only**
> — our exact context, no translation needed. Backs the DCA rule with real BTC crash/recovery
> data and a behavioral-finance case for why an automated system beats human timing.

### 12.1 DCA validated on real BTC data + the "DCA-through-drawdown" insight → `strategy/rules/`, design DCA rule
Backtest of the last BTC crash→recovery (monthly closes, $1,000/mo):
- **DCA starting *before* the crash** (endured ~2yr drawdown) ended at **$67,229**.
- **DCA starting at the *perfectly-timed* bottom** ended at **$38,916** — despite ~$12k less invested.
- → Getting in before the crash and **continuing to DCA through the drawdown ended ~$30k AHEAD**,
  because you **accumulate more units cheaply during the decline** and compounding starts earlier.
- **Key strategic insight:** our **DCA / dip-buy** rule *benefits* from continuing (or buying harder)
  through drawdowns — it should **not** be halted by fear. "You can actually do it because you can
  **automate it**" — directly validates our automated DCA rule as the low-speculation backbone.

### 12.2 Time-in-market > timing; systems beat willpower → design rationale
"Participation beats precision." Lump-sum + perfect timing wins *mathematically* but fails in
practice. "**Systems always beat willpower, especially under stress.**" This is the thesis of the
whole agent: turn investing from "make the perfect decision at the perfect moment" into "follow a
system regardless of emotion." Reinforces §5.4, §7.6, §8.5.

### 12.3 Behavioral-bias taxonomy → agent immune by construction + insights hook → `analysis/insights.py`
Five biases that make humans sell bottoms / miss recoveries: **loss aversion** (losses feel ~2×
gains — Kahneman/Tversky prospect theory), **recency bias** (extrapolate the recent past → "dead-cat
bounce" thinking), **regret aversion**, **herd behavior / social proof**, **action bias under stress**
(cortisol → freeze/overtrade). "Emotions lag price; markets recover before you feel safe."
- **For the agent:** a rule-gated, automated system is **structurally immune** to all five — the
  strongest possible validation of the mechanical design.
- **Insights hook:** in **confirm mode**, if the human repeatedly rejects rule-approved buys during
  drawdowns, the insights module (§6.1) can flag it as loss-aversion / herd behavior — turning the
  bias taxonomy into a concrete self-review signal.

### 12.4 Macro crash/recovery cycle analysis (reinforces deferred §8.4) → future `analysis/insights.py`
The presenter derives crash/recovery timing from **historical monthly-close cycle patterns** ("I'm
not predicting Bitcoin, I'm predicting people; markets produce patterns because human psychology is
constant"). This is a **concrete data method** for the §8.4 Wall-Street-cheat-sheet macro-cycle
(monthly closes → cycle comparison → regime). Still **deferred to v2** (subjective), but now with a
buildable proxy: e.g. drawdown-from-ATH regime / distance-from-long-term-MA to bias DCA intensity.

### 12.5 CAUTION — keep the DCA lesson, NOT the specific forecast → honesty / §6.4
The video makes a **specific price/date call** ("recovery ~October 6"). **We do NOT bake in
point forecasts** — that's exactly the "prediction oracle" we reject (§6.4). Extract the *durable*
lesson (DCA + behavioral discipline + patterns-not-prediction); discard the dated prediction.

### 12.6 DESIGN NUANCE — DCA vs the account-drawdown circuit breaker (reconcile §10.3) ⚠️
Tension: §10.3's **account-drawdown circuit breaker** halts new entries on deep drawdown, but §12.1
shows **DCA specifically profits from continuing through drawdowns**. Reconciliation for the spec:
- The **circuit breaker halts active *rule/strategy* trading** (CTS setups) — where a deep account
  drawdown signals something is wrong.
- **Scheduled DCA continues within its own fixed budget/cap** (it's not a discretionary signal; it's
  the accumulation backbone that *should* keep buying dips) — **unless the master kill-switch is
  thrown**. Make DCA a distinct order class with its own (small, capped) budget, exempt from the
  rule-trading DD breaker but still bounded by allowlist + per-asset cap + kill-switch.
This nuance matters: without it, the breaker would perversely stop the one strategy proven to
benefit from drawdowns.

### 12.7 Reinforced
DCA rule (design + §10.8), time-in-market > timing (§10.8), patterns-not-prediction / board-game
instructions (§1.1, §8.5), automation removes emotion (§4.10, §5.4), compounding on retained equity (§4.7).

### 12.8 Discarded from Source 12 (no agent value)
The specific dated price prediction (§12.5), Yahoo-Finance data-pull walkthrough, "share with someone
panicking" / subscribe CTAs, the earlier-video promo.
