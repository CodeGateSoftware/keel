# Engine Validation & Trade-Simulation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Simulate the deterministic engine over ~5yr of local historical data (per-rule edge test + realistic $500/mo-contribution dollar account), compare risk-adjusted return vs a DCA benchmark, and emit a GO-LIVE/TRAIN-MORE report with a deterministic "lacked-information" training backlog.

**Architecture:** New offline modules under `keel/` (`data/history.py`, `sim/*`). The live engine/executor/rails/ledger stay stdlib + `Decimal` + deterministic and are NOT imported by nor modified for analytics. The simulator drives the real `strategy/engine.evaluate()` and reuses `execution/sizing`; the account mirrors `execution/guards` cap arithmetic with a parity test. Read-only against Coinbase (candles only).

**Tech Stack:** Python 3.12, stdlib only (`decimal`, `dataclasses`, `sqlite3`, `time`, `datetime`), `click` (CLI), `pytest`, `ruff`. NO NumPy/Pandas/Matplotlib/statsmodels (declined — spec §10).

## Global Constraints

- **`Decimal` only** for money/prices/returns — never `float`. Use `Decimal.sqrt()` and iterative `Decimal` recurrences for stdev/EWMA.
- **stdlib-only** — no new runtime dependencies.
- **No lookahead** — the simulator may only read candles with `ts <= t`.
- **Read-only** — this harness never places an order; no authz/passphrase gate.
- **Long-only spot**, halal: `rf = 0` in all ratios (rf>0 = riba).
- Spec: `docs/superpowers/specs/2026-07-17-engine-validation-simulation-design.md`.
- Reuse, don't reimplement: `strategy/backtest.backtest`, `strategy/stats.summarize`, `strategy/engine.evaluate`, `execution/sizing.size`/`dca_size`, `data/repository` candle methods.
- TDD every task: failing test → run (fail) → minimal impl → run (pass) → commit. Commits `Refs #n`/final `Closes #n` + `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`. Stage files individually.
- Whole suite `uv run pytest` green + `uv run ruff check` clean before each PR.

**Wave order (worktree parallelism):** Wave A = Tasks 1,2,3,4 (independent). Wave B = Task 5 (needs 3). Wave C = Task 6 (needs 2). Wave D = Task 7 (needs 3,5,6). Wave E = Task 8 (needs 1,5,6,7). Wave F = Task 9 (needs 7).

---

### Task 1: Paginated 5yr history fetch — `keel/data/history.py`

**Files:**
- Create: `keel/data/history.py`
- Test: `tests/data/test_history.py`

**Interfaces:**
- Consumes: `CoinbaseClient.get_candles(product_id, granularity, start, end) -> list[Candle]` (epoch-seconds ints, ascending); `Repository.upsert_candles(product_id, granularity, list[Candle]) -> int`; `Repository.get_candles(product_id, granularity, start_ts, end_ts) -> list[Candle]`.
- Produces:
  ```python
  GRANULARITY_SECONDS: dict[Granularity, int]  # ONE_HOUR:3600, ONE_DAY:86400, FIFTEEN_MINUTE:900, ...
  MAX_CANDLES_PER_REQUEST = 300  # conservative under Coinbase's ~350 cap

  @dataclass(frozen=True)
  class CoverageInfo:
      product: str
      granularity: Granularity
      first_ts: int | None
      last_ts: int | None
      n_candles: int
      requested_start_ts: int
      gaps: int

  def ensure_history(client, repo, products: list[str], granularities: list[Granularity],
                     years: int, now_ts: int, sleep_fn=lambda s: None,
                     sleep_sec: float = 0.2, refresh: bool = False,
                     ) -> dict[tuple[str, Granularity], CoverageInfo]
  def coverage(repo, product: str, granularity: Granularity, requested_start_ts: int) -> CoverageInfo
  ```
  `sleep_fn` is injected (defaults to a no-op in tests; the CLI passes `time.sleep`) so tests never actually sleep.

- [ ] **Step 1: Write failing tests**

