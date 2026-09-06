# The 08-13 restatement, restated: the engine never moved the numbers — three weeks of candles did

**Date:** 2026-09-05 · **Restates:**
[`2026-08-13-restated-under-a-production-faithful-engine.md`](2026-08-13-restated-under-a-production-faithful-engine.md)
· **Engine:** `keel` at `main`, every code path this run exercises **byte-identical to `v0.13.3`**
(verified by `git diff v0.13.3 HEAD --` over `strategy/backtest.py`, `strategy/exit_policy.py`,
`strategy/stats.py`, all five rule modules, and `compliance.screen.median_daily_quote_volume`) ·
**Drivers:** `2026-09-05-restatement-restated.py` (960 trials, artifact
`2026-09-05-restatement-restated.jsonl`) and `2026-09-05-restatement-restated-control.py`
(§2's corpus control) ·
**Ledger row:** `restatement-restated-2026-09-05`

> **Read this first.** The 08-13 document is the record the front page of keeltrading.com cites for
> "0 of 90" and "0 of 82". **Its verdict survives, unchanged and stronger. Its numbers, its cost
> model and its scope claim do not.** This document does not rewrite it — records here are appended
> to, never revised (#247). It states what those same measurements produce on today's engine, over
> a population widened from three shipped rule families to five.

> **Cost note, and why this record carries a different one from its neighbours.** Every other
> document in this directory is priced at the flat 5bp floor and points at
> [the per-product restatement](2026-09-01-per-product-slippage-restatement.md) for the correction.
> This one reports **both** regimes side by side. Its **flat columns are a bridge, not a result** —
> they exist so §2 can compare like-for-like against the numbers 08-13 printed, and they are priced
> at a floor that restatement measured **0 of 24 assets reach**. Every **verdict** on this page is
> stated at per-product pricing and is unaffected by the correction; where a flat figure is quoted
> as though it were a finding — ZEC crossing the maker rate in §2.1 — §2.2 exists to remove it.

---

## The verdict, first

**Zero of 240 configurations clear `n ≥ 100 ∧ profit_factor > 1.0` at the taker rate this account
actually pays, priced per product.** Zero at the maker rate it cannot reach. The 08-13 null holds
across a population 2.7× larger, under a cost model that is strictly more expensive, on a corpus
23 days longer.

And the headline of this run is not that number, because that number was expected. It is this:

> **Three of the six cells 08-13 printed reproduce BIT-IDENTICALLY on today's engine.** Every
> engine change since — the gap-through-stop exit fill (#442), the ratchet exit policy (#442), the
> re-derived slippage cap (#523) — moved **nothing**. What moved the 08-13 record is **565 hourly
> candles**, and on one asset it moved a gross profit factor by **+0.67**.

That is a finding about evidence, not about execution, and §2 is the whole of it.

---

## 1. What changed under the 08-13 record, and what this run had to separate

Four things happened between 2026-08-13 and today, and the 08-13 document is silent about three
of them:

| | change | annotated in 08-13? |
| :-- | :-- | :-- |
| **#334/#335** | fills priced **per product** from each asset's own liquidity; `rules backtest` / `rules promote` now default to it | yes — a cost note added 2026-09-02 |
| **#442** | a bar gapping wholesale through the stop exits at that bar's **OPEN**, not at the stop; plus the ratchet exit policy, wired per family and default-OFF | **no** |
| **#523** | the slippage cap is the corpus tail (**183.8bp**), not a round 50bp | **no** |
| **#341/#342** | `cusum_event` and `triple_barrier` shipped — five signal families now, not three | **no** |

The last one falsifies a sentence. 08-13 §6 says *"every signal rule the codebase ships has been
measured at its shipped defaults across 24 assets."* Three shipped then. Five ship at `v0.13.3`
(six `RULE_REGISTRY` kinds with `dca`, which never closes and carries no profit factor, §12.6).
That sentence was true when written and is false now.

**And a fifth thing happened that is not a change to keel at all: the deployment fetched more
candles.** `~/keel/keel.db` has grown **565 ONE_HOUR bars** on the products it kept current — 23
days. A naive re-run would have absorbed that silently and attributed it to the engine. §2 exists
because it nearly did.

## 2. The control: the engine is innocent

Six cells, the exact table 08-13 §3 printed, run three ways at the flat 5bp those numbers were
priced at — as 08-13 printed them, on **today's engine over the 08-13 corpus** (candles truncated
to `ts < 2026-08-13`), and on today's engine over the **full** corpus. The middle column isolates
the engine. The gap between the middle and right columns is the new data.

| cell | n / gross · 08-13 | n / gross · today, **08-13 corpus** | n / gross · today, **full corpus** | bars added |
| :-- | --: | --: | --: | --: |
| `turtle` XRP-USD | 157 / 1.223 | **157 / 1.223** | 157 / 1.223 | +0 |
| `turtle` PAXG-USDT | 238 / 1.145 | **238 / 1.145** | 238 / 1.145 | +0 |
| `turtle` FET-USD | 269 / 1.206 | **269 / 1.206** | 272 / 1.207 | +565 |
| `turtle` CRV-USD | 260 / 1.017 | 260 / 1.018 | 267 / 1.023 | +565 |
| `turtle` ZEC-USD | 268 / 1.442 | 271 / 1.411 | 274 / **1.700** | +565 |
| `pullback` ZEC-USD | 170 / 1.044 | 171 / 1.025 | 174 / **1.695** | +565 |

A fourth identity, from Arm A rather than the control: `rsi_meanrev` at its shipped `oversold=20`
reproduces 08-13 §4's anchor **exactly** — median gross **1.1251** at median **n=42**, the same two
figures to four decimals and one integer. It fires rarely enough that its last trade predates every
new bar, so it is a pure engine identity across the whole 24-asset universe.

**Three cells and a 24-asset median reproduce to the digit. #442 and #523 are inert here** —
`turtle_breakout` carries a static stop and none of its exits meet a bar that trades wholesale
below it; neither rule declares `trail_atr_mult`/`be_roll_rr`, so the ratchet is identity by
construction. The two cells that moved differ only by the trades the new candles added. ZEC's
+3 trades in the truncated column against 08-13's count is the 08-13 fetch state sitting slightly
behind the truncation boundary, not a semantic difference — the three exact identities settle that.

### 2.1 The finding this bought, which is worth more than the null

08-13 §2 argued ZEC's apparent edge was **tail-carried**, and evidenced it by deletion: *"six of
seven gross-positive assets fell below break-even on deleting three trades."* A deletion test is an
argument about a counterfactual. This run ran the experiment in the other direction, by accident,
and it is not a counterfactual:

> **Adding three trades moved `pullback_continuation` on ZEC-USD from gross 1.025 to 1.695.**
> Adding six moved `turtle_breakout` on ZEC-USD from 1.411 to 1.700 — far enough to lift it across
> the **maker** rate at 1.139, which 08-13 §3 states flatly that none of its seven gross-positive
> cells survived.

A number that a fortnight of ordinary data moves by +0.67 on 174 trades is not measuring an edge.
It is measuring three trades. **This is the deletion test's converse, run on real data rather than
on a resample, and it is the cleanest evidence in this directory that the 08-13 gross-positive
column was never edge to begin with.**

### 2.2 And per-product pricing deletes the whole episode anyway

The maker crossing above is an artifact of the flat floor, and pricing ZEC at its own liquidity
removes it before a single fee is charged:

```
turtle ZEC-USD, n=274      flat 5.0bp    : gross 1.700   maker 1.139   taker 0.808
                           per-product   : gross 0.889   maker 0.650   taker 0.494
                             107.3bp = 21.5x the floor, on $1.08M/day
```

ZEC-USD is the **fourth-thinnest name in the universe**. Pricing it at the floor charged it 5bp to
cross a book that plausibly costs 107. **Gross falls below 1.0** — the rule loses money before fees
exist. Everything §2.1 describes happened inside a pricing error, and the honest reading is that
the 08-13 "seven gross-positive cells at the floor" were an artifact of the floor.

## 3. Arm A — the intersection, restated and widened

Five shipped signal families at their shipped defaults, 24 assets, three fees, both slippage
regimes. No sweep, no argmax, no free parameters: **120 cells, 720 trials.**

**The predicate 08-13 reported as "0 of 90":**

| slippage | fee 0 | maker 0.6% | **taker 1.2%** |
| :-- | --: | --: | --: |
| flat 5bp (08-13's pricing) | 23 of 120 | 1 of 120 | **0 of 120** |
| **per product (what the engine now does)** | 3 of 120 | **0 of 120** | **0 of 120** |

Median profit factor per family at the taker rate, and what honest pricing costs:

| rule | flat | per-product | delta |
| :-- | --: | --: | --: |
| `turtle_breakout` | 0.3363 | 0.2664 | −0.0699 |
| `rsi_meanrev` | 0.2606 | 0.1751 | −0.0855 |
| `pullback_continuation` | 0.0423 | 0.0118 | −0.0305 |
| `cusum_event` | 0.3420 | 0.2417 | −0.1003 |
| `triple_barrier` | 0.3391 | 0.2364 | −0.1027 |
| **all 120 cells** | **0.3120** | **0.2192** | **−0.0928** |

**This independently reproduces the 09-01 per-product restatement.** That document measured a
−0.090 median overstatement across the same five families; this run, four days later on a corpus
565 bars longer and with the arms rebuilt from scratch, measures **−0.0928**, and every per-family
figure lands within 0.005 of its 09-01 counterpart. Two independent runs agreeing to the third
decimal is the strongest statement this directory can make that the number is real.

### 3.1 The cells 08-13 printed, at the price their assets actually pay

08-13 §3 listed seven gross-positive cells at the floor and observed that all seven die at the
maker rate. Six of those seven are Arm A cells and are re-priced here; the seventh is an Arm B
cell, which this run measured **only at the taker rate**, so it has no zero-fee figure below.
Priced per product, **five of the six are already dead at zero fee** — the exception is XRP-USD,
the cheapest of them to trade at 13.9bp:

| rule | product | n | slippage | gross | maker | taker |
| :-- | :-- | --: | --: | --: | --: | --: |
| `turtle` | ZEC-USD | 274 | 107.3bp (21.5×) | **0.889** | 0.650 | 0.494 |
| `turtle` | XRP-USD | 157 | 13.9bp (2.8×) | 1.109 | 0.640 | 0.412 |
| `turtle` | FET-USD | 272 | 57.1bp (11.4×) | **0.865** | 0.624 | 0.470 |
| `turtle` | PAXG-USDT | 238 | 66.7bp (13.3×) | **0.186** | 0.049 | 0.016 |
| `turtle` | CRV-USD | 267 | 70.9bp (14.2×) | **0.626** | 0.432 | 0.312 |
| `pullback` | ZEC-USD | 174 | 107.3bp (21.5×) | **0.438** | 0.258 | 0.172 |

08-13's closing observation — *"the only cells anywhere above 1.0 at taker are WLD-USD (n=58, taker
1.061) and TON-USD (n=31, taker 0.774 — below)"* — does not survive either. WLD pays **120.9bp**
(24.2×) and TON pays the **183.8bp cap** (36.8×):

```
turtle WLD-USD  n=58   taker, flat 1.061  ->  taker, per-product 0.626
turtle TON-USD  n=31   taker, flat 0.774  ->  taker, per-product 0.317
```

### 3.2 The measured slippage, in full

**0 of 24 assets reach the 5bp floor.** Median **10.5× (52.3bp)** — the mean of ranks 12 and
13, BCH-USD and AAVE-USD; thirteen assets above 10×, four above 20×.

| | product | per-leg | × floor | median daily quote volume |
| :-- | :-- | --: | --: | --: |
| dearest | TON-USD | 183.8bp | 36.8× (the cap) | $0.28M |
| | PAXG-USD | 135.1bp | 27.0× | $0.69M |
| | WLD-USD | 120.9bp | 24.2× | $0.86M |
| | ZEC-USD | 107.3bp | 21.5× | $1.08M |
| **median** | **BCH-USD / AAVE-USD** | **53.5 / 51.2bp** | **10.7× / 10.2×** | $4.37M / $4.77M |
| | SOL-USD | 12.1bp | 2.4× | $85.21M |
| | ETH-USD | 7.0bp | 1.4× | $253.53M |
| cheapest | BTC-USD | 5.5bp | 1.1× | $419.73M |

## 4. Arm B — the transfer check, and the one number that got worse

08-13 §3.2 recomputed its headline out-of-sample transfer on a single engine and reported an
in-sample/out-of-sample gap of **0.034**, concluding: *"the sweep winner is not overfit; it is
stably unprofitable."* Same config, same 6/18 split, both sides run here:

| slippage | in-sample (6 selection assets) | out-of-sample (18 disjoint) | gap |
| :-- | --: | --: | --: |
| flat 5bp | 0.6621 | 0.5449 | **0.1172** |
| per product | 0.5435 | 0.4074 | **0.1362** |

**The gap is 3.4× to 4× wider than 08-13 measured.** The second half of its conclusion is
untouched — both sides are far below 1.0, and *stably unprofitable* is exactly what 0.54 against
0.41 describes. The first half is now weaker than it was stated: a 0.136 gap is not the clean
transfer 0.034 was, and 08-13 §6's use of Arm B as *"informal evidence overfitting has not been our
binding constraint"* should be read with that number beside it. It remains informal evidence, and
it is a weaker piece of it than the document claims.

**And the one cell in this entire run above 1.0 at the fee actually paid is here:**

```
Arm B  turtle_breakout  ZEC-USD  per-product, taker:  PF 1.0275  at  n=96
```

Ninety-six trades. **Four short of the admission floor**, on the asset 08-13 §2 already established
is regime-bound (92.7% of lifetime PnL in 2025–26) and tail-carried, priced at 21.5× the floor. It
is reported because it exists, not because it means anything: it is one cell in 240, it fails the
floor it would have to clear, and §2.1 is a demonstration on this exact asset that a handful of
trades moves its profit factor by more than this cell clears 1.0 by.

## 5. Arm C — the frequency axis, restated

`rsi_meanrev`'s `oversold` swept across 24 assets at the taker rate, both regimes. The `oversold=20`
row is Arm A's shipped default. **96 cells, 192 trials.**

| `oversold` | median n | median PF, flat | median PF, per-product | n ≥ 100 | **n≥100 ∧ PF>1** |
| --: | --: | --: | --: | --: | --: |
| 20 (shipped) | 42 | 0.2606 | 0.1751 | 0 of 24 | **0** |
| 25 | 130 | 0.2416 | 0.1538 | 18 of 24 | **0** |
| 30 | 334 | 0.1860 | 0.1089 | 21 of 24 | **0** |
| 35 | 622 | 0.1806 | 0.1021 | 22 of 24 | **0** |
| 40 | 910 | 0.1782 | 0.0992 | 22 of 24 | **0** |

Every 08-13 §4 conclusion survives, and two of its checks reproduce exactly:

- **The trigger did not fire, again.** 08-13 declared it would if fewer than 8 of 24 assets reached
  `n ≥ 100` at `oversold=40`. It measured **22 of 24**. This run measures **22 of 24**.
- **Monotonicity holds, again.** 08-13 §4.1 recorded that the fixes removed the non-monotonicity
  that exposed them: 3 assets non-monotonic on the old engine, **0** after. This run: **0 of 24**,
  on a corpus 565 bars longer. The anomaly is still absent, which is now a two-month-old check
  rather than a fresh one.
- **The level shift is unchanged in shape and worse in level.** Across all 120 per-product cells
  with the axis pooled, **83 reach `n ≥ 100`, median PF 0.1208, and 0 of 83 clear 1.0.** The rule's
  apparent edge remains a property of firing rarely, and it still does not survive being made
  measurable.

## 6. The break-even table, re-derived at the price the assets actually pay

This is the correction with the widest reach, because it is the one the website states as a
headline. `docs/research/2026-08-20-quant-lab-note-cross-verification.md` §5 applies the note's
`κ = 2(φ+ψ)/s` and `p_be = (1+κ)/(1+b)` at the median hourly stop `s = 2.40%` and `b = 6`, and
concludes that inside rail 14's fee-free allowance the rules sit **indistinguishably at
break-even**. That row is priced at **ψ = 5bp** — the floor this run measures **0 of 24 assets
reach**. Re-derived at the measured median ψ, with the note's own arithmetic and nothing else
changed:

| regime | φ | ψ | κ | `p_be` | vs the reconstructed 14.9% win rate |
| :-- | --: | --: | --: | --: | --: |
| inside allowance | 0 | 5bp (the note) | 0.042 | 14.88% | **+0.02 pts** |
| **inside allowance** | 0 | **52.3bp (measured median)** | 0.436 | **20.52%** | **−5.62 pts** |
| outside allowance | 120bp | 5bp (the note) | 1.042 | 29.17% | −14.27 pts |
| **outside allowance** | 120bp | **52.3bp (measured median)** | 1.436 | **34.80%** | **−19.90 pts** |

**"Indistinguishable from break-even inside the fee-free allowance" does not survive its own cost
model.** At the slippage the assets actually pay, the reconstructed rules are **5.6 points
underwater inside the allowance** — and the note's own sensitivity row already went negative at
ψ = 10bp, a fifth of the measured median.

What survives, and should replace it: **rail 14 is still the sharpest term in the result** — the
allowance is worth 14.3 points of break-even, more than any rule change measured in this directory
has ever produced. It is just no longer the boundary between negative and break-even. It is the
boundary between **decisively** negative and **clearly** negative. The taker fee is still the
largest single term; it is no longer the entire result, because slippage priced honestly is now the
second.

## 7. What this changes, and what it does not

**Unchanged, and now measured over a wider population and a longer corpus:**

- No shipped rule family is net positive at the taker rate. **0 of 240**, per product.
- Nothing clears at the maker rate either. **0 of 120** at shipped defaults.
- `rsi_meanrev`'s edge is selectivity, not alpha, and does not survive being made measurable.
- The sweep winner is stably unprofitable.
- The rule families divide the way 08-12 first recorded: `turtle_breakout` has a gross edge that
  cost destroys; `rsi_meanrev`, `cusum_event` and `pullback_continuation` have essentially none for
  cost to destroy.

**Changed, and these are corrections of record:**

1. **08-13 §6's scope claim is false.** Three families were measured; five ship. This document is
   the measurement that makes the sentence true again.
2. **08-13 §3's "all seven die at the maker rate" was an artifact of the flat floor.** Priced per
   product, **five of the six Arm A cells among those seven are dead at zero fee** — XRP-USD is
   the exception at 1.109, and the seventh cell is Arm B, which this run priced only at the taker
   rate. So is the WLD/TON observation that follows it.
3. **08-13 §3.2's 0.034 transfer gap is 0.117–0.136 when re-measured.** *Stably unprofitable*
   holds; *not overfit* is a weaker claim than the document makes.
4. **The 08-20 note's break-even-inside-the-allowance row is superseded** by §6 above.
5. **Neither #442 nor #523 moved any number in the 08-13 record.** Recorded because the honest
   expectation was that they would, and because a conservative-only correction that turns out to be
   a no-op is worth knowing before the next one is deferred on the same reasoning.

**Not changed by this document, and deliberately:** no rule row was added, nothing was promoted, no
config, allowlist or shipped parameter was touched. `docs/experiments/` records are appended to,
never revised — 08-13 keeps its numbers, and this page is what they become.

## 8. Honesty

- **No argmax anywhere in Arm A.** One pre-declared configuration per rule; 120 cells with zero
  free parameters. Arm B is one config on 24 assets, split 6/18 on a set fixed in August. Arm C is
  a disclosed sweep of one knob, and its per-asset best is a maximum of five draws — no cell in it
  comes near 1.0, so the bias changes nothing.
- **Screening result only.** No walk-forward, no PBO/CSCV (`series_missing`), no out-of-sample
  split beyond Arm B's. Same cached candles and ~5-year window as every document it restates.
- **The slippage model is an assumption, not a measurement.** keel stores no book snapshots;
  `slippage_for_quote_volume` proxies liquidity by the one statistic it computes, and §3.2's rates
  are that curve's output. Its own docstring records that it prices a clip ($26k–$165k) this
  deployment does not trade ($50) — an overstatement (#626), stated rather than cancelled against
  another error.
- **§6's 14.9% win rate is a reconstruction**, inherited from the 08-20 note. The +0.02 and −5.62
  columns are only as good as it is; what survives either way is the sign and the size of the shift,
  not the second decimal.
- **The corpus grew mid-record.** Every figure outside §2's control column is measured on candles
  through 2026-09-05, and products were last fetched on different dates (XRP-USD stops 2026-07-23,
  AAVE-USD 2026-08-11, BTC/ZEC 2026-09-05). That unevenness is in the data, not introduced here,
  and it is why §2 has a truncated column at all.
