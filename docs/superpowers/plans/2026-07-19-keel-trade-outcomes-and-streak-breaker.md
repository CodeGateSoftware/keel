# Closed-Trade Outcomes, Rail 16, and Waking Rail 11 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record closed-trade outcomes, use them to drive a new consecutive-loss circuit breaker (rail 16), and use marked-to-market equity to wake rail 11 — which is currently dormant because nothing writes its inputs.

**Architecture:** A producer/consumer split that honours the pattern rail 11 was designed for but never got. The **agent loop** owns computation (it is the only component with prices): on trade close it appends a `trade_outcomes` row and updates streak counters; every cycle it marks positions to market and writes drawdown scalars. **`guards.py` stays pure and stateless** — every rail reads precomputed scalars and decides. No rail gains I/O, a price feed, or a clock beyond `now_ts`.

**Tech Stack:** Python 3.12, stdlib `sqlite3` (no ORM), `click`, `pytest`, `ruff`, `mypy`, `uv`.

**Spec:** `docs/superpowers/specs/2026-07-19-keel-trade-outcomes-and-streak-breaker-design.md`

## Global Constraints

- Python `>=3.12`; ruff `line-length = 100`, `select = ["E", "F", "I", "UP"]`, `ignore = ["UP042"]`.
- Money is `Decimal` in the Python API and exact `TEXT` in SQLite, matching `candles`/`orders`/`broker_subscriptions`. **Never `float`.**
- `keel_core` is stdlib-only and must stay dependency-free.
- `mypy` strict applies to `keel_broker_*` only; `keel.*`, `tests.*`, `keel_core.*` stay `ignore_errors = true`. Do not tighten them here.
- `uv run pytest tests/baseline/ -v` must pass with `tests/fixtures/baseline_backtest.json` **byte-unchanged** at the end of every task. Backtesting never runs the rails, so any movement is a bug — investigate, never regenerate.
- Run everything through `uv run`. Never commit `keel.db`, `*.log`, or `transactions/`.
- After every task: `uv run pytest -q && uv run ruff check . && uv run mypy` must pass.

### Test-authoring rule (binds every task)

**Every positive assertion must be paired with its negative.** A test that only asserts a veto *fires* does not prove the rail caused it; a test that only asserts a veto is *absent* would pass against a deleted rail. This rule exists because the previous plan's review found four rail tests that survived deleting the entire rail block.

Concretely, for every new rail test:
- assert the **exact** violation key set with `_keys(result) == {...}`, not `in` — so another rail cannot satisfy it;
- pair "vetoes when tripped" with "does **not** veto when not tripped", using otherwise-identical inputs;
- for anything that opens a gate, assert the gate is also a **ceiling** (something above it is still refused).

**Two rails in this repo shipped unable to fire.** Rail 11 is dormant today. Treat "the test passes" as insufficient evidence throughout: where a step says to verify by mutation, do it.

---

### Task 1: Give a tracked position its entry context

`position_rule:<product_id>` currently holds a bare rule-name string. An outcome row needs `opened_at`, `entry_fill` and `qty`, so this key becomes a small dict. Purely a state-shape change: no new behaviour, no new table.

**Files:**
- Modify: `keel/agent.py` (`_handle_exits` ~line 200 and ~235; the ENTER path ~line 404)
- Test: `tests/test_agent.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `agent_state["position_rule:<product_id>"]` is now `dict | None` with keys `rule_name: str`, `opened_at: int`, `entry_fill: Decimal`, `qty: Decimal`. Tasks 3 and 5 read these exact key names. A helper `keel.agent._position_state(repo, product_id) -> dict | None` returns it, tolerating the legacy bare-string form.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_agent.py`:

```python
def test_opening_a_position_records_entry_context(repo, monkeypatch) -> None:
    """An outcome row later needs opened_at/entry_fill/qty, so the ENTER path must record them."""
    from keel.agent import _position_state

    _seed_open_position(repo, PRODUCT, Decimal("0.5"), Decimal("100"), ts=1_000)
    repo.set_state(
        f"position_rule:{PRODUCT}",
        {
            "rule_name": "turtle_breakout",
            "opened_at": 1_000,
            "entry_fill": Decimal("100"),
            "qty": Decimal("0.5"),
        },
    )

    state = _position_state(repo, PRODUCT)
    assert state is not None
    assert state["rule_name"] == "turtle_breakout"
    assert state["opened_at"] == 1_000
    assert state["entry_fill"] == Decimal("100")
    assert isinstance(state["entry_fill"], Decimal)
    assert state["qty"] == Decimal("0.5")


def test_position_state_tolerates_the_legacy_bare_string(repo) -> None:
    """Existing DBs hold a bare rule-name string; reading one must not crash mid-upgrade."""
    from keel.agent import _position_state

    repo.set_state(f"position_rule:{PRODUCT}", "turtle_breakout")

    state = _position_state(repo, PRODUCT)
    assert state is not None
    assert state["rule_name"] == "turtle_breakout"
    assert state["opened_at"] is None
    assert state["entry_fill"] is None


def test_position_state_is_none_when_unset(repo) -> None:
    """The negative: no tracked position must read as None, not as an empty dict."""
    from keel.agent import _position_state

    assert _position_state(repo, PRODUCT) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_agent.py -k position_state -v`
Expected: FAIL — `ImportError: cannot import name '_position_state' from 'keel.agent'`

- [ ] **Step 3: Add the helper**

In `keel/agent.py`, add above `_handle_exits`:

```python
def _position_state(repo: Repository, product_id: str) -> dict[str, Any] | None:
    """The tracked position for `product_id`, or `None` if nothing is held.

    `agent_state["position_rule:<product>"]` used to be a bare rule-name string and is now a dict
    carrying the entry context a `trade_outcomes` row needs. A database written by the previous
    version still holds the string form, so it is normalised here rather than migrated: the entry
    fields read back as `None`, which the outcome producer treats as "cannot attribute this trade"
    rather than guessing a price.
    """
    raw = repo.get_state(f"position_rule:{product_id}")
    if raw is None:
        return None
    if isinstance(raw, str):
        return {"rule_name": raw, "opened_at": None, "entry_fill": None, "qty": None}
    return raw
```

- [ ] **Step 4: Write the entry context on ENTER**

