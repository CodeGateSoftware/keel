# The 50 bp slippage cap: all three of #523's options, measured on one cohort by one method

> **Cost note (added 2026-09-02).** The figures below are priced at the flat 5bp
> slippage floor. [the per-product restatement](2026-09-01-per-product-slippage-restatement.md) later measured that **no
> asset in keel's universe reaches that floor** — the range is 1.1× to 36.8× — so every
> profit factor here is optimistic by roughly 0.09 at the median. **The verdict is
> unaffected:** the correction only ever moves a number *down*, and every result here was
> already negative. Nothing on this page has been rewritten; records are appended to, not
> revised.

**Issue #523.** Driver: [`2026-08-30-slippage-cap-options.py`](2026-08-30-slippage-cap-options.py)
(pre-registered in its docstring before the run). Artifact: every number below is a row of
[`2026-08-30-slippage-cap-options.jsonl`](2026-08-30-slippage-cap-options.jsonl) — 210 cells
(30 products × 7 arms).

**This document decides nothing.** #523 offers three remedies and says none is obviously right;
one of them disqualifies live PAXG. The cap, the admission floor and the model are unchanged by
this run and by this record. What follows is the evidence to decide on.

**Headline, stated first: #523's finding reproduces exactly on `origin/main` (`ed206c0`) — its
shadow-run figures are recoverable to four decimal places — and measuring all three options
together reverses the ordering the issue implies. Option 1 (raise/remove the cap) costs
0.3%–24.0% of net PF and is the only one that moves in the conservative direction. Option 2
(participation scaling) makes results 0.9%–38.6% BETTER at the clip sizes this deployment
actually trades, because at a $50 clip square-root-law impact is under 5 bp for every asset in
the corpus — it is a flattery, not a correction, unless the spread it would replace is measured
first, and keel stores nothing to measure it with. Option 3 (a $5M admission floor) changes no
number for any asset it admits: it deletes 15 listed products and live PAXG and leaves the
surviving 13 net PFs bit-identical. Nothing in any arm, including the most flattering one,
reaches a net profit factor of 1.0 at n ≥ 100.**

## Was #523 stale? No — and that is worth stating

A great deal has merged since #523 was filed. Every substantive claim in it was re-measured
against `origin/main` today:

| #523 claim | today | verdict |
|---|---|---|
| Model is `floor × sqrt(anchor/median)` clamped to [5 bp, 50 bp] | unchanged in `strategy/backtest.py` | ✅ |
| Cap binds at $5M/day | exactly $5M (`0.005 = 0.0005 × sqrt(5×10⁸/V)`) | ✅ |
| Admission floor is $1M/day | `ScreenPolicy.min_median_daily_volume = 1000000` | ✅ |
| Corpus tail (TON) would demand ~184 bp | TON median $369,944 → **183.8 bp** | ✅ measured, not copied |
| CRO $1,139,342 → 104.7 bp, capped | identical | ✅ |
| STX / OP / ATOM / FIL / JASMY medians | identical to the digit | ✅ |
| PAXG $1,224,999 → 101.0 bp | $1,249,190 → **100.0 bp** (470 → 479 daily bars) | ✅ still capped |
| BTC $568,943,908 → 4.7 bp | $568,492,017 → 4.7 bp | ✅ |
| CRO net PF 0.3195 → 0.2428 uncapped | 0.3195 → 0.2428 | ✅ exact |
| STX 0.2932 → 0.2276 | 0.2932 → 0.2276 | ✅ exact |
| ATOM 0.2172 → 0.2007; FIL 0.4167 → 0.3904; JASMY 0.6774 → 0.6663 | identical | ✅ exact |
| BTC/ETH controls unchanged | 0.1462 (n=294) / 0.2471 (n=284) vs 0.1471 (n=293) / 0.2388 (n=282) | ✅ drift is new bars only |
| Relative decline "perfectly monotone in the cost multiple" | ⚠️ true on the issue's 6 assets; **ρ = 0.95 with one inversion** on the full 13 | ⚠️ subset artifact |

The one correction is the monotonicity claim. It is exactly monotone on the six assets #523
tabulated and stops being exact once the cohort is widened (STX 1.96× → 22.4% vs ZEC 1.98× →
20.2%). That is expected — relative decline depends on the shape of the P&L distribution as
well as the cost multiple — and it does not weaken the finding. Everything else stands.

