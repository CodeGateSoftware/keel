[← Knowledge Base index](../README.md)

## Source 84 — `keeks` bankroll-management library + the "Bankroll Management with Keeks" series

**Provenance:** The [`keeks`](https://github.com/wdm0006/keeks) Python library (v0.3.0, by Will
McGinnis) — an educational implementation of the **Kelly Criterion and its variants** for optimal
capital allocation — read in full from the local checkout at
`/Users/elmehdiaitbrahim/Development/work/CodeGate/keeks` (package `keeks/`, `tests/`, `docs/`,
`examples/`). Plus the author's nine-part blog series that documents each strategy:

| # | Post | URL |
|---|---|---|
| a | keeks 0.3.0 release (Merton share) | `mcginniscommawill.com/posts/2025-10-15-keeks-0_3_0-release/` |
| b | Fractional Kelly | `.../2026-01-16-fractional-kelly/` |
| c | Drawdown-Adjusted Kelly | `.../2026-01-23-drawdown-adjusted-kelly/` |
| d | Optimal f | `.../2026-01-30-optimalf/` |
| e | Fixed Fraction | `.../2026-02-06-fixed-fraction/` |
| f | CPPI | `.../2026-02-13-cppi/` |
| g | Dynamic Bankroll Management | `.../2026-02-20-dynamic-bankroll-management/` |
| h | Naive Strategy | `.../2026-02-27-naive-strategy/` |
| i | Strategy Comparison | `.../2026-03-06-strategy-comparison/` |

> ### ⚠️ Disclaimer — read first (halal framing + educational-only)
>
> **Betting/gambling (*maysir*) is forbidden in Islam, and this project does not bet.** The
> `keeks` library is written in the vocabulary of wagering (bankroll, odds, payoff, ruin). We
> extract it for **one reason only: the underlying mathematics of optimal *capital allocation*
> under uncertainty is the same mathematics that governs how much of our cash to commit to a
> spot position.** The Kelly Criterion is a growth-rate optimiser over log-wealth — a portfolio
> result (Merton 1969; Thorp) — not a betting trick. We adopt the **sizing math**, never the
> betting context. Everywhere the source says "bet fraction," read it as **"fraction of trading
> capital risked on a long-only spot entry with a defined stop."**
>
> `keeks`'s own README carries the parallel caveat: *"for educational purposes only … not
> investment, legal, or tax advice … consult a professional."* Same here. **Nothing in this
> source is wired into the live agent by being written down** — every candidate must clear the
> paper-trading proving gate and a backtest floor before it sizes a single real order (§84.16).

---

### §84.1 — Why this matters for keel (the anchor: we already ship one of these)

keel sizes every stop-bearing entry with **fixed-fractional risk sizing**:
`keel/execution/sizing.py::size(equity, risk_pct, entry, stop)` risks `risk_pct` of equity over
the entry→stop distance, `qty = equity·risk_pct / |entry − stop|`. The config default is
**`risk_pct = 0.01`** (`keel/templates/config.yaml`). That is *exactly* the **Fixed Fraction**
strategy of §84.7 — the simplest member of the whole family below. So this source is not exotic:
it is the map of the neighbourhood around the one allocator keel already uses, and it lets us ask
a sharp question — *is 1% the right fraction, and how would the Kelly family answer?*

**The Kelly baseline computed on keel's own promotion floor.** keel only lets a rule trade once it
clears `min_win_rate = 0.55` and `min_rr = 1.5` (`packages/keel-core/keel_core/config.py`). Feed
that floor into Kelly (§84.2):

```
f* = (b·p − q) / b   with p = 0.55, q = 0.45, b = 1.5
   = (1.5·0.55 − 0.45) / 1.5 = 0.375 / 1.5 = 0.25
```

