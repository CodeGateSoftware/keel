# Optuna parameter study: candidates for the gauntlet, never auto-tuned lives

**Issue #476.** Driver: [`2026-08-22-optuna-parameter-study.py`](2026-08-22-optuna-parameter-study.py)
(pre-registered in its docstring before the run). Artifact: every number below is a row of
[`2026-08-22-optuna-parameter-study.jsonl`](2026-08-22-optuna-parameter-study.jsonl) — 9 cells
(3 families x 3 products), 60 seeded TPE trials each, every trial's params and train expectancy
recorded.

**Headline, stated first because it is the point: the gate refused every cell. No candidate may
be proposed — not one family, on one product, at the 120 bp taker fee, cleared both gate
conditions. The optimizer could not even find a positive TRAIN-window configuration for turtle
or pullback anywhere; the one positive train cell it did find (rsi on ETH, +221/trade) made
ZERO trades on the window it was not fitted on. This is research-side only: nothing tuned a
live or paper profile, the promotion gauntlet is unchanged, and the three winners were appended
to the trials ledger as `diagnostic_only` with `provenance="fitted"` — because that is what
they are.**

## Method

- **Harness** (`keel/research/tuning.py`, new in this PR): per (family, product), TPE with
  `TPESampler(seed=476)`, 60 trials, `n_startup_trials` at its default, deterministic under
  the seed (pinned by test — two runs reproduce every trial bit-for-bit). The objective is the
  TRAIN-window per-trade expectancy (fee- and slippage-adjusted, exact `Decimal`) at
  `backtest.TAKER_FEE_PCT` (120 bp per leg) + `SLIPPAGE_FLOOR_PCT` (5 bp per leg) — the same
  cost model the #475 significance reconstruction used.
- **Train/held-out before anything else.** The trailing 17,520 ONE_HOUR bars (2 years) per
  product split chronologically 70/30; the optimizer sees only the train window; the held-out
  future re-prices the winner exactly once. Window chosen from measured cost (rsi backtests
  are quadratic in bars: ~14 s over 1y, ~56 s over 2y), keeping the whole 9-cell run inside
  the ~30 min budget — the rsi cells were the binding 1,729 s wall clock.
- **Pinned search spaces**, 4-5 knobs a trader can name a reason for (the harness's
  `SEARCH_SPACES`, integrity-pinned by test against the rule constructors):
  - `turtle_breakout`: entry_lookback 20-60, exit_lookback 10-30, adx_threshold 20-35,
    atr_stop_mult 1.5-3.0, target_rr 3-8 (granularity fixed ONE_HOUR by the driver).
  - `rsi_meanrev`: oversold 15-30, overbought 70-85, atr_mult 1.0-2.5, fixed_rr 1-3,
    rsi_period 10-21.
  - `pullback_continuation`: ema fan 5-12/15-30/40-70 (strictly ordered, clamped
    deterministically), buffer_ticks 0.01-0.05.
  - `granularity` and `product_id` are never searched — fixed by the caller.
- **Products**: the liquid majors BTC-USD, ETH-USD, SOL-USD. No asset picking beyond
  liquidity, stated up front.
- **The gate BEFORE any proposal** (`evaluate_gate`): held-out expectancy > 0 AND
  CSCV/PBO over the study's own per-trial per-trade P&L columns <= 0.5 (s=8; trials with
  < 10 closed trades excluded rather than padded). The harness refuses to emit a
  "PROPOSE as candidate" line in any other state.
- Read-only against the deployment cache (`file:...?mode=ro`); the only writes are the JSONL
  artifact, the append-only trials ledger, and stdout.

## Results — per family winner (best train expectancy across the three products)