In `keel/agent.py`, replace the ENTER-path line (~404):

```python
                    repo.set_state(f"position_rule:{product_id}", signal.rule_name)
```

with:

```python
                    order = (
                        repo.get_order(result.order_id) if result.order_id is not None else None
                    )
                    repo.set_state(
                        f"position_rule:{product_id}",
                        {
                            "rule_name": signal.rule_name,
                            "opened_at": now_ts,
                            "entry_fill": None if order is None else order["actual_fill"],
                            "qty": None if order is None else order["qty"],
                        },
                    )
```

**Note on where the fill comes from.** `ExecutionResult` is `(placed, order_id, vetoed_by, preview,
reason)` — it carries **no** fill price, quantity or fee. The fills live in the `orders` row, reached
via `result.order_id` with `Repository.get_order(order_id)` (`repository.py:247`), whose
`actual_fill` / `qty` / `fee` come back as `Decimal`. Do not invent attributes on `ExecutionResult`.

An order that is `placed` but has no retrievable row yields `entry_fill=None`, which the Task 3
producer treats as "cannot attribute this trade" and skips rather than guessing a price.

- [ ] **Step 5: Update the exit-path reader**

In `keel/agent.py` `_handle_exits` (~line 200), replace:

```python
    owning_rule_name = repo.get_state(f"position_rule:{product_id}")
```

with:

```python
    position = _position_state(repo, product_id)
    owning_rule_name = None if position is None else position["rule_name"]
```

Leave the clearing line at ~235 (`repo.set_state(f"position_rule:{product_id}", None)`) unchanged —
Task 3 hooks there.

- [ ] **Step 6: Fix any other readers**

Run: `uv run grep -rn "position_rule" keel/ tests/ --include="*.py" | grep -v __pycache__`

Every read must go through `_position_state`. Update `tests/test_agent.py` fixtures that set the bare
string only where the test's intent needs the new shape — the legacy-tolerance test above deliberately
keeps a bare string and must stay that way.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_agent.py -v`
Expected: all pass, including the three new tests.

- [ ] **Step 8: Verify**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy`
Run: `uv run pytest tests/baseline/ -v && git diff --stat tests/fixtures/baseline_backtest.json`
Expected: all pass; the `git diff --stat` prints nothing.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat: track entry context on a held position"
```

---

### Task 2: The `trade_outcomes` table and repository access

Schema `2 → 3` using the versioned-migration machinery added on 2026-07-19. Additive: the table is created, nothing is backfilled.

**Files:**
- Modify: `keel/data/db.py` (`SCHEMA_VERSION`, `_SCHEMA_STATEMENTS`, `_MIGRATIONS`)
- Modify: `keel/data/repository.py`
- Test: `tests/data/test_trade_outcomes.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `Repository.insert_trade_outcome(outcome: dict) -> int` and `Repository.get_trade_outcomes(since_ts: int | None = None) -> list[dict]`. Tasks 3 and 5 call these exact names. Money fields round-trip as `Decimal`.

- [ ] **Step 1: Write the failing tests**

Create `tests/data/test_trade_outcomes.py`:

```python
"""Round-trip tests for the trade_outcomes table.

The table is the substrate rails 11 and 16 both depend on, so exactness matters: `pnl_net`'s SIGN
decides win-vs-loss, and a Decimal that drifts through storage would silently misclassify trades.
"""

from __future__ import annotations

from decimal import Decimal

from keel.data import db
from keel.data.repository import Repository


def _repo() -> Repository:
    conn = db.connect(":memory:")
    db.migrate(conn)
    return Repository(conn)


def _outcome(**overrides: object) -> dict:
    base: dict = {
        "product_id": "BTC-USD",
        "rule_name": "turtle_breakout",
        "is_dca": False,
        "opened_at": 1_800_000_000,
        "closed_at": 1_800_086_400,
        "qty": Decimal("0.5"),
        "entry_fill": Decimal("50000"),
        "exit_fill": Decimal("51000"),
        "fees": Decimal("1.25"),
        "pnl_net": Decimal("498.75"),
    }
    base.update(overrides)
    return base


def test_schema_is_at_version_3() -> None:
    conn = db.connect(":memory:")
    db.migrate(conn)
    version = conn.execute("SELECT version FROM schema_version").fetchone()["version"]
    assert version == db.SCHEMA_VERSION == 3


def test_fresh_database_has_no_outcomes() -> None:
    """No backfill by design: fabricated history would poison rail 16's threshold."""
    assert _repo().get_trade_outcomes() == []


def test_insert_then_get_round_trips_exactly() -> None:
    repo = _repo()
    repo.insert_trade_outcome(_outcome())
    rows = repo.get_trade_outcomes()
    assert len(rows) == 1
    assert rows[0]["pnl_net"] == Decimal("498.75")
    assert isinstance(rows[0]["pnl_net"], Decimal)
    assert rows[0]["rule_name"] == "turtle_breakout"
    assert rows[0]["is_dca"] is False


def test_high_precision_decimals_do_not_drift() -> None:
    repo = _repo()
    repo.insert_trade_outcome(_outcome(pnl_net=Decimal("-0.000000001")))
    assert repo.get_trade_outcomes()[0]["pnl_net"] == Decimal("-0.000000001")


def test_a_negative_pnl_survives_its_sign() -> None:
    """The sign is the whole signal — a loss must read back as a loss."""
    repo = _repo()
    repo.insert_trade_outcome(_outcome(pnl_net=Decimal("-12.5")))
    assert repo.get_trade_outcomes()[0]["pnl_net"] < 0


def test_is_dca_round_trips_as_a_bool_not_an_int() -> None:
    """SQLite has no bool; rail 16's exemption reads this, so it must not be 0/1."""
    repo = _repo()
    repo.insert_trade_outcome(_outcome(is_dca=True))
    assert repo.get_trade_outcomes()[0]["is_dca"] is True


def test_get_trade_outcomes_filters_by_since_ts() -> None:
    repo = _repo()
    repo.insert_trade_outcome(_outcome(closed_at=1_000))
    repo.insert_trade_outcome(_outcome(closed_at=2_000))
    assert len(repo.get_trade_outcomes(since_ts=1_500)) == 1
    assert len(repo.get_trade_outcomes(since_ts=None)) == 2


def test_outcomes_come_back_oldest_first() -> None:
    """Streak logic reads them in order; reverse order would invert the streak."""
    repo = _repo()
    repo.insert_trade_outcome(_outcome(closed_at=2_000, pnl_net=Decimal("5")))
    repo.insert_trade_outcome(_outcome(closed_at=1_000, pnl_net=Decimal("-5")))
    assert [r["closed_at"] for r in repo.get_trade_outcomes()] == [1_000, 2_000]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/data/test_trade_outcomes.py -v`