**Full Kelly would risk ~25% of capital per trade at our floor edge; half-Kelly 12.5%,
quarter-Kelly 6.25%. keel risks 1% — about 4% of full Kelly, i.e. *below even quarter-Kelly*.**
Whether that is admirable prudence or growth left on the table is the question the simulation in
§84.14 measures on our own terms. (Preview of the answer: sub-Kelly is *correct* here, but the
reasoning — estimation error and correlation — matters more than the number.)

---

### §84.2 — Kelly Criterion (the baseline) — `KellyCriterion`

**Formula (net-odds binary form):** `f* = (b·p − q) / b`, where `p` = win probability,
`q = 1 − p`, `b` = net payoff-to-loss odds (win pays `b×` the amount risked). Equivalent
"edge/odds" form: `f* = p − q/b`. Maximises the expected log growth rate `E[log W]` — i.e. CRRA
utility at risk-aversion `γ = 1`. keeks adjusts for costs first: `payoff' = payoff − tc`,
`loss' = loss + tc`, then `b = payoff'/loss'`; returns 0 if `p < min_probability` (default 0.5) or
the edge is non-positive; clamps to a max-safe bet so the bankroll can't go negative.

**Why it's the reference, not the recommendation:** Kelly is growth-optimal *only* when `p` and `b`
are known exactly and trades are independent. Both assumptions fail in trading — our `p` is a
noisy backtest estimate and our positions are correlated crypto. Full Kelly's expected drawdown is
punishing (~50% is routine), and **over-estimating `p` pushes you past the growth peak into
*negative* growth** (§84.14). Every other strategy below is a way of buying robustness back.

**keel mapping:** a diagnostic ceiling, not a sizer. Given a rule's backtested `win_rate` and
`R:R`, `f*` tells you the *most* any sane fixed-fractional `risk_pct` should ever be. Our 1% sits
far under it — deliberately.

---

### §84.3 — Fractional Kelly — `FractionalKellyCriterion`

**Formula:** `f = λ · f*`, `λ ∈ (0,1]` (½ and ¼ are standard). The growth curve is *quadratic* in
`λ` (`G(λ) ≈ λK − λ²K/2` about the risk-free rate), so growth is flat near the top: **half-Kelly
keeps ~75% of the growth for ~50% of the variance; quarter-Kelly ~44% growth for ~25% variance.**
That asymmetry is the single most useful fact in the whole source — you give up little growth to
buy a lot of calm, and you buy insurance against having over-estimated your edge.

Author's practitioner guidance: individuals 25–50% Kelly; professional managers 10–20% (capital
preservation); shift down a fraction when `p` is uncertain or the bankroll is small.

**keel mapping — the most directly usable idea here.** A principled way to set `risk_pct` per rule:
`risk_pct = λ · f*(win_rate, R:R)` with a small `λ` (¼ or less) and a hard cap. This makes sizing
*edge-aware* (stronger rules risk more) instead of a flat 1% for everyone — a **candidate lead**,
gated in §84.16.

### §84.4 — Drawdown-Adjusted Kelly — `DrawdownAdjustedKelly`

Two forms exist and they differ; keep them straight:
- **Blog (dynamic):** `f = (1 − d/D) · f*`, where `d` = current drawdown from peak, `D` = max
  acceptable drawdown. Bets shrink to zero as `d → D` — an automatic brake during losing streaks.
- **keeks class (static):** `f = min(1, D/0.5) · f*` — a one-time scale by "your tolerance vs
  Kelly's ~50% expected drawdown." Simpler, not state-dependent.

