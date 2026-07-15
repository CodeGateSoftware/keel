[← Knowledge Base index](../README.md)

## Source 9 — "High-Probability Fib Confluence / CTS"

### 9.1 RECONCILIATION — "CTS" = **Confluence Trading Score** (names §8.1, fills §2.5 gap)
This source names the scoring system: **CTS = Confluence Trading Score**. This resolves the
unexplained `CTS` column in the §2.5 journal schema *and* confirms §8.1's score→execution ladder
from a **second** source. Example point scheme: minor structure at completion = 1, **pattern
completion = 3**, Fib confluence = 1, a secondary setup (Fib-inversion) = 1, RSI overbought = 1,
lower-TF double-top = 1 → total score selects entry aggressiveness / size / stop / target.
→ Adopt **`CTS`** as the canonical name for the engine's confluence score; keep the same
**rail-bounded** aggressiveness rule from §8.1.

### 9.2 NEW concept — "magnet" levels → bias + target selection → `analysis/levels.py`
Well-tested **higher-timeframe** levels act like **magnets**: price tends to gravitate toward
them, and the closer price is, the higher the probability it reaches the level. Implementable:
tag strong multi-touch HTF levels as **magnet targets**; use proximity as a directional-bias
input and as a **probabilistic target** for trade management. Complements the §7.3 touch-count
strength and §3.2 multi-TF bias.

### 9.3 Candidate rule — Fibonacci Inversion strategy (fully specified) → deferred, `strategy/rules/`
On a "complex pullback": measure from the pullback low; **entry = limit at the 1.618 extension**;
**target = 0.618 retracement** (of low→1.618); **stop sized to make it 2:1**. (Attributed to Jason
Stapleton.) Fully mechanical *except* the subjective "complex pullback" identification. **Decision:
candidate rule — defer to post-v1**; if built, quantify "complex pullback" and force it through the
paper gate. Long-only note: demoed as a short → for us the **long mirror** is the entry; short = exit/filter.

### 9.4 Deferred harmonics — concrete ratios now recorded (extends §3.4) → future `strategy/rules/`
- **ABCD / equal measured move:** AB leg ≈ CD leg (equal measured move) → completion zone. Simplest
  harmonic; most mechanical.
- **Gartley (full ratios):** impulse leg → X→A; **B = 0.618 retrace of X–A** (must NOT hit 0.786);
  **C = 0.618 retrace of A–B** (must not violate A); **D = 1.272 extension** = completion/entry;
  **target = 0.382**. Sell-limit (short) as shown → long mirror for us.
- Still **deferred** (overfit risk, §3.4), but ratios are now captured so v2 doesn't re-derive them.

### 9.5 Minor — Fib 50% retracement level
Add **0.500** to the Fib retracement set (§1.5/§3.1: 0.382 / 0.500 / 0.618 / 0.786 / 0.886) as a
common confluence level; the demoed setup stacked 50% retrace + equal-measured-move + 1.618.

### 9.6 Reinforced
Multi-TF top-down (Daily level → 4H entry, §3.2), S/R strength via multiple HTF touches (§7.3),
resistance↔support role reversal, confluence stacking, Fib extension targets (§3.1),
CTS score→dynamic (rail-bounded) execution (§8.1).

### 9.7 Discarded from Source 9 (no agent value)
Workshop/Tier-One upsell, "this is a hindsight trade" caveats, CTAs.