**Separately verified: `taker_pct: 0.012` is right.** A research pass claimed it overstates real
costs by 2×. Against the three real live fills in `keel-live.db`:

| fill | notional | fee | effective |
|---|---:|---:|---:|
| BTC-USD | $50.00 | $0.592885 | 1.1858% |
| BTC-USD | $50.00 | $0.589805 | 1.1796% |
| PAXG-USD | $61.71 | $0.731621 | 1.1856% |
| **aggregate** | **$161.71** | **$1.914310** | **1.1838%** |

Configured 1.2000% / actual 1.1838% = **1.0137×**. The claim is false and nothing here rests on it.

## Method

- The shipped engine, unmodified. `keel.strategy.backtest.backtest` over each product's full
  cached ONE_HOUR history, next-bar-open market fills (#257), 120 bp taker fee both legs. Every
  arm is expressed purely through the `slippage_by_product` resolver the engine already accepts;
  **nothing under `keel/` is edited.**
- `turtle_breakout`, constructor defaults, every product with ≥ 2,000 cached ONE_HOUR bars
  (30 products, 5,902–44,841 bars). No asset picking.
- Rows are read only at **n ≥ 100** (`promotion.PromotionConfig.min_trades`, taken from the gate
  rather than restated). Rows below it are printed and marked, never read.
- **Invariance control.** For CRO and BTC the full entry/exit timestamp sequence is identical
  across slippage rates of 0, 5, 50, 104.74 and 183.8 bp, and `n_trades` is identical across all
  seven arms on all 30 products. The arms differ in cost alone. (`wins` legitimately varies:
  win/loss is a net-of-cost classification.)
- Read-only against the deployment cache (`file:...?mode=ro`); the only writes are the JSONL
  artifact and stdout. 210 cells in 116 s.

## The defect, restated at full corpus width

#523 tabulates 7 assets. The cap is wider than that: **17 of 30 cached products are capped, and
16 of them are simultaneously admissible** — the entire $1M–$5M band, which is most of the
corpus below the majors.

| band | products | rate |
|---|---|---|
| capped **and** inadmissible | TON | 50 bp (would demand 183.8) |
| **capped and admissible — the defect** | CRO, WLD, PAXG, ZEC, STX, OP, ATOM, FIL, CRV, NEAR, ALGO, JASMY, FET, ICP, UNI *(+ the `PAXG-USDT` cache series)* | **all 50.0 bp, regardless of a 4.3× spread in liquidity** |
| sqrt region | BCH, DOT, AAVE, XLM, AVAX, LTC, LINK, ADA, DOGE, XRP, SOL, ETH, BTC | 47.6 → 5.0 bp |

## Option 1 — raise or remove the cap

The principled value the cap's own docstring reasons about is the corpus tail. Measured today:
**TON, median $369,944 → 183.8 bp.** Setting the cap there and removing it entirely produce
**bit-identical results on every product**, because nothing cached is thinner than TON. *The
two variants of option 1 are the same measurement today; they differ only for a future asset
thinner than TON.*

Capped-and-admissible cohort, n ≥ 100, `turtle_breakout`, 120 bp fee:

| asset | n | shipped | uncapped | cost × | net @50 bp | net @uncapped | rel. decline |
|---|---:|---:|---:|---:|---:|---:|---:|
| CRO | 248 | 50.00 bp | 104.74 bp | 2.09× | 0.3195 | 0.2428 | **24.0%** |
| STX | 243 | 50.00 | 98.02 | 1.96× | 0.2932 | 0.2276 | **22.4%** |
| ZEC | 273 | 50.00 | 99.02 | 1.98× | 0.6600 | 0.5265 | **20.2%** |
| OP | 221 | 50.00 | 69.53 | 1.39× | 0.2898 | 0.2608 | 10.0% |
| ATOM | 268 | 50.00 | 63.66 | 1.27× | 0.2172 | 0.2007 | 7.6% |
| FIL | 261 | 50.00 | 61.60 | 1.23× | 0.4167 | 0.3904 | 6.3% |
| CRV | 265 | 50.00 | 61.36 | 1.23× | 0.3477 | 0.3276 | 5.8% |
| NEAR | 227 | 50.00 | 58.68 | 1.17× | 0.2347 | 0.2216 | 5.6% |
| ALGO | 256 | 50.00 | 58.04 | 1.16× | 0.3523 | 0.3394 | 3.7% |
| JASMY | 271 | 50.00 | 54.81 | 1.10× | 0.6774 | 0.6663 | 1.6% |
| FET | 271 | 50.00 | 51.71 | 1.03× | 0.4856 | 0.4819 | 0.8% |
| ICP | 260 | 50.00 | 50.91 | 1.02× | 0.2162 | 0.2152 | 0.5% |
| UNI | 269 | 50.00 | 50.48 | 1.01× | 0.1267 | 0.1263 | 0.3% |
| *PAXG-USDT (cache series)* | 238 | 50.00 | 60.76 | 1.22× | 0.0223 | 0.0182 | *18.5%* |
| **BTC (control)** | 294 | 5.00 | 5.00 | 1.00× | **0.1462** | **0.1462** | **0.0%** |
| **ETH (control)** | 284 | 6.15 | 6.15 | 1.00× | **0.2471** | **0.2471** | **0.0%** |

Below the sample floor, printed not read: WLD n=58 (0.8530 → 0.6827), PAXG n=75 (0.0461 → 0.0157).

Every one of the 13 sqrt-region products is unchanged to the last digit. Spearman ρ between cost
multiple and relative decline is 0.9516 over the 13 listed cohort products.

## Option 2 — participation-rate scaling

The model scales by the *asset's* liquidity and not at all by the *size of the clip*. #523's
framing: a $50 order and a $50,000 order in CRO are both charged 50 bp. Adding size means the
standard square-root impact law, which is the law the current docstring already invokes ("cost
scales with the SQUARE ROOT of the inverse volume ratio") — except that the law's argument is
the **participation rate**, size ÷ volume, and the shipped model drops the size:

    slip = SLIPPAGE_FLOOR_PCT + Y · σ_daily · sqrt(clip / median_daily_quote_volume)

`Y = 1.0` (middle of the usual 0.5–1.5 range); `σ_daily` is close-to-close stdev of daily log
returns over each product's full cached daily history — close-to-close and not Yang-Zhang
deliberately, because `analysis/indicators.yang_zhang_volatility` carries its own
measured-and-not-adopted warning for crypto.

**This deployment's real clip sizes**, read from the live config and the order history:
`config.live-sandbox.yaml` sets `max_per_order_usd: 100` (and `max_exposure_usd: 200`), and the
three filled live orders in `keel-live.db` were **$50.00, $50.00 and $61.71**.

| asset | median daily | σ_daily | impact @$50 | impact @$100 | impact @$50k | shipped charges |
|---|---:|---:|---:|---:|---:|---:|
| CRO | $1,139,342 | 4.60% | **3.04 bp** | 4.31 bp | 96.3 bp | 50.0 bp |
| STX | $1,300,933 | 5.47% | **3.39** | 4.79 | 107.2 | 50.0 |
| OP | $2,585,800 | 6.11% | **2.69** | 3.80 | 84.9 | 50.0 |
| ATOM | $3,084,518 | 4.80% | **1.93** | 2.73 | 61.1 | 50.0 |
| FIL | $3,294,287 | 5.31% | **2.07** | 2.92 | 65.4 | 50.0 |
| JASMY | $4,161,283 | 6.92% | **2.40** | 3.39 | 75.9 | 50.0 |
| PAXG | $1,249,190 | 1.43% | **0.90** | 1.28 | 28.6 | 50.0 |
| BTC | $568,492,017 | 2.76% | 0.08 | 0.12 | 2.6 | 5.0 |

**At the sizes this deployment trades, market impact is at most 3.5 bp for the cohort (ZEC) and
under 5 bp for every product in the corpus (TON, the thinnest, is 4.99 bp).** Priced honestly, a size-aware model charges essentially the spread and nothing
else. Net PF, `part_50` = 5 bp spread proxy + impact:

| asset | n | shipped | net @shipped | part_50 | net @part_50 | change |
|---|---:|---:|---:|---:|---:|---:|
| UNI | 269 | 50.00 bp | 0.1267 | 6.65 bp | 0.1757 | **+38.6%** |
| NEAR | 227 | 50.00 | 0.2347 | 6.92 | 0.3150 | +34.2% |
| ATOM | 268 | 50.00 | 0.2172 | 6.93 | 0.2817 | +29.7% |
| FIL | 261 | 50.00 | 0.4167 | 7.07 | 0.5362 | +28.7% |
| STX | 243 | 50.00 | 0.2932 | 8.39 | 0.3740 | +27.6% |
| OP | 221 | 50.00 | 0.2898 | 7.69 | 0.3679 | +27.0% |
| CRV | 265 | 50.00 | 0.3477 | 7.29 | 0.4408 | +26.8% |
| CRO | 248 | 50.00 | 0.3195 | 8.04 | 0.4030 | +26.2% |
| ICP | 260 | 50.00 | 0.2162 | 6.71 | 0.2721 | +25.8% |
| ALGO | 256 | 50.00 | 0.3523 | 6.82 | 0.4355 | +23.6% |
| ZEC | 273 | 50.00 | 0.6600 | 8.53 | 0.8146 | +23.4% |
| FET | 271 | 50.00 | 0.4856 | 7.05 | 0.5946 | +22.4% |
| JASMY | 271 | 50.00 | 0.6774 | 7.40 | 0.7888 | +16.4% |
| **BTC (control)** | 294 | 5.00 | 0.1462 | 5.08 | 0.1460 | **−0.1%** |
| **ETH (control)** | 284 | 6.15 | 0.2471 | 5.14 | 0.2495 | +0.9% |

**Option 2 moves in the flattering direction, and by more than option 1 moves in the
conservative one.** That is the single most important number in this record.

Two things make it worse than it looks:

1. **The 5 bp spread proxy is the whole model at these sizes, and it is not measured.** keel
   stores no book snapshots and no realised spreads. 5 bp is what the corpus's *most liquid*
   product pays; for a $1.1M/day book it is certainly too small. Every participation arm is
   therefore a **lower bound on cost**. The honest reading is not "costs are 8 bp" — it is "the
   impact term is negligible and the entire cost is a spread keel cannot see."
2. **Inverting the model shows what the shipped curve is actually pricing.** Setting square-root
   impact equal to the uncapped shipped rate gives `clip = floor² · anchor / σ²`, independent of
   volume: **CRO $59,196 · STX $41,793 · OP $33,524 · ATOM $54,337 · JASMY $26,103 · BTC
   $164,244 · PAXG $612,913.** The shipped model prices a clip of roughly $26k–$165k. **This
   deployment fills $50 — between 500× and 12,000× smaller.**

So option 2 does retire the cap's justification, exactly as #523 says. But it retires it by
revealing that the *uncapped* curve is the number that was never justified for this engine's
1-unit notional, and the 50 bp cap has been accidentally holding a wildly oversized cost
estimate down. Shipping option 2 without a spread measurement would replace a defensible
overstatement with an indefensible understatement.

## Option 3 — raise the admission floor to $5M/day

This changes no rate. Every asset a $5M floor still admits is above the cap threshold **by
construction**, so its shipped rate already equals its uncapped rate. Option 3 makes the cap
inert without touching it — by deleting every product on which it binds.

**Disqualified (15 listed products, + the `PAXG-USDT` cache series):**

| asset | median daily | n | net PF (unchanged) |
|---|---:|---:|---:|
| UNI | $4,905,568 | 269 | 0.1267 |
| ICP | $4,822,419 | 260 | 0.2162 |
| FET | $4,675,645 | 271 | 0.4856 |
| JASMY | $4,161,283 | 271 | 0.6774 |
| ALGO | $3,710,326 | 256 | 0.3523 |
| NEAR | $3,630,268 | 227 | 0.2347 |
| CRV | $3,320,365 | 265 | 0.3477 |
| FIL | $3,294,287 | 261 | 0.4167 |
| ATOM | $3,084,518 | 268 | 0.2172 |
| OP | $2,585,800 | 221 | 0.2898 |
| STX | $1,300,933 | 243 | 0.2932 |
| ZEC | $1,274,777 | 273 | 0.6600 |
| **PAXG** | **$1,249,190** | 75 | 0.0461 | 
| WLD | $1,245,627 | 58 | 0.8530 |
| CRO | $1,139,342 | 248 | 0.3195 |

**Surviving shortlist (13):** BTC $568.5M · ETH $330.3M · SOL $107.7M · XRP $77.4M · DOGE $29.7M
· ADA $21.9M · LINK $21.1M · LTC $14.8M · AVAX $13.1M · XLM $10.0M · AAVE $6.4M · DOT $6.3M ·
BCH $5.5M. Their net PFs — 0.1462, 0.2471, 0.3496, 0.4152, 0.3205, 0.3036, 0.2684, 0.1997,
0.2761, 0.2981, 0.2587, 0.1467, 0.1635 — are **exactly the numbers they carry today.** Option 3
costs 15 candidates and buys a zero-digit change in every surviving result.

**Live PAXG.** Of the six allowlisted live assets (BTC, ETH, PAXG, ADA, XLM, DOGE), **PAXG is
the only one disqualified.** The next-closest live asset is XLM at $10.0M, 2× clear of the floor.
Two details bear on the decision:

- The longer-history cross-check does not save it. #523 notes the `PAXG-USDT` cache series
  (1,826 bars) medians **$3,386,380** — 2.7× PAXG-USD's window, and **still below $5M.** PAXG is
  disqualified on both views, not just the short one.
- **The mechanism to keep it already exists and is already in use.** `keel-live.db`'s
  `screen_exceptions` carries a PAXG waiver granted by the operator for the `history` criterion,
  scoped in its own words: *"waives the 4-year on-chain -USD history floor ONLY (441 bars); all
  other admission criteria and all 17 guards.py rails still apply."* PAXG is live by documented
  exception today. A $5M floor would need a second, explicitly-reasoned `liquidity` exception —
  which is precisely what #523's acceptance criterion 4 asks for, and is the difference between
  deciding PAXG's status and having it decided by side effect.

## What does NOT change, under any option

- **No verdict moves.** Nothing reaches net PF 1.0 in any arm at n ≥ 100. The best cell in the
  entire 210-cell grid is ZEC at 0.8146 — under `part_50`, the **most flattering arm measured**,
  the one that prices this deployment's real $50 clip with a spread proxy that is certainly too
  low. #523's reading holds and strengthens: *the cap masks severity, not viability.*
- **The controls are frozen.** BTC and ETH are identical across `shipped`/`tail_cap`/`uncapped`,
  and move −0.1% / +0.9% under the participation arms. Every product above $5M/day is unaffected
  by options 1 and 3 by construction.
- **The binding constraint remains the 1.2% taker fee**, verified against real fills above at
  1.1838% effective. Round-trip friction is 240 bp of fee against 10–210 bp of slippage; the
  whole of this issue is a second-order term on the wrong side of that ratio.
- **The trade set is invariant.** Identical entry/exit sequences across every rate tested.

## Reading

The argument #523 makes for fixing this is not a performance argument, and this run does not
supply one. It supplies the opposite: the largest effect measured here is that option 2 would
make everything look ~25% better, for reasons that are an artifact of an unmeasured spread.

What the run adds to #523 is an **ordering**. The three options are not three routes to the same
place:

- Option 1 is a **cost correction** in the conservative direction, worth 0.3%–24.0%, with a
  principled value (183.8 bp, the corpus tail) that today is indistinguishable from removing the
  cap outright.
- Option 3 is a **universe deletion** that changes no number it keeps, costs 15 candidates and
  live PAXG, and leaves the modelling defect intact for any future asset — it only guarantees
  nothing is admitted where the defect is visible.
- Option 2 is a **model correction that requires data keel does not have.** Its size term is
  measurable and negligible; its spread term is the entire cost at this deployment's clip sizes
  and is unmeasured. Shipping it now trades a conservative bias for an optimistic one.

The one thing the run says unambiguously is that **the bias is real and the direction of the
error is currently safe.** The 50 bp cap understates the sqrt model by up to 2.09×; the sqrt
model in turn charges 23–34× the square-root-law impact of a $50 clip across the cohort (111× on
PAXG, whose 1.43% daily σ makes impact vanishingly small), because it is implicitly pricing a
clip of $26k–$165k. Two errors in opposite directions do not cancel to a justified
number — they cancel to a number nobody can defend, which is exactly the condition #523 objects
to.

**This record recommends nothing and changes nothing.** The measurements are here so the choice
can be made on numbers.