**keel mapping:** the *dynamic* form is the interesting one and it **overlaps our existing
account-DD breaker** (the design's total/weekly drawdown circuit-breaker). keel already halts on
deep drawdown; drawdown-adjusted Kelly would instead *taper* size continuously before the halt.
Candidate: a graded taper feeding the CTS execution ladder, not a new hard rail.

### §84.5 — Optimal f (Ralph Vince) — `OptimalF`

**Idea:** maximise Terminal Wealth Relative `TWR(f) = Π(1 + f·Rᵢ)` over the *historical* return
sequence `{Rᵢ}` — no forward probability needed; it fits `f` to what actually happened. keeks's
binary implementation reduces to `f = p − (1−p)/(reward/risk)` capped by `max_risk_fraction`
(default 0.2). **Tends to size *larger* than Kelly and draws down harder; the author says use
50–70% of the computed value and ≥30–50 trades of history.**

**keel mapping — mostly a caution.** Optimal f is acutely sensitive to the single worst historical
loss and to over-fitting a short record — exactly the failure mode our KB has fought (PBO, MinBTL,
the §79/§74 "settled by measurement" table). **Deferred**: interesting as a lens on our R-multiple
distributions, dangerous as a live sizer on 31-trade samples.

### §84.6 — Merton share / CRRA — `MertonShare`

**Formula:** `f = μ / (γ·σ²)` — expected excess return over `γ` × variance; `γ` = relative
risk-aversion. `γ = 1` recovers Kelly; higher `γ` = smaller size. keeks's 0.3.0 sim: at 55%/1000
bets, `γ=2` cut volatility 61% while keeping 84% of Kelly's return; `γ=5` cut volatility 85%
keeping 77%. This is the *continuous* generalisation of fractional Kelly (choosing `γ` ≈ choosing
`λ`), and it makes the risk-aversion knob explicit and defensible.

**keel mapping:** a cleaner theoretical framing for "why sub-Kelly" than an ad-hoc `λ`. Same
candidate as §84.3, expressed as a `γ` we can defend (`γ ≈ 2` is the standard human estimate).

### §84.7 — Fixed Fraction — `FixedFractionStrategy`  ★ this is keel today

**Formula:** `bet = c · bankroll`, constant `c`, ignoring odds and edge. keeks default and the
blog's guidance land at ~1–3%. "Theoretically impossible to go fully broke (but you can get
close); often leaves money on the table with strong edges." keeks's Monte-Carlo optimal fixed
fractions: 52% edge → ~1.5–2%, 55% → ~2–2.5%, 60% → ~3–4%.

**keel mapping — identity.** `sizing.size(...)` with `risk_pct` IS this. Note keel's 1% is *below*
even the 55%-edge optimal (~2–2.5%) that keeks found for a **known** 55% edge — consistent with our
edges being *estimated*, not known. The library's own finding "optimal fixed fraction is typically
lower than the Kelly fraction" is our lived reality.

### §84.8 — CPPI (Constant Proportion Portfolio Insurance) — `CPPIStrategy`

**Formula:** `exposure = m · (bankroll − floor)`; `cushion = bankroll − floor`; floor ratchets up
at new peaks. Size scales up as you win, and *automatically* toward zero as you approach the floor.
Prioritises capital preservation over growth; **gap risk** is the named failure (a sudden loss can
breach the floor). Parameter menu: conservative floor 90%/`m`=2 … aggressive floor 60%/`m`=5.

**keel mapping:** conceptually close to keel's **total-exposure cap + account-DD breaker** already
in the rails, but expressed as a smooth allocator instead of a hard clamp. The **Kelly-CPPI hybrid**
the comparison post recommends ("size by Kelly but never let capital fall below a floor") is
essentially *what keel already does structurally* (risk-sized order, clamped by caps). Good
vocabulary for documenting our design; not a new build.

### §84.9 — Dynamic Bankroll Management — `DynamicBankrollManagement`

**Formula:** `f = base · (streak × volatility × drawdown × probability factors)`, clamped to
`[min_fraction, max_fraction]`. Adapts size to recent performance. **Author's own caveats: complex,
parameter-heavy, "risks overreacting to normal variance," hard to backtest.**

**keel mapping — ⛔ mostly declined.** The streak factor is a soft **martingale/anti-martingale**,
and increasing size after wins collides with our **no-martingale rail** and our repeatedly-measured
lesson that streak-chasing adds *correlated* trades (README "settled by measurement" table). The
one defensible sub-component is the *drawdown* factor — which is just §84.4.

