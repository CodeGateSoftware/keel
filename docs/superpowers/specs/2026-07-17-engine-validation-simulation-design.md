# Engine Validation & Trade-Simulation — Design Spec

**Status:** Approved design (2026-07-17). Ready for implementation planning.
**Package:** `keel` (halal, long-only, spot-only; no leverage/margin/shorting).
**Goal:** Validate the accuracy and profitability of the deterministic engine — with all its
accumulated rules, patterns, and strategies — by simulating trades over ~5 years of local
historical data, then produce a report of P&L, algorithm accuracy, and a **GO-LIVE vs
TRAIN-MORE** verdict. The report also surfaces, deterministically, where the engine **lacked
information**, as a concrete training backlog to solidify it.

---

## 1. Purpose & decisions

A one-shot (re-runnable) validation harness that answers: *should we trade this engine live, or
train it more first?* It exercises the **real** `engine.evaluate()` + the **real** rail/cap
logic over history, so what passes the simulation is what runs live.

**Locked decisions (from brainstorming — do not re-litigate):**

| Decision | Choice |
|---|---|
| P&L model | **Both**: a sizing-agnostic per-rule *edge test* AND a realistic dollar *account* sim. |
| Over-fitting guard | **Straight full-5yr pass** (single window). Report is explicitly labeled **IN-SAMPLE**; walk-forward/OOS deferred. |
| Success bar | **Risk-adjusted + preservation**: clear the promotion floors, then win on drawdown / risk-adjusted return vs benchmark — not raw return. |
| Data depth | **Hourly + Daily**, ~5yr, per-asset up to whatever Coinbase retains. |
| Funding model | **Monthly contributions**: $500/mo fresh USDC (also the monthly BUY cap); P&L vs total contributed; benchmark gets the same $500/mo. |
| Benchmark | **DCA-into-allowlist** ($500/mo split by `target_weights`, hold) primary; **DCA-into-BTC** secondary. |

**Non-negotiable constraints (inherited):** deterministic (no LLM anywhere in this harness);
`Decimal` for all money/prices (never `float`); stdlib-only (no new dependencies); long-only
spot; read-only against Coinbase (candles only — this harness never places an order).

---

## 2. Architecture & modules

All new code under `keel/`, following existing patterns (stdlib, `Decimal`, dataclasses,
focused single-purpose files).

```
keel/data/history.py          NEW  paginated ~5yr fetch -> local candles cache
keel/sim/__init__.py          NEW
keel/sim/account.py           NEW  dollar-account ledger: cash, positions, caps,
                                   $500/mo allowance + contributions, USDC funding, fees
keel/sim/metrics.py           NEW  pure equity-curve math: sharpe, sortino, max_dd, irr, cagr
keel/sim/portfolio_sim.py     NEW  bar-by-bar simulator: drives engine.evaluate() over 5yr
keel/sim/benchmark.py         NEW  DCA-into-allowlist / DCA-into-BTC benchmarks
keel/sim/report.py            NEW  edge table + account results + gap analysis + verdict
keel/cli.py                   MOD  `keel simulate` command (pull -> run -> report)
keel/strategy/backtest.py     REUSE (unchanged) for the per-rule edge table
keel/execution/sizing.py      REUSE directly (sizing.size / sizing.dca_size)
keel/execution/guards.py      MIRROR the cap arithmetic in account.py, with a parity test
                                   asserting account.can_open agrees with guards.check
docs/superpowers/reports/YYYY-MM-DD-engine-validation.md   OUT  the committed report
```

**Data flow:**

