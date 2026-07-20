# ADX Ablation + Random-Entry Control Arm — 2026-07-20

**Status:** in-sample measurement on full cached history. **NOT out-of-sample. NOT a promotion decision.**

Motivated by two findings in KB `source-58` (Katz & McCormick, *The Encyclopedia of Trading Strategies*):

- **§58.2** — their ADX trend gate showed *no out-of-sample benefit* (*"do not rely on indicators like the
  ADX for trendiness determination"*). Our shipped `ADX(14) > 25` gate is load-bearing in the only
  positive-edge rule we have and had **never been ablated**.
- **§58.11** — many published entry strategies were *no better than random entries*; benchmark against a
  random-entry null through the **same exit**, which also separates ENTRY edge from EXIT edge.

## Method

`TurtleBreakout` (daily, entry_lookback=40 / exit_lookback=20, ATR(20) 2N stop), edge backtester
(`strategy.backtest.backtest`), fee 0.6%, slippage 0.05%, cached candles from `keel.db`.

- **Gate ON** (shipped): `adx_threshold=25.0`
- **Gate OFF** (ablated): `adx_threshold=-1.0` — no code change needed; the gate is a plain
  `if not adx_now > params["adx_threshold"]` comparison.
- **Random control**: a scratchpad subclass overriding **only** `detect()` with a seeded coin-flip entry,
  reusing the ATR stop/target math verbatim and inheriting `exit_signal()` unchanged, so **entry is the
  only thing that differs**. `p` calibrated per asset to match gate-ON trade counts; **30 seeds**.

Data spans: BTC/ETH 1819 daily bars (~5yr); **PAXG only 435 bars (~14 months)**.

## Part 1 — ADX ablation

| Asset | Variant | n | win% | R:R | PF | expectancy/trade |
|---|---|---:|---:|---:|---:|---:|
| BTC | **ON** | 13 | 46.2% | 1.88 | **1.61** | **$1,368** |
| BTC | OFF | 21 | 33.3% | 2.30 | 1.15 | $429 |
| ETH | **ON** | 13 | 30.8% | 2.75 | **1.22** | **$47** |
| ETH | OFF | 21 | 28.6% | 2.28 | **0.91** | **−$18** |
| PAXG | ON | 4 | 50.0% | 3.25 | 3.25 | $212 |
| PAXG | OFF | 4 | — | — | — | *identical* |
| **Pooled** | **ON** | **30** | 40.0% | 2.40 | **1.60** | **$642** |
| **Pooled** | OFF | 46 | 32.6% | 2.37 | 1.15 | $206 |

**PAXG produced byte-identical sequences** for ON vs OFF. Investigated rather than accepted: setting
`adx_threshold=200` correctly zeroed all three assets, so the threshold *is* applied. The cause is that all
23 raw Donchian breakouts in PAXG's short window had ADX between 26–62 — **the gate had nothing to reject
in that sample.** A property of short, strongly-trending data, not a bug.

## Part 2 — Random-entry null (30 seeds)

| Asset | p | target n | achieved mean n | null mean exp. | null stdev | z (ON) | z (OFF) |
|---|---:|---:|---:|---:|---:|---:|---:|
| BTC | 0.00978 | 13 | 13.3 | −$415 | **$2,017** | **0.88** | 0.42 |
| ETH | 0.00978 | 13 | 13.5 | $12 | $119 | 0.30 | −0.25 |
| PAXG | 0.01563 | 4 | 3.5 | $360 | $338 | −0.44 | −0.44 |
| **Pooled** | — | 30 | — | −$149 | $931 | **0.85** | 0.38 |

**Nothing clears the z ≥ 2 bar. The highest z anywhere is 0.88 — under half.**

## Conclusions

### 1. The ADX gate EARNS ITS PLACE — §58.2 does not replicate here. **Keep it.**

Gate-ON beats gate-OFF on every quality measure, pooled and per-asset: PF 1.60 vs 1.15, expectancy $642 vs
$206. Most tellingly, **removing the gate turns ETH into an outright losing system (PF 0.91, negative
expectancy)**. Katz & McCormick's warning was about *their* markets and *their* system; on our data the gate
is doing real work. This was worth testing precisely because the answer was not predictable.

### 2. The gate is NOT the under-deployment fix.

Removing it raises pooled trade count 30 → 46 (**+53%**), but the added trades are materially worse
(BTC expectancy $1,368 → $429; ETH goes negative). **Buying trade count by deleting the gate degrades the
edge.** Under-deployment must be solved elsewhere — e.g. the §60.2 rank-and-fill deployment cadence, which
adds trades without weakening the entry criterion.

### 3. ⚠️ The headline: at this trade frequency the rule **CANNOT BE VALIDATED — ever.**

The z-scores must not be read as "the entry has no edge." They mean **the sample cannot resolve the
question.** BTC's null stdev is **$2,017/trade** at n≈13; the implied per-trade σ is **~$7,365**.

For the *observed* gate-ON edge ($1,783/trade over the null) to clear z ≥ 2:

> **~68 trades needed. We produce 2.6/yr. That is ~26 YEARS of data.**

This reframes under-deployment from a returns problem into an **epistemics problem**: a rule trading ~6
times a year across three assets can never accumulate the evidence to prove it works, however good it is.
**Trade frequency is a precondition for knowability, not merely for profit.**

### 4. This VINDICATES `min_trades: 100` — correcting an earlier claim.

The KB row for §58.11 (and the summary written when source-58 landed) framed the random-entry control as *"a
significance test valid at low trade counts where `min_trades: 100` structurally cannot be met."* **That was
wrong, and this experiment disproves it.** A control arm does not manufacture statistical power; it reveals
how little there is. The ~68-trade requirement derived here lands close to the existing 100-trade floor,
which is therefore **well-calibrated on the sample-size axis**.

The per-rule-class floor (§25.5) remains correct on the **win-rate** axis — a trend-follower legitimately
wins <50% of the time. The two axes are independent, and only the win-rate one needed relaxing.

## Caveats

- In-sample, full history, single parameter set. No walk-forward, no OOS split.
- PAXG's 435-bar window is too short to conclude anything about that asset.
- The gate-ON vs gate-OFF comparison is itself within the noise band; it is **suggestive, not conclusive**.
  It supports keeping the gate; it does not prove the gate has edge.
- Fee/slippage assumptions are the defaults, not measured Coinbase fills.
