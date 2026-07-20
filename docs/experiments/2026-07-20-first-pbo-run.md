# First PBO/CSCV run — entry-lookback grid, 2026-07-20

**Status:** in-sample diagnostic over cached daily history. **Not a promotion decision.**
**Ledger session:** `pbo-grid-entry-lookback-2026-07-20` (12 columns, all `diagnostic_only`)
**Spec:** `docs/superpowers/specs/2026-07-20-trials-ledger-pbo-design.md`

## Method

Pre-declared candidate grid, frozen before the run (§78.5), bounded on both sides by the KB
(§79.15 floor ~50–100d, §79.5 ceiling ~150–250d, §74.2 150–200, §58.6 80–95):

```
entry_lookback ∈ {20, 30, 40, 50, 60, 70, 80, 100, 120, 150, 180, 200}
```

Everything else held at the shipped values (`exit_lookback=20`, ADX(14)>25, ATR(20) 2N stop).
Assets BTC/ETH/PAXG, real cached daily candles from `keel.db`. Each column is one
configuration's per-bar P&L over the common daily index — a bar's value is the sum of net P&L
of trades that **closed** on it, 0 otherwise.

`S = 16`, `T = 1,819 → 1,808` (11 oldest bars dropped, 113/block ≈ quarterly), `C(16,8) =
12,870` combinations, Sortino metric. **Runtime 11 seconds** — the block-aggregate
decomposition doing its job.

Trade counts fall monotonically with lookback, 37 at 20d down to 14 at 200d, confirming §73's
warning that a longer channel worsens the sample-size problem.

## Result

| statistic | value |
|---|---:|
| **PBO (φ)** | **0.8812** |
| **Degradation slope** | **−0.0006** |
| Prob[OOS < 0] | 0.2420 |
| Stochastic dominance | 1st-order **False**, 2nd-order **False** |
| **G4** | **PASS** |

## Reading it

### 1. G4 passes — and a bare 0.05 gate would have failed it

This is the plateau case §78.7 limitation 4 describes, arriving on the first real run.
PBO 0.88 is very high, but the degradation slope is **−0.0006 — essentially flat**, three
orders of magnitude away from the −0.5 floor and nowhere near §78.8's overfit calibration of
−0.75. High PBO with a flat OOS scatter is the *good* shape.

The mechanism is exactly the one the paper names: twelve adjacent lookbacks on the same rule
family and the same three assets are **near-identical configurations**, so no one of them is
reliably best out-of-sample and φ goes high **by construction**. §54.10/§73.13 tell us to
*prefer* a broad plateau; a scalar gate would have punished us for having one.

⚠️ **This is not evidence of edge.** G4 passing means "the selection procedure does not look
overfit," not "the strategy works." The Turtle still fails G2 on sample size (31 < 100).

### 2. The dominance test fails — and that is the real finding

Both first- and second-order stochastic dominance are **False**. §78.8 is blunt about what
that means:

> *"Should that not be the case, it would present strong evidence that strategy selection
> optimization does not provide consistently better OOS results than a random strategy
> selection."*

⇒ **Choosing the entry lookback by comparing our own backtests is no better than picking one
at random.** This is §58.11's random-entry-null question lifted from entries to the selection
process itself — the question §78.8 called the most under-rated item in the source, and the
first time this project has been able to ask it.

It converges with the flat slope on one story: **across 20–200 days there is no recoverable
signal in the choice of entry lookback.** The differences between these twelve configurations
are noise.

### 3. What follows for the queued lookback work

The KB already instructed (memory, 2026-07-20) that the `donchian_entry_n` work be treated as
an **`a_priori` re-derivation scored on MinBTL, not a sweep-and-pick scored on returns**. This
run supplies the empirical backing that instruction previously lacked: sweep-and-pick here
demonstrably does not beat random selection, so spending trials budget on it would buy nothing.

⛔ **Do not respond to this by searching the grid for a better lookback.** That is the
Strathern misuse (§78.7), and `PBOResult` structurally refuses to tell you which column won.

## Re-verified on repaired data (same day)

This run was computed over daily series carrying 6 internal gaps per major asset, which
`keel fetch --repair-gaps` subsequently filled (1,819 → 1,825 bars). The grid was re-run on the
repaired data as a robustness check — ledger session `pbo-grid-repaired-2026-07-20`:

| | original | repaired |
|---|---:|---:|
| PBO (φ) | 0.8812 | **0.8926** |
| Degradation slope | −0.0006 | **−0.0001** |
| Prob[OOS < 0] | 0.2420 | **0.2423** |
| Dominance 1st / 2nd | False / False | **False / False** |
| G4 | PASS | **PASS** |
| rows used / dropped | 1808 / 11 | 1824 / 4 |

**Every conclusion below survives unchanged.** The missing bars were ~0.33% of the series and
moved nothing that matters. Recorded because the earlier version of this document flagged the
gaps as an untested assumption — this is that assumption being tested rather than waved through.

## Caveats

- **In-sample, one axis.** Only `entry_lookback` varies; interactions with `exit_lookback`,
  ADX and the stop are untested. §79.6 notes `exit_lookback` has never been tested at all.
- **N = 12** clears §78.6's `N ≫ 10` only barely, so φ's granularity is coarse.
- **Mostly-cash days.** Running on daily bars puts zero-return days into the Sortino
  denominator (§54.22/§73.4). §78.6 acknowledges this is the cost of using bars rather than
  trades; the trade series (14–37 per config) is far too short for CSCV.
- **PBO is orthogonal to backtest correctness** (§78.7 limitation 3) — it says nothing about
  look-ahead bias or fee realism.
- **Correlated columns.** These twelve columns are not independent evidence; §78.2's `ρ̂`
  correction and MinBTL reporting (§78.13 items 1–3) are not built yet, so **no MinBTL or DSR
  number should be quoted from this run.**