```
keel simulate
  |
  |-- history.ensure_history(products, [1h,1d], years=5)   -- paginated, cached
  |
  |-- EDGE PASS    for each rule: backtest.backtest() over 5yr 1h -> per-rule R-stats
  |
  |-- ACCOUNT PASS portfolio_sim.run():
  |        step union of 1h timestamps ascending
  |        -> engine.evaluate(rolling window <= t)     -> intents (CTS + all rules + multi-TF)
  |        -> account.can_open() [real caps/allowance/USDC] -> fills at next-bar open + slippage
  |        -> per-asset position state drives exits (stop/target/exit_signal)
  |        -> record trades + daily equity samples + telemetry for gap analysis
  |
  |-- benchmark.run()   same $500/mo contributions through DCA strategies
  |
  |-- report.build()    edge table + account P&L + risk-adjusted vs benchmark
                        + gap analysis (training backlog) + verdict (IN-SAMPLE)
```

**Boundary rationale:** `history.py` hides pagination / rate-limits / per-asset gaps behind one
`ensure_history()`. `account.py` is a pure ledger (no market logic) that *feeds* the real
`guards`/`sizing` checks, so the sim can't silently diverge from live enforcement.
`portfolio_sim.py` owns only the time loop + wiring. `metrics.py`, `benchmark.py`, `report.py`
are pure functions over results.

---

## 3. Data acquisition — `keel/data/history.py`

**Entry point:**
```python
def ensure_history(
    client: CoinbaseClient,
    repo: Repository,
    products: list[str],                    # e.g. ["BTC-USD", "ETH-USD", "PAXG-USD"]
    granularities: list[Granularity],       # [ONE_HOUR, ONE_DAY]
    years: int,
    now_ts: int,
    sleep_sec: float = ...,                 # inter-request rate-limit pause
    refresh: bool = False,
) -> dict[tuple[str, Granularity], CoverageInfo]: ...

@dataclass(frozen=True)
class CoverageInfo:
    product: str
    granularity: Granularity
    first_ts: int | None
    last_ts: int | None
    n_candles: int
    requested_start_ts: int      # now_ts - years
    gaps: int                    # count of missing bars vs a continuous 24/7 grid
```

- **Pagination:** Coinbase returns ≤350 candles/request. Walk **backward** from `now_ts` in
  windows of `350 * granularity_seconds`: fetch -> `repo.upsert_candles()` -> step window back,
  until reaching `now_ts - years` OR an empty window (asset inception). `time.sleep(sleep_sec)`
  between requests.
- **Products:** allowlist assets map to **USD** market pairs for candle history
  (`BTC-USD`/`ETH-USD`/`PAXG-USD`); live trading quotes in USDC but historical depth lives on the
  USD pairs. This USD-for-USDC substitution is stated in the report caveats.
- **Idempotent + resumable:** upserts keyed on `(product, granularity, ts)`; a re-run fetches
  only missing bars; `refresh=True` forces a full re-pull.
- **Availability is data, not error:** PAXG on Coinbase is younger than 5yr — `CoverageInfo`
  records the actual window per asset; the report states it plainly.

---

## 4. Simulator — `keel/sim/portfolio_sim.py` + `keel/sim/account.py`

### 4.1 Time loop (`portfolio_sim.run`)

Step the **union of 1h bar timestamps** ascending (daily bars are higher-TF context only, never
stepped). At each `t`, for each allowlist asset:

- Assemble `candles_by_tf = {ONE_HOUR: window<=t, ONE_DAY: window<=t}` from a **rolling window**
  (last `WINDOW_BARS` ≈ 300, covering the longest indicator lookback). **Strictly `ts <= t`** —
  no lookahead (unit-test asserts this).
- **If flat on this asset:** `engine.evaluate([rules], candles_by_tf, ...)` -> ENTER signals
  (carry CTS score/tier/rule). Hand to `account.can_open()`; if it passes, fill at the **next
  bar's open** + slippage.
- **If holding:** resolve stop/target against the current 1h bar with backtest.py's conservative
  intrabar rule (no finer data at 1h -> stop-vs-target ambiguity resolves to the **stop**), AND
  check `rule.exit_signal(held_setup, candles_by_tf)`. Close on whichever fires first; realize
  P&L to cash.
