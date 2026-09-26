# 0006 — Daily strategies are evaluated on the pooled sample, never per asset

Date: 2026-09-26 · Issues: #822 (records #338, settles #427) · Status: decided

## Context

A daily breakout strategy trades rarely *per asset*, and that is a property of the strategy
rather than a defect in the data. It enters only on a new N-day high in a trending regime, and
holds one position per product at a time.

Measured on this deployment:

- **Five years of backtest** (`keel simulate --years 5`, 2026-09-26, the shipped paper rule set
  over 19 products): the daily `turtle_breakout` produced **5 to 18 trades per product** —
  roughly two per product-year — and **220 pooled**.
- **Forward paper** (the 2026-09-26 preview of the pooled review): **24 closed trades pooled**
  across three profiles after about five weeks, with no product above 6.

A per-product floor of 100 trades is therefore out of reach, not merely distant. At two
trades a year, a single product would need about fifty years of history that does not exist,
or fifty years of forward trading. A gate set at that floor can never be passed, so it
measures nothing.

The pooled sample is the only one that reaches a useful size. It carries a known cost:
breakouts fire in herds. #427 measured about **8.4 products triggering on the same day**,
an outcome ICC of **0.212**, and a design effect of **2.58**. So 100 pooled trades carry about
**39 independent observations** (`keel/research/throughput.py`,
`tests/research/test_throughput.py`).

#338 (2026-08-17) had already made the pool the unit of evaluation for promotion. It also set
a diversity floor, printed beside the per-rule reading. #427 (closed) left one question to the
owner: keep **n = 100 pooled** as the floor, or raise it to **259+ pooled** (n_eff 101) before
any evaluation may be confirmatory.

## Options

**Per-asset floor, n ≥ 100 per product.** Statistically cleanest, since there's no
cross-product correlation to correct for. Unreachable for any daily strategy, as shown above,
so it would reject every daily rule by construction. Rejected.

**Pooled floor, n ≥ 100 pooled.** Reachable within a backtest, and within months of forward
trading. Detects only large edges: at n_eff ≈ 39, 80% power one-sided at 5% needs an edge of
about **20 points** of win rate. It cannot confirm a small edge.

**Pooled floor, n ≥ 259 pooled (n_eff 101).** Makes "n = 100" mean what readers assume it
means, detecting an edge of about 12.4 points. Forward accrual at that size is years away
under the Basic subscription's allowance (#427, owner comment of 2026-08-20).

## Decision

1. **Daily strategies are evaluated on the pooled sample, not per asset.** A rule family's
   evidence is the closed trades of every product running the same parameters, pooled. That
   holds for backtest promotion (G2, already checked against `__pooled__` in `keel simulate`),
   for promotion by `keel rules promote` (#338's pooled reading), and for the forward review
   (`keel research pooled-review`). Per-product statistics are still reported, as
   **diagnostics** for spotting a product that behaves differently. They are never a gate.
2. **The floor is n ≥ 100 pooled**, with #338's diversity floor unchanged: at least
   **5 products contributing at least 10 trades each** (`promotion.MIN_POOLED_PRODUCTS`,
   `promotion.MIN_TRADES_PER_PRODUCT_POOLED`). `min_trades: 100` in every profile config
   keeps its value; this record states that its unit is the pool.
3. **At that floor an evaluation is descriptive.** Every report that applies the floor states
   its power in words: "at this n_eff this can only see an edge of X points or larger", as
   `pooled_review.render_report` already does. A pass at n = 100 pooled is never written up
   as a measured edge, and a fail is never written up as "no edge".

## Consequences

- The daily strategies become evaluable at all. The 5-year backtest already clears the floor
  (220 pooled). The forward pool reaches it on the timescale of the paper accounts.
- The price is power. Anyone reading a pooled result at this floor must read it as
  "an edge, if any, is under 20 points" rather than "there is no edge". Mistaking one for the
  other is exactly the error #427 was opened to prevent.
- A **confirmatory** claim — that a rule has an edge, stated as a finding — needs n_eff ≥ 101,
  which is about 259 pooled trades at the measured design effect. This record does not change
  that arithmetic. It only fixes 100 as the floor for evaluation.
- Pooled statistics must be **unit-free** to be poolable. Pooling price-unit P&L lets the
  highest-priced product dominate the result: BTC's price-unit expectancy was about 17,000
  times XLM's in the same table. Pooled expectancy is therefore computed in R-multiples
  (P&L ÷ initial risk, #820). Win rate and trade counts pool as they are.
- **Reopen** this record if the design effect changes materially: a narrower or broader
  product pool changes `k`, so the n_eff of 100 pooled moves with it. Reopen also if a
  subscription tier change makes 259 pooled reachable within a year of forward trading.
