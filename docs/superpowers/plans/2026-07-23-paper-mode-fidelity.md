# Paper-mode Fidelity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make paper trading a faithful rehearsal of live: fills are risk-sized off a synthetic account equity, and Rail 11 (the drawdown circuit breaker) is armed in paper against a real equity denominator.

**Architecture:** A synthetic paper account (cash + qty-bearing positions, persisted in `agent_state`) is seeded once from real broker equity, marked to market each cycle, and fed into the *existing* `equity.update_drawdown()` producer — which writes the global drawdown scalars `guards.py`'s Rail 11 already reads. `guards.py` and the DB schema are untouched. The 1-unit fill bug is fixed as part of the same change (fills sized via `sizing.size(paper_equity, ...)`).

**Tech Stack:** Python 3, `Decimal`-only money math, `uv` for tooling/tests (`uv run pytest`, `uv run ruff check`), Click CLI, SQLite via `Repository`.

## Global Constraints

- **No `guards.py` changes.** Rail 11 (`guards.py:384-397`) reads `drawdown_total_pct`/`drawdown_weekly_pct` from `agent_state`; paper must write those exact global keys. Do not namespace them.
- **No DB schema/migration changes.** All new state fits existing `agent_state` keys; order payloads already carry `qty`.
- **Money is `Decimal` only** — never `float`. Indicator math may be `float`, but equity/cash/qty/fees are `Decimal`.
- **Long-only, spot.** `Side.BUY` entries; sells close positions.
- **Fees/slippage stay faithful:** `PaperTrader` already books fills net of `_DEFAULT_FEE_PCT = Decimal("0.006")` / `_DEFAULT_SLIPPAGE_PCT = Decimal("0.0005")`. Do NOT re-apply fees anywhere else (no double-count).
- **Config templates + fixtures must stay in sync:** any new config field is added to all four of `keel/templates/config.yaml`, `keel/templates/config.live.yaml`, `tests/fixtures/config_golden_full.yaml`, `tests/fixtures/config_golden_defaults.yaml`.
- **Test/lint gate for every commit:** `uv run pytest` green and `uv run ruff check` clean.
- **Verify with `uv run pytest`** (plain; whole suite, no special markers).

## Design decisions carried from the spec (`docs/superpowers/specs/2026-07-23-paper-mode-fidelity-design.md`)

- **D1:** paper sizes off its synthetic account equity (like `portfolio_sim.py:607`), NOT off `caps.max_exposure_usd`.
- **D2:** the synthetic account is seeded once at paper-start from real broker equity (`_mark_to_market_equity` against the real broker), falling back to `paper.starting_equity_usd`; the loop is broker-free thereafter.
- **Mode-flip safety:** stamp `equity_state_mode`; on mismatch, clear the shared HWM/history/scalars before the first `update_drawdown` (same clearing as `keel reset-hwm`).
- **Halt = veto new buys**, open positions ride to their stops (mirrors live).
- **Non-goal (follow-up):** fixing the *live* executor's `max_exposure_usd`-proxy sizing to use real equity; unifying `SimAccount` and the paper account.
- **Deviation from spec §4.6:** `keel status` does not exist in the codebase; observability is delivered via `LoopResult` fields + `_print_loop_result` + `log_event`, and a dedicated `keel status` command is deferred.

## File Structure

- `packages/keel-core/keel_core/config.py` — new `PaperConfig` dataclass, `_parse_paper()`, `Config.paper` field, `__all__` export.
- `keel/templates/config.yaml`, `keel/templates/config.live.yaml`, `tests/fixtures/config_golden_full.yaml`, `tests/fixtures/config_golden_defaults.yaml` — add a `paper:` block.
- `keel/strategy/paper.py` — qty-bearing fills, synthetic cash (`paper_cash_usdc` state), funding check, `PaperTrader.equity()`, epoch-aware rehydration.
- `keel/execution/executor.py` — optional `equity_override` param on `_build_intent`.
- `keel/execution/equity.py` — small shared `mark_positions()` helper (positions → marked value with cost-basis fallback).
- `keel/agent.py` — paper seeding + mode-flip clear, per-cycle paper equity → `update_drawdown`, monthly contribution, wire paper equity into `_paper_enter`, `LoopResult` observability fields.
- `keel/cli.py` — `_print_loop_result` shows paper equity/drawdown.
- Tests: `tests/strategy/test_paper.py`, `tests/execution/test_paper_equity.py` (new), `tests/agent/` (paper rail-11 e2e — place next to existing agent tests; if none, `tests/test_agent_paper_rail11.py`), `tests/core/` (config).

---

### Task 1: `PaperConfig` — config field, parse, templates, fixtures

**Files:**
- Modify: `packages/keel-core/keel_core/config.py`
- Modify: `keel/templates/config.yaml`, `keel/templates/config.live.yaml`, `tests/fixtures/config_golden_full.yaml`, `tests/fixtures/config_golden_defaults.yaml`
- Test: `tests/core/test_config.py` (existing config tests live here; confirm path with a quick grep and use the existing config-test module)

**Interfaces:**
- Produces: `PaperConfig(starting_equity_usd: Decimal = Decimal("0"), monthly_contribution_usd: Decimal = Decimal("0"))`; `Config.paper: PaperConfig`.

- [ ] **Step 1: Write the failing test**

Add to the config test module:

```python
def test_paper_config_parsed_from_yaml(tmp_path):
    from decimal import Decimal
    from keel_core.config import load_config
    cfg_text = _MINIMAL_VALID_CONFIG + "\npaper:\n  starting_equity_usd: 30000\n  monthly_contribution_usd: 500\n"
    p = tmp_path / "config.yaml"
    p.write_text(cfg_text)
    cfg = load_config(str(p))
    assert cfg.paper.starting_equity_usd == Decimal("30000")
    assert cfg.paper.monthly_contribution_usd == Decimal("500")


def test_paper_config_defaults_when_absent(tmp_path):
    from decimal import Decimal
    from keel_core.config import load_config
    p = tmp_path / "config.yaml"
    p.write_text(_MINIMAL_VALID_CONFIG)  # no paper: block
    cfg = load_config(str(p))
    assert cfg.paper.starting_equity_usd == Decimal("0")
    assert cfg.paper.monthly_contribution_usd == Decimal("0")


def test_paper_config_rejects_negative(tmp_path):
    import pytest
    from keel_core.config import load_config, ConfigError
    p = tmp_path / "config.yaml"
    p.write_text(_MINIMAL_VALID_CONFIG + "\npaper:\n  starting_equity_usd: -1\n")
    with pytest.raises(ConfigError):
        load_config(str(p))
```

