# The hourly `turtle_breakout` over five years: 4,871 trades, 0 of 19 assets positive at the fee paid

**Date:** 2026-09-26
**Issue:** #823 (the run), in the evaluation frame of ADR 0006 (#822). It depends on #820
(statistics in R) and #821 (the simulator's DCA fixes), both on `main` at `438e78f`.
**Change:** none in code or config. The operational consequence (§7) was the owner's decision,
taken after reading this result: the hourly paper account was decommissioned.
**Harness:** `keel simulate`'s edge pass, unchanged: `sim.report.edge_table` over
`commands.simulate.load_sim_candles`, with per-product slippage from
`commands.simulate.slippage_assumptions` (#259). This is the code path behind every edge table
keel prints. Nothing here is a new instrument.
**Script:** [`2026-09-26-hourly-turtle-5year-backtest.py`](2026-09-26-hourly-turtle-5year-backtest.py),
read-only against a copy of the candle cache.
**Data:** [`2026-09-26-hourly-turtle-5year-backtest.jsonl`](2026-09-26-hourly-turtle-5year-backtest.jsonl)
holds all 4,871 trades, each carrying its R at every fee arm, plus per-product and pooled
summaries (1.5 MB).
**Ledger:** no row. The run was `--no-trial-record`: it is diagnostic, selects nothing, and
under spec §4.4 must not count toward `N`.
**Pre-registration:** **none.** This records a run already made and reported on #823. The one
arm added afterwards is the 0% fee arm, added because the first write-up extrapolated to zero
fee instead of measuring it (§5).

## 1. The question

The daily Turtle yields about two trades per product-year, so no per-asset sample of 100
exists even in five years of history (ADR 0006). The owner asked whether hourly bars would
give a per-asset sample above 100, and what that sample says once fees are charged at the
rate actually paid.

## 2. Method

| | |
|---|---|
| rules | the 19 `turtle_breakout` rows with `params.granularity = ONE_HOUR` from `keel-paperhourly.db`, as persisted: `entry_lookback 40`, `adx 14/25`, `atr_period 20`, `atr_stop_mult 2`, `target_rr 6` |
| window | 5 × 365 days ending `now_ts = 1790455996` (2026-09-26 16:53 UTC): 2021-09-27 to 2026-09-26 |
| fills | next-bar-open market fills; fee on both legs; R from the achieved fill against the original stop (#820) |
| slippage | per product, per leg, from median daily quote volume: 5.0 bp (BTC) to 100.8 bp (ZEC) |
| fee arms | **1.20%** taker (headline: `config.paper-hourly.yaml`, and ≥ 1.136% per leg measured on this deployment's forward fills), **0.60%** (sensitivity), **0%** (measured) |
| trade identity | the driver asserts that all three arms produce the identical set of trades, since a fee changes P&L and never an entry, stop or exit bar |

The per-trade dataset reproduces the 2026-09-26 `keel simulate` report exactly: N = 4,871,
pooled expectancy −1.1778 R at 1.20% and −0.6963 R at 0.60%. The pooled losing streak is 71
here against 70 in the report; the only difference is the tie order among trades that exit in
the same hour.

## 3. Result

| Product | N | Slippage / leg | Median stop, % of price | Win % @ 1.20% | E[R] @ 1.20% | E[R] @ 0.60% | E[R] @ 0% |
|---|---|---|---|---|---|---|---|
| ADA | 255 | 24.9 bp | 2.53% | 15.3% | −1.053 | −0.563 | −0.073 |
| ALGO | 257 | 60.2 bp | 3.03% | 15.6% | −1.088 | −0.692 | −0.297 |
| AVAX | 279 | 31.0 bp | 2.75% | 17.9% | −0.993 | −0.546 | −0.098 |
| BCH | 253 | 48.7 bp | 2.58% | 12.2% | −1.370 | −0.893 | −0.417 |
| BTC | 289 | 5.0 bp | 1.41% | 13.5% | −1.882 | −0.936 | +0.011 |
| CRV | 264 | 64.0 bp | 3.76% | 14.8% | −1.064 | −0.725 | −0.385 |
| DOGE | 280 | 20.9 bp | 2.57% | 13.6% | −1.116 | −0.627 | −0.138 |
| DOT | 275 | 45.5 bp | 2.65% | 12.0% | −1.361 | −0.896 | −0.431 |
| ETH | 275 | 6.3 bp | 1.87% | 16.7% | −1.300 | −0.615 | +0.071 |
| FET | 270 | 53.0 bp | 3.91% | 17.8% | −0.766 | −0.448 | −0.130 |
| ICP | 254 | 52.4 bp | 3.18% | 15.0% | −1.076 | −0.691 | −0.306 |
| LINK | 285 | 24.8 bp | 2.65% | 15.8% | −1.077 | −0.597 | −0.118 |
| LTC | 243 | 29.7 bp | 2.23% | 11.9% | −1.448 | −0.901 | −0.355 |
| NEAR | 235 | 57.7 bp | 3.28% | 16.2% | −1.005 | −0.634 | −0.264 |
| PAXG | 78 | 99.9 bp | 1.58% | 6.4% | −2.476 | −1.720 | −0.963 |
| SOL | 276 | 10.8 bp | 2.75% | 20.3% | −0.803 | −0.333 | +0.136 |
| UNI | 269 | 50.7 bp | 3.09% | 12.6% | −1.213 | −0.811 | −0.409 |
| XLM | 263 | 35.9 bp | 2.45% | 17.5% | −1.148 | −0.638 | −0.127 |
| ZEC | 271 | 100.8 bp | 3.91% | 14.8% | −1.025 | −0.708 | −0.391 |
| **pooled** | **4,871** | 5–101 bp | **2.75%** | **15.1%** | **−1.178** | **−0.696** | **−0.215** |

Pooled, the averages are +2.62 R / −1.85 R per win / loss at 1.20%, and +2.55 R / −1.02 R at 0%.
Profit factor in R is 0.25, 0.41 and 0.73 across the three arms.

- **Per-asset N:** 235–289 for 18 of 19 products. PAXG has 78, because its Coinbase history
  starts 3.6 years into the window. The sample size the question asked for is reached.
- **At 1.20%:** 0 of 19 positive. **At 0.60%:** 0 of 19. **At 0%:** 3 of 19, BTC, ETH and
  SOL, the three products with the lowest slippage (5–11 bp).
- The account pass of the same run is capital- and allowance-limited (rail 14) to 63 trades.
  It returned −2.3% against +80.2% for monthly DCA into the same allowlist. It is not the
  measurement here, but it says the same thing.

## 4. Why: fees are a large fraction of an hourly stop

A trade's round-trip cost, measured in R, is the cost as a fraction of price divided by the
stop distance as a fraction of price:

    cost_R ≈ 2 × (fee + slippage) / (stop distance / entry)

The stop is 2 × ATR(20), so on hourly bars it is narrow. The median across 4,871 trades is
**2.75% of price** (interquartile range 2.08–3.61%). A 1.20% taker fee on each leg is 2.40% of
price per round trip before slippage, so **the fee alone consumes about 0.87 R of every
trade**. That is exactly the measured difference between the 0% and 1.20% arms: median 0.87 R,
mean 0.96 R per trade.

The worst results line up with the narrowest stops. BTC's median stop is 1.41% of price, so its
round trip costs about 1.8 R, and BTC is the second-worst product at 1.20% (−1.88 R) despite
breaking even at 0%.

The daily Turtle on the same universe has a median stop of **11.6% of price**. That figure
comes from the 231 closed trades of every daily `turtle_breakout` row in the daily cache (the 19
`paper` rows plus one AAVE `candidate`), over this same window, and is written to the dataset's
`daily_risk` row. It is not the 220-trade daily `keel simulate` run cited in §6, which ended
about nine hours earlier and in which AAVE had no cached candles.

At an 11.6% stop, the same 2.40% round trip costs about **0.21 R**. The fee is identical; the stop is four times wider, so the cost per unit of risk is four times smaller.
**On this venue at this fee, intraday breakout stops are too tight for the round trip to be
paid.**

## 5. Relationship to earlier records, and one correction

- [`2026-08-11-hourly-backtest-turtle-breakout.md`](2026-08-11-hourly-backtest-turtle-breakout.md)
  found the same thing at a 5 bp flat slippage floor, with profit factors in price units: 0 of
  19 above 1.0 at 1.20%. This record restates it with per-product slippage (#259), in R (#820),
  on five years instead of the history then cached. The verdict is unchanged, and the losses
  are larger, since #259 only ever moves costs up.
- [`2026-08-12-fee-curve-and-rsi-meanrev.md`](2026-08-12-fee-curve-and-rsi-meanrev.md) found
  the hourly Turtle **profitable at zero fee**, at a flat 5 bp slippage. With per-product
  slippage it is profitable at zero fee **only on BTC, ETH and SOL**, where slippage is
  5–11 bp. At 25–101 bp the other 16 products lose before any fee is paid. Both records are
  right about what they measured; slippage is the difference.
- **Correction to the #823 comment:** the first write-up stated about −0.2 R at 0% fee by
  straight-line extrapolation from the 1.20% and 0.60% arms. The measured figure is −0.215 R.
  The extrapolation happened to land close, but it was not a measurement and should not have
  been stated as one. The measured number is the one to cite.

## 6. What this says, and what it does not

**It says:** at the fee this deployment actually pays, the hourly `turtle_breakout` loses on
every product in its universe, over a sample of 235–289 trades per product. Halving the fee does
not change that. The mechanism is arithmetic, not bad luck: a round trip costs close to a full
R when the stop sits about 2.75% from entry.

**It does not say** that daily strategies have an edge. The daily Turtle's own 5-year run
(220 pooled trades, 25% win rate, simulate report of 2026-09-26) and the forward pooled review
(24 trades, 12.5% against a 32.7% break-even) show no edge either. The daily stop's lower
cost-per-R makes a daily edge *possible* to pay for; it does not make one present.
**Regime-filtered daily strategies are a hypothesis this record motivates, not one it tests.**

**Weekly DCA is not an edge claim**, and nothing here evaluates it as one. It is fixed-budget
accumulation: 52 buys a year, no stop, no exit, so none of the breakout arithmetic above applies
to it. It stays live (rule 6, `keel-live.db`, $50 of BTC every 7 days) on its own rationale.

## 7. Operational consequence: the hourly paper account is decommissioned (2026-09-26)

On the owner's direction, after this result:

- `com.keel.paper-hourly` (the hourly trading job) and `com.keel.serve.paper-hourly` (its
  console) were unloaded (`launchctl bootout`). Their plists remain, so the account can be
  reloaded.
- All 19 hourly `turtle_breakout` rules in `keel-paperhourly.db` were set to `disabled`
  (`keel rules disable`). `keel rules enable` restores a rule at `candidate`, never at `paper`.
- One open **paper** BCH position was left as it stands. Nothing manages its exit now. It is
  synthetic, and it is recorded here so that nobody reads it as a live exposure.
- The live account and its weekly DCA are untouched. The daily paper Turtle
  (`com.keel.paperforward`) and paper equities are untouched.
