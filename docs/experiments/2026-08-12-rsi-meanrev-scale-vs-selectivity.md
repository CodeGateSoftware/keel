# `rsi_meanrev`'s edge is selectivity, not alpha — and the search for it found a simulator defect

> **⚠️ AMENDED 2026-08-13 — read alongside
> [`2026-08-13-restated-under-a-production-faithful-engine.md`](2026-08-13-restated-under-a-production-faithful-engine.md).**
>
> Every number below was produced by an engine carrying two defects, since fixed (#256, #258).
> **All conclusions here survive**, and the central one strengthens: the level shift across the
> trade floor widens from 1.1631 → 0.8938 to **1.1251 → 0.8396**, and gross-positive cells at the
> floor nearly halve (11/76 → 6/82). Still 0 net-positive at maker.
>
> §5's monotonicity anomaly — the 34× UNI-USD collapse that led to both fixes — is **structurally
> gone**: 3 non-monotonic assets → 0.
>
> These numbers are **annotated, not restated**, per the convention #247 set.

**Date:** 2026-08-12
**Issue:** #253 — closes the single open question left by #252
**Change:** documentation only. No code, no config, no rule status, no version bump. The simulator
defect found in §5 is filed separately (#254) and deliberately **not** fixed here: a research PR
should not carry a change to the engine every prior experiment was measured on.
**Script:** `docs/experiments/2026-08-12-rsi-meanrev-scale-vs-selectivity.py` — **pre-registration
lives in its docstring, written before the run.** That is the correction to the defect §7 of #252
recorded against itself, where the declaration lived only in a dispatch brief.
**Ledger:** one row, `rsi-meanrev-scale-vs-selectivity-2026-08-12`.
**Deployment:** `keel 0.7.0`. Fees and slippage passed explicitly on every call.

**Verdict: `rsi_meanrev` can be made to clear the promotion floor trivially — 21 of 24 assets — and
it has no edge when it gets there. The rule is unpromotable by construction, and the last live lead
in the shipped strategy library closes negatively.**

| question | answer |
|---|---|
| did the primary arm reach `n≥100`? | **yes, 21/24** — the conditional proximity arm never fired |
| gross PF at defaults (median n=38) | median **1.1631**, 14/24 gross-positive |
| gross PF at every cell with `n≥100` | median **0.8938**, 11/76 gross-positive |
| net PF > 1.0 at `n≥100`, any fee | **0 of 76** — including maker |
| pre-registered slope | **−0.0386** per +100 trades (median −0.0197), 15/24 negative |
| was the declared statistic the right one? | **no** — see §4, it was underpowered for its own question |
| anything else? | a **pending setup never expires**, silently freezing a strategy for the rest of a series (§5) |

---

## 1. The question #252 left open

`2026-08-12-shipped-defaults-intersection.md` measured all three shipped signal rules at their
constructor defaults across 24 assets. `rsi_meanrev` came out of it with the best gross-edge
distribution of the three — median gross PF **1.1631** against `turtle`'s 0.9892 and `pullback`'s
0.9292 — while reaching `min_trades=100` on **zero** assets, median n=38.

Two readings, opposite consequences, and no data separating them:

- **(a) the edge is real** and the defaults are over-constrained, in which case relaxing them
  reaches the floor with the edge intact and this is the only promotable rule in the codebase;
- **(b) the edge is an artifact of selectivity** — the rule looks good because it only fires on
  rare, easy setups, and buying trades means accepting worse ones.

#252 called this "the one live lead" and "the only route by which any rule the codebase ships
reaches its own promotion floor honestly."

## 2. Design

A **monotonicity test, not a search.** The declared statistic was the *slope* of gross profit
factor against n, per asset then averaged — chosen because with 120 cells a maximum is guaranteed
and a slope is not. The docstring says it outright: *"The best cell is never reported as a result."*

**Variable axis, one only:** `oversold ∈ {20, 25, 30, 35, 40}`.

`oversold` is the entire frequency mechanism, and that was measured rather than assumed — #248's
108-cell diagnostic found `oversold` 25→30 multiplied trade count ×2.18 and 30→35 by ×3.93, against
×1.186 for `support_proximity_pct` and ×1.185 for `level_min_touches`.

**Held fixed:** `overbought=80`, `support_proximity_pct=0.005`, everything else at defaults.
`overbought` is held not because it is a weak lever but because it is the **wrong kind** — it
governs the exit side, so moving it changes trade *outcomes* and not merely trade *counts*, and the
slope would stop being interpretable.

**Anchor:** `oversold=20` is the shipped default, already measured across all 24 assets by #252.
Those rows are reused rather than recomputed — identical in every other parameter and in cost
treatment — so this grid ran 4 new levels × 24 assets = **96 combinations**, 3h36m on 8 workers.

**Conditional arm, declared before any data existed:** if fewer than 8 of 24 assets reached
`n≥100` at `oversold=40`, widen `support_proximity_pct ∈ {0.005, 0.02, 0.05}` and report it as a
**separate curve, never pooled**. Writing the trigger and the reporting rule down in advance is
what stops a widening from being invented later to rescue a disappointing primary arm.

**It did not fire.** 21 of 24 assets reached `n≥100` at `oversold=40`. Clean single-axis answer.

## 3. The result

```
gross PF at oversold=20 (median n=38)  : median 1.1631    gross>1: 14/24
gross PF at every cell with n>=100     : median 0.8938    gross>1: 11/76
net > 1.0 at n>=100, at ANY fee        : 0 of 76   (including 0.6% maker)
```

**The edge evaporates exactly when the rule becomes measurable.** Not gradually as trades
accumulate — as a level shift across the floor. The 1.1631 that made this rule look like the best
of the three *is* what n=38 looks like.

The per-asset curves show the same shape almost everywhere: a high, noisy profit factor at
`oversold=20` on a few dozen trades, collapsing to ~0.9 by `oversold=25` and staying flat
thereafter.

```
asset            20              25              30              35              40
              n  grossPF      n  grossPF      n  grossPF      n  grossPF      n  grossPF
ADA-USD      41    1.746    128    0.934    309    0.925    600    0.878    875    0.879
FET-USD      26    1.652     94    0.993    240    0.824    496    0.815    623    0.867
ICP-USD      22    1.566     81    0.603    235    0.783    505    0.993    684    0.957
XLM-USD      39    1.484    124    0.904    298    0.874    571    0.876    821    0.813
DOGE-USD     64    1.417    164    1.001    366    0.989    652    0.921    925    0.897
PAXG-USD     12    2.877     32    1.476     70    0.913    104    0.962    145    0.908
```

For completeness rather than selection — the declaration forbids reporting a best cell — **all 11
gross-positive cells at `n≥100`**, and what each does once costs are charged:

```
ALGO-USD  oversold=25  n= 112  gross=1.215  maker=0.678  taker=0.395
ZEC-USD   oversold=40  n= 464  gross=1.210  maker=0.645  taker=0.345
AAVE-USD  oversold=25  n= 160  gross=1.179  maker=0.645  taker=0.360
ZEC-USD   oversold=30  n= 238  gross=1.086  maker=0.581  taker=0.307
BTC-USD   oversold=40  n= 118  gross=1.072  maker=0.360  taker=0.110
AVAX-USD  oversold=25  n= 142  gross=1.045  maker=0.550  taker=0.291
ZEC-USD   oversold=35  n= 450  gross=1.031  maker=0.542  taker=0.283
BCH-USD   oversold=25  n= 160  gross=1.031  maker=0.491  taker=0.242
AVAX-USD  oversold=35  n= 583  gross=1.026  maker=0.509  taker=0.256
CRV-USD   oversold=35  n= 460  gross=1.013  maker=0.561  taker=0.315
DOGE-USD  oversold=25  n= 164  gross=1.001  maker=0.533  taker=0.293
```

The best of them is 1.215 gross and 0.678 at maker. There is no cost structure reachable from here
that makes any of these viable.

**Hypothesis (b), confirmed.** The rule is unpromotable by construction: no parameter choice
escapes buying volume with quality, because the quality was never there at volume.

## 4. The pre-registered statistic was underpowered for its own question

Reported as declared, because that is what pre-registration is for:

```
slope of gross PF per +100 trades, across 24 assets
  mean  -0.0386     median -0.0197     stdev 0.3487
  negative 15/24    positive 9/24
  min -1.2172 (PAXG-USD)     max +1.0626 (WLD-USD)
```

Directionally right, and the sign is the one the prediction attached to hypothesis (b). But it is a
weak instrument for what turned out to be happening, and the write-up should say so rather than
present a marginal number as a clean one:

1. **The relationship is not linear.** Fitting a straight line through five points whose leftmost
   is n≈38 and whose rightmost is n≈800 mostly measures the leftmost point. The phenomenon is a
   threshold effect at the floor, which a slope smears out.
2. **The spread is contaminated by assets that barely trade.** PAXG-USD (n 12→145) and WLD-USD
   (n 1→71) supply the −1.22 and +1.06 extremes and most of the 0.35 standard deviation. Their
   "slopes" are fitted through noise.

The partition on `n≥100` in §3 is the decisive reading, and it is not a post-hoc convenience: the
floor was pre-declared as C1 in #252 and is the criterion the promotion gate actually applies. What
is fair to say against ourselves is that this partition **should have been the declared primary
statistic**, and the fact that n varies twentyfold across the grid was knowable in advance.

Recording this because the alternative — quietly reporting the stronger analysis and omitting that
it was not the declared one — is the exact failure the pre-registration exists to prevent.

## 5. The design's monotonicity assumption is violated, and the cause is a simulator defect

`oversold` does not always increase firing rate. Three of 24 assets go backwards:

```
UNI-USD   56@20  135@25  309@30     9@35    15@40     <-- 309 -> 9
BTC-USD   37@20   98@25  181@30    80@35   118@40
AAVE-USD  40@20  160@25  324@30   582@35   524@40
```

UNI's collapse is 34×, and it is not a rule behaviour:

```
oversold=30: closed=309  open=1   last exit 2026-08-04     (trades throughout)
oversold=35: closed=9    open=0   last exit 2021-11-15     (dead for 4.7 years)
```

No open position, so nothing is stuck in a trade. The rule stopped *detecting* for ~40,000
consecutive bars. The cause is in `backtest()`:

```python
if position is None and pending is None:
    pending = rule.detect(candles_by_tf)
    continue

if position is None and pending is not None:
    entry_touched = _touches(candle, pending.entry)
    stop_touched = _touches(candle, pending.stop)
    if not entry_touched:
        continue          # pending persists
```

**A pending setup has no expiry.** The only path that clears it without a fill is the stop being
touched first and `_resolve_order` confirming the stop preceded entry. If price drifts away so that
*neither* entry nor stop is touched again, `pending` stays set forever, the
`position is None and pending is None` branch never runs again, and **`rule.detect()` is never
called for the remainder of the series**.

The strategy freezes silently. The output is indistinguishable from "the rule found no more
setups", which is precisely how it survived unnoticed: a frozen backtest looks like a selective one.

This matters beyond the anomaly:

- It is **rule-agnostic and study-wide**. Every backtest in #245–#252 ran on this code.
- It biases *toward* low trade counts, i.e. toward exactly the "unmeasurable" verdicts these
  documents have been issuing.
- A real deployment does not behave this way: `keel`'s live path re-detects each cycle, so this is
  a simulator-only divergence from production semantics — which makes it a fidelity bug, not just
  a performance one.

**The headline results of #252 were checked against it and are clean.** The three `turtle` assets
that missed the trade floor are not frozen — each trades to within days of its series end, and
their low counts are genuinely short history:

```
asset       n    last exit    series end   dead tail
PAXG-USD   61   2026-08-11    2026-08-12         1d      (history starts 2025-05)
WLD-USD    45   2026-07-21    2026-07-23         2d      (starts 2025-04)
TON-USD    29   2026-07-22    2026-07-23         1d      (starts 2025-11)
ZEC-USD   250   2026-07-16    2026-07-23         7d
BTC-USD   274   2026-07-27    2026-08-12        16d
```

So #252 stands as written. What is **not** established is the defect's blast radius across the
lower-n cells of every prior sweep, and quantifying that means re-running them — filed as #254
rather than guessed at here.

## 6. What this closes, and what it leaves

**Closed.** `rsi_meanrev` was the last rule with an untested route to the promotion floor. It
reaches the floor easily and has no edge there. Combined with #252, every signal rule the codebase
ships has now been measured at defaults across 24 assets and along its own frequency axis, and
there is no asset-rule-parameter combination that is simultaneously measurable and profitable at
any reachable fee.

**Left open, in priority order:**

1. **#254 — the pending-expiry defect.** A correctness issue in the instrument every one of these
   conclusions was produced with. It should be fixed and the affected sweeps re-run before any
   further strategy work, because until then every low trade count in this directory carries an
   asterisk.
2. **The PBO/CSCV gate is deployed and unfed.** #247 wired `g4_pbo_gate` into `can_promote` where
   `pbo=None` blocks. Nothing supplies it a trial matrix. That is the principled instrument for
   the overfitting questions these documents keep answering by hand.
3. **Nothing else here justifies engineering investment.** In particular the maker-execution and
   queue-simulation work has no target: #252 removed its only candidate, and this document removes
   the rule that might have supplied another.