- **One position per asset** at a time (matches backtest.py no-overlap + engine flat-only
  detect); multiple assets may hold concurrently — that's the portfolio. Cash/allowance are
  shared, so the $500/mo genuinely constrains the whole book.

**Signature:**
```python
@dataclass
class SimResult:
    trades: list[SimTrade]              # dollar-denominated, per asset, with cts/rule tags
    equity_curve: list[tuple[int, Decimal]]   # (ts, equity) daily samples
    contributions: list[tuple[int, Decimal]]  # (ts, +500) monthly deposits
    coverage: dict[tuple[str, Granularity], CoverageInfo]
    telemetry: SimTelemetry             # for gap analysis (section 6)

def run(
    rules: list[Rule],
    candles_by_asset: dict[str, dict[Granularity, list[Candle]]],
    config: Config,
    start_ts: int,
    end_ts: int,
    monthly_contribution: Decimal,
) -> SimResult: ...
```

### 4.2 Account ledger (`account.py`)

A pure, testable mirror of the live rails.

```python
@dataclass
class SimAccount:
    cash_usdc: Decimal
    positions: dict[str, OpenPosition]   # asset -> position
    month_spend: Decimal                 # BUY notional this calendar month
    day_spend: Decimal
    contributed: Decimal                 # cumulative deposits
    # methods:
    def deposit(self, amount: Decimal, now_ts: int) -> None      # monthly contribution + reset month/day counters on rollover
    def can_open(self, intent, config, now_ts) -> tuple[bool, list[str]]
    def open(self, asset, qty, fill_price, fee, now_ts) -> None  # debit cash + fee, record position, += spends
    def close(self, asset, fill_price, fee, now_ts) -> Decimal   # credit proceeds - fee, realize pnl, clear position
    def mark_to_market(self, prices: dict[str, Decimal]) -> Decimal   # cash + Σ qty*price
```

- `can_open` enforces the **same** caps as `guards.check` — per-order, per-day, exposure,
  per-asset%, **USDC-funding** (`cash >= notional AND cash > 0`), **$500/mo allowance** (with
  `subscription.pacing`). `guards.check` is coupled to live repo/DB state (it derives spend from
  `repo.get_orders`, reads `repo.get_subscription`, kill-switch, feed timestamps) and is called
  once per candidate order live; recomputing it from a persisted order log on every one of ~44k×3
  bars would be O(history) per call. So the sim **mirrors the same cap arithmetic** in
  `account.py` against its own in-memory counters, and a **parity unit test** asserts
  `account.can_open` returns the same verdict as `guards.check` on equivalent inputs — guaranteeing
  the sim can't silently diverge from the live rails without failing a test. (Rails not about
  spend caps — kill-switch, stale-data, no-averaging, correlation, DD breaker — are engine/live
  concerns not modeled by the funding sim; the caps above are the ones that bound *what gets
  bought* and are what the account pass exists to honor.)
- **Monthly contribution + reset:** at each calendar-month boundary within `[start, end]`,
  `deposit($500)` adds cash and resets `month_spend` (the $500/mo BUY cap). `day_spend` resets on
  day rollover.
- **Sizing:** stop-bearing rules -> `sizing.size(equity, risk_pct, entry, stop)`, clamped to
  caps; DCA -> `sizing.dca_size(dca.budget_usd, entry)`. Same functions the live executor uses.
- **Costs:** `slippage_pct` worsens each fill; `fee_pct` charged on both legs — shared with the
  edge pass so both passes assume identical costs.

---

## 5. Metrics & benchmark — `keel/sim/metrics.py` + `keel/sim/benchmark.py`

### 5.1 Edge pass (per rule, unit-less)
Straight from existing `strategy/stats.summarize()`: n_trades, win_rate, expectancy (R),
avg_win/avg_loss, profit_factor, max_drawdown (R), max_losing_streak, avg_mfe/mae, realized R:R.
Reported per rule and pooled. Reuses `strategy/backtest.backtest()` unchanged, one rule at a
time over the 5yr 1h series.

