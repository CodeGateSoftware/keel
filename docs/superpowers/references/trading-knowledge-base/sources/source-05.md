[← Knowledge Base index](../README.md)

## Source 5 — "Biggest Mistakes Traders Make" (10 yr coaching)

> Short/philosophical, but each "mistake" maps to a concrete guard or validates a decision.

### 5.1 NEW hard rails → `execution/guards.py`
- **No averaging into losers (no martingale).** Mistake #3: traders add to losing positions
  to avoid being "wrong." Rail: the agent **never increases a position that is underwater
  against its stop**; losers are closed at the stop, full stop. Un-overridable in any mode.
- **No stop-loss widening.** Same mistake: moving the stop away to avoid the loss. Rail: a
  stop may only move **toward** profit (trailing, §3.5) — **never further from entry**. The
  original risk is the max risk.
- These join the rails from §4.1 (exposure cap, correlation sizing, min-move) and the design's
  four base rails (allowlist, caps, sell-only-on-rule, kill-switch/audit).

### 5.2 Rule lifecycle: minimum sample + streak tolerance (new params) → `strategy/promotion.py`
- Mistake #2 (strategy-hopping after 5 losses): **5 trades tells you nothing.** Require a
  **minimum sample (≈100–200 trades, backtest+paper combined)** before a rule is judged —
  both for **promotion** *and* for **retirement**. Don't kill a rule on a short losing run
  that is **within its historically-tested max losing streak** (ties to §4.6 drawdown data).
- Practical effect: rule status changes are **data-gated, not reaction-gated** — mirrors the
  mechanical, un-panicked agent we're building.

### 5.3 "Judge behavior, not outcome" → rule/agent evaluation logic
Mistake #3/#4: "a good trade = one where you followed your plan," and one trade's outcome
means little; 200 trades mean a lot. → Evaluate rules and the agent on **process adherence +
long-run expectancy over the sample**, not single-trade P&L. Concretely: the journal's
`rules_followed` (§2.5) and rolling expectancy (§1.6) are the scorecards — not the last trade.

### 5.4 DESIGN VALIDATIONS (no new code — rationale for the spec)
Five of the seven "mistakes" are things our architecture prevents **by construction**:
| Mistake | Our design already prevents it via |
|---|---|
| #1 Trading real money too early | **Mandatory paper-trading proving gate** |
| #4 Not knowing win rate / R:R / streak / expectancy | Per-rule **backtest+paper stats**, surfaced before live |
| #5 Risking too much (10% → needs 70% to recover) | **1% fixed-fractional** + exposure cap (§4.1) |
| #6 Consuming > applying | Agent **only acts on tested rules**; this KB *is* the applied distillation |
| #7 Chasing excitement | Scheduled poller + fixed rules = the "boring, underwhelming" disciplined trader — **the whole point** |

This source is the clearest statement of *why* the agent is built the way it is: **"stop
fighting the process… build a strategy you understand, test it, manage risk, repeat."** That
sentence is essentially our system's spec in plain English.

### 5.5 Discarded from Source 5 (no agent value)
Motivational framing, school-conditioning analogy, tier-one/30-day-challenge pitch.