### §84.10 — Naive / flat stake — `NaiveStrategy`

Two meanings again: the **blog's Naive** = flat dollar stake every trade (`bet = const`), the
crudest baseline; **keeks's `NaiveStrategy` class** = risk-neutral EV-proportional
(`f = EV/payoff` if `EV > 0`). **keel mapping:** the flat-dollar form is our **DCA sizing**
(`dca_size`: fixed USD budget ÷ price, no stop) — so keel *already* runs a "naive" sizer for its
stopless accumulation rule, correctly, for a different job than risk sizing. Useful as the
simulation's floor baseline.

---

### §84.11 — Strategy comparison matrix (from the "Strategy Comparison" post)

The author's qualitative ranking (1000 bets, p=0.55, even money, seed 43), reframed for keel:

| Strategy | Growth | Drawdown risk | Complexity | keel verdict |
|---|---|---|---|---|
| Full Kelly | Excellent | High | Moderate | ceiling/diagnostic only |
| Fractional Kelly (½) | Very good | Moderate | Moderate | **candidate sizer (§84.3)** |
| Drawdown-Adj. Kelly | Good | Low–Mod | High | candidate taper (§84.4) |
| Optimal f | Excellent | High | High | deferred — overfit risk |
| Merton (γ≈2) | Very good | Moderate | Moderate | candidate framing (§84.6) |
| Fixed Fraction | Good | Moderate | **Low** | **keel today (1%)** |
| CPPI | Moderate | **Very low** | Moderate | ≈ existing rails |
| Dynamic BM | Good | Moderate | High | ⛔ martingale-adjacent |
| Naive flat | Low | High | Very low | = keel DCA sizing |

Author's headline: *"there's no one-size-fits-all … even the most mathematically optimal strategy
is only as good as your ability to stick with it."* That psychological-adherence point is the same
one §54 (Kaufman) and §83 (Zerodha) already make — it's why keel is deliberately conservative.

### §84.12 — The `keeks` API, for reference (if we ever port a formula)

- **`BankRoll(initial_funds, percent_bettable=1.0, max_draw_down=0.3)`** — stateful funds tracker;
  `remove_funds`/`withdraw` raise `RuinError` on bankruptcy or on exceeding `max_draw_down` per
  transaction. `history` is the equity curve.
- **Strategies** — all subclass `BaseStrategy`, implement `evaluate(probability, current_bankroll)
  → fraction ∈ [0,1]`, and share `get_max_safe_bet()` clamping. Utility strategies add
  `calculate_max_entry_price(outcomes, probabilities, wealth)` for one-shot gambles.
- **Simulators** — `RepeatedBinarySimulator` (fixed `p`), `RandomBinarySimulator` (`p ~ N(0.5,σ)`),
  `RandomUncertainBinarySimulator` (perceived vs actual `p` — the *estimation-error* model we
  borrow in §84.14). All call `evaluate_strategy(strategy, bankroll)` and mutate the bankroll
  in-place, stopping gracefully on `RuinError`.
- **`utils`** — `crra_utility(W, γ)` (`log W` at γ=1, else `W^{1−γ}/(1−γ)`),
  `expected_utility`, `find_indifference_price` (binary search — resolves St. Petersburg to a
  finite price under risk aversion). **Deps: numpy/matplotlib/pandas** — heavier than keel wants,
  which is why our own simulation (§84.14) re-implements only the handful of formulas in stdlib.

### §84.13 — Halal / adaptation screen (summary)

Nothing here involves *riba* (no borrowing/leverage — Kelly sizes cash only), and we strip the
*maysir* context entirely: these are capital-allocation formulas applied to long-only spot entries
with defined stops. **Usable:** fractional-Kelly / Merton sizing (§84.3/§84.6), drawdown taper
(§84.4). **Already ours:** fixed fraction (§84.7), CPPI-like caps (§84.8), naive/DCA (§84.10).
**Declined:** dynamic streak-scaling (§84.9, martingale-adjacent), optimal f as a live sizer
(§84.5, overfit). No shorting, no leverage, no derivatives touched by any of it.

