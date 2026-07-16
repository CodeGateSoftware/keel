# halal-cb Phase 3 — Execution, Rails & Agent Loop (confirm mode) — Implementation Plan

> **For agentic workers:** each task is a GitHub issue. Implement in its own git **worktree** on branch
> `feat/<issue#>-<slug>`, **TDD**, commits `Refs #n`/`Closes #n`, push, open a **PR to `main`** for human review.
> **Model policy:** Opus designs (this doc); **Sonnet** codes+tests; **Haiku** for pure file I/O. **Read the ACTUAL
> merged modules on `main` for real signatures — do not guess.**

**Goal:** Make the agent *live-capable but safe* — a hard-rail guard layer, an order executor (preview →
guards → confirm|bypass → place), a scheduled agent loop (confirm mode), the portable encrypted-secrets vault +
dangerous-action passphrase gate, and the CLI. **Live order placement exists but is gated**: confirm-mode by
default, the 12 rails enforced before *every* order (un-overridable), and dangerous actions require the passphrase.

**Architecture:** builds on the merged Phase 1–2 stack (`analysis/*`, `strategy/*`, `data/*`). Engine emits
`Signal`s; the executor turns a `Signal` into a guarded order via the `CoinbaseClient`/broker; `agent.py` drives
the loop. Money/prices `Decimal`. **No real trade in tests** — the broker client is injected/faked; `place_order`
is exercised only against a fake transport.

**Tech Stack:** Python 3.12, uv, stdlib, `cryptography` (secrets vault), pytest, ruff.

## Global Constraints

- Same as Phases 1–2 (Decimal money, %/ATR not pips, no network in pure-logic tests, TDD, ruff-clean, focused files).
- **Long-only spot, halal.** Enforced by the allowlist rail + (future) compliance policy.
- **Rails run before EVERY order, in every mode (incl. bypass), un-overridable.**
- **Confirm mode is the default.** Bypass and other dangerous actions require the passphrase gate (Task 7).
- **`.env`/`secrets.enc` git-ignored; secrets never logged.**
- Import shared types from `halal_cb.types` / `halal_cb.strategy.rules.base`.

**Dependency waves:**
- **Wave A (prereq):** Task 1 (Repository API + shared stats helper — pays down the 3 debts). Merge before Wave B.
- **Wave B (parallel):** Task 2 (sizing), Task 3 (guards/rails), Task 5 (cb_client `place_order`), Task 6 (secrets
  vault), Task 7 (authz gate). Independent → separate worktrees.
- **Wave C:** Task 4 (executor — needs guards + sizing + place_order).
- **Wave D:** Task 8 (agent loop — needs executor + engine + feed), Task 9 (CLI — needs most).

---

### Task 1 — Repository API + shared stats helper  `[prereq; pays down Phase-2 debts]`

**Files:** modify `halal_cb/data/repository.py`; create `halal_cb/strategy/stats.py`; refactor
`halal_cb/strategy/{backtest,paper,promotion,engine}.py` to use the new surface; update/extend their tests.

**Produces:**
- `Repository`: `insert_rule(kind, params:dict, status="candidate") -> int`, `get_rules(status=None) -> list[dict]`,
  `update_rule_status(rule_id, status) -> None`, `get_orders(mode=None, product_id=None, status=None) -> list[dict]`,
  `insert_signal(signal:dict) -> int`.
- `strategy/stats.py`: `summarize(trades: list[Trade]) -> BacktestResult` (the aggregation currently duplicated in
  `backtest.py` + `paper.py`).

**Steps (TDD):** add the repo methods (tested on `:memory:`); extract `summarize` and switch `backtest.py` +
`paper.track_record` to call it (behavior unchanged — existing tests still pass); replace the direct `repo._conn`
accesses in `paper.py`/`promotion.py`/`engine.py` with the new methods. `uv run pytest` (whole suite) green; ruff
clean. Commits reference the issue; PR to `main`. **Merge before Wave B.**

**Acceptance:** no module reaches into `repo._conn`; `summarize` is shared (no duplication); full suite green.

---

### Task 2 — Position sizing  `[wave B]`

**Files:** Create `halal_cb/execution/sizing.py`, `tests/execution/test_sizing.py`.
**Produces:** `size(equity:Decimal, risk_pct:Decimal, entry:Decimal, stop:Decimal) -> Decimal` (qty =
`(equity*risk_pct)/abs(entry-stop)`); `spend(qty, entry) -> Decimal`; `dca_size(budget_usd, entry) -> Decimal`
(no-stop accumulation class). All `Decimal`; %/price, never pips.

**Steps (TDD):** equity 10000, risk 1%, entry 100, stop 90 → qty 10 (risk $100 / $10 stop); spend = qty*entry;
`dca_size(50, 100)` = 0.5. Commits; PR. **Acceptance:** fixed-fractional + DCA sizing exact.