```python
# tests/data/test_history.py
from decimal import Decimal
from keel.data.db import connect
from keel.data.repository import Repository
from keel.data.history import ensure_history, coverage, GRANULARITY_SECONDS, MAX_CANDLES_PER_REQUEST
from keel.types import Candle, Granularity

class FakeClient:
    """Serves candles from an in-memory dict keyed by product; honors [start,end]."""
    def __init__(self, series: dict[str, list[Candle]]):
        self.series = series
        self.calls: list[tuple] = []
    def get_candles(self, product_id, granularity, start, end):
        self.calls.append((product_id, granularity, start, end))
        return [c for c in self.series.get(product_id, []) if start <= c.ts <= end]

def _mk(ts): return Candle(ts=ts, open=Decimal(1), high=Decimal(1), low=Decimal(1), close=Decimal(1), volume=Decimal(1))

def test_paginates_backward_and_caches_all():
    step = GRANULARITY_SECONDS[Granularity.ONE_HOUR]
    now = 1_000_000 * step
    full = [_mk(now - i * step) for i in range(1000)]  # 1000 hourly candles
    client = FakeClient({"BTC-USD": full})
    repo = Repository(connect(":memory:"))
    cov = ensure_history(client, repo, ["BTC-USD"], [Granularity.ONE_HOUR], years=1, now_ts=now)
    stored = repo.get_candles("BTC-USD", Granularity.ONE_HOUR)
    # every candle within the 1yr window got cached, de-duplicated, ascending
    assert len(stored) == len({c.ts for c in full if c.ts >= now - GRANULARITY_SECONDS[Granularity.ONE_HOUR]*24*365})
    assert stored == sorted(stored, key=lambda c: c.ts)
    assert cov[("BTC-USD", Granularity.ONE_HOUR)].n_candles == len(stored)

def test_stops_at_inception_on_empty_window():
    step = GRANULARITY_SECONDS[Granularity.ONE_HOUR]
    now = 500 * step
    # only 50 candles exist (asset "born" recently)
    born = [_mk(now - i * step) for i in range(50)]
    client = FakeClient({"NEW-USD": born})
    repo = Repository(connect(":memory:"))
    ensure_history(client, repo, ["NEW-USD"], [Granularity.ONE_HOUR], years=5, now_ts=now)
    # once a backward window returns empty, it stops (bounded number of calls, not 5yr worth)
    assert len(client.calls) < 5  # 50 candles => ~1 full window + 1 empty
    assert repo.get_candles("NEW-USD", Granularity.ONE_HOUR)  # got what existed

def test_idempotent_resume_only_fetches_missing():
    step = GRANULARITY_SECONDS[Granularity.ONE_HOUR]
    now = 2000 * step
    full = [_mk(now - i * step) for i in range(200)]
    client = FakeClient({"BTC-USD": full})
    repo = Repository(connect(":memory:"))
    ensure_history(client, repo, ["BTC-USD"], [Granularity.ONE_HOUR], years=1, now_ts=now)
    calls_first = len(client.calls)
    ensure_history(client, repo, ["BTC-USD"], [Granularity.ONE_HOUR], years=1, now_ts=now)
    # second run already fully cached => far fewer (ideally 0-1) new fetches
    assert len(client.calls) - calls_first <= 1
```

- [ ] **Step 2: Run tests, verify they fail** — `uv run pytest tests/data/test_history.py -v` (ImportError / not defined).

- [ ] **Step 3: Implement `history.py`.** Algorithm for `ensure_history`, per (product, granularity):
  - `step = GRANULARITY_SECONDS[gran]`; `start_floor = now_ts - years*365*86400`.
  - If not `refresh`: query existing `repo.get_candles(product, gran)`; let `earliest_cached` = min ts (or None). Fetch backward only for the region below `earliest_cached` and forward for the region above `latest_cached` (missing recent bars). Simplest correct approach: compute the set of already-cached ts; page backward from `now_ts`, skipping windows fully covered.
  - Backward loop: `window_end = now_ts`; while `window_end > start_floor`: `window_start = max(start_floor, window_end - MAX_CANDLES_PER_REQUEST*step)`; `batch = client.get_candles(product, gran, window_start, window_end)`; if `batch` empty → break (inception); `repo.upsert_candles(product, gran, batch)`; `sleep_fn(sleep_sec)`; `window_end = window_start - step`.
  - Return `coverage(repo, product, gran, start_floor)` per pair.
  - `coverage()`: read cached candles in `[start_floor, now]`, compute first/last ts, count, and `gaps = expected_bars - actual` where `expected = (last-first)//step + 1` (guard div-by-zero / empty).

- [ ] **Step 4: Run tests, verify pass.**

- [ ] **Step 5: Commit** — `git add keel/data/history.py tests/data/test_history.py && git commit -m "Add paginated 5yr candle history fetch (Refs #<n>) ..."`

---

### Task 2: Simulation account ledger — `keel/sim/account.py`

