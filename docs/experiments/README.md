# Experiment records — index

This directory is keel's experiment log: **pre-registered, reproducible records** of
measurements, feasibility studies, and engine-defect findings. Every document states its own
scope and status up front (in-sample vs out-of-sample, "not a promotion decision", "amends
…"), and no document here changes a shipped parameter by itself — **decisions land in
[`trials-ledger.jsonl`](trials-ledger.jsonl)**, the append-only ledger the promotion gates
count; the docs narrate what was measured and why.

**Why the `.py` files are committed.** A script in this directory is a claim: *the numbers in
the doc beside it reproduce.* The driver is committed next to the document that cites it, so
a reader can re-run the measurement rather than trust the prose. Committed `.jsonl` beside a
driver (e.g. `2026-08-21-rule-family-significance.jsonl`) is that run's recorded output.
Bulk sweep output is **not** committed — it is derived data, regenerated from `keel.db`
(`/docs/experiments/_out/` is gitignored).

Index is **newest first**, by the date each document carries in its filename.

## 2026-08

- [`2026-08-22-trailing-vs-static-exits.md`](2026-08-22-trailing-vs-static-exits.md) — Does
  the #442 ratchet-only exit policy (ATR trail / break-even roll) help `pullback_continuation`
  or `rsi_meanrev` at the 120 bp fee? No — and the trailing arm is clearly worse; the knobs
  ship default-OFF · driver `2026-08-22-trailing-vs-static-exits.py`
- [`2026-08-22-optuna-parameter-study.md`](2026-08-22-optuna-parameter-study.md) — Optuna
  TPE parameter study (60 seeded trials per cell, 3 families × 3 products) producing tuning
  *candidates for the gauntlet, never auto-tuned lives* (#476) · driver
  `2026-08-22-optuna-parameter-study.py`
- [`2026-08-21-rule-family-significance.md`](2026-08-21-rule-family-significance.md) — Is a
  rule family's edge distinguishable from zero at the fee actually paid? 180 pre-registered
  cells (3 families × 30 products × 2 fee regimes) plus pooled rows (#475) · driver
  `2026-08-21-rule-family-significance.py`
- [`2026-08-17-honest-cost-restatement-and-dca-ablation.md`](2026-08-17-honest-cost-restatement-and-dca-ablation.md)
  — Two measurements enabled by Phase 9: the first `keel simulate` re-run under per-product
  slippage, and the first DCA-family measurement in a fee-explicit harness
- [`2026-08-13-restated-under-a-production-faithful-engine.md`](2026-08-13-restated-under-a-production-faithful-engine.md)
  — Two engine defects invisible to 2,712 passing tests, and what the 08-12 conclusions
  become without them · drivers `2026-08-13-restated-intersection.py`,
  `2026-08-13-restated-rsi-scale.py`
- [`2026-08-12-shipped-defaults-intersection.md`](2026-08-12-shipped-defaults-intersection.md)
  — Three rules, 24 assets, zero free parameters: the viable intersection is empty, and one
  rule fails for the opposite reason first recorded ⚠️ *amended by the 08-13 restatement* ·
  driver `2026-08-12-shipped-defaults-intersection.py`
- [`2026-08-12-rsi-meanrev-scale-vs-selectivity.md`](2026-08-12-rsi-meanrev-scale-vs-selectivity.md)
  — `rsi_meanrev`'s edge is selectivity, not alpha — and the search for it found a simulator
  defect ⚠️ *amended by the 08-13 restatement* · driver
  `2026-08-12-rsi-meanrev-scale-vs-selectivity.py`
- [`2026-08-12-fee-curve-and-rsi-meanrev.md`](2026-08-12-fee-curve-and-rsi-meanrev.md) — At
  zero fee `turtle_breakout` makes money and `rsi_meanrev` does not: two failure modes that
  were one inference away from being pooled · drivers
  `2026-08-12-fee-curve-and-rsi-meanrev-diag.py`,
  `2026-08-12-fee-curve-and-rsi-meanrev-sweep.py`
- [`2026-08-11-round-number-scale.md`](2026-08-11-round-number-scale.md) — `is_round_number`
  had no sense of scale: the correctness fix, and what it costs live scoring · driver
  `2026-08-11-round-number-scale.py`
- [`2026-08-11-hourly-param-sweep-turtle-breakout.md`](2026-08-11-hourly-param-sweep-turtle-breakout.md)
  — Re-tuning `turtle_breakout` to the hourly clock buys 51% and still loses on everything:
  0 of 144 parameter sets · driver `2026-08-11-hourly-param-sweep-turtle-breakout.py`
- [`2026-08-11-hourly-backtest-turtle-breakout.md`](2026-08-11-hourly-backtest-turtle-breakout.md)
  — `turtle_breakout` clears `min_trades=100` on hourly bars — and loses on all 19 products
  tested
- [`2026-08-09-tradenation-feasibility.md`](2026-08-09-tradenation-feasibility.md) — Can keel
  trade through Trade Nation? §28.1 excludes CFDs at the root, which is what Trade Nation is
- [`2026-08-09-quantcrawler-teardown.md`](2026-08-09-quantcrawler-teardown.md) — What
  transfers to keel from QuantCrawler's product, strategy and GTM pages — near-term, and
  later if keel becomes a SaaS
- [`2026-08-09-equities-feasibility.md`](2026-08-09-equities-feasibility.md) — Can keel trade
  US stocks, from Coinbase or from anyone else? §71.6's screening axis: what an instrument
  legally *represents*
- [`2026-08-09-cts-factor-collinearity.md`](2026-08-09-cts-factor-collinearity.md) — The
  suspected momentum cluster in the CTS factors is not there — and the real defect found on
  the way is a different one · driver `2026-08-09-cts-factor-collinearity.py`
- [`2026-08-09-ctrader-open-api-feasibility.md`](2026-08-09-ctrader-open-api-feasibility.md)
  — Can keel trade through cTrader's Open API? Retail "spot" FX that perpetually rolls is
  not spot settlement (§56.1 as corrected by §66.3)
- [`2026-08-08-between-family-independence.md`](2026-08-08-between-family-independence.md) —
  The §80.16 between-family independence harness exists, and it is calibrated · driver
  `2026-08-08-between-family-independence.py`
- [`2026-08-07-unvalidated-skip-set-reassessment.md`](2026-08-07-unvalidated-skip-set-reassessment.md)
  — Re-assessing the "SOL/LTC/LINK are the unvalidated skip set" claim carried in a
  live-money config: a documentation correction plus one measurement
- [`2026-08-05-coinbase-asset-class-feasibility.md`](2026-08-05-coinbase-asset-class-feasibility.md)
  — Coinbase's new asset classes (futures, equities): what keel can actually trade, verified
  by execution against the live config · driver `2026-08-05-coinbase-asset-class-probe.py`

## 2026-07

- [`2026-07-20-yang-zhang-efficiency.md`](2026-07-20-yang-zhang-efficiency.md) — Yang–Zhang
  volatility on 24/7 crypto: the 8× efficiency gain does not replicate — keep close-to-close
  and ATR (§79.9)
- [`2026-07-20-trials-backfill.md`](2026-07-20-trials-backfill.md) — Reconstructing the first
  39 rows of `trials-ledger.jsonl`, every backfilled row marked `series_missing: true`
- [`2026-07-20-paper-trading-wiring.md`](2026-07-20-paper-trading-wiring.md) — Paper trading:
  it never existed, and after wiring it still cannot start — the honest bootstrap finding
- [`2026-07-20-multi-slot-sim-and-ensemble-retest.md`](2026-07-20-multi-slot-sim-and-ensemble-retest.md)
  — Concurrent rule slots in the simulator: the harness limitation was real and fixed, and
  the ensemble rejection was NOT an artifact
- [`2026-07-20-minbtl-sizing.md`](2026-07-20-minbtl-sizing.md) — MinBTL: how much evidence
  the Turtle actually needs before its track record means anything (§78.1–§78.3, §73.2)
- [`2026-07-20-income-purification.md`](2026-07-20-income-purification.md) — The §65.9
  purification obligation, run against the real imported history: report-only, what it found
- [`2026-07-20-horizon-independence.md`](2026-07-20-horizon-independence.md) — The horizon
  ladder does NOT add independent evidence: measured cross-horizon P&L correlation 0.508 vs
  the 0.22 benchmark
- [`2026-07-20-guards-and-strategy-review.md`](2026-07-20-guards-and-strategy-review.md) —
  Guards and strategy review against KB sources 58–74, checked against the code (not the
  KB's description of it), with a ranked change list
- [`2026-07-20-first-pbo-run.md`](2026-07-20-first-pbo-run.md) — First PBO/CSCV run over the
  entry-lookback grid: in-sample diagnostic, all columns `diagnostic_only`
- [`2026-07-20-exit-lookback-ratio.md`](2026-07-20-exit-lookback-ratio.md) — §79.6's
  monotone prediction for `exit_lookback` does NOT replicate: no change, stays at 20
- [`2026-07-20-candidate-universe.md`](2026-07-20-candidate-universe.md) — 936 products
  screened, 7 viable: the expansion thesis holds, discovery only, nothing admitted
- [`2026-07-20-allowlist-screen-first-run.md`](2026-07-20-allowlist-screen-first-run.md) —
  The halal admission screen's first run rejects our own allowlist: gate built and run,
  nothing attested
- [`2026-07-20-adx-ablation-and-random-entry-control.md`](2026-07-20-adx-ablation-and-random-entry-control.md)
  — ADX ablation plus a random-entry control arm, motivated by Katz & McCormick's own
  out-of-sample finding against ADX gates (§58.2)
