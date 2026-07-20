# Concurrent RULE slots in the sim — and the ensemble rejection was NOT an artifact

**Date:** 2026-07-20
**Status:** harness correctness fix + in-sample characterisation. **No parameter or rule changed.**
**Verdict:** the harness limitation was real and is fixed; **the hypothesis it was expected to
vindicate is refuted.**

## The harness fix

`keel/sim/portfolio_sim.py` enforced *"only one RULE position per asset at a time"*, while the
LIVE executor has been able to hold concurrent tranches since PR #96 shipped the per-tranche
`positions` table. **The harness could not represent a behaviour the executor could already
perform.**

Positions are now keyed by `(asset, rule_name)` in both `SimAccount` and the sim loop — one
position per *rule* per asset, so distinct rules get distinct slots. Deliberately **not**
multiple positions from the same rule; that is pyramiding (§26.1), a separate feature with its
own exposure-rail implications.

**The rail-weakening risk, pinned by a test.** `_position_notional` was keyed by asset alone and
*replaced* on each open. Left that way, a second concurrent position would have overwritten the
first's notional and the per-asset concentration cap would have seen only the newer one — two
$2k positions reading as $2k of exposure. It is now slot-keyed and `_asset_notional` sums across
slots.

**Regression proof.** A single-rule run over the real cached 5-year series produces a
**byte-identical trade fingerprint** before and after the change: 18 trades, final equity
$30,970.61, same trade sequence. The default slot `""` preserves the historical behaviour for
any caller that does not pass one.

## The retest — and the correction

Project memory recorded this, after the ensemble was rejected on a +$393 vs +$431 comparison:

> **THIS REFRAMES A RECORDED RESULT: the S1+S2 ensemble rejection is an ARTIFACT OF LINE 600, not
> a finding about the ensemble.** It was judged through a harness that could not represent it.

That was a reasonable inference. It is now measurable, and **it is wrong.**

| configuration | trades | net | return | max DD |
|---|---:|---:|---:|---:|
| single 40/20 (shipped) | 18 | **$470.61** | **1.54%** | **2.48%** |
| S1(20/10 filtered) + S2(55/20) | 21 | $407.94 | 1.34% | 3.17% |

**The ensemble is still worse — on both return and drawdown — now that it can compound.** It is
worse on the risk axis by more than it is on the return axis, which is the axis this project
judges on.

And it barely buys deployment: **18 → 21 trades**, not the near-doubling the "competing for one
slot" story implied. If the slot cap had been the binding constraint, trade count should have
risen far more than 17%.

⇒ **The recorded reframing is retracted. The ensemble rejection was a finding about the
ensemble, not an artifact of the harness.**

## What the fix is still worth

The lift was worth doing regardless, for three reasons that do not depend on the ensemble:

1. **It is a correctness fix.** A harness that cannot represent what the executor does will
   mis-judge anything that relies on the difference — silently, and in an unknown direction.
2. **It unblocks the horizon ladder** (§79.2/§79.3), which is the remaining lever on *independent*
   evidence and the one thing memory identifies as attacking knowability rather than deployment.
3. **It made this correction possible at all.** The previous belief could not have been tested
   without it.

## Caveats

- In-sample, single comparison, no promotion gate. Not a decision about the ensemble beyond
  declining to revive it.
- The ensemble's extra trades are **correlated** (same rule family, same assets), so even had it
  won on returns it would have helped deployment, not knowability (§73.3 wants ~independent
  evidence).
- Idle-span telemetry changes slightly: entry evaluation now runs on bars where a position is
  held, where it was previously skipped. P&L and trades are unaffected — proven by the identical
  fingerprint — but gap-analysis idle spans are not directly comparable across this change.
