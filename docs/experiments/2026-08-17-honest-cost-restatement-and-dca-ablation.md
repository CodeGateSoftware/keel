# 2026-08-17: The honest-cost simulate re-run, and the DCA dip-bonus ablation

> **Cost note (added 2026-09-02).** The figures below are priced at the flat 5bp
> slippage floor. [the per-product restatement](2026-09-01-per-product-slippage-restatement.md) later measured that **no
> asset in keel's universe reaches that floor** — the range is 1.1× to 36.8× — so every
> profit factor here is optimistic by roughly 0.09 at the median. **The verdict is
> unaffected:** the correction only ever moves a number *down*, and every result here was
> already negative. Nothing on this page has been rewritten; records are appended to, not
> revised.

Two measurements, one date, both enabled by Phase 9: the first `keel simulate` run under
per-product slippage (#334, v0.9.0), and the first-ever measurement of the DCA family in a
fee-explicit harness. Recorded together because they answer the two questions Phase 10's
re-measurement step (#339) posed: do the thin-asset outliers survive honest pricing, and
does the one unswept rule family behave as its citation predicts.

## What was measured, on what

- Engine: `keel 0.9.0+eebf12b88292 [release]`, deployed 2026-08-17 (four distributions at
  0.9.0, clean build identity).
- Data: the deployment's `keel.db` candles, post-repair (2026-08-17: every series 0 bars
  behind, all remaining gaps proven absent at the venue).
- Instrument A (simulate): `keel simulate` — the paper book's rules over the 8-asset
  paper universe, edge pass priced at 1.2% taker per leg **plus per-product slippage**
  (`floor × sqrt(anchor / median_daily_quote_volume)`, floor 5bp, cap 50bp, anchor
  $500M/day — an assumption, not a measurement, stated beside every table the run prints).
- Instrument B (ablation): `sim/portfolio_sim.run` directly, `fee_pct=0.012`,
  `slippage_pct=0.0005`, BTC-USD hourly (44,537 bars, 2021-08 → 2026-08), three cells
  differing only in `dip_bonus_pct` (0 / 1 / 2) at constructor defaults
  (`cadence_days=7, budget_usd=50, lookback_days=90`). Metric: terminal DCA-sleeve
  mark-to-market vs deployed (qty × average cost basis) — never a profit factor, because
  the sleeve never closes and DCA is exempt from the promotion gate (§12.6).

## Result A — the honest-cost simulate: verdict unchanged, direction conservative

Verdict: **TRAIN MORE**, on the same gates as the morning's flat-5bp run (the `[default]`
trio is the disabled-DCA artifact, not a measurement; `[trend_follow] n_trades 80 < 100` is
the live gate). The per-asset edge table moved exactly as #334 predicted — **thin assets
got worse, never better**:

| turtle_breakout | PF, flat 5bp (08-17 AM) | PF, per-product (this run) | assumed slippage |
| --- | --- | --- | --- |
| BTC | 1.5216 | 1.5216 | 5.0bp (floor/anchor) |
| ETH | 1.2320 | 1.2284 | 6.2bp |
| SOL | 0.2309 | 0.2285 | 10.7bp |
| ADA | 1.7638 | 1.7087 | 24.1bp |
| XLM | 4.7567 | 4.4435 | 35.7bp |
| PAXG | 1.3152 | 1.3152 | 50.0bp (capped) |

**No flattered outlier survived.** The TON-class result #259 worried about (gross PF 3.751
on n=9 under flat 5bp) cannot print at that price anymore — products without a liquidity
statistic fall back to the floor and are flagged, and the thin end of the measured universe
(XLM at 35.7bp, ADA at 24.1bp, PAXG capped) now pays what its books plausibly demand. The
pooled reading (N=80, PF 1.4703) is below the promotion floor on sample size alone, as it
was. **Comparison caveat: pre-#334 numbers in older experiment documents were produced
under the flat model and are not comparable cell-for-cell with this table; they stand as
annotated records of what the engine of their day printed.**

## Result B — the DCA ablation: the pre-registered expectation was NOT confirmed

Terminal outcomes over the same ~5-year BTC window, `budget_usd=50` weekly, entry fees
1.2% + 5bp per leg, no exit leg ever:

| `dip_bonus_pct` | qty accumulated | deployed (qty × avg basis) | terminal MTM | MTM/deployed |
| --- | --- | --- | --- | --- |
| 0 (plain) | 0.054628 | $2,495.79 | $3,512.63 | **1.40742** |
| 1 | 0.053646 | $2,456.50 | $3,449.50 | **1.40424** |
| 2 | 0.054829 | $2,494.65 | $3,525.55 | **1.41325** |

The citation behind the proposal (bestinterest.blog, BTC 2013–2021, costs not modeled,
not independently replicated) found plain cadence beating every dip-conditioning variant,
monotonically worse at deeper thresholds. **That ordinal prediction does not transfer:**
the three cells sit within 0.65% relative of each other with no monotone direction
(dip-1 slightly under plain, dip-2 slightly over). Two honest readings:

1. **Mechanism mismatch, stated plainly.** The citation's scheme holds cash until a
   threshold dip — it changes WHEN buys happen and bears round-trip-adjacent timing risk.
   `dip_bonus_pct` always buys and only scales size up on dip weeks. The nearest
   expressible analogue was measured, not the cited scheme, and on this window the two
   behave alike.
2. **Power.** One asset, one window, no replication. A ≤0.65% spread in terminal ratio is
   inside path noise for anything this unreplicated; the result is "no detectable effect
   at keel's cost structure", not "an effect of zero".

**Consequence for the friction-sizing thesis (#342): none either way.** DCA's cost
structure (one leg, never exits, ad-valorem fees) is precisely the case the round-trip
argument does not govern, and the ablation was never a test of it. **Consequence for the
operator: no basis to prefer any cell — the live rule stays at `dip_bonus_pct=0`, which is
also its constructor default.** First-ever coverage of the family is now on record; the
proposal's value was the coverage, not the tuning.

## Deployment actions recorded with this run

- The disabled paper DCA twins (rules 19, 20) were re-enabled through the supported CLI
  path — `keel rules enable` (→ candidate, the lifecycle floor) then `keel rules promote
  --force` (the documented bypass for a paper-forward whose backtest can never reach the
  floor; both warnings printed and `rules.promote_forced` events logged). The paper
  pipeline now has an always-firing family alongside the turtles, at $55/week combined
  against $550 synthetic cash — either the operator tops up or the cash caps veto, and
  both are admissible evidence.

## Caveats

- In-sample, single-asset (BTC), single-window; the ablation's cells share one price path.
- The simulate run's dollar account pass and benchmarks still price at the flat fallback;
  the report says so beside both sections (#334's stated asymmetry).
- Per-product slippage is an assumption with recoverable parameters (floor/cap/anchor
  printed with every table); it is not a measured spread.
