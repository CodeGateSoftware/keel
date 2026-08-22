# Rule-family significance: is a family's edge distinguishable from zero at the fee actually paid?

**Issue #475.** Driver: [`2026-08-21-rule-family-significance.py`](2026-08-21-rule-family-significance.py)
(pre-registered in its docstring before the run). Artifact: every number below is a row of
[`2026-08-21-rule-family-significance.jsonl`](2026-08-21-rule-family-significance.jsonl) — 180
cells (3 families x 30 products x 2 fee regimes) plus 6 pooled rows.

**Headline, stated first because it is the point: no shipped family's edge is distinguishable
from zero — in the positive, promotion-relevant direction — in ANY fee regime, pooled over the
full cached history. Outside the fee-free allowance the reconstructions are decisively NEGATIVE.
Inside it they sit at — slightly below — break-even. This is report-only evidence; the tool's
job was to be able to say no, and it says no.**

## Method

- **Reconstruction, not new simulation.** Each family runs through
  `keel.strategy.backtest.backtest` over every product's full cached ONE_HOUR history
  (2021-07-18 to 2026-08-21 UTC; 30 products with >= 2,000 hourly bars, ~44k bars each):
  next-bar-open market fills, 5 bp per-leg slippage in BOTH regimes, fee per leg as below.
  - `turtle_breakout`: the cross-verification §4 hourly profile (`entry_lookback 40,
    adx_period 14, adx_threshold 25, atr_period 20, atr_stop_mult 2, target_rr 6`,
    ONE_HOUR; `exit_lookback` at its default 20).
  - `rsi_meanrev`, `pullback_continuation`: constructor defaults (both ONE_HOUR-native).
  - `dca` is out of scope — no stop, so no win/loss framing (cross-verification §7).
- **Two fee regimes, never a blend.** `outside_allowance_taker` = `backtest.TAKER_FEE_PCT`
  (120 bp per leg); `inside_allowance_fee_free` = 0 — rail 14's fee-free volume allowance,
  which §5 of the cross-verification showed is the profitability boundary. Same trades,
  same fills, re-priced. Slippage stays 5 bp/leg inside the allowance because the allowance
  waives the fee, not the spread.
- **The test** (`keel/research.significance`, new in this PR): break-even `1/(1+b)` from the
  payoff measured off the same net-pnl trades; one-sided `1 - Phi(z)` at alpha 5%;
  standard error `sqrt(p_be(1-p_be)/n_eff)`.