**Files:**
- Create: `keel/sim/__init__.py` (empty), `keel/sim/account.py`
- Test: `tests/sim/test_account.py`

**Interfaces:**
- Consumes: `Config` (`caps`, `subscription`, `quote_currency`, `dca`), `execution.guards` (for the parity test only — `guards.check`, `OrderIntent`), `execution.sizing`.
- Produces:
  ```python
  @dataclass
  class OpenPosition:
      asset: str; qty: Decimal; entry_fill: Decimal; entry_ts: int; stop: Decimal | None; rule_kind: str

  @dataclass
  class OpenIntent:  # sim-local candidate order (pre-cap)
      asset: str; qty: Decimal; entry: Decimal; stop: Decimal | None; notional: Decimal; is_dca: bool; rule_kind: str

  class SimAccount:
      def __init__(self, fee_pct: Decimal, slippage_pct: Decimal): ...
      cash_usdc: Decimal          # starts 0; grows via deposit()
      positions: dict[str, OpenPosition]
      contributed: Decimal
      realized_pnl: Decimal
      def deposit(self, amount: Decimal, now_ts: int) -> None      # += cash/contributed; reset month/day counters on rollover
      def can_open(self, intent: OpenIntent, config, now_ts: int) -> tuple[bool, list[str]]
      def open(self, intent: OpenIntent, fill_price: Decimal, now_ts: int) -> None
      def close(self, asset: str, fill_price: Decimal, now_ts: int) -> Decimal   # realized pnl
      def exposure_usd(self, prices: dict[str, Decimal]) -> Decimal
      def mark_to_market(self, prices: dict[str, Decimal]) -> Decimal            # cash + Σ qty*price
  ```

**Cap arithmetic (mirror `guards.check`, spend-caps subset only):** per-order (`notional <= caps.max_per_order_usd`); per-day (`day_spend + notional <= caps.max_per_day_usd`); exposure (`current_exposure + notional <= caps.max_exposure_usd`); per-asset (`asset_exposure + notional <= caps.max_per_asset_pct * caps.max_exposure_usd`); USDC-funding (`cash_usdc > 0 AND cash_usdc >= notional`); monthly-allowance (`month_spend + notional <= subscription.monthly_allowance_usd`, plus `even_daily` pacing when configured). DCA is exempt from none of these (matches rail 14).

- [ ] **Step 1: Write failing tests** — one per cap + deposit/rollover + open/close pnl + the parity test:

```python
# tests/sim/test_account.py (excerpt — write all caps)
def test_usdc_funding_blocks_when_cash_below_notional(sim_config):
    acc = SimAccount(fee_pct=Decimal("0.006"), slippage_pct=Decimal("0.0005"))
    acc.deposit(Decimal("50"), now_ts=DAY0)
    ok, reasons = acc.can_open(_intent(notional=Decimal("100")), sim_config, DAY0)
    assert not ok and any("usdc" in r.lower() for r in reasons)

def test_monthly_allowance_caps_cumulative_buys(sim_config):  # sim_config.subscription.monthly_allowance_usd == 500
    acc = SimAccount(Decimal("0"), Decimal("0"))
    acc.deposit(Decimal("100000"), DAY0)  # plenty of cash; allowance is the binding cap
    acc.open(_intent(notional=Decimal("450")), fill_price=Decimal("10"), now_ts=DAY0)
    ok, reasons = acc.can_open(_intent(notional=Decimal("100")), sim_config, DAY0)
    assert not ok and any("allowance" in r.lower() for r in reasons)

def test_open_then_close_realizes_pnl_net_of_fees(sim_config):
    acc = SimAccount(Decimal("0.006"), Decimal("0.0005")); acc.deposit(Decimal("1000"), DAY0)
    acc.open(_intent(asset="BTC", qty=Decimal("1"), entry=Decimal("100"), notional=Decimal("100")), Decimal("100"), DAY0)
    pnl = acc.close("BTC", fill_price=Decimal("120"), now_ts=DAY0)
    assert pnl > 0 and "BTC" not in acc.positions

def test_deposit_resets_month_spend_on_month_rollover(sim_config):
    acc = SimAccount(Decimal("0"), Decimal("0")); acc.deposit(Decimal("100000"), JAN15)
    acc.open(_intent(notional=Decimal("500")), Decimal("10"), JAN15)     # month maxed
    assert not acc.can_open(_intent(notional=Decimal("1")), sim_config, JAN20)[0]
    acc.deposit(Decimal("500"), FEB01)                                    # new month
    assert acc.can_open(_intent(notional=Decimal("1")), sim_config, FEB01)[0]

def test_parity_with_guards_check(sim_config):
    """account.can_open must AGREE with guards.check on the same spend-cap scenario."""
    # Build equivalent repo state (orders + subscription) and a guards.OrderIntent, assert
    # both verdicts match across a grid of notionals spanning each cap boundary.
    ...
```

- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement `SimAccount`.** Track `month_spend`/`day_spend` with the current UTC month/day key (reuse `guards._utc_month_bounds`/`_utc_day_bounds` semantics — import or replicate the 1st-of-month/day boundary logic). `can_open` collects all violated caps (no short-circuit), mirroring `guards.check`. `open` debits `notional + entry_fee` from cash, records `OpenPosition`, increments `day_spend`/`month_spend`. `close` credits `qty*exit_fill - exit_fee`, adds to `realized_pnl`. Fees/slippage per `strategy/backtest`'s convention (`entry_fill = price*(1+slippage)`, `exit_fill = price*(1-slippage)`, `fee = fill*qty*fee_pct`).
- [ ] **Step 4: Run, verify pass** (incl. parity test).
- [ ] **Step 5: Commit.**

---

### Task 3: Hand-rolled Decimal financial metrics — `keel/sim/metrics.py`

**Files:**
- Create: `keel/sim/metrics.py`
- Test: `tests/sim/test_metrics.py`

**Interfaces (Produces):**
```python
def daily_returns(equity_curve: list[tuple[int, Decimal]]) -> list[Decimal]
def cumulative_returns(equity_curve: list[tuple[int, Decimal]]) -> list[Decimal]
def volatility(returns: list[Decimal]) -> Decimal                 # sample stdev, Decimal.sqrt
def ewma_volatility(returns: list[Decimal], lam: Decimal = Decimal("0.94")) -> Decimal
def sharpe(returns: list[Decimal], periods_per_year: int = 365) -> Decimal   # rf=0, annualized
def sortino(returns: list[Decimal], periods_per_year: int = 365) -> Decimal  # rf=0, downside dev
def max_drawdown_pct(equity_curve: list[tuple[int, Decimal]]) -> Decimal
def irr(cashflows: list[tuple[int, Decimal]], ending_value: Decimal) -> Decimal   # money-weighted, bisection on Decimal
def cagr_money_weighted(cashflows, ending_value, start_ts, end_ts) -> Decimal
def return_per_drawdown(total_return_pct: Decimal, max_dd_pct: Decimal) -> Decimal
```

- [ ] **Step 1: Write failing tests** with hand-computed fixtures:

```python
def test_daily_returns_simple():
    ec = [(0, Decimal("100")), (1, Decimal("110")), (2, Decimal("99"))]
    assert daily_returns(ec) == [Decimal("0.1"), Decimal("-0.1")]

def test_max_drawdown_pct():
    ec = [(0, Decimal("100")), (1, Decimal("120")), (2, Decimal("90")), (3, Decimal("110"))]
    assert max_drawdown_pct(ec) == Decimal("0.25")  # 120 -> 90

def test_sharpe_zero_when_flat():
    assert sharpe([Decimal("0"), Decimal("0"), Decimal("0")]) == Decimal("0")

def test_sortino_ignores_upside_vol():
    # a series with big positive spikes but tiny downside has Sortino > Sharpe
    r = [Decimal("0.2"), Decimal("-0.01"), Decimal("0.2"), Decimal("-0.01")]
    assert sortino(r) > sharpe(r)

def test_irr_recovers_known_rate():
    # $100 in at t0, ends $121 after 2 periods => ~10%/period
    rate = irr([(0, Decimal("-100"))], ending_value=Decimal("121"))
    assert abs(rate - Decimal("0.1")) < Decimal("0.01")
```

- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** — pure `Decimal`. `volatility`: sample stdev via `Decimal.sqrt()`. `sharpe`: `mean/stdev * sqrt(periods_per_year)` (0 if stdev 0). `sortino`: same but denominator = downside deviation (stdev of negative returns only). `irr`: bisection over rate in a bracketed range solving `Σ cf_i/(1+r)^{t_i} + ending/(1+r)^{T} = 0` using `Decimal` power via integer-exponent loop or `Decimal` `** `; bound iterations, return best. `ewma_volatility`: iterative `var = lam*var + (1-lam)*r^2` recurrence, return `sqrt(var)`.
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit.**