### 5.2 Account pass (dollars) — `metrics.py` (pure, hand-rolled `Decimal`)

**Dependency decision (2026-07-17):** the financial-statistics toolkit (NumPy / Pandas /
statsmodels / Matplotlib) was considered and **declined** — the live engine, executor, rails, and
money ledger are intentionally stdlib-only, `Decimal`-exact, and deterministic, and we keep one
consistent world rather than introducing float64 analytics. All statistics below are hand-rolled
in `Decimal`, the same way the codebase already hand-rolls its indicators. (Rationale and the
declined items are recorded in §10.)

```python
def daily_returns(equity_curve: list[tuple[int, Decimal]]) -> list[Decimal]
def cumulative_returns(equity_curve: list[tuple[int, Decimal]]) -> list[Decimal]
def volatility(daily_returns: list[Decimal]) -> Decimal          # stdev of daily returns
def ewma_volatility(daily_returns: list[Decimal], lam: Decimal) -> Decimal   # exp-weighted vol
def sharpe(daily_returns: list[Decimal]) -> Decimal              # rf = 0 (rf>0 would be riba)
def sortino(daily_returns: list[Decimal]) -> Decimal             # downside deviation
def max_drawdown_pct(equity_curve: list[tuple[int, Decimal]]) -> Decimal
def irr(cashflows: list[tuple[int, Decimal]], ending_value: Decimal) -> Decimal   # money-weighted
def cagr_money_weighted(...) -> Decimal
def return_per_drawdown(total_return_pct: Decimal, max_dd_pct: Decimal) -> Decimal  # MAR-like
```

`Decimal` has no `sqrt`/`exp` on floats-only libs; use `Decimal.sqrt()` (via `decimal` context)
for stdev, and an iterative `Decimal` exponential-weight recurrence for EWMA — no `math`/`float`
round-trips on money-derived series.
Reported: contributed vs ending value, net P&L $/%, IRR, CAGR, max-DD %, return/DD, Sharpe,
Sortino, time-in-market %, trade count, avg hold, per-asset P&L, **allowance utilization** (how
often the $500/mo cap bound).

### 5.3 Benchmark (`benchmark.py`)
Identical $500/mo contributions, same cost model, same metrics:
- **DCA-into-allowlist (primary):** each month buy $500 split by `config.target_weights`
  (40/30/30 BTC/ETH/PAXG), hold to end.
- **DCA-into-BTC (secondary):** each month buy $500 of BTC, hold.

Both produce an equity curve fed through the same `metrics.py`.

---

## 6. Report & verdict — `keel/sim/report.py`

**Deliverable:** Markdown at `docs/superpowers/reports/YYYY-MM-DD-engine-validation.md` (the
diffable source of truth), plus an optional self-contained **HTML Artifact** (inline-SVG equity
curve vs benchmark, drawdown underwater plot, per-asset bars), default-private, from the same
`SimResult`.

**Structure:**
1. **Verdict box** — `GO-LIVE candidate` | `TRAIN MORE`, one-line reason, **IN-SAMPLE** label.
2. **Data coverage** — per-asset window actually pulled.
3. **Edge table** — per-rule + pooled unit-less stats.
4. **Account results** — the section-5.2 metrics.
5. **Benchmark comparison** — engine vs DCA-into-allowlist (and DCA-BTC): return, max-DD,
   Sortino, return/DD side by side.
6. **Knowledge & data gaps -> training backlog** (section 6.1).
7. **Caveats** — in-sample (no holdout); USD-pair candles stand in for USDC; Phase-4 money-mgmt
   sizing ramp NOT modeled (plain fixed-fractional used); PAXG partial history; even on GO-LIVE,
   run the supervised tiny-cap confirm-mode test before real capital.

### 6.1 Gap analysis (deterministic — the "lacked information" section)

