# Accumulation policy after fees: no arm beats static DCA

**Date:** 2026-09-27
**Issue:** #831 · **Design:** [`2026-09-27-accumulation-policy-design.md`](../superpowers/specs/2026-09-27-accumulation-policy-design.md)
(revision 2, approved as #834)
**Pre-registration:** the driver's docstring,
[`2026-09-27-accumulation-policy.py`](2026-09-27-accumulation-policy.py), committed and pushed
as `4113ed0` (2026-09-27 02:04 UTC) **before the run**. No line of the driver has changed
since.
**Harness:** `keel/sim/accumulation_policy.py` (47 tests, each seen failing first; 15 mutants
killed). Arm A reproduces `sim.benchmark.dca_into_allowlist` to the cent under zero fees on
gapless candles. It fills at the next open where the benchmark fills at the close, as the
design requires, and a second test pins that difference.
**Data:** [`2026-09-27-accumulation-policy.jsonl`](2026-09-27-accumulation-policy.jsonl).
**Ledger:** 16 `ablation` rows (4 arms × 2 runs × 2 fee models), `a_priori`,
`diagnostic_only`, session `accumulation-policy-2026-09-27`.

## 1. Setup (as pre-registered)

- **Deposits:** $500 on the first UTC day of every month, identical for every arm. Idle cash
  earns nothing.
- **Fills:** decided on the completed daily bar, filled at the next open.
- **Fees, allowance-aware (headline):** buys fee-free up to $500 a month; buy notional beyond
  that pays the 1.20% taker, and sells always pay it. Per-product slippage on every leg.
  The flat-taker model charges 1.20% on every leg.
- **Runs:**
  - **Primary:** 5 years, 2021-09-28 → 2026-09-25, the 7 non-PAXG assets with weights
    renormalised. 1,824 days, 61 deposits.
  - **Secondary:** about 1.4 years, 2025-05-08 → 2026-09-25, all 8 assets including PAXG.
    506 days, 17 deposits.
- **Arms:**
  - **A:** static DCA.
  - **B:** bounded value averaging, buy-only, C_max = 3.
  - **C:** deposit steered to underweights by shortfall.
  - **D:** DCA plus selective trimming, band `max(0.15·w, 0.015)`.
- **Inference:** a joint stationary bootstrap, 2,000 paths at mean blocks of 20 and 60 days,
  seed 831.
- **Rule D6:** an arm is "better" only if **P(ΔSortino > 0) ≥ 0.95 and median Δmax-drawdown
  ≤ 0 under both block lengths**.

## 2. Result: headline (primary run, allowance-aware fees)

| Arm | Terminal value | TWR | Sortino | Max DD | Fees (buy / sell) | Cash share | P(ΔSortino > 0) b20 / b60 | Median ΔmaxDD b20 / b60 | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| A | $46,629 | +23.7% | 0.523 | 79.5% | $0 / $0 | 0.3% | — | — | baseline |
| B | $48,840 | +32.6% | 0.542 | 78.5% | $33 / $0 | 8.3% | 0.583 / 0.599 | −2.1 / −2.3 pts | not better |
| C | $47,967 | +31.6% | 0.564 | 79.1% | $0 / $0 | 0.3% | 0.365 / 0.407 | +0.6 / +0.4 pts | not better |
| D | $52,919 | +46.0% | 0.611 | 79.3% | $462 / $468 | 0.3% | 0.363 / 0.434 | +0.3 / +0.1 pts | not better |

Deposits total $30,500 in every arm.

**Verdict under D6: no arm is better than static DCA after fees.** It is the same in all four
combinations of run and fee model. No arm reaches even P = 0.62, against a bar of 0.95.

## 3. The other runs

| Run / fees | A | B | C | D | Verdict |
|---|---|---|---|---|---|
| Primary / flat taker | $46,069 · Sortino 0.494 | $48,387 · 0.514 | $47,389 · 0.536 | $52,284 · 0.582 | none better |
| Secondary / allowance | $8,834 · 0.240 | $8,996 · 0.294 | $8,992 · 0.275 | $8,974 · 0.257 | none better |
| Secondary / flat taker | $8,728 · 0.136 | $8,884 · 0.190 | $8,885 · 0.174 | $8,867 · 0.157 | none better |

Each cell is terminal value on the historical path · Sortino of the time-weighted index.
Bootstrap probabilities for every combination are in the dataset. The highest anywhere is B on
the primary run with flat fees: 0.6125 at 60-day blocks.

## 4. What it says

- **The historical path flatters band trimming, and the bootstrap takes it back.** On the one
  path that happened, arm D ends $6,290 (13.5%) ahead of A, with the best Sortino. Across
  2,000 resampled paths, D improves on A's Sortino only **36–43% of the time**. Its lead
  belongs to the ordering of this one path, not to a property the resampled paths share. Why
  this path favoured it was not examined here. That is exactly the result a single backtest
  would have reported as a win, and this record declines to.
- **Value averaging is the most consistent arm, and still nowhere near the bar.** B is ahead
  of A in about 58–61% of paths at both block lengths, and it lowers the median drawdown by
  about 2 points. It pays for that with an 8% average cash share, the cash drag the design
  predicted. It is modest and consistent, but not robust.
- **Steering contributions (C) is effectively A.** It costs nothing extra and changes little,
  as predicted.
- **Fees are not the story at these amounts.** Flat taker lowers each arm's terminal value
  by $453–$635 over five years. The ranking and the verdict don't change.
- **All four arms lived through the same crash.** The maximum drawdown of the time-weighted
  index is 78–80% in every arm on the primary run (2022). No deployment policy tested here
  changes the fact that the portfolio is almost entirely correlated crypto.

## 5. Prediction versus outcome

| Pre-registered expectation | Outcome |
|---|---|
| C ≈ A, a small effect | **Held.** |
| B trails in rising markets, leads after drawdowns, no robust Sortino edge | **Held**, for no robust edge. B also finished ahead on both historical paths. |
| D is hurt on the 5-year PAXG-free run (sell fees, correlated assets) | **Wrong on the historical path** (D finished first) and **right under the rule**: the bootstrap gives D no robust edge. |
| D has its best case on the short run with PAXG | **Wrong.** On the secondary run D was the weakest of the three alternatives. PAXG's slippage is about 100 bp per leg, the highest in the universe, so trimming into and out of it is expensive, and 1.4 years is one regime. |

The KB §51 prior ("fixed-weight rebalancing = wrong paradigm") is **not contradicted**: the
one leg that was supposed to rescue rebalancing did not.

## 6. What follows

Under the pre-registered rule, nothing changes. The live weekly $50 BTC DCA (rule 6) remains
the accumulation policy. Nothing here is a reason to add rebalancing sells, which would also
need the `sell_only_on_rule` decision the design flagged. If the question is re-opened, the
data points to value averaging's drawdown reduction as the only effect with a consistent
sign. A re-test should be pre-registered on drawdown, not Sortino, and counted against the 3
trials already spent here.