Expected: FAIL — `AttributeError: 'Repository' object has no attribute 'insert_trade_outcome'`

- [ ] **Step 3: Add the table and bump the schema version**

In `keel/data/db.py`, change:

```python
SCHEMA_VERSION = 3
```

Append to `_SCHEMA_STATEMENTS`, after the `broker_subscriptions` statement:

```python
    """
    CREATE TABLE IF NOT EXISTS trade_outcomes (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id    TEXT NOT NULL,
        rule_name     TEXT,
        is_dca        INTEGER NOT NULL,
        opened_at     INTEGER NOT NULL,
        closed_at     INTEGER NOT NULL,
        qty           TEXT NOT NULL,
        entry_fill    TEXT NOT NULL,
        exit_fill     TEXT NOT NULL,
        fees          TEXT NOT NULL,
        pnl_net       TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_trade_outcomes_closed_at ON trade_outcomes(closed_at)",
```

Add the migration step before `_MIGRATIONS`:

```python
def _migrate_v3_trade_outcomes(conn: sqlite3.Connection) -> None:
    """v3 adds `trade_outcomes`. Table creation is handled by `_SCHEMA_STATEMENTS`; there is
    deliberately NO backfill.

    Historical `orders` rows cannot be reliably paired into round-trips (partial fills, scale-outs,
    positions opened before entry context was tracked). Rail 16's threshold is derived from streak
    statistics, so seeding it with guessed history would be worse than starting empty.
    """
```

and register it:

```python
_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    2: _migrate_v2_broker_subscriptions,
    3: _migrate_v3_trade_outcomes,
}
```

- [ ] **Step 4: Add the repository methods**

In `keel/data/repository.py`, add a new section after the broker-subscriptions section:

```python
    # -- trade outcomes (closed round-trips; rails 11 and 16) ---------------

    def insert_trade_outcome(self, outcome: dict[str, Any]) -> int:
        """Append one CLOSED trade. `pnl_net` is realized and NET OF FEES — its sign is what
        rail 16 counts, so a fee-dominated "winner" must arrive here already negative.
        """
        cursor = self._conn.execute(
            """
            INSERT INTO trade_outcomes (
                product_id, rule_name, is_dca, opened_at, closed_at,
                qty, entry_fill, exit_fill, fees, pnl_net
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                outcome["product_id"],
                outcome["rule_name"],
                1 if outcome["is_dca"] else 0,
                int(outcome["opened_at"]),
                int(outcome["closed_at"]),
                _dec_to_text(outcome["qty"]),
                _dec_to_text(outcome["entry_fill"]),
                _dec_to_text(outcome["exit_fill"]),
                _dec_to_text(outcome["fees"]),
                _dec_to_text(outcome["pnl_net"]),
            ),
        )
        self._conn.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def get_trade_outcomes(self, since_ts: int | None = None) -> list[dict[str, Any]]:
        """Closed trades, OLDEST FIRST -- streak logic depends on that order."""
        if since_ts is None:
            rows = self._conn.execute(
                "SELECT * FROM trade_outcomes ORDER BY closed_at, id"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM trade_outcomes WHERE closed_at >= ? ORDER BY closed_at, id",
                (since_ts,),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "product_id": row["product_id"],
                "rule_name": row["rule_name"],
                "is_dca": bool(row["is_dca"]),
                "opened_at": int(row["opened_at"]),
                "closed_at": int(row["closed_at"]),
                "qty": Decimal(row["qty"]),
                "entry_fill": Decimal(row["entry_fill"]),
                "exit_fill": Decimal(row["exit_fill"]),
                "fees": Decimal(row["fees"]),
                "pnl_net": Decimal(row["pnl_net"]),
            }
            for row in rows
        ]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/data/test_trade_outcomes.py -v`
Expected: 8 passed.

- [ ] **Step 6: Verify**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy`
Run: `uv run pytest tests/baseline/ -v && git diff --stat tests/fixtures/baseline_backtest.json`
Expected: all pass; no diff output.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: trade_outcomes table and typed repository access"
```

---

### Task 3: The producer — record outcomes and maintain the streak

The agent writes an outcome row when a position closes, then updates the streak counter and, if the threshold is reached, the halt timestamp.

**Files:**
- Create: `keel/execution/streak.py`
- Modify: `keel/agent.py` (`_handle_exits`, at the position-clearing point ~line 235)
- Test: `tests/execution/test_streak.py`

**Interfaces:**
- Consumes: `_position_state` (Task 1); `insert_trade_outcome` (Task 2); `config.money_mgmt.max_consecutive_losses` / `.streak_cooloff_days` (Task 4 adds these — until then, read them with `getattr(..., default)` is NOT acceptable; Task 4 must land before this task's config-dependent test runs, so **add the two config fields as part of this task's Step 3** and Task 4 only consumes them).
- Produces: `keel.execution.streak.record_closed_trade(repo, config, *, product_id, position, exit_fill, exit_qty, fees, is_dca, now_ts) -> None`, and the `agent_state` keys `consecutive_losses: int` and `streak_halt_until: int`. Task 4's rail reads `streak_halt_until` only.

- [ ] **Step 1: Write the failing tests**

Create `tests/execution/test_streak.py`:

```python
"""Tests for the closed-trade producer and the streak counters it maintains.

The counter is the producer's private state; rail 16 reads only `streak_halt_until`. Keeping the
threshold decision in one place is deliberate -- if the rail also evaluated the counter, the two
could disagree about whether the breaker is tripped.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.execution import streak

NOW = 1_800_000_000
DAY = 86_400


def _repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    return Repository(conn)


def _config(max_consecutive_losses: int = 3, streak_cooloff_days: int = 2):
    # NOTE: `caps` and `market_data` have NO defaults on `Config` -- omitting them raises
    # `TypeError: missing 2 required positional arguments`. Verified against config.py:206.
    from keel.config import Caps, Config, MarketDataConfig, MoneyMgmtConfig

    return Config(
        allowlist=["BTC"],
        target_weights={},
        risk_pct=Decimal("0.01"),
        caps=Caps(
            max_per_order_usd=Decimal("100000"),
            max_per_day_usd=Decimal("300000"),
            max_exposure_usd=Decimal("1000000"),
            max_per_asset_pct=Decimal("1"),
        ),
        market_data=MarketDataConfig(granularities=[], history_days=365),
        money_mgmt=MoneyMgmtConfig(
            max_consecutive_losses=max_consecutive_losses,
            streak_cooloff_days=streak_cooloff_days,
        ),
    )


def _close(repo, config, *, pnl: str, is_dca: bool = False, now_ts: int = NOW) -> None:
    """Close one trade with a given net P&L."""
    entry = Decimal("100")
    qty = Decimal("1")
    exit_fill = entry + Decimal(pnl)
    streak.record_closed_trade(
        repo,
        config,
        product_id="BTC-USD",
        position={
            "rule_name": None if is_dca else "turtle_breakout",
            "opened_at": now_ts - DAY,
            "entry_fill": entry,
            "qty": qty,
        },
        exit_fill=exit_fill,
        exit_qty=qty,
        fees=Decimal("0"),
        is_dca=is_dca,
        now_ts=now_ts,
    )


def test_a_closed_trade_appends_exactly_one_outcome_row() -> None:
    repo = _repo()
    _close(repo, _config(), pnl="5")
    assert len(repo.get_trade_outcomes()) == 1


def test_a_losing_trade_increments_the_counter() -> None:
    repo = _repo()
    _close(repo, _config(), pnl="-5")
    assert repo.get_state("consecutive_losses") == 1


def test_a_winning_trade_resets_the_counter_to_zero() -> None:
    """The counter resets on ANY win -- that is normal operation, no halt involved."""
    repo = _repo()
    config = _config()
    _close(repo, config, pnl="-5")
    _close(repo, config, pnl="-5")
    assert repo.get_state("consecutive_losses") == 2
    _close(repo, config, pnl="+5")
    assert repo.get_state("consecutive_losses") == 0


def test_fees_can_turn_a_gross_winner_into_a_counted_loss() -> None:
    """Rail 7 exists because fees dominate small moves; the streak must agree with that."""
    repo = _repo()
    streak.record_closed_trade(
        repo,
        _config(),
        product_id="BTC-USD",
        position={
            "rule_name": "turtle_breakout",
            "opened_at": NOW - DAY,
            "entry_fill": Decimal("100"),
            "qty": Decimal("1"),
        },
        exit_fill=Decimal("100.10"),   # +0.10 gross
        exit_qty=Decimal("1"),
        fees=Decimal("0.25"),          # -0.15 net
        is_dca=False,
        now_ts=NOW,
    )
    assert repo.get_trade_outcomes()[0]["pnl_net"] == Decimal("-0.15")
    assert repo.get_state("consecutive_losses") == 1


def test_reaching_the_threshold_sets_the_halt() -> None:
    repo = _repo()
    config = _config(max_consecutive_losses=3, streak_cooloff_days=2)
    for _ in range(3):
        _close(repo, config, pnl="-5")
    assert repo.get_state("streak_halt_until") == NOW + 2 * DAY


def test_below_the_threshold_sets_no_halt() -> None:
    """The negative for the test above: two losses with a threshold of three must NOT halt."""
    repo = _repo()
    config = _config(max_consecutive_losses=3)
    for _ in range(2):
        _close(repo, config, pnl="-5")
    assert repo.get_state("streak_halt_until", default=0) == 0


def test_a_dca_loss_records_an_outcome_but_never_moves_the_streak() -> None:
    """DCA is designed to buy through drawdowns (§12.6) -- counting it would trip the breaker
    during exactly the accumulation it exists to perform."""
    repo = _repo()
    config = _config(max_consecutive_losses=1)
    _close(repo, config, pnl="-5", is_dca=True)
    assert len(repo.get_trade_outcomes()) == 1        # recorded
    assert repo.get_state("consecutive_losses", default=0) == 0   # but not counted
    assert repo.get_state("streak_halt_until", default=0) == 0    # and never halts


def test_the_rail_is_inert_when_disabled() -> None:
    """max_consecutive_losses = 0 is the shipped default and must never halt."""
    repo = _repo()
    config = _config(max_consecutive_losses=0)
    for _ in range(10):
        _close(repo, config, pnl="-5")
    assert repo.get_state("streak_halt_until", default=0) == 0


def test_a_position_with_no_entry_context_is_skipped_not_guessed() -> None:
    """Legacy bare-string state (Task 1) yields entry_fill=None. Inventing a price would
    fabricate a P&L and could trip a live-money breaker on a number nobody observed."""
    repo = _repo()
    streak.record_closed_trade(
        repo,
        _config(),
        product_id="BTC-USD",
        position={"rule_name": "turtle_breakout", "opened_at": None,
                  "entry_fill": None, "qty": None},
        exit_fill=Decimal("100"),
        exit_qty=Decimal("1"),
        fees=Decimal("0"),
        is_dca=False,
        now_ts=NOW,
    )
    assert repo.get_trade_outcomes() == []
    assert repo.get_state("consecutive_losses", default=0) == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/execution/test_streak.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'keel.execution.streak'`

- [ ] **Step 3: Add the config fields**

In `packages/keel-core/keel_core/config.py`, extend `MoneyMgmtConfig`:

```python
@dataclass(frozen=True)
class MoneyMgmtConfig:
    profit_trigger_pct: Decimal = Decimal("0.1")
    acceleration_pct: Decimal = Decimal("0.05")
    max_total_dd_pct: Decimal = Decimal("0.2")
    max_weekly_dd_pct: Decimal = Decimal("0.08")
    # Rail 16 (consecutive-loss breaker). SHIPS DISABLED: 0 means off.
    #
    # A live default would be actively harmful here. `turtle_breakout`'s own simulation shows a
    # max losing streak of 5, so the source's suggested threshold of 3 would have fired repeatedly
    # on a strategy that was working. The threshold must sit ABOVE the strategy's tested max streak
    # (`strategy/stats.py:max_losing_streak`) or the breaker fires on normal variance and stands the
    # system down during exactly the runs it was designed to survive. Set it from a sweep.
    max_consecutive_losses: int = 0
    streak_cooloff_days: int = 0
```