Expectancies are USD per 1-unit-notional trade (the engine's fixed sizing), net of the
120 bp/leg fee and 5 bp/leg slippage.

| family | winner | best params | train exp | held-out exp (n) | PBO | verdict |
|---|---|---|---|---|---|---|
| turtle_breakout | SOL-USD | entry 44 / exit 26 / adx 34.5 / stop 1.54 ATR / rr 8 | **−1.34** | −1.82 (14) | 0.70 | **REFUSED** |
| rsi_meanrev | ETH-USD | oversold 15.7 / overbought 80.7 / atr 2.50 / rr 1 / period 18 | **+220.98** | 0.00 (0 closed) | 0.57 | **REFUSED** |
| pullback_continuation | SOL-USD | ema 11/17/40 / buffer 0.0497 | **−4.79** | −2.56 (15) | 0.84 | **REFUSED** |

All nine cells (artifact rows, in full): turtle BTC −1683 → −1705 (PBO 0.81), turtle ETH
−12.4 → −68.1 (PBO 0.79), turtle SOL −1.34 → −1.82 (PBO 0.70); rsi BTC −1576 → −2195
(PBO 0.47, refused on the held-out sign alone), rsi ETH +221 → 0 over ZERO closed trades
(PBO 0.57), rsi SOL 0 → 0 (PBO 0.24, refused: no held-out edge); pullback BTC −2323 → −1742
(PBO 0.97), pullback ETH −76.3 → −50.2 (PBO 0.80), pullback SOL −4.79 → −2.56 (PBO 0.84).

Reading the table honestly:

- **The fee, not the parameters.** On turtle and pullback the optimizer could not find a
  SINGLE configuration with positive in-sample expectancy on any of the three majors — 120
  trials across two families, all negative on the window they were fitted ON. That is #475's
  "not distinguishable from zero at the fee" seen from the optimizer's side: there is nothing
  for the sampler to climb.
- **The one apparent exception is the overfitting signature itself.** rsi on ETH fit its way
  to +221/trade in-sample — and the fitted parameters (oversold 15.7, period 18) are so
  selective that the held-out window produced no closed trade at all. An edge that stops
  existing the moment the data it was not fitted on arrives is not an edge; the gate refused
  it on both conditions.
- **PBO agrees.** Where every trial produced a usable column (turtle, pullback), PBO ran
  0.70-0.97: the in-sample ranking of these configurations is worse than a coin flip at
  predicting the out-of-sample ranking. The rsi cells' PBO is computed on fewer columns
  (9-52 of 60 trials cleared the 10-trade floor) and is correspondingly weaker evidence —
  stated, not hidden; the held-out sign refused those cells regardless.

## Validation

Reproduced by `tests/research/test_tuning.py` (19 tests):

- `SEARCH_SPACES` integrity: families present, bounds well formed, mid-space params construct
  the real rules (catches parameter-name drift).
- `params_from_trial` exact kwargs at lo/mid/hi; unknown family raises; out-of-order EMA
  suggestions clamped strictly ordered, deterministically.
- **Determinism, the acceptance test**: `run_study` twice with the same seed on the same
  candles → identical best_params, best_train_expectancy, gate, and full trial sequence; a
  different seed walks a different path (so the guarantee is not vacuous).
- The gate: held-out sign, `pbo` exactly equal to `cscv.pbo` on the same columns
  (wired, not reimplemented), thin-column skipping, and the refusal when CSCV has no matrix.
- `proposal_verdict` emits "PROPOSE as candidate" only when passed; otherwise
  "no candidate may be proposed" naming every failing condition with its numbers.
- `import keel.research.tuning` pulls no optuna (subprocess check); `run_study` raises a
  `RuntimeError` naming the extra when optuna is absent.

## Provenance

```
uv run python docs/experiments/2026-08-22-optuna-parameter-study.py --workers 12
# db: /Users/elmehdiaitbrahim/keel/keel.db (read-only), 9 cells x 60 trials, seed 476, 1,729 s
# artifact: docs/experiments/2026-08-22-optuna-parameter-study.jsonl
# ledger: 3 diagnostic_only rows (476-optuna-{turtle_breakout,rsi_meanrev,pullback_continuation}),
#   provenance "fitted", chain intact (90 rows total)
```

Research-side only. Results may seed CANDIDATE proposals; nothing auto-tunes a live or paper
profile; the promotion gauntlet is unchanged — and this run proposes nothing.
