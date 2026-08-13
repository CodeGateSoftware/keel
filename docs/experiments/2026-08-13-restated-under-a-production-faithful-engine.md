# Two engine defects, both invisible to 2,712 passing tests — and what the conclusions become without them

**Date:** 2026-08-13
**Amends:** `2026-08-12-shipped-defaults-intersection.md` (#252) and
`2026-08-12-rsi-meanrev-scale-vs-selectivity.md` (#255)
**Engine changes it restates them under:** #256 (`fix/pending-setup-never-expires`) and #258
(`fix/next-bar-open-fills`)
**Ledger:** two rows, session `restated-production-faithful-engine-2026-08-13`.

**This document annotates; it does not rewrite.** #252 and #255 keep their numbers exactly as
printed, per the convention #247 set: *"Past numbers in `docs/experiments/` were real outputs of
the code as it stood; they are annotated by this change, not restated."* Each remains a true record
of what its engine produced. What follows is what those same experiments produce on an engine that
matches production.

**Verdict: every conclusion in #252 and #255 survives except one, and losing it makes the finding
simpler and harder. Under production-faithful execution the viable quadrant is empty at every
reachable fee — 0 of 90 in #252's matrix and 0 of 82 in #255's — with no survivor requiring three
probes to eliminate.**

---

## 1. What was wrong with the engine

Two defects, found within hours of each other, pushing in **opposite directions**.

**#256 — a pending setup never expired.** `backtest()` held an unfilled `Setup` indefinitely; the
only escape without a fill was the stop being touched first. A setup whose entry and stop were both
never revisited pinned `pending` forever, so the flat branch never ran again and `rule.detect()`
was never called again. **The engine switched its own detector off.** Measured: `rsi_meanrev` on
UNI-USD at `oversold=35` stopped detecting in November 2021 and sat dead for ~40,000 bars — 9
trades against 309 at the *stricter* `oversold=30`.

**#258 — entries filled at a price production never waits for.** The simulator held the setup until
a later bar's range *touched* `entry`, then filled *at that level*. Live places market orders
(`order_type="market"`, `limit_price=None`, the setup's price kept only as `expected_fill`), so it
never rests an order and never waits. The old model granted two things production does not have:
free optionality on the entry price (unfavourable entries were silently declined, because a setup
only became a trade if the market offered the chosen level) and unbounded patience.

**The directions matter, and this is the single most useful sentence in this document:**

> #256 suppressed **opportunity**. #258 flattered **execution**. Correcting both did not produce
> symmetrical noise — it moved everything the same way. Across the 90-combination matrix, trade
> counts rose in **87 of 90** and gross profit factors fell in **69 of 90**.

Every prior conclusion in `docs/experiments/` was therefore measured on an engine that was
simultaneously too pessimistic about how often a rule fires and too optimistic about what it pays
to get in.

## 2. The one conclusion that changed

#252 recorded ZEC-`turtle` as the single cell clearing `n≥100 ∧ gross>1 ∧ maker>1`, subsequently
eliminated by a temporal probe showing three consecutive losing years. Under the faithful engine it
never clears at all:

```
ZEC-USD turtle    touch-fill (#256):  n=247   gross 1.542   maker 1.034
                  market-fill (#258): n=268   gross 1.442   maker 0.968
```

That collapses #252 §6's architecture. Its argument — three probes, each catching what the others
miss, with ZEC as the through-line surviving the fee curve and the tail test before dying to the
temporal one — describes a survivor the faithful engine does not produce.

**The replacement is shorter and worse for the strategy library.** Nothing needs three gates to
die, because nothing passes the first. The temporal and tail probes are not thereby worthless:
they remain the reason we know ZEC's apparent edge was regime-bound (92.7% of lifetime PnL in
2025–26) and tail-carried (six of seven gross-positive assets fell below break-even on deleting
three trades). They are repositioned from *"the mechanism that killed the last survivor"* to
*"forensics explaining an artifact a defective engine manufactured."*

#255's conclusions all survive unchanged.

## 3. #252 restated

| rule | median n | n≥100 | median gross | C1+C2 | C1+C2+C3 maker | taker |
|---|---|---|---|---|---|---|
| `turtle_breakout` | 241 → **262** | 21 → **21** | 0.9892 → **0.9262** | 7 → **5** | ZEC → **NONE** | 0 → 0 |
| `pullback_continuation` | 60 → **124** | 4 → **14** | 0.9292 → **0.7736** | 1 → **1** | 0 → 0 | 0 → 0 |
| `rsi_meanrev` | 38 → **42** | 0 → **0** | 1.1631 → **1.1251** | 0 → 0 | 0 → 0 | 0 → 0 |
| Arm B (OOS) | 84 → **92** | 1 → **2** | 1.2311 → **1.2015** | 0 → **1** | 0 → 0 | 0 → 0 |

**Every cell clearing `n≥100 ∧ gross>1`, and what it does once costs are charged:**

```
turtle    ZEC-USD    n=268  gross 1.442  maker 0.968  taker 0.685
turtle    XRP-USD    n=157  gross 1.223  maker 0.688  taker 0.438
turtle    FET-USD    n=269  gross 1.206  maker 0.825  taker 0.599
turtle    PAXG-USDT  n=238  gross 1.145  maker 0.194  taker 0.051
turtle    CRV-USD    n=260  gross 1.017  maker 0.648  taker 0.445
pullback  ZEC-USD    n=170  gross 1.044  maker 0.344  taker 0.144
Arm B     PAXG-USDT  n=101  gross 1.987  maker 0.484  taker 0.154
```

Seven cells of ninety are gross-positive at the floor. **All seven die at the maker rate**, before
the taker rate we actually pay is even reached.

The only cells anywhere above 1.0 at taker are WLD-USD (n=58, taker 1.061) and TON-USD (n=31,
taker 0.774 — below), both far under the trade floor and both on histories starting in 2025.

### 3.1 `pullback_continuation`, and a distinction worth protecting

Its numbers moved most: median n **58 → 124**, assets clearing the floor **1 → 14**, median gross
**0.9219 → 0.7736**. The mechanism is legible — `entry = signal_candle.high + buffer_ticks` is a
confirmation condition, and a market fill removes it, so the rule now takes the trades it was
designed to decline. The doubling *is* the count of those trades.

**It does not follow that the offset entry was generating alpha.** Both sides of that comparison
lose money gross: 0.9219 with the filter, 0.7736 without. The filter separated **bad from worse**,
not good from toxic. There is no profitable subset of `pullback_continuation` that the entry
condition was protecting, and this dataset must not be cited as evidence that offset entries add
edge.

What it *is* evidence for is #260: production silently overrides a rule's stated entry logic. The
landmine is not this rule — it is the next one that expresses a condition through its entry price.

### 3.2 Arm B, restated on a single engine

#252's headline transfer number compared figures produced by one engine. Recomputed with both
sides on #258:

```
in-sample mean net PF@1.2%, on the 6 selection assets : 0.5770
out-of-sample mean, on the 18 disjoint assets         : 0.5427
gap                                                    : 0.0343
```

#252 reported 0.6335 vs 0.6346 — a three-decimal agreement that was partly luck. The honest figure
is a gap of **0.034**, still a clean transfer, and the conclusion is unchanged and now
engine-consistent: **the sweep winner is not overfit; it is stably unprofitable**, which is the
harder result of the two.

## 4. #255 restated

| | old engine | faithful engine |
|---|---|---|
| `oversold=20` (the anchor) | 1.1631 at median n=38 | **1.1251** at median n=42 |
| every cell with `n≥100` | 0.8938 across 76 cells | **0.8396** across 82 cells |
| gross>1 at the floor | 11/76 | **6/82** |
| net>1 at maker | 0/76 | **0/82** |
| trigger (assets reaching `n≥100` at `oversold=40`) | 21/24 | **22/24** — did not fire |
| slope per +100 trades | −0.0386, 15/24 negative | **−0.0328, 15/24 negative** |

**The level shift widens.** 1.1251 → 0.8396 against the previous 1.1631 → 0.8938, and the count of
gross-positive cells at the floor nearly halves. The conclusion is unchanged and stronger:
`rsi_meanrev`'s apparent edge is a property of firing rarely, and it does not survive being made
measurable. The rule is unpromotable by construction.

### 4.1 The monotonicity anomaly is structurally gone

#255 §5 recorded that `oversold` did not reliably increase firing rate — three of 24 assets went
backwards, UNI-USD by 34×. That was the symptom that led to #256 and then #258. Re-measured:

```
OLD engine : 3 assets non-monotonic   AAVE 1.1x,  BTC 2.3x,  UNI 34.3x
#258 engine: 0 assets non-monotonic
```

Trade count is now monotonic in `oversold` across all 24 assets. This is an independent check on
the fixes rather than a restatement: the anomaly that exposed the defects is absent, not merely
smaller.

## 5. The operational takeaway: the engine needs observable invariants, not more tests

**Neither defect was found by looking for defects, and neither was findable by the means we had.**

- The full suite — **2,712 tests** — passed throughout, before and after both fixes.
- Every experiment in `docs/experiments/` ran on the defective engine and produced plausible,
  internally consistent output.
- #256 surfaced only from a 34× non-monotonicity, which was visible only because a grid happened
  to sweep one parameter across a wide enough range for the anomaly to be obvious.
- #258 surfaced only from asking why #256's fix *reduced* trade counts on some assets — a question
  nobody had a reason to ask.

The reason unit tests could not catch either is structural, and generalises:

> **A frozen backtest and a highly selective strategy produce identical-looking output.** So do a
> patient limit fill and a lucky one. Nothing about a summary ledger — n, win rate, profit factor —
> distinguishes "the rule declined to fire" from "the engine stopped asking", or "we got a good
> entry" from "we skipped every bad one".

Adding tests does not fix this, because a test asserts a behaviour someone already imagined. The
fix is **invariants the engine reports about itself**, so that a defect announces itself in ordinary
output rather than waiting to be inferred. Three, in the order they would have paid off:

**1. Dead-tail warning.** Report when a backtest's last trade closes far before its candle corpus
ends. #256 would have announced itself immediately — `rsi_meanrev` on UNI-USD had a 4.7-year dead
tail sitting in plain sight of every run.

**2. Intent-divergence logging (#260).** Have the live executor log when `intent.entry` differs
materially from the price at routing time. The order row already records both numbers side by side
and nothing compares them; #258's entire finding was one comparison away from being visible.

**3. Cost anchored to output (#247, already shipped).** `rules backtest` now prints
`fee_pct=1.2000% (taker, from config fees.taker_pct)` beside every result. This is the model for
the other two, and its own commit put it best: *prior numbers were unfalsifiable by their readers*.
A profit factor without its fee rate cannot be checked; neither can one without its slippage
assumption (#259) or its fill model.

The common property is that each makes a class of defect **visible in the normal output of a normal
run**, to a reader who is not hunting for it. That is the cheap half of correctness, and this
project has been paying for its absence.

## 6. Status of the two research branches

**Both are closed, and the closure is now engine-consistent.** Every signal rule the codebase ships
has been measured at its shipped defaults across 24 assets, and `rsi_meanrev` additionally along
its own frequency axis, on an engine matching production in both detection and fills. There is no
asset-rule-parameter combination that is simultaneously measurable (`n≥100`), gross-positive, and
net-positive at any fee reachable from this venue.

**Open, in priority order, none scheduled here:**

1. **#260** — the executor discards conditional entry prices. Not urgent for any current rule; a
   landmine for any future price-conditional one. The cheap mitigation is invariant 2 above.
2. **#259** — one global 5bp slippage constant applied from BTC to TON. Matters when a thin asset
   becomes a candidate, which is exactly when someone will want to trust the number.
3. **The PBO/CSCV gate is deployed and unfed.** #247 wired `g4_pbo_gate` into `can_promote` where
   `pbo=None` blocks. Nothing supplies it a trial matrix. Worth noting that Arm B (§3.2) is
   informal evidence overfitting has *not* been our binding constraint — a config selected on six
   assets reproduced within 0.034 on eighteen it never saw. The gate is a guard for a future
   candidate, not an instrument for finding one.