And in the `money_mgmt` parsing block of `load_config`, add:

```python
            max_consecutive_losses=_non_negative_int(
                money_raw.get("max_consecutive_losses", 0), "money_mgmt.max_consecutive_losses"
            ),
            streak_cooloff_days=_non_negative_int(
                money_raw.get("streak_cooloff_days", 0), "money_mgmt.streak_cooloff_days"
            ),
```

**`_non_negative_int` does not exist yet — add it** next to `_non_negative_decimal` in the same file:

```python
def _non_negative_int(value: Any, key: str) -> int:
    """Parse a non-negative integer config value, rejecting bools and negatives.

    `isinstance(True, int)` is True in Python, so a bare int() would silently accept `yes`/`true`
    from YAML as 1 -- for a rail threshold that would turn a typo into a live setting.
    """
    if isinstance(value, bool):
        raise ConfigError(f"{key}: must be an integer, got a boolean")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{key}: must be an integer, got {value!r}") from exc
    if parsed < 0:
        raise ConfigError(f"{key}: must be non-negative, got {parsed}")
    return parsed
```

- [ ] **Step 4: Implement the producer**

Create `keel/execution/streak.py`:

```python
"""Closed-trade outcome recording and the consecutive-loss streak counters.

This is the PRODUCER half of the split rail 11 was designed for and never got: the agent owns
computation, `guards.py` stays pure and reads precomputed scalars. Rail 16 reads exactly one key
from here -- `streak_halt_until` -- and never the counter, so the "is the threshold reached"
decision lives in one place and cannot disagree with itself.

`pnl_net` is realized and NET OF FEES. That is not a detail: rail 7 exists because fees dominate
small moves, so a trade that is up gross and down net is a loss, and the streak must agree.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from keel_core.telemetry import log_event

from keel.config import Config
from keel.data.repository import Repository

logger = logging.getLogger(__name__)

SECONDS_PER_DAY = 86_400


def record_closed_trade(
    repo: Repository,
    config: Config,
    *,
    product_id: str,
    position: dict[str, Any],
    exit_fill: Decimal,
    exit_qty: Decimal,
    fees: Decimal,
    is_dca: bool,
    now_ts: int,
) -> None:
    """Append the outcome of one fully-closed trade and update the streak counters.

    A `position` with no `entry_fill` (the legacy bare-string state form) is SKIPPED rather than
    guessed: inventing an entry price would fabricate a P&L that could trip a live-money breaker on
    a number nobody observed.
    """
    entry_fill = position.get("entry_fill")
    if entry_fill is None:
        log_event(
            logger,
            logging.WARNING,
            "streak.outcome_skipped_no_entry_context",
            product=product_id,
        )
        return

    pnl_net = (exit_fill - entry_fill) * exit_qty - fees

    repo.insert_trade_outcome(
        {
            "product_id": product_id,
            "rule_name": position.get("rule_name"),
            "is_dca": is_dca,
            "opened_at": position.get("opened_at") or now_ts,
            "closed_at": now_ts,
            "qty": exit_qty,
            "entry_fill": entry_fill,
            "exit_fill": exit_fill,
            "fees": fees,
            "pnl_net": pnl_net,
        }
    )

    # DCA is exempt from the STREAK, not from the RECORD: its P&L is real and rail 11 needs it,
    # but DCA is designed to buy through drawdowns on a fixed budget (§12.6).
    if is_dca:
        return

    if pnl_net >= 0:
        repo.set_state("consecutive_losses", 0)
        return

    losses = int(repo.get_state("consecutive_losses", default=0)) + 1
    repo.set_state("consecutive_losses", losses)

    threshold = config.money_mgmt.max_consecutive_losses
    if threshold > 0 and losses >= threshold:
        halt_until = now_ts + config.money_mgmt.streak_cooloff_days * SECONDS_PER_DAY
        repo.set_state("streak_halt_until", halt_until)
        log_event(
            logger,
            logging.WARNING,
            "streak.breaker_tripped",
            product=product_id,
            consecutive_losses=losses,
            threshold=threshold,
            halt_until=halt_until,
        )
```

- [ ] **Step 5: Hook it into the agent's exit path**

In `keel/agent.py` `_handle_exits`, replace:

```python
    if result.placed:
        repo.set_state(f"position_rule:{product_id}", None)
```

with:

```python
    if result.placed:
        exit_order = repo.get_order(result.order_id) if result.order_id is not None else None
        if position is not None and exit_order is not None:
            streak.record_closed_trade(
                repo,
                config,
                product_id=product_id,
                position=position,
                exit_fill=exit_order["actual_fill"],
                exit_qty=exit_order["qty"],
                fees=exit_order["fee"] or Decimal("0"),
                is_dca=False,
                now_ts=now_ts,
            )
        repo.set_state(f"position_rule:{product_id}", None)
```

Add `from keel.execution import streak` to the imports.

**`ExecutionResult` carries no fill data** — it is `(placed, order_id, vetoed_by, preview, reason)`.
The exit fill, quantity and fee come from the `orders` row via `Repository.get_order(result.order_id)`
(`repository.py:247`), which returns them as `Decimal`. Do not add attributes to `ExecutionResult`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/execution/test_streak.py -v`
Expected: 9 passed.

- [ ] **Step 7: Verify**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy`
Run: `uv run pytest tests/baseline/ -v && git diff --stat tests/fixtures/baseline_backtest.json`
Expected: all pass; no diff output.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: record closed-trade outcomes and maintain streak counters"
```

---

### Task 4: Rail 16 — the consecutive-loss breaker

**Files:**
- Modify: `keel/execution/guards.py` (module docstring rail list; new rail after rail 15)
- Modify: `keel/sim/account.py` (parity)
- Modify: `config.yaml`
- Test: `tests/execution/test_guards.py`, `tests/sim/test_account.py`

**Interfaces:**
- Consumes: `agent_state["streak_halt_until"]` (Task 3); `config.money_mgmt.max_consecutive_losses` (Task 3 added it).
- Produces: violation key `consecutive_loss_breaker`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/execution/test_guards.py`:

```python
def test_rail16_vetoes_a_buy_while_the_streak_halt_is_active(repo: Repository) -> None:
    repo.set_state("streak_halt_until", NOW_TS + 3600)
    config = _config(max_consecutive_losses=3)

    result = check(_intent(), repo, config, NOW_TS)

    assert result.ok is False
    assert _keys(result) == {"consecutive_loss_breaker"}


def test_rail16_does_not_veto_once_the_halt_has_expired(repo: Repository) -> None:
    """The negative control: same repo, same config, halt one second in the past."""
    repo.set_state("streak_halt_until", NOW_TS - 1)
    config = _config(max_consecutive_losses=3)

    result = check(_intent(), repo, config, NOW_TS)

    assert result.ok is True
    assert result.violations == []


def test_rail16_boundary_halt_expires_exactly_at_now(repo: Repository) -> None:
    """`now_ts < halt_until` -- due-at is the moment it expires, matching rail 14's convention."""
    repo.set_state("streak_halt_until", NOW_TS)
    result = check(_intent(), repo, _config(max_consecutive_losses=3), NOW_TS)
    assert result.ok is True


def test_rail16_never_vetoes_a_sell(repo: Repository) -> None:
    """A breaker that blocked EXITS would trap capital in a losing position, inverting its
    own purpose. Entries only."""
    repo.set_state("streak_halt_until", NOW_TS + 3600)
    result = check(_intent(side=Side.SELL), repo, _config(max_consecutive_losses=3), NOW_TS)
    assert "consecutive_loss_breaker" not in _keys(result)


def test_rail16_never_vetoes_dca(repo: Repository) -> None:
    repo.set_state("streak_halt_until", NOW_TS + 3600)
    result = check(_intent(is_dca=True), repo, _config(max_consecutive_losses=3), NOW_TS)
    assert "consecutive_loss_breaker" not in _keys(result)


def test_rail16_is_inert_when_no_halt_was_ever_set(repo: Repository) -> None:
    """The shipped default: nothing set, nothing vetoed."""
    result = check(_intent(), repo, _config(), NOW_TS)
    assert "consecutive_loss_breaker" not in _keys(result)


def test_rail16_violation_message_names_the_cause_and_the_override(repo: Repository) -> None:
    """A bare veto is arithmetically true and operationally useless (the rail-14 lesson)."""
    repo.set_state("streak_halt_until", NOW_TS + 3600)
    result = check(_intent(), repo, _config(max_consecutive_losses=3), NOW_TS)
    violation = next(v for v in result.violations if v.startswith("consecutive_loss_breaker"))
    assert "consecutive" in violation
    assert "Exits" in violation
    assert "resume-entries" in violation
```

Extend `_config(...)` in that file with a `max_consecutive_losses: int = 0` keyword, wired into the
`MoneyMgmtConfig(...)` it builds.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/execution/test_guards.py -k rail16 -v`
Expected: FAIL — no `consecutive_loss_breaker` key exists; the veto tests fail.

- [ ] **Step 3: Implement the rail**

In `keel/execution/guards.py`, after rail 15:

```python
    # 16. Consecutive-loss circuit breaker — a SEQUENCE breaker where rail 11 is a MAGNITUDE
    #     breaker: it detects that the edge may have stopped working BEFORE the drawdown
    #     accumulates, which is a cheap regime-degradation proxy needing no regime classifier.
    #     ENTRIES ONLY — a breaker that blocked exits would trap capital in a losing position,
    #     inverting its own purpose. DCA exempt (§12.6). The counter lives in the producer
    #     (`execution/streak.py`); this rail reads only the halt timestamp, so the
    #     threshold decision exists in exactly one place.
    if is_buy and not intent.is_dca:
        halt_until = int(repo.get_state("streak_halt_until", default=0) or 0)
        if now_ts < halt_until:
            violations.append(
                f"consecutive_loss_breaker: {config.money_mgmt.max_consecutive_losses} "
                f"consecutive losing trades tripped the breaker; new entries are halted for "
                f"another {halt_until - now_ts}s. Exits, stop-outs and DCA are unaffected. "
                f"Clear it early with `keel resume-entries`."
            )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/execution/test_guards.py -k rail16 -v`
Expected: 7 passed.

- [ ] **Step 5: Prove the tests discriminate (mutation check)**

Two rails in this repo shipped unable to fire, so a green suite is not evidence. In a scratch
worktree, comment out the rail-16 block and re-run:

```bash
WT="$(mktemp -d)/rail16-mut"
git worktree add -f "$WT" HEAD
cp keel/execution/guards.py "$WT/keel/execution/guards.py"
cp tests/execution/test_guards.py "$WT/tests/execution/test_guards.py"
cd "$WT"
python3 - <<'EOF'
import pathlib
p = pathlib.Path("keel/execution/guards.py"); s = p.read_text()
old = '            violations.append(\n                f"consecutive_loss_breaker:'
assert old in s, "anchor not found -- check the rail-16 block"
p.write_text(s.replace(old, '            pass  # MUTANT: rail 16 disabled\n            _unused = (\n                f"consecutive_loss_breaker:'))
EOF
uv run pytest tests/execution/test_guards.py -k rail16 -q
```

Expected: the two positive tests (`vetoes_a_buy_while...`, `violation_message_names...`) **FAIL**,
while the negative controls still pass. If everything still passes, the tests are not testing the
rail — fix them before continuing. Remove the worktree afterwards (`git worktree remove --force`).

- [ ] **Step 6: Mirror the rail in the simulator**

`keel/sim/account.py` re-implements the rails for backtests and is held in parity by
`tests/sim/test_account.py`. Add the same halt check to `SimAccount.can_open`, reading the same
`streak_halt_until` state, and add a parity test asserting sim and `guards.check` agree while a halt
is active. Without this, `keel simulate` reports entries the live engine would refuse — the sim/live
divergence class the spec names — and the threshold sweep would be measuring the wrong system.

- [ ] **Step 7: Document the knobs in `config.yaml`**

```yaml
money_mgmt:
  # Rail 16 (consecutive-loss breaker) — DISABLED by default (0 = off).
  # Set from a backtest sweep, and set it ABOVE the strategy's tested max losing streak:
  # turtle_breakout's max streak is 5, so a threshold of 3 would fire on normal variance.
  max_consecutive_losses: 0
  streak_cooloff_days: 0