Produced from `SimTelemetry` gathered during the account pass. Each detected gap is one row:
**gap -> evidence (counts/periods) -> recommended training input.**

| Detector | Signal collected | Example recommendation |
|---|---|---|
| **Idle-through-moves** | spans with **zero rule firings** while benchmark moved > `MOVE_THRESHOLD_PCT` | "Strong-trend-no-pullback + parabolic-blowoff regimes have no rule — implement + backtest the deferred macro-cycle / trailing-exit knowledge." |
| **Unfed CTS factors** | CTS context keys **never populated / always default** across the run (e.g. `seasonality`, `fib_confluence`) | "Factor N contributed 0 signal all run — wire it and backtest, or drop it from the scorer." |
| **Would-have-traded-but-for-data** | setups rejected **only** because a confluence input was unavailable (defaulted), not a genuine market reject | "N missed setups blocked by missing input X — prioritize that data/feature." |
| **Data-coverage limits** | assets/timeframes that capped the test | "PAXG 4.1yr only; no 15m -> intrabar resolution coarse — pull finer data." |
| **Trade-management gaps** | systematic large **MAE** (stops too wide) or **MFE giveback** (no trailing/partial) | "Add a trailing-stop / partial-exit management rule; backtest against current exits." |
| **Losing buckets (§20.7 pivot-slice)** | rule × asset × regime buckets with persistently negative expectancy | "Bucket {rule,asset,regime} loses consistently — retune or demote; gather more samples." |

On `TRAIN MORE` this section is the roadmap; on `GO-LIVE` it's the hardening list. It hands work
to the future LLM-proposer feature — but any proposal still must clear backtest -> paper ->
promotion before entering the live rule library (LLM asymmetry principle, main-spec §5).

### 6.2 Verdict logic (all failing reasons surfaced, evaluated in order)
- **G1 Data sufficiency:** each asset has enough bars for a meaningful sample; assets below the
  bar are excluded from the pooled verdict and flagged.
- **G2 Accuracy / promotion floors:** pooled (and per-rule) stats clear the **canonical
  proving-gate floors** (main-spec §11 / knowledge-base §4.5): expectancy > 0, R:R ≥ 1.5,
  win ≥ **0.55**, n_trades ≥ **100**. The harness reads these from `config.promotion.*` — but
  `config.yaml` currently holds looser Phase-1 **placeholders** (`min_trades: 30`,
  `min_win_rate: 0.40`); the implementation plan MUST set `config.yaml`'s `promotion` block to the
  canonical values (`min_trades: 100`, `min_win_rate: 0.55`, `min_rr: 1.5`, `min_expectancy: 0`) so
  the validation verdict and live promotion agree. *(This answers "accuracy of the algorithms.")*
- **G3 Risk-adjusted edge:** engine's **return-per-drawdown AND Sortino ≥** DCA-into-allowlist
  benchmark, **or** comparable return at materially lower max-DD.
- **Verdict:** `GO-LIVE candidate` iff (sufficient data) AND G2 AND G3; else `TRAIN MORE`,
  itemizing which floor/benchmark comparison failed, with section 6.1 as the backlog.

---

## 7. CLI — `keel simulate`

```
keel simulate [--years 5] [--products BTC-USD,ETH-USD,PAXG-USD]
              [--contribution 500] [--out PATH] [--artifact]
              [--refresh] [--no-fetch]
```
- **Read-only** against Coinbase (candles only; never places an order) -> no passphrase/authz
  gate; uses the read-only `.env` key already present.
- Flow: `ensure_history` (unless `--no-fetch`) -> edge pass -> account pass -> benchmark ->
  report (+ optional Artifact) -> print the verdict summary to stdout.

---

## 8. Testing (TDD, pytest, `Decimal`, no network)

- **history.py** — backward pagination; stop-at-inception (empty window); idempotent upsert;
  `refresh`; `CoverageInfo` values. Fake transport returns canned windows.
