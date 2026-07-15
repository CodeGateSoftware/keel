[← Knowledge Base index](../README.md)

## Source 19 — "Trailing Stops" (trade management)

> Detailed, concrete **trailing-stop algorithm** for the "manage" phase. Direction-agnostic mechanics;
> for us it manages **long runners**. Enriches §3.5 with a buildable procedure. Consistent with the
> **no-stop-widening rail** (§5.1) — trailing only moves the stop *toward* profit, never away.

### 19.1 The trailing-stop algorithm → `execution/executor.py`
1. **(Optional) partial target + break-even.** Take a partial (e.g. half/70%) at the first target,
   then **roll the stop to break-even** on the remainder → the runner is **risk-free** (can't give back
   more than already banked). (Reinforces partial exits, §3.5/§16.3.)
2. **Trail on a LOWER timeframe.** Drop to a finer TF and trail the stop **1 ATR below each new
   structure low** (the latest outside-return low) — updated **only when a new structure high is
   confirmed** (a close above the prior high). Locks profit on smaller structure the higher TF misses.
3. **Patience gate.** Do **not** move the stop until the new structure high actually closes — no
   anticipating. (Binary discipline, §2.2; and it only ratchets toward profit, §5.1.)
4. **Full exit at major structure.** When price reaches a major higher-TF structure level (low
   probability of pushing through), **close the remaining position** rather than trailing into it.

### 19.2 The trade-off (a tunable parameter)
More stop breathing room → catches longer moves but locks less; tighter trail → locks more but exits
early. Expose as `trail_atr_mult` / `trail_tf`, tuned by the backtester per rule. Trailing is for
**trend-continuation** runners, not mean-reversion scalps.

### 19.3 Reconciliation with the rails
Trailing is fully compatible with **no-stop-widening (§5.1)**: the stop is monotonic toward profit.
The **break-even-after-partial** step makes the runner incapable of a net loss — strengthens the risk
posture. Nothing here overrides a hard rail.

### 19.4 Reinforced
Partial exits / OCO bracket (§3.5), ATR-based stops (§17.3), structure highs/lows & new-high
confirmation (§1.2/§2.2), multi-TF (§3.2), "must backtest this" (§2.3), let-profits-run (§1.5).

### 19.5 Discarded (no agent value)
Bar-replay UI walkthrough, 30-day/tier-one CTAs.
