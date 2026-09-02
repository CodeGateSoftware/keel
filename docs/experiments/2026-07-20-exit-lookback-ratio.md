# `exit_lookback` ratio test — §79.6's monotone prediction does NOT replicate

> **Cost note (added 2026-09-02).** The figures below are priced at the flat 5bp
> slippage floor. [the per-product restatement](2026-09-01-per-product-slippage-restatement.md) later measured that **no
> asset in keel's universe reaches that floor** — the range is 1.1× to 36.8× — so every
> profit factor here is optimistic by roughly 0.09 at the median. **The verdict is
> unaffected:** the correction only ever moves a number *down*, and every result here was
> already negative. Nothing on this page has been rewritten; records are appended to, not
> revised.

**Date:** 2026-07-20
**Status:** in-sample sensitivity characterisation on cached daily history. **Not a promotion decision.**
**Ledger session:** `exit-lookback-2026-07-20` (5 diagnostic columns + 1 decision row)
**Verdict:** ⛔ **No change. `exit_lookback` stays at the shipped 20.**

## What was pre-declared, before running

Frozen candidate set (§78.5): `exit_lookback ∈ {10, 20, 30, 40, 60}` with `entry_lookback` fixed
at the shipped 40 — ratios 0.25 / 0.50 / 0.75 / 1.00 / 1.50. Everything else shipped (ADX(14)>25,
ATR(20) 2N stop, fee 0.6%, slippage 0.05%).

**Hypothesis (§79.6, Clare Table 4A):** performance is **monotone increasing in the exit/entry
ratio** — their curve runs 0.25 → 0.14, 0.4 → 0.29, 0.6 → 0.39, 0.8 → 0.52, 1.0 → 0.59 (Sharpe).
Our shipped 40/20 sits at ratio 0.50, inside their losing region.

**Counter-hypothesis (§27 / §54.14, canonical Turtle):** the short exit is *deliberate* — it
protects the fat-tailed downside a long channel rides through.

**Prediction recorded in advance:** since PBO already measured the *entry*-lookback axis as
signal-free, the exit axis was expected to be signal-free too.

**Binding decision rule:** report per-asset first (pooled is biased upward without fixed effects,
§79.11); adopt only if the monotone direction replicates per-asset **and** CSCV stochastic
dominance says selection beats random; if our data *contradicts* the direction, keep 20 and record
the null.

## Result

Profit factor by exit/entry ratio, **per asset** (pooled deliberately not headlined):

| exit | ratio | BTC PF | ETH PF | PAXG PF | BTC hold (d) |
|---:|---:|---:|---:|---:|---:|
| 10 | 0.25 | 1.82 | 1.11 | 3.25 | 23.1 |
| **20** | **0.50** | **1.61** | **1.21** | **3.25** | **32.2** |
| 30 | 0.75 | **1.88** | 1.11 | 3.25 | 43.3 |
| 40 | 1.00 | 1.64 | 1.11 | 3.25 | 46.0 |
| 60 | 1.50 | 1.49 | 1.28 | 3.25 | 51.5 |

**The monotone prediction does not replicate.**

- **BTC is non-monotone and peaks in the middle** (0.75), and the *longest* exit tested is the
  *worst* of the five — the opposite of the source's direction at the top of the range.
- **ETH is flat** (1.11–1.28 across a 6× change in exit length). No signal.
- **PAXG is exactly invariant** — 3.25 at every setting. Diagnosed rather than accepted: its 4
  trades all exit via stop or target, so the exit channel never binds. **PAXG contributes zero
  information to this question**, which means this test effectively runs on two assets, not three.
- **The parameter does work mechanically** — average hold rises monotonically 23 → 51 days. The
  knob moves; it just does not move the outcome.

### CSCV diagnostic

| statistic | value |
|---|---:|
| PBO (φ) | 0.9249 |
| Degradation slope | −0.1899 |
| Prob[OOS < 0] | 0.2982 |
| Stochastic dominance | 1st **False**, 2nd **False** |
| G4 | **PASS** |

⚠️ **N = 5 columns is below §78.6's `N ≫ 10`**, so φ is coarse here and should not be compared
closely against the entry-axis run's 0.89. The dominance result is the usable part.

**Dominance fails in both orders for the second axis running.** Selecting an exit lookback by
comparing our own backtests is no better than picking one at random.

## Decision

**Rule D fires: our data contradicts the external prior, so keep the shipped 20 and record the
null.** No parameter changes. One ledger row (`exit-lookback-decision`, `threshold_nudge`,
`rejected`, `fitted`) records that a change was considered against our own backtests and declined —
that consultation is itself a trial (§73.10), so `N_decisions` goes 29 → 30.

⛔ **The argmax of this grid (BTC at ratio 0.75) must not be adopted.** It is a 12-trade sample,
it is not corroborated by ETH, and dominance says the ranking carries no information. Adopting it
would be exactly the sweep-and-pick §73/§78 exist to prevent.

## What this does and does not settle

**Does not settle the §79.6 vs §27/§54.14 tension.** The KB genuinely disagrees with itself about
whether a short exit is a mistake or a deliberate fat-tail protection. This run does not resolve
that — it only declines to support §79.6 *on our data*. The canonical asymmetric exit stands by
default, unchanged, not vindicated.

**Does add a third data point to a hardening pattern.** Entry lookback: signal-free. ADX gate:
gate-ON won but inside the noise band. Exit lookback: signal-free. Three consecutive attempts to
find a parameter that matters have come back negative, and each was measured rather than assumed.
That is consistent with §79.13's consolation (with 31 years and 55 futures, 47 of 55 assets fail
`t = 1.65`) and with the standing conclusion that **at ~6 trades/year this rule cannot be tuned
into significance** — the binding constraint is evidence, not parameters.

## Caveats

- In-sample, one axis, `entry_lookback` held fixed. §79.6 explicitly asks for a *joint* sweep with
  the stop (§58.12/§79.7); that was not run, and a joint grid would cost materially more budget.
- Effectively two assets (PAXG invariant), 12–14 trades each. Differences of PF 1.49–1.88 on that
  sample are well inside noise.
- Costs are modelled at a flat 0.6% fee + 0.05% slippage. §79.4's fee argument for longer holds is
  real but small here: trade count barely moves (14 → 12).
- No MinBTL or DSR number is quoted, because §78.13 items 1–3, 6 and 7 are still unbuilt.