```

- [ ] **Step 8: Verify**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy`
Run: `uv run pytest tests/baseline/ -v && git diff --stat tests/fixtures/baseline_backtest.json`
Expected: all pass; no diff output. With the default of 0 the rail is inert, so no pre-existing test
should change behaviour — if one does, the rail is firing when it must not.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat: rail 16 consecutive-loss circuit breaker (ships disabled)"
```

---

### Task 5: Wake rail 11 — marked-to-market equity and drawdown

Rail 11's code does not change. This task supplies the inputs it has always read and never received.

**Files:**
- Create: `keel/execution/equity.py`
- Modify: `keel/agent.py` (`run_once`, once per cycle)
- Test: `tests/execution/test_equity.py`

**Interfaces:**
- Consumes: `_position_state` (Task 1); `get_trade_outcomes` (Task 2).
- Produces: `keel.execution.equity.update_drawdown(repo, *, equity, now_ts) -> None`, writing `agent_state` keys `equity_high_water_mark`, `drawdown_total_pct`, `drawdown_weekly_pct` — the exact keys rail 11 already reads.

- [ ] **Step 1: Write the failing tests**

Create `tests/execution/test_equity.py`:

```python
"""Tests for the equity/drawdown producer that rail 11 depends on.

Rail 11 shipped DORMANT: it reads `drawdown_total_pct`/`drawdown_weekly_pct` with a default of 0 and
nothing ever wrote them, so the account-drawdown circuit breaker could not trip while reading as
enforced. The regression test at the bottom is the one that would have caught that.
"""

from __future__ import annotations

from decimal import Decimal

from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.execution import equity

NOW = 1_800_000_000
DAY = 86_400


def _repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    return Repository(conn)


def test_first_update_sets_the_high_water_mark_and_zero_drawdown() -> None:
    repo = _repo()
    equity.update_drawdown(repo, equity=Decimal("10000"), now_ts=NOW)
    assert repo.get_state("equity_high_water_mark") == Decimal("10000")
    assert repo.get_state("drawdown_total_pct") == Decimal("0")


def test_a_new_peak_raises_the_high_water_mark() -> None:
    repo = _repo()
    equity.update_drawdown(repo, equity=Decimal("10000"), now_ts=NOW)
    equity.update_drawdown(repo, equity=Decimal("12000"), now_ts=NOW + DAY)
    assert repo.get_state("equity_high_water_mark") == Decimal("12000")
    assert repo.get_state("drawdown_total_pct") == Decimal("0")


def test_drawdown_is_measured_from_the_peak_not_from_deposits() -> None:
    """Drawdown-from-deposit would read 0% forever on an account in profit."""
    repo = _repo()
    equity.update_drawdown(repo, equity=Decimal("10000"), now_ts=NOW)
    equity.update_drawdown(repo, equity=Decimal("12000"), now_ts=NOW + DAY)
    equity.update_drawdown(repo, equity=Decimal("9000"), now_ts=NOW + 2 * DAY)
    assert repo.get_state("drawdown_total_pct") == Decimal("0.25")   # 3000/12000


def test_the_high_water_mark_never_falls() -> None:
    repo = _repo()
    equity.update_drawdown(repo, equity=Decimal("12000"), now_ts=NOW)
    equity.update_drawdown(repo, equity=Decimal("9000"), now_ts=NOW + DAY)
    assert repo.get_state("equity_high_water_mark") == Decimal("12000")


def test_weekly_drawdown_uses_a_rolling_7_day_peak() -> None:
    """Rolling, not calendar: a calendar reset clears the breaker every Monday regardless of
    conditions."""
    repo = _repo()
    equity.update_drawdown(repo, equity=Decimal("10000"), now_ts=NOW)
    equity.update_drawdown(repo, equity=Decimal("8000"), now_ts=NOW + DAY)
    assert repo.get_state("drawdown_weekly_pct") == Decimal("0.2")


def test_a_peak_older_than_7_days_no_longer_binds_the_weekly_drawdown() -> None:
    """The negative for the test above -- proves the window actually rolls."""
    repo = _repo()
    equity.update_drawdown(repo, equity=Decimal("10000"), now_ts=NOW)
    equity.update_drawdown(repo, equity=Decimal("8000"), now_ts=NOW + 8 * DAY)
    assert repo.get_state("drawdown_weekly_pct") == Decimal("0")


def test_rail11_actually_trips_once_the_producer_runs() -> None:
    """THE REGRESSION TEST. Rail 11 was unfireable because these keys were never written.
    This fails if the producer stops writing them."""
    from keel.execution.guards import check

    repo = _repo()
    repo.set_state("kill_switch", False)
    repo.set_state("last_feed_ts", NOW)
    equity.update_drawdown(repo, equity=Decimal("10000"), now_ts=NOW)
    equity.update_drawdown(repo, equity=Decimal("7000"), now_ts=NOW + DAY)   # 30% > 20% cap

    from tests.execution.test_guards import _config, _intent, _keys

    result = check(_intent(), repo, _config(), NOW + DAY)
    assert "account_dd_breaker_total" in _keys(result)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/execution/test_equity.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'keel.execution.equity'`

- [ ] **Step 3: Implement the producer**

Create `keel/execution/equity.py`:

```python
"""Marked-to-market equity, the high-water mark, and the drawdown scalars rail 11 reads.

Rail 11 shipped DORMANT: it reads `drawdown_total_pct`/`drawdown_weekly_pct` from `agent_state`
with a default of 0, and nothing wrote them. It read as enforced in `guards.py` and in the design
docs and could not trip. This module is the missing producer.

Unrealized P&L is INCLUDED deliberately. A drawdown breaker that saw only realized P&L would sit at
0% while a position bled and would notice only after the loss was booked -- backwards for a circuit
breaker, which must fire WHILE you are losing. That is what forces mark-to-market, and therefore
why this lives agent-side (the agent has prices; `guards.check` does not).
"""