---

### Task 4: Raise promotion floors to canonical values — `config.yaml` + `keel/config.py`

**Files:**
- Modify: `config.yaml` (the `promotion` block), `keel/config.py` (the `PromotionConfig` defaults + `load_config` fallbacks at lines ~68/236/242)
- Test: `tests/test_config.py`

**Interfaces:** No signature change — only the default *values* move from Phase-1 placeholders to the canonical proving-gate floors (spec §6.2 / main-spec §11): `min_trades: 100`, `min_win_rate: 0.55`, `min_rr: 1.5`, `min_expectancy: 0`.

- [ ] **Step 1: Update the failing test first** — change/add `tests/test_config.py` assertions to expect the canonical defaults:

```python
def test_promotion_defaults_are_canonical_proving_floors():
    cfg = load_config(<minimal config without a promotion block>)
    assert cfg.promotion.min_trades == 100
    assert cfg.promotion.min_win_rate == Decimal("0.55")
    assert cfg.promotion.min_rr == Decimal("1.5")
    assert cfg.promotion.min_expectancy == Decimal("0")
```

- [ ] **Step 2: Run, verify the new assertion fails** (currently 30 / 0.4).
- [ ] **Step 3: Edit** `config.yaml` promotion block → `min_trades: 100`, `min_win_rate: 0.55` (keep `min_rr: 1.5`, `min_expectancy: 0`); edit `keel/config.py` `PromotionConfig` field defaults and the three `promotion_raw.get(...)` fallbacks to match. **Check** whether any existing promotion/paper tests seeded fewer than 100 trades and relied on promotion passing — if so, update those fixtures to seed ≥100 trades (they were testing the gate mechanic, not the specific number).
- [ ] **Step 4: Run the whole suite** — `uv run pytest` — fix any test that assumed the old looser floors.
- [ ] **Step 5: Commit** — `"Raise promotion floors to canonical proving-gate values (Refs #<n>)"`.

---

### Task 5: DCA benchmarks — `keel/sim/benchmark.py`

**Files:**
- Create: `keel/sim/benchmark.py`
- Test: `tests/sim/test_benchmark.py`

**Interfaces:**
- Consumes: Task 3 `metrics.*`; `Config.target_weights`; per-asset daily candle series (close prices) for valuation.
- Produces:
  ```python
  @dataclass
  class BenchmarkResult:
      name: str
      equity_curve: list[tuple[int, Decimal]]
      contributions: list[tuple[int, Decimal]]
      ending_value: Decimal
      total_return_pct: Decimal
      max_drawdown_pct: Decimal
      sharpe: Decimal
      sortino: Decimal
      return_per_drawdown: Decimal

  def dca_into_allowlist(prices_by_asset, target_weights, monthly_contribution, months, fee_pct, slippage_pct) -> BenchmarkResult
  def dca_into_btc(prices_by_asset, monthly_contribution, months, fee_pct, slippage_pct) -> BenchmarkResult
  ```
  `prices_by_asset: dict[str, list[tuple[int, Decimal]]]` = daily (ts, close). Each month, spend `$contribution` split by weights, buy at that day's close (+slippage, −fee), accumulate qty, never sell. Sample the equity curve daily via mark-to-market; feed to `metrics.*`.

- [ ] **Step 1: Write failing tests**:

```python
def test_dca_into_btc_holds_all_contributions():
    # flat price => ending value == contributed minus fees; DD ~ 0
    ...
def test_dca_into_allowlist_splits_by_weights():
    # 40/30/30, one month, known prices => known per-asset qty
    ...
def test_benchmark_metrics_populated():
    r = dca_into_btc(...); assert r.max_drawdown_pct >= 0 and r.ending_value > 0
```

- [ ] **Step 2–4:** Run-fail → implement (monthly loop buying at close, daily mark-to-market, metrics) → run-pass.
- [ ] **Step 5: Commit.**

---

### Task 6: The portfolio simulator — `keel/sim/portfolio_sim.py`

**Files:**
- Create: `keel/sim/portfolio_sim.py`
- Test: `tests/sim/test_portfolio_sim.py`

