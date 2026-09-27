# Accumulation policy: bounded value averaging and band rebalancing vs static DCA: design

**Date:** 2026-09-27 · **Issue:** #831 · **Status:** DRAFT, for review. No code or simulation
runs until this is approved. §9 lists the decisions the review has to make.

## 1. Purpose and non-goals

**Question.** Given the same monthly deposits, does *how* the money is deployed (value
averaging, directing contributions to underweight assets, or band rebalancing) give better
risk-adjusted accumulation than static monthly DCA, net of the fees this account actually
pays?

**Non-goals.**
- **Not an edge claim.** This is accumulation policy, like the live DCA rule. ADR 0006's
  trade-count floor does not apply; the comparison is between account paths.
- **No live wiring.** This spec covers the simulation only. Any live allocator would be a
  separate design, reviewed against the rails (§6).
- **No parameter search.** Every parameter below is declared, not fitted. A first run that
  tunes its own bands is measuring the tuner.

## 2. The prior this must answer

The knowledge base marks fixed-weight rebalancing as the **wrong paradigm** for this
project, and records that correlated alts are "near-single exposure" (KB §51). The
rebalancing premium comes from dispersion between *weakly correlated* assets. Crypto majors
move together, so between BTC, ETH and the small caps there is little to harvest. **The one
leg where a premium is plausible is PAXG (gold), the only low-correlation asset in
`target_weights`.** The hypothesis is sharpened accordingly (§8): if band rebalancing helps,
it should show up through the PAXG leg, and it should not help a PAXG-free universe much.

## 3. Universe, weights, window

- **Weights:** `config.paperforward.yaml`'s `target_weights`: BTC 0.30, ETH 0.20, PAXG 0.20,
  SOL, XLM, LTC, ADA, LINK 0.06 each. These already exist, are not fitted, and are what the
  `dca_into_allowlist` benchmark uses.
- **The PAXG problem.** Coinbase's PAXG history starts **2025-05-08**, so a 5-year window
  cannot include it. Two runs are proposed (decision **D1**):
  - **Primary (5 years, 2021-09-27 → 2026-09-26):** the 7 non-PAXG assets, weights
    renormalised to sum to 1. Long enough to include a full bear and bull cycle.
  - **Secondary (~1.4 years, 2025-05-08 → 2026-09-26):** all 8 assets. This is the only test
    of the §2 prediction about PAXG. It is short, one regime, and labelled as such.

## 4. The account model (common to every arm)

- **Deposits:** every arm receives the **same** deposit **D = $500** on the first UTC day of
  each month (decision **D2**). An arm decides how much to deploy. **Undeployed cash stays
  as cash and earns nothing**: no interest, consistent with the project's fiqh constraints.
  Every arm is judged on **account value = holdings at the daily close + cash**.
- **Decisions and fills:** decided on the completed daily bar of the deposit day, filled at
  the next day's open, as in `report.accumulation_table` (#821).
