# keel Phase 1 — Offline Foundation — Implementation Plan

> **For agentic workers:** Each task below is a GitHub issue. Implement it in its own git **worktree**
> on a **feature branch** `feat/<issue#>-<slug>`, using **TDD** (test → fail → implement → pass → commit).
> Every commit message ends with a GitHub issue reference (`Refs #<n>` / final commit `Closes #<n>`).
> Push the branch and open a **PR targeting `main`** for human review. **Model policy:** Opus plans/designs
> (this doc); **Sonnet** writes code + tests; **Haiku** for pure file read/write and web lookups.

**Goal:** Build the fully-offline foundation of the keel agent — SQLite persistence, historical CSV
import, FIFO P&L, the pure analysis primitives (candles, levels, regime, indicators), a thin Coinbase
client, and a market-data feed — all unit-tested with no live money and no network in pure-logic tests.

**Architecture:** Python package `keel/` with focused single-responsibility modules. Pure-logic
modules (analysis/*, csv_import, pnl) take data in / return values out — no network, no global state.
`data/db.py` owns the schema; `data/repository.py` is the only write path to SQLite. `data/cb_client.py`
is the sole network module (exercised against canned fixtures in tests). Money/prices use `Decimal`;
indicator math uses `float`.

**Tech Stack:** Python 3.12, `uv` (deps + venv), stdlib `sqlite3` (no ORM), `coinbase-advanced-py`,
`pytest`, `ruff`. Config in `config.yaml` (`PyYAML`); secrets in git-ignored `.env` (`python-dotenv`).

## Global Constraints

- **Python 3.12**, managed with **uv** (`uv add`, `uv run pytest`).
- **stdlib `sqlite3` only** — no ORM. One DB file `keel.db` (git-ignored).
- **No network in pure-logic tests.** `cb_client` tested only against canned JSON fixtures.
- **Money & prices = `decimal.Decimal`**; indicator math may use `float`. Never use float for P&L.
- **Crypto units:** measure moves in **% / ATR / price ticks**, never forex "pips".
- **Halal / long-only / no-leverage** is enforced in later phases; Phase 1 stores/analyzes only.
- **Shared value types** live in `keel/types.py` (created in Task 1) — all tasks import from there.
- **TDD, DRY, YAGNI, frequent commits.** Each task ends green (`uv run pytest` passes) + `ruff check` clean.
- **`.env` never committed.** `.gitignore` covers `.env`, `*.db`, `.venv/`, `__pycache__/`, `.DS_Store`.

**Dependency waves (for worktree parallelization):**
- **Wave A (sequential, first):** Task 1 (scaffolding+config+types) — blocks everything. Merge before Wave B.
- **Wave B (parallel, after Task 1 merged):** Tasks 2 (db/repo), 3 (candles), 4 (levels), 5 (regime),
  6 (indicators), 7 (cb_client) — mutually independent → separate worktrees.
- **Wave C (parallel, after Task 2 merged; 10 also needs 7):** Tasks 8 (csv_import), 9 (pnl), 10 (market_feed).

---

### Task 1 — Project scaffolding, config & shared types  `[prerequisite]`

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.env.example`, `config.yaml`, `README.md` (short)
- Create: `keel/__init__.py`, `keel/types.py`, `keel/config.py`
- Create: `tests/__init__.py`, `tests/test_config.py`, `tests/conftest.py`
- Create: `keel/{data,analysis,strategy,execution}/__init__.py` (empty package dirs)

**Interfaces — Produces (later tasks rely on these):**
- `keel/types.py`:
  - `class Granularity(str, Enum)`: `ONE_MINUTE="ONE_MINUTE"`, `FIVE_MINUTE`, `FIFTEEN_MINUTE`, `ONE_HOUR`, `SIX_HOUR`, `ONE_DAY` (values match Coinbase API granularity strings).
  - `@dataclass(frozen=True) class Candle: ts:int; open:Decimal; high:Decimal; low:Decimal; close:Decimal; volume:Decimal` (`ts` = epoch seconds, candle open time).
  - `class Side(str, Enum)`: `BUY="BUY"`, `SELL="SELL"`.
- `keel/config.py`:
  - `class ConfigError(Exception)`.
  - `@dataclass` config tree: `Config` with `.allowlist:list[str]`, `.target_weights:dict[str,Decimal]`, `.risk_pct:Decimal`, `.caps` (`max_per_order_usd`, `max_per_day_usd`, `max_exposure_usd`, `max_per_asset_pct`), `.market_data` (`granularities:list[Granularity]`, `history_days:int`), and pass-through blocks `auto_trade`, `promotion`, `money_mgmt`, `dca` (typed dataclasses; unused fields OK in Phase 1).
  - `def load_config(path: str | Path) -> Config` — parses `config.yaml`, validates, raises `ConfigError("<key>: <reason>")` on missing/invalid `allowlist`/`caps`; never silently defaults those.
  - `def load_secrets(env_path=".env") -> dict` — loads CDP `api_key`/`api_secret`; returns `{}` if absent (offline commands still work).

**Steps (TDD):**
- [ ] Init project: `uv init --package --python 3.12`, then `uv add pyyaml python-dotenv` and `uv add --dev pytest ruff`. Create the package dirs + empty `__init__.py`s.
- [ ] Write `.gitignore` (`.env`, `*.db`, `.venv/`, `__pycache__/`, `*.pyc`, `.DS_Store`, `.ruff_cache/`), `.env.example` (`CDP_API_KEY=`, `CDP_API_SECRET=`), and a placeholder `config.yaml` with the spec §16 keys (BTC/ETH/PAXG allowlist placeholders, 1% risk, cap placeholders, `market_data.granularities: [ONE_DAY, ONE_HOUR, FIFTEEN_MINUTE]`, `history_days: 365`).
- [ ] **Test-first** `tests/test_config.py`: (a) `load_config` on a valid fixture returns a `Config` with `Decimal` weights summing to 1 and the allowlist; (b) missing `allowlist` raises `ConfigError` mentioning `allowlist`; (c) a cap set to a negative value raises `ConfigError`; (d) `Candle` and `Granularity` importable from `keel.types` with correct field types.
- [ ] Run tests → fail. Implement `types.py` + `config.py` minimally → tests pass.
- [ ] `uv run ruff check` clean; `uv run pytest` green.
- [ ] Commit(s): `chore: scaffold keel package, config loader and shared types (Refs #1)`; final `Closes #1`. Open PR to `main`.

**Acceptance:** `uv run pytest` and `uv run ruff check` pass on a fresh clone; `.env` is git-ignored; the package imports cleanly. **This PR establishes conventions — review/merge before Wave B starts.**

---

### Task 2 — SQLite schema & repository  `[wave B]`

**Files:** Create `keel/data/db.py`, `keel/data/repository.py`, `tests/data/test_db.py`, `tests/data/test_repository.py`.

**Interfaces — Consumes:** `keel.types` (Candle, Granularity, Side).
**Interfaces — Produces:**
- `db.py`: `def connect(path: str | Path = "keel.db") -> sqlite3.Connection` (row_factory=Row, `PRAGMA foreign_keys=ON`); `def migrate(conn) -> None` (idempotent — creates all §6 tables + indexes if absent, tracks `schema_version`).
- `repository.py` — `class Repository` wrapping a connection with typed methods (all money as `Decimal`, stored as TEXT):
  - `upsert_transaction(tx: dict) -> None` (dedupe on `coinbase_id` UNIQUE), `get_transactions(asset=None) -> list[dict]`.
  - `upsert_candles(product_id:str, granularity:Granularity, candles:list[Candle]) -> int` (returns rows written; PK `(product_id, granularity, ts)`), `get_candles(product_id, granularity, start_ts=None, end_ts=None) -> list[Candle]` (ordered by ts).
  - `insert_order(order: dict) -> int`, `update_order(order_id, **fields) -> None`.
  - `get_state(key, default=None)`, `set_state(key, value)` (the `agent_state` KV table).

**Steps (TDD):** Build the 8 tables from spec §6 (`transactions, candles, orders, rules, signals, backtests, pnl_daily, agent_state, journal`). Test against an in-memory DB (`connect(":memory:")`): migrate is idempotent (run twice, no error); candle upsert dedupes on PK and round-trips `Decimal` exactly (store TEXT); transaction upsert dedupes on `coinbase_id`; `get_state`/`set_state` round-trip. Commits reference `#2`; PR to `main`.

**Acceptance:** All §6 tables exist with correct PKs/uniques/indexes; `Decimal` values survive round-trips exactly; migrate is idempotent.

---

### Task 3 — Candlestick primitives  `[wave B, parallel]`

**Files:** Create `keel/analysis/candles.py`, `tests/analysis/test_candles.py`.
**Consumes:** `keel.types.Candle`. **Produces (pure functions on `Candle`/`list[Candle]`, all `k`/`m` as params):**
- `body(c)`, `upper_wick(c)`, `lower_wick(c)`, `range_(c)` → Decimal.
- `is_pin_bar(c, zone=Decimal("0.30")) -> Literal["bullish","bearish"] | None` (open AND close in outer `zone` of range).
- `is_doji(c, m=Decimal("0.1"))`, `is_marubozu(c, frac=Decimal("0.05"))`, `is_hammer(c,k=2)`, `is_shooting_star(c,k=2)` → bool.
- `is_three_bar_reversal(c1,c2,c3) -> Literal["bullish","bearish"] | None`.
- `is_tweezer(c1,c2, tol=Decimal("0.001")) -> Literal["top","bottom"] | None` (equal highs/lows within `tol`, bodies in outer 50%).
- `pattern_confidence(name:str) -> Decimal` (low-test/hammer > tweezer > doji, per §7.2).

**Steps (TDD):** Table-driven tests with hand-built candles for each detector: a textbook bullish pin bar returns "bullish"; a mid-range close returns None; a doji (open≈close) is a doji but not a pin bar; a tweezer-bottom pair with equal lows returns "bottom". Commits `#3`; PR to `main`. **Acceptance:** each detector correct on positive + negative fixtures; confidence ordering holds.

---

### Task 4 — Levels: support/resistance  `[wave B, parallel]`

**Files:** Create `keel/analysis/levels.py`, `tests/analysis/test_levels.py`.
**Consumes:** `keel.types.Candle`. **Produces:**
- `swing_highs(candles, lookback=2) -> list[int]`, `swing_lows(...)` → indices of pivots.
- `@dataclass Level: price:Decimal; kind:Literal["support","resistance"]; touches:int; angular:bool`.
- `find_levels(candles, tolerance=Decimal("0.002"), min_touches=3) -> list[Level]` (cluster pivots into levels; count touches; §7.3 min 3).
- `is_round_number(price, step=Decimal("0.005")) -> bool` (even-handle proximity).
- `role_reversed(level, candles) -> bool` (prior resistance now acting as support, §1.3).
- `nearest_level(price, levels, kind=None) -> Level | None`.

**Steps (TDD):** Build a synthetic series with three clean bounces off one price → `find_levels` yields a `support` Level with `touches>=3`; a level touched twice is excluded when `min_touches=3`; round-number detection on `1.10000` true, `1.10237` false. Commits `#4`; PR. **Acceptance:** levels detected with correct touch counts; min_touches enforced; round-number + role-reversal correct.

---

### Task 5 — Regime: condition & phase  `[wave B, parallel]`

**Files:** Create `keel/analysis/regime.py`, `tests/analysis/test_regime.py`.
**Consumes:** `keel.types.Candle`, `levels.swing_highs/lows` (import from Task 4 — declare the dependency; if worktrees race, stub the two functions' signatures).
**Produces:**
- `class Condition(str,Enum)`: `BULLISH,BEARISH,RANGING,CHOPPY`.
- `class Phase(str,Enum)`: `RUN,PULLBACK`.
- `detect_condition(candles, lookback=20) -> Condition` (HH+HL=bullish, LH+LL=bearish, flat swings=ranging, structure violations=choppy).
- `detect_phase(candles) -> Phase` (in-trend impulse vs retracement).
- `is_tradeable(condition) -> bool` (False for CHOPPY, §1.2).

**Steps (TDD):** Synthetic higher-highs/higher-lows series → `BULLISH`; erratic series → `CHOPPY` and `is_tradeable` False; a clear retracement within an uptrend → `PULLBACK`. Commits `#5`; PR. **Acceptance:** all four conditions + both phases classified correctly on fixtures.

---

### Task 6 — Indicators  `[wave B, parallel]`

**Files:** Create `keel/analysis/indicators.py`, `tests/analysis/test_indicators.py`.
**Consumes:** `keel.types.Candle`. Uses `float` internally; accepts `list[Candle]` or `list[float]` of closes.
**Produces:**
- `ema(values:list[float], period:int) -> list[float]`, `ema_fan(candles, periods=(8,20,50)) -> dict[int,list[float]]`, `fan_aligned(fan, idx, direction) -> bool`.
- `rsi(values, period=14) -> list[float]`; `is_overbought(rsi_val, thr=80.0)`, `is_oversold(rsi_val, thr=20.0)`.
- `rsi_divergence(candles, rsi_vals, lookback=20) -> Literal["bullish","bearish"] | None` (§4.4).
- `macd(values, fast=12, slow=26, signal=9) -> tuple[list,list,list]`.
- `atr(candles, period=14) -> list[float]` (crypto-calibrated usage; absolute value).
- `fib_retracements(swing_high, swing_low) -> dict[str,Decimal]` (0.382/0.5/0.618/0.786/0.886), `fib_extensions(...) -> dict[str,Decimal]` (1.272/1.618).
- `deceleration(candles, n=3) -> bool` (N consecutive shrinking bodies, §1.4).

**Steps (TDD):** Verify `ema`/`rsi`/`atr` against small hand-computed fixtures (tolerance 1e-6); a series with price higher-high but RSI lower-high → `rsi_divergence == "bearish"`; three shrinking candles → `deceleration True`; Fib levels exact `Decimal`. Commits `#6`; PR. **Acceptance:** numeric indicators match reference values; divergence + deceleration + Fib correct.

---

### Task 7 — Coinbase client wrapper  `[wave B, parallel]`

**Files:** Create `keel/data/cb_client.py`, `tests/data/test_cb_client.py`, `tests/fixtures/cb_*.json`.
**Consumes:** `keel.types` (Candle, Granularity), `config.load_secrets`. **Produces** a thin, injectable wrapper (accepts a transport/`RESTClient` so tests inject a fake — NO live calls in tests):
- `class CoinbaseClient` with `get_candles(product_id, granularity, start, end) -> list[Candle]`, `get_spot(product_id) -> Decimal`, `get_accounts() -> list[dict]` (balances), `preview_order(...) -> dict`. (Order *placement* stubbed with `NotImplementedError` in Phase 1 — added in Phase 3.)
- Adapters convert raw Coinbase JSON → `Candle`/`Decimal`.

**Steps (TDD):** Save real-shaped JSON fixtures; inject a fake transport returning them; assert `get_candles` maps to `Candle` with `Decimal` OHLCV and correct `ts`; `get_spot` returns `Decimal`. No network. `add coinbase-advanced-py`. Commits `#7`; PR. **Acceptance:** JSON→typed mapping correct; zero network in tests; placement raises `NotImplementedError`.

---

### Task 8 — CSV import  `[wave C, needs #2]`

**Files:** Create `keel/data/csv_import.py`, `tests/data/test_csv_import.py`, `tests/fixtures/*.csv` (trimmed real samples).
**Consumes:** `Repository` (#2). **Produces:**
- `def import_csv(path, repo) -> ImportResult` where `ImportResult(imported:int, skipped:int, warnings:list[str])`.
- `def import_dir(dir_path, repo) -> ImportResult` (all `transactions/*.csv`, idempotent — re-running imports nothing new).
- Parses the Coinbase "Transactions" CSV format (header row `ID,Timestamp,Transaction Type,Asset,Quantity Transacted,...`), strips `$`/currency, parses `Decimal`, dedupes by `ID`, skips malformed rows with a counted warning (never silently dropped, §10).

**Steps (TDD):** Import a 3-row fixture → `imported==3`; re-import → `imported==0, skipped==3`; a malformed row → counted in `warnings`, others still imported; `$114,194.285`-style prices parse to exact `Decimal`. Commits `#8`; PR. **Acceptance:** idempotent; correct Decimal parsing; malformed rows warned + counted; handles the real `transactions/*.csv` shape (both the Gain/Loss and the standard Transactions exports — detect by header).

---

### Task 9 — P&L (FIFO)  `[wave C, needs #2]`

**Files:** Create `keel/analysis/pnl.py`, `tests/analysis/test_pnl.py`.
**Consumes:** `Repository.get_transactions` (#2). **Produces:**
- `def realized_pnl(transactions, asset=None) -> Decimal` (FIFO lot matching of buys→sells/converts).
- `def unrealized_pnl(transactions, marks:dict[str,Decimal]) -> dict[str,Decimal]` (open lots marked to `marks`).
- `def position(transactions, asset) -> Position` (`qty:Decimal, avg_cost:Decimal`).
- `def daily_snapshot(transactions, marks, date) -> list[dict]` (rows for `pnl_daily`: asset, qty, avg_cost, price, realized, unrealized).
- `def max_drawdown(equity_curve:list[Decimal]) -> tuple[Decimal, int]` (depth, time-in-drawdown bars, §4.6); `def recovery_pct(dd_pct:Decimal) -> Decimal` (the §10.5 table: `dd/(1-dd)`).

**Steps (TDD):** buy 1@100, buy 1@200, sell 1@300 → realized `Decimal("200")` (FIFO: first lot), remaining position qty 1 @ avg 200; unrealized with mark 250 → `50`; `recovery_pct(0.5)==1.0`, `recovery_pct(0.1)≈0.1111`; drawdown depth+duration on a synthetic curve. All `Decimal`. Commits `#9`; PR. **Acceptance:** FIFO realized/unrealized correct incl. converts; drawdown + recovery-table exact.

---

### Task 10 — Market feed  `[wave C, needs #2 + #7]`

**Files:** Create `keel/data/market_feed.py`, `tests/data/test_market_feed.py`.
**Consumes:** `CoinbaseClient` (#7, injected), `Repository` (#2), `Config` (#1). **Produces:**
- `def backfill(client, repo, products:list[str], granularities:list[Granularity], history_days:int) -> int` (fetch + upsert candles; returns rows written; resumable — only fetches gaps).
- `def poll_once(client, repo, products, granularities) -> int` (fetch latest closed candles + upsert).
- `def is_fresh(repo, product, granularity, now_ts, max_age_sec) -> bool` (stale-data guard input, §10.4 — used by later phases).

**Steps (TDD):** With a fake client returning fixture candles, `backfill` writes N rows to an in-memory repo and is idempotent on re-run (gap-only); `poll_once` appends only new closed candles; `is_fresh` False when latest candle older than `max_age`. No network. Commits `#10`; PR. **Acceptance:** backfill+poll upsert correctly and idempotently; freshness check correct; injectable client (no live calls in tests).

---

## Self-Review

**Spec coverage (Phase 1 scope, §19 + §6/§7):** DB schema (T2 ✓ all 8 tables), CSV import + no agent-txn-unrecorded (T8 ✓, orders/transactions in T2), P&L incl. drawdown/recovery (T9 ✓), candles/levels/regime/indicators primitives (T3–T6 ✓), market feed + freshness (T10 ✓), cb_client (T7 ✓), config + shared types + `.env` security (T1 ✓). Rules/CTS/backtest/execution/rails = Phases 2–4 (out of Phase-1 scope, correctly deferred).

**Placeholder scan:** No TBD/TODO; config allowlist/caps are explicit runtime placeholders (spec §21 open item), not code gaps. Each task has concrete acceptance criteria + representative test cases.

**Type consistency:** `Candle`/`Granularity`/`Side` defined once in `types.py` (T1) and consumed everywhere; `Decimal` for money across T2/T8/T9; `Repository` method names consistent between T2 (producer) and T8/T9/T10 (consumers); `swing_highs/lows` produced by T4 consumed by T5.

**Note for implementers:** these tasks are sized for skilled Sonnet subagents doing TDD — interfaces/signatures and acceptance criteria are exact; write the failing test first, keep files focused, end each task green + ruff-clean.