**Interfaces:**
- Consumes: Task 2 `SimAccount`/`OpenIntent`; `strategy/engine.evaluate(rules, candles_by_tf, ...)`; `strategy/rules/base` (`Rule`, `Signal`, `Action`, `Setup`); `execution/sizing.size`/`dca_size`; `Config`.
- Produces:
  ```python
  @dataclass
  class SimTrade:
      asset: str; entry_ts: int; exit_ts: int | None; entry: Decimal; exit: Decimal | None
      qty: Decimal; pnl: Decimal | None; r_multiple: Decimal | None; mfe: Decimal; mae: Decimal
      outcome: str; rule_kind: str; cts_score: int; entry_technique: str

  @dataclass
  class SimTelemetry:  # feeds gap analysis (Task 7)
      bars: int
      signals_emitted: int
      idle_spans: list[tuple[int, int, str, Decimal]]        # (start_ts, end_ts, asset, benchmark_move_pct)
      cts_factor_populated: dict[str, int]                   # per CTS context key: count of non-default
      rejected_for_missing_input: dict[str, int]             # input key -> count of would-be setups blocked
      per_bucket_pnl: dict[tuple[str, str, str], Decimal]    # (rule, asset, regime) -> summed pnl
      mae_samples: list[Decimal]; mfe_giveback_samples: list[Decimal]

  @dataclass
  class SimResult:
      trades: list[SimTrade]
      equity_curve: list[tuple[int, Decimal]]
      contributions: list[tuple[int, Decimal]]
      coverage: dict            # passthrough from history
      telemetry: SimTelemetry

  WINDOW_BARS = 300

  def run(rules, candles_by_asset: dict[str, dict[Granularity, list[Candle]]], config,
          start_ts: int, end_ts: int, monthly_contribution: Decimal,
          fee_pct: Decimal = Decimal("0.006"), slippage_pct: Decimal = Decimal("0.0005")) -> SimResult
  ```

