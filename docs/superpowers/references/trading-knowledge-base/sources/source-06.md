[← Knowledge Base index](../README.md)

## Source 6 — "How AI Is Changing Trading" (meta / philosophy)

> No strategies or indicators — but it's *about* what our agent is, so it's useful meta-guidance:
> two concrete features, strong validation of our honesty stance, and one real design tension.

### 6.1 NEW feature — AI-assisted behavioral / performance insights → `analysis/insights.py`
The strongest concrete idea: feed the **journal + trade history (our DB)** to an AI layer that
**surfaces patterns the trader can't see** — e.g. "you overtrade on Fridays," "risk creeps up
after losses," "best trades in a specific session," "worst trades when the routine is skipped."
This fits our stack perfectly (the agent is already AI-driven). Build an **insights report**
over `transactions`/`orders`/`pnl_daily` that flags: edge decay vs backtest, best/worst
sessions & weekdays, post-loss behavior, and rule-level performance drift. Extends the §2.4
quarterly pivot-review from manual pivot tables into an automated analysis.

### 6.2 NEW analysis — seasonality / historical-context grounding → `analysis/insights.py`
"Ground decisions in probability, historical context, and **seasonality**" (deviation from
historical averages, time-of-day/day/month behavior) to remove emotional bias. → add
**seasonality dimensions** to the analysis (performance & price behavior by session / weekday /
month). Reinforces the slicing dimensions our `pnl_daily`/`orders` tables already need (§4.6).

### 6.3 IMPORTANT caution — simple mechanical edges decay → honest limitations + demotion
Direct quote of substance: **AI/automation "will remove a lot of simplistic trading edges over
time… obvious mechanical systems, simple indicators, basic arbitrage"** as markets get more
efficient. **Our curated rules (EMA/RSI/pin-bar) ARE exactly these simple mechanical edges.**
→ (a) State plainly in the spec that a rule that backtests/papers well **can and will decay**;
(b) this is *why* we need continuous re-validation + a **demotion path** (a live rule whose
rolling stats fall below the promotion floor is auto-demoted to paper/disabled), not just a
one-time promotion. Strengthens §5.2 lifecycle and the design's "no profit guarantee."

### 6.4 DESIGN VALIDATION — "AI = decision support, not a prediction oracle"
Core thesis: AI is **not** a crystal ball; it won't remove market uncertainty. Its real value
is **research, filtering, pattern-spotting, journaling analysis, friction removal** — "less
prediction, more decision support." → Validates our stance that the agent's intelligence lives
in **deterministic tested rules + backtest stats + analysis**, NOT in an LLM "predicting" price.
Reinforces the design's §F "what I will NOT promise." **Guard rail implication:** never let an
LLM's free-form market opinion place a trade — trades come only from the deterministic rule
engine + rails. (An LLM may *explain/summarize/flag*, never *decide the entry*.)

### 6.5 DESIGN TENSION — human judgment vs full-auto (bypass) mode ⚠️
The source argues human judgment (context, emotion control, one-off events) "is very difficult
to automate" and the winners "combine technology + structure + **human judgment**." This is in
mild tension with the **bypass / full-auto** mode you requested. **Synthesis for the spec:**
- **Confirm mode = the recommended default** — AI-as-assistant + human judgment in the loop
  (exactly the "combine" sweet spot this source endorses).
- **Bypass mode is legitimate but strictly bounded** — its autonomy is safe *only because* the
  9 hard rails + the event-blackout filter (§3.6) + the min-sample/decay demotion (§5.2/§6.3)
  substitute structurally for the missing human judgment. Worth stating explicitly so bypass
  isn't mistaken for "trust the AI's judgment" — it's "trust the tested rules within hard limits."

### 6.6 Reinforced
Core principles never change: **risk management, discipline, patience, probabilities, human
behavior** = our rails + expectancy focus + boring-consistency (§5.4). "Remove emotional bias,
ground in probability" = what a rule-gated agent does by construction.

### 6.7 Discarded from Source 6 (no agent value)
Pit-trader nostalgia, forecaster.biz promo, "adapt or die" motivation, tier-one pitch.