from __future__ import annotations

from decimal import Decimal

from keel.data.repository import Repository

WEEK_SECONDS = 7 * 86_400


def update_drawdown(repo: Repository, *, equity: Decimal, now_ts: int) -> None:
    """Record `equity` and refresh the drawdown scalars rail 11 consumes."""
    hwm = repo.get_state("equity_high_water_mark")
    if hwm is None or equity > hwm:
        hwm = equity
        repo.set_state("equity_high_water_mark", hwm)

    repo.set_state(
        "drawdown_total_pct",
        Decimal("0") if hwm <= 0 else max((hwm - equity) / hwm, Decimal("0")),
    )

    history = [
        point
        for point in (repo.get_state("equity_history", default=[]) or [])
        if int(point["ts"]) >= now_ts - WEEK_SECONDS
    ]
    history.append({"ts": now_ts, "equity": equity})
    repo.set_state("equity_history", history)

    weekly_peak = max(Decimal(str(p["equity"])) for p in history)
    repo.set_state(
        "drawdown_weekly_pct",
        Decimal("0") if weekly_peak <= 0 else max((weekly_peak - equity) / weekly_peak, Decimal("0")),
    )
```

- [ ] **Step 4: Call it once per cycle from the agent**

In `keel/agent.py` `run_once`, after prices for the cycle are available and before the entry loop,
compute equity and call the producer:

```python
        equity_now = _mark_to_market_equity(repo, broker, latest_price_by_product)
        equity_mod.update_drawdown(repo, equity=equity_now, now_ts=now_ts)
```

Add a helper in the same module:

```python
def _mark_to_market_equity(
    repo: Repository, broker: Any, price_by_product: dict[str, Decimal]
) -> Decimal:
    """Quote balance + mark-to-market value of every open position.

    A product with no fresh price this cycle is valued at its ENTRY fill rather than dropped --
    dropping it would understate equity and could trip rail 11 on a data gap rather than a loss.
    """
    total = Decimal("0")
    for account in broker.get_accounts():
        if account["currency"] == "USDC":
            total += Decimal(str(account["available_balance"] or 0))
    for product_id, price in price_by_product.items():
        position = _position_state(repo, product_id)
        if position is None or position.get("qty") is None:
            continue
        mark = price or position.get("entry_fill") or Decimal("0")
        total += position["qty"] * mark
    return total
```

Import as `from keel.execution import equity as equity_mod`.

**`run_once` has no ready-made price mapping** — it iterates `for product_id in products:` and works
with `candles_by_tf` per product. Build the mapping inside that loop as you go:

```python
        latest_price_by_product: dict[str, Decimal] = {}
        for product_id in products:
            ...
            finest_candles = candles_by_tf.get(finest) if finest is not None else None
            if finest_candles:
                latest_price_by_product[product_id] = finest_candles[-1].close
```

then call `update_drawdown` **after** the product loop, so every product has contributed its price.
A stale product that was skipped simply has no entry, and `_mark_to_market_equity` falls back to its
entry fill — see that helper's docstring for why dropping it would be wrong.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/execution/test_equity.py -v`
Expected: 7 passed, including `test_rail11_actually_trips_once_the_producer_runs`.

- [ ] **Step 6: Prove the regression test discriminates**

The unit tests above call the producer directly, so they would all still pass if the agent never
invoked it — which is precisely how rail 11 came to be dormant. The wiring needs its own test.

Add to `tests/test_agent.py` (it already provides a `repo` fixture, `FakeBroker`, `_config` and
`_candle`):

```python
def test_run_once_writes_the_drawdown_scalars(repo) -> None:
    """Rail 11's inputs must be produced by the CYCLE, not only by a directly-called helper.

    Without this, `test_equity.py` passes while `run_once` never calls the producer -- exactly the
    state that left rail 11 unable to trip for the whole life of the project.
    """
    series = {(PRODUCT, Granularity.ONE_DAY): [_candle(1_000 + i * 86_400) for i in range(30)]}
    broker = FakeBroker(series=series)

    run_once(broker, repo, _config(), now_ts=1_000 + 29 * 86_400)

    assert repo.get_state("drawdown_total_pct") is not None
    assert repo.get_state("equity_high_water_mark") is not None
```

Then, in a scratch worktree, comment out the `equity_mod.update_drawdown(...)` call in `agent.py`
and re-run:

```bash
uv run pytest tests/test_agent.py -k run_once_writes_the_drawdown -q
```

Expected: **FAIL**. If it passes, the producer is not actually wired into the cycle and rail 11 is
still dormant regardless of what `test_equity.py` says.

- [ ] **Step 7: Verify**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy`
Run: `uv run pytest tests/baseline/ -v && git diff --stat tests/fixtures/baseline_backtest.json`
Expected: all pass; no diff output.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: mark-to-market equity producer; rail 11 can now trip"
```

---

## Done criteria

- `uv run pytest -q`, `uv run ruff check .`, `uv run mypy` all pass.
- `tests/fixtures/baseline_backtest.json` byte-identical to its original generation.
- A closing trade appends exactly one `trade_outcomes` row with `pnl_net` net of fees.
- A gross-winner-net-loser counts as a loss in the streak.
- A DCA close records a row but never moves the streak counter or sets a halt.
- Rail 16 vetoes BUY entries while halted and never vetoes a SELL, an exit, or a DCA buy.
- With `max_consecutive_losses = 0` (the shipped default) rail 16 is inert and no pre-existing test changes behaviour.
- `drawdown_total_pct` / `drawdown_weekly_pct` are written every cycle, and a test fails if they are not.
- Rail 11 trips in a test — proving the dormancy is fixed.
- `sim/account.py` parity holds with rail 16 present.

## Follow-on (explicitly not in this plan)

- **Sweeping `max_consecutive_losses` / `streak_cooloff_days`** and deciding whether to enable rail 16. That needs `keel simulate` runs and is a separate decision; the rail ships disabled precisely so this can be done deliberately.
- `keel resume-entries` CLI command for the operator override. The violation message names it, so it must exist before rail 16 is enabled — but it is not needed while the rail is off. **Track it: enabling the rail without this command leaves an operator unable to clear a halt early.**
- Backfilling historical outcomes — deliberately never (see Task 2, Step 3).
