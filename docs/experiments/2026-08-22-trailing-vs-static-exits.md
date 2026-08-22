# Trailing vs static exits: does the ratchet-only exit policy help, at the fee actually paid?

**Issue #442.** Driver: [`2026-08-22-trailing-vs-static-exits.py`](2026-08-22-trailing-vs-static-exits.py)
(pre-registered in its docstring before the run). Artifact: every number below is a row of
[`2026-08-22-trailing-vs-static-exits.jsonl`](2026-08-22-trailing-vs-static-exits.jsonl) — 240
cells (2 families x 30 products x 4 arms) plus 8 pooled rows.

**Headline, stated first because it is the point: the pre-registered expectation held —
trailing and break-even rolls do NOT improve either family at the 120 bp taker fee, and the
trailing arm makes both families clearly worse. The constructor defaults therefore stay OFF
(`trail_atr_mult=None`, `be_roll_rr=None`): the capability is wired and measured, and the
measurement says not to turn it on.**

## Why this run exists

#442 found `executor.trail_stop_atr` / `roll_to_break_even` / `scale_out` implemented,
unit-tested, and never called. The PR that ships alongside this record wires the exit POLICY
(ratchet-only ATR trailing + break-even roll) into the engines that drive exits per bar
(`strategy.backtest`, `sim.portfolio_sim`, via `strategy/exit_policy.py` and the per-family
`trail_atr_mult`/`be_roll_rr` params), with turtle deliberately excluded. This run measures
what the wiring changes, before any default is flipped.

## Method (frozen in the driver's docstring before the run)

- Same engine as the #475 significance reconstruction: `keel.strategy.backtest.backtest` over
  every product's full cached ONE_HOUR history (2021-07-18 to 2026-08-21 UTC; 30 products with
  >= 2,000 hourly bars, 5,902 to 44,623 bars each), next-bar-open market fills, 5 bp per-leg
  slippage, and the **120 bp taker fee per leg** (`backtest.TAKER_FEE_PCT`) — the rate outside
  the fee-free allowance, because the question is whether exit management changes the
  family's real-cost economics.
- Four pre-registered arms, constructor defaults for everything except the exit knobs:
  `static` (no knobs — the shipped default and the pre-#442 behavior), `trail_1_5`
  (`trail_atr_mult=1.5` — the live primitive's own default multiplier), `be_1`
  (`be_roll_rr=1`), `trail_1_5_be_1` (both).
- `turtle_breakout` is out of scope BY DESIGN: its real exit is the Donchian channel and the
  family carries no exit-policy knobs (#442 hypothesis 3) — there is no trailing arm to
  measure.
- Read-only against the deployment cache (`file:...?mode=ro`); the only writes are the JSONL
  artifact and stdout.

## Results — pooled per (family, arm), the pre-registered question

| family | arm | n (pooled) | win rate | expectancy (1-unit notional) | profit factor | avg R |
|---|---|---|---|---|---|---|
| pullback_continuation | static | 3,261 | 0.1435 | −68.00 | 0.0075 | −2.438 |
| pullback_continuation | trail_1_5 | 3,697 | 0.0933 | −61.89 | 0.0017 | −2.195 |
| pullback_continuation | be_1 | 3,303 | 0.1150 | −69.19 | 0.0016 | −2.424 |
| pullback_continuation | trail_1_5_be_1 | 3,704 | 0.0886 | −62.03 | 0.0016 | −2.196 |
| rsi_meanrev | static | 1,201 | 0.2714 | −108.89 | 0.0776 | −1.394 |
| rsi_meanrev | trail_1_5 | 1,213 | 0.1665 | −111.40 | 0.0197 | −1.377 |
| rsi_meanrev | be_1 | 1,207 | 0.2030 | −118.30 | 0.0336 | −1.409 |
| rsi_meanrev | trail_1_5_be_1 | 1,215 | 0.1597 | −110.33 | 0.0198 | −1.375 |

Wall clock 5,189 s for 240 cells. Per-product direction (arm expectancy vs static
expectancy, 30 products each cell):

| family | arm | products better / worse / same |
|---|---|---|
| pullback_continuation | trail_1_5 | 17 / 13 / 0 |
| pullback_continuation | be_1 | 16 / 13 / 1 |
| pullback_continuation | trail_1_5_be_1 | 16 / 14 / 0 |
| rsi_meanrev | trail_1_5 | 14 / 16 / 0 |
| rsi_meanrev | be_1 | 13 / 15 / 2 |
| rsi_meanrev | trail_1_5_be_1 | 13 / 17 / 0 |

## What the numbers say

- **Every arm of every family is deeply negative at 120 bp/leg** — the same wall #475 hit;
  exit management does not move it. This run was never going to find an edge that survives a
  fee the families do not survive, and it does not.
- **The trailing arm is clearly worse, not differently-bad.** Win rate collapses
  (pullback 14.4% → 9.3%; rsi 27.1% → 16.7%) and profit factor drops ~4x in both families:
  the trail cuts winners before the fixed-R target they were sized to reach — exactly the
  pre-registered mechanism, and exactly why turtle (whose entire payoff is the long tail)
  was excluded by design.
- **rsi_meanrev's expectancy is strictly worse in every arm** (−108.89 → −110.33..−118.30):
  a mean-reverter's edge is the quick full move; every form of early exit gives back part
  of it and pays the same round-trip fee.
- **pullback_continuation's expectancy moves −68.00 → −61.89..−69.19** — a less-bad loss on
  the same sign, not an improvement. The trailing arms' n grows 3,261 → 3,697 (+436 trades,
  +13%): earlier exits free the slot sooner, each re-entry paying the full round-trip
  friction again. The trail also halves what little profit factor the static arm retains.
- **The per-product counts are a coin flip** (best cell 17/30 better, worst 13/30) — there
  is no consistent per-product direction to appeal to either.

## Decision the evidence supports

- **Constructor defaults stay OFF for both families** (`trail_atr_mult=None`,
  `be_roll_rr=None`). The knobs remain available for research sweeps and for any future rule
  row that wants to carry them, but nothing shipped turns them on: at the fee actually paid,
  the wiring's measured effect is negative-to-neutral, exactly the direction the
  pre-registration predicted and for the reasons it predicted.
- The wiring itself is NOT reverted — the capability was the point of #442, and a default-off
  policy whose knobs measurably do what they say (ratchet, exit earlier) is now tested
  behavior rather than a dormant claim.
- Live stop management remains issue #502 (the broker port has no bracket/OCO `OrderSpec`
  kind), independent of this result.
- **A stated limitation:** the paper trader (`strategy/paper.py`) is NOT wired. It is
  signal-driven — it holds no `Rule` instance to read params from, and its `on_candle`
  receives one candle at a time, so the trailing ATR has no series to compute from. With the
  defaults OFF no paper row can carry the knobs into a divergence today; a paper row that
  sets them would backtest with management and paper-trade without it, which the knobs'
  PARAM_DOCS state outright ("Sim/backtest engines only"). Paper should follow #502.