### §84.14 — Simulation on OUR terms (measured, not asserted)

We re-implemented the handful of formulas above in **pure stdlib** (no numpy/pandas) and ran a
seeded Monte-Carlo to answer §84.1's question directly. Code:
`docs/superpowers/analysis/bankroll_sizing/` (`sizing_strategies.py`, `simulate.py`,
`test_sizing_strategies.py` — **38 unit tests, all pass**). Full write-up:
[`docs/superpowers/reports/2026-07-22-bankroll-sizing-comparison.md`](../../../reports/2026-07-22-bankroll-sizing-comparison.md).

**Experiment 1 — reproduce the keeks binary comparison** (1000 bets, p=0.55, even money,
$1000, 500 seeded paths). The point is the *ordering*, and it reproduces the literature exactly:
growth and drawdown both rise monotonically with the Kelly fraction.

| Strategy | Median terminal | Median max-DD | Ruin |
|---|---|---|---|
| Full Kelly | $122,449 | **89.6%** | 0% |
| Half Kelly | $38,572 | 61.0% | 0% |
| Quarter Kelly | $8,482 | 35.5% | 0% |
| **Fixed-1% (keel)** | $2,535 | **15.3%** | 0% |
| Drawdown-adj Kelly (D=0.20) | $993 | 20.0% | 0% |
| CPPI (floor 0.8, m=3) | $640 | 60.0% | 0% |
| Naive-flat $10 | $1,980 | 11.4% | 0% |

**Experiment 2 — risk_pct vs the Kelly family AT keel's floor edge** (Profile A: p=0.55, b=1.5;
200-trade sequences, 500 paths). Terminal shown as a *multiple* of starting capital. The right
two columns are the punchline — the **same sizing, but the true win-rate came in 5 points below
the estimate** (still a positive edge):

| Sizing (risk fraction) | p correct: median × | p correct: worst DD | p over-est 0.05: median × | over-est: ruin |
|---|---|---|---|---|
| **keel-1%** | 2.0× | 19% | 1.6× | **0%** |
| Quarter-Kelly (6.25%) | 49× | 76% | 12× | **0%** |
| Half-Kelly (12.5%) | 721× | 95% | 46× | **0%** |
| Full-Kelly (25%) | 5077× | **99.9%** | **22×** | **3.6%** |

*(Multiples are frictionless geometric compounding in an i.i.d. model — illustrations of the
growth/safety gradient, NOT return forecasts; see caveats below.)*

**What this means for keel — two true things at once:**
1. **1% is mathematically far to the safe side.** Every sub-Kelly level tested compounds a real
   edge far faster than 1% does — because 1% barely lets a genuine edge compound geometrically.
   *If* our backtested p/b were trustworthy point estimates, something around **quarter-Kelly
   (~6%) would capture most of the growth** at a fraction of full-Kelly's ~90% drawdowns.
2. **…but sub-Kelly is the correct posture, and 1% is a defensible extreme of it.** The
   estimation-error column is decisive: over-estimating p by 0.05 collapses **full-Kelly** from
   5077× to 22× and lifts its ruin rate from 0% to **3.6%**, while quarter-/half-Kelly *and* keel's
   1% all keep **0% ruin**. Full Kelly assumes a *known* edge; ours is a **noisy backtest
   estimate** (§58.11: "W/R off ~23 trades is noise wearing a formula"), so the fractional-Kelly
   margin of safety is exactly the right instinct. keel sits on the safe side of that argument —
   just pushed further than the math alone requires.

**Caveats (from the report):** i.i.d. independent trades (real crypto positions are *correlated* —
understates higher-fraction risk); known b, no fees/slippage; a single fixed misestimation
magnitude; keel's real order/day/exposure caps not modelled; float money (keel's real path is
Decimal-only). **This is an observation about the growth/safety tradeoff, NOT a recommendation to
change `risk_pct`** — any change would need its own review against the live rails and correlation.