- **account.py** — each cap (per-order/day/exposure/per-asset/USDC/allowance); monthly
  deposit + month/day reset on rollover; fee debits; sizing clamp; mark-to-market. Pure units.
- **metrics.py** — sharpe/sortino/max_dd/irr/cagr vs hand-computed fixtures.
- **benchmark.py** — DCA-into-allowlist buys the right monthly split and holds; DCA-BTC; metrics.
- **portfolio_sim.py** — synthetic series -> known trades/equity; **no-lookahead assertion**
  (window strictly ≤ t); one-position-per-asset; exit via stop/target/`exit_signal`; small
  2-rule engine integration.
- **report.py** — G1/G2/G3 verdict gates; each gap-analysis detector fires on a constructed
  scenario; Markdown renders; Artifact HTML is self-contained (no external requests).
- **cli** — `keel simulate --no-fetch` over a seeded in-memory DB produces a report; no network.

**Performance:** ~44k hourly bars × 3 assets × per-bar `engine.evaluate` on a capped ~300-bar
rolling window — a one-off expected to run in minutes; if too slow, profile and cache the daily
context. Acceptable for a validation run.

---

## 9. Implementation flow

New milestone; one issue per module: `history`, `account`, `metrics`, `benchmark`,
`portfolio_sim`, `report`, `cli+wiring`. Feature branch + git worktree per issue; commits
`Refs #n` / `Closes #n` with the Co-Authored-By trailer; PR -> auto-merge on green `pytest` +
clean `ruff`. Sequencing: `history`, `account`, `metrics` are independent (parallel);
`benchmark` needs `metrics`; `portfolio_sim` needs `account`; `report` needs
`portfolio_sim`+`benchmark`+`metrics`; `cli` last.

## 10. Out of scope (this spec)

**General:** walk-forward / OOS validation; the Phase-4 money-mgmt smooth-ratio sizing ramp; the
LLM-proposer feature; live order placement (harness is read-only). Tracked elsewhere; may consume
this harness later.

**Quant-stack proposals considered and declined (2026-07-17)** — kept here so they aren't
re-litigated:

| Proposal | Disposition | Reason |
|---|---|---|
| NumPy / Pandas / Matplotlib / statsmodels | **Declined** as dependencies | Preserve the stdlib-only, `Decimal`-exact, deterministic live path + one consistent world. The metrics we actually need are hand-rolled in `Decimal` (§5.2); report charts are hand-emitted inline-SVG (§6). |
| Daily/cumulative returns, volatility, EWMA vol, Sharpe/Sortino | **Adopted** (hand-rolled) | These are the account-pass/benchmark metrics — §5.2. |
| ARIMA (and any price-forecasting model) | **Declined** for the decision path | Violates the **no-prediction-oracle** rule (main-spec §6.4). Permissible only as *offline research / a risk-reducing flag*, and per the §5 asymmetry rule any signal it yields must clear backtest→paper→promotion before it can ever add a trade — never a live entry input. Not built here. |
| CAPM (rf-based expected return / tangency allocation) | **Declined** | Built on a **risk-free rate + rf-borrowing → riba**. If useful, compute strategy **beta/correlation to BTC descriptively** (rf=0) — not CAPM allocation. |
| Portfolio-allocation optimization (`target_weights`) | **Deferred** | The classic Markowitz *tangency* portfolio borrows at rf → riba. A **long-only, rf=0, no-leverage** variant (min-variance / min-drawdown / risk-parity) is hand-rollable without NumPy and could tune the hardcoded 40/30/30 later — its own future spec, validated by this harness. |
| EMH | **Adopted as framing** | Not a tool — it's *why* the verdict demands a risk-adjusted edge over buy-and-hold (not raw return) and why the report is labeled in-sample (§6). |
| Quantopian | **Declined** | Platform shut down in 2020; and we validate *our* engine/rails, not an external backtest lib (Approach C, §2). Borrow pyfolio-style tearsheet *ideas* for the report only. |
