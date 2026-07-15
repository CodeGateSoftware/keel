[← Knowledge Base index](../README.md)

## Source 14 — "What's Happening to Bitcoin? — Cycle & Seasonality"

> **On-topic (BTC spot, long-only).** Operationalizes the deferred macro-cycle idea (§8.4/§12.4)
> with **concrete, quantifiable** Bitcoin cycle + seasonality metrics. Same hard caution as §12.5:
> keep the *statistical tendencies*, discard the *specific price/date forecast*.

### 14.1 Concrete BTC cycle metrics (upgrades §8.4/§12.4 from "subjective" → "buildable, low-weight v2")
Measured from the weekly chart across the last ~3 cycles (halving-aligned):
- **Bull leg (cycle low → cycle high): ≈ 1,046 / 1,064 / 1,050 days** (~2.9 yr) — remarkably consistent.
- **Bear leg (cycle high → cycle low): ≈ 364 / 378 days** (~1 yr).
- **Full cycle ≈ ~1,415 days ≈ ~3.9 yr** (consistent with the BTC halving cycle).
- **Peak-to-trough drawdown regime: historically ~78% / 84% / 86%.**

### 14.2 BTC monthly seasonality (candidate low-weight signal)
- **Feb/Mar: "relief-rally trap"** — a ~65–90% bounce that fools people into thinking recovery has
  started, then resumes declining.
- **May: weak** — 3 of the last 5 Mays had double-digit % declines.
- **July: strongest, most consistently positive month** (e.g. +8.2% / +21% / +16.8%).
- **Oct/Nov: cyclical bottom** (≈365 days after the peak).

### 14.3 How this could feed the agent (v2, low-weight, must be validated) → `analysis/insights.py`, `analysis/regime.py`
Usable **only as macro-context that biases LONG activity** (halal-fine — all buying):
- **Bias DCA / dip-buy *intensity*** — accumulate more near cyclical-bottom windows / deep
  drawdown-from-ATH, lighter near cyclical tops. (Never sell on it — long-only.)
- **Regime input:** cycle-day-count since last halving/low, **drawdown-from-ATH bucket**, month-of-year.
- Feeds the **seasonality analysis** (§6.2) and could gate rule aggressiveness as one CTS factor.

### 14.4 HARD CAUTIONS (why this stays deferred + low-weight)
- **No point forecasts.** The video's "$36–40k bottom, ~Oct 6 2026" is exactly the prediction-oracle
  we reject (§6.4/§12.5). Extract tendencies, never a hardcoded target price/date.
- **Non-stationary & tiny sample.** ~3 cycles is almost no data; crypto seasonality is weak and may
  not persist. **Must be backtested, kept low-weight, and never a standalone trigger** — it's context,
  not a rule. If it doesn't survive out-of-sample, it's dropped.

### 14.5 Reinforced
Patterns-not-prediction / "predicting people not Bitcoin" (§1.1, §8.5, §12.2), don't trade emotion
(§4.10, §12.3), drawdown is normal / most-time-in-drawdown (§4.6, §10.5).

### 14.6 Discarded (no agent value)
The specific price/date prediction (§14.4), Epstein/conspiracy asides, "mark my words / come back in
2026" framing, tier-one/30-day CTAs.