### §84.15 — Commands to explore the rules & strategies (educational)

All read-only; none place an order. Use them to *see* the `p` and `R:R` that feed the Kelly math
above, and to run the sizing study yourself.

```bash
# --- the rule library & lifecycle (candidate → paper → live → disabled) ---
keel rules list                      # every rule + status + params
keel rules list --status live        # filter by lifecycle stage
keel rules seed                      # populate one candidate per (kind, product) from defaults

# --- a rule's edge stats: win_rate (p) and R:R (b) are the Kelly inputs ---
keel rules backtest <RULE_ID>        # → n_trades, win_rate, expectancy, profit_factor, max_drawdown
keel rules promote <RULE_ID>         # re-backtest and advance IF it clears the floor (0.55 / 1.5)

# --- the bankroll-sizing study from this source (§84.14) ---
uv run python docs/superpowers/analysis/bankroll_sizing/simulate.py          # regenerates the report
uv run pytest docs/superpowers/analysis/bankroll_sizing/test_sizing_strategies.py -q   # 38 tests
```

Kelly ceiling for any rule, from its backtested stats — a sanity-check on a hand-set `risk_pct`,
**not** an autonomous sizer (see §84.16):

```python
from docs.superpowers.analysis.bankroll_sizing.sizing_strategies import kelly_fraction, fractional_kelly
p, b = 0.55, 1.5                       # e.g. keel's promotion floor
kelly_fraction(p, b)                   # 0.25  → full-Kelly ceiling
fractional_kelly(p, b, 0.25)           # 0.0625 → quarter-Kelly reference
# keel's actual risk_pct = 0.01 is ~4% of the full-Kelly ceiling.
```

### §84.16 — Takeaways & candidate leads (all gated by the paper-proving floor)

**Verdict — like §83, this source CONFIRMS the risk model, it does not reshape it.** The Kelly
family is a fourth independent route to "use fractional f, never full" (§54.18, §83.5, §83.11), and
our own simulation shows keel's deeply-sub-Kelly 1% is a *defensible* answer to estimation error,
not mere timidity.

- ✎ **Candidate (humble): edge-aware `risk_pct` as a ceiling/sanity-check.** `λ·f*(win_rate, R:R)`
  with a small `λ` and a hard cap could make sizing edge-aware. **Gate:** §58.11/§83.5 already
  ruled per-rule W/R too noisy to size on autonomously — so use it to *flag* a hand-set `risk_pct`
  that exceeds a fractional-Kelly ceiling, never to set it. Deterministic; cheap to prototype.
- ✎ **Candidate: dynamic drawdown taper (§84.4)** `(1−d/D)·f*` — taper size continuously as
  account drawdown builds, *before* rail 11's hard breaker halts. Feeds the CTS execution ladder,
  not a new rail. The one item here with no existing keel analog.
- ⛔ **Declined:** dynamic streak-scaling (§84.9, anti-martingale — collides with the no-martingale
  rail + the "correlated trades" lesson); optimal-f as a live sizer (§84.5, overfits the worst
  historical loss on 31-trade samples).
- ⧉ **Already ours, now with vocabulary:** fixed fraction (§84.7 = `sizing.size`), CPPI-like caps
  (§84.8), naive/DCA (§84.10 = `dca_size`). The "Kelly-CPPI hybrid" the series recommends is,
  structurally, what keel already does (risk-sized order, clamped by exposure caps).

**Cross-references:** design spec `docs/superpowers/specs/2026-07-15-halal-cb-autotrade-design.md` · §54.18 (optimal-f + fractional) · §83.1
(equity base) · §83.5/§83.11 (Kelly = `W−(1−W)/R`) · §58.11 (small-sample W/R is noise) ·
§54.7 (volatility-parity sizing) · §33/§50.1/§54.22/§68 (MPT declined — Kelly is *not* MPT).
