# Triple-barrier exits: a real gross improvement, worth exactly nothing after friction

> **Cost note (added 2026-09-02).** The figures below are priced at the flat 5bp
> slippage floor. [the per-product restatement](2026-09-01-per-product-slippage-restatement.md) later measured that **no
> asset in keel's universe reaches that floor** — the range is 1.1× to 36.8× — so every
> profit factor here is optimistic by roughly 0.09 at the median. **The verdict is
> unaffected:** the correction only ever moves a number *down*, and every result here was
> already negative. Nothing on this page has been rewritten; records are appended to, not
> revised.

**Date:** 2026-09-01 · **Issue:** [#342](https://github.com/CodeGateSoftware/keel/issues/342) ·
**Rule:** `triple_barrier` · **Control:** `cusum_event`
([measured the same day](2026-09-01-cusum-event-first-measurement.md)) ·
**Driver:** `2026-09-01-triple-barrier-first-measurement.py` · **192 trials disclosed**

## Why this is an A/B and not another level reading

`cusum_event` and `triple_barrier` **share an entry** — the same CUSUM filter at the same
threshold, on the same universe over the same window. What differs is the exit: ATR barriers with
a signal exit, against friction-sized barriers with a vertical time stop. Holding the entry fixed
makes the exit the only thing that changed, so the difference is attributable.

**Declared before the run:** the primary metric is the **delta in profit factor against the
control at the taker rate** — not the level. The level was already known to be a null, and asking
"does it clear 1.0" invites reading a 0.4 as encouraging. The question is how much a better exit
moves a rule whose entry has no gross edge.

## The answer, in two numbers

| | zero fee | 1.2% taker |
| :-- | --: | --: |
| control (`cusum_event`) median PF | 0.925 | 0.343 |
| `triple_barrier` median PF | **1.001** | **0.338** |
| median delta | **+0.033** | **−0.004** |
| assets improved | **17 of 24** | 11 of 24 (a coin flip) |

**The exit genuinely works.** At zero fee it lifts the median profit factor across break-even —
from 0.925 to 1.001 — and improves 17 of 24 assets. That is a real, measurable gross improvement
from a better exit, and it is the first thing in this series of documents to move a number in the
right direction.

**And it is worth nothing.** At the rate the account actually pays, the median delta is −0.004
and the sign of the improvement is a coin flip. The gross gain the exit produces is smaller than
the friction it must be harvested through.

Zero of 24 clear PF 1.0 at the taker rate, and zero at the 0.6% maker rate either. n ≥ 100 on 20
of 24, median 431 — the vertical barrier closes positions the signal exit would have let run, so
trade count falls slightly against the control (median 553) while staying well clear of the floor.

## The vertical barrier alone

Arm B, `max_holding_bars` at the taker rate — the one leg no other rule has, and the only knob
the source grid-searched:

| bars | median n | PF median | PF max | above 1.0 |
| --: | --: | --: | --: | --: |
| 6 | 520 | 0.161 | 0.276 | 0 |
| 12 | 486 | 0.243 | 0.412 | 0 |
| 24 | 431 | 0.338 | 0.527 | 0 |
| 48 | 374 | 0.392 | 1.270 | 1 |
| 72 | 338 | 0.464 | 1.063 | 1 |

Monotone: holding longer is better, and the source's own 24-bar barrier is mid-range rather than
optimal. The direction agrees with the paper's "wide barriers beat next-bar labeling" finding —
what disagrees is the magnitude, because a 1.2% taker rate is twelve times the 0.1% that paper
priced.

The two cells above 1.0 are both **TON-USD, at n=17 and n=16** — a sixth of the admission floor.
They are maxima of five draws on the thinnest asset in the universe and are not evidence of
anything. **The intersection of n ≥ 100 and PF > 1.0 is empty across all 192 trials.**

## What this settles about the source

The paper's method is CUSUM sampling plus wide triple barriers. Both halves are now implemented
on keel's cost structure and measured on the same universe:

* the **entry** half has essentially no gross edge (median 0.925 at zero cost);
* the **exit** half is a real improvement (+0.033 gross, 17 of 24) that friction consumes entirely.

The paper is not wrong about its own venue. At 0.1% per leg its round trip is 0.2% and a +0.033
gross improvement is worth keeping. At 2.5% it is not. **This is the clearest measurement in the
series of the difference between a result and a result at a price.**

## Honesty

**Selection bias.** Arm A is one pre-declared configuration and carries none. Arm B's per-asset
best is a maximum of five draws — the best cell overall is TON-USD at 48 bars, PF 1.270, n=17,
and quoting it as anything but an artefact would be exactly the error this section exists to
prevent.

**Validation.** Screening result only: no walk-forward, no out-of-sample split, no CSCV/PBO
(`series_missing`). Same cached candles and ~5-year window as every other document here.
`slippage_pct` held at 0.0005 in every backtest cell, so the zero column is zero *fee*, not zero
cost.

**A unit trap worth recording.** `median_daily_quote_volume` returns a **per-bar** median despite
its name. `slippage_for_quote_volume` is anchored on $500M *daily*, so feeding it the hourly
figure unscaled reports every asset as maximally thin, clamps the universe to the 183.8bp cap and
makes every barrier four times too wide — silently, with no error. `per_product_round_trip` scales
by bars-per-day and a test asserts the consequence.

**Changed nothing.** A document, a driver, a ledger row. No rule row added, nothing promoted, no
config or allowlist touched. `triple_barrier` is registered and untraded.

## Recommended next

1. **Not a barrier sweep.** The horizontal barriers are already friction-sized and the vertical
   one is monotone across a twelvefold range for 0.30 of profit factor — all of it below 1.0.
2. **The source is now fully tested and fully answered.** Both halves are measured; neither
   survives this venue's cost. There is no third half.
3. **The null grows again: 0 of 90 → 0 of 114 → 0 of 138.** The most useful thing this pair of
   documents produced is not a rule but a number: **a better exit bought +0.033 gross and −0.004
   net.** That is what "cost is the binding constraint" means, stated as a measurement.
