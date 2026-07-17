# keel Phase 2 — Rules, CTS Engine, Backtest & Paper Gate — Implementation Plan

> **For agentic workers:** each task is a GitHub issue. Implement in its own git **worktree** on branch
> `feat/<issue#>-<slug>`, **TDD**, commits referencing the issue (`Refs #n`/`Closes #n`), push, open a **PR to
> `main`** for human review. **Model policy:** Opus designs (this doc); **Sonnet** codes+tests; **Haiku** for
> pure file I/O. Read the ACTUAL merged modules on `main` for real signatures — do not guess.

**Goal:** Build the strategy layer — a Rule interface + curated rule library (pullback-continuation family,
RSI mean-reversion, DCA), the CTS confluence scorer, the evaluation engine, a backtester (intrabar-resolved,
no-overlap, MFE/MAE), a forward paper-trader, and the promotion/demotion lifecycle — all offline/paper, **no
live orders** (Phase 3). Everything remains **long-only spot, no leverage**; bearish setups are exit/don't-buy filters.

**Architecture:** Builds on Phase 1's `analysis/*` (candles, levels, regime, indicators), `data/repository.py`
(orders/rules/signals/backtests tables), `types.py`. New `strategy/` package. Pure-logic where possible;
`paper.py` reads/writes the DB via `Repository`. Money/prices = `Decimal`; indicator math = `float`.

**Tech Stack:** Python 3.12, uv, stdlib, pytest, ruff (same as Phase 1).

## Global Constraints