- **n_eff, never raw n (#427).** Signals herd (k = 8.43, ICC 0.212, DEFF 2.57516), so every
  standard error is formed on `n_eff = n / 2.57516`. The 7,152 pooled turtle trades are
  2,777 effective observations, not 7,152.
- Read-only against the deployment cache (`file:...?mode=ro`); the only writes are the JSONL
  artifact and stdout.

## Results — pooled per family (the issue's question)

| family | regime | n (pooled) | n_eff | payoff b | break-even | win rate | edge | z | p | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| turtle_breakout | outside (120 bp) | 7,152 | 2,777.3 | 0.784 | 0.5606 | 0.1615 | **−0.3991** | −42.38 | 1.0000 | not_distinguishable |
| turtle_breakout | inside (0 bp) | 7,152 | 2,777.3 | 2.616 | 0.2765 | 0.2482 | **−0.0283** | −3.34 | 0.9996 | not_distinguishable |
| rsi_meanrev | outside (120 bp) | 1,201 | 466.4 | 0.208 | 0.8276 | 0.2714 | **−0.5562** | −31.80 | 1.0000 | not_distinguishable |
| rsi_meanrev | inside (0 bp) | 1,201 | 466.4 | 1.493 | 0.4011 | 0.3172 | **−0.0839** | −3.70 | 0.9999 | not_distinguishable |
| pullback_continuation | outside (120 bp) | 3,261 | 1,266.3 | 0.045 | 0.9569 | 0.1435 | **−0.8134** | −142.52 | 1.0000 | not_distinguishable |
| pullback_continuation | inside (0 bp) | 3,261 | 1,266.3 | 1.468 | 0.4052 | 0.3382 | **−0.0670** | −4.85 | 1.0000 | not_distinguishable |

Every product produced trades for every family — no `insufficient_n` cell in the run.

Reading the table honestly, in two halves:

- **Outside the allowance, the taker fee is the whole story — again.** At 120 bp per leg the
  payoff `b` collapses (turtle 2.62 -> 0.78, rsi 1.49 -> 0.21, pullback 1.47 -> 0.05), the
  break-even win rate explodes past 56-96%, and the observed win rates (14-27%) are tens of
  standard errors BELOW it. The one-sided positive test refuses; the two-sided reading is
  worse. This is `docs/launch.md`'s 0-of-90/0-of-82 result reproduced under a significance
  framework with the sample size counted honestly.
- **Inside the allowance, break-even — with the spread's bite visible.** Fee-free, the pooled
  edges are −2.8, −8.4 and −6.7 points: not distinguishable from zero in the positive
  direction, and in fact 3.3-4.9 standard errors below break-even. That small negative is
  the 5 bp/leg slippage retained in this regime: the cross-verification's §5 table put
  turtle at +0.02% with ψ = 0 and −0.58% at ψ = 10 bp, and 2 x 5 bp sits inside that range.
  The shape survives the whole spread range: **indistinguishable from break-even inside the
  fee-free allowance, decisively negative outside it.** The 95% one-sided lower bounds
  (−4.2, −12.1, −9.0 points) also say what a promotion gate may rely on: no family's edge is
  credibly above zero anywhere.

### The three per-cell flags — read, not celebrated

3 of 180 cells came back `distinguishable`, all `turtle_breakout` inside the allowance, all
thin-history/thin-liquidity assets: ZEC-USD (n=272, edge +7.3 pts, p=0.030), WLD-USD (n=58,
+13.6 pts, p=0.038), PAXG-USD (n=74, +13.4 pts, p=0.045). Each z (1.70-1.88) is below even
the 80%-power boundary of 2.49, all three p-values are marginal, and 180 comparisons at
alpha 5% expect ~9 false positives under a global null — while the same regime's POOLED row
is −2.8 points. WLD and TON are the exact assets the #259 slippage work documented as
thin-book "apparent outliers". These are noise wearing significance's clothes; the artifact
keeps them so a reader can check that reading.

## Validation

Reproduced by `tests/research/test_significance.py` (19 tests) and the existing
`tests/research/test_throughput.py`:

- `design_effect()` = **2.57516** (1 + (8.43−1) x 0.212), the published "DEFF 2.58".
- `n_eff(100)` = **38.83 -> "39"** effective observations — never raw n (#427).
- `detectable_edge(39)` = **0.199** — the published "a 100-trade pool can only detect a
  20-point edge".
- The detection-boundary test pins the relationship end-to-end: at break-even 1/2 (the
  maximal-variance geometry the constant `(z_0.95+z_0.80)^2/4 = 1.5464` assumes), n = 100
  pooled and an edge of exactly 20 points gives z = 2.4926 = z_0.95 + z_0.80 to within 0.01,
  p = 0.0063. One correction to a draft of this experiment's spec, recorded because numbers
  that almost line up are how errors propagate: that boundary is the 80%-POWER boundary, not
  p = 0.05. A one-sided 5% test crosses alpha at 13.2 points on this geometry; 20 points is
  where 80% power arrives. At the operative b = 6 break-even (1/7) the null variance is
  lower, so the same edge is further from the boundary still — the published 20-point figure
  is the conservative case.
- `se_null`/z/p hand-computed against `deflate.normal_cdf`; exact-Decimal payoff/break-even
  fixtures (b=3 -> 1/4, b=4 -> 1/5, b=1.5 -> 2/5); degenerate samples (no wins, no losses,
  empty) answered, never smoothed.

## Provenance

```
uv run python docs/experiments/2026-08-21-rule-family-significance.py --workers 12
# db: /Users/elmehdiaitbrahim/keel/keel.db (read-only), 180 cells + 6 pooled rows, 3,542 s
# artifact: docs/experiments/2026-08-21-rule-family-significance.jsonl
```

Report-only. Nothing here promotes, gates or sizes anything; a `not_distinguishable` verdict
is the honest result, not a tool failure.