- **Costs, modelled as this account is actually billed** (decision **D3**):
  - **Buys** are fee-free up to the monthly allowance **A = $500** (Coinbase One Basic, the
    account's attested tier and the simulator's `assumed_free_volume_usd`). Buy notional
    beyond A in a calendar month pays the 1.20% taker fee.
  - **Sells always** pay the 1.20% taker fee. Rail 14's allowance covers buys only.
  - **Slippage** applies to every leg, per product, from `simulate.slippage_assumptions`
    (#259).
  - **Sensitivity run:** a flat 1.20% on every leg (an account with no subscription).
- **Holdings:** one lot per asset at average cost. A sale reduces quantity at the average
  cost, so realised P&L is correct and nothing is double-counted.
- **No leverage, no shorting, no borrowing.** An arm can never spend more than its cash.

## 5. Arms (all parameters declared here)

Let `w_i` be target weights, `P_{t,i}` prices, `H_{t,i}` the value held in asset *i* before
month *t*'s trades, `H_t = Σ_i H_{t,i}`, and `t = 1, 2, …` the month index.

- **A. Static DCA (baseline).** Buy `w_i · D` of each asset every month.
  - Must reproduce `sim.benchmark.dca_into_allowlist` to the cent under a zero-fee model.
    This is a harness test (§7) that pins the baseline to existing, trusted code.
- **B. Bounded value averaging (buy-only).** The target value path for each asset is
  `V_{t,i} = w_i · D · t`: cumulative deposits, zero assumed growth (g = 0). No return
  estimate is fitted, which keeps the path honest. Each month:

      ΔC_{t,i} = clamp( V_{t,i} − H_{t,i},  C_min · w_i · D,  C_max · w_i · D )

  with **C_min = 0** (buy-only: a surplus is never sold) and **C_max = 3** (decision **D4**).
  If cash can't cover the sum, each asset's buy is scaled down proportionally. When prices
  run ahead of the path the arm buys nothing and cash builds up. That is a real cost of value
  averaging, and it is reported as cash drag.
- **C. DCA with contributions steered to underweights (buy-only).** Deploy exactly D each
  month, but split it by *shortfall*, not by weight:

      s_i = max(0, w_i · (H_t + D) − H_{t,i}),  buy_i = D · s_i / Σ_j s_j

  If every `s_i = 0`, the arm splits D by `w_i`. It never sells, so it pays no sell fees and
  raises no sale-rail question (§6). It is the cheapest way to lean towards targets.
- **D. DCA plus threshold band rebalancing (sells).** Arm A's monthly buy, then, on the same
  day, if **any** asset's weight drifts outside a **±15% relative band**
  (`|h_i / H − w_i| > 0.15 · w_i`, decision **D5**), trade every asset back to `w_i`: sell
  overweights, then buy underweights with the proceeds. A *relative* band scales with the
  weight: BTC's corridor is 25.5–34.5%, and a 6% asset's is 5.1–6.9%.

That is three treatments against one baseline: three comparisons, stated beside every
difference.

## 6. Rails and constraints (sim-only now; what a live version would have to answer)

This phase is a simulation, so no rail runs. It records which rails a live version would
hit, so the design isn't chosen blind:

- **Rail 14 (monthly fee-free allowance):** modelled directly in §4.
- **`sell_only_on_rule`:** requires every SELL to carry a `rule_kind` ("a defined
  exit/harvest rule"). Arm D's trims are neither an exit nor a harvest in today's sense. A
  live Arm D needs a decision on whether a rebalancing trim is a permitted sale kind. **Arms
  B and C sell nothing and avoid the question entirely.**
- **Per-asset concentration and total-exposure caps:** the concentration cap vetoed the live
  weekly DCA on 2026-09-25, until the per-asset ceiling was raised to 0.75 (#819). A growing accumulation portfolio would hit `max_exposure_usd` over
  time. A live version would need its own sleeve accounting, which is out of scope here.
- **Fiqh:** unchanged. Spot assets only, no interest on idle cash, no leverage, no shorting.

## 7. Harness requirements

- A **new pure module**, e.g. `keel/sim/accumulation_policy.py`, kept separate from
  `portfolio_sim`. That module is a rule engine with rails, entries and exits. This is an
  allocator: deposits in, orders out.
- **Interface:** `allocate(t, prices, holdings, cash, deposit, month_buy_notional) -> list[Order]`,
  one pure function per arm. A driver loop applies fills, fees, the monthly allowance, daily
  marks and deposits.
- **Reuse, not reimplementation:** `commands.simulate.load_sim_candles`,
  `slippage_assumptions`, `sim.benchmark.dca_into_allowlist` (as the arm A oracle) and
  `metrics` for Sharpe, Sortino and drawdown.
- **Tests first (TDD):**
  - arm A equals the benchmark under zero fees;
  - each arm's formula on hand-built price paths;
  - the allowance and fee split (buys within and beyond A; sells always taxed);
  - cash never goes negative;
  - average-cost sell accounting;
  - determinism under a seed.
- **Returns:** a daily time-weighted return index for every arm, with deposits neutralised, so
  Sharpe, Sortino and drawdown measure policy rather than cash timing. Money-weighted return
  (IRR on deposits) is reported beside it.

## 8. Metrics, inference and the pre-registration to be frozen after review

- **Reported per arm:** terminal account value, IRR, time-weighted Sharpe, **Sortino**,
  **max drawdown**, fees paid (buy and sell), turnover and average cash share (drag).
- **Primary comparison:** the Sortino difference between each arm and A, with the max
  drawdown difference beside it.
- **Inference.** One historical path is one sample. A **stationary block bootstrap** of
  *joint* daily asset returns (keeping cross-correlation; mean block 20 days; 2,000 paths;
  fixed seed) runs every arm on the same resampled paths. The quantity reported is the
  distribution of Δ(arm − A).
  - **Stated limitation:** 20-day blocks destroy multi-month mean reversion, which is exactly
    what value averaging feeds on, so the bootstrap is biased *against* arm B. A 60-day-block
    sensitivity is reported beside it, and the historical path is always shown.
- **Decision rule (draft, decision D6):** an arm is "better than static DCA" only if
  `P(ΔSortino > 0) ≥ 0.95` across bootstrap paths **and** its median Δmax-drawdown is not
  worse. Anything else is "not better than static DCA after fees": a complete and useful
  result.
- **Expectation (to be recorded before running):** arm C roughly equals A (nearly free,
  small effect); arm B trails in rising markets (cash drag) and leads after drawdowns, with no
  robust Sortino edge; arm D is hurt on the 5-year PAXG-free run (sell fees, correlated
  assets) and has its best case on the short run with PAXG.
- **Freezing:** once D1–D6 are settled, §3–§8 become the driver's docstring, committed before
  any run, exactly as for #830.

## 9. Decisions for the review

| # | Decision | Proposed |
|---|---|---|
| D1 | Universe and window | 5-year PAXG-free primary + 1.4-year with-PAXG secondary |
| D2 | Monthly deposit | $500 (the tier's fee-free allowance) |
| D3 | Fee model | allowance-aware (buys fee-free to $500/month, sells at taker), plus a flat-taker sensitivity run |
| D4 | Value averaging bounds and path | C_min 0 (buy-only), C_max 3, g = 0 |
| D5 | Rebalancing band | ±15% **relative** to each target weight, checked monthly |
| D6 | Decision rule | P(ΔSortino > 0) ≥ 0.95 and median ΔmaxDD not worse |

A choice to *add* an arm (for example value averaging with sells, or a wider band) should be
made here, before freezing, so it counts as a declared trial rather than a later one.