**Loop algorithm:**
1. Build the ascending union of ONE_HOUR timestamps across assets in `[start_ts, end_ts]`.
2. `account = SimAccount(fee_pct, slippage_pct)`; track `last_month_key`.
3. For each `t` (and, per asset, index `i` of its 1h bar at `t`):
   - **Contribution:** if `t`'s UTC month != `last_month_key`: `account.deposit(monthly_contribution, t)`; record in `contributions`; `last_month_key = month`.
   - **Per asset:**
     - `window = {ONE_HOUR: hourly[max(0,i-WINDOW_BARS+1):i+1], ONE_DAY: daily bars with ts <= t}` (slice; **assert all ts <= t** in a debug/test hook).
     - **If holding:** resolve stop/target against `hourly[i]` (reuse `backtest`'s conservative `_touches`/intrabar rule; import the helper or replicate: no finer series ⇒ stop-vs-target ambiguity ⇒ stop). Also `rule.exit_signal(held.setup, window)`. On exit: `pnl = account.close(asset, exit_fill, t)`; append `SimTrade`; update telemetry buckets/MAE/MFE-giveback.
     - **If flat:** `signals = engine.evaluate([rule_for_or_all_rules], window, ...)`. For each ENTER signal: size via `sizing.size(equity, config.risk_pct, entry, stop)` (or `dca_size` for DCA class); build `OpenIntent`; `ok, reasons = account.can_open(intent, config, t)`. If ok: fill at **next** hourly bar's open (`hourly[i+1].open` if exists, else skip) + slippage; `account.open(...)`; stash held `Setup`. Record telemetry: increment `cts_factor_populated` for each non-default CTS context key; if `not ok` purely due to a missing/defaulted confluence input, increment `rejected_for_missing_input`.
   - **Daily equity sample:** when the UTC day changes, append `(t, account.mark_to_market(latest_closes))` to `equity_curve`.
4. **Idle-span telemetry:** track the last `t` any signal fired per asset; when a gap exceeds a threshold span AND the asset's price moved > `MOVE_THRESHOLD_PCT` across it, record an `idle_span`.
5. Return `SimResult`.

- [ ] **Step 1: Write failing tests** with a tiny deterministic rule + synthetic candles:

```python
def test_no_lookahead_window_only_past():
    # a spy Rule asserts every candle it sees has ts <= the current bar ts
    ...
def test_one_position_per_asset():
    # a rule that fires every bar yields non-overlapping trades for one asset
    ...
def test_exit_on_stop_and_on_target():
    # craft candles so a held position hits target (win) then another hits stop (loss)
    ...
def test_monthly_contribution_deposited_each_month():
    res = run(...); assert len(res.contributions) == expected_months
def test_records_idle_span_when_no_rule_fires_through_a_move():
    # a never-firing rule + a large price move => one idle_span in telemetry
    ...
```

- [ ] **Step 2–4:** Run-fail → implement loop → run-pass (keep the WINDOW slice + no-lookahead assertion behind a cheap check).
- [ ] **Step 5: Commit.**

---

### Task 7: Report, verdict & gap analysis — `keel/sim/report.py`

**Files:**
- Create: `keel/sim/report.py`
- Test: `tests/sim/test_report.py`

**Interfaces:**
- Consumes: Task 6 `SimResult`/`SimTelemetry`; Task 5 `BenchmarkResult`; Task 3 `metrics.*`; `strategy/stats.summarize` + `strategy/backtest.backtest` for the edge table; `strategy/promotion.PromotionConfig`/`can_promote`.
- Produces:
  ```python
  @dataclass
  class Verdict:
      status: str                 # "GO-LIVE candidate" | "TRAIN MORE"
      reasons: list[str]          # failing-gate reasons (empty on GO-LIVE)
      data_sufficient: bool
      g2_pass: bool; g3_pass: bool

  @dataclass
  class GapItem:
      kind: str; evidence: str; recommendation: str

  def edge_table(rules, candles_by_asset, fee_pct, slippage_pct) -> dict[str, BacktestResult]  # per-rule + "__pooled__"
  def build_verdict(pooled: BacktestResult, account_metrics: dict, benchmark: BenchmarkResult,
                    coverage, promotion_cfg) -> Verdict
  def analyze_gaps(telemetry: SimTelemetry, coverage, move_threshold_pct: Decimal) -> list[GapItem]
  def render_markdown(sim: SimResult, edge: dict, account_metrics: dict, benchmark: BenchmarkResult,
                      verdict: Verdict, gaps: list[GapItem], in_sample: bool = True) -> str
  ```

**Verdict gates (spec §6.2):** G1 data_sufficient = every included asset has ≥ some min bars (assets under it flagged/excluded). G2 = `can_promote(pooled, promotion_cfg)` (expectancy>0, R:R≥1.5, win≥0.55, n≥100). G3 = `sim.return_per_drawdown >= benchmark.return_per_drawdown AND sim.sortino >= benchmark.sortino`, OR (`sim.total_return_pct >= k*benchmark` at materially lower `max_drawdown_pct`). GO-LIVE iff data_sufficient AND G2 AND G3.

**Gap detectors (spec §6.1)** — each yields `GapItem`s from telemetry: idle-through-moves (from `idle_spans`), unfed CTS factors (keys with `cts_factor_populated[k] == 0`), would-have-traded (`rejected_for_missing_input`), data-coverage limits (from `coverage`: partial history / no 15m), trade-management (mean `mae_samples` / `mfe_giveback_samples` beyond a threshold), losing buckets (`per_bucket_pnl` entries with negative sums).

- [ ] **Step 1: Write failing tests** — construct telemetry/results to fire each detector and each verdict branch:

```python
def test_gap_flags_unfed_cts_factor():
    tel = SimTelemetry(...); tel.cts_factor_populated = {"seasonality": 0, "in_pullback": 42, ...}
    gaps = analyze_gaps(tel, coverage={}, move_threshold_pct=Decimal("0.1"))
    assert any(g.kind == "unfed_cts_factor" and "seasonality" in g.evidence for g in gaps)

def test_verdict_train_more_when_floor_fails():
    pooled = _result(n_trades=200, win_rate=0.50, ...)  # win<0.55
    v = build_verdict(pooled, account_metrics=..., benchmark=..., coverage=..., promotion_cfg=CANONICAL)
    assert v.status == "TRAIN MORE" and any("win" in r for r in v.reasons)

def test_verdict_go_live_when_floors_and_risk_adjusted_edge():
    ...
def test_render_markdown_has_all_sections():
    md = render_markdown(...); 
    for h in ["Verdict","Data coverage","Edge","Account","Benchmark","gaps","Caveats"]:
        assert h.lower() in md.lower()
    assert "IN-SAMPLE" in md
```

- [ ] **Step 2–4:** Run-fail → implement → run-pass.
- [ ] **Step 5: Commit.**

---

### Task 8: CLI wiring — `keel simulate` (`keel/cli.py`)

**Files:**
- Modify: `keel/cli.py` (add the `simulate` command)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: Task 1 `ensure_history`; Task 6 `portfolio_sim.run`; Task 5 benchmarks; Task 7 report fns; existing rule registry (`agent.RULE_REGISTRY` / rules from repo) and `config`.
- Produces the command:
  ```
  keel simulate [--years 5] [--products BTC-USD,ETH-USD,PAXG-USD] [--contribution 500]
                [--out PATH] [--artifact] [--refresh] [--no-fetch]
  ```
  Read-only; no authz gate. Flow: build client (unless `--no-fetch`), `ensure_history` → load candles per asset from repo into `candles_by_asset` → `edge_table` → `portfolio_sim.run` → benchmarks → verdict + gaps → `render_markdown` → write `--out` (default `docs/superpowers/reports/<date-from-now_ts>-engine-validation.md`; date passed in, never `datetime.now()` in library code) → print verdict summary. `--artifact` deferred to Task 9 (flag parsed, prints "artifact: run Task 9").

- [ ] **Step 1: Write failing test** — `keel simulate --no-fetch` over a seeded in-memory DB (candles pre-loaded) produces a report file + prints a verdict; assert no network (inject a client that raises if called):

```python
def test_simulate_no_fetch_produces_report(tmp_path, seeded_repo):
    result = runner.invoke(cli, ["simulate","--no-fetch","--out",str(tmp_path/"r.md")])
    assert result.exit_code == 0
    assert (tmp_path/"r.md").read_text().lower().count("verdict") >= 1
```

- [ ] **Step 2–4:** Run-fail → implement command (`now_ts` from `int(time.time())` at the CLI boundary only) → run-pass.
- [ ] **Step 5: Commit** — `"Add keel simulate command (Closes #<n>)"`.

---

### Task 9: HTML Artifact (inline-SVG visuals) — additive

**Files:**
- Create: `keel/sim/artifact.py`
- Modify: `keel/cli.py` (wire `--artifact`)
- Test: `tests/sim/test_artifact.py`

**Interfaces (Produces):**
```python
def render_html(sim: SimResult, benchmark: BenchmarkResult, verdict: Verdict, gaps, in_sample=True) -> str
def _svg_line(series: list[tuple[int, Decimal]], ...) -> str       # hand-emitted inline SVG
def _svg_drawdown(equity_curve) -> str
```
Self-contained HTML: no external requests, all CSS inline, charts as inline `<svg>`. Equity curve vs benchmark, underwater drawdown plot, per-asset P&L bars. CLI `--artifact` writes the HTML next to the Markdown; publishing to an Artifact URL is a manual follow-up (the CLI just emits the file).

- [ ] **Step 1: Write failing test** — `render_html(...)` returns a string containing `<svg`, no `http://`/`https://` external refs, and the verdict text.
- [ ] **Step 2–4:** Run-fail → implement hand-emitted SVG (map ts→x, value→y with Decimal→float only at the pixel-coordinate boundary, never for money) → run-pass.
- [ ] **Step 5: Commit** — `"Add HTML/SVG simulation artifact (Closes #<n>)"`.

---

## Self-Review

**Spec coverage:** §2 modules → Tasks 1–9 (history, account, metrics, benchmark, portfolio_sim, report, cli, artifact). §3 pagination → Task 1. §4 account+sim → Tasks 2,6. §5 metrics+benchmark → Tasks 3,5. §6 report+verdict+gaps → Task 7. §6.2 canonical floors → Task 4. §7 CLI → Task 8. §8 testing → each task's tests. §10 stdlib-only → Global Constraints (no numpy/pandas). Covered.

**Placeholder scan:** every task has concrete file paths, interfaces, representative test code, and named algorithms. Larger orchestration (Task 6/7) gives the loop/gate algorithm + interface signatures + test scenarios rather than every line — appropriate for subagent execution, consistent with the Phase 1–3 plans.

**Type consistency:** `SimResult`/`SimTrade`/`SimTelemetry`/`BenchmarkResult`/`Verdict`/`GapItem`/`CoverageInfo`/`OpenIntent`/`SimAccount` names are used identically across producing and consuming tasks. `metrics.*` signatures in Task 3 match their call sites in Tasks 5/7. `portfolio_sim.run` signature in Task 6 matches Task 8's call. `ensure_history` signature matches Task 1↔8.

## Execution notes
- Reuse `guards._utc_month_bounds`/`_utc_day_bounds` and `backtest`'s intrabar/`_touches` helper rather than re-deriving (import; if private, lift to a shared spot in that task).
- Never call `datetime.now()`/`time.time()` inside library code — pass `now_ts` from the CLI boundary (keeps the sim deterministic + testable).
- Performance: if a full 5yr run is slow, cache the daily-context slice per day and cap `WINDOW_BARS`; acceptable as a one-off.