---

### Task 3 — Hard rails (guards)  `[wave B]`

**Files:** Create `halal_cb/execution/guards.py`, `tests/execution/test_guards.py`.
**Consumes:** `sizing` (Task 2 — build against its signature), `data.repository` (`agent_state` via
`get_state`/`set_state`), `config.Config`. **Produces:**
- `@dataclass OrderIntent(product_id, side, qty:Decimal, entry:Decimal, stop:Decimal|None, notional:Decimal, is_dca:bool, rule_kind:str)`.
- `@dataclass GuardResult(ok:bool, violations:list[str])`.
- `def check(intent, repo, config, now_ts) -> GuardResult` — runs **all 12 rails** and returns every violation
  (never short-circuits silently): (1) **halal allowlist**, (2) **per-order $ cap**, (3) **per-day $ cap**
  (running total from `agent_state`/orders), (4) **total open-exposure cap**, (5) **correlation-adjusted sizing**
  (scale-down check on correlated open positions), (6) **per-asset concentration cap**, (7) **min-move /
  anti-scalping** (target clears spread+fees), (8) **no averaging into losers** (no add to underwater position),
  (9) **no stop-widening** (stop only ratchets toward profit vs a prior stop), (10) **sell-only-on-rule**,
  (11) **account-drawdown breaker — total AND weekly** (DCA exempt from this one, §12.6), (12) **stale-data /
  feed-health** + **kill-switch** (from `agent_state`).
- Rails are **un-overridable**: `check` is called before every order regardless of mode.

**Steps (TDD):** one focused test per rail — a compliant intent → `ok=True`; each violating intent → `ok=False`
with the specific rail named (non-allowlisted asset; over per-order cap; exposure over cap; stop wider than prior;
add-to-loser; DCA exempt from DD breaker but still allowlist-bounded; kill-switch set → all orders vetoed).
Commits; PR. **Acceptance:** every rail rejects correctly and reports which; kill-switch halts all; DCA exemption correct.

---

### Task 4 — Executor  `[wave C]`

**Files:** Create `halal_cb/execution/executor.py`, `tests/execution/test_executor.py`.
**Consumes:** `guards` (Task 3), `sizing` (Task 2), the broker (`CoinbaseClient` — `preview_order`/`place_order`),
`data.repository` (orders), `strategy.rules.base.Signal`. **Produces:**
- `def execute(signal, broker, repo, config, mode:Literal["confirm","bypass"], confirm_fn=None, now_ts=...) -> ExecutionResult`
  — build an `OrderIntent` (size via `sizing`), **run `guards.check` (veto on any violation)**, `broker.preview_order`,
  then: **confirm** → call `confirm_fn(preview)` (returns approve/reject) ; **bypass** → proceed; then
  `broker.place_order`, write the fill to `orders` (before+after), attach the **OCO bracket** (stop+target linked,
  cancel-sibling-on-fill), support **partial scale-out** + **break-even roll** + **ATR trailing** for management.
- `@dataclass ExecutionResult(placed:bool, order_id:int|None, vetoed_by:list[str], preview:dict|None, reason:str)`.

**Steps (TDD):** with a **fake broker** (no network): a compliant confirm-mode signal + `confirm_fn`→approve →
places + logs; `confirm_fn`→reject → not placed; a rail-violating signal → vetoed (never previews/places);
bypass-mode compliant → places without prompt; OCO: filling target cancels the stop. Commits; PR. **Acceptance:**
guards always run first; confirm/bypass honored; OCO/partial/trailing correct; every order logged; no live network in tests.

---

### Task 5 — cb_client `place_order`  `[wave B]`

**Files:** modify `halal_cb/data/cb_client.py`; extend `tests/data/test_cb_client.py`.
**Produces:** implement `place_order(product_id, side, order_configuration) -> dict` (currently
`NotImplementedError`) against the injected transport — supports market + limit + stop; maps the response to the
normalized order dict; **still injected/fixture-tested (NO live network in tests)**. Keep read methods unchanged.

**Steps (TDD):** fake transport returns a canned placed-order JSON → `place_order` maps it to the normalized shape;
market vs limit config passed through correctly. Commits; PR. **Acceptance:** placement maps correctly against
fixtures; zero network in tests.

---

### Task 6 — Portable encrypted secrets vault  `[wave B]`

**Files:** Create `halal_cb/security/secrets.py`, `tests/security/test_secrets.py`. `uv add cryptography`.
**Produces (main spec §14 Part A):** master passphrase → **scrypt KDF** → key → **AES-GCM** encrypt/decrypt a JSON
secrets blob in `secrets.enc` (copyable between machines): `save_vault(secrets:dict, passphrase, path="secrets.enc")`,
`load_vault(passphrase, path="secrets.enc") -> dict` (raises on wrong passphrase), `migrate_from_env(.env) -> None`.
`chmod 600`; secrets never logged.