- Same as Phase 1 (Decimal money, %/ATR not pips, no network in pure-logic tests, TDD, ruff-clean, focused files).
- **Long-only:** entries are BUY; exits are SELL of a held position. No shorts, ever.
- **No live order placement** in Phase 2 — paper only (`orders.mode='paper'`).
- **Rails are NOT enforced here** (that's Phase 3 `execution/guards.py`); the engine emits *intents*, execution enforces.
- **Shared strategy types live in `keel/strategy/rules/base.py`** (Task 1) — all rules/engine/backtest import from there.
- Import Phase-1 types from `keel.types`; analysis from `keel.analysis.*`; persistence from `keel.data.repository`.

**Dependency waves:**
- **Wave A (first, blocks all):** Task 1 (`strategy/rules/base.py` — Rule interface + Signal/Trade/Setup types). Merge before Wave B.
- **Wave B (parallel, after Task 1):** Task 2 (CTS scorer), Task 3 (pullback rule), Task 4 (RSI mean-rev rule), Task 5 (DCA rule), Task 6 (backtester). Independent → separate worktrees.
- **Wave C (after Wave B merged):** Task 7 (engine — needs CTS + rules), Task 8 (paper trader — needs engine + repository), Task 9 (promotion/demotion — needs backtest/paper stats).

---

### Task 1 — Strategy interfaces & shared types  `[prerequisite]`

**Files:** Create `keel/strategy/__init__.py`, `keel/strategy/rules/__init__.py`, `keel/strategy/rules/base.py`, `tests/strategy/test_base.py`.

**Interfaces — Produces (everything downstream imports these):**
- `class Action(str, Enum)`: `ENTER, EXIT, NONE`.
- `@dataclass(frozen=True) class Setup`: a *candidate* pre-scoring — `product_id:str`, `direction:Literal["long"]` (v1 long-only), `entry:Decimal`, `stop:Decimal`, `target:Decimal`, `context:dict[str,Any]` (indicator values for explainability + CTS input), `ts:int`. Property `rr:Decimal` = `(target-entry)/(entry-stop)`.
- `@dataclass(frozen=True) class Signal`: the engine's decision — `rule_name:str`, `product_id:str`, `action:Action`, `side:Side`, `setup:Setup|None`, `cts_score:int`, `entry_technique:str`, `ts:int`. (BUY on ENTER; SELL on EXIT.)
- `@dataclass class Trade`: a backtest/paper fill pair — `entry_ts:int, exit_ts:int|None, entry:Decimal, exit:Decimal|None, qty:Decimal, side:Side, pnl:Decimal|None, r_multiple:Decimal|None, mfe:Decimal, mae:Decimal, outcome:Literal["win","loss","open","scratch"]`.
- `class Rule(ABC)` (or `Protocol`): attributes `name:str`, `params:dict`; methods:
  - `detect(self, candles_by_tf: dict[Granularity, list[Candle]]) -> Setup | None` — pure; returns a long entry Setup or None.
  - `exit_signal(self, held: Setup, candles_by_tf) -> bool` — whether to close a held long.
  - `describe(self) -> dict` — name + params (for the `rules` table).

**Steps (TDD):** Write `tests/strategy/test_base.py`: `Setup.rr` computes reward:risk correctly (e.g. entry 100, stop 90, target 120 → rr=2); `Signal`/`Trade` construct with expected fields; a trivial concrete `Rule` subclass (in the test) satisfies the ABC. Implement minimal `base.py`. `uv run pytest` green, `ruff` clean. Commits `Refs #`/`Closes #`; PR to `main`. **This PR sets the strategy contract — review/merge before Wave B.**

**Acceptance:** all downstream tasks can `from keel.strategy.rules.base import Rule, Setup, Signal, Trade, Action`; `Setup.rr` correct; ABC enforced.

---

### Task 2 — CTS confluence scorer  `[wave B]`

**Files:** Create `keel/strategy/indicators_cts.py`, `tests/strategy/test_cts.py`.
**Consumes:** `base.Setup`, `analysis.*`. **Produces:**
- `@dataclass CTSFactor: name:str; points:int; present:bool; detail:str`.
- `@dataclass CTSResult: total:int; factors:list[CTSFactor]`.
- `def score(context:dict, weights:dict[str,int]|None=None) -> CTSResult` — sums points for present confluence factors: condition-aligned, in-pullback, S/R touches≥3, round-number/magnet proximity, deceleration, EMA fan aligned, RSI extreme, RSI divergence, candlestick pattern, Fib confluence, seasonality (weight 0 / off by default in v1). Default `weights` from spec §9.
- `def entry_technique(total:int, low=5, high=8) -> Literal["confirm_3bar","signal_candle","aggressive"]` — the graded ladder (spec §9/§17.1): <low→confirm_3bar, <high→signal_candle, else aggressive.

**Steps (TDD):** a context with 3 present high-value factors scores their sum; absent factors add 0; `entry_technique` returns the right tier at boundary scores. Commits `#2`; PR. **Acceptance:** additive scoring correct; tiers correct at boundaries; weights configurable.

---

### Task 3 — Rule: pullback-continuation family  `[wave B]`

**Files:** Create `keel/strategy/rules/pullback_continuation.py`, `tests/strategy/test_pullback.py`.
**Consumes:** `base.Rule/Setup`, `analysis.{regime,levels,indicators,candles}`. **Produces:** `class PullbackContinuation(Rule)` — parameterized (spec §8): `ema_periods`, `entry_zone` (`ema_touch`|`ema_band`), `signal_patterns`, `buffer_ticks`, `stop_method` (`fixed`|`atr`), `target_method` (`measured_1to1`|`swing`|`fib_ext`). `detect()` implements Identify→Predict→Decide: bullish condition + pullback phase + EMA fan aligned + price in entry zone + a qualifying signal candle → returns a **long** `Setup` (entry = buy-stop above signal high; stop per `stop_method`; target per `target_method`). `exit_signal()` fires on the bearish mirror.

**Steps (TDD):** build a synthetic bullish-pullback candle series that satisfies all gates → `detect` returns a Setup with correct entry/stop/target and rr; a choppy/no-pullback series → None; verify `target_method` variants. Commits `#3`; PR. **Acceptance:** detects the textbook long setup; rejects non-setups; all three target methods produce correct levels.

---

### Task 4 — Rule: RSI mean-reversion  `[wave B]`

**Files:** Create `keel/strategy/rules/rsi_meanrev.py`, `tests/strategy/test_rsi_meanrev.py`.
**Consumes:** `base.Rule/Setup`, `analysis.{indicators,levels,candles}`. **Produces:** `class RsiMeanReversion(Rule)` — params `oversold=20`, `overbought=80`, `require_divergence:bool`, `stop_method`, `target_method`. `detect()`: RSI oversold bounce at support (+ optional bullish divergence) → **long** Setup. `exit_signal()`: RSI overbought = exit held long.

**Steps (TDD):** oversold-at-support series (+divergence) → long Setup; mid-RSI → None; overbought → exit_signal True. Commits `#4`; PR. **Acceptance:** oversold-bounce entry + overbought-exit correct; divergence gate honored.

---

### Task 5 — Rule: DCA / dip-buy backbone  `[wave B]`

**Files:** Create `keel/strategy/rules/dca.py`, `tests/strategy/test_dca.py`.
**Consumes:** `base.Rule/Setup`. **Produces:** `class Dca(Rule)` — params `cadence` (e.g. weekly), `budget_usd`, `dip_bonus_pct` (buy more when down N% from recent high). `detect()`: on a cadence boundary, emit a **market-buy** long Setup sized to `budget_usd` (no stop — accumulation); scales up on dips (spec §8 rule 3, §12.1). Distinct order class; `exit_signal()` always False (DCA doesn't exit on signals). Note: DCA is exempt from the rule-trading DD breaker (enforced later in Phase 3 guards, §12.6) — just document it here.

**Steps (TDD):** on a cadence boundary → a buy Setup for `budget_usd`; off-cadence → None; a deep dip → larger size. Commits `#5`; PR. **Acceptance:** cadence + dip-scaling correct; no exit signals; budget respected.

---

### Task 6 — Backtester  `[wave B]`

**Files:** Create `keel/strategy/backtest.py`, `tests/strategy/test_backtest.py`.
**Consumes:** `base.Rule/Setup/Trade`, `types.Candle`. **Produces:**
- `@dataclass BacktestResult: trades:list[Trade]; n_trades:int; win_rate:float; avg_win:Decimal; avg_loss:Decimal; expectancy:Decimal; profit_factor:Decimal; max_drawdown:Decimal; max_losing_streak:int; avg_mfe:Decimal; avg_mae:Decimal`.
- `def backtest(rule, candles, finer_candles=None, fee_pct=Decimal("0.006"), slippage_pct=Decimal("0.0005")) -> BacktestResult`.
- **Intrabar resolution:** when a single candle's range spans both entry and stop, use `finer_candles` (a finer granularity for that window) to determine which hit first / whether the stop was breached before entry (invalidating the trade), spec §12/§2.3.
- **No overlap:** one open position per instrument; skip signals while in a trade (§20.5).
- Records **MFE/MAE** per trade (§20.2); models fee+slippage on entry and exit.

**Steps (TDD):** a rule + candle series with a known winning trade → expectancy>0, correct win_rate/MFE/MAE; a candle spanning entry+stop with finer_candles showing stop-first → the trade is a loss (or invalidated); overlapping signals don't double-count. Commits `#6`; PR. **Acceptance:** metrics correct; intrabar resolution correct; no-overlap enforced; fees/slippage applied.

---

### Task 7 — Evaluation engine  `[wave C]`

**Files:** Create `keel/strategy/engine.py`, `tests/strategy/test_engine.py`.
**Consumes:** `base`, `indicators_cts` (#2), the rules (#3–#5), `analysis.regime`, `data.repository` (to read `live` rules + write `signals`). **Produces:**
- `def evaluate(rules:list[Rule], candles_by_tf:dict[Granularity,list[Candle]], weights=None) -> list[Signal]` — for each rule: run `detect()`, build the CTS `context`, `score()` it, apply the **kill-zone gate** (reject if rr below floor / setup outside the ≥1:1 band, §17.2) and the choppy-regime gate (§1.2), pick the `entry_technique` from the CTS tier, and emit a `Signal` (writing to `signals` when a repo is provided). Multi-timeframe: higher-TF bias gate + trading-TF trigger (§3.2).

**Steps (TDD):** a rule that detects + high CTS → an `aggressive` ENTER Signal with the right score; low CTS → `confirm_3bar`; a sub-floor rr → no Signal (kill-zone reject); choppy regime → no Signal. Commits `#7`; PR. **Acceptance:** CTS→technique mapping, kill-zone + choppy gating, and signal emission all correct.

---

### Task 8 — Paper trader  `[wave C]`

**Files:** Create `keel/strategy/paper.py`, `tests/strategy/test_paper.py`.
**Consumes:** `engine` (#7), `data.repository` (orders table). **Produces:**
- `class PaperTrader`: given `Signal`s + candle data, simulate fills (entry, stop, target, MFE/MAE) and **write `orders(mode='paper')`** via `Repository`; track an open paper position per instrument (no overlap). `def track_record(repo, rule_name) -> BacktestResult`-shaped stats from paper orders.

**Steps (TDD):** feeding an ENTER signal then price hitting target writes two paper orders (entry+exit) with correct pnl; `track_record` aggregates paper trades into stats. Uses `Repository(connect(":memory:"))`. Commits `#8`; PR. **Acceptance:** paper fills logged to DB (mode=paper); track-record stats correct; no live orders.

---

### Task 9 — Promotion / demotion lifecycle  `[wave C]`

**Files:** Create `keel/strategy/promotion.py`, `tests/strategy/test_promotion.py`.
**Consumes:** `backtest.BacktestResult` (#6), paper `track_record` (#8), `data.repository` (rules table). **Produces:**
- `@dataclass PromotionConfig: min_trades=100; min_expectancy=Decimal("0"); min_rr=Decimal("1.5"); min_win_rate=0.55`.
- `def can_promote(stats, cfg) -> tuple[bool,list[str]]` — passes only if expectancy>0 AND rr≥min AND win≥min AND n_trades≥min; returns reasons on fail.
- `def should_demote(rolling_stats, cfg) -> bool` — a `live` rule whose rolling stats drop below floor (§6.3/§20.7).
- `def transition(repo, rule_name, stats, cfg) -> str` — updates `rules.status` (candidate→paper→live→disabled) and returns the new status.

**Steps (TDD):** stats meeting all floors → `can_promote` True; failing win-rate → False with a reason; below-floor rolling stats → `should_demote` True; `transition` updates the rules row. Commits `#9`; PR. **Acceptance:** gates enforce all four floors + min-sample; demotion triggers correctly; status transitions persisted.

---

## Self-Review

**Spec coverage (Phase 2, §8/§9/§11/§12):** Rule interface + parameterized pullback family (T1,T3 ✓), RSI mean-rev (T4 ✓), DCA backbone incl. DD-breaker-exemption note (T5 ✓), CTS scoring + graded entry techniques + kill-zone + multi-TF (T2,T7 ✓), backtester with intrabar resolution + no-overlap + MFE/MAE + fees/slippage (T6 ✓), paper gate (T8 ✓), promotion + demotion + floors/min-sample (T9 ✓). Execution/rails = Phase 3 (correctly out of scope; engine emits intents only).

**Placeholder scan:** none; all tasks have exact signatures + acceptance. Default weights/floors reference spec §9/§4.5 (concrete numbers).

**Type consistency:** `Rule/Setup/Signal/Trade/Action` defined once in `base.py` (T1), consumed everywhere; `BacktestResult` shape produced by T6 and reused by T8's `track_record` and T9's gates; `CTSResult`/`entry_technique` from T2 consumed by T7.

**Note:** interfaces are exact so Wave-B agents stay consistent; each agent must read the merged Phase-1 modules for real analysis/repository signatures and TDD its module to green + ruff-clean.