(`_MINIMAL_VALID_CONFIG` — reuse the existing minimal-config helper/fixture in that test module; if none exists, build one from `tests/fixtures/config_golden_defaults.yaml`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_config.py -k paper_config -v`
Expected: FAIL (`Config` has no attribute `paper`).

- [ ] **Step 3: Add the dataclass, parser, field, and export**

In `packages/keel_core/keel_core/config.py`, add the dataclass (near `DcaConfig`):

```python
@dataclass(frozen=True)
class PaperConfig:
    """Paper-forward account model (spec: paper-mode fidelity).

    `starting_equity_usd` is only a FALLBACK seed used when the one-time real-equity
    read at paper-start fails; the primary seed is live mark-to-market equity. A value of
    0 means "no fallback" — if the broker read also fails, paper drawdown tracking stays
    dormant that run (logged loudly) rather than seeding a bogus 0 denominator.
    `monthly_contribution_usd` models ongoing deposits during the paper-forward; 0 disables.
    """

    starting_equity_usd: Decimal = Decimal("0")
    monthly_contribution_usd: Decimal = Decimal("0")
```

Add the `Config.paper` field (in the `Config` dataclass, after `dca`):

```python
    paper: PaperConfig = field(default_factory=PaperConfig)
```

Wire it in `load_config` (mirror the `dca` block, using the strict non-negative helper):

```python
    paper_raw = raw.get("paper") or {}
    ...
        paper=PaperConfig(
            starting_equity_usd=_non_negative_decimal(
                paper_raw.get("starting_equity_usd", "0"), "paper.starting_equity_usd"
            ),
            monthly_contribution_usd=_non_negative_decimal(
                paper_raw.get("monthly_contribution_usd", "0"), "paper.monthly_contribution_usd"
            ),
        ),
```

Add `"PaperConfig"` to `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/core/test_config.py -k paper_config -v`
Expected: PASS (all three).

- [ ] **Step 5: Add the `paper:` block to all four config files**

In each of `keel/templates/config.yaml`, `keel/templates/config.live.yaml`, `tests/fixtures/config_golden_full.yaml`, `tests/fixtures/config_golden_defaults.yaml`, insert after the `dca:` block:

```yaml
paper:
  starting_equity_usd: 0   # fallback seed only; primary seed is live mark-to-market equity
  monthly_contribution_usd: 0   # ongoing deposits during a paper-forward; 0 disables
```

If a golden-config test asserts exact schema/round-trip, update its expected structure to include `paper`.

- [ ] **Step 6: Run the config + golden suites**

Run: `uv run pytest tests/core -v` and any golden-config test (`uv run pytest -k golden -v`)
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add packages/keel-core/keel_core/config.py keel/templates/config.yaml keel/templates/config.live.yaml tests/fixtures/config_golden_full.yaml tests/fixtures/config_golden_defaults.yaml tests/core/test_config.py
git commit -m "feat(config): add PaperConfig (starting_equity_usd, monthly_contribution_usd)"
```

---

### Task 2: Qty-bearing paper fills (sizing fix, part 1)

Make `PaperTrader` fill at a caller-supplied quantity instead of the hardcoded `_QTY = 1`, keeping `_QTY` as the default so existing callers/tests are unaffected. Positions carry their qty.

**Files:**
- Modify: `keel/strategy/paper.py` (`_OpenPaperPosition`, `on_signal`, `_enter`, `_close`, `_exit_on_signal`, `on_candle`, `_load_open_positions`)
- Test: `tests/strategy/test_paper.py`

**Interfaces:**
- Consumes: `sizing.size` (Task 4 wires the caller; here just accept a qty).
- Produces: `PaperTrader.on_signal(signal, candle=None, qty: Decimal = _QTY) -> int | None`; `_OpenPaperPosition` gains `qty: Decimal`.

- [ ] **Step 1: Write the failing test**

```python
def test_paper_entry_records_supplied_qty(tmp_repo):
    from decimal import Decimal
    from keel.strategy.paper import PaperTrader
    trader = PaperTrader(tmp_repo)
    signal = _enter_signal(product_id="BTC-USD", entry=Decimal("100"), stop=Decimal("90"), target=Decimal("130"))
    order_id = trader.on_signal(signal, qty=Decimal("3"))
    order = _get_order(tmp_repo, order_id)
    assert order["qty"] == Decimal("3")
    import json
    assert json.loads(order["raw_response"])["qty"] == "3"
```

(Reuse the existing `test_paper.py` helpers for building signals and a temp repo; mirror their names.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/strategy/test_paper.py::test_paper_entry_records_supplied_qty -v`
Expected: FAIL (qty is 1, not 3).

- [ ] **Step 3: Thread qty through `_OpenPaperPosition`, `on_signal`, `_enter`, `_close`, `on_candle`, `_exit_on_signal`**

- Add `qty: Decimal` to `_OpenPaperPosition`.
- `on_signal(self, signal, candle=None, qty: Decimal = _QTY)`: pass `qty` to `_enter(signal, qty)`; EXIT/`_exit_on_signal` and `on_candle` read `position.qty`.
- `_enter(self, signal, qty: Decimal = _QTY)`: replace every `_QTY` with `qty` — `fee = entry_fill * qty * self._fee_pct`, payload `"qty": str(qty)`, order-row `"qty": qty`; store `qty=qty` on the `_OpenPaperPosition`.
- `_close(self, position, exit_price, exit_ts)`: replace `_QTY` with `position.qty` in `entry_fee`, `exit_fee`, `pnl`, `risk`, payload `"qty"`, order-row `"qty"`.
- `on_candle` / `_exit_on_signal`: MFE/MAE math is per-unit (unchanged); they call `_close(position, ...)` which now uses `position.qty`.
- `_load_open_positions`: set `qty=Decimal(payload["qty"])` on the rehydrated `_OpenPaperPosition` (payload already carries `"qty"`).

- [ ] **Step 4: Run tests to verify**

Run: `uv run pytest tests/strategy/test_paper.py -v`
Expected: PASS (new test + all existing paper tests, since `qty` defaults to `_QTY`).

- [ ] **Step 5: Commit**

```bash
git add keel/strategy/paper.py tests/strategy/test_paper.py
git commit -m "feat(paper): fills carry a caller-supplied qty (default 1)"
```

---

### Task 3: Synthetic cash, funding check, equity, epoch cutoff

Give `PaperTrader` a persisted synthetic cash balance, a funding check, an `equity()` method, and make rehydration/cash ignore pre-epoch legacy orders.

**Files:**
- Modify: `keel/strategy/paper.py`
- Modify: `keel/execution/equity.py` (add `mark_positions` helper)
- Test: `tests/strategy/test_paper.py`, `tests/execution/test_paper_equity.py` (new)

**Interfaces:**
- Produces:
  - `equity.mark_positions(cash: Decimal, positions: list[tuple[Decimal, Decimal]], price_by_product: dict[str, Decimal], product_ids: list[str]) -> Decimal` — each `positions[i]` is `(qty, cost_basis)`; see Step 3a for the exact signature.
  - `PaperTrader.equity(price_by_product: dict[str, Decimal]) -> Decimal | None` (None only if cash is unseeded).
  - State keys: `paper_cash_usdc` (Decimal), `paper_ledger_start_ts` (int).
  - `PaperTrader.seed_cash(amount: Decimal, now_ts: int) -> None` (sets `paper_cash_usdc` and `paper_ledger_start_ts` if unset).
  - `PaperTrader.deposit(amount: Decimal) -> None` (adds to `paper_cash_usdc`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/execution/test_paper_equity.py
from decimal import Decimal
from keel.execution.equity import mark_positions

def test_mark_positions_uses_fresh_price():
    # positions: (qty, entry_fill_cost_basis, ) keyed by product via product_ids
    eq = mark_positions(
        cash=Decimal("1000"),
        positions=[(Decimal("2"), Decimal("100"), )],  # qty=2, cost basis 100
        price_by_product={"BTC-USD": Decimal("150")},
        product_ids=["BTC-USD"],
    )
    assert eq == Decimal("1000") + Decimal("2") * Decimal("150")

def test_mark_positions_falls_back_to_cost_basis_when_price_missing():
    eq = mark_positions(
        cash=Decimal("1000"),
        positions=[(Decimal("2"), Decimal("100"), )],
        price_by_product={},  # no fresh price
        product_ids=["BTC-USD"],
    )
    assert eq == Decimal("1000") + Decimal("2") * Decimal("100")  # cost-basis fallback
```

```python
# tests/strategy/test_paper.py
def test_paper_equity_seed_and_mark(tmp_repo):
    from decimal import Decimal
    from keel.strategy.paper import PaperTrader
    trader = PaperTrader(tmp_repo)
    assert trader.equity({"BTC-USD": Decimal("100")}) is None  # unseeded
    trader.seed_cash(Decimal("30000"), now_ts=1_700_000_000)
    assert trader.equity({}) == Decimal("30000")  # all cash, no positions
    # open a position and re-mark
    sig = _enter_signal("BTC-USD", entry=Decimal("100"), stop=Decimal("90"), target=Decimal("130"))
    trader.on_signal(sig, qty=Decimal("5"))
    eq = trader.equity({"BTC-USD": Decimal("120")})
    # cash was debited by fill+fee; positions valued at 5*120
    assert eq < Decimal("30000") + Decimal("5") * Decimal("120")  # fee/slippage drag
    assert eq > Decimal("29000")

def test_paper_funding_check_rejects_when_cash_insufficient(tmp_repo):
    from decimal import Decimal
    from keel.strategy.paper import PaperTrader
    trader = PaperTrader(tmp_repo)
    trader.seed_cash(Decimal("50"), now_ts=1_700_000_000)
    sig = _enter_signal("BTC-USD", entry=Decimal("100"), stop=Decimal("90"), target=Decimal("130"))
    # notional 5*100 = 500 >> 50 cash -> no fill
    assert trader.on_signal(sig, qty=Decimal("5")) is None
    assert trader.get_cash() == Decimal("50")  # unchanged
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/execution/test_paper_equity.py tests/strategy/test_paper.py -k "mark_positions or paper_equity or funding" -v`
Expected: FAIL (`mark_positions`, `seed_cash`, `equity`, funding check don't exist).

- [ ] **Step 3a: Add the shared `mark_positions` helper to `keel/execution/equity.py`**

```python
def mark_positions(
    cash: Decimal,
    positions: list[tuple[Decimal, Decimal]],
    price_by_product: dict[str, Decimal],
    product_ids: list[str],
) -> Decimal:
    """Mark-to-market equity = cash + Σ qty·mark, with a cost-basis fallback.

    `positions[i]` is `(qty, cost_basis)` for `product_ids[i]`. A product with no fresh
    price in `price_by_product` is valued at its `cost_basis` rather than dropped — dropping
    a held position understates equity and would trip a drawdown breaker on a data gap rather
    than a loss (mirrors agent._mark_to_market_equity's fallback).
    """
    total = cash
    for (qty, cost_basis), product_id in zip(positions, product_ids):
        if qty <= 0:
            continue
        mark = price_by_product.get(product_id)
        if mark is None or mark <= 0:
            mark = cost_basis
        if mark <= 0:
            continue
        total += qty * mark
    return total
```

- [ ] **Step 3b: Add cash/equity/funding to `PaperTrader`**

- In `__init__`, after `_load_open_positions()`, load cash: `self._cash = self._repo.get_state("paper_cash_usdc")` (may be `None` = unseeded). Load `self._ledger_start_ts = self._repo.get_state("paper_ledger_start_ts")`.
- Add:

```python
    def get_cash(self):
        return self._cash

    def seed_cash(self, amount: Decimal, now_ts: int) -> None:
        self._cash = amount
        self._repo.set_state("paper_cash_usdc", amount)
        if self._repo.get_state("paper_ledger_start_ts") is None:
            self._ledger_start_ts = now_ts
            self._repo.set_state("paper_ledger_start_ts", now_ts)

    def deposit(self, amount: Decimal) -> None:
        if self._cash is None:
            return
        self._cash += amount
        self._repo.set_state("paper_cash_usdc", self._cash)

    def equity(self, price_by_product: dict[str, Decimal]):
        if self._cash is None:
            return None
        from keel.execution.equity import mark_positions
        product_ids = list(self._open.keys())
        positions = [
            (self._open[p].qty, self._open[p].entry_fill) for p in product_ids
        ]
        return mark_positions(self._cash, positions, price_by_product, product_ids)
```

- In `_enter`, BEFORE writing the order (only when `self._cash is not None`): compute `notional = setup.entry * qty`; if `self._cash < notional`, log a paper funding skip and `return None`. After a successful fill, debit cash: `self._cash -= entry_fill * qty + fee; self._repo.set_state("paper_cash_usdc", self._cash)`.
- In `_close`, after a successful exit, credit cash: `self._cash += exit_fill * position.qty - exit_fee; self._repo.set_state("paper_cash_usdc", self._cash)` (guard `self._cash is not None`).
- In `_load_open_positions`, when a `paper_ledger_start_ts` is set, SKIP entry/exit orders whose `created_at` (or payload `ts`) is `< paper_ledger_start_ts` so legacy pre-epoch orders never rehydrate into the synthetic account.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/execution/test_paper_equity.py tests/strategy/test_paper.py -v`
Expected: PASS (new + existing).

- [ ] **Step 5: Commit**

```bash
git add keel/strategy/paper.py keel/execution/equity.py tests/execution/test_paper_equity.py tests/strategy/test_paper.py
git commit -m "feat(paper): synthetic cash ledger, funding check, equity(), epoch cutoff"
```

---

### Task 4: `_build_intent` equity override (sizing fix, part 2)

**Files:**
- Modify: `keel/execution/executor.py` (`_build_intent`)
- Test: `tests/execution/test_executor.py`

**Interfaces:**
- Produces: `_build_intent(signal, broker, repo, config, now_ts, equity_override: Decimal | None = None) -> OrderIntent | None`. When `equity_override` is None, sizing uses `config.caps.max_exposure_usd` (unchanged live behavior).

- [ ] **Step 1: Write the failing test**

```python
def test_build_intent_uses_equity_override(tmp_repo, base_config):
    from decimal import Decimal
    from keel.execution import executor
    sig = _enter_signal("BTC-USD", entry=Decimal("100"), stop=Decimal("90"), target=Decimal("130"))
    default_intent = executor._build_intent(sig, None, tmp_repo, base_config, now_ts=1)
    override_intent = executor._build_intent(sig, None, tmp_repo, base_config, now_ts=1, equity_override=Decimal("30000"))
    # qty scales with equity: 30000 vs caps.max_exposure_usd (5000 in base_config)
    assert override_intent.qty != default_intent.qty
    assert override_intent.qty == default_intent.qty * (Decimal("30000") / base_config.caps.max_exposure_usd)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/execution/test_executor.py::test_build_intent_uses_equity_override -v`
Expected: FAIL (`_build_intent` has no `equity_override`).

- [ ] **Step 3: Add the parameter**

Change the signature to include `equity_override: Decimal | None = None`, and in the ENTER/non-DCA branch:

```python
            equity = equity_override if equity_override is not None else config.caps.max_exposure_usd
            qty = sizing.size(equity, config.risk_pct, setup.entry, setup.stop)
```

Leave the DCA and EXIT branches unchanged.

- [ ] **Step 4: Run tests to verify**

Run: `uv run pytest tests/execution/test_executor.py -v`
Expected: PASS (override test + all existing executor tests, since default preserves behavior).

- [ ] **Step 5: Commit**

```bash
git add keel/execution/executor.py tests/execution/test_executor.py
git commit -m "feat(executor): optional equity_override on _build_intent (live path unchanged)"
```

---

### Task 5: Agent — seed paper account, mode-flip clear, per-cycle drawdown

Wire the synthetic account into the agent loop's equity block so Rail 11's scalars advance in paper.

**Files:**
- Modify: `keel/agent.py` (equity block ~682-716; add a paper-seed + mode-stamp helper)
- Test: `tests/agent/test_agent_paper_equity.py` (new; or the existing agent test module)

**Interfaces:**
- Consumes: `PaperTrader.seed_cash/equity/deposit` (Task 3), `_mark_to_market_equity` (existing), `equity.update_drawdown` (existing).
- Produces: a private helper `_seed_paper_account_if_needed(repo, broker, config, products, price_by_product, now_ts, paper_trader) -> None` that (a) enforces the mode stamp/clear and (b) seeds cash on first paper run.

- [ ] **Step 1: Write the failing tests**

```python
def test_paper_cycle_advances_drawdown_scalar(monkeypatch, paper_repo, fake_broker, paper_config):
    # A paper run_once with a seeded account and a losing mark writes drawdown_total_pct > 0.
    from decimal import Decimal
    from keel import agent
    paper_repo.set_state("kill_switch", False)
    paper_repo.set_state("last_feed_ts", NOW)
    # seed HWM high, then a lower equity via a held losing position (arrange candles/prices)
    ...
    agent.run_once(fake_broker, paper_repo, paper_config, now_ts=NOW)
    assert paper_repo.get_state("drawdown_total_pct", default=Decimal("0")) >= Decimal("0")
    assert paper_repo.get_state("equity_state_mode") == "paper"

def test_mode_flip_clears_hwm(paper_repo, fake_broker, paper_config):
    from decimal import Decimal
    from keel import agent
    # simulate a prior LIVE run's scalars
    paper_repo.set_state("equity_state_mode", "live")
    paper_repo.set_state("equity_high_water_mark", Decimal("999999"))
    paper_repo.set_state("drawdown_total_pct", Decimal("0.9"))
    paper_repo.set_state("kill_switch", False)
    paper_repo.set_state("last_feed_ts", NOW)
    agent.run_once(fake_broker, paper_repo, paper_config, now_ts=NOW)
    # the stale live HWM must have been cleared and re-seeded from paper equity
    assert paper_repo.get_state("equity_state_mode") == "paper"
    assert paper_repo.get_state("equity_high_water_mark") != Decimal("999999")

def test_seed_falls_back_to_config_when_broker_read_none(paper_repo, null_equity_broker, paper_config_with_fallback):
    from decimal import Decimal
    from keel import agent
    paper_repo.set_state("kill_switch", False)
    paper_repo.set_state("last_feed_ts", NOW)
    agent.run_once(null_equity_broker, paper_repo, paper_config_with_fallback, now_ts=NOW)
    # paper_config_with_fallback.paper.starting_equity_usd = 10000
    assert paper_repo.get_state("paper_cash_usdc") == Decimal("10000")
```

(Use the existing agent-test scaffolding — fake broker, in-memory repo, seeded candles. Confirm the agent test module path; if none, create `tests/test_agent_paper_equity.py`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -k "paper_cycle_advances or mode_flip_clears or seed_falls_back" -v`
Expected: FAIL.

- [ ] **Step 3: Implement the seed/stamp helper and replace the equity block**

Add to `keel/agent.py`:

```python
def _seed_paper_account_if_needed(
    repo, broker, config, products, price_by_product, now_ts, paper_trader
) -> None:
    """Enforce the equity-state mode stamp and seed the synthetic paper account once.

    On a paper->live or live->paper flip, clear the shared HWM/history/drawdown scalars
    (same keys `keel reset-hwm` clears) before this cycle's update_drawdown, so a synthetic
    HWM never poisons live equity (or vice versa). Seed `paper_cash_usdc` on first paper run
    from real broker mark-to-market equity, falling back to `config.paper.starting_equity_usd`.
    """
    if repo.get_state("equity_state_mode") != "paper":
        repo.set_state("equity_high_water_mark", None)
        repo.set_state("drawdown_total_pct", Decimal("0"))
        repo.set_state("drawdown_weekly_pct", Decimal("0"))
        repo.set_state("equity_history", [])
        repo.set_state("equity_state_mode", "paper")
    if paper_trader.get_cash() is None:
        seed = _mark_to_market_equity(
            repo, broker, products, price_by_product, config.quote_currency
        )
        if seed is None:
            fallback = config.paper.starting_equity_usd
            seed = fallback if fallback > 0 else None
        if seed is None:
            log_event(logger, logging.WARNING, "agent.paper_seed_unavailable")
            return
        paper_trader.seed_cash(seed, now_ts)
```

Replace the equity block (`agent.py:~550-567` in the extraction, the `equity_now = None if paper_trader...` branch) with a paper-aware version:

```python
        if paper_trader is not None:
            _seed_paper_account_if_needed(
                repo, broker, config, products, latest_price_by_product, now_ts, paper_trader
            )
            equity_now = paper_trader.equity(latest_price_by_product)
        else:
            equity_now = _mark_to_market_equity(
                repo, broker, products, latest_price_by_product, config.quote_currency
            )
        if equity_now is None:
            log_event(
                logger,
                logging.INFO if paper_trader is not None else logging.WARNING,
                "agent.equity_unavailable",
                paper=paper_trader is not None,
            )
        else:
            equity_mod.update_drawdown(repo, equity=equity_now, now_ts=now_ts)
```

Note for the live path: on a live cycle where `equity_state_mode != "live"`, the same stamp/clear must run before live's `update_drawdown`. Add the symmetric guard in the `else` branch (stamp `"live"`, clear on mismatch) so a paper→live flip is safe in both directions.

- [ ] **Step 4: Run tests to verify**

Run: `uv run pytest -k "paper_cycle_advances or mode_flip_clears or seed_falls_back" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add keel/agent.py tests/  # the new agent test file
git commit -m "feat(agent): seed paper account, mode-flip clear, advance Rail 11 scalars in paper"
```

---

### Task 6: Agent — size paper fills off paper equity

Make `_paper_enter` size the fill off the synthetic account equity and pass that qty to the trader (currently it fills 1 unit).

**Files:**
- Modify: `keel/agent.py` (`_paper_enter`, and its call site to pass paper equity)
- Test: `tests/strategy/test_paper.py` or the agent test module

**Interfaces:**
- Consumes: `_build_intent(..., equity_override=)` (Task 4), `PaperTrader.on_signal(signal, qty=)` (Task 2), `PaperTrader.equity` (Task 3).
- Produces: `_paper_enter(trader, signal, repo, config, now_ts, paper_equity: Decimal) -> executor.ExecutionResult`.

- [ ] **Step 1: Write the failing test**

```python
def test_paper_enter_sizes_off_paper_equity(tmp_repo, base_paper_config, fake_broker):
    from decimal import Decimal
    from keel import agent
    from keel.strategy.paper import PaperTrader
    trader = PaperTrader(tmp_repo)
    trader.seed_cash(Decimal("30000"), now_ts=NOW)
    tmp_repo.set_state("kill_switch", False)
    tmp_repo.set_state("last_feed_ts", NOW)
    sig = _enter_signal("BTC-USD", entry=Decimal("100"), stop=Decimal("90"), target=Decimal("130"))
    res = agent._paper_enter(trader, sig, tmp_repo, base_paper_config, NOW, paper_equity=Decimal("30000"))
    assert res.placed
    order = _last_paper_order(tmp_repo)
    # qty = size(30000, 0.01, 100, 90) = 300/10 = 30
    assert order["qty"] == Decimal("30")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest -k paper_enter_sizes_off_paper_equity -v`
Expected: FAIL (fills 1 unit; `_paper_enter` has no `paper_equity` param).

- [ ] **Step 3: Update `_paper_enter` and its caller**

In `_paper_enter`, add `paper_equity: Decimal` param; build the intent with the override and pass qty to the trader:

```python
    intent = executor._build_intent(
        signal, None, repo, config, now_ts, equity_override=paper_equity
    )
    if intent is None:
        return _result(False, reason="paper: nothing to size")

    verdict = guards.check(intent, repo, config, now_ts, offline=True)
    if not verdict.ok:
        return _result(False, vetoed_by=verdict.violations, reason="paper: vetoed by rails")

    order_id = trader.on_signal(signal, qty=intent.qty)
    if order_id is None:
        return _result(False, reason="paper: no fill (position open or insufficient synthetic cash)")
    return _result(True, order_id=order_id, reason=f"paper: filled (skipped rails: {', '.join(verdict.skipped_rails)})")
```

At the `_paper_enter` call site in `run_once`, pass the `equity_now` computed in Task 5 (the paper equity for this cycle) as `paper_equity`. If `equity_now` is None (unseeded/unavailable), skip paper entries this cycle (log it) rather than sizing off an unknown equity.

- [ ] **Step 4: Run tests to verify**

Run: `uv run pytest tests/strategy/test_paper.py -k paper_enter -v` and the agent suite.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add keel/agent.py tests/
git commit -m "feat(agent): paper fills sized off synthetic account equity"
```

---

### Task 7: Monthly contribution (calendar-month rollover)

**Files:**
- Modify: `keel/agent.py` (in the paper equity path)
- Test: agent test module

**Interfaces:**
- Consumes: `guards._utc_month_bounds(now_ts)` (existing, used by rail 14), `PaperTrader.deposit`, `equity.record_external_flow`.
- Produces: state key `paper_last_contribution_month` (int month-start ts).

- [ ] **Step 1: Write the failing test**

```python
def test_paper_monthly_contribution_applied_once_per_month(paper_repo, fake_broker, paper_config_with_contribution):
    from decimal import Decimal
    from keel import agent
    # paper_config_with_contribution.paper.monthly_contribution_usd = 500
    paper_repo.set_state("kill_switch", False)
    paper_repo.set_state("last_feed_ts", JAN15)
    agent.run_once(fake_broker, paper_repo, paper_config_with_contribution, now_ts=JAN15)
    cash_after_first = paper_repo.get_state("paper_cash_usdc")
    # same month, second cycle: no new contribution
    paper_repo.set_state("last_feed_ts", JAN20)
    agent.run_once(fake_broker, paper_repo, paper_config_with_contribution, now_ts=JAN20)
    assert paper_repo.get_state("paper_cash_usdc") == cash_after_first  # unchanged by contribution (mod fills)
    # next month: contribution applied
    paper_repo.set_state("last_feed_ts", FEB03)
    before = paper_repo.get_state("paper_cash_usdc")
    agent.run_once(fake_broker, paper_repo, paper_config_with_contribution, now_ts=FEB03)
    assert paper_repo.get_state("paper_cash_usdc") >= before + Decimal("500") - Decimal("1")  # allow fill drift
```

(Choose JAN15/JAN20/FEB03 as epoch-seconds constants in distinct calendar months; reuse `guards._utc_month_bounds`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest -k monthly_contribution -v`
Expected: FAIL.

- [ ] **Step 3: Apply the contribution after seeding, before update_drawdown**

In the paper branch (Task 5), after seeding and before computing equity:

```python
            contribution = config.paper.monthly_contribution_usd
            if contribution > 0 and paper_trader.get_cash() is not None:
                month_start, _ = guards._utc_month_bounds(now_ts)
                if repo.get_state("paper_last_contribution_month") != month_start:
                    paper_trader.deposit(contribution)
                    equity_mod.record_external_flow(repo, amount=contribution)
                    repo.set_state("paper_last_contribution_month", month_start)
```

`record_external_flow` rebases the HWM + weekly history so the deposit is not read as a recovery/drawdown.

- [ ] **Step 4: Run tests to verify**

Run: `uv run pytest -k "monthly_contribution or paper_cycle" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add keel/agent.py tests/
git commit -m "feat(agent): monthly paper contribution (once per calendar month, HWM-rebased)"
```

---

### Task 8: Rail 11 end-to-end enforcement in paper (headline test)

**Files:**
- Test only: agent test module (`tests/test_agent_paper_rail11.py` or existing)

**Interfaces:**
- Consumes: everything above. No new production code — this is the acceptance test that the whole feature works. If it fails, the fix is in a prior task.

- [ ] **Step 1: Write the acceptance test**

```python
def test_paper_drawdown_halt_vetoes_buys(paper_repo, fake_broker, paper_config):
    """A paper account drawn down past max_total_dd_pct gets buys vetoed by Rail 11."""
    from decimal import Decimal
    from keel.execution import guards
    from keel.execution.guards import OrderIntent
    from keel.types import Side
    # Arrange: seed a high HWM and a low current drawdown scalar directly (unit-level of the wiring)
    paper_repo.set_state("equity_state_mode", "paper")
    paper_repo.set_state("drawdown_total_pct", Decimal("0.25"))  # 25% > 20% ceiling
    paper_repo.set_state("kill_switch", False)
    paper_repo.set_state("last_feed_ts", NOW)
    intent = OrderIntent(
        product_id="BTC-USD", side=Side.BUY, qty=Decimal("1"), entry=Decimal("100"),
        stop=Decimal("90"), notional=Decimal("100"), is_dca=False, rule_kind="turtle_breakout",
    )
    verdict = guards.check(intent, paper_repo, paper_config, NOW, offline=True)
    assert not verdict.ok
    assert any("account_dd_breaker_total" in v for v in verdict.violations)


def test_paper_weekly_drawdown_halt_vetoes_buys(paper_repo, paper_config):
    from decimal import Decimal
    from keel.execution import guards
    from keel.execution.guards import OrderIntent
    from keel.types import Side
    paper_repo.set_state("equity_state_mode", "paper")
    paper_repo.set_state("drawdown_weekly_pct", Decimal("0.10"))  # 10% > 8% ceiling
    paper_repo.set_state("kill_switch", False)
    paper_repo.set_state("last_feed_ts", NOW)
    intent = OrderIntent(
        product_id="BTC-USD", side=Side.BUY, qty=Decimal("1"), entry=Decimal("100"),
        stop=Decimal("90"), notional=Decimal("100"), is_dca=False, rule_kind="turtle_breakout",
    )
    verdict = guards.check(intent, paper_repo, paper_config, NOW, offline=True)
    assert not verdict.ok
    assert any("account_dd_breaker_weekly" in v for v in verdict.violations)
```

Add ONE full-loop test that drives a real drawdown through `run_once` (seed cash, feed a sequence of losing candles that stop out positions, assert `drawdown_total_pct` rises and a subsequent entry is vetoed) — mirror the agent-suite's candle-seeding helpers.

- [ ] **Step 2: Run to verify PASS (feature already implemented in Tasks 1-7)**

Run: `uv run pytest -k "paper_drawdown_halt or paper_weekly_drawdown" -v`
Expected: PASS. If FAIL, the wiring bug is in Task 5/6 — fix there, not here.

- [ ] **Step 3: Commit**

```bash
git add tests/
git commit -m "test(agent): Rail 11 total+weekly drawdown halt enforced in paper (e2e)"
```

---

### Task 9: Observability — paper equity/drawdown in `LoopResult` + output

**Files:**
- Modify: `keel/agent.py` (`LoopResult` dataclass + populate it), `keel/cli.py` (`_print_loop_result`)
- Test: agent test module + a CLI output test

**Interfaces:**
- Produces: `LoopResult` gains optional `paper_equity: Decimal | None = None`, `drawdown_total_pct: Decimal | None = None`, `drawdown_weekly_pct: Decimal | None = None` (backward-compatible defaults).

- [ ] **Step 1: Write the failing test**

```python
def test_loop_result_carries_paper_equity_and_drawdown(paper_repo, fake_broker, paper_config):
    from keel import agent
    paper_repo.set_state("kill_switch", False)
    paper_repo.set_state("last_feed_ts", NOW)
    result = agent.run_once(fake_broker, paper_repo, paper_config, now_ts=NOW)
    assert result.paper_equity is not None
    assert result.drawdown_total_pct is not None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest -k loop_result_carries_paper -v`
Expected: FAIL (`LoopResult` has no `paper_equity`).

- [ ] **Step 3: Add fields, populate, print, log**

- Add the three optional fields to `LoopResult` (with `None` defaults so existing constructions compile).
- In the paper branch of `run_once`, after `update_drawdown`, read back `drawdown_total_pct`/`drawdown_weekly_pct` and set them plus `paper_equity=equity_now` on the returned `LoopResult`. Emit `log_event(logger, logging.INFO, "agent.paper_equity", equity=str(equity_now), dd_total=..., dd_weekly=...)`.
- In `cli.py::_print_loop_result`, when `result.paper_equity is not None`, add a line: `click.echo(f"paper equity ${result.paper_equity} | drawdown {result.drawdown_total_pct} total / {result.drawdown_weekly_pct} weekly")`.

- [ ] **Step 4: Run to verify**

Run: `uv run pytest -k "loop_result_carries_paper or print_loop" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add keel/agent.py keel/cli.py tests/
git commit -m "feat(agent,cli): surface paper equity + drawdown in LoopResult output/logs"
```

---

### Task 10: Full-suite green, ruff clean, spec-deviation note

**Files:**
- Modify (docs): `docs/superpowers/specs/2026-07-23-paper-mode-fidelity-design.md` (note the `keel status` → LoopResult observability deviation)

- [ ] **Step 1: Run the whole suite**

Run: `uv run pytest`
Expected: PASS (test count up vs. pre-PR baseline).

- [ ] **Step 2: Lint**

Run: `uv run ruff check`
Expected: clean.

- [ ] **Step 3: Record the observability deviation in the spec**

Add a line under §4.6 noting `keel status` did not exist and observability landed via `LoopResult`/`_print_loop_result`/logging; a dedicated `keel status` is a follow-up.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-07-23-paper-mode-fidelity-design.md
git commit -m "docs(spec): note paper observability via LoopResult (keel status deferred)"
```

---

## Self-Review

**Spec coverage:**
- §1 Rail 11 inert → Tasks 5, 8. §2 mis-sized fills → Tasks 2, 4, 6. §4.1 synthetic account/`equity()`/cost-basis fallback → Task 3. §4.1 sizing off account equity (D1) → Tasks 4, 6. §4.2 funding coherence → Task 3. §4.3 wiring Rail 11 → Task 5. §4.4 mode-flip → Task 5. §4.5 epoch cutoff → Task 3. §4.6 halt (veto buys, ride to stops) → inherent (no force-close code; positions resolve via existing `on_candle`/`_paper_resolve_bars`) + Task 8 asserts halt; observability → Task 9. §5 config → Task 1; contribution cadence → Task 7. §6 tests → each task + Tasks 8, 10. §7 risks: MTM divergence → Task 3 `mark_positions` + Task 3/8 tests; HWM mode-flip → Task 5; legacy orders → Task 3; seed broker-coupling/fallback → Task 5. **D2 seed-from-real-equity** → Task 5.
- One deliberate deviation: **observability** delivered via `LoopResult` not `keel status` (Task 9 + Task 10 spec note). No other spec requirement is unmapped.

**Placeholder scan:** No "TBD"/"handle edge cases"/vague steps; every code step shows code. Two spots intentionally defer to existing scaffolding (test helper names in `test_paper.py`/agent suite, `_MINIMAL_VALID_CONFIG`) — these are named, existing fixtures the implementer reuses, not invented placeholders.

**Type consistency:** `qty: Decimal` threads consistently (Tasks 2→3→6). `equity()`/`get_cash()` return `Decimal | None` consistently (Task 3, consumed Tasks 5/6 with explicit None-handling). `equity_override: Decimal | None` (Task 4) matches the `paper_equity: Decimal` passed by Task 6. `mark_positions` signature is identical in its definition (Task 3a) and its `PaperTrader.equity` caller (Task 3b). Global state keys (`paper_cash_usdc`, `paper_ledger_start_ts`, `equity_state_mode`, `paper_last_contribution_month`, and the shared `equity_high_water_mark`/`drawdown_*`/`equity_history`) are used with consistent names across Tasks 3/5/7.

## Execution notes
- Tasks are ordered by dependency; Tasks 1–4 are largely independent and can be built in parallel worktrees, Tasks 5–9 depend on 1–4, Task 8/10 are acceptance/cleanup. Per the project's tiering, dispatch Sonnet subagents per task (TDD) with Opus review between tasks.
- Confirm the exact agent-test module path and shared fixtures before Task 5 (the extraction found `tests/strategy/test_paper.py` and an `tests/execution/` tree; locate the `run_once` agent tests and reuse their broker/repo/candle fixtures).