**Steps (TDD):** round-trip a secrets dict through save/load with a passphrase; wrong passphrase raises; file is not
plaintext (ciphertext ≠ the values). Commits; PR. **Acceptance:** encrypt/decrypt round-trips; wrong passphrase
fails; on-disk blob is ciphertext; `.enc` git-ignored.

---

### Task 7 — Dangerous-action passphrase gate  `[wave B]`

**Files:** Create `halal_cb/security/authz.py`, `tests/security/test_authz.py`.
**Produces (main spec §14):** `set_passphrase(passphrase, path)` (stores a **scrypt hash**), `verify(passphrase, path) -> bool`
(rate-limited — track attempts + backoff), `require(action, passphrase, path)` (raises `AuthzError` on wrong
passphrase) for the dangerous actions `{arm_bypass, raise_caps, disable_killswitch, unlock_vault}`. Read-only +
confirm-mode actions require nothing.

**Steps (TDD):** correct passphrase → `verify` True / `require` passes; wrong → False / raises; N wrong attempts →
rate-limited. Commits; PR. **Acceptance:** gate passes/fails correctly; rate-limiting works; hash (not plaintext) stored.

---

### Task 8 — Agent loop (confirm mode)  `[wave D]`

**Files:** Create `halal_cb/agent.py`, `tests/test_agent.py`.
**Consumes:** `market_feed`, `strategy.engine.evaluate`, `execution.executor.execute`, `data.repository`, the rules
(loaded via `get_rules('live')`). **Produces:**
- `def run_once(broker, repo, config, now_ts) -> LoopResult` — one cycle: **poll fresh candles** (feed), **evaluate
  live rules** (engine → Signals), **handle EXIT signals on held positions** (call `rule.exit_signal` for each open
  position; emit EXIT → executor), **execute ENTER signals** (executor, confirm|bypass), respecting the **kill-switch**.
- `def loop(broker, repo, config, interval_sec, stop_flag)` — the scheduled wrapper.
- **EXIT-signal wiring** (the Phase-2 gap): the loop owns position state, so it drives exits (§ engine only did entries).

**Steps (TDD):** with fake broker + in-memory repo + a scripted engine/rules, `run_once` polls→evaluates→executes;
kill-switch set → no orders; a held position whose exit fires → an EXIT order. Commits; PR. **Acceptance:** one cycle
composes feed→evaluate→execute; exits driven from position state; kill-switch halts; confirm/bypass honored.

---

### Task 9 — CLI  `[wave D]`

**Files:** Create `halal_cb/cli.py`, `tests/test_cli.py`. `uv add click`.
**Produces:** commands: `db import`, `monitor [--loop]`, `agent --loop [--confirm|--bypass]`, `rules
list|backtest|promote|demote|disable`, `pnl`, `insights`(stub ok), `kill`/`resume`. **Dangerous commands**
(`agent --bypass`, cap overrides, `resume`) go through the **authz gate** (Task 7). Read-only commands don't.
Renders results; prints the halal + not-financial-advice disclaimer footer.

**Steps (TDD):** invoke via click's `CliRunner`: `db import` runs the importer; `agent --bypass` without the
passphrase is refused; `kill`/`resume` flip `agent_state`; read-only commands need no passphrase. Commits; PR.
**Acceptance:** commands wired; dangerous ones gated; disclaimer shown; no live calls in tests.

---

## Self-Review

**Spec coverage (Phase 3, §10/§13/§14/§4):** rails (T3 ✓ all 12), executor incl. OCO/partial/trailing (T4 ✓),
live place_order (T5 ✓), confirm/bypass modes + kill-switch (T4/T8 ✓), agent loop + EXIT wiring (T8 ✓, closes the
Phase-2 gap), sizing (T2 ✓), Part A security — encrypted vault + dangerous-action gate (T6/T7 ✓), CLI + gated
dangerous commands (T9 ✓), the 3 Phase-2 debts (T1 ✓). Money-management *ramp* + insights = Phase 4 (deferred).

**Placeholder scan:** none; each task has exact interfaces + acceptance + test cases.

**Type consistency:** `OrderIntent`/`GuardResult` (T3) consumed by executor (T4); `sizing.size` (T2) used by T3/T4;
`stats.summarize` (T1) used by backtest/paper; `Signal` from base consumed by executor/agent; repo methods (T1)
used by guards/executor/agent.

**Safety note:** live `place_order` (T5) is only reachable through the executor (T4), which **always runs the rails
first** and honors confirm-mode; dangerous mode changes require the passphrase (T7). No path places an order without
the guards.
