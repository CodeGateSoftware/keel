[← Knowledge Base index](../README.md)

## Source 8 — "Markets Are Not Random / Confluence Scoring"

> Familiar confluence content, but introduces the most important *design refinement* so far:
> confluence as a **graded score that drives dynamic execution**, not a binary threshold.

### 8.1 KEY REFINEMENT — confluence SCORING → graded execution → upgrade `strategy/engine.py`
Assign **points per confluence factor** (major S/R, psychological/round number, RSI divergence,
deceleration/price-action pattern, EMA-zone, candlestick, touch-count…). The **total score**
then selects *how* to act, on a tier ladder:

| Score | Entry mode | Sizing / management posture |
|---|---|---|
| Low (≈4–5) | **conservative** — require a **3-bar-reversal confirmation** before entry | smaller size, wider stop breathing room |
| Mid (≈6–7) | wait for the **single signal candle** (buy-stop above its high) | base size |
| High (≈8–10) | **aggressive** — enter at market / limit on level touch | larger size (toward cap), tighter stop, more aggressive target, possible 2nd position / trailing |

This turns our engine from "fire above a fixed threshold" into a **score → {entry timing, size,
stop width, target method}** mapping. It stays fully deterministic (fixed tiers, no discretion).

**⚠️ HARD-RAIL BOUND (critical):** "more aggressive" is **capped by the rails**. A high score may
scale size *up toward* `max_per_order_usd` / the 1% base / the exposure & correlation caps —
**never beyond them.** Dynamic aggressiveness operates strictly *inside* the §4.1 + base rails;
the rails are the ceiling, the score only moves you within it. This preserves the user's
non-negotiable safety model while allowing graded response.

### 8.2 Three-bar reversal — precise definition (as the low-score confirmation entry) → `analysis/candles.py`
Refines §1.4's mention: **(1)** rejection/signal candle, **(2)** an indecision/doji candle,
**(3)** a higher-high-higher-close candle → **enter at market on that 3rd close** (bearish mirror:
lower-low-lower-close). This is the extra-confirmation entry used for **lower-score** setups (§8.1).

### 8.3 Terminology reconciliation — "lotus candle" = rejection candle
The source's "lotus candle" (price pushed one way, rejected, closed back near the open) is just a
**rejection / low-test (or high-test) candle** — already captured (§1.3/§7.2). Log as a synonym;
no new detector. Also confirms the **looser "upper/lower third" rejection definition** as a tunable
variant alongside Source 2's stricter 30% (cf. §2.1 reconciliation).

### 8.4 Wall Street Cheat Sheet / market-psychology cycle — candidate macro-context, DEFERRED
The "Wall Street Cheat Sheet" (accumulation→markup→euphoria→distribution→markdown→despair) is
claimed to overlay Bitcoin/IPO/ICO cycles. Potentially relevant **macro-cycle context for crypto**,
but **subjective and hard to operationalize deterministically** (phase labels are eyeballed).
**Decision: note as a possible future macro-context signal; do NOT build in v1.** If pursued,
approximate via quantifiable proxies (e.g. distance from long-term MA / drawdown-from-ATH regime).

### 8.5 REINFORCED — edge decay & the "board-game instructions" thesis
- "As their edge deteriorates over time… they're not keeping up… they lose money" — a **third**
  source (with §6.3, §5.2) confirming **edge decay** → the demotion path is essential, not optional.
- "Board-game set of instructions" + "probability, not prediction; we never know what the market
  will do" — the exact framing from the user's original ask and §1.1. Now stated across multiple
  sources; firmly a core principle.

### 8.6 Reinforced (already captured)
4 market conditions, runs/pullbacks, horizontal/angular/round-number S/R, resistance↔support swap,
EMA fan/zone, MACD/RSI convergence-divergence (§4.4), deceleration, candle-close validation (§2.2),
multiple target methods (EMA zone / structure / trail; §7.4), buy-stop/stop-loss placement.

### 8.7 Discarded from Source 8 (no agent value)
Webinar upsell, tier-one/30-day pitch, "type X in the comments" CTAs, reticular-activating-system
motivational aside.


