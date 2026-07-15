[← Knowledge Base index](../README.md)

## Source 20 — "How Professionals Backtest" (backtesting + money-management + optimization)

> The **authoritative source for our backtest / journal / money-management / insights modules.**
> Much reinforces §1.7/§2.3–2.5/§4.5, but adds several concrete, buildable pieces. All in-scope
> (long spot; forex "pips/lots" → crypto price/quantity/%).

### 20.1 Valid-strategy criteria + framing
A strategy must be **testable, repeatable, verifiable** before it earns real money. "Rules must be
board-game instructions a 10-year-old could follow" — zero subjectivity (deterministic; §1.1).
Backtesting exists to **lower expectations, not raise them** (§2.3).

### 20.2 NEW metrics — MFE / MAE → `strategy/backtest.py`, backtest schema
- **MFE (Maximum Favorable Excursion):** how far price moved *in your favor* before you exited →
  reveals **money left on the table** → **target optimization** (e.g. "75% of trades ran ≥40 pips
  but I took 20" → widen target).
- **MAE (Maximum Adverse Excursion):** how far price moved *against you* before target/stop →
  reveals whether the **stop can be tightened** ("80% of the time it only went 15 against a 20 stop").
- Record MFE/MAE per trade; use them to tune `target_method` and `stop_method` **without re-running**
  the whole backtest. High-value for the data-tuning loop.

### 20.3 NEW — Smooth-ratio money management → `strategy/money_mgmt.py` (or `execution/`)
A position-sizing ramp bounded by drawdown caps:
- **Profit trigger:** grow equity by $X (e.g. +$1,000, or +$200) before increasing size.
- **Acceleration:** how many size-units to add per trigger.
- **Guardrails:** **max acceptable (total) drawdown** AND **max WEEKLY drawdown** — if the chosen
  ramp would breach either (per backtest history), the tool **flashes a hard warning** and won't
  recommend it. Compares **fixed-size vs smooth-ratio** equity curves.
- For us: this is **fixed-fractional on current equity (§4.7) with an explicit ramp + DD guardrails**.
  Sizing scales up only within the rails (cf. §8.1 CTS bound, §10.3 DD breaker).

### 20.4 NEW rail refinement — max WEEKLY drawdown (extends §10.3) → `execution/guards.py`
Beyond the total-account drawdown circuit breaker (§10.3), add a **weekly** drawdown bound (e.g. "I'll
accept 40% total but never >15% in one week"). Both are config-driven hard limits; breaching the
weekly bound halts new rule entries for the week. Money management is calculated **weekly**.

### 20.5 NEW — backtest realism: no overlapping trades → `strategy/backtest.py`
Don't count a signal you **couldn't have taken because you were already in a position**. Enforce
**one open position per instrument** in the backtest (skip overlapping signals until flat) — else the
backtest inflates trade count and returns. "Get as close to real trading as possible; spot the losers."

### 20.6 Sample size (reconciles §5.2) → `strategy/promotion.py`
Minimum **100 trades OR 5 years, whichever comes first** (some do 10–20yr). Covers seasonality. This
sharpens the §5.2 "100–200 trades" gate into a concrete promotion threshold.

### 20.7 The "tuneup" — pivot-slice optimization → `analysis/insights.py` (concretizes §2.4/§6.1)
Slice results by **pair × day-of-week × time × entry-type × timeframe × target** to find what to prune:
- Unprofitable pairs ("death by a thousand cuts" — drop them), weak days (e.g. "don't trade Sundays/
  Fridays"), a target-2 that loses (drop the 2nd target), a timeframe that underperforms.
- → our insights module should **auto-surface underperforming pair/time/rule slices** and feed the
  **demotion path** (§6.3): a live rule/pair/time-bucket whose sliced stats fall below floor gets
  disabled. Turns the manual pivot review into automated pruning.

### 20.8 Journal discipline (reinforces §2.5) — rules-followed + errors-made
Columns: **rules_followed (yes/no)** and **errors_made** (order-entry error / trade-management /
fear-greed). "Winning by NOT following your rules is not to be celebrated; approximate **zero gross
performance discrepancy**." Restates the no-deviation principle (§10.6). Error-type breakdown (e.g.
"50% order-entry, 50% fear-greed") shows where to fix — auto-handled for the agent (no fat-fingers),
but tracked for the human in confirm mode.

### 20.9 Manual vs automated backtesting → RECONCILE with our paper gate ⚠️
The source **advocates MANUAL backtesting** (builds pattern-recognition/RAS, trust, "connection to the
numbers") and warns **against automated backtesting** for *learning* — "you're relying on software to
tell you it's profitable, then risking real money on trust you didn't earn." Tension for us (we MUST
automate — it's software). **Reconciliation:** our substitutes for the manual tester's earned trust are
(a) the **mandatory paper-trading proving gate**, (b) **conservative modeling** (fees/spread/slippage,
§4.2; no-overlap, §20.5; intrabar resolution, §2.3), (c) **out-of-sample + demotion** (§6.3), and
(d) **human confirm mode** by default (§6.5). State this explicitly in the spec so automated
backtesting isn't mistaken for blind trust. (He's fine with automation for *journaling* once proven.)

### 20.10 Not accounting software → live P&L from actual fills, not the model
The money-management sheet uses nominal pip values and does **not** model broker fees/spread/slippage/
commissions to the penny. For us: the backtest models costs (§4.2), but **live P&L comes from actual
Coinbase fills logged to the DB** — never the model. Reconciles the model-vs-reality gap.

### 20.11 Reinforced
Three RAS/confidence/optimization benefits of backtesting; expectancy & strike-rate & max-DD & max
losing-streak metrics (§1.6, §4.6); one-strategy/one-pair/one-timeframe at a time; edge decay →
optimize don't destroy (§6.3); "board-game instructions" (§1.1); test before real money (§5.4).

### 20.12 Discarded (no agent value)
Tier-one workbook/CTA promos, "type backtesting in the comments", Excel/Google-Sheets UI mechanics,
forex pip-value tables, ~20-hours-per-test war stories.
