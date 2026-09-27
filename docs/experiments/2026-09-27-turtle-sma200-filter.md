# A 200-day SMA filter on the daily `turtle_breakout`: no arm distinguishable from the baseline

**Date:** 2026-09-27
**Issue:** #830. It is evaluated under ADR 0006 (pooled sample, n ≥ 100, descriptive at that floor).
**Pre-registration:** the driver's docstring,
[`2026-09-27-turtle-sma200-filter.py`](2026-09-27-turtle-sma200-filter.py), committed and pushed
as `696bc5b` (2026-09-27 00:39 UTC) **before any arm was run**. The arms, window, statistics
and decision rule below are that docstring's. The only later edits to the driver changed how
figures are stored in the trials ledger (§6), never what was computed.
**Change:** `turtle_breakout` gains an optional `trend_filter` (`off` | `above` | `slope` |
`both`, default `off`), so no existing rule changes behaviour. No rule, config or status is
changed by this record.
**Data:** [`2026-09-27-turtle-sma200-filter.jsonl`](2026-09-27-turtle-sma200-filter.jsonl),
with per-arm and per-product rows.
**Ledger:** four `ablation` rows, `provenance=a_priori`, `decision=diagnostic_only`, session
`turtle-sma200-filter-2026-09-27`, each carrying its per-trade R series.

## 1. Result

The rules are the daily paper account's 19 `turtle_breakout` rows, over the window
2021-09-27 → 2026-09-26, at 1.20% taker plus per-product slippage, in R. Every arm is
evaluated on the **same** window: entries at or after each product's 206th daily bar, the
first bar a 200-day SMA with a 5-bar slope can be computed on.

| Arm | Pooled N | Retained | Win % | E[R] | PF (R) | Edge over break-even | n_eff | Detectable edge | Δ E[R] vs `off`, 95% CI | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| `off` (baseline) | 202 | — | 26.7% | +0.270 | 1.32 | +5.1 pts | 78.4 | 14.0 pts | — | — |
| `above` | 181 | 90% | 25.4% | +0.218 | 1.25 | +4.0 pts | 70.3 | 14.8 pts | [−0.641, +0.540] | no improvement distinguishable |
| `slope` | 121 | 60% | 29.8% | +0.419 | 1.53 | +8.1 pts | 47.0 | 18.1 pts | [−0.515, +0.829] | no improvement distinguishable |
| `both` | 121 | 60% | 28.9% | +0.409 | 1.51 | +7.7 pts | 47.0 | 18.1 pts | [−0.524, +0.824] | no improvement distinguishable |

For reference only: the unrestricted `off` arm, including the first ~205 days, is **220 trades,
25.0%, +0.166 R**. That reproduces the baseline in #830's issue body.

**Pre-registered verdict: no arm is promising.** Every arm clears the 100-trade floor, and no
arm's difference interval excludes zero. Nothing goes to walk-forward, and nothing is proposed
for any rule.

## 2. What the numbers say

- **`slope` and `both` point the hypothesised way.** They cut 40% of entries and move the
  point estimate from +0.27 R to about +0.41 R, with the win rate up about 3 points.
  15 of 19 products are positive against 14 for `off`. But the 95% interval on the difference
  is about 1.3 R wide and centred near +0.15 R. A difference this size is well inside what
  resampling produces by chance. Removing 81 trades also costs power: the detectable edge goes
  from 14 to 18 points.
- **`above` does worse than doing nothing.** It keeps 90% of entries and lowers the point
  estimate. The likely reason, which this run did not test: a Donchian breakout usually closes
  above its 200-day SMA anyway, so the gate rarely binds, and where it did bind it removed
  winners about as often as losers.
- **The first ~205 days mattered more than any filter.** Restricting `off` to the common
  window raised its expectancy from +0.166 R to +0.270 R. The 18 excluded trades
  (2021-09 → 2022-04, the onset of the 2022 bear market) summed to about −18 R, an
  average near −1.0 R. That
  supports the hypothesis's intuition, that entries in a falling market hurt, more than any
  arm here does. It is also one early episode of about 18 trades, not a measurement.
- **The baseline itself is not an edge.** +5.1 win-rate points over break-even against a
  14-point detectable edge is ADR 0006's descriptive regime. This record compares arms; it
  does not establish that any of them, including `off`, makes money.

## 3. Prediction versus outcome

The pre-registration expected 40–60% of entries retained, expectancy moving by less than its
interval width, and "no improvement distinguishable from the baseline". The verdict matched.
Retention came out at 90% for `above` (the prediction was wrong there: price above its
200-day SMA was the normal state at breakouts) and 60% for `slope`/`both`.

## 4. Multiple testing

Three filtered arms were tested against one baseline. With no arm clearing an uncorrected 95%
interval, no correction changes anything. The four ledger rows are counted as trials, so any
future re-test of a trend filter on this rule family starts from M = 3 already spent.

## 5. Pricing

Every figure here is priced per product, as
[the per-product restatement](2026-09-01-per-product-slippage-restatement.md) established, with
the taker fee of 1.20% per leg. The flat-5 bp records this one sits beside are optimistic by
the amounts the restatement measured, and their **verdicts are unaffected**; this record's
verdict is its own and needs no correction.

## 6. Two ledger defects found while recording this, fixed in the same change

The first run's four ledger rows were never committed. `keel.research.ledger` accepted two
kinds of summary value that it cannot read back:

1. **A non-numeric string** (the arm name, the verdict). `read_trials` decodes every summary
   string as a `Decimal`, so it raised `InvalidOperation`, and the append-only ledger would
   have been unreadable from that row on.
2. **A float** (the win rate, the interval bounds). A float is hashed as a JSON number when
   written but read back as a `Decimal`, so the row's own hash never verifies:
   `verify_chain` reports it as tampered, permanently.

Both are now refused when a row is appended (`_validate_summary`), with a test each. The
driver stores words in `params` and figures as exact decimal strings. The run is
deterministic (seed 830), and the committed dataset's figures equal the first run's to the
last digit.

## 7. What follows

Under the pre-registered rule, nothing. If trend filtering is re-opened, the honest next
questions are about sample size, not a finer filter. A detectable edge of 14–18 points at
n = 121–202 means no filter on this rule family can be told apart from the baseline without
roughly doubling the pooled sample. That means more history, more products, or forward trades.
